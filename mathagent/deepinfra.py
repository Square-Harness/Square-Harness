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
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .agent import AgentError
from .backends import OpenAICompatible


BASE_URL = 'https://api.deepinfra.com/v1/openai'
MODEL = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
LEGACY_MODEL = 'deepseek-ai/DeepSeek-V4-Flash-0731'
SUPPORTED_MODELS = (MODEL, LEGACY_MODEL)
CONTEXT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = 384_000


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
        try:
            return self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            with exc:
                if optional and exc.code in {404, 405}:
                    return None
            # A provider error can reflect submitted headers or credentials.
            # Never copy any part of its body into saved jobs or GUI logs.
            hints = {400: 'Check the model request and supported parameters.',
                     401: 'Check the API credential and its expiry.',
                     402: 'Check available credit and the credential spending limit.',
                     403: 'Check the credential scopes and allowed models.',
                     404: 'The requested model or endpoint is unavailable.',
                     405: 'This endpoint does not support the requested operation.',
                     422: 'Check the model request and supported parameters.',
                     429: 'The provider rate, capacity or spending limit was reached.'}
            hint = hints.get(exc.code, 'The provider request failed; check its service status.')
            raise AgentError(f'DeepInfra HTTP {exc.code}: {hint}') from None
        except (URLError, TimeoutError, OSError) as exc:
            raise AgentError('Cannot reach DeepInfra: ' + self._redact(exc)) from None

    def _payload(self, payload):
        if payload.get('model') not in SUPPORTED_MODELS:
            raise AgentError('The DeepInfra test profile requires model ' + MODEL +
                             ' (or ' + LEGACY_MODEL + ' for saved work)')
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
        return data

    @staticmethod
    def _bytes(value):
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def count_input_tokens(self, payload):
        """Include full material, plus conservative template/schema allowance.

        The native count covers messages only. The additional byte allowance
        accommodates framing and server-injected tool/schema text, without
        claiming an exact reconstruction of DeepInfra's rendering.
        """
        data = self._payload(payload)
        framing = 1024 + 128 * len(data['messages'])
        extras = {key: data[key] for key in ('tools', 'response_format') if key in data}
        extra_allowance = 2 * self._bytes(extras) + 1024 * len(data.get('tools', []))
        if self._tokenize_supported is not False:
            response = self._request_url(self.tokenize_url, {'model': data['model'], 'messages': data['messages'],
                                                           'return_token_strs': False}, optional=True)
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
                self.input_token_count_method = 'DeepInfra message token count plus framing/tool/schema estimate'
                return count + framing + extra_allowance
            self._tokenize_supported = False
        self.input_token_count_method = 'DeepInfra UTF-8 byte and framing estimate (tokenizer unavailable)'
        return self._bytes(data['messages']) + framing + extra_allowance

    def stream(self, payload):
        try:
            yield from super().stream(payload)
        except AgentError as exc:
            redacted = self._redact(exc)
            if redacted != str(exc):
                raise AgentError(redacted) from None
            raise
