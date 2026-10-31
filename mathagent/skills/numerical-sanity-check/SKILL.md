---
name: numerical-sanity-check
description: >
  Proactively test mathematical conjectures, inequalities, identities and
  intermediate proof claims using inexpensive numerical experiments. Search for
  counterexamples using random values, small dimensions, boundary cases and
  numerical optimization. Use when numerical falsification can improve
  mathematical reasoning. May also be explicitly invoked.
metadata:
  invocation: automatic
---

# Numerical Sanity Check

You are a mathematical falsification assistant: detect incorrect claims with
cheap experiments. Numerical experiments guide reasoning; they never replace a proof.

## Core rules

- Check a nontrivial claim (inequality, positivity, matrix or spectral identity,
  "for every n", asymptotics, a doubtful proof step) only when a result could
  change your reasoning. Skip it when an exact argument is immediate.
- Try to falsify: smallest cases, boundaries, degenerate inputs, seeded random
  sampling, minimization of the margin. Respect every hypothesis.
- A violation is a candidate until admissibility, conditioning and precision are
  checked (mpmath; exact SymPy values when possible).
- Report exactly one of: exact counterexample, numerically verified candidate,
  no counterexample detected, inconclusive. Passing tests never prove a claim; a
  failed or inconclusive run is not evidence against it.

## When to run a check

Consider one for: positivity of functions or operators; inequalities; matrix
identities and spectral properties; statements "for every dimension n";
suspected counterexamples; asymptotic estimates and limits; variational or
optimization claims; a nontrivial intermediate step of a proof.

Do not run one when an exact proof or counterexample is immediate (say
(x−y)² ≥ 0: just give the argument), when no computation can assess the
statement, when the result could not change your answer, or when automatic
checks are unavailable. Most mathematical statements need no experiment.

## Procedure

1. State the precise claim, its quantifiers and the admissible parameters.
2. Ask what computable example could falsify it; prefer the smallest one.
3. Test simple and degenerate cases, small dimensions (1, 2, 3, 4), boundaries
   and extreme parameters, then seeded random sampling or numerical
   minimization of the quantity that should be nonnegative.
4. Run one short script with `execute_python` (fixed seed; results in `RESULT`).
5. Treat any violation as a candidate: re-check admissibility (symmetry, positive
   definiteness, domain), conditioning and rounding; confirm at higher precision
   (mpmath) and, when possible, exactly (SymPy, rational or closed-form inputs).
6. Simplify the counterexample to explicit, ideally exact, values.
7. Report the mathematical consequence briefly.

Every run must respect the claim's hypotheses. Seek violations, not confirmations:
minimize the margin, push toward boundaries, mix distributions (not only uniform).

## Rigor

Classify every outcome as exactly one of:
- **exact counterexample**: verified symbolically or in exact arithmetic;
- **numerically verified candidate**: violation well above the error level,
  confirmed at higher precision, not yet exact;
- **no counterexample detected**: the tested range only; this is not a proof;
- **inconclusive**: errors, limits, roundoff-level margins or unclear admissibility.

A failed, timed-out or inconclusive experiment is not evidence against a theorem.
Margins near machine precision (about 1e-15 relative in float64) are roundoff
until higher precision shows otherwise.

## Budget

Each run: about 5 s, 512 MB, 1 CPU, no network or files; at most 3 runs per check;
no escalation. Keep experiments small; a heavy computation is for `/python-lab`.

## Reporting

Successful check: one or two sentences in the mathematical discussion (what was
tested, the range, "no counterexample found", and that this is not a proof).
Counterexample: the claim, explicit values or objects, the observed violation,
its precision or error estimate, and whether it is exact, numerically verified or
uncertain. Never overstate numerical evidence.

References (read with `read_skill_reference`): `strategies` (search techniques
with code), `examples` (worked cases, including when to skip).
