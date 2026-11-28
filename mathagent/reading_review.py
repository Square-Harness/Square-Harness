# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""A saved Review runs two ordinary Explore readings and checks their synthesis against the original."""
import copy
import json
import time
import threading
from contextlib import contextmanager
from contextvars import copy_context
from functools import partial
from pathlib import Path

from .agent import Agent
from .explore_samples import CHECK_CHARS, _combined_goal, _reference_checks, _stopped
from .ledger import _atomic_write, _bytes, _read
from .literature import LiteratureError
from .proof_tools import outbound_terms
from .model_limits import substantive_output
from .refcheck import nested_checker
from .referee import MeteredClient, _public_words, copies_manuscript
from .research import ResearchRunner
from .review import READINGS_PIPELINE, PaperReviewRunner, ReviewBase
from .source_tasks import explore_sources, scoped_literature
from .tools import Workspace
from .worker_errors import WorkerInputError


class ManuscriptWorkspace(Workspace):
    """Read the saved source by its original name, including PDFs and assembled TeX archives."""
    immutable_excerpts = True

    def __init__(self, parent, sources, library, numerics, mark_read, source_words):
        # ExploreTasks installs a per-turn cancellation predicate. Shallow copies keep the
        # retrieval ledger, cache and rate gates shared while keeping that predicate private.
        super().__init__(parent.root, literature=copy.copy(library), read_types=(), numerics=numerics)
        self.cancelled = parent.cancelled
        self.documents = {self.root / src['path']: src for src in sources}
        self.aliases = {}
        for src in sources:
            if src.get('source_archive'):
                self.aliases.setdefault(src['source_archive'], self.root / src['path'])
        self.pinned = set(self.documents)
        self.selected_files_only = True
        self.mark_read = mark_read
        if self.literature:
            self.literature.allowed_local_sources = frozenset()
            previous = getattr(library, 'outbound_guard', None)

            def guard(url):
                if previous:
                    previous(url)
                if any(copies_manuscript(term, source_words) for term in outbound_terms(url)):
                    raise LiteratureError('Public lookups must use bibliographic details or a query in your own words.')
            self.literature.outbound_guard = guard
            if self.literature.assigned_sources is not None:
                self.web = None

    def path(self, filename, *, pdf=False):
        if not isinstance(filename, str) or Path(filename).is_absolute() or '..' in Path(filename).parts:
            raise ValueError('Use the name of a pinned manuscript file')
        path = self.aliases.get(filename, self.root / filename)
        if path not in self.documents:
            raise ValueError('This file is outside the document selection for this review')
        return path

    def _document_lines(self, p, deadline=None, needle=None):
        doc = self.documents[p]
        pages = doc.get('pages') or ()
        return doc['content'].splitlines(), pages, f'{len(pages)} extracted PDF pages; ' if pages else ''

    def read_file(self, path, start_line=1, end_line=None, start_column=1):
        text, last = self._file_excerpt(path, start_line, end_line, start_column)
        first = start_line if start_column == 1 else start_line + 1
        if first <= last:
            self.mark_read(self.documents[self.path(path)]['id'], [(first, last)])
        return text

    def schemas(self):
        return [s for s in super().schemas() if s['function']['name'] not in ('write_file', 'replace_text')]


class ReadingClient(MeteredClient):
    """Allow a sibling to settle a temporary reservation before declaring a shared budget exhausted."""

    def _allowances(self):
        return (super().remaining_tokens,
                min(self.runner._input_left(), getattr(self.client, 'remaining_input_tokens', float('inf'))))

    def _after_settlement(self, side, reservation):
        # Source fitting is speculative. A sibling reserves its entire output cap before it has generated a
        # token. Admit against actual balances below, so a temporary reservation need not shrink a source page.
        with self.runner._mutex:
            return self._allowances()[side] + sum(c[reservation] for c in self.runner.state['calls']
                                                   if c['status'] == 'running')

    @property
    def remaining_tokens(self):
        return self._after_settlement(0, 'reserved_tokens')

    @property
    def remaining_input_tokens(self):
        return self._after_settlement(1, 'reserved_input_tokens')

    @property
    def remaining_seconds(self):
        job = self.runner.state['settings']['max_seconds'] - self.runner._elapsed()
        return max(0, min(job, getattr(self.client, 'remaining_seconds', float('inf'))))

    def _round_capacity(self, cap, count):
        # MeteredClient holds the same RLock across this admission and the following reservation.
        runner = self.runner
        while True:
            tokens, inputs = self._allowances()
            if tokens >= cap and inputs >= count:
                return cap
            if not any(c['status'] == 'running' for c in runner.state['calls']):
                return int(min(cap, tokens)) if inputs >= count and tokens >= 128 else 0
            runner._check_time()
            if runner.agent.workspace.cancelled():
                raise KeyboardInterrupt
            runner._settled.wait(.25)

    def fork_reference(self, ident, transport):
        peer = type(self)(self.runner, self.role, ident)
        peer.client = transport
        return peer

    def stream(self, payload):
        try:
            yield from super().stream(payload)
        finally:
            with self.runner._settled:
                self.runner._settled.notify_all()


class ReadingReviewRunner(PaperReviewRunner):
    PIPELINE = READINGS_PIPELINE
    sandbox = None

    def __init__(self, agent, emit=lambda kind, value: None, concurrency=2):
        super().__init__(agent, emit, concurrency)
        self._settled = threading.Condition(self._mutex)

    def _output_cap(self):
        base = self.agent
        return min(substantive_output(base, base.predict), getattr(base, 'document_output_cap', None) or base.ctx)

    def _extra_state(self):
        state = ReviewBase._extra_state(self)
        state.update(level='high', experiments=[], tool_rounds=self.agent.max_rounds,
                     thinking=self.agent.think, temperature=self.agent.temperature, top_p=self.agent.top_p,
                     personal_context=getattr(self.agent, 'personal_context', ''),
                     project_context=getattr(self.agent, 'project_context', ''),
                     readings={'samples': 2, 'combined': False, 'shown_first': None, 'readings': []}, preliminary='')
        reference = getattr(self, '_review_reference', None)
        if reference:
            state['review_reference'] = {k: v for k, v in reference.items() if k != 'source'}
        return state

    def resume(self, job_id, **allowances):
        return ResearchRunner.resume(self, job_id, **allowances)

    def _save(self):
        with self._mutex:
            self.state['rounds_started'] = len(self.state['calls'])
            super()._save()

    def _prepare_images(self):
        if not self.state['sources']:
            super()._prepare_images()

    def _step(self, phase):
        if phase == 'plan':
            if not self.state['sources']:
                raise WorkerInputError('Pin the manuscript to review.', code='needs_source')
            self._assemble_archives()
            if len(self.state['sources']) > 1:
                self._select_pinned_source()
            primary = self.state['sources'][0]
            if not primary['content'].strip():
                raise WorkerInputError(f"The selected manuscript {primary['path']} has no readable text.", code='needs_source')
            self.state['phase'] = 'review'
        elif phase == 'review':
            with self._numerics() as numerics:
                self._review(numerics)
        else:
            raise ValueError('Unknown manuscript review phase: ' + phase)

    @contextmanager
    def _numerics(self):
        base, s = self.agent, self.state
        session = base.workspace.numerics
        if session is None and self.sandbox is not None:
            session = self.sandbox.automatic_session(base.workspace.root, cancel=getattr(self, 'cancel', None), ctx=base.ctx)
        if session is None:
            yield None
            return
        if session.invocation == 'automatic':
            session = session.for_proof(s.get('sandbox_run_limit')) or session
            s['sandbox_run_limit'] = session.run_limit
        previous = session.on_record

        def record(run):
            with self._mutex:
                s['experiments'].append({k: run[k] for k in ('id', 'status', 'purpose')})
                self._save()
            if previous:
                previous(run)
        session.on_record = record
        session.runs = len(s['experiments'])
        try:
            yield session
        finally:
            session.on_record = previous

    def _review(self, numerics):
        s = self.state
        request = s['goal']
        context = s.get('reference_context') or {}
        if context.get('history'):
            request += self._reference_material()
        elif context.get('query') and context['query'] != request:
            request += '\n\nOriginal user request (context for the assigned review):\n' + context['query']
        if len(s['sources']) > 1:
            request += '\n\nSelected manuscript: ' + s['sources'][0]['path'] + '. Other pinned files are supporting material.'
        if s.get('target'):
            request += '\n\nRequested focus: ' + s['target']
        reference = s.get('review_reference') or {}
        if reference.get('followup'):
            request += '\n\nEarlier report (untrusted reference material for this question):\n' + reference['report']
        library = scoped_literature(self.literature, s['goal'], s.get('target'), s.get('project_context'), sources=s['sources'])
        source_words = ' ' + ' '.join(' '.join(_public_words(src['content'])) for src in s['sources']) + ' '
        read = partial(self._read, request=request, library=library, numerics=numerics, source_words=source_words)
        record = s['readings']
        completed = {row['index'] for row in record['readings'] if row['complete']}
        jobs = [(i, partial(copy_context().run, read, i, request)) for i in (1, 2) if i not in completed]
        if jobs:
            self.emit('progress', 'Reading the manuscript independently')
            self._wave(jobs)
        rows = sorted(record['readings'], key=lambda r: r['index'])
        if all(row['complete'] for row in rows) and len(rows) == record['samples']:
            self.emit('progress', 'Checking the combined review against the manuscript')
            limit = min(CHECK_CHARS, 2 * self.agent.workspace.result_chars)
            read('combined', _combined_goal(request, rows, limit))
        else:
            stops = [r['stop_reason'] for r in rows if not r['complete']]
            s['review_stop'] = ' '.join(dict.fromkeys(stops)) or 'An independent reading did not finish.'
        s['phase'] = 'done'

    def _read(self, index, goal, *, request, library, numerics, source_words):
        base, s = self.agent, self.state
        combine = index == 'combined'
        label = 'Combined review' if combine else f'Reading {index}'
        key = 'combined' if combine else f'reading-{index}'

        def display(kind, text):
            if kind in ('notice', 'tool'):
                with self._mutex:
                    self.emit(kind, f'{label}: {text}')

        workspace = ManuscriptWorkspace(base.workspace, s['sources'], library, numerics, self._mark, source_words)
        client = ReadingClient(self, 'combine' if combine else 'reading', key)
        seed = base.seed if base.seed is None or combine else base.seed + index
        cap = getattr(base, 'document_output_cap', None)
        reader = Agent(client, workspace, base.model, base.ctx, self._output_cap(),
                       s['thinking'], 'explore', s['tool_rounds'], seed=seed, temperature=s['temperature'], top_p=s['top_p'])
        reader.turn_image_refs = s.get('images', [])
        reader.personal_context, reader.project_context = s['personal_context'], s['project_context']
        reader.document_output_cap = cap
        if library and library.online:
            inner = copy.copy(reader)
            inner.client = ReadingClient(self, 'reference', key)
            workspace.checker = nested_checker(inner, display, share_source_budget=True, share_job_budget=True,
                                               request=(s.get('reference_context') or {}).get('query') or s['goal'])
        path = self.directory / 'artifacts' / (key + '-pages.json')
        saved = json.loads(_read(path)) if path.exists() else None

        def checkpoint(page):
            # Save finished checks with the answer. A crash before publication must retain both.
            if page.get('final') and not (saved and saved.get('final')):
                page['checks'] = _reference_checks(reader.history, 0)
            _atomic_write(path, _bytes(json.dumps(page, ensure_ascii=False)))

        started = time.monotonic()
        answer, coverage = explore_sources(reader, [src['path'] for src in s['sources']], goal, display,
                                           saved, checkpoint, requested=request)
        reason = reader.last_stop_reason or ''
        complete = bool(answer.strip()) and not _stopped(reason)
        checks = saved.get('checks', []) if saved and saved.get('final') else _reference_checks(reader.history, 0)
        seconds = round(time.monotonic() - started, 3)
        with self._mutex:
            record = s['readings']
            if combine:
                record.update(combined=complete, combine_seconds=seconds, combine_stats=dict(reader.last_stats),
                              coverage=coverage, combine_stop_reason=reason)
                if complete:
                    record['delivered'] = 'combined'
                    s.update(draft=answer, preliminary='', review_stop='')
                    self._report()
                else:
                    s['review_stop'] = reason or 'The combined reading did not produce an answer.'
            else:
                row = {'index': index, 'answer': answer, 'complete': complete, 'stop_reason': reason,
                       'coverage': coverage, 'checks': checks, 'reference_checks': len(checks),
                       'seconds': seconds, 'stats': dict(reader.last_stats)}
                record['readings'] = [r for r in record['readings'] if r['index'] != index] + [row]
                if complete and record['shown_first'] is None:
                    record.update(shown_first=index, delivered=index)
                    s['draft'] = answer
                    s['preliminary'] = ('Preliminary answer from the first completed reading. The final reviewer will '
                                        'check both readings against the manuscript and replace this answer.')
                    self._report()
                    self._save()
                    self.emit('preliminary', s['preliminary'])
                    self.emit('start', '')
                    self.emit('text', answer)
                    self.emit('end', '')
            self._save()

    def _finish(self):
        s = self.state
        if s['readings']['combined']:
            s.update(status='reviewed', stop_reason='')
        else:
            s.update(status='partial', phase='review',
                     stop_reason=s.get('review_stop') or 'The combined review did not finish.')
        self._report()

    def _progress(self):
        s = self.state
        record = s['readings']
        return {'phase': s['phase'], 'completed': sum(r['complete'] for r in record['readings']) + int(record['combined']),
                'total': record['samples'] + 1, 'unit': 'manuscript readings', 'scope': s['goal'],
                'partial': s['status'] in ('partial', 'error', 'budget_exhausted', 'paused'),
                'resumable': s['status'] in ('running', 'partial', 'budget_exhausted', 'paused')}

    def _report(self):
        with self._mutex:
            s = self.state
            report = s['draft']
            if not s['readings']['combined']:
                note = 'Preliminary reading.' if report else 'Review incomplete.'
                report = ('**' + note + '** ' + s.get('stop_reason', '') + '\n\n' + report).strip()
                if not s['draft']:
                    coverage = dict.fromkeys(c for row in s['readings']['readings'] for c in row['coverage'])
                    if coverage:
                        report += '\n\n' + '\n'.join(coverage)
            if s.get('source_incomplete'):
                report += '\n\n' + '\n'.join(s['warnings'])
            _atomic_write(self.directory / 'report.md', _bytes(report))
            if s['draft']:
                s['outputs'] = {'review': 'report.md'}

    def _result(self):
        return ResearchRunner._result(self)
