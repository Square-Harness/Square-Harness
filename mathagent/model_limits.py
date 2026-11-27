# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Explicit DS4 deployment defaults; existing local-model limits stay unchanged.

Context capacity, per-call generation and cumulative workflow allowances are
separate settings. These are starting allowances, not measured optimal values.
Call allocation and each job's saved explicit limits still take precedence.
"""

PROFILE = 'ds4.1-flash-1017'
PROFILE_VERSION = 3
MODEL_OUTPUT_TOKENS = 384_000
PROOF_CALL_SECONDS = 10_800

# These roles generate mathematical content. Compact control messages have separate limits.
OUTPUTS = dict.fromkeys((
    'assistant', 'assistant_final', 'assistant_recovery', 'review_check', 'review_confirm',
    'review_explain', 'review_recovery', 'review_direct', 'review_write', 'writeup',
    'research_plan', 'research_investigate', 'research_draft', 'research_review', 'research_revise',
), MODEL_OUTPUT_TOKENS)
OUTPUTS.update(review_metadata=16_384, router=4_096, writeup_min=32_768)
# Review calls that answer in JSON get a finite ceiling, three times the largest measured need: on DS4
# (November 2026) a proof check used at most 44k tokens of reasoning and answer. The ceiling never binds
# a real check; it ends a reasoning loop in minutes instead of hours (the whole ceiling is also reserved
# from the job's budget while the call runs).
OUTPUTS.update(review_check=131_072, review_confirm=131_072, review_scope=131_072, review_read=131_072,
               review_write=32_768)
INPUTS = {
    'brief_index': 24_000, 'brief_solver': 60_000,
    'source_overview': 224_000, 'review_packet': 56_000, 'review_lines': 600, 'review_whole': 400_000,
    'research_excerpt': 36_000, 'research_lines': 400, 'style_packet': 1_000_000, 'style_guidelines': 12_000,
    'working_note': 10_000, 'recovery_note': 16_000,
    'writeup_conversation': 800_000, 'router_context': 32_768,
}
DEFAULTS = {
    'ctx': 1_048_576, 'predict': MODEL_OUTPUT_TOKENS, 'temperature': 1.0,
    'proof_solve_tokens': MODEL_OUTPUT_TOKENS, 'proof_verify_tokens': MODEL_OUTPUT_TOKENS,
    'proof_call_seconds': PROOF_CALL_SECONDS,
    'proof_tokens': 5_760_000, 'proof_seconds': 15 * PROOF_CALL_SECONDS,
    'research_tokens': 15 * MODEL_OUTPUT_TOKENS, 'research_input_tokens': 33_554_432,
    'research_seconds': 15 * PROOF_CALL_SECONDS, 'research_chars': 200_000,
    'assistant_tokens': 12_568_576, 'assistant_input_tokens': 33_554_432,
    'gui_model_concurrency': 5, 'gui_concurrency': 5, 'assistant_concurrency': 5,
}
EFFORTS = {
    'low': dict(tries=1, minutes=3 * PROOF_CALL_SECONDS // 60, tokens=3 * MODEL_OUTPUT_TOKENS, research_tokens=3 * MODEL_OUTPUT_TOKENS,
                input=1_048_576, requests=20, chars=60_000, solve_tokens=MODEL_OUTPUT_TOKENS, verify_tokens=MODEL_OUTPUT_TOKENS),
    'medium': dict(tries=3, minutes=9 * PROOF_CALL_SECONDS // 60, tokens=3_456_000, research_tokens=9 * MODEL_OUTPUT_TOKENS,
                   input=4_194_304, requests=50, chars=120_000,
                   solve_tokens=MODEL_OUTPUT_TOKENS, verify_tokens=MODEL_OUTPUT_TOKENS),
    'high': dict(tries=5, minutes=15 * PROOF_CALL_SECONDS // 60, tokens=5_760_000, research_tokens=15 * MODEL_OUTPUT_TOKENS,
                 input=8_388_608, requests=80, chars=240_000,
                 solve_tokens=MODEL_OUTPUT_TOKENS, verify_tokens=MODEL_OUTPUT_TOKENS),
}
# Paused workers retain the allowances they started with.
LEGACY_EFFORTS = {
    'xhigh': dict(tries=7, minutes=120, tokens=8_064_000, research_tokens=1_048_576,
                  input=16_777_216, requests=120, chars=400_000,
                  solve_tokens=MODEL_OUTPUT_TOKENS, verify_tokens=MODEL_OUTPUT_TOKENS),
    'poincare': dict(tries=10, minutes=240, tokens=11_520_000, research_tokens=2_097_152,
                     input=33_554_432, requests=160, chars=600_000,
                     solve_tokens=MODEL_OUTPUT_TOKENS, verify_tokens=MODEL_OUTPUT_TOKENS),
}


def proof_limits(name, args):
    """Full call ceilings; a smaller total never silently shrinks a DS4 call.

    One solver pass may have one review-format retry, so its durable allowance
    covers a solve and two reviews. A repair is itself another solver pass.
    """
    effort = EFFORTS.get(name, LEGACY_EFFORTS.get(name, EFFORTS['medium']))
    solve = min(args.proof_solve_tokens, effort['solve_tokens'])
    verify = min(args.proof_verify_tokens, effort['verify_tokens'])
    tokens = effort['tries'] * (solve + 2 * verify)
    configured = getattr(args, 'proof_tokens', None)
    if configured is not None:
        tokens = min(tokens, configured)
    seconds = 3 * effort['tries'] * getattr(args, 'proof_call_seconds', PROOF_CALL_SECONDS)
    configured_seconds = getattr(args, 'proof_seconds', None)
    if configured_seconds is not None:
        seconds = min(seconds, configured_seconds)
    return dict(rounds=effort['tries'], tokens=tokens, seconds=seconds,
                solve_tokens=solve, verify_tokens=verify,
                repair_tokens=min(solve, getattr(args, 'proof_repair_tokens', None) or solve))


def workflow_budget(name, args=None):
    """Cumulative inference allowance for a DS4 worker at its selected effort."""
    effort = EFFORTS.get(name, LEGACY_EFFORTS.get(name, EFFORTS['medium']))
    tokens, seconds = effort['research_tokens'], effort['minutes'] * 60
    configured = getattr(args, 'research_tokens', None)
    if configured is not None:
        tokens = min(tokens, configured)
    if getattr(args, 'research_seconds', None) is not None:
        seconds = min(seconds, args.research_seconds)
    return dict(tokens=tokens, seconds=seconds)


def effort_presets(args):
    """Server-provided presets, keeping non-Proof budgets and times separate."""
    presets = {}
    for name, effort in EFFORTS.items():
        proof = proof_limits(name, args)
        work = workflow_budget(name, args)
        presets[name] = {**effort, 'research_tokens': work['tokens'], 'minutes': work['seconds'] / 60, 'tokens': proof['tokens'],
                         'solve_tokens': proof['solve_tokens'], 'verify_tokens': proof['verify_tokens'],
                         'repair_tokens': proof['repair_tokens'], 'proof_seconds': proof['seconds'],
                         'proof_call_seconds': getattr(args, 'proof_call_seconds', PROOF_CALL_SECONDS)}
    return presets


# The hosted API profiles that serve DS4.1 Flash with the robust transport (mathagent.deepinfra.DeepInfra and its
# subclasses): every DS4 limit, preset and hosted behaviour keys on this set, never on one provider's name.
HOSTED_DS4 = frozenset({'deepinfra', 'openrouter'})


def hosted_profile(profile):
    """Whether an --api-profile name is a hosted DS4 profile."""
    return profile in HOSTED_DS4


def ds4_args(args):
    """Whether the launch arguments select a hosted DS4 profile."""
    return getattr(args, 'api_profile', None) in HOSTED_DS4


def is_ds4(subject):
    client = getattr(subject, 'client', subject)
    return getattr(client, 'profile', 'default') in HOSTED_DS4


def substantive_output(subject, fallback):
    return MODEL_OUTPUT_TOKENS if is_ds4(subject) else fallback


def output_limit(subject, name, fallback):
    return OUTPUTS.get(name, fallback) if is_ds4(subject) else fallback


def input_limit(subject, name, fallback):
    return INPUTS.get(name, fallback) if is_ds4(subject) else fallback


def sampling(subject, fallback):
    return {'temperature': 1.0, 'top_p': 0.95} if is_ds4(subject) else dict(fallback)


def apply_defaults(args, arguments):
    """Apply only to flags the user omitted; manual allowances are authoritative."""
    if not ds4_args(args):
        return
    explicit = {value.split('=', 1)[0] for value in arguments if value.startswith('--')}
    for name, value in DEFAULTS.items():
        if '--' + name.replace('_', '-') not in explicit:
            setattr(args, name, value)
    if '--predict' not in explicit:
        args.predict = min(args.predict, args.ctx - 1025)
    if '--request-timeout' not in explicit:
        args.request_timeout = args.proof_call_seconds
    # GUI launch caps accommodate every effort; terminal jobs derive their
    # cumulative allowance from their actual number of solver passes.
    rounds = 5 if getattr(args, 'gui', False) else args.proof_rounds
    if '--proof-tokens' not in explicit:
        args.proof_tokens = rounds * (args.proof_solve_tokens + 2 * args.proof_verify_tokens)
    if '--proof-seconds' not in explicit:
        args.proof_seconds = 3 * rounds * args.proof_call_seconds
