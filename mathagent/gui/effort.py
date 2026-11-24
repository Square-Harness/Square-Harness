# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Effort levels: depth and time behind Low, Medium and High.

The interface's forms use the same legacy table (frontend/src/effort.ts).
DS4 presets come from model_limits and are sent to the interface by the server.
"""
from ..litreview import breadth
from ..model_limits import EFFORTS as DS4_EFFORTS, LEGACY_EFFORTS as DS4_LEGACY_EFFORTS, proof_limits, workflow_budget
from ..router import effort_for

# Token estimates remain for older integrations. New jobs have no cumulative
# token ceiling unless one was explicitly supplied at launch or in the form.
EFFORTS = {
    'low': dict(tries=1, minutes=1, tokens=30_000, input=120_000, requests=20, chars=15_000),
    'medium': dict(tries=3, minutes=15, tokens=60_000, input=240_000, requests=50, chars=30_000),
    'high': dict(tries=5, minutes=30, tokens=100_000, input=400_000, requests=80, chars=60_000),
}
LEGACY_EFFORTS = {
    'xhigh': dict(tries=7, minutes=60, tokens=150_000, input=600_000, requests=120, chars=100_000),
    'poincare': dict(tries=10, minutes=120, tokens=200_000, input=800_000, requests=160, chars=150_000),
}
LABELS = {'low': 'Low', 'medium': 'Medium', 'high': 'High', 'xhigh': 'Extra high', 'poincare': 'Poincaré'}
# A review reads the whole paper in each call, many calls at once: some 50 calls of 80k input tokens for a
# 3,000-line paper at Medium, each reserving about three times that while it runs (no DS4 tokenizer: UTF-8 bytes).
# Four times the input allowance of other DS4 workers. ('detailed_review' is the former name of the review; a typo
# check reads the whole paper in each call too.) A review and a typo check run at one fixed level (router.FIXED_EFFORT).
REVIEW_MODES = ('referee', 'detailed_review', 'typo_check')
REVIEW_INPUT = 4


def level(mode, name, args=None, *, review_companion=False):
    """Legacy workload estimates; a review (which checks every proof) allows twice the working time on a local model and
    four times the input on DS4 (every call reads the whole paper); literature uses its own search and writing budget."""
    table = DS4_EFFORTS if getattr(args, 'api_profile', None) == 'deepinfra' else EFFORTS
    legacy = DS4_LEGACY_EFFORTS if table is DS4_EFFORTS else LEGACY_EFFORTS
    name = 'low' if review_companion and mode == 'literature' else effort_for(mode, name)
    value = dict(table.get(name, legacy.get(name, table['medium'])))
    if table is DS4_EFFORTS:
        if mode == 'prove':
            proof = proof_limits(name, args)
            value.update(tokens=proof['tokens'], minutes=proof['seconds'] / 60)
        else:
            work = workflow_budget(name, args)
            value.update(tokens=work['tokens'], minutes=work['seconds'] / 60)
    if mode == 'literature':
        review = breadth(value['tries'])
        value['requests'] = review['requests']
        if table is not DS4_EFFORTS:
            value['minutes'] = review['seconds'] / 60
        if getattr(args, 'research_seconds_explicit', False):
            value['minutes'] = min(value['minutes'], args.research_seconds / 60)
        cap = getattr(args, 'research_requests', None)
        if cap is not None:
            value['requests'] = min(cap, value['requests'])
    if mode in REVIEW_MODES:
        if table is DS4_EFFORTS:
            value.update(input=REVIEW_INPUT * value['input'])
        else:
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


def limits(mode, level, args, *, review_companion=False):
    """Budget keyword arguments for Hub.start_route; answers in the conversation have none."""
    ds4 = getattr(args, 'api_profile', None) == 'deepinfra'
    table = DS4_EFFORTS if ds4 else EFFORTS
    legacy = DS4_LEGACY_EFFORTS if ds4 else LEGACY_EFFORTS
    level = 'low' if review_companion and mode == 'literature' else effort_for(mode, level)
    effort = table.get(level, legacy.get(level, table['medium']))
    seconds = effort['minutes'] * 60
    def ceiling(name, value):
        configured = getattr(args, name, None)
        return min(value, configured) if configured is not None else value
    if mode == 'prove':
        if ds4:
            return proof_limits(level, args)
        tokens, solve, verify = _proof_ceilings(args, getattr(args, 'proof_tokens', None))
        return dict(rounds=effort['tries'], tokens=tokens, seconds=seconds, solve_tokens=solve, verify_tokens=verify)
    output = getattr(args, 'research_tokens', None)
    if ds4:
        work = workflow_budget(level, args)
        output, seconds = work['tokens'], work['seconds']
    input_tokens = ceiling('research_input_tokens', effort['input']) if ds4 else getattr(args, 'research_input_tokens', None)
    if mode in ('writeup', 'summarize'):
        return dict(tokens=output, input_tokens=input_tokens, seconds=seconds)
    if mode == 'check':
        # A literature check has its own small budgets (refcheck.EFFORTS), chosen by level.
        return dict(effort=level if level in EFFORTS else 'medium')
    if mode in REVIEW_MODES:
        # More checking time and input, without silently increasing an explicit token cap.
        if ds4:
            input_tokens = ceiling('research_input_tokens', REVIEW_INPUT * effort['input'])
        return dict(rounds=effort['tries'], tokens=output,
                    input_tokens=input_tokens, seconds=seconds if ds4 else 2 * seconds,
                    requests=effort['requests'], chars=effort['chars'])
    if mode == 'literature':
        review = breadth(effort['tries'])
        if not ds4:
            seconds = ceiling('research_seconds', review['seconds']) if getattr(args, 'research_seconds_explicit', False) else review['seconds']
        return dict(rounds=effort['tries'], tokens=output, input_tokens=input_tokens, seconds=seconds,
                    requests=ceiling('research_requests', review['requests']), chars=effort['chars'])
    if mode in ('quick_review', 'explain'):
        return dict(rounds=effort['tries'], tokens=output,
                    input_tokens=input_tokens, seconds=seconds,
                    requests=effort['requests'], chars=effort['chars'])
    return {}
