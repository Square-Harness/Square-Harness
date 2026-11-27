# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Independently cancellable connections for concurrent mathematical work on the same provider."""
import copy
from functools import partial
from http.client import HTTPConnection, HTTPSConnection
import socket
import threading
from urllib.request import HTTPHandler, HTTPSHandler, OpenerDirector

from .assistant_budget import BudgetClient


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
        watcher = threading.Thread(target=watch, daemon=True, name='review-peer-headers')
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
