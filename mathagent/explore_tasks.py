# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Turn-scoped background checks and delivery of their evidence to the working agent."""
import copy
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import ContextVar, copy_context

from .ledger import _atomic_write, _bytes, _read
from .tools import schema


RETRIEVAL = frozenset(('search_papers', 'search_web', 'find_quotes', 'lookup_identifier',
                       'open_paper', 'open_url', 'read_paper', 'search_paper', 'read_page',
                       'search_page', 'read_references'))
ELIGIBLE = RETRIEVAL | {'execute_python', 'check_reference'}
# Set only inside the worker which was explicitly asked to run this check.
active_check = ContextVar('explore_background_check', default=None)


class TurnCancellation:
    def __init__(self, cancelled):
        self.parent = cancelled
        self.stopped = threading.Event()

    def is_set(self):
        return self.stopped.is_set() or self.parent()

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while not self.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            self.stopped.wait(min(.1, remaining))
        return True


class ExploreTasks:
    def __init__(self, workspace, journal=None):
        self.workspace = workspace
        self.journal = journal
        self.cancel = TurnCancellation(getattr(workspace, 'cancelled', lambda: False))
        self.pool = ThreadPoolExecutor(thread_name_prefix='explore-check')
        self.records = {}
        self.futures = {}
        self.cancellations = {}
        self.lock = threading.RLock()
        self.library_lock = threading.RLock()
        if journal is not None and journal.exists():
            self.records = json.loads(_read(journal))
            for row in self.records.values():
                if 'result' not in row:
                    row['result'] = 'Interrupted before the result was saved. No evidence is available; this check was not repeated.'
            self._save()

    def __enter__(self):
        library = getattr(self.workspace, 'literature', None)
        if library is not None:
            self.original_cancelled = library.cancelled
            def cancelled():
                slot = active_check.get()
                if slot and slot[0] is self and self.cancellations[slot[1]].is_set():
                    return True
                return self.cancel.is_set() or self.original_cancelled()
            library.cancelled = cancelled
        return self

    def __exit__(self, *exc):
        # A failed or paused turn must release every inference and sandbox worker before its job lock.
        self.cancel.stopped.set()
        try:
            self.pool.shutdown(wait=True, cancel_futures=True)
        finally:
            library = getattr(self.workspace, 'literature', None)
            if library is not None:
                library.cancelled = self.original_cancelled

    def _save(self):
        if self.journal is not None:
            _atomic_write(self.journal, _bytes(json.dumps(self.records, ensure_ascii=False)))

    @staticmethod
    def schemas(original, *, saved_checks=False, review_citations=False):
        offered = copy.deepcopy(original)
        available = False
        for spec in offered:
            fn = spec['function']
            if fn['name'] in ELIGIBLE:
                available = True
                fn['parameters']['properties']['background'] = {
                    'type': 'boolean', 'description': 'Set true when independent work remains. Returns a check ID '
                    'immediately. Its evidence is delivered at a model boundary. Omit to use the result immediately.'}
                fn['parameters']['properties']['required_for_report'] = {
                    'type': 'boolean', 'description': 'For a background check, default true: join before writing the '
                    'final answer. Set false only for optional exploration that can be dropped without compromising '
                    'the requested scope or your verdict. An explicitly requested verification is required.'}
                if review_citations and fn['name'] == 'check_reference':
                    fn['parameters']['properties']['background']['description'] = (
                        'Defaults to true in manuscript Review. Submission returns a check ID immediately. '
                        'Continue working on independent proofs. Set false only for a foreground-only transport.')
                    fn['parameters']['properties']['required_for_report']['description'] = (
                        'Set true if the author requested this verification or if the verdict depends on it. '
                        'Set false for an ancillary attribution that can be disclosed as unverified.')
                    fn['parameters']['required'].append('required_for_report')
        if available or saved_checks:
            offered.append(schema('await_background_check', 'Wait for one submitted check when further work depends '
                                  'on it. Completed checks arrive automatically; do not poll.',
                                  {'check_id': {'type': 'string'}}, ['check_id']))
            if review_citations:
                offered[-1]['function']['parameters']['properties']['needed_for'] = {
                    'type': 'string', 'description': 'For a pending citation, name the unresolved inference or '
                    'requested verification that needs this result now. If omitted, the check keeps running '
                    'while you examine other passages. Completed results are returned immediately.'}
        return offered

    def _access(self, name, arguments):
        # PDF extraction and text search can change a shared LiteratureTools deadline.
        if getattr(self.workspace, 'immutable_excerpts', False) and name in ('read_file', 'search_text'):
            return nullcontext()
        uses_library = (name in RETRIEVAL or name == 'search_text' and
                        ('.pdf' in getattr(self.workspace, 'readable', ()) or
                         any(str(p).lower().endswith('.pdf') for p in getattr(self.workspace, 'pinned', ()))) or
                        name == 'read_file' and str(arguments.get('path', '')).lower().endswith('.pdf'))
        return self.library_lock if uses_library else nullcontext()

    def execute(self, name, raw):
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(args, dict):
                raise ValueError('Tool arguments must be an object')
            args = dict(args)
            if name == 'await_background_check':
                return self.await_result(args.get('check_id'), args.get('needed_for'))
            if name in ELIGIBLE:
                citation = name == 'check_reference' and getattr(self.workspace, 'defer_citations', False)
                background = args.pop('background', citation)
                required = args.pop('required_for_report', True)
                if not isinstance(background, bool) or not isinstance(required, bool):
                    raise ValueError('background and required_for_report must be booleans')
                if background:
                    allowed = {s['function']['name'] for s in self.workspace.schemas()}
                    if name not in allowed:
                        raise ValueError(f'Unknown or disabled tool: {name}')
                    return self.submit(name, args, required)
            with self._access(name, args):
                return self.workspace.execute(name, args)
        except (TypeError, ValueError) as exc:
            return f'Tool error: {exc}'

    def submit(self, name, args, required):
        key = json.dumps([name, args], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        ident = 'check-' + hashlib.sha256(key.encode()).hexdigest()[:24]
        with self.lock:
            if ident not in self.records:
                if self.cancel.is_set():
                    raise KeyboardInterrupt
                self.records[ident] = {'tool': name, 'required_for_report': required, 'submitted_at': time.time(), 'waits': []}
                if name == 'check_reference' and getattr(self.workspace, 'defer_citations', False):
                    self.records[ident]['citation_statement'] = str(args.get('statement') or ident)
                self.cancellations[ident] = TurnCancellation(self.cancel.is_set)
                self._save()  # An interrupted dispatch can never be silently resubmitted on resume.
                self.futures[ident] = self.pool.submit(copy_context().run, self._run, ident, name, args)
            elif required and not self.records[ident].get('required_for_report', True):
                self.records[ident]['required_for_report'] = True
                self._save()
        return json.dumps({'background_check_id': ident, 'status': 'submitted'})

    def _run(self, ident, name, args):
        ctx = active_check.set((self, ident))
        cancel = self.cancellations[ident]
        try:
            if cancel.is_set():
                raise KeyboardInterrupt
            with self._access(name, args):
                if cancel.is_set():
                    raise KeyboardInterrupt
                with self.lock:
                    self.records[ident]['started_at'] = time.time()
                    self._save()
                result = self.workspace.execute(name, args)
        except KeyboardInterrupt:
            result = 'Check interrupted; no completed evidence was returned.'
        except Exception as exc:
            result = f'Check failed ({type(exc).__name__}): {exc}'
        finally:
            active_check.reset(ctx)
        with self.lock:
            row = self.records[ident]
            row.setdefault('result', result)  # A cancelled optional result is never promoted back into evidence.
            row['finished_at'] = time.time()
            self._save()

    def _wait(self, ident, reason):
        future = self.futures.get(ident)
        if future is None or future.cancelled():
            return
        if future.done():
            future.result()  # Propagate a failed journal write.
            return
        started = time.time()
        try:
            future.result()
        finally:
            with self.lock:
                self.records[ident]['waits'].append({'reason': reason, 'start': started, 'end': time.time()})
                self._save()

    def await_result(self, ident, needed_for=None):
        if not isinstance(ident, str) or ident not in self.records:
            return 'Tool error: unknown background check ID'
        with self.lock:
            row = self.records[ident]
            if ('citation_statement' in row and 'result' not in row
                    and not (isinstance(needed_for, str) and needed_for.strip())):
                return json.dumps({'check_id': ident, 'status': 'running',
                                   'next': 'Continue an independent part of the review. This result arrives '
                                   'automatically. To wait now, give the specific dependency in needed_for.'})
        self._wait(ident, 'dependency')
        return self._evidence(ident)

    def _evidence(self, ident):
        row = self.records[ident]
        value = row['result']
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
        data = dict(value) if isinstance(value, dict) else {'result': value}
        data.update(background_check_id=ident, background_tool=row['tool'])
        return json.dumps(data, ensure_ascii=False)

    def pending_receipts(self, messages):
        for message in messages:
            if message['role'] == 'tool' and 'background_check_id' in message.get('content', ''):
                try:
                    data = json.loads(message['content'])
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get('status') == 'submitted':
                    return True
        return False

    def deliver(self, messages, final):
        """Replace submitted receipts by the corresponding real tool output, retaining native tool roles/IDs."""
        if not self.records:
            return False, []
        if final:
            # Let optional work finish while waiting for any required results.
            for ident in self.futures:
                if self.records[ident].get('required_for_report', True):
                    self._wait(ident, 'final verdict')
            with self.lock:
                for ident, future in self.futures.items():
                    row = self.records[ident]
                    if 'result' not in row and not row.get('required_for_report', True):
                        self.cancellations[ident].stopped.set()
                        row.update(cancelled_at=time.time(), result='Optional check was unfinished at finalization; '
                                   'it supplies no evidence.')
                        if future.cancel():
                            row['finished_at'] = time.time()
                        self._save()
        changed, seen = False, set()
        with self.lock:
            for msg in messages:
                if msg['role'] != 'tool' or 'background_check_id' not in msg.get('content', ''):
                    continue
                try:
                    data = json.loads(msg['content'])
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                ident = data.get('background_check_id')
                if not isinstance(ident, str) or ident not in self.records:
                    continue
                seen.add(ident)
                if data.get('status') == 'submitted' and 'result' in self.records[ident]:
                    row = self.records[ident]
                    msg['content'] = self._evidence(ident)
                    # The report discloses unfinished ancillary citations by name. Cancellation alone
                    # supplies no new evidence for the referee to assess in a second generation.
                    if 'cancelled_at' not in row or 'citation_statement' not in row:
                        changed = True
            # The process might have stopped after dispatch and before checkpointing the model's tool batch.
            orphaned = [self._evidence(i) for i in self.records.keys() - seen if 'result' in self.records[i]]
        return changed, orphaned
