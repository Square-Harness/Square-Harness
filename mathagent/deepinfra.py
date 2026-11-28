# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Opt-in hosted transport for a pinned DeepSeek checkpoint: DeepInfra, and the base of other hosted profiles.

Credentials stay outside model payloads and are sent only to the configured,
approved origin. Token preflight is deliberately labelled an estimate: the
documented tokenizer does not expose every chat-template and tool option.

`DeepInfra` carries the robust hosted transport (retry windows, busy waits, silent-start detection, replays, the
tester ledger). Its provider-specific values are class attributes; another hosted provider of the same model is a
subclass that overrides them (mathagent.openrouter.OpenRouter). Which one runs is the --api-profile.
"""
import copy
import ipaddress
import json
import os
import random
import stat
import threading
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

from . import images
from .agent import AgentError, ModelNotDispatched
from .backends import (MeasuredStreamCancellation, OpenAICompatible, ProviderStreamError, SilentStreamStart,
                       StalledStream)
from .model_limits import HOSTED_DS4, MODEL_OUTPUT_TOKENS


BASE_URL = 'https://api.deepinfra.com/v1/openai'
MODEL = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
LEGACY_MODEL = 'deepseek-ai/DeepSeek-V4-Flash-0731'
SUPPORTED_MODELS = (MODEL, LEGACY_MODEL)
CONTEXT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = MODEL_OUTPUT_TOKENS
# Provider failures at scale (measured on DS4.1 Flash, November 2026): about 3% of accepted calls go silent,
# in bursts of a few minutes, and a fresh request usually starts at once; HTTP 429 and 503 refuse a request
# before any generation. A logical call is therefore sent up to RETRIES more times within its own deadline,
# for every key: at once after a silent start, after a growing pause after a refusal. A call whose output
# had started (a stream that stalled or broke mid-answer) is replayed at most REPLAYS times, and only by a
# caller that restarts the whole call (a worker); a conversation already showing text is never replayed.
RETRIES, REPLAYS = 4, 2
BACKOFF = (2, 5, 15, 30, 60)  # seconds before a new request after a refusal without Retry-After
LONGEST_RETRY_AFTER = 120  # a provider asking for a longer wait is not retried automatically
START_SECONDS = 30  # an accepted stream silent this long (plus prefill time) is closed and sent again
# A refusal made before any generation (HTTP 429 "Model busy", 500-504) costs nothing, and DeepInfra's overloads come in
# waves of a minute or more (measured 2026-11-26: about 37% of calls refused for hours, whatever the request size,
# reasoning or service tier). Such refusals do not use the RETRIES replacements: a call keeps sending again, every
# BUSY_STEP seconds at most, until BUSY_PATIENCE seconds have passed since its first refusal, and says so each time.
BUSY_PATIENCE = 300  # measured from the start of the call, so waits on other calls' cooldown count too
BUSY_STEP = 30  # longest pause between two sends (about 24-36 s with jitter) unless the provider asks for longer
MAX_REFUSALS = 60
NOTICE_EVERY = 45  # seconds between two "<provider> is busy" notices of one call (the first is always shown)
_cooldown = {'until': 0.0}
_cooldown_lock = threading.Lock()


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
                and not self.started)

    @property
    def started(self):
        """Whether the provider had produced output for this request (reasoning counts)."""
        return bool(self.output_started or any(self.stream_progress.values()) or self.stats.get('eval_count'))


@dataclass
class _GenerationWindow:
    """One logical call: its deadline and the replacements it has used (a litreview replacement window starts
    spent). retry_spent means no replacement is left."""
    deadline: float
    retry_spent: bool
    retries: int = 0
    replays: int = 0
    replacement: bool = False  # a caller that replaces the whole call itself (litreview): refusals are not retried here

    def spend(self, replay=False):
        self.retries += 1
        self.replays += bool(replay)
        self.retry_spent = self.retry_spent or self.retries >= RETRIES


_generation_window = ContextVar('deepinfra_generation_window', default=None)
# Who hears about refusals while a call waits (the owner's emit), and how long a call waits for a busy provider.
_refusal_notice = ContextVar('deepinfra_refusal_notice', default=None)
_busy_patience = ContextVar('deepinfra_busy_patience', default=None)


@contextmanager
def busy_patience(seconds):
    """Wait at most this long for a busy provider in the calls made inside (a step with a fallback, such as routing)."""
    token = _busy_patience.set(seconds)
    try:
        yield
    finally:
        _busy_patience.reset(token)


@contextmanager
def refusal_notices(emit):
    """Tell this emit about every refusal the calls made inside are waiting out (the first owner keeps it)."""
    if emit is None or _refusal_notice.get() is not None:
        yield
        return
    token = _refusal_notice.set(emit)
    try:
        yield
    finally:
        _refusal_notice.reset(token)


def generation_deadline(timeout):
    """Do not give a replacement attempt a new per-call time allowance."""
    end = time.monotonic() + timeout
    window = _generation_window.get()
    return end if window is None else min(end, window.deadline)


@contextmanager
def generation_window(client, *, replacement=False, deadline=None):
    """Share one replacement across HTTP rejections, terminal SSE errors and nested callers."""
    if getattr(client, 'profile', None) not in HOSTED_DS4 or _generation_window.get() is not None:
        yield
        return
    end = generation_deadline(client.timeout)
    previous = _generation_window.set(_GenerationWindow(end if deadline is None else min(end, deadline), replacement,
                                                         replacement=replacement))
    try:
        yield
    finally:
        _generation_window.reset(previous)


def replaceable(exc, window, replay):
    """Whether a failed request may be sent again: a transient provider failure, replacements left, and
    (for a request whose output had started) a caller that restarts the whole call."""
    if (window is None or window.retry_spent or isinstance(exc, DeepInfraRateLimit)
            or exc.category not in {'server_error', 'service_unavailable', 'interrupted', 'rate_limit'}):
        return False
    return not exc.started or (replay and window.replays < REPLAYS)


def _pause(client, seconds, deadline):
    """Wait before a replacement; False when the deadline would pass first. A cancellation interrupts."""
    seconds = max(0.0, seconds)
    if time.monotonic() + seconds >= deadline:
        return False
    cancel = getattr(client, 'cancel', None)
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise KeyboardInterrupt
    return True


@contextmanager
def generation_attempts(client, emit, *, before_retry=None, replay=True):
    """One owner retries outside all its admission, reservation and journal steps.

    replay=False: the caller already showed the partial output (a conversation), so only a request that
    produced nothing is sent again.
    """
    if getattr(client, 'profile', None) not in HOSTED_DS4 or _generation_window.get() is not None:
        yield (nullcontext(),)
        return
    failed = []

    @contextmanager
    def attempt():
        try:
            yield
        except DeepInfraGenerationError as exc:
            cancel = getattr(client, 'cancel', None)
            if cancel is not None and cancel.is_set():
                raise KeyboardInterrupt from None
            window = _generation_window.get()
            if (not replaceable(exc, window, replay) or time.monotonic() >= window.deadline
                    or (before_retry and not before_retry(exc))):
                raise
            window.spend(replay=exc.started)
            failed.append(exc)

    def attempts():
        while True:
            count = len(failed)
            yield attempt()
            if len(failed) == count:
                return
            exc, window = failed[-1], _generation_window.get()
            what = ('stopped in the middle of its answer' if exc.started else
                    'accepted the request but did not start' if isinstance(exc, DeepInfraSilentStart) else
                    'ended the request before producing output')
            emit('notice', f'The provider {what}; sending it again ({window.retries} of {RETRIES} retries).')
            # A silent or broken stream is usually served at once by a fresh request; a provider error
            # reported for the request itself gets a short pause.
            pause = 0 if isinstance(exc, DeepInfraSilentStart) else BACKOFF[min(window.retries, len(BACKOFF)) - 1]
            if not _pause(client, pause * random.uniform(.8, 1.2), window.deadline):
                raise exc

    with generation_window(client), refusal_notices(emit):
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
    """Plain Agent turns have no separate worker journal; re-enter the budgeted client. Their output is
    shown as it arrives, so only a request that produced nothing is sent again."""
    with generation_attempts(client, emit, replay=False) as attempts:
        for attempt in attempts:
            with attempt:
                yield from client.stream(payload)
                return


class DeepInfraSilentStart(DeepInfraGenerationError):
    """The provider accepted the request and sent no output before the caller's start limit. The caller
    closed the stream itself, so nothing was generated for it: it can be replaced like a terminal error."""

    def __init__(self, stream_progress=None, retry_spent=False, display='DeepInfra'):
        super().__init__(f'{display} accepted the request but did not start to answer.',
                         category='service_unavailable', stream_progress=stream_progress,
                         confirmed_terminal=True, retry_spent=retry_spent)


class DeepInfraRateLimit(DeepInfraGenerationError):
    """A definitive refusal (HTTP 429, or a provider's in-flight budget limit), before the transport opened a
    success stream. code=None leaves the HTTP status out of the message: a transient 402 must not read as a credit
    refusal (referee.provider_refused stops a review on 'HTTP 402')."""

    def __init__(self, hint, *, completion_rejected, transient=False, display='DeepInfra', code=429):
        super().__init__((f'{display} HTTP {code}: ' if code else f'{display}: ') + hint, category='rate_limit')
        self.completion_rejected = completion_rejected
        self.retryable = transient


class DeepInfraUnavailable(DeepInfraRateLimit):
    """A definitive HTTP 5xx response before any stream: the provider refused the request without generating."""

    def __init__(self, code, *, completion_rejected, display='DeepInfra'):
        DeepInfraGenerationError.__init__(self, f'{display} HTTP {code}: the provider is temporarily unavailable.',
                                          category='service_unavailable')
        self.completion_rejected = completion_rejected


class TesterAllowanceInsufficient(DeepInfraGenerationError):
    """The tester ledger refused a new call (the allowance is spent) before any request was sent."""

    def __init__(self):
        super().__init__('The tester allowance is used up; contact the administrator to continue',
                         category='quota')


class _RejectedRetryCancelled(KeyboardInterrupt):
    """A cancellation between an explicit 429 and any subsequent request."""

    def __init__(self, completion_rejected):
        super().__init__()
        self.completion_rejected = completion_rejected


def provider_busy(exc):
    """Whether a call failed because the provider refused or could not serve it for now (busy, unavailable, a
    silent start), as opposed to a request, credit or protocol problem."""
    return (isinstance(exc, DeepInfraGenerationError) and bool(getattr(exc, 'retryable', False))
            and exc.category in {'rate_limit', 'service_unavailable', 'server_error'} and not exc.started)


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


# Provider wording of a temporary refusal, matched inside its fields: DeepInfra answers an overloaded model with
# {"error": {"message": "Model busy, retry later", "type": "invalid_request_error", "code": "engine_overloaded"}}.
_BUSY = ('model busy', 'engine overloaded', 'model overloaded', 'server overloaded', 'capacity exceeded',
         'at capacity', 'retry later', 'try again later')


def _mentions(signals, phrases):
    return any(phrase in signal for signal in signals for phrase in phrases)


def _credit_limit(signals):
    return any(word in s for s in signals for word in ('credit', 'balance', 'spending', 'billing', 'payment',
                                                         'insufficient quota', 'quota exhausted'))


def _generation_failure(value, display='DeepInfra'):
    """Map known provider signals to public diagnostics. Raw fields may reflect private inputs."""
    signals = _provider_signals(value)
    if _credit_limit(signals) or '402' in signals:
        return 'quota', f'{display} reported a credit or spending limit during generation.'
    if signals & {'401', '403', 'authentication error', 'permission denied', 'unauthorized', 'forbidden'}:
        return 'auth', f'{display} reported an authorization error during generation.'
    if _mentions(signals, _BUSY):  # before 'invalid request': DeepInfra types engine_overloaded invalid_request_error
        return 'service_unavailable', f'{display} reported that the model is busy.'
    if signals & {'429', 'rate limit error', 'rate limit exceeded', 'rate limited', 'too many requests'}:
        return 'rate_limit', f'{display} reported a rate limit during generation.'
    if signals & {'400', '404', '422', 'invalid request', 'invalid request error', 'context length exceeded'}:
        return 'invalid_request', f'{display} reported an invalid model request during generation.'
    if signals & {'502', '503', '504', 'model busy', 'model overloaded', 'server overloaded', 'service unavailable', 'gateway timeout'}:
        return 'service_unavailable', f'{display} reported a temporary service failure during generation.'
    if signals & {'500', 'internal server error', 'server error', 'internal error', 'inference error', 'generation error'}:
        return 'server_error', f'{display} reported an internal model error during generation.'
    return 'unknown', f'{display} reported an unspecified error during generation.'


def _read_error_body(exc, deadline):
    """The decoded body of an HTTP refusal (JSON, or text), or None. Only matched against recognized signals:
    never retained or quoted, since it may reflect submitted material or request headers. The HTTP status alone
    confirms rejection; at most one second is spent reading this optional diagnostic."""
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
    return value


def _rate_limit(exc, deadline):
    signals = _provider_signals(_read_error_body(exc, deadline))
    if _credit_limit(signals):
        return 'Check the provider credit balance and spending limit.', False
    if signals & {'rate limited', 'rate limit exceeded', 'rate limit error', 'too many requests',
                  'concurrency limit exceeded', 'too many concurrent requests'}:
        return 'The provider is temporarily limiting requests.', True
    if _mentions(signals, _BUSY):
        return 'The model is temporarily at capacity.', True
    return 'The provider rate, capacity or spending limit was reached.', False


def _refusal_delay(delay, header, attempt):
    """The pause before sending a refused completion again: the provider's Retry-After when it asks for longer,
    never less than the backoff step (a Retry-After of 0 must not make calls send back to back), at most BUSY_STEP
    when the provider names no wait."""
    if delay is None:
        return None
    step = min(BACKOFF[min(attempt, len(BACKOFF) - 1)], BUSY_STEP) * random.uniform(.8, 1.2)
    return max(delay, step) if header is not None else min(delay, BUSY_STEP * random.uniform(.8, 1.2))


def _retry_delay(header, attempt=0):
    """Seconds to wait, or None when the provider requests a long or invalid wait. Without Retry-After the
    wait grows with each refusal (with jitter, so that parallel calls do not return together)."""
    if header is None:
        return BACKOFF[min(attempt, len(BACKOFF) - 1)] * random.uniform(.8, 1.2)
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
    return seconds if seconds <= LONGEST_RETRY_AFTER else None


def read_api_key(key_file_env='DEEPINFRA_API_KEY_FILE', key_env='DEEPINFRA_API_KEY', display='DeepInfra'):
    """Read a private file without following its final symlink, or an env key."""
    path = os.environ.get(key_file_env)
    if path:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise AgentError(f'{display} key file must be an owned, private regular file (mode 600)')
                raw = source.read(16_385)
            if len(raw) > 16_384:
                raise AgentError(f'{display} key file is too large')
            key = raw.decode('ascii').strip()
        except (OSError, UnicodeError):
            raise AgentError(f'Cannot read the private {display} key file') from None
    else:
        key = os.environ.get(key_env, '').strip()
    if not key or len(key) > 16_384 or any(ord(char) <= 32 or ord(char) >= 127 for char in key):
        raise AgentError(f'Set {key_file_env} to a private API-key file, or {key_env}')
    return key


HINTS = {400: 'Check the model request and supported parameters.',
         401: 'Check the API credential and its expiry.',
         402: 'Check available credit and the credential spending limit.',
         403: 'Check the credential scopes and allowed models.',
         404: 'The requested model or endpoint is unavailable.',
         405: 'This endpoint does not support the requested operation.',
         422: 'Check the model request and supported parameters.'}


class DeepInfra(OpenAICompatible):
    """Authenticated stateless Chat Completions; no redirects or proxy env.

    The class attributes below are DeepInfra's; a subclass for another hosted provider of the same model overrides
    them (and, where the provider words things differently, _refusal, _stream_error and _read_service_tier)."""
    profile = 'deepinfra'
    display = 'DeepInfra'  # every user-visible provider name
    origin_host = 'api.deepinfra.com'
    api_path = '/v1/openai'
    host_paths = ('', '/v1/openai')  # path forms accepted on --host
    routes = ('/v1/openai/models', '/v1/openai/chat/completions', '/v1/tokenize')  # credential-scoped allowlist
    public_routes = ('/v1/openai/models',)  # sent without credentials (scoped JWTs are rejected there)
    tokenize_path = '/v1/tokenize'  # None: the token count is always the byte estimate
    key_file_env, key_env, loopback_env = 'DEEPINFRA_API_KEY_FILE', 'DEEPINFRA_API_KEY', 'DEEPINFRA_ALLOW_LOOPBACK'
    model_ids = {MODEL: MODEL, LEGACY_MODEL: LEGACY_MODEL}  # model name in Square Harness -> model name on the wire
    tolerate_repeated_finish = False  # a provider that repeats an empty finish frame to carry usage (backends._stream)
    require_terminal_usage = True
    detect_repetition = True
    # DS4 streams about 150 tokens/s once started; three minutes with only keep-alives is a stalled
    # stream. It is ended; a worker call is then replayed (see REPLAYS), a conversation is not.
    stall_seconds = 180

    @classmethod
    def default_base_url(cls):
        return 'https://' + cls.origin_host + cls.api_path

    def __init__(self, host=None, timeout=600):
        host = host or self.default_base_url()
        parsed = urlsplit(host.rstrip('/'))
        try:
            port = parsed.port
        except ValueError:
            raise AgentError(f'Invalid {self.display} endpoint port') from None
        production = parsed.scheme == 'https' and parsed.hostname == self.origin_host and port in {None, 443}
        loopback = False
        if os.environ.get(self.loopback_env) == '1' and parsed.scheme in {'http', 'https'}:
            try:
                loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
            except ValueError:
                pass
        if (not (production or loopback) or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip('/') not in set(self.host_paths)):
            raise AgentError(f'{self.display} requires {self.default_base_url()}')
        origin = parsed.scheme + '://' + parsed.netloc
        super().__init__(origin, timeout)
        if self.thinking_budget_policy:
            raise AgentError(f'The {self.display} profile does not support vLLM thinking-budget fractions')
        self.host = self.base_url = origin + self.api_path
        self.tokenize_url = origin + self.tokenize_path if self.tokenize_path else None
        if self.tokenize_url is None:
            self._tokenize_supported = False
        self._origin = (parsed.scheme, parsed.hostname, port or (443 if parsed.scheme == 'https' else 80))
        self.reasoning_effort = os.environ.get('SH_DEEPINFRA_REASONING_EFFORT', 'high')
        if self.reasoning_effort not in {'none', 'low', 'high', 'max'}:
            raise AgentError('DeepSeek reasoning effort must be none, low, high or max')
        self.service_tier = self._read_service_tier()
        self._api_key = read_api_key(self.key_file_env, self.key_env, self.display)
        self.observed_context_tokens = CONTEXT_TOKENS
        self.input_token_count_method = f'{self.display} preflight estimate'

    def _read_service_tier(self):
        tier = os.environ.get('SH_DEEPINFRA_SERVICE_TIER', 'standard')
        if tier not in {'standard', 'priority'}:
            raise AgentError('DeepInfra service tier must be standard or priority')
        return tier

    def _refusal(self, exc, deadline):
        """How an HTTP refusal is handled: ('unavailable' | 'rate_limit' | 'fatal', hint, transient)."""
        if exc.code in {500, 502, 503, 504}:
            # Refused before any generation: the provider or its gateway is overloaded.
            return 'unavailable', None, True
        if exc.code != 429:
            return 'fatal', HINTS.get(exc.code, 'The provider request failed; check its service status.'), False
        hint, transient = _rate_limit(exc, deadline)
        return 'rate_limit', hint, transient

    def _stream_error(self, detail):
        """(category, message) of an error event inside an accepted stream."""
        return _generation_failure(detail, self.display)

    def _redact(self, value):
        return str(value).replace(self._api_key, '[redacted]')

    def _request_url(self, url, data=None, *, optional=False, before_send=None):
        parsed = urlsplit(url)
        origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
        if (origin != self._origin or parsed.path not in set(self.routes) or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise AgentError(f'Refused to send {self.display} credentials outside approved API endpoints')
        headers = {'Content-Type': 'application/json',
                   'Accept': 'text/event-stream' if parsed.path.endswith('/chat/completions') else 'application/json'}
        # Catalog reads are public. Inference-only scoped JWTs are rejected by
        # this endpoint if supplied, although they work for generation.
        if parsed.path not in set(self.public_routes):
            headers['Authorization'] = 'Bearer ' + self._api_key
        request = Request(url, data=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
                          headers=headers)
        completion = parsed.path.endswith('/chat/completions')
        deadline = generation_deadline(self.timeout)
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise ModelNotDispatched(f'The {self.display} request time allowance elapsed before dispatch.')
        cancel = getattr(self, 'cancel', None)
        attempt, plain_errors, started, announced = 0, 0, time.monotonic(), {'at': None, 'refused': False}
        window = _generation_window.get() if completion else None
        # A completion in a generation window waits out refusals by time (a caller-managed replacement window,
        # litreview's, keeps none); the patience counts from the start of the call.
        patient = completion and window is not None and not window.replacement
        patience = _busy_patience.get()
        patience = BUSY_PATIENCE if patience is None else patience
        notice = _refusal_notice.get() if completion else None

        def announce(text, refusal=False):
            # The call's own first refusal is always said; otherwise at most one notice every NOTICE_EVERY seconds.
            now = time.monotonic()
            first_refusal = refusal and not announced['refused']
            announced['refused'] = announced['refused'] or refusal
            if notice is not None and (first_refusal or announced['at'] is None or now - announced['at'] >= NOTICE_EVERY):
                announced['at'] = now
                notice('notice', text)

        while True:
            if attempt and cancel is not None and cancel.is_set():
                raise _RejectedRetryCancelled(completion)
            if completion:
                # After a refusal every call of this process waits out the provider's pause, so parallel
                # calls do not keep hitting an overloaded provider.
                with _cooldown_lock:
                    wait = _cooldown['until'] - time.monotonic()
                if 0 < wait <= LONGEST_RETRY_AFTER and patient and time.monotonic() + wait - started > patience:
                    # Another call's pause would outlast this call's patience. A call that has not been sent yet is
                    # sent once anyway (its own answer decides); one already refused gives up as busy (checked before
                    # the deadline, so routing falls back instead of pausing the turn).
                    if attempt:
                        raise DeepInfraRateLimit('The model is temporarily at capacity.', completion_rejected=completion,
                                                 transient=True, display=self.display)
                    wait = 0
                if 0 < wait <= LONGEST_RETRY_AFTER:
                    if time.monotonic() + wait >= deadline:
                        raise ModelNotDispatched('The provider asked to wait beyond this request\'s time allowance.')
                    if wait >= 1:
                        announce(f'{self.display} is busy (another request was just refused); sending in {round(wait)} s '
                                 f'(waited {round(time.monotonic() - started)} s, up to {max(1, round(patience / 60))} min).')
                    if cancel is not None and cancel.wait(wait):
                        raise _RejectedRetryCancelled(completion)
                    if cancel is None:
                        time.sleep(wait)
                    timeout = deadline - time.monotonic()
            if before_send is not None:
                before_send()  # check after any busy wait, immediately before this HTTP attempt
            try:
                return self.opener.open(request, timeout=timeout)
            except HTTPError as exc:
                with exc:
                    if optional and exc.code in {404, 405}:
                        return None
                    kind, hint, transient = self._refusal(exc, deadline)
                    if kind == 'fatal':
                        raise AgentError(f'{self.display} HTTP {exc.code}: {hint}') from None
                    if kind == 'unavailable':
                        rejection = DeepInfraUnavailable(exc.code, completion_rejected=completion, display=self.display)
                        transient = True
                    else:
                        rejection = DeepInfraRateLimit(hint, completion_rejected=completion, transient=transient,
                                                       display=self.display, code=429 if exc.code == 429 else None)
                    delay = _retry_delay(exc.headers.get('Retry-After') if exc.headers else None, attempt)
                now = time.monotonic()
                if completion:
                    delay = _refusal_delay(delay, exc.headers.get('Retry-After') if exc.headers else None, attempt)
                if patient and exc.code != 500:
                    # A busy or unavailable provider costs nothing: waited out by time, not counted against the
                    # replacements. A plain 500 may come from the request itself, so it keeps counted retries.
                    give_up = attempt + 1 >= MAX_REFUSALS or now - started + (delay or 0) > patience
                elif completion and window is not None:
                    plain_errors += exc.code == 500  # a plain 500 has its own count, apart from busy refusals
                    give_up = window.replacement or plain_errors > RETRIES
                else:
                    give_up = bool(attempt)  # a request outside a generation window (catalog, tokenizer) retries once
                if give_up or not transient or delay is None or now + delay >= deadline:
                    raise rejection from None
                announce(f'{self.display} is busy ({hint.rstrip(".").lower() if kind == "rate_limit" else "temporarily unavailable"}); '
                         f'trying again in {max(1, round(delay))} s (waited {round(now - started)} s, '
                         f'up to {max(1, round(patience / 60))} min).', refusal=True)
                attempt += 1
                if completion:
                    with _cooldown_lock:
                        _cooldown['until'] = max(_cooldown['until'], time.monotonic() + delay)
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
                raise AgentError(f'Cannot reach {self.display}: ' + self._redact(exc)) from None

    def _payload(self, payload, *, include_images=True):
        if payload.get('model') not in self.model_ids:
            raise AgentError(f'The {self.display} test profile requires model ' + MODEL +
                             (' (or ' + LEGACY_MODEL + ' for saved work)' if LEGACY_MODEL in self.model_ids else ''))
        refs = getattr(self, 'image_refs', [])
        if refs and '_sh_bounded_title' in payload:
            raise AgentError('A bounded conversation-title call cannot include images')
        data = super()._payload(payload)
        data['model'] = self.model_ids[payload['model']]
        if self.service_tier == 'priority':
            data['service_tier'] = 'priority'
        data.pop('chat_template_kwargs', None)
        data.pop('thinking_token_budget', None)
        cap = data.pop('max_completion_tokens', None)
        if type(cap) is not int or not 0 < cap <= MAX_OUTPUT_TOKENS:
            raise AgentError(f'{self.display} needs an explicit output cap between 1 and {MAX_OUTPUT_TOKENS}')
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
            self._bounded_input_tokens(payload, data)
        if refs and include_images:
            data['messages'] = images.materialize(self.image_root, refs, data['messages'], originals=payload['messages'])
        window = _generation_window.get()
        if window is not None and window.retries and data['messages'] and isinstance(data['messages'][0].get('content'), str):
            # Measured on DeepInfra: after a silent start, a request whose first characters differ starts more
            # often than an identical one. Leading spaces change nothing for the model.
            data['messages'][0] = {**data['messages'][0], 'content': ' ' * window.retries + data['messages'][0]['content']}
        return data

    @staticmethod
    def _bytes(value):
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def _bounded_input_tokens(self, payload, data):
        """The input tokens of a validated nonthinking title or routing call; CONTEXT_TOKENS for any other call."""
        # `stream` adds the transport-only start deadline before the payload is validated; it changes
        # nothing the provider receives, so it is not part of the bounded shape.
        keys = set(payload) - {'_start_seconds'}
        if '_sh_bounded_routing' in payload:
            from .proof_routing import POLICY, SCHEMA, OUTPUT_TOKENS
            messages, options = payload.get('messages'), payload.get('options')
            valid = (payload['_sh_bounded_routing'] is True and payload.get('think') is False
                     and payload.get('stream') is True and not getattr(self, 'image_refs', None)
                     and payload.get('format') == SCHEMA
                     and not keys - {'model', 'messages', 'options', 'think', 'stream', 'format', '_sh_bounded_routing'}
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
            # An invalid marker never lowers an ordinary call's input bound.
            return CONTEXT_TOKENS
        if '_sh_bounded_title' not in payload:
            return CONTEXT_TOKENS
        messages, options = payload.get('messages'), payload.get('options')
        valid = (payload['_sh_bounded_title'] is True and payload.get('think') is False
                 and payload.get('stream') is True
                 and not keys - {'model', 'messages', 'options', 'think', 'stream', '_sh_bounded_title'}
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
            bound = self._bounded_input_tokens(payload, data)
            if bound < CONTEXT_TOKENS:
                self.input_token_count_method = 'Bounded routing UTF-8 byte and framing/schema estimate'
                return bound
        if '_sh_bounded_title' in payload:
            self.input_token_count_method = 'Bounded title UTF-8 byte and framing estimate'
            return self._bounded_input_tokens(payload, data)
        framing = 1024 + 128 * len(data['messages'])
        extras = {key: data[key] for key in ('tools', 'response_format') if key in data}
        # Tool schemas are rendered once into the prompt: twice their JSON bytes, one fixed tool-calling
        # preamble and a small per-tool header bound them (a flat 1024 per tool was the
        # largest part of an Assistant estimate about ten times its billed prompt).
        tools = data.get('tools', [])
        extra_allowance = 2 * self._bytes(extras) + (1024 + 256 * len(tools) if tools else 0)
        refs = getattr(self, 'image_refs', [])
        image_allowance, image_method = 0, ''
        if refs:
            image_allowance = images.input_allowance(refs, payload['messages'])
            image_method = ' + conservative 4096-token/image vision allowance (not base64 text tokens)'
        if self._tokenize_supported is not False:
            request = {'model': data['model'], 'messages': data['messages'], 'return_token_strs': False}
            cached = self._cached_token_count(request)
            if cached is not None:
                self.input_token_count_method = f'{self.display} message token count plus framing/tool/schema estimate' + image_method
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
                    raise AgentError(f'Malformed tokenizer response from {self.display}') from None
                self._tokenize_supported = True
                self.observed_context_tokens = min(context, CONTEXT_TOKENS)
                self._remember_token_count(request, count)
                self.input_token_count_method = f'{self.display} message token count plus framing/tool/schema estimate' + image_method
                return count + framing + extra_allowance + image_allowance
            self._tokenize_supported = False
        self.input_token_count_method = f'{self.display} UTF-8 byte and framing estimate (tokenizer unavailable)' + image_method
        return self._bytes(data['messages']) + framing + extra_allowance + image_allowance

    def _stream_deadline(self):
        return generation_deadline(self.timeout)

    def _stream_failure(self, exc, category, progress, final_usage, output_started):
        if str(exc) in {'Proof preparation exceeded its short text allowance', 'Proof preparation exceeded its short tool-argument allowance'}:
            return exc
        if isinstance(exc, SilentStreamStart):
            window = _generation_window.get()
            return DeepInfraSilentStart(progress, retry_spent=bool(window and window.retry_spent), display=self.display)
        if isinstance(exc, ProviderStreamError):
            category, message = self._stream_error(exc.detail)
        elif isinstance(exc, StalledStream):
            message = str(exc)
        elif category == 'interrupted':
            message = f'The {self.display} connection ended before the completion stream finished.'
        elif str(exc) == 'Model stream ended without finish reason and token usage':
            message = str(exc)
        else:
            message = f'{self.display} returned an invalid completion stream.'
        window = _generation_window.get()
        return DeepInfraGenerationError(message, category=category, stream_progress=progress, stats=final_usage,
                                        confirmed_terminal=isinstance(exc, ProviderStreamError), output_started=output_started,
                                        retry_spent=bool(window and window.retry_spent))

    def stream(self, payload):
        with generation_window(self):
            if payload.get('_start_seconds') is None:
                # A silent accepted stream is closed and replaced instead of waiting minutes for the provider's
                # own error. Prefill of a long prompt comes first: about 2,500 input tokens a second.
                size = sum(len(str(m.get('content') or '').encode()) for m in payload.get('messages') or [])
                payload = {**payload, '_start_seconds': START_SECONDS + size / 3 / 2500}
            yield from self._generation(payload)

    def _generation(self, payload):
        from .hosted_quota import HostedQuota
        quota = HostedQuota.from_environment(self.service_tier)
        accounting_warning = None

        def record_usage(stats, progress):
            nonlocal accounting_warning
            try:
                if stats is not None:
                    quota.charge(stats['prompt_eval_count'], stats['eval_count'], stats.get('cost_usd'))
                else:
                    # Never count base64 transport as input tokens. Unknown usage stays out of the measured totals.
                    data = self._payload(payload, include_images=False)
                    inputs = self._bytes(data['messages']) + 1024 + 128 * len(data['messages'])
                    inputs += images.input_allowance(getattr(self, 'image_refs', []), payload['messages'])
                    quota.charge(min(CONTEXT_TOKENS, inputs), min(data['max_tokens'], sum(progress.values()) + 256),
                                 estimated=True)
            except AgentError:
                accounting_warning = 'The tester allowance could not record this call.'
            return accounting_warning

        try:
            if quota is None:
                yield from super().stream(payload)
            else:
                yield from self._stream(self._payload(payload), output_char_limit=payload.get('_output_char_limit'),
                                        start_seconds=payload.get('_start_seconds'), before_send=quota.admit,
                                        on_usage=record_usage)
        except (AgentError, MeasuredStreamCancellation, _RejectedRetryCancelled) as exc:
            if accounting_warning:
                exc.args = (str(exc) + ' ' + accounting_warning,)
            redacted = self._redact(exc)
            if redacted != str(exc):
                raise AgentError(redacted) from None
            raise
