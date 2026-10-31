# Worked examples: when and how to check

Each script fits one run (≈5 s), uses a fixed seed and returns a `classification`
in `RESULT`: exact counterexample, numerically verified candidate, no
counterexample detected, or inconclusive.

## A. Positivity: f(x) = x⁴ − 3x² + 1 ≥ 0 for all real x (false)

Worth checking: a quartic with a negative middle coefficient may dip below zero.
Minimize numerically, then compute the minimum exactly.

```python
import numpy as np
import sympy as sp
from scipy.optimize import minimize_scalar
f = lambda t: t**4 - 3 * t**2 + 1
xs = np.linspace(-3, 3, 6001)
x0 = xs[np.argmin(f(xs))]
res = minimize_scalar(f, bracket=(x0 - 0.1, x0, x0 + 0.1))
x = sp.symbols('x', real=True)
point = sp.nsimplify(res.x, [sp.sqrt(6)])            # recognise sqrt(3/2) = sqrt(6)/2
exact = sp.simplify((x**4 - 3 * x**2 + 1).subs(x, point))
RESULT = {'numerical_min': res.fun, 'at': res.x, 'exact_point': str(point), 'exact_value': str(exact),
          'classification': 'exact counterexample' if exact < 0 else 'inconclusive'}
```

Report: f(±√(3/2)) = −5/4 < 0, an **exact counterexample** (calculus confirms
the critical points 0, ±√(3/2)).

## B. Matrix inequality: AB + BA ⪰ 0 for all SPD A, B (false)

Search random 2×2 SPD pairs, check the hypotheses, then verify an explicit
integer pair exactly.

```python
import numpy as np
import sympy as sp
rng = np.random.default_rng(0)
worst = None
for _ in range(2000):
    M, N = rng.standard_normal((2, 2, 2))
    A, B = M @ M.T + 1e-3 * np.eye(2), N @ N.T + 1e-3 * np.eye(2)
    lam = np.linalg.eigvalsh(A @ B + B @ A).min()
    if worst is None or lam < worst[0]:
        worst = (lam, A, B)
A = sp.Matrix([[1, 0], [0, 100]])                    # simplified candidate
B = sp.Matrix([[1, 1], [1, 2]])
S = A * B + B * A
eigs = S.eigenvals()
RESULT = {'random_search_min_eig': worst[0], 'A_spd': A.is_positive_definite, 'B_spd': B.is_positive_definite,
          'AB_plus_BA': str(S), 'exact_eigenvalues': [str(e) for e in eigs], 'det': int(S.det()),
          'classification': 'exact counterexample' if S.det() < 0 else 'inconclusive'}
```

Report: A = diag(1, 100), B = [[1, 1], [1, 2]] are SPD, and AB + BA = [[2, 101],
[101, 400]] has determinant −9401 < 0, so an eigenvalue 201 − √49802 ≈ −22.16:
an **exact counterexample** in dimension 2. (AB + BA ⪰ 0 does hold when A, B commute.)

## C. "For every dimension n": the volume of the unit n-ball increases with n (false)

Test the smallest dimensions and return the first failure.

```python
import sympy as sp
def V(n):
    return sp.pi**sp.Rational(n, 2) / sp.gamma(sp.Rational(n, 2) + 1)
first = next(n for n in range(2, 20) if sp.N(V(n) - V(n - 1), 30) < 0)
gap = sp.simplify(V(first) - V(first - 1))
RESULT = {'first_failing_n': first, 'V_prev': str(V(first - 1)), 'V_n': str(V(first)), 'difference': str(gap),
          'classification': 'exact counterexample' if bool(gap < 0) else 'inconclusive'}
```

Report the minimal dimension: V₆ = π³/6 < V₅ = 8π²/15 (equivalent to π < 3.2),
an **exact counterexample** at n = 6.

## D. Conditioning: g(x) = (1 − cos x)/x² − 1/2 + x²/24 > 0 for 0 < x ≤ 10⁻² (true)

float64 shows "violations" for tiny x: the subtraction 1 − cos x loses all digits.
Re-evaluate at 50 digits before believing them.

```python
import math
import mpmath as mp
xs = [10.0 ** (-k) for k in range(2, 6)]
f64 = [(1 - math.cos(x)) / x**2 - 0.5 + x**2 / 24 for x in xs]
mp.mp.dps = 50
hp = [(1 - mp.cos(mp.mpf(x))) / mp.mpf(x)**2 - mp.mpf(1) / 2 + mp.mpf(x)**2 / 24 for x in xs]
RESULT = {'x': xs, 'float64': f64, 'mp50': [mp.nstr(v, 8) for v in hp],
          'float64_negative': [v < 0 for v in f64], 'mp50_positive': all(v > 0 for v in hp),
          'series_leading_term': 'x^4/720',
          'classification': 'no counterexample detected' if all(v > 0 for v in hp) else 'numerically verified candidate'}
```

Report: the float64 negatives are **roundoff**, not violations; at 50 digits
every value is positive and matches x⁴/720 − x⁶/40320 + …, which also gives the
exact argument for small x.

## E. Spectral convergence: the first Dirichlet eigenvalue of −u″ on (0, π) is the limit 1

Refine the finite-difference grid, measure errors, residuals and the observed order.

```python
import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import eigsh
rows = []
for n in (25, 50, 100, 200):
    h = np.pi / (n + 1)
    L = (diags([-1, 2, -1], [-1, 0, 1], shape=(n, n)) / h**2).tocsc()
    lam, v = eigsh(L, k=1, sigma=0, which='LM')
    rows.append((n, lam[0], np.linalg.norm(L @ v[:, 0] - lam[0] * v[:, 0])))
errors = [abs(lam - 1.0) for _, lam, _ in rows]
orders = [np.log(errors[i] / errors[i + 1]) / np.log((rows[i + 1][0] + 1) / (rows[i][0] + 1)) for i in range(3)]
RESULT = {'n': [r[0] for r in rows], 'lambda_1': [r[1] for r in rows], 'residuals': [r[2] for r in rows],
          'errors': errors, 'observed_orders': orders,
          'classification': 'no counterexample detected'}
```

Report: errors fall like h² (observed order ≈ 2) with tiny residuals. This
**suggests** convergence to 1; the proof is the explicit discrete spectrum
λ_h = (4/h²) sin²(h/2) → 1.

## F. Skip: (x − y)² ≥ 0 for real x, y

Do **not** run anything. A real square is nonnegative; give that one-line exact
argument. An experiment would cost a sandbox run and add nothing.

## G. A valid inequality must not be "proved" numerically: AM–GM in three variables

Claim: (a + b + c)/3 ≥ (abc)^{1/3} for a, b, c > 0 (true). Minimize the margin
over a log-parametrization (a = eˢ keeps positivity) from several starts.

```python
import numpy as np
from scipy.optimize import minimize
rng = np.random.default_rng(3)
def margin(s):
    a, b, c = np.exp(np.clip(s, -20, 20))
    return (a + b + c) / 3 - np.cbrt(a * b * c)
runs = [minimize(lambda s: margin(s) / np.exp(np.mean(s)), rng.normal(scale=3, size=3), method='Nelder-Mead')
        for _ in range(8)]
samples = np.exp(rng.normal(scale=4, size=(20000, 3)))
sample_margins = samples.mean(axis=1) - np.cbrt(samples.prod(axis=1))
RESULT = {'min_scaled_margin': min(r.fun for r in runs), 'min_sample_margin_relative':
          float((sample_margins / samples.mean(axis=1)).min()),
          'classification': 'no counterexample detected'}
```

Report: the margin is ≥ 0 on every sample and its minimum is ≈ 0 at a = b = c:
**no counterexample detected**, consistent with AM–GM. This is evidence, not a
proof; the proof is the classical AM–GM argument.
