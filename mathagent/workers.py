# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Run one native delegated mode with its parent's budget and durable child checkpoint."""
import copy
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from . import bibcheck, briefs, images
from .agent import AgentError
from .assistant_budget import AssistantBudget
from .backends import TokenBudgetError
from .call_policy import MIN_PROOF_REVIEW_TOKENS, CallContextError
from .deepinfra import DeepInfraGenerationError, TesterAllowanceInsufficient
from .ledger import ProofStore, _ID, _atomic_write, _bytes, _directory, _now, _read, _regular
from .litreview import ReviewRunner, review_outcome
from .mode_calls import USER_MODES, routed_python_task
from .model_limits import input_limit as model_input_limit, output_limit, is_ds4, HOSTED_DS4, ProviderChanged
from .numerics import SandboxUnavailable
from .orchestrator import CUT_MARK, FINAL_PREDICT, PROOF_OUTCOMES
from .proof import ProofRunner
from .proof_support import complete_proof_evidence
from .refcheck import EFFORTS as REFCHECK_EFFORTS, CheckRunner, nested_checker, precise_reference_request
from .research import _read_state, _job_directory
from .review import ReviewBase, runner_class as review_runner, runner_for_state
from .source_tasks import explore_sources, named_explore_sources, requests_workspace_search
from .worker_errors import WorkerInputError
from .writeup import WriteupRunner

# Characters of output shown for each sandbox run that did not finish (timeout, error, memory).
FAILED_RUN_OUTPUT = 1000

REPAIR_REQUEST = re.compile(r"\A(?=.{0,300}\Z)(?=.*\b(?:fix|repair|correct|corrige[rz]?|r[ée]pare[rz]?)\b)"
                            r"(?!.*\b(?:proof|preuve|d[ée]monstration|lemma|lemme|theorem|th[ée]or[èe]me|argument)\b)",
                            re.I | re.S)

# detailed_review, the former name of the review, runs the review; typo_check is the typo check. A saved child of
# a review started at Low effort resumes as a typo check (the runner reads the saved level).
REVIEW_MODES = {'referee': 'review', 'detailed_review': 'review', 'typo_check': 'typos', 'quick_review': 'quick',
                'explain': 'explain'}

def worker_limits(mode, level_name, args):
    """Concrete native reservations for a bounded investigation task."""
    from .gui import effort
    level = effort.level(mode, level_name, args)
    limits = effort.limits(mode, level_name, args)
    values = dict(tokens=level['tokens'], input_tokens=level['input'], seconds=level['minutes'] * 60,
                  requests=level['requests'], tries=level['tries'])
    for key in values:
        if limits.get(key) is not None:
            values[key] = min(values[key], limits[key])
    if mode == 'check' and getattr(args, 'api_profile', None) not in HOSTED_DS4:
        values['seconds'] = min(values['seconds'], REFCHECK_EFFORTS['high']['seconds'])
    return values


def _answer_path(root, chat_id, ident, *, create=False):
    if not all(isinstance(key, str) and _ID.fullmatch(key) for key in (chat_id, ident)):
        raise ValueError('Invalid answer or chat ID')
    base = Path(root)
    for part in ('.mathagent', 'answers', chat_id):
        base = base / part
        if base.is_symlink() or base.exists() or create:
            _directory(base, create=create)
    path = base / (ident + '.json')
    _regular(path, optional=True)
    return path


def _save_answer(root, chat_id, child, result, agent, ident):
    experiments, checks, trail = list(child.get('experiment_ids', [])), [], []
    for message in agent.history:
        if message.get('role') != 'tool':
            continue
        name = message.get('tool_name', '')
        trail.append(name)
        try:
            record = json.loads(message.get('content', ''))
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict):
            if name == 'execute_python' and isinstance(record.get('experiment_id'), str):
                experiments.append(record['experiment_id'])
            if name == 'check_reference' and isinstance(record.get('check_id'), str):
                checks.append(record['check_id'])
    record = dict(id=ident, chat_id=chat_id, child_id=child['id'], mode=child['action']['mode'],
                  request=child['action']['request'], original_request=child['context']['query'],
                  status=result['status'], answer=result['answer'], answer_sha256=hashlib.sha256(result['answer'].encode()).hexdigest(),
                  created=_now(), warnings=result.get('warnings', []),
                  experiments=list(dict.fromkeys(experiments)), checks=checks, trail=trail,
                  launch_project=(child['context'].get('project_snapshot') or {}).get('id'),
                  result_fields={k: result[k] for k in ('source_excerpt', 'source_coverage', 'shown_files', 'recorded_measurements', 'experiments')
                                 if k in result})
    data = _bytes(json.dumps(record, ensure_ascii=False, allow_nan=False))
    _atomic_write(_answer_path(root, chat_id, ident, create=True), data)


def _packet_room(agent):
    """Characters of source or result text one call of this agent can take beside its reply.

    DS4 has its own allowance. Another model gives the packet half of the tokens its
    reply leaves, counted at 2.5 characters per token (the estimate Proof uses for saved notes).
    """
    left = agent.ctx - output_limit(agent, 'assistant_final', FINAL_PREDICT) - 4096
    if is_ds4(agent):
        return min(model_input_limit(agent, 'source_overview', 28000), max(2000, left))
    return max(2000, left * 5 // 4)


def _shown(text, room, ident, part):
    """The whole text, or its opening followed by what was cut and the call that reads the rest."""
    if len(text) <= room:
        return text
    kept = text.rfind('\n', 0, room) + 1 or room
    rest = f', offset={kept}' if part == 'stdout' else ''
    return (text[:kept].rstrip('\n') + f'\n{CUT_MARK} the first {kept} of {len(text)} characters are shown. '
            f'Read the rest with read_experiment(experiment_id="{ident}", part="{part}"{rest}).]')


def _saved_run(session, ident):
    store = session.store
    return getattr(store, 'read_saved', store.load)(ident)


def _sandbox_succeeded(session, child):
    """Whether one of this worker's durable experiments finished with status ok."""
    return any(_saved_run(session, ident)['status'] == 'ok' for ident in child.get('experiment_ids', []))


SANITY_VERDICTS = ('exact counterexample', 'numerically verified candidate', 'no counterexample detected', 'inconclusive')
_VERDICT_NOTES = {
    'exact counterexample': 'A run reported a violation confirmed in exact arithmetic.',
    'numerically verified candidate': 'A run reported a violation above the error level, not yet confirmed exactly.',
    'no counterexample detected': 'Only the tested cases: numerical evidence, not a proof.',
    'inconclusive': 'The runs settle nothing either way.',
    'unclassified': 'No successful run reported one of the four classifications; read the runs and the discussion below.',
}


def sanity_verdict(session, child, answer):
    """The sanity check's one classification, derived by code, never the model's prose verdict.

    From the RESULT['classification'] of the successful runs (the strongest finding wins), else the
    only label the answer names when a run succeeded (several are ambiguous: "not an exact
    counterexample"), else inconclusive."""
    found = []
    for ident in child.get('experiment_ids', []):
        try:
            record = _saved_run(session, ident)
        except (OSError, ValueError):
            continue
        value = record.get('result') if record.get('status') == 'ok' else None
        label = value.get('classification') if isinstance(value, dict) else None
        if isinstance(label, str) and label.strip().lower() in SANITY_VERDICTS:
            found.append(label.strip().lower())
    if found:
        return min(found, key=SANITY_VERDICTS.index), 'runs'
    if _sandbox_succeeded(session, child):
        named = [label for label in SANITY_VERDICTS if label in answer.lower()]
        if len(named) == 1:
            return named[0], 'answer'
    return 'unclassified', 'none'


def with_sanity_verdict(answer, verdict, source):
    """Put the code-derived verdict first; a model 'Verdict' heading or 'the claim is true' cannot stand."""
    # The header is the code's; the model's text below is never edited (rewording it could change its meaning).
    header = f'**Numerical sanity check: {verdict}.** {_VERDICT_NOTES[verdict]}'
    if verdict in ('no counterexample detected', 'inconclusive', 'unclassified'):
        header += (' Numerical runs do not prove a claim: any statement below that the claim is true is the '
                   'model\'s own reasoning, which the check did not verify.')
    return header + '\n\n' + answer.strip()


def _sandbox_observations(session, child, room):
    """This worker's own durable experiments, within ``room`` characters.

    A run that did not finish (timeout, error, memory) is listed with its status, its error
    and at most ``FAILED_RUN_OUTPUT`` characters of what it printed before it stopped:
    a stopped run is an observation too, never a run that did not happen. Successful
    runs share the rest equally. Nothing is cut silently: a longer result or output
    ends with the call that reads the rest.
    """
    records = [_saved_run(session, ident) for ident in child.get('experiment_ids', [])]
    if not records:
        return ''
    failed = [record for record in records if record['status'] != 'ok']
    failed_share = min(FAILED_RUN_OUTPUT, room // len(records))
    ok_share = (room - failed_share * len(failed)) // max(1, len(records) - len(failed))
    observations = []
    for record in records:
        left = ok_share if record['status'] == 'ok' else failed_share
        # The run is named by its purpose; its identifier is the argument of read_experiment.
        lines = [f"Python run: {record.get('purpose') or 'calculation'} (experiment_id for read_experiment: {record['id']})"]
        if record['status'] != 'ok':
            error = record.get('error') if isinstance(record.get('error'), dict) else {}
            reason = ': '.join(str(part) for part in (error.get('type'), error.get('message') or record.get('message')) if part)
            lines.append(f"Status: {record['status']}" + (f' ({reason})' if reason else '') +
                         '. This run did not finish: it ran, and its output is only what it printed before it stopped.')
        for label, part, value in (('Result', 'result', json.dumps(record['result'], ensure_ascii=False)
                                    if record.get('result') is not None else ''),
                                   ('Output' if record['status'] == 'ok' else 'Output before the stop', 'stdout',
                                    record.get('stdout') or '')):
            if value:
                shown = _shown(value, left, record['id'], part)
                left = max(0, left - len(shown))
                lines.append(label + ': ' + shown)
        if record.get('stdout_truncated'):
            lines.append('The sandbox kept only the beginning of this output: the run printed more than its output limit.')
        if record.get('artifacts'):
            lines.append('Saved files: ' + ', '.join(item['path'] for item in record['artifacts']))
        observations.append('\n'.join(lines))
    return '\n\n'.join(observations)


def worker_error(exc, mode):
    """The structured error of a failed worker; a retryable one leaves its child interrupted and resumable."""
    if isinstance(exc, WorkerInputError):
        return exc.as_dict()
    if isinstance(exc, ProviderChanged):
        # Started on another provider or endpoint: resumable once the original one is selected again.
        return {'code': 'provider_changed', 'message': str(exc), 'retryable': True,
                'scope': 'transport', 'details': {'mode': mode}}
    if isinstance(exc, ValueError):
        return WorkerInputError(str(exc), details={'mode': mode}).as_dict()
    if isinstance(exc, (AssistantBudget, TokenBudgetError, TesterAllowanceInsufficient)):
        return {'code': 'worker_budget_exhausted', 'message': str(exc), 'retryable': False,
                'scope': 'budget', 'details': {'mode': mode}}
    if isinstance(exc, DeepInfraGenerationError):
        return dict(code=exc.category, message=str(exc), retryable=exc.retryable, scope='provider', details={'mode': mode})
    if isinstance(exc, CallContextError):
        return {'code': 'context_overflow', 'message': str(exc), 'retryable': False,
                'scope': 'context', 'details': exc.details}
    return {'code': 'worker_operation_failed', 'message': str(exc), 'retryable': True,
            'scope': 'operation', 'details': {'mode': mode}}


def _investigation_sources(host, parent, child, save):
    """The plan's pinned primary sources, including peers finished since this Proof started."""
    from .gui.store import index_view
    with parent._state_lock:
        pinned = [*child.get('dependency_entries', []), *child.get('peer_source_entries', [])]
        known = {e['job_id'] for e in pinned} | set(child.get('peer_source_jobs', []))
        jobs = set(child.get('evidence_jobs', []))
        for slot in parent.state['investigation']['tasks'].values():
            if slot.get('status') in ('complete', 'failed') and slot.get('job_id'):
                jobs.add(slot['job_id'])
                jobs.update(slot.get('linked_job_ids', []))
    missing = jobs - known
    if missing:
        # The project index can contain other investigations. Start only at native
        # jobs in this plan and follow their own recorded links to source checks.
        entries = index_view(host.root, chat=host.chat_id)['entries']
        linked = missing | {job for e in entries if e['job_id'] in missing for job in e['linked_job_ids']}
        seen = {e['id'] for e in pinned}
        new = [e for e in entries if e['job_id'] in linked and e['id'] not in seen and e['documents']]
        with parent._state_lock:
            child.setdefault('peer_source_entries', []).extend(new)
            child['peer_source_jobs'] = sorted(known | missing)
            save()
        pinned.extend(new)
    return {'entries': [e for e in pinned if e.get('documents')]}


def run_worker(host, parent, budget, action, child, save):
    """Run one native mode. Host supplies workspace, engine, cards, approvals and write-up inputs."""
    from .gui import effort
    from .gui.agent_views import FAILED
    root, online, state_lock = host.root, host.online, parent._state_lock
    mode = action['mode']
    # A sibling may be copying the shared state for a checkpoint: a child record
    # gains keys only under the parent's state lock.
    with state_lock:
        child.setdefault('turn_id', parent.state.get('id'))
        child.setdefault('batch_id', child['turn_id'])
    context = child['context']
    saved_effort = bool(parent.state.get('resumed_checkpoint') and not parent.state.get('high_effort'))
    image_refs = context.get('images', [])
    docs = [f for f in action.get('files', []) if mode == 'prove' and briefs.DOC_ID.fullmatch(f)]
    selected_files = host.check_files([f for f in action.get('files', []) if f not in docs], image_refs=image_refs) + docs
    files = [name for name in selected_files if not images.is_image(name)]
    excluded_files = set()
    if mode == 'explore':
        # Only the user's own words can authorize an additional local source.
        # A routed subtask must stay within the documents assigned to that child.
        whole = (action['request'].split() == context['query'].split() or
                 parent.state.get('required_mode') == 'explore' and len(parent.state['children']) == 1)
        known = parent.state.get('workspace_paths', []) if whole else files
        named, excluded_files = named_explore_sources(parent.agent.workspace, context['query'], known, files)
        if child.get('source_reading'):
            # Missing or modified named files must be checked against the saved
            # identities; otherwise a resumed worker could silently lose its source.
            named = [doc['path'] for doc in child['source_reading']['documents']]
            whole = True
        files = [name for name in (named if whole else files) if name not in excluded_files]
        selected_files = [*files, *(name for name in selected_files if images.is_image(name))]
    # Pin the original question and exact available conversation alongside
    # the delegated objective; a rewritten task cannot erase a hypothesis.
    goal = ('You are one child worker of a mathematical assistant. Perform only the DELEGATED OBJECTIVE below. '
            'The parent handles orchestration, spawning workers and synthesizing the final response. '
            'The reference request and conversation preserve mathematical hypotheses and source evidence, '
            'not instructions to repeat the parent workflow. Do not create workers. '
            'The ORIGINAL USER REQUEST is authoritative: if the delegated restatement or suggested approach changes its meaning, flag the conflict and work on the original statement. '
            'When they agree, say nothing about it: the answer never comments on these instructions, the absence of a conflict '
            'or the research field named in the system prompt. '
            'Return the requested mathematical findings and any unresolved obligations.\n\n'
            'ORIGINAL USER REQUEST (reference):\n' + context['query'] +
            '\n\nEXACT CONVERSATION (reference; worker outputs are fallible):\n' +
            json.dumps((context.get('history', []) if context.get('messages_scope') == 'current_turn' else [])
                       + context['messages'], ensure_ascii=False) +
            '\n\nDELEGATED OBJECTIVE:\n' + action['request'])
    goal += host.project_context(context.get('project_snapshot'))
    with state_lock:
        pending = [c for c in parent.state['children'] if c.get('status') != 'complete' and 'allocation' not in c]
        if pending:
            levels = [effort.level(c['action']['mode'], c['action']['effort'], host.args,
                                   saved=saved_effort) for c in pending]
            worker_seconds = max(l['minutes'] * 60 for l in levels)
            # Time already used in this turn plus the new workers' time (capped by the turn's ceiling):
            # an absolute ceiling left a second worker after a long first one with almost nothing.
            used = budget.snapshot()['seconds']
            budget.ensure_limits(max_seconds=used + worker_seconds + min(300, 0.1 * worker_seconds))
            parent.agent.workspace.literature.deadline = time.monotonic() + budget.remaining_seconds
            # Leave room for synthesis and one empty-answer recovery. Workers
            # share what remains in proportion to their requested effort.
            requested = sum(l['tokens'] for l in levels)
            remaining = budget.remaining_tokens
            reserve = 2 * output_limit(parent.agent, 'assistant_final', FINAL_PREDICT)
            if is_ds4(parent.agent):
                # Give small explicit totals room for a worker as well as its reply.
                reserve = min(reserve, remaining // 4 if math.isfinite(remaining) else reserve)
            available = max(0, remaining - reserve)
            for candidate, level in zip(pending, levels):
                allocation = None if math.isinf(available) else int(available * level['tokens'] // requested)
                cap = getattr(host.args, 'proof_tokens' if candidate['action']['mode'] == 'prove' else 'research_tokens', None)
                if cap is not None:
                    allocation = cap if allocation is None else min(allocation, cap)
                if getattr(host.args, 'api_profile', None) in HOSTED_DS4:
                    preset = effort.limits(candidate['action']['mode'], candidate['action']['effort'], host.args,
                                           saved=saved_effort)
                    preset_cap = preset.get('tokens', level['tokens'])
                    if preset_cap is not None:
                        allocation = preset_cap if allocation is None else min(allocation, preset_cap)
                candidate['allocation'] = allocation
                candidate.setdefault('usage', {'tokens': 0, 'input_tokens': 0})
            save()
        allocation = child['allocation']
        error = (child.get('result') or {}).get('error')
        if isinstance(error, dict) and error.get('retryable') is True:
            child['result'] = None
        host.start_card(child, action, selected_files, image_refs)
        save()
    native = child.get('native_id')
    answer_id = native
    if mode in ('explore',) + USER_MODES and answer_id is None:
        answer_id = str(uuid.UUID(child['id'].removeprefix('child_')))
    use_original = (mode in ('writeup', 'summarize') and (parent.state.get('proof_routing') or {}).get('route') != 'workers') or (
        mode == 'literature' and parent.state.get('required_mode') == 'literature')
    native_request = context['query'] if use_original else child.get('shared_proof_objective', action['request'])
    if native and (not isinstance(native, str) or not _ID.fullmatch(native)):
        raise ValueError('Invalid native worker job ID')
    if mode in ('explore',) + USER_MODES:
        path = _answer_path(root, host.chat_id, answer_id)
        if path.exists():
            saved = json.loads(_read(path))
            if (saved['child_id'], saved['chat_id'], saved['mode'], saved['request'], saved['id']) != (
                    child['id'], host.chat_id, mode, action['request'], answer_id):
                raise ValueError('Saved answer identity differs from this child')
            answer_sha = hashlib.sha256(saved['answer'].encode()).hexdigest()
            if saved.get('answer_sha256', answer_sha) != answer_sha:
                raise ValueError('Saved answer digest mismatch')
            incomplete = saved['status'] == 'incomplete'
            outcome = (saved.get('warnings') or ['The worker answer is incomplete.'])[-1] if incomplete else 'Worker answer restored.'
            host.update_card(child, status='failed' if incomplete else 'done', outcome=outcome,
                             native_status=saved['status'], error=outcome if incomplete else None,
                             answer_id=answer_id, answer_sha256=answer_sha)
            host.saved()
            return dict(job_id=answer_id, status=saved['status'], answer=saved['answer'],
                        warnings=saved['warnings'], **saved.get('result_fields', {}))
    elif native and not child.get('job_id'):
        folder = 'proofs' if mode == 'prove' else 'checks' if mode == 'check' else 'research'
        directory = Path(root) / '.mathagent' / folder / native
        if directory.exists() or directory.is_symlink():
            state = (ProofStore.load(root, native).state if mode == 'prove'
                     else _read_state(_job_directory(root, native, folder), native))
            if state['goal'] != (native_request if mode == 'prove' else native_request.strip()):
                raise ValueError('Saved native job has a different mathematical objective')
            with state_lock:
                child['job_id'] = native
                host.update_card(child, job_id=native)
                save()
    agent = host.engine(mode)
    agent.notebook_source = context.get('notebook_source', '')
    if mode in ('explore', 'writeup'):
        # A write-up keeps the user's Personalization (standing instructions, macros) in its saved state.
        agent.personal_context = getattr(parent.agent, 'personal_context', '')
    images.bind(agent, image_refs)
    local_files = [f for f in files if f not in docs]
    selected_only = (parent.agent.workspace.selected_files_only or
                     bool(local_files) and not requests_workspace_search(context['query']))
    for name in excluded_files:
        try:
            agent.workspace.excluded_files.add(agent.workspace.path(name, pdf=True))
        except ValueError:
            pass  # An unsupported or escaping path was never readable.
    agent.workspace.pin_files(local_files, selected_only=selected_only)
    sandbox_error, unanswered = None, False
    numerical_assignment = routed_python_task(parent.state, action)
    if mode in USER_MODES:
        # Failures are raised inside the worker's try below, so the card reports them.
        selected = parent.state.get('required_mode') == mode
        if not selected and not numerical_assignment:
            sandbox_error = f'@{mode} runs only when you select it yourself'
        else:
            skill = 'python-lab' if mode == 'python_lab' else 'numerical-sanity-check'
            try:
                agent.workspace.numerics = host.bind_sandbox(skill, routed=not selected)
            except SandboxUnavailable as exc:
                sandbox_error = f'@{mode} needs the Python sandbox on this computer. {exc}'
    scoped = briefs.load_view(root, parent.state.get('index_view'))
    deps = child.get('evidence_jobs', [])
    if 'evidence_jobs' not in child and mode in ('explore', 'prove'):
        # In an ordinary turn a Proof or an exploration builds on what the Assistant had
        # obtained when it delegated: the results of the turn's earlier batches.
        with state_lock:
            deps = [c['job_id'] for c in parent.state['children'] if c.get('status') == 'complete'
                    and c.get('job_id') and c.get('batch_id') != child['batch_id']]
    if scoped is not None and deps and 'dependency_entries' not in child:
        # These results finished after the turn's view was frozen. Restrict their fresh
        # view to them and their own nested checks and runs, then checkpoint it before dispatch.
        from .gui.store import index_view
        fresh = index_view(root, chat=host.chat_id)['entries']
        linked = {*deps, *(job for e in fresh if e.get('job_id') in deps for job in e.get('linked_job_ids', []))}
        dependencies = {'entries': [e for e in fresh if e.get('job_id') in linked]}
        with state_lock:
            child['dependency_entries'] = dependencies['entries']
            save()
    if scoped is not None:
        local_view = dict(scoped, entries=[*child.get('dependency_entries', []),
                          *[e for e in scoped['entries'] if e['id'] not in {x['id'] for x in child.get('dependency_entries', [])}]])
        if mode == 'explore' and local_view['entries']:
            agent.workspace.index = briefs.IndexReader(root, local_view)
        if mode in ('explore', 'prove') and 'brief' not in child:
            brief = briefs.for_worker(root, local_view, action['request'], 'solver' if mode == 'prove' else mode,
                                      chat_id=host.chat_id, preferred=deps, exclude='' if mode == 'prove' else goal,
                                      max_chars=agent.workspace.result_chars)
            with state_lock:
                child['brief'] = brief
                save()
        if mode == 'explore' and (child.get('brief') or {}).get('text'):
            agent.optional_leads = child['brief']['text']
    host.show_experiments(agent.workspace)
    if mode in ('explore',) + USER_MODES and agent.workspace.numerics is not None:
        session = agent.workspace.numerics
        show_record = session.on_record
        def record_experiment(record):
            # Commit the link before notifying the GUI. The next model call can
            # time out or exhaust its budget after Docker has returned real data.
            with state_lock:
                ids = child.setdefault('experiment_ids', [])
                if record['id'] not in ids:
                    ids.append(record['id'])
                    save()
            if show_record:
                show_record(record)
        session.on_record = record_experiment
        if child.get('experiment_ids'):
            session.runs = len(child['experiment_ids'])
            goal += ('\nUse these saved results from your interrupted attempt. Repeat a run only if '
                     'its outcome cannot satisfy the objective:\n' + _sandbox_observations(session, child, _packet_room(agent)))
    if mode in ('explore',) + USER_MODES:
        # These workers return analysis; file mutation belongs to Write-up,
        # whose durable runner already checkpoints approved tool actions.
        schemas = agent.workspace.schemas
        if numerical_assignment:
            allowed = {'execute_python', 'read_experiment', 'read_skill_reference', 'list_files', 'read_file', 'search_text'}
            agent.workspace.schemas = lambda: [s for s in schemas() if s['function']['name'] in allowed]
            goal += ('\nStart the delegated calculation by calling execute_python. Use the saved experiment '
                     'output for your numerical answer. Report finite-sample averages as sample statistics. '
                     'Do not infer moment existence or an asymptotic law from a simulation. If a run errors, '
                     'correct it or report its limitation.')
        else:
            agent.workspace.schemas = lambda: [s for s in schemas() if s['function']['name'] not in ('write_file', 'run_python')]
        if mode == 'sanity_check':
            goal += ('\nEvery run sets RESULT["classification"] to exactly one of: ' + ', '.join(SANITY_VERDICTS)
                     + '. The harness states the verdict from these; do not write your own verdict heading.')
        if action['effort'] == 'low':
            agent.think = False
    review_companion = mode == 'literature' and child.get('companion') == 'review'
    level = effort.level(mode, action['effort'], host.args, saved=saved_effort)
    if mode in ('prove', 'literature') and child.get('job_id'):
        saved = (ProofStore.load(root, child['job_id']).state if mode == 'prove'
                 else _read_state(_job_directory(root, child['job_id']), child['job_id']))
        # New selectable profiles must not shorten a paused worker's saved
        # time allowance (including the retired seven/ten-pass profiles).
        level['minutes'] = max(0, saved['settings']['max_seconds'] - saved['seconds_used']) / 60
    if mode == 'explore' and agent.client.backend == 'openai' and getattr(agent.client, 'profile', 'default') == 'default':
        agent.client.thinking_budget_policy.setdefault('ordinary', 0.6)
    reserve = min(300, 0.1 * budget.pool.state['max_seconds'])
    worker_seconds = (REFCHECK_EFFORTS.get(action['effort'], REFCHECK_EFFORTS['high'])['seconds']
                      if mode == 'check' and not is_ds4(agent) else level['minutes'] * 60)
    limits = child.get('investigation_limits') or {}
    seconds = (min(worker_seconds, budget.remaining_seconds, limits['seconds'] - child['usage'].get('seconds', 0)) if limits
               else max(0.1, min(worker_seconds, budget.remaining_seconds - reserve)))
    if seconds <= 0:
        raise AssistantBudget('No time remains for this worker')
    if limits:
        level['requests'] = min(level['requests'], limits['requests'])
        level['tries'] = min(level['tries'], limits.get('tries', level['tries']))
        if agent.workspace.literature is not None:
            library = agent.workspace.literature
            library.reset_budget(max_requests=level['requests'], max_chars=level['chars'])
            if mode == 'explore':
                def save_sources():
                    with state_lock:
                        child['source_budget'] = library.snapshot()
                        save()
                if child.get('source_budget'):
                    library.restore(child['source_budget'])
                library.on_budget_change = save_sources
        sandbox = agent.workspace.numerics
        if sandbox is not None:
            def save_runs(runs):
                with state_lock:
                    child['sandbox_runs'] = runs
                    save()
            sandbox.runs = child.get('sandbox_runs', 0)
            sandbox.on_run_change = save_runs
    # The Review runs a lead, co-referees and judges at once (review v4): its own slots, up to the review's concurrency
    # within the interface's model-request limit, instead of the Assistant tree's few.
    slots = (threading.BoundedSemaphore(host.review_concurrency())
             if REVIEW_MODES.get(mode) == 'review' and hasattr(host, 'review_concurrency') else None)
    agent.client = budget.fork(agent.client, allowance=allocation, usage=child['usage'], max_seconds=seconds, slots=slots,
                               **({'input_allowance': limits['input_tokens']} if limits else {}))
    input_limit = budget.pool.state['max_input_tokens']
    configured_input = getattr(host.args, 'research_input_tokens', None)
    if configured_input is not None:
        input_limit = configured_input if input_limit is None else min(input_limit, configured_input)
    if getattr(host.args, 'api_profile', None) in HOSTED_DS4:
        input_limit = level['input'] if input_limit is None else min(input_limit, level['input'])
    if limits:
        input_limit = min(input_limit, limits['input_tokens']) if input_limit is not None else limits['input_tokens']
    runner = None

    def clear_progress():
        with state_lock:
            child.pop('progress', None)
            parent.state.pop('progress', None)
            for sibling in reversed(parent.state['children']):
                if sibling.get('progress') and sibling.get('status') == 'running':
                    parent.state['progress'] = copy.deepcopy(sibling['progress'])
                    break
            save()

    def emit(kind, value):
        if runner is not None:
            ident = runner.state['id'] if runner.state else None
            if ident and child.get('job_id') != ident:
                with state_lock:
                    child['job_id'] = ident
                    host.update_card(child, job_id=ident)
                    save()
            progress = getattr(runner, '_progress', None)
            if runner.state and callable(progress):
                with state_lock:
                    child['progress'] = copy.deepcopy(progress())
                    parent.state['progress'] = copy.deepcopy(child['progress'])
                    save()
        if kind in ('notice', 'tool', 'result'):
            # Activity is a display log. Source passages, tool arguments and raw results stay in their native record.
            if kind == 'tool':
                # A nested reference check relays its tool calls as '↳ <tool> …' and its notices as
                # '↳ check: …': name the inner tool, or the check itself.
                value = str(value or '').strip()
                value = 'check_reference' if value.startswith('↳ check:') else value.removeprefix('↳').strip()
                name = value.split(maxsplit=1)[0] if value else ''
                text = 'Using ' + (name if re.fullmatch('[A-Za-z0-9_]+', name) else 'a tool')
            elif kind == 'result':
                text = 'Tool result received'
            else:
                text = str(value).splitlines()[0][:240] if value else ''
            item = {'kind': kind, 'text': text, 'time': time.time(), 'job': mode, 'id': child.get('job_id'),
                    'child_id': child['id'], 'card_index': child.get('card_index'), 'turn_id': child.get('turn_id'),
                    'batch_id': child.get('batch_id')}
            with state_lock:
                timeline = child.setdefault('agent_timeline', [])
                timeline.append(item)
                del timeline[:-40]
                save()
            host.activity(mode, child.get('job_id'), item)

    if mode == 'explore' and online:
        # As in an Explore chat: check_reference, charged to this worker's share and
        # given at most the time of a reference check at the worker's own effort.
        agent.workspace.checker = nested_checker(
            agent, emit, share_source_budget=bool(limits), request=action['request'],
            max_seconds=seconds if is_ds4(agent) else min(seconds, REFCHECK_EFFORTS['high']['seconds']))
    try:
        if sandbox_error:
            raise ValueError(sandbox_error)
        if mode == 'prove':
            runner = ProofRunner(agent, emit, evidence_coordinator=True, review_concurrency=host.review_concurrency())
            if child.get('job_id'):
                result = runner.resume(child['job_id'])
            else:
                solve, verify = host.args.proof_solve_tokens, host.args.proof_verify_tokens
                if saved_effort and action['effort'] == 'low' and not is_ds4(agent):
                    solve, verify = min(solve, 4096), min(verify, 2048)
                repair = min(solve, host.args.proof_repair_tokens or solve)
                minimum = host.args.proof_min_solve_tokens or 128
                if allocation is not None and allocation < minimum + MIN_PROOF_REVIEW_TOKENS:
                    raise AssistantBudget(f'Proof needs {minimum} solver tokens and {MIN_PROOF_REVIEW_TOKENS} verifier tokens.')
                clean = briefs.proof_reference(context) if scoped is not None else None
                if clean is not None and child.get('shared_proof_objective') and context.get('project_snapshot'):
                    questions = briefs.earlier_project_questions(root, local_view,
                        set((child.get('brief') or {}).get('entries', [])), host.chat_id)
                    if questions:
                        clean['earlier_user_questions_in_project'] = questions
                passages = (briefs.primary_sources(local_view, [*deps, *briefs.named_entries(local_view, action['request'])])
                            if scoped is not None else [])
                result = runner.start(native_request, job_id=native, statement=child.get('proof_statement'),
                    reference_context=context if clean is None else clean, verifier_reference=clean,
                    solver_leads=child.get('brief'), source_passages=passages, source_files=files, max_rounds=level['tries'],
                    max_tokens=allocation, max_seconds=max(0.1, seconds), max_predict=solve,
                    verify_tokens=verify, repair_tokens=repair, min_solve_tokens=minimum,
                    verify_temperature=host.args.proof_verify_temperature)
            investigation_view = (partial(_investigation_sources, host, parent, child, save)
                                  if parent.state.get('investigation') else None)
            result = complete_proof_evidence(runner, result, view=local_view if scoped is not None else None,
                                             investigation_view=investigation_view)
            # The direction of a no-issue review (proved or refuted) words the card and the parent's result.
            claim = result.get('claim_outcome') or 'unclassified'
            reviewed = PROOF_OUTCOMES.get(claim) if result['status'] == 'candidate_complete' else None
            outcome = (reviewed + '.' if reviewed else
                       {'candidate_complete': 'The verifier found no issue in the response; read it to distinguish a proof from a counterexample.',
                        'uncertain': result['stop_reason'].removesuffix(' Candidate and review retained.')
                        if result['stop_reason'].startswith('Not accepted') else 'The model review is uncertain.',
                        'review_unavailable': 'Review unavailable; the candidate is preserved.'}.get(result['status'], result['stop_reason'] or result['status']))
            result = {'status': result['status'], 'answer': result['answer'], 'job_id': result['id'],
                      'review_status': result.get('selected_review_status'), 'outcome': claim,
                      'warnings': [reviewed + ': the model verifier found no issue. This does not certify the requested claim.'
                                   if reviewed else result['stop_reason']],
                      'artifact': str(Path(result['proof_path']).relative_to(root))}
        elif mode == 'check':
            # A quick verified lookup: its own small budgets (refcheck.EFFORTS), capped by
            # this worker's share. Reference context preserves the hypotheses; public queries must omit private source text.
            runner = CheckRunner(agent, emit)
            if child.get('job_id'):
                result = runner.resume(child['job_id'])
            else:
                if allocation is not None and allocation < 256:
                    raise AssistantBudget('No budget remains for a literature check.')
                objective = (action['request'] if (parent.state.get('proof_routing') or {}).get('route') == 'workers'
                             else parent.state['query'])
                result = runner.start(action['request'], effort=action['effort'], reference_context=child['context'],
                    job_id=native, max_requests=level['requests'], max_chars=level['chars'],
                    material=bibcheck.attached_material(agent.workspace, files),
                    lookup='precise' if precise_reference_request(objective) else 'standard',
                    max_tokens=allocation,
                    max_input_tokens=input_limit,
                    max_seconds=seconds)
            status = result['status']
            references = json.loads(runner.result_for_agent())
            outcome = {'answered': 'Reference confirmed; see the answer.',
                       'partial': 'No exact place confirmed; see the levels.'}.get(status, status.replace('_', ' ').capitalize() + '.')
            if references.get('lookup') == 'standard':
                outcome = ('Standard reference lookup finished; precise place unconfirmed.'
                           if references.get('references') else
                           'No bibliographic record confirmed; no verified recommendation.')
            result = {'status': status, 'answer': runner.state.get('answer') or runner._compose(),
                      'references': references, 'job_id': result['id'],
                      'artifact': str(Path(result['report_path']).relative_to(root)),
                      'warnings': list(runner.state.get('warnings', [])) +
                                  ([] if online else ['The specified source scope allowed only cached material.'])}
            if references.get('lookup') == 'standard':
                result['disposition'] = 'answer_with_qualification'
        elif mode in ('literature', 'writeup', 'summarize') or mode in REVIEW_MODES:
            # Literature writes a review from verified records; the exact conversation sets its scope.
            # Independent review calls use the same bounded global inference pool.
            if mode in ('writeup', 'summarize'):
                runner = WriteupRunner(agent, emit, styles_root=host.base)
            elif mode == 'literature':
                runner = ReviewRunner(agent, emit, concurrency=host.review_concurrency())
            else:
                if child.get('job_id'):
                    saved = _read_state(_job_directory(root, child['job_id']), child['job_id'])
                    review_type = runner_for_state(saved)
                else:
                    review_type = review_runner(REVIEW_MODES[mode])
                runner = (review_type(agent, emit, concurrency=host.review_concurrency())
                          if issubclass(review_type, ReviewBase) else review_type(agent, emit))
            runner.cancel = host.cancel
            runner.sandbox = getattr(host, 'sandbox', None)  # the Review's referees compute in it (policy "review")
            extra = 0
            previous_markdown = parent.state.get('previous_markdown') if mode == 'writeup' else None
            if previous_markdown:
                runner.previous_markdown = previous_markdown
            compile_job = parent.state.get('compile_job') if mode == 'writeup' else None
            if (not compile_job and mode == 'writeup' and not child.get('job_id') and not selected_files
                    and REPAIR_REQUEST.search(context['query'])):
                # "Fix this error" after a write-up that did not compile repairs that document; it is not rewritten.
                previous = host.previous_writeup()
                if previous and WriteupRunner.reopen_for_repair(root, previous, hint=context['query']):
                    with state_lock:
                        child['job_id'], extra = previous, max(0.1, seconds)
                        save()
            if compile_job:
                result = runner.compile_saved(compile_job, parent.state['id'])
            elif child.get('job_id'):
                result = runner.resume(child['job_id'], additional_seconds=extra)
            else:
                kwargs = dict(source_files=files, max_rounds=level['tries'], max_tokens=allocation,
                    max_input_tokens=input_limit,
                    max_seconds=max(0.1, seconds), max_requests=level['requests'], max_chars=level['chars'])
                if mode == 'literature':
                    kwargs['kind'] = mode
                elif mode in ('writeup', 'summarize'):
                    # Attached .sty/.cls/.bib files and complete .tex documents are templates; the rest is material.
                    templates = [f for f in files if Path(f).suffix.lower() in ('.sty', '.cls', '.bib') or
                                 (f.lower().endswith('.tex') and '\\begin{document}' in agent.workspace.text(agent.workspace.path(f)))]
                    kwargs['template_files'] = templates
                    kwargs['source_files'] = [f for f in selected_files if f not in templates]
                    kwargs['explain_templates'] = any(f.lower().endswith('.tex') for f in templates)
                    # The results so far, as the user saw them (this turn's finished jobs included, the
                    # request itself left out), in place of the raw history (which would fill the context twice).
                    kwargs.update(host.writeup_inputs(parent.state, previous_markdown, kwargs['source_files']),
                                  previous_markdown=previous_markdown, variant='summary' if mode == 'summarize' else None,
                                  scoped=True, deliver='download')
                if mode not in ('writeup', 'summarize'):
                    kwargs['reference_context'] = {key: value for key, value in context.items()
                                                   if key != 'review_reference'}
                    if mode == 'referee' and context.get('review_reference'):
                        kwargs['review_reference'] = context['review_reference']
                elif context.get('project_snapshot') is not None or context.get('images') or context.get('notebook_source'):
                    # The bounded visible conversation above is the writer's
                    # source. Pin its notebook separately without duplicating
                    # the parent's full native history in the job state.
                    kwargs['reference_context'] = {
                        'query': context['query'], 'messages': [], 'files': [],
                        'images': copy.deepcopy(context.get('images', [])),
                        **{key: copy.deepcopy(context[key]) for key in
                           ('project_snapshot', 'project_note_status', 'notebook_source') if key in context}}
                start = runner.start_companion if review_companion else runner.start
                result = start(native_request, job_id=native, **kwargs)
            verdict = (runner.state or {}).get('verdict')
            outcome = (result['status'].replace('_', ' ').capitalize() + (f' ({verdict.replace("_", " ")})' if verdict else '')
                       + '; see the saved report.')
            if result.get('stop_reason') and result['status'] in ('error', 'budget_violation'):
                outcome = result['stop_reason']
            result_error = result.get('worker_error')
            answer = result['report']
            if mode in ('writeup', 'summarize') and runner.state:
                outcome = answer = result.get('compile_answer') or runner.ready_message()
            elif mode == 'literature':
                # The review itself is the answer the user sees verbatim; the card gets one short line.
                outcome = review_outcome(runner.state) or outcome
                draft = runner.state.get('draft')
                answer = draft if isinstance(draft, str) and draft.strip() else answer
            coverage = result.get('coverage')
            result = {'status': result['status'], 'answer': answer, 'job_id': result['id'],
                      'artifact': str(Path(result['report_path']).relative_to(root))}
            if coverage:
                # What a quick review did and did not check: one proof is never a verdict on the document.
                result['coverage'] = coverage['summary']
            if mode == 'literature' and runner.state.get('outputs'):
                result['outputs'] = copy.deepcopy(runner.state['outputs'])
            if runner.state.get('progress'):
                result['progress'] = copy.deepcopy(runner.state['progress'])
            if mode in ('writeup', 'summarize') and runner.state:
                # What a write-up lacks goes back to the main agent (status needs_input: nothing was written).
                result['kind'] = runner.state.get('document_kind')
                result['needs'] = [{key: need[key] for key in ('type', 'what', 'concerns')}
                                   for need in runner.state.get('needs') or [] if need.get('blocking')]
            if result_error:
                result['error'] = result_error
        else:
            legacy_packet = child.get('source_overview') if mode == 'explore' else None
            if legacy_packet:
                # Old checkpoints hold a frozen packet but no digest of omitted text. Never join it to
                # changed files implicitly or label an earlier partial reading as complete.
                goal += '\n\nFROZEN SOURCE READING FROM THIS SAVED WORKER:\n' + legacy_packet
            fallback = FINAL_PREDICT if action['effort'] == 'low' else 16384
            output_cap = output_limit(agent, 'assistant_final', fallback)
            agent.predict = min(output_cap, agent.document_output_cap or output_cap)
            agent.max_rounds = getattr(host.args, 'max_rounds', None)
            narration_error, coverage = None, None
            room = _packet_room(parent.agent)
            try:
                if mode == 'explore' and files and not legacy_packet:
                    def save_reading(state):
                        with state_lock:
                            child['source_reading'] = copy.deepcopy(state)
                            save()
                    answer, coverage = explore_sources(agent, files, goal, emit, child.get('source_reading'), save_reading,
                                                       requested=action['request'])
                else:
                    answer = agent.run(goal, emit, fresh=True, recover_empty_tool_answer=True)
                    if legacy_packet and not legacy_packet.startswith('SOURCE TEXT'):
                        agent.last_stop_reason = 'Only the saved source excerpts are available for this resumed reading.'
                        answer += '\n\n' + agent.last_stop_reason
            except (AgentError, OSError, ValueError) as exc:
                if (mode not in USER_MODES or not child.get('experiment_ids')
                        or not _sandbox_succeeded(agent.workspace.numerics, child)):
                    raise
                # Execution and its interpretation are separate outcomes.
                answer = ''
                narration_error = str(exc)
            complete = (not narration_error and agent.last_stats.get('done_reason') in ('stop', 'tool_calls')
                        and bool(answer.strip()) and not agent.last_stop_reason
                        and all(type(agent.last_stats.get(key)) is int and agent.last_stats[key] >= 0
                                for key in ('eval_count', 'prompt_eval_count')))
            result = {'status': 'complete' if complete else 'incomplete', 'answer': answer,
                      'job_id': answer_id, 'warnings': [] if complete else [
                          narration_error or agent.last_stop_reason or 'The worker answer is incomplete.']}
            unanswered = not complete and not answer.strip()
            if mode in USER_MODES and (numerical_assignment or child.get('experiment_ids')):
                # Every run is reported, a stopped one with its status: the parent must not say it never ran.
                observations = _sandbox_observations(agent.workspace.numerics, child, room)
                succeeded = _sandbox_succeeded(agent.workspace.numerics, child)
                result['experiments'] = list(child.get('experiment_ids', []))
                if numerical_assignment and not succeeded:
                    complete, unanswered = False, True
                    result.update(status='incomplete', answer='The simulation produced no successful sandbox run.' + (
                                      '\n\n' + observations if observations else ''),
                                  warnings=[*result['warnings'], 'No successful numerical experiment was recorded.'])
                elif observations and not complete:
                    unanswered = False
                    result.update(status='partial' if succeeded else 'incomplete', answer=observations + '\n\n' + (
                                      'The numerical output was saved; its interpretation was not completed.' if succeeded
                                      else 'No run finished; the interpretation was not completed.') + (
                                      '\n\nIts unfinished interpretation:\n' + answer if answer.strip() else ''))
                elif numerical_assignment:
                    # The answer is the worker's interpretation. The measurements are what
                    # its runs printed, kept apart so the parent can tell the two.
                    result['recorded_measurements'] = observations
            elif (mode == 'explore' and not complete and child.get('experiment_ids')
                  and agent.workspace.numerics is not None):
                # An unfinished exploration (for example one that only announced more work) stays
                # incomplete, and keeps what its sandbox runs printed.
                unanswered = False
                result['experiments'] = list(child['experiment_ids'])
                result['answer'] = ((answer.rstrip() + '\n\n' if answer.strip() else '') + 'Its sandbox runs:\n\n'
                                    + _sandbox_observations(agent.workspace.numerics, child, room))
            if mode == 'sanity_check' and not unanswered and agent.workspace.numerics is not None:
                verdict, source = sanity_verdict(agent.workspace.numerics, child, result['answer'])
                result['verdict'] = verdict
                result['answer'] = with_sanity_verdict(result['answer'], verdict, source)
            if unanswered:
                result['job_id'] = None  # no answer is saved under that identifier
            if agent.workspace.numerics is not None and agent.workspace.numerics.runs:
                result['shown_files'] = ('The figures and files of its sandbox runs are already displayed in '
                                         'the conversation: refer to them, never insert image links.')
            if coverage is not None:
                result['source_coverage'] = coverage
            elif legacy_packet:
                result['source_excerpt'] = legacy_packet
            outcome = ('Worker finished.' if complete else 'Numerical results saved.' if result['status'] == 'partial'
                       else result['warnings'][-1])
        if result['status'] == 'paused' and not host.cancel.is_set():
            result.setdefault('error', {'code': 'worker_operation_failed', 'message': outcome,
                                        'retryable': True, 'scope': 'operation', 'details': {'mode': mode}})
        if host.cancel.is_set():
            host.update_card(child, status='stopped', outcome='Paused; work is saved.')
            save()
            raise KeyboardInterrupt
        clear_progress()
        answered = mode in ('explore',) + USER_MODES and not unanswered
        if answered:
            _save_answer(root, host.chat_id, child, result, agent, answer_id)
        failed = result['status'] in FAILED or bool(result.get('error'))
        host.update_card(child, status='stopped' if result['status'] == 'paused' else 'failed' if failed else 'done',
                          **({'answer_id': answer_id, 'answer_sha256': hashlib.sha256(result['answer'].encode()).hexdigest()}
                             if answered else {}), outcome=outcome,
                          error=(result.get('error') or {}).get('message') or (outcome if failed else None),
                          native_status=result['status'], error_code=(result.get('error') or {}).get('code'),
                          job_id=result['job_id'] if mode not in ('explore',) + USER_MODES else None,
                          **({'outputs': runner.state.get('outputs') or {}}
                             if mode in ('writeup', 'summarize', 'literature') and runner and runner.state else {}))
        host.saved()
        return result
    except KeyboardInterrupt:
        clear_progress()
        host.update_card(child, status='stopped', outcome='Paused; work is saved.')
        host.saved()
        raise
    except (AgentError, ValueError, OSError) as exc:
        clear_progress()
        if (isinstance(exc, WorkerInputError) and exc.code == 'needs_source' and mode == 'explain'
                and not action.get('files') and not image_refs and not host.cancel.is_set()):
            # "Explain the idea of the proof of Brouwer's theorem": no file or message holds a proof to
            # explain line by line, so the explanation comes from Explore, as for any classical result.
            fallback = {**action, 'mode': 'explore'}
            with state_lock:
                child['action'] = copy.deepcopy(fallback)
                child['job_id'] = None
                child.pop('native_id', None)
                host.update_card(child, mode='explore', job_id=None)
                save()
            return run_worker(host, parent, budget, fallback, child, save)
        error = worker_error(exc, mode)
        host.update_card(child, status='stopped' if error['retryable'] else 'failed',
                          outcome=str(exc), error=str(exc), error_code=error['code'],
                          agent_error={k: error[k] for k in ('code', 'message', 'scope', 'retryable') if k in error})
        host.saved()
        # Only a native job that exists is named: Resume reloads the job a child points to.
        return {'status': 'failed', 'answer': ('The saved Write-Up could not be compiled. ' + str(exc)
                if parent.state.get('compile_job') else ''), 'warnings': [str(exc)], 'error': error,
                'job_id': child.get('job_id')}


def _passed_over(runs, answer):
    """The runs a finished answer passes over: it names a later run of its own (by its identifier or the
    identifier's final hexadecimal part) but not these, like a first run it reports as wrongly scaled."""
    def named(run):
        return run in answer or re.search(r'(?<![0-9a-f])' + run[-6:] + r'(?![0-9a-f])', answer)
    runs = sorted(runs)  # an identifier starts with its creation time
    last = max((i for i, run in enumerate(runs) if named(run)), default=0)
    return [run for run in runs[:last] if not named(run)]


def export_answer_evidence(state):
    """The small worker's output remains a model-written lead, including after a successful run."""
    from .numerics.artifacts import EXPERIMENT_ID
    links = [('checks', k) for k in state.get('checks', []) if isinstance(k, str) and _ID.fullmatch(k)]
    runs = [k for k in state.get('experiments', []) if isinstance(k, str) and EXPERIMENT_ID.fullmatch(k)]
    links += [('experiments', k) for k in runs]
    finished = state.get('status') in ('complete', 'done', 'completed')
    qualifiers = [] if finished else ['unfinished']
    text = ' '.join(state['answer'].split())
    excerpt = text if len(text) <= 220 else text[:219] + '…'
    evidence = {'sha256': hashlib.sha256(state['answer'].encode()).hexdigest()}
    passed_over = _passed_over(runs, state['answer']) if finished else []
    if passed_over:
        evidence['superseded_runs'] = passed_over
    return {'kind': 'lead', 'mode': state['mode'], 'text': state['request'], 'status': 'lead (model-written)',
            'qualifiers': qualifiers, 'summary': excerpt, 'evidence': evidence, 'links': links}
