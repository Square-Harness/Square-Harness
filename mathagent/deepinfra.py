# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Opt-in DeepInfra transport for a pinned DeepSeek checkpoint.

Credentials stay outside model payloads and are sent only to the configured,
approved origin. Token preflight is deliberately labelled an estimate: the
documented tokenizer does not expose every chat-template and tool option.
"""
import copy
import ipaddress
import json
import os
import stat
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from functools import wraps
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .agent import AgentError, ModelNotDispatched
from .backends import MeasuredStreamCancellation, OpenAICompatible, ProviderStreamError, ToolCallError
from .model_limits import MODEL_OUTPUT_TOKENS


BASE_URL = 'https://api.deepinfra.com/v1/openai'
MODEL = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
LEGACY_MODEL = 'deepseek-ai/DeepSeek-V4-Flash-0731'
SUPPORTED_MODELS = (MODEL, LEGACY_MODEL)
CONTEXT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = MODEL_OUTPUT_TOKENS


class DeepInfraGenerationError(AgentError):
    """Provider failure that must reach the workflow before it dispatches more work."""

    def __init__(self, message, *, category='unknown', stream_progress=None, stats=None,
                 confirmed_terminal=False, output_started=False, retry_spent=False):
        super().__init__(message)
        self.category = category
        self.retryable = category in {'interrupted', 'service_unavailable', 'server_error'}
        self.stream_progress = dict(stream_progress or {})
        self.stats = dict(stats or {})
        self.confirmed_terminal = confirmed_terminal
        self.output_started = output_started
        self.retry_spent = retry_spent

    @property
    def retry_before_output(self):
        return (self.confirmed_terminal and not self.retry_spent and self.category in {'server_error', 'service_unavailable'}
                and not self.output_started and not any(self.stream_progress.values())
                and not self.stats.get('eval_count'))


@dataclass
class _GenerationWindow:
    deadline: float
    retry_spent: bool


_generation_window = ContextVar('deepinfra_generation_window', default=None)


def generation_deadline(timeout):
    """Do not give a replacement attempt a new per-call time allowance."""
    end = time.monotonic() + timeout
    window = _generation_window.get()
    return end if window is None else min(end, window.deadline)


@contextmanager
def generation_window(client, *, replacement=False, deadline=None):
    """Share one replacement across HTTP rejections, terminal SSE errors and nested callers."""
    if getattr(client, 'profile', None) != 'deepinfra' or _generation_window.get() is not None:
        yield
        return
    end = generation_deadline(client.timeout)
    previous = _generation_window.set(_GenerationWindow(end if deadline is None else min(end, deadline), replacement))
    try:
        yield
    finally:
        _generation_window.reset(previous)


@contextmanager
def generation_attempts(client, emit, *, before_retry=None):
    """One owner retries outside all its admission, reservation and journal steps."""
    if getattr(client, 'profile', None) != 'deepinfra' or _generation_window.get() is not None:
        yield (nullcontext(),)
        return
    retry = None

    @contextmanager
    def first():
        nonlocal retry
        try:
            yield
        except DeepInfraGenerationError as exc:
            cancel = getattr(client, 'cancel', None)
            if cancel is not None and cancel.is_set():
                raise KeyboardInterrupt from None
            window = _generation_window.get()
            if (not exc.retry_before_output or window.retry_spent or time.monotonic() >= window.deadline
                    or (before_retry and not before_retry(exc))):
                raise
            retry = exc
            window.retry_spent = True

    def attempts():
        yield first()
        if retry:
            emit('notice', 'The provider ended the request before producing output. Retrying once within the remaining budget.')
            cancel = getattr(client, 'cancel', None)
            if cancel is not None and cancel.is_set():
                raise KeyboardInterrupt
            if time.monotonic() >= _generation_window.get().deadline:
                raise retry
            yield nullcontext()

    with generation_window(client):
        yield attempts()


def retry_generation_call(method):
    """Retry the entire worker call, including its durable accounting."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with generation_attempts(self.agent.client, self.emit) as attempts:
            for attempt in attempts:
                with attempt:
                    return method(self, *args, **kwargs)
    return wrapped


def generation_stream(client, payload, emit):
    """Plain Agent turns have no separate worker journal; re-enter the budgeted client."""
    with generation_attempts(client, emit) as attempts:
        for attempt in attempts:
            with attempt:
                yield from client.stream(payload)
                return


class DeepInfraRateLimit(DeepInfraGenerationError):
    """A definitive HTTP 429 response, before the transport opened a success stream."""

    def __init__(self, hint, *, completion_rejected):
        super().__init__('DeepInfra HTTP 429: ' + hint, category='rate_limit')
        self.completion_rejected = completion_rejected


class TesterAllowanceInsufficient(DeepInfraGenerationError):
    """The local tester ledger declined admission before a completion request was sent."""

    def __init__(self):
        super().__init__('Tester allowance is insufficient for this call; wait for active calls or contact the administrator',
                         category='quota')


class _RejectedRetryCancelled(KeyboardInterrupt):
    """A cancellation between an explicit 429 and any subsequent request."""

    def __init__(self, completion_rejected):
        super().__init__()
        self.completion_rejected = completion_rejected


def rejected_before_inference(exc):
    return isinstance(exc, (TesterAllowanceInsufficient, ModelNotDispatched)) or (
        isinstance(exc, (DeepInfraRateLimit, _RejectedRetryCancelled)) and exc.completion_rejected)


def _provider_signals(value):
    fields = []
    if isinstance(value, dict):
        for item in (value, value.get('error'), value.get('detail')):
            if isinstance(item, dict):
                fields.extend(item.get(k) for k in ('code', 'type', 'status', 'status_code', 'message', 'detail'))
            else:
                fields.append(item)
    else:
        fields.append(value)
    return {' '.join(str(field).lower().replace('_', ' ').replace('-', ' ').split()).strip('.')
            for field in fields if isinstance(field, (str, int))}


def _credit_limit(signals):
    return any(word in s for s in signals for word in ('credit', 'balance', 'spending', 'billing', 'payment',
                                                         'insufficient quota', 'quota exhausted'))


def _generation_failure(value):
    """Map known provider signals to public diagnostics. Raw fields may reflect private inputs."""
    signals = _provider_signals(value)
    if _credit_limit(signals) or '402' in signals:
        return 'quota', 'DeepInfra reported a credit or spending limit during generation.'
    if signals & {'401', '403', 'authentication error', 'permission denied', 'unauthorized', 'forbidden'}:
        return 'auth', 'DeepInfra reported an authorization error during generation.'
    if signals & {'429', 'rate limit error', 'rate limit exceeded', 'rate limited', 'too many requests'}:
        return 'rate_limit', 'DeepInfra reported a rate limit during generation.'
    if signals & {'400', '404', '422', 'invalid request', 'invalid request error', 'context length exceeded'}:
        return 'invalid_request', 'DeepInfra reported an invalid model request during generation.'
    if signals & {'502', '503', '504', 'model busy', 'model overloaded', 'server overloaded', 'service unavailable', 'gateway timeout'}:
        return 'service_unavailable', 'DeepInfra reported a temporary service failure during generation.'
    if signals & {'500', 'internal server error', 'server error', 'internal error', 'inference error', 'generation error'}:
        return 'server_error', 'DeepInfra reported an internal model error during generation.'
    return 'unknown', 'DeepInfra reported an unspecified error during generation.'


def _rate_limit(exc, deadline):
    # Only match recognized signals. Never retain or quote a provider body,
    # which may reflect submitted material or request headers.
    # The HTTP status alone confirms rejection. Spend at most one second reading
    # the optional diagnostic, even when the normal generation timeout is hours.
    read_end = min(deadline, time.monotonic() + 1)
    body = bytearray()
    complete = False
    response = exc.fp
    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    try:
        while len(body) <= 4096:
            remaining = read_end - time.monotonic()
            if remaining <= 0:
                break
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(4097 - len(body))
            if not chunk:
                complete = True
                break
            body.extend(chunk)
            if response.isclosed():
                complete = len(body) <= 4096
                break
    except (OSError, HTTPException, ValueError):
        pass
    value = None
    if complete:
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            value = body.decode('ascii', errors='replace')
    signals = _provider_signals(value)
    if _credit_limit(signals):
        return 'Check the provider credit balance and spending limit.', False
    if signals & {'rate limited', 'rate limit exceeded', 'rate limit error', 'too many requests',
                  'concurrency limit exceeded', 'too many concurrent requests'}:
        return 'The provider is temporarily limiting requests.', True
    if signals & {'model busy', 'model overloaded', 'capacity exceeded', 'server overloaded'}:
        return 'The model is temporarily at capacity.', True
    return 'The provider rate, capacity or spending limit was reached.', False


def _retry_delay(header):
    """Seconds to wait, or None when the provider requests a long or invalid wait."""
    if header is None:
        return 1.0
    header = header.strip()
    if len(header) > 128:
        return None
    try:
        if header.isascii() and header.isdigit():
            seconds = int(header)
        else:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                return None
            seconds = max(0, date.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return seconds if seconds <= 5 else None


def read_api_key():
    """Read a private file without following its final symlink, or an env key."""
    path = os.environ.get('DEEPINFRA_API_KEY_FILE')
    if path:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise AgentError('DeepInfra key file must be an owned, private regular file (mode 600)')
                raw = source.read(16_385)
            if len(raw) > 16_384:
                raise AgentError('DeepInfra key file is too large')
            key = raw.decode('ascii').strip()
        except (OSError, UnicodeError):
            raise AgentError('Cannot read the private DeepInfra key file') from None
    else:
        key = os.environ.get('DEEPINFRA_API_KEY', '').strip()
    if not key or len(key) > 16_384 or any(ord(char) <= 32 or ord(char) >= 127 for char in key):
        raise AgentError('Set DEEPINFRA_API_KEY_FILE to a private API-key file, or DEEPINFRA_API_KEY')
    return key


class DeepInfra(OpenAICompatible):
    """Authenticated stateless Chat Completions; no redirects or proxy env."""
    profile = 'deepinfra'
    require_terminal_usage = True

    def __init__(self, host=BASE_URL, timeout=600):
        parsed = urlsplit(host.rstrip('/'))
        try:
            port = parsed.port
        except ValueError:
            raise AgentError('Invalid DeepInfra endpoint port') from None
        production = parsed.scheme == 'https' and parsed.hostname == 'api.deepinfra.com' and port in {None, 443}
        loopback = False
        if os.environ.get('DEEPINFRA_ALLOW_LOOPBACK') == '1' and parsed.scheme in {'http', 'https'}:
            try:
                loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
            except ValueError:
                pass
        if (not (production or loopback) or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip('/') not in {'', '/v1/openai'}):
            raise AgentError('DeepInfra requires https://api.deepinfra.com/v1/openai')
        origin = parsed.scheme + '://' + parsed.netloc
        super().__init__(origin, timeout)
        if self.thinking_budget_policy:
            raise AgentError('The DeepInfra profile does not support vLLM thinking-budget fractions')
        self.host = self.base_url = origin + '/v1/openai'
        self.tokenize_url = origin + '/v1/tokenize'
        self._origin = (parsed.scheme, parsed.hostname, port or (443 if parsed.scheme == 'https' else 80))
        self.reasoning_effort = os.environ.get('SH_DEEPINFRA_REASONING_EFFORT', 'high')
        if self.reasoning_effort not in {'none', 'low', 'high', 'max'}:
            raise AgentError('DeepSeek reasoning effort must be none, low, high or max')
        self.service_tier = os.environ.get('SH_DEEPINFRA_SERVICE_TIER', 'standard')
        if self.service_tier not in {'standard', 'priority'}:
            raise AgentError('DeepInfra service tier must be standard or priority')
        self._api_key = read_api_key()
        self.observed_context_tokens = CONTEXT_TOKENS
        self.input_token_count_method = 'DeepInfra preflight estimate'

    def _redact(self, value):
        return str(value).replace(self._api_key, '[redacted]')

    def _request_url(self, url, data=None, *, optional=False):
        parsed = urlsplit(url)
        origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
        paths = {'/v1/openai/models', '/v1/openai/chat/completions', '/v1/tokenize'}
        if (origin != self._origin or parsed.path not in paths or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise AgentError('Refused to send DeepInfra credentials outside approved API endpoints')
        headers = {'Content-Type': 'application/json',
                   'Accept': 'text/event-stream' if parsed.path.endswith('/chat/completions') else 'application/json'}
        # Catalog reads are public. Inference-only scoped JWTs are rejected by
        # this endpoint if supplied, although they work for generation.
        if parsed.path != '/v1/openai/models':
            headers['Authorization'] = 'Bearer ' + self._api_key
        request = Request(url, data=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
                          headers=headers)
        hints = {400: 'Check the model request and supported parameters.',
                 401: 'Check the API credential and its expiry.',
                 402: 'Check available credit and the credential spending limit.',
                 403: 'Check the credential scopes and allowed models.',
                 404: 'The requested model or endpoint is unavailable.',
                 405: 'This endpoint does not support the requested operation.',
                 422: 'Check the model request and supported parameters.'}
        completion = parsed.path.endswith('/chat/completions')
        deadline = generation_deadline(self.timeout)
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise ModelNotDispatched('The DeepInfra request time allowance elapsed before dispatch.')
        cancel = getattr(self, 'cancel', None)
        for attempt in (0, 1):
            if attempt and cancel is not None and cancel.is_set():
                raise _RejectedRetryCancelled(completion)
            try:
                return self.opener.open(request, timeout=timeout)
            except HTTPError as exc:
                with exc:
                    if optional and exc.code in {404, 405}:
                        return None
                    if exc.code != 429:
                        hint = hints.get(exc.code, 'The provider request failed; check its service status.')
                        raise AgentError(f'DeepInfra HTTP {exc.code}: {hint}') from None
                    hint, transient = _rate_limit(exc, deadline)
                    delay = _retry_delay(exc.headers.get('Retry-After') if exc.headers else None)
                rejection = DeepInfraRateLimit(hint, completion_rejected=completion)
                window = _generation_window.get() if completion else None
                if attempt or (window and window.retry_spent) or not transient or delay is None or time.monotonic() + delay >= deadline:
                    raise rejection from None
                if window:
                    window.retry_spent = True
                try:
                    if cancel is None:
                        time.sleep(delay)
                    elif cancel.wait(delay):
                        raise KeyboardInterrupt
                except KeyboardInterrupt:
                    raise _RejectedRetryCancelled(completion) from None
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise rejection from None
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                raise AgentError('Cannot reach DeepInfra: ' + self._redact(exc)) from None

    def _payload(self, payload, *, include_images=True):
        if payload.get('model') not in SUPPORTED_MODELS:
            raise AgentError('The DeepInfra test profile requires model ' + MODEL +
                             ' (or ' + LEGACY_MODEL + ' for saved work)')
        refs = getattr(self, 'image_refs', [])
        if refs:
            from . import images
            if not images.supported(self.profile, payload.get('model')):
                raise AgentError('Native images require DS4.1 Flash 1017; the legacy V4 model cannot receive them')
            if '_sh_bounded_title' in payload:
                raise AgentError('A bounded conversation-title call cannot include images')
        data = super()._payload(payload)
        if self.service_tier == 'priority':
            data['service_tier'] = 'priority'
        data.pop('chat_template_kwargs', None)
        data.pop('thinking_token_budget', None)
        cap = data.pop('max_completion_tokens', None)
        if type(cap) is not int or not 0 < cap <= MAX_OUTPUT_TOKENS:
            raise AgentError(f'DeepInfra needs an explicit output cap between 1 and {MAX_OUTPUT_TOKENS}')
        data['max_tokens'] = cap
        effort = ('none' if not payload.get('think', True) else
                  payload.get('reasoning_effort') or self.reasoning_effort)
        if effort not in {'none', 'low', 'high', 'max'}:
            raise AgentError('DeepSeek reasoning effort must be none, low, high or max')
        data['reasoning_effort'] = effort
        for message in data['messages']:
            if 'reasoning' in message:
                message['reasoning_content'] = message.pop('reasoning')
        data.pop('structured_outputs', None)
        schema = payload.get('format')
        if isinstance(schema, dict):
            data['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'square_harness', 'strict': False, 'schema': copy.deepcopy(schema)}}
        if '_sh_bounded_title' in payload:
            self._quota_input_cap(payload, data)
        if refs and include_images:
            data['messages'] = images.materialize(self.image_root, refs, data['messages'], originals=payload['messages'])
        return data

    @staticmethod
    def _bytes(value):
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def _quota_input_cap(self, payload, data):
        """Only validated nonthinking title/routing shapes use a smaller reserve."""
        if '_sh_bounded_routing' in payload:
            from .proof_routing import POLICY, SCHEMA, OUTPUT_TOKENS
            messages, options = payload.get('messages'), payload.get('options')
            valid = (payload['_sh_bounded_routing'] is True and payload.get('think') is False
                     and payload.get('stream') is True and not getattr(self, 'image_refs', None)
                     and payload.get('format') == SCHEMA
                     and not set(payload) - {'model', 'messages', 'options', 'think', 'stream', 'format', '_sh_bounded_routing'}
                     and isinstance(options, dict) and not set(options) - {'num_ctx', 'num_predict', 'temperature'}
                     and type(data['max_tokens']) is int and 0 < data['max_tokens'] <= OUTPUT_TOKENS
                     and isinstance(messages, list) and len(messages) == 2)
            if valid:
                valid = all(isinstance(message, dict) and {'role', 'content'} <= set(message)
                            and not set(message) - {'role', 'content', 'images'}
                            and message['role'] == role and isinstance(message['content'], str)
                            and message.get('images', []) == []
                            for message, role in zip(messages, ('system', 'user')))
            if valid and messages[0]['content'] == POLICY:
                bound = (self._bytes(data['messages']) + 1024 + 128 * len(messages)
                         + 2 * self._bytes({'response_format': data['response_format']}))
                if bound <= 65_536:
                    return bound
            # An invalid marker never reduces an ordinary call's reservation.
            return CONTEXT_TOKENS
        if '_sh_bounded_title' not in payload:
            return CONTEXT_TOKENS
        messages, options = payload.get('messages'), payload.get('options')
        valid = (payload['_sh_bounded_title'] is True and payload.get('think') is False
                 and payload.get('stream') is True
                 and not set(payload) - {'model', 'messages', 'options', 'think', 'stream', '_sh_bounded_title'}
                 and isinstance(options, dict) and not set(options) - {'num_ctx', 'num_predict', 'temperature'}
                 and type(data['max_tokens']) is int and 0 < data['max_tokens'] <= 64
                 and isinstance(messages, list) and len(messages) == 2)
        if valid:
            valid = all(isinstance(message, dict) and set(message) == {'role', 'content'}
                        and message['role'] == role and isinstance(message['content'], str)
                        for message, role in zip(messages, ('system', 'user')))
        if not valid:
            raise AgentError('Invalid bounded conversation-title payload')
        # UTF-8 bytes upper-bound text tokens; preserve the normal framing allowance.
        bound = self._bytes(data['messages']) + 1024 + 128 * len(messages) + 2 * self._bytes({})
        if bound > 4096:
            raise AgentError('Bounded conversation-title input exceeds 4096 tokens')
        return bound

    def count_input_tokens(self, payload):
        """Include full material, plus conservative template/schema allowance.

        The native count covers messages only. The additional byte allowance
        accommodates framing and server-injected tool/schema text, without
        claiming an exact reconstruction of DeepInfra's rendering.
        """
        # Count text and image-processing allowance, never the base64 transport encoding.
        data = self._payload(payload, include_images=False)
        if '_sh_bounded_routing' in payload:
            bound = self._quota_input_cap(payload, data)
            if bound < CONTEXT_TOKENS:
                self.input_token_count_method = 'Bounded routing UTF-8 byte and framing/schema estimate'
                return bound
        if '_sh_bounded_title' in payload:
            self.input_token_count_method = 'Bounded title UTF-8 byte and framing estimate'
            return self._quota_input_cap(payload, data)
        framing = 1024 + 128 * len(data['messages'])
        extras = {key: data[key] for key in ('tools', 'response_format') if key in data}
        extra_allowance = 2 * self._bytes(extras) + 1024 * len(data.get('tools', []))
        refs = getattr(self, 'image_refs', [])
        image_allowance, image_method = 0, ''
        if refs:
            from .images import VISION_TOKEN_ESTIMATE, message_images, prompt_overhead
            image_allowance = sum(len(attached) * VISION_TOKEN_ESTIMATE + prompt_overhead(attached)
                                  for _, attached in message_images(refs, payload['messages']))
            image_method = ' + conservative 4096-token/image vision allowance (not base64 text tokens)'
        if self._tokenize_supported is not False:
            request = {'model': data['model'], 'messages': data['messages'], 'return_token_strs': False}
            cached = self._cached_token_count(request)
            if cached is not None:
                self.input_token_count_method = 'DeepInfra message token count plus framing/tool/schema estimate' + image_method
                return cached + framing + extra_allowance + image_allowance
            response = self._request_url(self.tokenize_url, request, optional=True)
            if response is not None:
                try:
                    with response:
                        result = json.load(response)
                    count, context = result['count'], result['max_model_len']
                    if type(count) is not int or count < 0 or type(context) is not int or context <= 0:
                        raise ValueError('Invalid tokenizer count or context')
                except (ValueError, KeyError, TypeError, OSError, HTTPException):
                    raise AgentError('Malformed tokenizer response from DeepInfra') from None
                self._tokenize_supported = True
                self.observed_context_tokens = min(context, CONTEXT_TOKENS)
                self._remember_token_count(request, count)
                self.input_token_count_method = 'DeepInfra message token count plus framing/tool/schema estimate' + image_method
                return count + framing + extra_allowance + image_allowance
            self._tokenize_supported = False
        self.input_token_count_method = 'DeepInfra UTF-8 byte and framing estimate (tokenizer unavailable)' + image_method
        return self._bytes(data['messages']) + framing + extra_allowance + image_allowance

    def _stream_deadline(self):
        return generation_deadline(self.timeout)

    def _stream_failure(self, exc, category, progress, final_usage, output_started):
        if str(exc) in {'Proof preparation exceeded its short text allowance', 'Proof preparation exceeded its short tool-argument allowance'}:
            return exc
        if isinstance(exc, ProviderStreamError):
            category, message = _generation_failure(exc.detail)
        elif category == 'interrupted':
            message = 'The DeepInfra connection ended before the completion stream finished.'
        elif str(exc) == 'Model stream ended without finish reason and token usage':
            message = str(exc)
        else:
            message = 'DeepInfra returned an invalid completion stream.'
        window = _generation_window.get()
        return DeepInfraGenerationError(message, category=category, stream_progress=progress, stats=final_usage,
                                        confirmed_terminal=isinstance(exc, ProviderStreamError), output_started=output_started,
                                        retry_spent=bool(window and window.retry_spent))

    def stream(self, payload):
        with generation_window(self):
            yield from self._generation(payload)

    def _generation(self, payload):
        quota = None
        if os.environ.get('SH_DEEPINFRA_QUOTA_FILE'):
            from contextlib import closing
            from .hosted_quota import HostedQuota
            quota = HostedQuota.from_environment(self.service_tier)
            data = self._payload(payload)
            reservation = quota.reserve(self._quota_input_cap(payload, data), data['max_tokens'])
        try:
            if quota is None:
                yield from super().stream(payload)
            else:
                with closing(super().stream(payload)) as stream:
                    for event in stream:
                        if event.get('done'):
                            try:
                                quota.settle(reservation, event.get('prompt_eval_count'), event.get('eval_count'))
                            except AgentError:
                                # The provider already completed. Keep the full
                                # reservation and deliver the billed answer and
                                # its usage instead of losing finished work.
                                event = {**event, 'accounting_warning':
                                    'Tester allowance settlement failed; the full reservation was retained.'}
                        yield event
        except (AgentError, MeasuredStreamCancellation, _RejectedRetryCancelled) as exc:
            if rejected_before_inference(exc):
                if quota is not None:
                    try:
                        quota.settle(reservation, 0, 0)
                    except AgentError:
                        # Storage failure retains the hosted reservation; the rejection
                        # still reaches the worker so it can checkpoint its own budget.
                        if isinstance(exc, DeepInfraRateLimit):
                            exc.args = (str(exc) + ' The tester allowance could not be updated.',)
                raise
            if quota is not None and isinstance(exc, (ToolCallError, DeepInfraGenerationError, MeasuredStreamCancellation)) and exc.stats:
                try:
                    quota.settle(reservation, exc.stats['prompt_eval_count'], exc.stats['eval_count'])
                except AgentError:
                    exc.args = (str(exc) + ' Tester allowance settlement failed; the full reservation was retained.',)
            redacted = self._redact(exc)
            if redacted != str(exc):
                raise AgentError(redacted) from None
            raise
