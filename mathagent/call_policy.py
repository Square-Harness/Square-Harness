# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Shared physical request allocation, independent of cumulative cost limits.

Allocations never remove prompt material. A caller decides which exact source
units belong in a request and which bounded recovery is appropriate afterward.
"""
import copy
import json

from .agent import AgentError


CONTEXT_MARGIN = 512
POLICY_VERSION = 1


class CallContextError(AgentError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def count_input(client, payload):
    """Count the complete backend request, or conservatively bound its input.

Backend counters receive messages, tools, structured output and template options
as one payload. Backends determine how their response schema affects input.
"""
    if getattr(client, 'image_refs', None):
        from .images import supported
        if not supported(getattr(client, 'profile', None), payload.get('model')):
            raise AgentError('Native image input requires DeepInfra DS4.1 Flash 1017; this backend/model cannot receive images')
    counter = getattr(client, 'count_input_tokens', None)
    count = counter(payload) if callable(counter) else None
    if count is None:
        material = json.dumps({key: payload[key] for key in ('messages', 'tools', 'format') if key in payload},
                              ensure_ascii=False)
        # About 3-4 characters a token for English and 2.5-3 for LaTeX; other scripts about one each.
        ascii_chars = sum(1 for char in material if ord(char) < 128)
        count = -(-ascii_chars * 2 // 5) + (len(material) - ascii_chars) + 256
        return count, 'conservative character estimate (2.5 per token)'
    if type(count) is not int or count < 0:
        raise AgentError('Invalid exact input token count')
    return count, getattr(client, 'input_token_count_method', 'server token count')


def allocate_call(client, payload, desired, minimum=128, explicit_cap=None):
    """Return ``(fitted_payload, allocation)`` with a physical output allowance.

``desired`` is a phase's requested allowance, not ordinary-chat ``--predict``.
``minimum`` expresses a role's useful/required output floor. A cumulative cost
policy can supply its remaining allowance as ``explicit_cap``; no such limit is
implied when absent. The input is never mutated or clipped.
"""
    if type(desired) is not int or desired <= 0 or type(minimum) is not int or not 0 < minimum <= desired:
        raise ValueError('Call output allowances must be positive integers with minimum <= desired')
    if explicit_cap is not None and (type(explicit_cap) is not int or explicit_cap < 0):
        raise ValueError('An explicit call output cap must be a nonnegative integer or null')
    fitted = copy.deepcopy(payload)
    options = fitted.get('options')
    context = options.get('num_ctx') if isinstance(options, dict) else None
    if type(context) is not int or context <= 0:
        raise ValueError('A model call needs a positive physical context limit')
    options['num_predict'] = desired
    # Tokenization discovers model capacity on some backends. The configured
    # request limit is not prompt material: leave it out only for this preflight
    # so a smaller discovered server limit can be reconciled before dispatch.
    preflight = copy.deepcopy(fitted)
    preflight['options'].pop('num_ctx', None)
    count, method = count_input(client, preflight)
    server_context = getattr(client, 'observed_context_tokens', None)
    if server_context is not None and (type(server_context) is not int or server_context <= 0):
        raise AgentError('Invalid observed model context capacity')
    configured_context = context
    if server_context is not None:
        context = min(context, server_context)
    options['num_ctx'] = context
    room = max(0, context - count - CONTEXT_MARGIN)
    cap = min(desired, room, explicit_cap if explicit_cap is not None else desired)
    allocation = {'policy_version': POLICY_VERSION, 'input_tokens': count, 'method': method,
                  'requested_output_tokens': desired, 'output_tokens': cap,
                  'minimum_output_tokens': minimum, 'context_tokens': context,
                  'configured_context_tokens': configured_context,
                  'server_context_tokens': server_context,
                  'context_source': 'configured' if server_context is None else 'server_and_configured',
                  'context_margin_tokens': CONTEXT_MARGIN,
                  'available_output_tokens': room, 'explicit_cap': explicit_cap}
    if cap < minimum:
        if explicit_cap is not None and explicit_cap < minimum:
            raise CallContextError(
                f'The explicit output allowance {explicit_cap} is below the required {minimum}. '
                'No hypotheses, sources or saved work were clipped.', allocation)
        raise CallContextError(
            f'The complete request needs {count} input tokens ({method}); context {context} '
            f'leaves {room} output tokens after framing, below the required {minimum}. '
            'No hypotheses, sources or saved work were clipped.', allocation)
    options['num_predict'] = cap
    return fitted, allocation
