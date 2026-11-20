# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Reported output and capacity held by workers, shared by the GUI and saved reports."""


def reported_output(call):
    count = (call.get('stats') or {}).get('eval_count')
    return count if type(count) is int and count >= 0 else 0


def output_totals(charged, limit, reserved, measured):
    reserved = min(charged, reserved)
    measured = min(charged - reserved, measured)
    return {'used': measured, 'limit': limit, 'reserved': reserved, 'uncertain': charged - reserved - measured}


def job_output_budget(state, running):
    """Only a provider usage record establishes generated tokens; caps enforce admission."""
    calls = state.get('calls', [])
    reserved = sum(call.get('reserved_tokens', 0) for call in calls
                   if running and call.get('status') == 'running' and 'charged_tokens' not in call)
    return output_totals(state.get('tokens_charged', 0), state['settings'].get('max_tokens'), reserved,
                         sum(map(reported_output, calls)))


def output_summary(state):
    tokens = job_output_budget(state, state.get('status') == 'running')
    parts = [f'Provider-reported output was {tokens["used"]} tokens.']
    if tokens['uncertain']:
        parts.append(f'Usage is unknown for up to {tokens["uncertain"]} additional output tokens held against the allowance.')
    if tokens['reserved']:
        parts.append(f'Active calls reserve another {tokens["reserved"]} output tokens.')
    parts.append(f'The cumulative output limit is {tokens["limit"]} tokens.' if tokens['limit'] is not None
                 else 'There is no cumulative output limit.')
    return ' '.join(parts)


def usage_footer(state):
    settings = state['settings']

    def amount(value, limit, *, seconds=False):
        display = f'{value:.1f}' if seconds else str(value)
        if limit is None:
            return display + ' (no cumulative limit)'
        maximum = f'{limit:.1f}' if seconds else str(limit)
        return display + '/' + maximum

    return (output_summary(state) + ' Input tokens charged: ' + amount(state.get('input_tokens_charged', 0), settings.get('max_input_tokens'))
            + '; elapsed seconds: ' + amount(state.get('seconds_used', 0), settings.get('max_seconds'), seconds=True) + '.')

def one_line(text, limit=80):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'
