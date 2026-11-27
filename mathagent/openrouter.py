# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""OpenRouter: the same DeepSeek V4.1 Flash 1017 model, served through OpenRouter's router (--api-profile openrouter).

It is the DeepInfra hosted transport (mathagent.deepinfra.DeepInfra: retries, busy waits, silent starts, replays,
the tester ledger) with OpenRouter's address, key, model name and wording, plus fixed routing rules sent with every
request:

- only the upstream providers in SH_OPENROUTER_PROVIDERS (DEFAULT_PROVIDERS otherwise), tried in that order, with
  fallback to the next one when a provider is down or busy;
- zero data retention and no data collection, fp8 quantization, required parameters (a request with tools or a
  JSON schema never goes where they would be dropped), the standard service tier (priority copies of a model match
  the same provider slug and cost more), a price cap that keeps every listed provider but leaves out the "fast"
  tiers, and OpenRouter's context compression off (a prompt is never silently cut).

Under the tester ledger each call is charged the price OpenRouter reports for it (`usage.cost`).

Measured 2026-11-27: reasoning streams as `delta.reasoning`, which the shared parser reads; the finish frame comes
twice (the second one carries usage and cost), which tolerate_repeated_finish accepts; each chunk names the upstream
provider. OpenRouter has no tokenizer endpoint, so input tokens are the conservative byte estimate.
"""
import copy
import os
import re

from .agent import AgentError
from .deepinfra import (DeepInfra, HINTS, MODEL, _BUSY, _credit_limit, _generation_failure, _mentions,
                        _provider_signals, _read_error_body)

WIRE_MODEL = 'deepseek/deepseek-v4.1-flash-1017'
# Chosen 2026-11-27 among OpenRouter's endpoints for this model: fp8, zero data retention, at least 384k output,
# structured outputs. Order chosen 2026-11-27 after measuring them through this client (long reasoning answers):
# Crusoe ~243 tok/s ($0.19/$0.78 per M), Cohere ~155 ($0.12/$0.40), CoreWeave ~317 ($0.30/$1.20), DeepInfra ~136
# ($0.10/$0.29), Parasail erratic (65-376). The first available one serves; the next takes over when it is busy.
DEFAULT_PROVIDERS = ('crusoe', 'cohere', 'coreweave', 'deepinfra', 'parasail')
MAX_PRICE = {'prompt': '0.30', 'completion': '1.20'}  # USD per million tokens; leaves out "fast" tiers ($0.60/$2.40)
_SLUG = re.compile(r'^[a-z0-9][a-z0-9._-]*(/[a-z0-9._-]+)*$')
# OpenRouter's wording of a refusal that is worth waiting out, matched inside its fields.
_ROUTER_BUSY = _BUSY + ('rate limited upstream', 'temporarily rate limited', 'retry shortly', 'provider returned error',
                        'no available model provider', 'overloaded')
# Inside an accepted stream, an upstream failure worth replacing: these codes, or these metadata.error_type values
# (OpenRouter's stable field). Any other 4xx keeps its own meaning (invalid request, authorization, credit).
_BUSY_CODES = {408, 429, 500, 502, 503, 504, 524, 529}
_BUSY_TYPES = {'rate_limit_exceeded', 'provider_overloaded', 'provider_unavailable', 'timeout'}
# A request no allowed provider can ever serve (routing rules, parameters): waiting does not help. Matched in the
# normalized signals (underscores and hyphens read as spaces).
_UNSERVABLE = ('no endpoints found', 'no allowed providers', 'data policy', 'require parameters', 'requires parameters',
               'routing requirements', 'guardrail')
_ROUTING_HINT = ('No allowed provider can serve this request: check SH_OPENROUTER_PROVIDERS and the routing rules '
                 '(zero data retention, fp8, price cap) and the key\'s guardrails.')


def _error_fields(value):
    """The numeric code, metadata.error_type and metadata.limit_source of an OpenRouter error body or stream error."""
    error = value.get('error') if isinstance(value, dict) and isinstance(value.get('error'), dict) else value
    if not isinstance(error, dict):
        return None, None, None
    code = error.get('code')
    try:
        code = int(code) if not isinstance(code, bool) else None
    except (TypeError, ValueError):
        code = None
    metadata = error.get('metadata') if isinstance(error.get('metadata'), dict) else {}
    kind, source = metadata.get('error_type'), metadata.get('limit_source')
    return code, kind if isinstance(kind, str) else None, source if isinstance(source, str) else None

def routing():
    """The provider routing object sent with every request (see the module docstring)."""
    raw = os.environ.get('SH_OPENROUTER_PROVIDERS', '')
    providers = [item.strip() for item in raw.split(',') if item.strip()] if raw.strip() else list(DEFAULT_PROVIDERS)
    if not 1 <= len(providers) <= 16 or any(not _SLUG.match(item) for item in providers):
        raise AgentError('SH_OPENROUTER_PROVIDERS must list 1 to 16 OpenRouter provider slugs, such as deepinfra,cohere')
    return {'order': providers, 'only': providers, 'allow_fallbacks': True, 'require_parameters': True,
            'data_collection': 'deny', 'zdr': True, 'quantizations': ['fp8'], 'max_price': dict(MAX_PRICE)}


class OpenRouter(DeepInfra):
    profile = 'openrouter'
    display = 'OpenRouter'
    origin_host = 'openrouter.ai'
    api_path = '/api/v1'
    host_paths = ('', '/api/v1')
    routes = ('/api/v1/models', '/api/v1/chat/completions')
    public_routes = ('/api/v1/models',)
    tokenize_path = None
    key_file_env, key_env, loopback_env = 'OPENROUTER_API_KEY_FILE', 'OPENROUTER_API_KEY', 'OPENROUTER_ALLOW_LOOPBACK'
    model_ids = {MODEL: WIRE_MODEL}  # the legacy V4 model is not offered here; native images stay off until verified
    tolerate_repeated_finish = True

    def __init__(self, host=None, timeout=600):
        super().__init__(host, timeout)
        self.routing = routing()

    def _read_service_tier(self):
        # OpenRouter is always sent the standard tier ('default'); SH_DEEPINFRA_SERVICE_TIER is DeepInfra's.
        return 'standard'

    def _payload(self, payload, *, include_images=True):
        data = super()._payload(payload, include_images=include_images)
        data.pop('stream_options', None)  # deprecated on OpenRouter; usage is requested below
        data['usage'] = {'include': True}
        data['service_tier'] = 'default'
        data['provider'] = copy.deepcopy(self.routing)
        data['plugins'] = [{'id': 'context-compression', 'enabled': False}]
        return data

    def models(self):
        """The served model names: OpenRouter's catalog, with this profile's model under its Square Harness name."""
        served = super().models()
        return [name for name, wire in self.model_ids.items() if wire in served] + served

    def _refusal(self, exc, deadline):
        code = exc.code
        if code in {400, 401, 405, 422}:
            return 'fatal', HINTS[code], False
        value = _read_error_body(exc, deadline)
        _, _, source = _error_fields(value)
        signals = _provider_signals(value)
        if code in {403, 404}:
            if _mentions(signals, _UNSERVABLE):
                return 'fatal', _ROUTING_HINT, False
            if source or _credit_limit(signals):
                return 'fatal', 'Check the OpenRouter credit balance and the key\'s credit limit.', False
            return 'fatal', HINTS[code], False
        if code == 402:
            # OpenRouter's in-flight budget: the spending of calls still running is counted against the credit
            # balance; it frees up as they finish. Any other 402 is out of credit. The stated source decides; the
            # wording only when there is none.
            if source == 'openrouter_in_flight_budget' or (source is None and _mentions(signals, ('in flight',))):
                return 'rate_limit', 'the in-flight spending budget is full; waiting for running calls to finish.', True
            return 'fatal', 'Check the OpenRouter credit balance and the key\'s credit limit.', False
        if code == 429:
            if _credit_limit(signals):
                return 'rate_limit', 'Check the provider credit balance and spending limit.', False
            if _mentions(signals, _BUSY):
                return 'rate_limit', 'The model is temporarily at capacity.', True
            return 'rate_limit', 'The provider is temporarily limiting requests.', True
        if code == 503 and _mentions(signals, _UNSERVABLE):
            return 'fatal', _ROUTING_HINT, False
        if code in {408, 500, 502, 503, 504, 524, 529}:
            return 'unavailable', None, True
        return 'fatal', 'The provider request failed; check its service status.', False

    def _stream_error(self, detail):
        # After the 200, OpenRouter sends an upstream failure as an error chunk (once every fallback has failed).
        code, kind, _ = _error_fields(detail)
        signals = _provider_signals(detail)
        if _mentions(signals, _UNSERVABLE):
            return 'invalid_request', _ROUTING_HINT
        if _credit_limit(signals):
            return 'quota', 'OpenRouter reported a credit or spending limit during generation.'
        if code is not None and 400 <= code < 500 and code not in _BUSY_CODES:
            return _generation_failure({'code': code}, self.display)
        busy = (code in _BUSY_CODES or kind in _BUSY_TYPES
                or ((code is None or code >= 500) and _mentions(signals, _ROUTER_BUSY)))
        if busy:
            return 'service_unavailable', 'OpenRouter reported that the provider is busy or failed before answering.'
        return _generation_failure(detail, self.display)
