# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Effort levels: depth and time behind Low … Poincaré.

The interface's forms use the same legacy table (frontend/src/effort.ts).
DS4 presets come from model_limits and are sent to the interface by the server.
"""
from ..model_limits import EFFORTS as DS4_EFFORTS, proof_limits

# Token estimates remain for older integrations. New jobs have no cumulative
# token ceiling unless one was explicitly supplied at launch or in the form.
EFFORTS = {
    'low': dict(tries=1, minutes=1, tokens=30_000, input=120_000, requests=20, chars=15_000),
    'medium': dict(tries=3, minutes=15, tokens=60_000, input=240_000, requests=50, chars=30_000),
    'high': dict(tries=5, minutes=30, tokens=100_000, input=400_000, requests=80, chars=60_000),
    'xhigh': dict(tries=7, minutes=60, tokens=150_000, input=600_000, requests=120, chars=100_000),
    'poincare': dict(tries=10, minutes=120, tokens=200_000, input=800_000, requests=160, chars=150_000),
}
LABELS = {'low': 'Low', 'medium': 'Medium', 'high': 'High', 'xhigh': 'Extra high', 'poincare': 'Poincaré'}


def level(mode, name, args=None):
    """Legacy workload estimates; detailed reviews allow twice the working time."""
    table = DS4_EFFORTS if getattr(args, 'api_profile', None) == 'deepinfra' else EFFORTS
    value = dict(table.get(name, table['medium']))
    if table is DS4_EFFORTS:
        if mode == 'prove':
            proof = proof_limits(name, args)
            value.update(tokens=proof['tokens'], minutes=proof['seconds'] / 60)
        else:
            value['tokens'] = value['research_tokens']
    if mode == 'detailed_review':
        value.update(tokens=2 * value['tokens'], input=2 * value['input'], minutes=2 * value['minutes'])
    return value


def _proof_ceilings(args, tokens, effort=None):
    """Fit one full solve and its review in the budget, keeping the launch repair floors."""
    solve, verify = args.proof_solve_tokens, args.proof_verify_tokens
    if effort is not None:
        solve = min(solve, effort['solve_tokens'])
        verify = min(verify, effort['verify_tokens'])
    if tokens is None or tokens >= solve + verify:
        return tokens, solve, verify
    floor = max(128, args.proof_repair_tokens or 0, args.proof_min_solve_tokens or 0)
    fitted = min(solve, max(floor, tokens * solve // (solve + verify)))
    review = min(verify, max(128, tokens - fitted))
    return tokens, fitted, review


def limits(mode, level, args):
    """Budget keyword arguments for Hub.start_route; answers in the conversation have none."""
    ds4 = getattr(args, 'api_profile', None) == 'deepinfra'
    table = DS4_EFFORTS if ds4 else EFFORTS
    effort = table.get(level, table['medium'])
    seconds = effort['minutes'] * 60
    def ceiling(name, value):
        configured = getattr(args, name, None)
        return min(value, configured) if configured is not None else value
    if mode == 'prove':
        if ds4:
            return proof_limits(level, args)
        tokens, solve, verify = _proof_ceilings(args, getattr(args, 'proof_tokens', None))
        return dict(rounds=effort['tries'], tokens=tokens, seconds=seconds, solve_tokens=solve, verify_tokens=verify)
    output = ceiling('research_tokens', effort['research_tokens']) if ds4 else getattr(args, 'research_tokens', None)
    input_tokens = ceiling('research_input_tokens', effort['input']) if ds4 else getattr(args, 'research_input_tokens', None)
    if mode in ('writeup', 'summarize'):
        return dict(tokens=output, input_tokens=input_tokens, seconds=seconds)
    if mode == 'check':
        # A literature check has its own small budgets (refcheck.EFFORTS), chosen by level.
        return dict(effort=level if level in EFFORTS else 'medium')
    if mode == 'detailed_review':
        # More checking time, without silently increasing an explicit token cap.
        return dict(rounds=effort['tries'], tokens=output,
                    input_tokens=input_tokens, seconds=2 * seconds,
                    requests=effort['requests'], chars=effort['chars'])
    if mode in ('literature', 'referee', 'quick_review', 'explain'):
        return dict(rounds=effort['tries'], tokens=output,
                    input_tokens=input_tokens, seconds=seconds,
                    requests=effort['requests'], chars=effort['chars'])
    return {}
