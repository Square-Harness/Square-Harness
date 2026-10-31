# Python Lab: worked requests

Each script is complete (sandboxes are stateless), seeds its randomness and puts
its main numbers in `RESULT`. All of them run within the default limits.

## 1. "Compute the eigenvalues of this matrix"

```python
import numpy as np
A = np.array([[2.0, -1.0, 0.0], [-1.0, 2.0, -1.0], [0.0, -1.0, 2.0]])
w, V = np.linalg.eigh(A)                       # symmetric: eigh, sorted ascending
residual = np.linalg.norm(A @ V - V * w)       # check A v = lambda v
exact = 2 - 2 * np.cos(np.arange(1, 4) * np.pi / 4)
print('eigenvalues', w)
RESULT = {'eigenvalues': w, 'residual': residual, 'max_error_vs_exact': np.abs(w - exact).max()}
```

Report the eigenvalues, the residual, and that they match 2 − 2cos(kπ/4).

## 2. "Simulate this SDE": geometric Brownian motion

dX = μX dt + σX dW, X₀ = 1. Euler–Maruyama against the exact mean e^{μT}.

```python
import numpy as np
import matplotlib.pyplot as plt
rng = np.random.default_rng(SEED if SEED is not None else 0)
mu, sigma, T, n, paths = 0.5, 0.3, 1.0, 200, 20000
dt = T / n
X = np.ones(paths)
sample = [X[:5].copy()]
for _ in range(n):
    X = X + mu * X * dt + sigma * X * np.sqrt(dt) * rng.standard_normal(paths)
    sample.append(X[:5].copy())
mean, stderr = X.mean(), X.std(ddof=1) / np.sqrt(paths)
plt.plot(np.linspace(0, T, n + 1), np.array(sample))
plt.title('Euler–Maruyama sample paths'); plt.xlabel('t'); plt.ylabel('X')
RESULT = {'mean_XT': mean, 'standard_error': stderr, 'exact_mean': float(np.exp(mu * T)),
          'z_score': (mean - np.exp(mu * T)) / stderr}
```

Report the Monte Carlo mean with its standard error, not just the point value,
and mention the time-step bias (O(dt) weak error) when comparing with exact values.

## 3. "Plot this function"

```python
import numpy as np
import matplotlib.pyplot as plt
x = np.linspace(-2, 2, 801)
f = x**4 - 3 * x**2 + 1
plt.plot(x, f, label='$x^4-3x^2+1$'); plt.axhline(0, color='k', lw=0.5)
plt.legend(); plt.title('f(x) = x^4 - 3x^2 + 1')
RESULT = {'min_on_grid': f.min(), 'argmin_on_grid': x[f.argmin()]}
```

The figure is saved automatically (`figure-1.png`); its text summary lists the
axes, labels and data ranges.

## 4. "Compare these methods": stiff ODE solvers

```python
import numpy as np
from scipy.integrate import solve_ivp
def f(t, y):                       # stiff: y' = -1000 (y - cos t)
    return -1000.0 * (y - np.cos(t))
rows = {}
for method in ('RK45', 'Radau', 'LSODA'):
    sol = solve_ivp(f, (0, 2), [0.0], method=method, rtol=1e-6, atol=1e-9)
    rows[method] = {'nfev': int(sol.nfev), 'y_end': float(sol.y[0, -1])}
    print(f'{method:6s} nfev={sol.nfev:6d} y(2)={sol.y[0, -1]:.8f}')
RESULT = rows
```

The explicit method needs far more evaluations on a stiff problem; say so with the counts.

## 5. "Solve symbolically, then check numerically"

```python
import sympy as sp
x = sp.symbols('x', real=True)
f = x**4 - 3 * x**2 + 1
critical = sp.solve(sp.diff(f, x), x)
values = {str(c): sp.nsimplify(f.subs(x, c)) for c in critical}
minimum = min(values.values())
print(critical, values)
RESULT = {'critical_points': [str(c) for c in critical], 'values': {k: str(v) for k, v in values.items()},
          'minimum': str(minimum)}
```

## Errors

If a run fails, read `error.type`, `error.message` and the traceback lines from
`<experiment>`; fix the code and rerun. A `timeout` or `memory` status means the
limits were reached: simplify, or request more resources (the user approves).
