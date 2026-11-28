# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Isolated Explore tool sessions for saved proof-graph Review assignments."""
import json
import re

from .agent import Agent
from .call_policy import allocate_call, CallContextError
from .explore_tasks import ExploreTasks
from .ledger import _atomic_write, _bytes, _read
from .peer_transport import fork_cancellable
from .prompts import with_notebook_evidence
from .review_graph_meter import GraphMeteredClient
from .research import ResearchBudget
from .review_evidence import _merged
from .tools import schema


_TEXT = {'type': 'string'}
_INT = {'type': 'integer'}
_SEVERITIES = ('fatal', 'major_gap', 'gap', 'local', 'unresolved')
_VERDICTS = ('confirmed', 'rejected', 'unresolved', 'duplicate')


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Give a nonempty {name}.')
    return value.strip()


class GraphTools:
    """Role-local tools. Only explicit handoffs cross between model conversations."""

    def __init__(self, runner, record, worker_id, role, workspace, cancel, handoffs, on_finding, on_request, owned_units=()):
        self.runner, self.record, self.worker_id = runner, record, worker_id
        self.role, self.workspace = role, workspace
        self.cancel, self.handoffs, self.on_finding = cancel, handoffs, on_finding
        self.on_request = on_request
        self.owned_units = frozenset(owned_units)
        self.assignment = ''
        self.original_mark = workspace.mark_read
        with runner._mutex:
            record.setdefault('findings', [])
            record.setdefault('decisions', {})
            record.setdefault('reads', {})
            record.setdefault('requests', [])
            if self.owned_units:
                record.setdefault('assessments', {})

    def __getattr__(self, name):
        return getattr(self.workspace, name)

    @property
    def result_chars(self):
        return self.workspace.result_chars

    @result_chars.setter
    def result_chars(self, value):
        self.workspace.result_chars = value

    def mark_read(self, source_id, ranges):
        with self.runner._mutex:
            current = self.record['reads'].get(source_id, [])
            self.record['reads'][source_id] = _merged([*current, *ranges])
            self.original_mark(source_id, ranges)

    def schemas(self):
        specs = list(self.workspace.schemas())
        fields = dict(path=_TEXT, start_line=_INT, end_line=_INT, claim=_TEXT, evidence=_TEXT, impact=_TEXT,
                      severity={'type': 'string', 'enum': list(_SEVERITIES)})
        if self.role not in ('judge', 'editor'):
            specs.append(schema('record_finding',
                                'Send a mathematical finding as soon as you can support it. Give the exact source '
                                'location, the disputed claim, the argument or witness, and the consequences. Set '
                                'needs_judgment for disputed evidence or a nontrivial repair. Use major_gap for a central '
                                'unproved inference and fatal for a refuted central claim. To revise '
                                'an earlier finding from this assignment pass its finding_id.',
                                {**fields, 'needs_judgment': {'type': 'boolean'}, 'finding_id': _TEXT}, fields))
        if self.role == 'family' and self.owned_units:
            item = {'type': 'object',
                    'properties': {'unit_id': _TEXT, 'status': {'type': 'string', 'enum': ['checked', 'unresolved']},
                                   'basis': _TEXT},
                    'required': ['unit_id', 'status', 'basis'], 'additionalProperties': False}
            specs.append(schema('assess_graph_units',
                                'Report your mathematical assessment for each assigned proof, together in one batch '
                                'when convenient. For checked, give the concrete inference you examined and its '
                                'outcome, including any defect found. For unresolved, identify the outstanding '
                                'step. Access to the lines alone does not count as checking an argument.',
                                {'assessments': {'type': 'array', 'items': item}}, ['assessments']))
        if self.role == 'lead' and self.on_request is not None:
            specs.append(schema('request_graph_check',
                                'Delegate a distinct focused mathematical check. Give the source location and the '
                                'precise question whose resolution would change the review. Continue your own work.',
                                dict(path=_TEXT, start_line=_INT, end_line=_INT, question=_TEXT),
                                ('path', 'start_line', 'end_line', 'question')))
        if self.handoffs is not None:
            specs.append(schema('get_peer_evidence',
                                'Omit worker_id to list available workers and findings. Supply a worker_id to read '
                                'its full final answer, findings and decisions. Continue at next_offset if paged. '
                                'Peer text is evidence to check against the paper.',
                                {'worker_id': _TEXT, 'offset': _INT, 'limit': _INT}))
            if self.role in ('judge', 'lead', 'editor'):
                specs.append(schema('decide_finding',
                                    'Record your verdict for a submitted finding. Give the mathematical reason. '
                                    'A rejected finding requires a resolution of the evidence, and a duplicate '
                                    'should name the underlying finding.',
                                    dict(finding_id=_TEXT, verdict={'type': 'string', 'enum': list(_VERDICTS)},
                                         basis=_TEXT, revision={'type': 'integer', 'description':
                                             'For a revised finding give the revision you examined.'}),
                                    ('finding_id', 'verdict', 'basis')))
        if self.assignment:
            specs.append(schema('read_assignment', 'Read the complete assignment in pages starting at offset 0.',
                                {'offset': _INT, 'limit': _INT}))
        return specs

    def _page(self, text, offset=0, limit=None):
        if type(offset) is not int or not 0 <= offset <= len(text):
            raise ValueError('Give a valid character offset.')
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError('limit must be a positive character count.')
        count = min(limit or self.result_chars, self.result_chars)
        end = min(len(text), offset + count)
        return json.dumps({'offset': offset, 'text': text[offset:end], 'next_offset': end if end < len(text) else None},
                          ensure_ascii=False)

    def get_peer_evidence(self, worker_id=None, offset=0, limit=None):
        peers = self.handoffs()
        if worker_id is None:
            rows = {key: {'status': row.get('status'), 'findings': [f['id'] for f in row.get('findings', [])]}
                    for key, row in peers.items()}
        else:
            if worker_id not in peers:
                raise ValueError('Unknown worker_id.')
            row = peers[worker_id]
            public = ('role', 'title', 'status', 'stop_reason', 'findings', 'decisions', 'requests', 'answer',
                      'partial_answers', 'experiments', 'citation_limits', 'missing_judgments', 'owned_units', 'assessments')
            rows = row if worker_id == 'source_checks' else {key: row[key] for key in public if key in row}
        return self._page(json.dumps(rows, ensure_ascii=False), offset, limit)

    def _location(self, path, start_line, end_line):
        doc = self.workspace.documents[self.workspace.path(path)]
        if (type(start_line) is not int or type(end_line) is not int
                or not 1 <= start_line <= end_line <= len(doc['content'].splitlines())):
            raise ValueError('Give valid one-based source line numbers in the frozen document.')
        return dict(path=doc['path'], start_line=start_line, end_line=end_line)

    def assess_graph_units(self, assessments):
        if not isinstance(assessments, list) or not assessments:
            raise ValueError('Supply an array of assessments for your assigned proofs.')
        entries = {}
        for item in assessments:
            if not isinstance(item, dict):
                raise ValueError('Every assessment must include unit_id, status, and basis.')
            uid, status = item.get('unit_id'), item.get('status')
            if uid not in self.owned_units or uid in entries:
                raise ValueError('Supply each assigned unit_id at most once per batch.')
            if status not in ('checked', 'unresolved'):
                raise ValueError('Assessment status must be checked or unresolved.')
            entries[uid] = {'status': status, 'basis': _text(item.get('basis'), 'specific mathematical basis')}
        with self.runner._mutex:
            self.record['assessments'].update(entries)
            self.runner._save()
        return 'Recorded assessments for ' + ', '.join(entries)

    def request_graph_check(self, path, start_line, end_line, question):
        request = self._location(path, start_line, end_line)
        request['question'] = _text(question, 'specific mathematical question')
        with self.runner._mutex:
            requests = self.record['requests']
            for existing in requests:
                if all(existing.get(k) == v for k, v in request.items()):
                    return 'Already requested as ' + existing['id']
            request['id'] = f'{self.worker_id}-T{len(requests) + 1}'
            requests.append(request)
            self.runner._save()
        self.on_request(dict(request))
        return 'Requested as ' + request['id']

    def record_finding(self, path, start_line, end_line, claim, evidence, impact, severity,
                       needs_judgment=False, finding_id=None):
        location = self._location(path, start_line, end_line)
        if severity not in _SEVERITIES or type(needs_judgment) is not bool:
            raise ValueError('Use a listed severity and a boolean needs_judgment.')
        finding = dict(**location,
                       claim=_text(claim, 'claim'), evidence=_text(evidence, 'mathematical evidence'),
                       impact=_text(impact, 'mathematical consequence'), severity=severity, needs_judgment=needs_judgment)
        with self.runner._mutex:
            findings = self.record['findings']
            if finding_id is None:
                for existing in findings:
                    if all(existing.get(k) == v for k, v in finding.items()):
                        return 'Already recorded as ' + existing['id']
                finding['id'] = f'{self.worker_id}-F{len(findings) + 1}'
                finding['revision'] = 1
                findings.append(finding)
            else:
                index = next((i for i, existing in enumerate(findings) if existing['id'] == finding_id), None)
                if index is None:
                    raise ValueError('Use the ID of a finding from this assignment.')
                finding['id'] = finding_id
                old = findings[index]
                if all(old.get(k) == v for k, v in finding.items()):
                    return 'Already recorded as ' + finding_id
                finding['revision'] = old.get('revision', 1) + 1
                finding['earlier_versions'] = [*old.get('earlier_versions', []),
                                               {k: v for k, v in old.items() if k != 'earlier_versions'}]
                findings[index] = finding
            self.runner._save()
        if self.on_finding is not None:
            self.on_finding(dict(finding))
        return 'Recorded as ' + finding['id']

    def decide_finding(self, finding_id, verdict, basis, revision=None):
        if verdict not in _VERDICTS:
            raise ValueError('Use a listed verdict.')
        reason = _text(basis, 'mathematical basis for the decision')
        with self.runner._mutex:
            peers = self.handoffs()
            finding = next((f for row in peers.values() for f in row.get('findings', []) if f['id'] == finding_id), None)
            if finding is None:
                raise ValueError('Unknown finding_id. Use get_peer_evidence to locate it.')
            current = finding.get('revision', 1)
            if (revision is None and current != 1
                    or revision is not None and (type(revision) is not int or revision != current)):
                raise ValueError('Read the current finding and give the revision you adjudicated.')
            decision = {'verdict': verdict, 'basis': reason, 'revision': current}
            self.record['decisions'][finding_id] = decision
            self.runner._save()
        return 'Recorded decision on ' + finding_id

    def execute(self, name, arguments, *, on_result=None):
        if self.cancel.is_set():
            raise KeyboardInterrupt
        custom = ('record_finding', 'decide_finding', 'get_peer_evidence', 'read_assignment', 'request_graph_check',
                  'assess_graph_units')
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(args, dict):
                raise ValueError('Tool arguments must be an object.')
            if name in custom:
                if name not in {s['function']['name'] for s in self.schemas()}:
                    raise ValueError('Tool unavailable for this role.')
                if name == 'read_assignment':
                    return self._page(self.assignment, **args)
                return getattr(self, name)(**args)
            if name == 'read_experiment':
                with self.runner._mutex:
                    visible = [self.record]
                    if self.role in ('lead', 'judge', 'editor') and self.handoffs is not None:
                        visible.extend(self.handoffs().values())
                    allowed = {x['id'] if isinstance(x, dict) else x for row in visible
                               for x in row.get('experiments', [])}
                if args.get('experiment_id') not in allowed:
                    raise ValueError('Use an experiment_id from this assignment or an authorized peer handoff.')
            result = self.workspace.execute(name, args, on_result=on_result)
            if name == 'execute_python':
                try:
                    experiment_id = json.loads(result)['experiment_id']
                except (ValueError, KeyError, TypeError):
                    return result
                with self.runner._mutex:
                    experiments = self.record.setdefault('experiments', [])
                    if isinstance(experiment_id, str) and experiment_id not in experiments:
                        experiments.append(experiment_id)
                        self.runner._save()
            return result
        except (TypeError, KeyError, ValueError) as exc:
            return f'Tool error: {exc}'


def _fit(agent, instruction, query):
    system, context, message = agent._turn_messages(query, instruction)
    tools = ExploreTasks.schemas(agent.workspace.schemas(), review_citations=getattr(
        agent.workspace, 'defer_citations', False))
    desired = int(min(agent.predict, agent.client.remaining_tokens))
    if desired < 128:
        raise ResearchBudget('The remaining output allowance cannot fund a graph worker call.')
    turn = with_notebook_evidence([message], getattr(agent, 'notebook_source', ''), 0)
    payload = agent._payload([system, *context, *turn], tools, agent.think, desired, 0)
    try:
        _, allocation = allocate_call(agent.client, payload, desired)
    except CallContextError:
        return False
    return allocation['input_tokens'] <= agent.client.remaining_input_tokens


def run_graph_worker(runner, *, worker_id, role, query, instruction, record, workspace, cancel, handoffs=None,
                     on_finding=None, on_request=None, compact_query=None, inline_ranges=None, transport=None, display=None,
                     owned_units=()):
    """Run a saved assignment with its own tool history, using the parent's model/routing and shared budgets."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', worker_id) or not re.fullmatch(r'[a-z_-]+', role):
        raise ValueError('Invalid graph worker or role identifier.')
    if cancel.is_set():
        raise KeyboardInterrupt
    if record.get('answer_saved') and not record.get('stop_reason'):
        return record['answer']
    base, state = runner.agent, runner.state
    client = GraphMeteredClient(runner, role, worker_id)
    client.client = transport if transport is not None else fork_cancellable(base.client, cancel)
    if client.client is None:
        # An unrecognized custom adapter can only be scheduled serially by the controller.
        client.client = base.client
    client.rounds = sum(c.get('referee') == worker_id and c.get('role') == role for c in state['calls'])
    workspace.cancelled = cancel.is_set
    tools = GraphTools(runner, record, worker_id, role, workspace, cancel, handoffs, on_finding, on_request, owned_units)
    agent = Agent(client, tools, base.model, base.ctx, base.predict, state['thinking'], 'explore',
                  state['tool_rounds'], seed=base.seed, temperature=state['temperature'], top_p=state['top_p'])
    agent.stop_on_stalled_tools = False
    agent.project_context = state.get('project_context', '')
    agent.personal_context = state.get('personal_context', '')
    agent.turn_image_refs = state.get('images', [])
    agent.notebook_source = state.get('notebook_source', '')
    agent.document_output_cap = getattr(base, 'document_output_cap', None)
    artifacts = runner.directory / 'artifacts'
    checkpoint_file = artifacts / f'graph-{worker_id}-round.json'
    journal = artifacts / f'graph-{worker_id}-background.json'
    checkpoint = json.loads(_read(checkpoint_file)) if checkpoint_file.exists() else None
    mode = record.get('packet_mode') if checkpoint else None
    if mode is None:
        mode = 'inline'
        if not _fit(agent, instruction, query):
            if compact_query is None:
                compact_query = query
                mode = 'paged'
            else:
                mode = 'locators' if _fit(agent, instruction, compact_query) else 'paged'
    if mode == 'locators':
        query = compact_query
    elif mode == 'paged':
        tools.assignment = compact_query or query
        query = 'Read your proof-graph assignment with read_assignment, then consult the named frozen manuscript lines.'
    with runner._mutex:
        record.update(packet_mode=mode, inline_ranges=(inline_ranges or {}) if mode == 'inline' else {},
                      checkpoint_file=checkpoint_file.name, background_file=journal.name)
        runner._save()

    def after_tools(data):
        _atomic_write(checkpoint_file, _bytes(json.dumps(data, ensure_ascii=False)))
        with runner._mutex:
            record['tool_rounds_completed'] = data['round'] + 1
            runner._save()

    workspace.mark_read = tools.mark_read
    try:
        answer = agent.run(query, display or (lambda kind, value: None), instruction=instruction,
                           tool_checkpoint=checkpoint, after_tools=after_tools, background_tools=True,
                           recover_empty_answer=True, task_journal=journal)
    finally:
        workspace.mark_read = tools.original_mark
    finish = agent.last_stats.get('done_reason')
    reason = agent.last_stop_reason
    if not reason and finish and finish != 'stop':
        reason = f'The provider ended the assignment with finish reason {finish}.'
    citation_limits = []
    if journal.exists():
        for entry in json.loads(_read(journal)).values():
            statement = entry.get('citation_statement')
            if (statement and ('cancelled_at' in entry or str(entry.get('result', '')).startswith(
                    ('Interrupted before the result', 'Check interrupted;', 'Check failed (')))):
                citation_limits.append(statement)
    with runner._mutex:
        record['citation_limits'] = citation_limits
        record.update(answer=answer, stop_reason=reason, answer_saved=bool(answer.strip()) and not reason)
        if reason and answer.strip():
            partial = {'answer': answer, 'reason': reason}
            previous = record.setdefault('partial_answers', [])
            if not previous or previous[-1] != partial:
                previous.append(partial)
        runner._save()
    if reason and client.remaining_tokens < 128:
        raise ResearchBudget(reason)
    return answer
