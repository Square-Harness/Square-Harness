# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Saved manuscript reviews written by Explore, with versioned prompts and tool environments."""
import copy
import json
import time
from pathlib import Path

from .agent import Agent
from .ledger import _atomic_write, _bytes, _read
from .refcheck import nested_checker
from .referee import MeteredClient, copies_manuscript
from .research import ResearchBudget, ResearchRunner
from .review import EXPLORE_PIPELINE, LEGACY_EXPLORE_PIPELINE, PaperReviewRunner, ReviewBase
from .review_challenge import Challenge
from .review_evidence import ReviewEvidence, bibliographic_query
from .review_position import _words
from .source_tasks import excludes_external_sources
from .tools import Workspace
from .worker_errors import WorkerInputError


class ManuscriptWorkspace(Workspace):
    """Explore's normal file tools, reading the immutable attachments by their original names."""
    immutable_excerpts = True

    def __init__(self, workspace, sources, numerics, mark_read):
        super().__init__(workspace.root, literature=workspace.literature, read_types=(), numerics=numerics)
        self.cancelled = workspace.cancelled
        self.documents = {self.root / src['path']: src for src in sources}
        self.aliases = {}
        for doc in sources:
            if doc.get('source_archive'):
                self.aliases.setdefault(doc['source_archive'], self.root / doc['path'])
        self.pinned = set(self.documents)
        self.selected_files_only = True
        self.mark_read = mark_read
        self.source_words = ' ' + ' '.join(' '.join(_words(s['content'])) for s in sources) + ' '

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
        text, last_complete = self._file_excerpt(path, start_line, end_line, start_column)
        first_complete = start_line if start_column == 1 else start_line + 1
        if first_complete <= last_complete:
            self.mark_read(self.documents[self.path(path)]['id'], [(first_complete, last_complete)])
        return text

    def schemas(self):
        return [s for s in super().schemas() if s['function']['name'] not in ('write_file', 'replace_text')]

    def execute(self, name, arguments, *, on_result=None):
        if name in ('search_papers', 'search_web', 'open_url', 'open_paper', 'find_quotes', 'check_reference',
                    'lookup_identifier'):
            public = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
            if copies_manuscript(public, self.source_words):
                return 'Tool error: public lookups must use bibliographic details or a query in your own words.'
        return super().execute(name, arguments, on_result=on_result)


class LegacyExploreReviewRunner(PaperReviewRunner):
    PIPELINE = LEGACY_EXPLORE_PIPELINE
    workspace_class = ManuscriptWorkspace
    sandbox = None

    def _extra_state(self):
        state = ReviewBase._extra_state(self)
        state.update(level='high', concurrency=1, experiments=[], tool_rounds=self.agent.max_rounds,
                     thinking=self.agent.think, temperature=self.agent.temperature, top_p=self.agent.top_p,
                     personal_context=getattr(self.agent, 'personal_context', ''),
                     project_context=getattr(self.agent, 'project_context', ''))
        reference = getattr(self, '_review_reference', None)
        if reference:
            state['review_reference'] = {k: v for k, v in reference.items() if k != 'source'}
        return state

    def resume(self, job_id, **allowances):
        # ResearchRunner holds the job lock across execution and preserves the original allowance and provider.
        return ResearchRunner.resume(self, job_id, **allowances)

    def _prepare_images(self):
        # An image attached alongside a manuscript is already available natively to Explore. For an
        # image-only paper retain the existing text snapshot for line references and future follow-ups.
        if not self.state['sources']:
            super()._prepare_images()

    def _step(self, phase):
        if phase == 'plan':
            if not self.state['sources']:
                raise WorkerInputError('Pin the manuscript to review.', code='needs_source')
            self._assemble_archives()
            if len(self.state['sources']) > 1:
                self._select_pinned_source()
            self.state['phase'] = 'review'
        elif phase == 'review':
            self._review()
        else:
            raise ValueError('Unknown manuscript review phase: ' + phase)

    def _review(self):
        state = self.state
        base = self.agent
        numerics = base.workspace.numerics
        if numerics is None and self.sandbox is not None:
            numerics = self.sandbox.automatic_session(base.workspace.root, cancel=getattr(self, 'cancel', None), ctx=base.ctx)
        if numerics is not None:
            previous = numerics.on_record
            def record(run):
                with self._mutex:
                    state['experiments'].append({'id': run['id'], 'status': run['status'], 'purpose': run['purpose']})
                    self._save()
                if previous:
                    previous(run)
            numerics.on_record = record
            numerics.runs = len(state['experiments'])
        workspace = self.workspace_class(base.workspace, state['sources'], numerics, self._mark)
        client = MeteredClient(self, 'review', 'reviewer')
        reviewer = Agent(client, workspace, base.model, base.ctx, base.predict, state['thinking'], 'explore',
                         state['tool_rounds'], seed=base.seed, temperature=state['temperature'], top_p=state['top_p'])
        reviewer.turn_image_refs = state.get('images', [])
        reviewer.personal_context = state.get('personal_context', '')
        reviewer.project_context = state.get('project_context', '')
        reviewer.document_output_cap = getattr(base, 'document_output_cap', None)
        if workspace.literature and workspace.literature.online:
            if excludes_external_sources(state['goal']):
                workspace.literature.online = False
            else:
                reference_agent = copy.copy(reviewer)
                reference_agent.client = MeteredClient(self, 'reference', 'reference')
                workspace.checker = nested_checker(reference_agent, self.emit, share_source_budget=True)

        query = state['goal']
        if state.get('target'):
            query += '\n\nRequested focus: ' + state['target']
        reference = state.get('review_reference') or {}
        if reference.get('followup'):
            query += '\n\nEarlier report (reference material for this question):\n' + reference['report']
        query += '\n\nFiles provided by the user (read them with read_file): ' + ', '.join(s['path'] for s in state['sources'])
        checkpoint_path = self.directory / 'artifacts' / 'review-tool-round.json'
        checkpoint = json.loads(_read(checkpoint_path)) if checkpoint_path.exists() else None

        def after_tools(data):
            _atomic_write(checkpoint_path, _bytes(json.dumps(data, ensure_ascii=False)))
            state['rounds_started'] = data['round'] + 1
            self._save()

        chunks, partial, last_write = [], [], time.monotonic()

        def display(kind, value):
            nonlocal last_write
            if kind == 'start':
                chunks.clear()
                partial.clear()
            elif kind == 'draft':
                partial.append(value)
                if time.monotonic() - last_write >= 2:
                    state['draft'] = ''.join(partial)
                    self._save()
                    last_write = time.monotonic()
                return
            elif kind == 'text':
                chunks.append(value)
                if time.monotonic() - last_write >= 2:
                    state['draft'] = ''.join(chunks)
                    self._save()
                    last_write = time.monotonic()
            self.emit(kind, value)

        self.emit('notice', 'Reviewing the pinned manuscript.')
        try:
            answer = self._run_reviewer(reviewer, query, display, checkpoint, after_tools)
        finally:
            if chunks or partial:
                state['draft'] = ''.join(chunks or partial)
            if numerics is not None:
                numerics.on_record = previous
        if answer.strip():
            state['draft'] = answer
        state['review_stop'] = reviewer.last_stop_reason
        finish = reviewer.last_stats.get('done_reason')
        if finish and finish != 'stop' and not state['review_stop']:
            state['review_stop'] = f'The provider ended the review with finish reason {finish}.'
        state['phase'] = 'done'

    def _run_reviewer(self, reviewer, query, display, checkpoint, after_tools):
        return reviewer.run(query, display, recover_empty_answer=True, tool_checkpoint=checkpoint,
                            after_tools=after_tools, background_tools=False)

    def _finish(self):
        s = self.state
        if s.get('draft', '').strip() and not s.get('review_stop'):
            s.update(status='reviewed', stop_reason='')
            # Install the report before the final state checkpoint advertises a completed job.
            self._report()
        else:
            # Resume continues from the last completed batch of tools and its saved tool results.
            s.update(status='partial', phase='review',
                     stop_reason=s.get('review_stop') or 'The reviewer did not return a written conclusion.')

    def _progress(self):
        s = self.state
        return {'phase': s['phase'], 'completed': int(s['status'] == 'reviewed'), 'total': 1,
                'unit': 'manuscript review', 'scope': s['goal'],
                'partial': s['status'] in ('partial', 'error', 'budget_exhausted', 'paused'),
                'resumable': s['status'] in ('running', 'partial', 'budget_exhausted', 'paused')}

    def _report(self):
        s = self.state
        report = s['draft']
        if s['status'] != 'reviewed':
            report = ('**Review incomplete.** ' + s.get('stop_reason', '') + '\n\n' + report).strip()
        _atomic_write(self.directory / 'report.md', _bytes(report))

    def _result(self):
        # Preserve all the reviewer's Markdown, including any user-requested headings or horizontal rules.
        return ResearchRunner._result(self)


REVIEW_PROMPT = """You are the author's pre-submission referee. Review the whole selected manuscript unless
the author specifies a narrower scope. Check each proof against the printed statement and its exact hypotheses.
First identify any fatal flaw and the results it affects. Distinguish an admissible counterexample to a theorem
from a failure of its proof. If the conclusion remains open, say so. Record repairable missing justifications
even when you can supply the argument yourself. Give a correction only if you can justify it independently.
Check formulas, notation, cross-references, typographical errors and the bibliography. For a citation used in
a proof, separate existence and bibliographic identity from support for this precise theorem and its hypotheses.
Identify unavailable sources as unverified. Honor the author's requested focus, language and source restrictions.
When a computation or reference check can run while you examine another passage, submit it in the background
and continue reading. Await it when the next inference depends on it. Mark ancillary checks optional for the report.
Use the private index to track proofs, other passages and references. Reading lines establishes access; record
your actual assessment with assess_review_items. Group related items when they have the same specific basis.
Use record_review_finding for concrete located findings that should survive subsequent tool calls. Validate any
independent challenger evidence before using it. Open with the mathematical consequence most relevant to the
author, give the locations, reasons and justified actions, and disclose material unresolved or unchecked scope.
The author knows the paper. Write one useful report in your own words."""

FOLLOWUP_PROMPT = """Answer the author's follow-up on the pinned manuscript and earlier report. Respect its
scope, distinguish a false statement from a gap, give the evidence for any correction, and identify uncertainty."""


class ReviewWorkspace(ManuscriptWorkspace):
    evidence = None

    def schemas(self):
        return super().schemas() + (self.evidence.schemas() if self.evidence else [])

    def execute(self, name, arguments, *, on_result=None):
        if self.evidence and name in self.evidence.tool_names:
            return self.evidence.execute(name, arguments)
        if name in ('search_papers', 'search_web', 'open_url', 'open_paper', 'find_quotes', 'check_reference',
                    'lookup_identifier'):
            allowed, arguments = bibliographic_query(name, arguments, self.source_words, self.documents.values())
            if not allowed:
                return 'Tool error: public lookups must use bibliographic details or a query in your own words.'
        return super().execute(name, arguments, on_result=on_result)


class ExploreReviewRunner(LegacyExploreReviewRunner):
    PIPELINE = EXPLORE_PIPELINE
    workspace_class = ReviewWorkspace

    def _extra_state(self):
        state = super()._extra_state()
        state['concurrency'] = min(2, self.concurrency)
        state['challenge'] = {'status': 'new'}
        return state

    def _admit_call(self, role, cap, input_tokens):
        if role == 'challenge':
            # Re-check under the metering lock, including reservations made by the lead between planning
            # and admission. Keep room for a full native lead call and its next source context.
            if self._tokens_left(cap) < self.agent.predict or self._input_left(input_tokens) < input_tokens:
                raise ResearchBudget('Remaining review allowance reserved for the main referee.')

    def _run_reviewer(self, reviewer, query, display, checkpoint, after_tools):
        state = self.state
        artifacts = self.directory / 'artifacts'
        def read_ranges():
            with self._mutex:
                return copy.deepcopy(state['manuscript_ranges'])
        primary, *support = state['sources']
        indexed = [primary, *(s for s in support if Path(s['path']).suffix.lower() in {'.bib', '.bbl'})]
        evidence = ReviewEvidence(indexed, artifacts, read_ranges, support=support)
        query += '\nPrimary manuscript: ' + primary['path']
        if support:
            query += '\nSupporting material (consult where relevant): ' + ', '.join(s['path'] for s in support)
        reviewer.workspace.evidence = evidence
        challenge = Challenge(self)
        followup = bool((state.get('review_reference') or {}).get('followup'))
        instruction = FOLLOWUP_PROMPT if followup else REVIEW_PROMPT
        def challenge_workspace(cancel, record):
            numerics = None
            if self.sandbox is not None and reviewer.workspace.numerics is not None:
                numerics = self.sandbox.automatic_session(reviewer.workspace.root, cancel=cancel, ctx=reviewer.ctx)
                if numerics is not None:
                    def saved(experiment):
                        with self._mutex:
                            record['experiments'].append(experiment['id'])
                            self._save()
                    numerics.on_record = saved
            # No shared pointer to the lead's notebook, literature, or list of lines the lead has inspected.
            selected = Workspace(reviewer.workspace.root, read_types=(), numerics=numerics)
            scope = ManuscriptWorkspace(selected, state['sources'], numerics, lambda *args: None)
            scope.cancelled = cancel.is_set
            scope.result_chars = reviewer.workspace.result_chars
            return scope

        def at_boundary(final):
            return {'snapshot': evidence.summary(), 'message': challenge.handoff(final)}

        try:
            challenge.start(challenge_workspace)
            answer = reviewer.run(query, display, instruction=instruction, recover_empty_answer=True,
                                  tool_checkpoint=checkpoint, after_tools=after_tools, background_tools=True,
                                  evidence_hook=at_boundary, task_journal=artifacts / 'review-background.json')
            if (answer.strip() and not followup and not state.get('target') and not reviewer.last_stop_reason
                    and reviewer.last_stats.get('done_reason') in (None, 'stop')):
                note = evidence.coverage_note()
                if note:
                    state['coverage_disclosure'] = note
                    answer = answer.rstrip() + '\n\n' + note
            return answer
        finally:
            challenge.close()
