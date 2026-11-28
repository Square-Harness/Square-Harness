# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Turn-scoped background checks and delivery of their evidence to the working agent."""
import copy
import hashlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import ContextVar, copy_context
from functools import partial
from http.client import HTTPConnection, HTTPSConnection
from urllib.request import HTTPHandler, HTTPSHandler, OpenerDirector

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


class _PeerConnection:
    def __init__(self, *args, cancel, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel = cancel

    def getresponse(self):
        # The regular Interruptible transport takes over once the response headers have arrived.
        # A server can accept a connection and queue a generation before sending any headers.
        sock = self.sock
        done = threading.Event()
        def watch():
            while not done.wait(0.05):
                if self.cancel.is_set():
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
        watcher = threading.Thread(target=watch, daemon=True, name='explore-check-headers')
        watcher.start()
        try:
            return super().getresponse()
        finally:
            done.set()
            watcher.join()


class _PeerHTTP(_PeerConnection, HTTPConnection):
    pass


class _PeerHTTPS(_PeerConnection, HTTPSConnection):
    pass


class _PeerOpen:
    def do_open(self, connection, request, **options):
        peer = _PeerHTTPS if connection is HTTPSConnection else _PeerHTTP
        return super().do_open(partial(peer, cancel=self.cancel), request, **options)


class _PeerHTTPHandler(_PeerOpen, HTTPHandler):
    pass


class _PeerHTTPSHandler(_PeerOpen, HTTPSHandler):
    pass


def _peer_opener(opener, cancel):
    # Copy configured handlers, including redirect/proxy and TLS policies. Swap only stdlib connections.
    peer = OpenerDirector()
    peer.addheaders = list(opener.addheaders)
    for handler in opener.handlers:
        clone = copy.copy(handler)
        variant = {HTTPHandler: _PeerHTTPHandler, HTTPSHandler: _PeerHTTPSHandler}.get(type(handler))
        if variant:
            clone.__class__ = variant
            clone.cancel = cancel
        peer.add_handler(clone)
    return peer


def fork_cancellable(client, cancel):
    """A private stream transport with independent cancellation but the same provider and quota accounting.

    Known CLI transports acquire the GUI's existing interruptible stream implementation. Unknown/custom
    adapters can supply fork_for_review(cancel) to preserve their own metering and cancellation semantics.
    """
    from .assistant_budget import BudgetClient
    from .agent import Ollama
    from .api_profiles import MLX
    from .backends import LlamaCpp, OpenAICompatible
    from .deepinfra import DeepInfra
    from .openrouter import OpenRouter
    from .gui.hub import (Interruptible, InterruptibleDeepInfra, InterruptibleLlamaCpp, InterruptibleMLX,
                          InterruptibleOllama, InterruptibleOpenAI, InterruptibleOpenRouter)

    if isinstance(client, BudgetClient):
        transport = fork_cancellable(client.client, cancel)
        if transport is None:
            return None
        sibling = client.fork(transport, allowance=client.allowance, usage=client.usage,
                              input_allowance=client.input_allowance, slots=client.slots)
        sibling.deadline = client.deadline
        return sibling
    factory = getattr(client, 'fork_for_review', None)
    if factory:
        return factory(cancel)
    if isinstance(client, Interruptible):
        sibling = copy.copy(client)
    else:
        adapters = {Ollama: InterruptibleOllama, DeepInfra: InterruptibleDeepInfra, OpenRouter: InterruptibleOpenRouter,
                    OpenAICompatible: InterruptibleOpenAI, LlamaCpp: InterruptibleLlamaCpp, MLX: InterruptibleMLX}
        adapter = adapters.get(type(client))
        if adapter is None:
            return None
        # Carry the configured transport, including its provider allowlist, privacy/usage settings and opener.
        # Reconstructing it from the environment could silently change the provider for a saved job.
        sibling = adapter.__new__(adapter)
        vars(sibling).update(vars(client))
        sibling.owner = sibling.pool = sibling.on_model_wait = None
    sibling.cancel = cancel
    sibling._active_responses = {}
    sibling._response_lock = threading.Lock()
    sibling._admitted = threading.local()
    sibling.opener = _peer_opener(client.opener, cancel)
    return sibling


class ExploreTasks:
    def __init__(self, workspace):
        self.workspace = workspace
        self.cancel = TurnCancellation(getattr(workspace, 'cancelled', lambda: False))
        self.pool = ThreadPoolExecutor(thread_name_prefix='explore-check')
        self.records = {}
        self.futures = {}
        self.cancellations = {}
        self.lock = threading.RLock()
        self.library_lock = threading.RLock()

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

    @staticmethod
    def schemas(original):
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
        if available:
            offered.append(schema('await_background_check', 'Wait for one submitted check when further work depends '
                                  'on it. Completed checks arrive automatically; do not poll.',
                                  {'check_id': {'type': 'string'}}, ['check_id']))
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
                return self.await_result(args.get('check_id'))
            if name in ELIGIBLE:
                background = args.pop('background', False)
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
                self.records[ident] = {'tool': name, 'required_for_report': required}
                self.cancellations[ident] = TurnCancellation(self.cancel.is_set)
                self.futures[ident] = self.pool.submit(copy_context().run, self._run, ident, name, args)
            elif required and not self.records[ident].get('required_for_report', True):
                self.records[ident]['required_for_report'] = True
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

    def await_result(self, ident):
        if not isinstance(ident, str) or ident not in self.records:
            return 'Tool error: unknown background check ID'
        future = self.futures[ident]
        if not future.cancelled():
            future.result()
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
            return False
        if final:
            # Let optional work finish while waiting for any required results.
            for ident in self.futures:
                if self.records[ident].get('required_for_report', True):
                    self.futures[ident].result()
            with self.lock:
                for ident, future in self.futures.items():
                    row = self.records[ident]
                    if 'result' not in row and not row.get('required_for_report', True):
                        self.cancellations[ident].stopped.set()
                        row['result'] = 'Optional check was unfinished at finalization; it supplies no evidence.'
                        future.cancel()
        changed = False
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
                if data.get('status') == 'submitted' and 'result' in self.records[ident]:
                    msg['content'] = self._evidence(ident)
                    changed = True
        return changed
