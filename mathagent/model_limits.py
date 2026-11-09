# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Explicit DS4 deployment defaults; existing local-model limits stay unchanged.

Context capacity, per-call generation and cumulative workflow allowances are
separate settings. These are starting allowances, not measured optimal values.
Call allocation and each job's saved explicit limits still take precedence.
"""

PROFILE = 'ds4.1-flash-1017'
PROFILE_VERSION = 2
PROOF_OUTPUT_TOKENS = 384_000
PROOF_CALL_SECONDS = 10_800

OUTPUTS = {
    'assistant': 32_768, 'assistant_final': 65_536, 'assistant_recovery': 131_072,
    'review_check': 65_536, 'review_confirm': 32_768, 'review_explain': 65_536,
    'review_recovery': 131_072, 'review_direct': 16_384, 'review_metadata': 16_384,
    'review_write': 32_768, 'writeup': 65_536, 'writeup_min': 32_768,
    'research_plan': 8_192, 'research_investigate': 16_384,
    'research_draft': 32_768, 'research_review': 16_384, 'research_revise': 32_768,
    'router': 4_096,
}
INPUTS = {
    'source_overview': 224_000, 'review_packet': 56_000, 'review_lines': 600,
    'research_excerpt': 36_000, 'research_lines': 400, 'style_packet': 96_000,
    'working_note': 10_000, 'recovery_note': 16_000,
    'writeup_conversation': 800_000, 'router_context': 32_768,
}
DEFAULTS = {
    'ctx': 1_048_576, 'predict': 32_768, 'temperature': 1.0,
    'proof_solve_tokens': PROOF_OUTPUT_TOKENS, 'proof_verify_tokens': PROOF_OUTPUT_TOKENS,
    'proof_call_seconds': PROOF_CALL_SECONDS,
    'proof_tokens': 11_520_000, 'proof_seconds': 30 * PROOF_CALL_SECONDS,
    'research_tokens': 2_097_152, 'research_input_tokens': 33_554_432,
    'research_seconds': 1_800, 'research_chars': 200_000,
    'assistant_tokens': 12_568_576, 'assistant_input_tokens': 33_554_432,
    'gui_model_concurrency': 5, 'gui_concurrency': 5, 'assistant_concurrency': 5,
}
EFFORTS = {
    'low': dict(tries=1, minutes=10, tokens=65_536, research_tokens=65_536,
                input=1_048_576, requests=20, chars=60_000, solve_tokens=32_768, verify_tokens=16_384),
    'medium': dict(tries=3, minutes=30, tokens=3_456_000, research_tokens=262_144,
                   input=4_194_304, requests=50, chars=120_000,
                   solve_tokens=PROOF_OUTPUT_TOKENS, verify_tokens=PROOF_OUTPUT_TOKENS),
    'high': dict(tries=5, minutes=60, tokens=5_760_000, research_tokens=524_288,
                 input=8_388_608, requests=80, chars=240_000,
                 solve_tokens=PROOF_OUTPUT_TOKENS, verify_tokens=PROOF_OUTPUT_TOKENS),
    'xhigh': dict(tries=7, minutes=120, tokens=8_064_000, research_tokens=1_048_576,
                  input=16_777_216, requests=120, chars=400_000,
                  solve_tokens=PROOF_OUTPUT_TOKENS, verify_tokens=PROOF_OUTPUT_TOKENS),
    'poincare': dict(tries=10, minutes=240, tokens=11_520_000, research_tokens=2_097_152,
                     input=33_554_432, requests=160, chars=600_000,
                     solve_tokens=PROOF_OUTPUT_TOKENS, verify_tokens=PROOF_OUTPUT_TOKENS),
}


def proof_limits(name, args):
    """Full call ceilings; a smaller total never silently shrinks a DS4 call.

    One solver pass may have one review-format retry, so its durable allowance
    covers a solve and two reviews. A repair is itself another solver pass.
    """
    effort = EFFORTS.get(name, EFFORTS['medium'])
    solve = min(args.proof_solve_tokens, effort['solve_tokens'])
    verify = min(args.proof_verify_tokens, effort['verify_tokens'])
    tokens = effort['tries'] * (solve + 2 * verify)
    configured = getattr(args, 'proof_tokens', None)
    if configured is not None:
        tokens = min(tokens, configured)
    seconds = (600 if name == 'low' else
               3 * effort['tries'] * getattr(args, 'proof_call_seconds', PROOF_CALL_SECONDS))
    configured_seconds = getattr(args, 'proof_seconds', None)
    if configured_seconds is not None:
        seconds = min(seconds, configured_seconds)
    return dict(rounds=effort['tries'], tokens=tokens, seconds=seconds,
                solve_tokens=solve, verify_tokens=verify,
                repair_tokens=min(solve, getattr(args, 'proof_repair_tokens', None) or solve))


def effort_presets(args):
    """Server-provided presets, keeping non-Proof budgets and times separate."""
    presets = {}
    for name, effort in EFFORTS.items():
        proof = proof_limits(name, args)
        presets[name] = {**effort, 'tokens': proof['tokens'],
                         'solve_tokens': proof['solve_tokens'], 'verify_tokens': proof['verify_tokens'],
                         'repair_tokens': proof['repair_tokens'], 'proof_seconds': proof['seconds'],
                         'proof_call_seconds': getattr(args, 'proof_call_seconds', PROOF_CALL_SECONDS)}
    return presets


def is_ds4(subject):
    client = getattr(subject, 'client', subject)
    return getattr(client, 'profile', 'default') == 'deepinfra'


def output_limit(subject, name, fallback):
    return OUTPUTS.get(name, fallback) if is_ds4(subject) else fallback


def input_limit(subject, name, fallback):
    return INPUTS.get(name, fallback) if is_ds4(subject) else fallback


def sampling(subject, fallback):
    return {'temperature': 1.0, 'top_p': 0.95} if is_ds4(subject) else dict(fallback)


def apply_defaults(args, arguments):
    """Apply only to flags the user omitted; manual allowances are authoritative."""
    if getattr(args, 'api_profile', None) != 'deepinfra':
        return
    explicit = {value.split('=', 1)[0] for value in arguments if value.startswith('--')}
    for name, value in DEFAULTS.items():
        if '--' + name.replace('_', '-') not in explicit:
            setattr(args, name, value)
    if '--request-timeout' not in explicit:
        args.request_timeout = args.proof_call_seconds
    # GUI launch caps accommodate every effort; terminal jobs derive their
    # cumulative allowance from their actual number of solver passes.
    rounds = 10 if getattr(args, 'gui', False) else args.proof_rounds
    if '--proof-tokens' not in explicit:
        args.proof_tokens = rounds * (args.proof_solve_tokens + 2 * args.proof_verify_tokens)
    if '--proof-seconds' not in explicit:
        args.proof_seconds = 3 * rounds * args.proof_call_seconds
