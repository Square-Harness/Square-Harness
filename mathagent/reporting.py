# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Consistent human-readable accounting in saved worker reports."""


def usage_footer(state):
    settings = state['settings']

    def amount(value, limit, *, seconds=False):
        display = f'{value:.1f}' if seconds else str(value)
        if limit is None:
            return display + ' (no cumulative limit)'
        maximum = f'{limit:.1f}' if seconds else str(limit)
        return display + '/' + maximum

    return ('Generated tokens charged: ' + amount(state.get('tokens_charged', 0), settings.get('max_tokens'))
            + '; input tokens charged: ' + amount(state.get('input_tokens_charged', 0), settings.get('max_input_tokens'))
            + '; elapsed seconds: ' + amount(state.get('seconds_used', 0), settings.get('max_seconds'), seconds=True) + '.')
