# Falsification strategies

Small, complete scripts (each runs well within 5 s). Adapt the claim, keep the
structure: admissible inputs only, the margin that should be ≥ 0, the worst case.

## 1. Scalar inequalities: grid, then local minimization

Search for the minimum of the margin g(x) = lhs − rhs over the admissible set.

```python
import numpy as np
from scipy.optimize import minimize_scalar
g = lambda x: x**4 - 3 * x**2 + 1           # claim: g(x) >= 0 for all real x
xs = np.linspace(-3, 3, 6001)                # include boundaries and a wide range
x0 = xs[np.argmin(g(xs))]
res = minimize_scalar(g, bracket=(x0 - 0.1, x0, x0 + 0.1))
RESULT = {'grid_min': float(g(xs).min()), 'x_star': res.x, 'g_star': res.fun}
```

Unbounded domains: also try large |x| and a change of variable (x = tan t).

## 2. Random matrices with structural constraints

Generate exactly the class in the hypothesis and check it.

```python
import numpy as np
rng = np.random.default_rng(0)
def spd(n):                                   # symmetric positive definite
    M = rng.standard_normal((n, n))
    return M @ M.T + 1e-3 * np.eye(n)
def orthogonal(n):
    Q, R = np.linalg.qr(rng.standard_normal((n, n)))
    return Q * np.sign(np.diag(R))
def with_spectrum(eigs):                      # prescribed eigenvalues, random basis
    Q = orthogonal(len(eigs))
    return Q @ np.diag(eigs) @ Q.T
A = with_spectrum([1e-4, 1.0, 1e4])           # ill-conditioned on purpose
RESULT = {'symmetric': bool(np.allclose(A, A.T)), 'min_eig': np.linalg.eigvalsh(A).min(),
          'cond': np.linalg.cond(A)}
```

Also try diagonal, rank-deficient, nearly singular, commuting (same basis) and
non-commuting cases; check every hypothesis on the generated input.

## 3. Optimization over admissible parameters

Maximize the violation instead of sampling blindly; restart from several seeds.

```python
import numpy as np
from scipy.optimize import minimize
rng = np.random.default_rng(1)
def margin(p):                                # claim: a^2 + b^2 >= 2ab + 0.1 (false)
    a, b = p
    return a**2 + b**2 - 2 * a * b - 0.1
best = min((minimize(margin, rng.normal(size=2), method='Nelder-Mead') for _ in range(10)),
           key=lambda r: r.fun)
RESULT = {'worst_margin': best.fun, 'at': best.x}
```

Constraints: parametrize them away (a = exp(s) for a > 0, softmax for a simplex,
QR for orthogonal matrices) rather than penalizing.

## 4. Precision escalation with mpmath

A tiny float64 margin is not a violation until it survives higher precision.

```python
import math
import mpmath as mp
x = 1e-8
f64 = (1 - math.cos(x)) / x**2                # catastrophic cancellation in float64
mp.mp.dps = 50
exact = (1 - mp.cos(mp.mpf(x))) / mp.mpf(x)**2
RESULT = {'float64': f64, 'mp50': mp.nstr(exact, 20), 'float64_wrong': abs(f64 - 0.5) > 1e-3}
```

Rule: repeat the suspicious evaluation at 30–50 digits; trust a violation only
when its size is stable as the precision increases.

## 5. Spectral residual checks

Never trust a computed eigenpair without its residual and the conditioning.

```python
import numpy as np
rng = np.random.default_rng(2)
A = rng.standard_normal((6, 6))
w, V = np.linalg.eig(A)                       # nonsymmetric: eigenvectors may be ill-conditioned
residuals = np.linalg.norm(A @ V - V * w, axis=0)
RESULT = {'max_residual': residuals.max(), 'eigvec_cond': np.linalg.cond(V),
          'symmetric': bool(np.allclose(A, A.T))}
```

For symmetric problems use `eigh`; for claims about extreme eigenvalues compare
`eigvalsh` with a Rayleigh-quotient bound from an explicit test vector.

## 6. Discretization and convergence tests

Refine and measure the observed order; a limit claim needs the trend, not one value.

```python
import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import eigsh
def first_eig(n):                             # -u'' on (0, pi), Dirichlet: limit 1
    h = np.pi / (n + 1)
    L = (diags([-1, 2, -1], [-1, 0, 1], shape=(n, n)) / h**2).tocsc()
    return eigsh(L, k=1, sigma=0, which='LM')[0][0]
ns = [25, 50, 100, 200]
errs = [abs(first_eig(n) - 1.0) for n in ns]
orders = [np.log(errs[i] / errs[i + 1]) / np.log((ns[i + 1] + 1) / (ns[i] + 1)) for i in range(3)]
RESULT = {'errors': errs, 'observed_orders': orders}
```

Report the observed order (here ≈ 2) and say that convergence is suggested, not proved.

## 7. From a numerical candidate to an exact counterexample

Round the candidate to simple rationals or recognizable surds, then verify exactly.

```python
import sympy as sp
x_num = 1.2247448713915890                    # numerical minimizer found earlier
guess = sp.nsimplify(x_num, [sp.sqrt(2), sp.sqrt(3), sp.sqrt(6)])
x = sp.symbols('x', real=True)
value = sp.simplify((x**4 - 3 * x**2 + 1).subs(x, guess))
RESULT = {'exact_point': str(guess), 'exact_value': str(value), 'negative': bool(value < 0)}
```

For matrices: replace entries by nearby small integers or rationals, check the
hypotheses exactly (`sp.Matrix(...).is_positive_definite`), and compute the
offending eigenvalue or determinant symbolically.
