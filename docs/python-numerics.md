# Python sandbox: python-lab and numerical sanity checks

Two skills share one local, Docker-based Python sandbox:

- **`python-lab`** runs the computations you ask for: code, simulations,
  linear algebra, optimization, symbolic work and plots. It is **explicit**:
  only you start it, with `/python-lab` in the terminal or `@python_lab` in the
  interface's Assistant.
- **`numerical-sanity-check`** lets the model try to **falsify** a claim with a
  cheap experiment before relying on it: a negative value, a 2×2
  counterexample, a failing dimension. It is **automatic** in conversations,
  and you can also ask for it with `/sanity-check` or `@sanity_check`.

Numerical evidence is reported as exactly one of: *exact counterexample*,
*numerically verified candidate*, *no counterexample detected*, or
*inconclusive*. A passing check is never presented as a proof.

## Install

1. **Docker.** Linux: Docker Engine (rootful or rootless). macOS: Docker
   Desktop or another engine such as Colima. Windows: Docker Desktop with the
   WSL 2 backend and Linux containers. The harness finds `docker` on `PATH`
   (or `SQUARE_DOCKER=/path/to/docker`) and honours `DOCKER_HOST` and
   `DOCKER_CONTEXT`. Images run on `linux/amd64` and `linux/arm64`.
2. **Build the image once** (about 200 MB of wheels; nothing is installed later):

   ```bash
   python -m mathagent.numerics build      # docker build -t square-harness/python-numerics:2 …
   python -m mathagent.numerics doctor     # checks Docker, the image and a tiny run
   ```

   For another architecture or a shared registry:
   `docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/python-numerics:2 --push mathagent/numerics/docker`,
   then pass `--python-image <registry>/python-numerics:2`. Images are never
   pulled automatically (`--pull never`).

The image is pinned: `python:3.12.13-slim-trixie` by its multi-architecture
digest, and NumPy 2.5.4, SciPy 1.18.1, SymPy 1.14.0, mpmath 1.3.0 and
Matplotlib 3.11.2 (with their dependencies) by wheel hashes in
`mathagent/numerics/docker/requirements.lock`. To update it, edit
`requirements.in` and regenerate the lock with the command at its top, for
both `x86_64` and `aarch64`.

If Docker or the image is missing, `/python-lab` says exactly what to do,
and automatic checks are switched off with a one-line note: chat keeps working.

## Python Lab

```text
critic> /python-lab Compute the eigenvalues of [[2,-1,0],[-1,2,-1],[0,-1,2]] and check them against 2-2cos(kπ/4).
python_lab> Simulate dX = 0.5 X dt + 0.3 X dW up to T = 1 with 10000 paths and plot five of them.
python_lab> Run this unchanged:
            import numpy as np; print(np.linalg.svd(np.vander([1, 2, 3]))[1])
```

`/python-lab` (or `/python_lab`) switches the terminal to the lab mode; later
messages stay there until you choose another mode. The model writes complete,
seeded scripts, runs them, reads errors, fixes genuine coding mistakes, and
reports numbers, figures and the experiment id. When you ask for code to run
unchanged, it is executed byte for byte. Default limits: **30 s, 4 GB, 2 CPUs,
20 runs per request**. If the model asks for more, the harness shows you the
requested limits and runs only after you approve them, up to a configured
ceiling (by default 300 s, 16 GB, 4 CPUs).

In the visual interface, type `@` in an Assistant (∀) conversation and pick
**`@python_lab`** (a flask on teal) or **`@sanity_check`** (¬∀ on vermilion),
then your request. The card shows the run; requests for more resources open the
usual approval dialog. Only your selection starts these two: the Assistant's own
`delegate` tool does not offer them, and the harness refuses them unless the
turn's `@` selection named that mode.

## Automatic numerical sanity checks

The `numerical-sanity-check` tool is offered to the model in:

- terminal `critic` and `explore` chats (and `/review`);
- in the visual interface: Critique and Exploration conversations, the
  Assistant's own replies, and the critic/explore workers it delegates to.

It is **not** offered in proof jobs, reviews, literature work or write-ups,
which keep their existing tool policy. The model decides whether a check is
worth it: the skill tells it to skip immediate exact arguments (such as
(x − y)² ≥ 0) and to look for violations rather than confirmations. Each check
has **at most 3 runs** (one user turn), each **5 s, 2 GB, 1 CPU**, with no
escalation: a heavier experiment needs your `/python-lab`. Memory limits are
caps, not reservations: a run uses only what it allocates.

- Disable automatic checks: `--no-numerical-checks` (terminal and interface).
  `/sanity-check` and `/python-lab` stay available.
- Disable the sandbox entirely: `--no-python-sandbox`.
- Automatic checks need a model context of at least 32,768 tokens (the
  default is 40,960); smaller contexts skip them so the tool schemas and
  results do not crowd out the mathematics.
- Ask explicitly: `/sanity-check Is AB + BA positive semidefinite for SPD A, B?`
  runs the same skill and the same strict limits.

## How the skills reach the model

The skills live in `mathagent/skills/<name>/SKILL.md` with Agent Skills style
frontmatter (`name`, `description`, `metadata.invocation: explicit|automatic`);
`/skills` lists them. Disclosure is progressive: the `execute_python` tool
description carries the skill's description, its *Core rules* and the budget;
the full `SKILL.md` and the `references/` files (search strategies, worked
examples with runnable code) are fetched on demand with `read_skill_reference`.
The Assistant's own system prompt also gets two lines that encourage one cheap
falsification experiment for nontrivial claims.

## Security model

All generated code is treated as untrusted. Each run is a fresh container
started from a fixed argument array (no shell, no model-supplied flags, image
or command):

| Control | Setting |
| --- | --- |
| Network | `--network none` (loopback only) |
| User | uid/gid 10001, no capabilities (`--cap-drop ALL`), `no_new_privs`, no setuid binaries in the image |
| Filesystem | read-only root; `/tmp` is a size-limited `noexec` tmpfs, the only writable place |
| Host access | no mounts, no host environment variables, no Docker socket, no home directory |
| Resources | cgroup memory (no swap), CPU quota, process count, open files; wall-clock deadline |
| Output | stdout/stderr go to the tmpfs and come back as bounded head+tail excerpts; the host also caps the bytes it reads while streaming |
| Lifetime | `--rm`; on timeout, cancellation, output overflow, Ctrl+C or an exception the host runs `docker rm --force` |
| Logs | `--log-driver none`, so a flood of output never reaches the daemon's disk |

The code is sent on stdin. Results come back as one JSON document; figures
and data files are accepted only as PNG (checked header), CSV, TXT or JSON
with simple names, within the per-run artifact budget.

`no_new_privs` is enforced twice: Docker's `--security-opt no-new-privileges`
when the engine supports it, and always by the sandbox runner itself
(`prctl(PR_SET_NO_NEW_PRIVS)`) before any untrusted code runs. Snap-packaged
Docker on Ubuntu refuses `exec` under the Docker option because of an AppArmor
rule; `doctor` then reports `"no_new_privileges": "process"`, and the
guarantee still holds for all untrusted code (the tests check
`NoNewPrivs: 1` from inside the container).

**Policies are bound by the harness, not chosen by the model.** The terminal's
`/python-lab` command (your input) binds the lab policy; every automatic context
binds the sanity policy. Tool arguments can only lower limits. A model that asks
for more in an automatic check, names another policy or image, or calls itself
"python-lab" gets a refusal; binding `python-lab` without an explicit command
raises an error in code.

### Configuring limits

```bash
square-harness --python-limits limits.json …
```

```json
{
  "sanity": {"limits": {"timeout_seconds": 5, "memory_mb": 2048, "cpus": 1}, "max_runs": 3},
  "lab": {"defaults": {"timeout_seconds": 30, "memory_mb": 4096, "cpus": 2},
          "ceiling": {"timeout_seconds": 300, "memory_mb": 16384, "cpus": 4}, "max_runs": 20}
}
```

Each policy also has `pids`, `tmpfs_mb`, `output_bytes` and `artifact_bytes`.
Values are checked against built-in maxima. Automatic checks cannot exceed
30 s, 4 GB, 2 CPUs or 10 runs, and nothing exceeds 600 s, 32 GB or 4 CPUs.
On a smaller machine, lower the memory values in this file.

## Experiments and artifacts

Every run is saved in the workspace, next to the other jobs:

```text
.mathagent/experiments/exp-20261031-221508-3fa2c1/
  record.json   code, seed, parameters, image id, limits, status, timings, stdout/stderr, RESULT, files
  code.py       the exact code that ran
  figure-1.png  open Matplotlib figures (with a text summary in record.json)
  values.csv    files the code saved in OUTPUT_DIR (.png .csv .txt .json)
```

The oldest records are pruned beyond 200 runs or 256 MB. The model sees
each run as compact JSON: the experiment id, status, bounded stdout and
stderr, the error and traceback, `RESULT`, and figure summaries (axes, labels,
ranges), so text-only models can still read a plot. `read_experiment`
retrieves more of a saved run by id; it never takes a host path.

```bash
square-harness …  then  /experiments                       # list in the terminal
python -m mathagent.numerics list --workspace DIR           # list
python -m mathagent.numerics show EXPERIMENT_ID --workspace DIR
python -m mathagent.numerics rerun EXPERIMENT_ID --workspace DIR   # same code, seed, parameters, limits
python -m mathagent.numerics run script.py [--lab] [--seed N]
python -m mathagent.numerics clean                          # remove any leftover sandbox containers
```

Inside a script: `SEED` (also applied to `random` and `numpy.random`),
`PARAMS` (the call's `parameters`), `OUTPUT_DIR`, and `RESULT` (any
JSON-friendly value, including NumPy and SymPy objects) for structured results.

## Tests

```bash
python -m unittest discover -s tests                     # whole suite; sandbox tests skip without the image
cd tests && python -m unittest -v test_python_executor test_python_security test_python_lab test_numerical_sanity_check
```

The sandbox tests are split in two kinds. Process tests (cleanup on timeout,
cancellation, Ctrl+C and output floods; missing Docker; the snap fallback;
policy enforcement; routing) use a scripted `docker` stand-in and run everywhere.
Container tests need the built image and are skipped without it (set
`SQUARE_SKIP_DOCKER_TESTS=1` to force that). They check the isolation from
inside a real container, figures, errors, timeouts, memory and output limits,
reproducibility, and every runnable block of the skills' reference files. CI
runs both kinds in a dedicated `sandbox` job.

`scripts/test-numerics-live.py --host URL --model ID` is an opt-in smoke test
with a real model. It records whether the model runs a check for each worked
claim, skips the trivial one, and avoids claiming a numerical proof.

## End-to-end examples

Excerpts from `scripts/test-numerics-live.py` with Qwen3.8-27B-FP8 served by
vLLM, each in a fresh workspace. Model wording varies from run to run.

**Python Lab.** `/python-lab Simulate geometric Brownian motion dX = 0.5 X dt + 0.3 X dW
with X0 = 1 up to T = 1 using Euler–Maruyama with 10000 paths and seed 1, report the
mean of X_T with its standard error against exp(0.5), and plot five sample paths.`
One sandbox run (status `ok`), then:

```text
| Sample mean of X_T        | 1.660521 |
| Standard error (s/√N)     | 0.005142 |
| Exact mean exp(0.5)       | 1.648721 |
| z-score of the difference | +2.30    |
… ordinary Monte Carlo fluctuation of a single seed …
![GBM sample paths](.mathagent/experiments/exp-20261031-225206-9e3454/figure-1.png)
```

**Automatic check, valid claim.** *Is (a + b + c)/3 ≥ (abc)^{1/3} for all positive
reals?* The model ran one search for counterexamples ("Search for counterexamples
to (a+b+c)/3 >= (abc)^(1/3) and verify the algebraic identity…"), found none,
and then answered with the classical proof. The numerics were never presented
as the proof.

**Automatic check, skipped.** *Is (x − y)² ≥ 0 for all real x, y?* No run:
"the square of any real number is nonnegative … no numerical check is needed —
the argument is exact and immediate."

**Automatic check, false claim found numerically, then made exact.** *Is the volume of
the unit ball in Rⁿ increasing in n?* Two runs ("Verify values of the unit ball volume
V_n = π^{n/2}/Γ(n/2+1), locate the maximum…"), then: "**No.** … it increases up to
n = 5, then strictly decreases", with V₅ = 8π²/15 ≈ 5.2638 > V₆ = π³/6 ≈ 5.1677 and a
rigorous ratio argument.

**Automatic check, false claim with an immediate exact counterexample.**
*Is x⁴ − 3x² + 1 ≥ 0 for every real x?* No run was needed: "the claim fails
already at x = 1" (value −1), with the exact minimum −5/4 at x = ±√6/2.

The worked cases in `references/examples.md` (including the 2×2 counterexample
to AB + BA ⪰ 0, the first failing dimension of the unit-ball volume, and a
roundoff trap that 50-digit arithmetic resolves) are executed by the test suite
in the real sandbox.

## Limitations

- Docker is required. Apptainer/Singularity (common on HPC clusters) and
  Podman are not supported backends yet; on a cluster without Docker, run the
  harness where Docker is available, or use `--no-python-sandbox`.
- The terminal cannot show figures: it prints their saved paths. The interface
  shows them under each run (click to open full size).
- Each run is stateless: there is no persistent kernel between calls.
- Whether a check is worthwhile is the model's judgment, guided by the skill;
  the harness enforces the budget, not the mathematics.
