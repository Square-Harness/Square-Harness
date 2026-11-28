# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Saved proof-family assignments, concurrent mathematical reading, and one referee report."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

from .explore_review import ExploreReviewRunner, FOLLOWUP_PROMPT
from .ledger import _atomic_write, _bytes, _read
from .peer_transport import fork_cancellable
from .refcheck import nested_checker
from .review import GRAPH_PIPELINE
from .review_challenge import ChallengeCancellation
from .review_evidence import _code_span
from .review_graph_meter import GraphMeteredClient
from .review_graph_plan import ROLE_PROMPTS, build_graph_plan, render_packet
from .review_graph_sources import check_sources
from .review_graph_worker import run_graph_worker


JUDGE_PROMPT = """Give an independent decision on the supplied mathematical objection(s).
Read each cited passage and the assumptions it uses. Determine whether the example is admissible,
the proof step follows, and any proposed repair actually suffices. A problem with one proof can
leave the theorem open. Use decide_finding for every assigned ID with a brief mathematical reason.
Record uncertainty when the available evidence cannot decide. Give one short handoff to the author of the report."""
EXTRA_TEST_PROMPT = """Independently resolve the specific mathematical question at the given passage.
Check its exact assumptions in the frozen manuscript. Supply a calculation, derivation, or admissible
example that decides it, or identify the point still open. Record any located defect with record_finding.
Use major_gap for a central proof failure, fatal for a disproved statement, and request judgment for a
disputed conclusion or nontrivial proposed repair. Return the evidence to the main reader."""
EDITOR_PROMPT = """Write the final referee report for the mathematician who wrote this manuscript.
The lead reading, independent checks, fresh mathematical judgments, and source checks are available.
Read their complete handoffs, including points raised only in prose and the assessed implications
for individual proofs. Resolve mathematical disagreements against the manuscript and explain material
uncertainty that remains. Use decide_finding for each finding ID with the basis for any change or grouping.
Start with the most consequential conclusion. For each defect give the location, exactly why it matters,
what remains true, and a repair only when supported. Include repairable gaps even when the theorem survives.
Respect the requested scope and language. State limitations that matter for the author. Finish once this
material supports the report. Keep internal worker IDs and the history of the investigation out of it."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=1)


def _overlap(a, b):
    return (a['path'] == b['path'] and a['start_line'] <= b['end_line']
            and b['start_line'] <= a['end_line'])


def _judge_assignments(workers):
    """One targeted batch per originating handoff. An overlapping contrary assessment travels with it."""
    reports = [(name, f) for name, row in workers.items()
               if row['role'] in ('lead', 'family', 'test') and row['status'] == 'done'
               for f in row.get('findings', ())]
    assigned = {finding for row in workers.values() if row['role'] == 'judge' for finding in row['candidates']}
    groups = {}
    for owner, finding in reports:
        if finding['id'] in assigned:
            continue
        contrary = [f for other, f in reports if other != owner and f['severity'] != finding['severity']
                    and _overlap(finding, f)]
        substantial = finding['severity'] in ('fatal', 'major_gap') or finding.get('needs_judgment')
        if substantial or contrary:
            batch = groups.setdefault(owner, {})
            for item in [finding, *contrary]:
                batch[item['id']] = item
                assigned.add(item['id'])
    return [list(batch.values()) for batch in groups.values()]


class GraphReviewRunner(ExploreReviewRunner):
    PIPELINE = GRAPH_PIPELINE

    def _extra_state(self):
        fields = super()._extra_state()
        fields.pop('challenge')
        fields['graph_review'] = {'workers': {}}
        return fields

    def _plan(self):
        graph = self.state['graph_review']
        path = self.directory / 'artifacts' / 'review-proof-graph.json'
        if path.exists():
            plan = json.loads(_read(path))
        else:
            plan = build_graph_plan(self.state['sources'], target=self.state.get('target', ''), goal=self.state['goal'])
            _atomic_write(path, _bytes(_json(plan)))
        graph['plan'] = path.name
        self._save()
        return plan

    def _progress(self):
        progress = super()._progress()
        workers = self.state.get('graph_review', {}).get('workers', {})
        if workers:
            progress.update(unit='graph assignments', completed=sum(r['status'] == 'done' for r in workers.values()),
                            total=len(workers))
        return progress

    def _peer_evidence(self, omit=None, candidates=None):
        with self._mutex:
            graph = self.state['graph_review']
            evidence = {}
            for name, row in graph['workers'].items():
                if name == omit:
                    continue
                if candidates is not None:
                    findings = [f for f in row.get('findings', ()) if f['id'] in candidates]
                    if findings:
                        evidence[name] = {'role': row['role'], 'findings': copy.deepcopy(findings),
                                          'experiments': list(row.get('experiments', ())),
                                          'citation_limits': list(row.get('citation_limits', ()))}
                else:
                    evidence[name] = {k: copy.deepcopy(row[k]) for k in
                                      ('role', 'title', 'status', 'answer', 'findings', 'decisions', 'requests',
                                       'stop_reason', 'citation_limits', 'experiments', 'missing_judgments',
                                       'owned_units', 'assessments') if k in row}
            if candidates is None and 'source_checks' in graph:
                evidence['source_checks'] = {'status': 'done', 'answer': _json(graph['source_checks'])}
            return evidence

    def _workspace(self, reviewer, transport, cancel, name):
        # Share the immutable documents, the job's thread-safe Python allowance and retrieval budget.
        # Give each reader its own read callback, source tools and separately attributed citation calls.
        workspace = copy.copy(reviewer.workspace)
        workspace.cancelled = cancel.is_set
        workspace.mark_read = self._mark
        workspace.encodings = {}
        workspace.source_archives = {}
        if workspace.literature is not None:
            # ExploreTasks installs its own cancellation callback for the duration of a role. The
            # copied handle retains the job's shared stats, retrieval lock, and offline source scope.
            workspace.literature = copy.copy(workspace.literature)
            if workspace.web is not None:
                workspace.web = copy.copy(workspace.web)
                workspace.web.literature = workspace.literature
        if workspace.checker is not None:
            reference_agent = copy.copy(reviewer)
            reference_agent.workspace = workspace
            reference_agent.client = GraphMeteredClient(self, 'reference', name)
            reference_agent.client.client = transport
            workspace.checker = nested_checker(reference_agent, self.emit, share_source_budget=True,
                                               share_job_budget=True, original_first=True)
        return workspace

    def _run_reviewer(self, reviewer, query, display, checkpoint, after_tools):
        state = self.state
        if (state.get('review_reference') or {}).get('followup'):
            reviewer.notebook_source = state.get('notebook_source', '')
            return reviewer.run(query, display, instruction=FOLLOWUP_PROMPT, recover_empty_answer=True,
                                tool_checkpoint=checkpoint, after_tools=after_tools, background_tools=True,
                                task_journal=self.directory / 'artifacts' / 'review-background.json')
        plan = self._plan()
        assignments = [plan['lead'], *([plan['test']] if plan.get('test') else []), *plan['families']]
        graph = state['graph_review']
        records = graph['workers']
        for assignment in assignments:
            row = records.setdefault(assignment['id'], {'role': assignment['role'], 'title': assignment['title'],
                                                        'status': 'pending'})
            row['owned_units'] = [uid for uid in assignment['units'] if plan['units'][uid].get('proofs')
                                  and plan['units'][uid].get('owner') == assignment['id']]
        records.setdefault('REPORT', {'role': 'editor', 'title': 'Final report', 'status': 'pending'})
        self._save()

        cancel = ChallengeCancellation(getattr(self, 'cancel', None) or getattr(self.agent.client, 'cancel', None))
        first_transport = fork_cancellable(self.agent.client, cancel)
        slots = self.concurrency if first_transport is not None else 1
        graph['model_slots'] = slots
        # A custom transport without support for independent streams runs the same assignments serially.
        def transport():
            nonlocal first_transport
            if first_transport is not None:
                client, first_transport = first_transport, None
                return client
            return fork_cancellable(self.agent.client, cancel) or self.agent.client

        wake = Queue()
        specs = {a['id']: a for a in assignments}

        def execute(name, client):
            row = records[name]
            role = row['role']
            evidence = None
            ranges = {}
            if name in specs:
                assignment = specs[name]
                instruction = ROLE_PROMPTS[role]
                task = render_packet(plan, assignment, state['sources'], state['goal'])
                compact = render_packet(plan, assignment, state['sources'], state['goal'], inline=False)
                ranges = assignment.get('inline_ranges', {})
                if role == 'lead':
                    evidence = lambda: self._peer_evidence(name)
            elif role == 'judge':
                instruction = JUDGE_PROMPT
                ids = row['candidates']
                evidence = lambda: self._peer_evidence(name, ids)
                compact = f"{query}\n\nJudge only the following candidate IDs: {', '.join(ids)}. " \
                          "Read their evidence with get_peer_evidence."
                task = compact + '\n\nCandidate handoffs:\n' + _json(evidence())
            elif role == 'test':
                instruction = EXTRA_TEST_PROMPT
                req = row['request']
                task = (query + f"\n\nCheck {req['path']}, lines {req['start_line']}–{req['end_line']}.\n"
                        + req['question'])
                compact = task
            else:
                instruction = EDITOR_PROMPT
                evidence = lambda: self._peer_evidence(name)
                material = evidence()
                compact = query + '\n\nRead the handoffs using get_peer_evidence. Available: ' + ', '.join(material)
                task = compact + '\n\nComplete handoffs:\n' + _json(material)
            with self._mutex:
                row['status'] = 'running'
                row.pop('stop_reason', None)
                self._save()
            self.emit('notice', f"Review · {row['title']}")
            def finding_sent(finding):
                self.emit('notice', f"Review · {name} recorded an observation at "
                          f"{finding['path']}:{finding['start_line']} — {finding['claim']}")
            try:
                result = run_graph_worker(self, worker_id=name, role=role, query=task, compact_query=compact,
                                          instruction=instruction, record=row, transport=client, cancel=cancel,
                                          workspace=self._workspace(reviewer, client, cancel, name),
                                          handoffs=evidence, inline_ranges=ranges, owned_units=row.get('owned_units', ()),
                                          on_finding=finding_sent,
                                          on_request=lambda _: wake.put(None),
                                          display=display if role == 'editor' else None)
                with self._mutex:
                    row['answer'] = result
                    row['status'] = 'done' if result.strip() and not row.get('stop_reason') else 'partial'
                    if role == 'judge':
                        row['missing_judgments'] = [i for i in row['candidates'] if i not in row.get('decisions', {})]
                    self._save()
            except BaseException as exc:
                with self._mutex:
                    row.update(status='partial', stop_reason=str(exc) or 'Interrupted')
                    self._save()
                raise

        def additions():
            # Requests are persisted by the lead before the scheduler is signalled. Rebuilding this list on
            # Resume also handles interruption between publication of a handoff and admission of its worker.
            with self._mutex:
                for request in records[plan['lead']['id']].get('requests', ()):
                    name = request['id']
                    records.setdefault(name, {'role': 'test', 'status': 'pending', 'request': request,
                                              'title': f"Independent check at {request['path']}:{request['start_line']}"})
                for batch in _judge_assignments(records):
                    ids = [f['id'] for f in batch]
                    records['J-' + ids[0]] = {'role': 'judge', 'status': 'pending', 'candidates': ids,
                                              'title': 'Independent judgment on ' + ', '.join(ids)}

        def source_checks():
            try:
                result = check_sources(state['sources'], cancel=cancel)
            except Exception as exc:
                # The source checks cannot certify textual or mathematical soundness if the checker is unavailable.
                result = {'status': 'unavailable', 'reason': str(exc)}
            if cancel.is_set():
                raise KeyboardInterrupt
            with self._mutex:
                graph['source_checks'] = result
                self._save()

        # The event queue wakes on a completed reader or a new lead request. Source checking occupies
        # its own CPU slot; transport admission and Python sandbox slots remain governed by production.
        submitted = set()
        with ThreadPoolExecutor(max_workers=slots + 1, thread_name_prefix='graph-review') as pool:
            active = {}
            if 'source_checks' not in graph:
                future = pool.submit(source_checks)
                active[future] = None
                future.add_done_callback(wake.put)
            try:
                while True:
                    if cancel.is_set():
                        raise KeyboardInterrupt
                    additions()
                    for name, row in list(records.items()):
                        if sum(value is not None for value in active.values()) >= slots:
                            break
                        if row['role'] == 'editor' or row['status'] == 'done' or name in submitted:
                            continue
                        submitted.add(name)
                        future = pool.submit(execute, name, transport())
                        active[future] = name
                        future.add_done_callback(wake.put)
                    if not active:
                        break
                    future = wake.get()
                    if future is not None:
                        del active[future]
                        future.result()
            except BaseException:
                cancel.set()
                for future in active:
                    future.cancel()
                raise

        incomplete = [row['title'] for row in records.values()
                      if row['role'] != 'editor' and row['status'] != 'done']
        if incomplete:
            reviewer.last_stop_reason = 'Assignments still require a completed handoff: ' + ', '.join(incomplete)
            return ''
        if records['REPORT']['status'] != 'done':
            execute('REPORT', transport())
        report = records['REPORT']
        if report['status'] != 'done':
            reviewer.last_stop_reason = report.get('stop_reason') or 'The final report was interrupted.'
        return self._disclose_undecided(report.get('answer', ''), plan)

    def _disclose_undecided(self, answer, plan):
        records = self.state['graph_review']['workers']
        decisions = records['REPORT'].get('decisions', {})
        pending = [f for row in records.values() for f in row.get('findings', ()) if f['id'] not in decisions]
        unjudged = {i for row in records.values() for i in row.get('missing_judgments', ())}
        if pending and answer.strip():
            notes = [f"- {f['path']}, lines {f['start_line']}–{f['end_line']}: {f['claim']} "
                     f"Basis proposed by the reader: {f['evidence']}" for f in pending]
            answer += '\n\n**Observations with no recorded final decision**\n\n' + '\n'.join(notes)
        if unjudged and answer.strip():
            findings = [f for row in records.values() for f in row.get('findings', ()) if f['id'] in unjudged]
            notes = [f"- {f['path']}, lines {f['start_line']}–{f['end_line']}: {f['claim']}" for f in findings]
            answer += '\n\n**Observations with no recorded independent judgment**\n\n' + '\n'.join(notes)
        unfinished = dict.fromkeys(statement for row in records.values() for statement in row.get('citation_limits', ()))
        if unfinished and answer.strip():
            answer += '\n\n**Citation lookups left unfinished**\n\n' + '\n'.join(
                '- ' + _code_span(statement) for statement in unfinished)
        missing = {}
        for row in records.values():
            for uid in row.get('owned_units', ()):
                if uid in row.get('assessments', {}):
                    continue
                unit = plan['units'][uid]
                section = unit.get('section')
                source_ids = unit.get('proof_locations') or {unit['source']: ()}
                paths = ', '.join(plan['sources'][sid]['path'] for sid in source_ids)
                location = paths + (f', §{section}' if section else '')
                missing.setdefault(location, []).append(unit['name'])
        if missing and answer.strip():
            answer += '\n\n**Proofs without a recorded assessment**\n\n' + '\n'.join(
                '- ' + location + ' — ' + ', '.join(names) for location, names in missing.items())
        return answer

    def _report(self):
        if self.state.get('status') == 'reviewed' or self.state.get('draft', '').strip():
            return super()._report()
        workers = self.state.get('graph_review', {}).get('workers', {})
        if not workers:
            return super()._report()
        # Interruption can precede the final lead call. Make the saved independent work usable in a
        # partial report and keep the full source, checkpoints, and decisions in the job for resume.
        parts = ['**Review incomplete.** ' + self.state.get('stop_reason', '')]
        for row in workers.values():
            parts.append(f"### {row['title']} ({row['status']})")
            if row.get('answer'):
                parts.append(row['answer'])
            elif row.get('findings'):
                parts.extend(f"{f['path']}:{f['start_line']} – {f['claim']} {f['evidence']}"
                             for f in row['findings'])
        _atomic_write(self.directory / 'report.md', _bytes('\n\n'.join(parts)))
