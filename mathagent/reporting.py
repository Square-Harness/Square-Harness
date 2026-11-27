# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Reported output and capacity held by workers, shared by the GUI and saved reports."""
import unicodedata


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
                         sum(map(reported_output, calls)) + sum(entry.get('measured_tokens', 0)
                             for entry in state.get('evidence', []) if isinstance(entry, dict)))


def output_summary(state):
    tokens = job_output_budget(state, state.get('status') == 'running')
    parts = [f'Provider-reported output was {tokens["used"]} tokens.']
    if tokens['uncertain']:
        parts.append(f'Usage is unknown for up to {tokens["uncertain"]} additional output tokens held against the allowance.')
    if tokens['reserved']:
        parts.append(f'Active calls reserve another {tokens["reserved"]} output tokens.')
    parts.append(f'The cumulative output limit is {tokens["limit"]} tokens.' if tokens['limit'] is not None
                 else 'There is no cumulative output limit.')
    estimated = [c for c in state.get('calls', []) if c.get('charge_basis') == 'estimate']
    if estimated:
        parts.append(f'{len(estimated)} interrupted call(s) with no usage report are charged an estimate from their '
                     f'streamed output plus a margin ({sum(c.get("charged_tokens", 0) for c in estimated)} tokens in all).')
    resettled = state.get('charge_resettlements') or []
    if resettled:
        parts.append(f'On resume, {len(resettled)} earlier interrupted call(s) were re-charged from their saved journals: '
                     f'{sum(r["from_tokens"] for r in resettled)} tokens held became {sum(r["to_tokens"] for r in resettled)}.')
    names = {'tokens': 'output tokens', 'seconds': 'seconds', 'rounds': 'attempts'}
    for addition in state.get('allowance_additions') or []:
        parts.append(f'Extra allowance added on resume at your request ({addition.get("time", "")}): '
                     + ', '.join(f'+{addition[k]:g} {names[k]}' for k in names if addition.get(k)) + '.')
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
    # Control characters (NUL included) become spaces: a title or preview is a single printable line.
    text = ' '.join(''.join(' ' if unicodedata.category(ch) == 'Cc' else ch for ch in str(text)).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'
