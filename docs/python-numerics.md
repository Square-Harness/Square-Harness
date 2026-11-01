# Python sandbox: python-lab and numerical sanity checks

Two skills share one local Docker sandbox:

- **`python-lab`** runs the computations you ask for: code, simulations, linear
  algebra, optimization, symbolic work and plots. Only you start it:
  `/python-lab` in the terminal, `@python_lab` in an Assistant conversation.
- **`numerical-sanity-check`** lets the model try to falsify a nontrivial claim
  with a cheap experiment before relying on it. It is offered automatically in
  conversations; `/sanity-check` and `@sanity_check` ask for it explicitly.

Numerical evidence is reported as an *exact counterexample*, a *numerically
verified candidate*, *no counterexample detected* or *inconclusive*; passing
tests are never presented as a proof.

## Install

Install Docker (Engine on Linux, Desktop on macOS or Windows with WSL 2), then
build the pinned image once. Nothing is installed or pulled at run time.

```bash
python -m mathagent.numerics build     # square-harness/python-numerics:2
python -m mathagent.numerics doctor    # Docker, image and a test run
```

The base image (`python:3.12.13-slim-trixie`) is pinned by digest and NumPy,
SciPy, SymPy, mpmath and Matplotlib by wheel hashes in
`mathagent/numerics/docker/requirements.lock` (amd64 and arm64). The harness
finds `docker` on `PATH` (or `SQUARE_DOCKER`) and honours `DOCKER_HOST`. Without
Docker or the image, `/python-lab` explains what is missing and automatic checks
are simply not offered.

## Use

```text
/python-lab Simulate 20000 Brownian paths on [0, 1] and plot 10 of them.
/sanity-check Is AB + BA positive semidefinite for all SPD matrices A, B?
```

In the interface, type `@` in an Assistant conversation and pick **Python Lab**
(a flask) or **Sanity check** (¬∀). Each run's files appear in the conversation
where the run happened: PNG and SVG figures as images, HTML in a sandboxed frame,
PDF and data files as links.

Automatic checks are offered in Critique and Exploration conversations, to the
Assistant and to its critic/explore workers, never to proof, review, literature
or write-up jobs, and only with a model context of at least 32,768 tokens. The
skill tells the model to skip immediate exact arguments and to look for
violations rather than confirmations. `--no-numerical-checks` turns them off;
`--no-python-sandbox` disables the sandbox entirely.

## Limits

| | Automatic check / `@sanity_check` | `python-lab` |
| --- | --- | --- |
| Time per run | 5 s | 30 s |
| Memory | 2 GB | 4 GB |
| CPUs | 1 | 2 |
| Runs per turn | 3 | 20 |
| More | never | up to 300 s, 16 GB, 4 CPUs after your approval |

Memory limits are caps, not reservations. Change the limits with
`--python-limits limits.json`; values are checked against built-in maxima
(automatic checks: 30 s, 4 GB, 2 CPUs, 10 runs; anything: 600 s, 32 GB, 4 CPUs):

```json
{"sanity": {"limits": {"timeout_seconds": 5, "memory_mb": 2048}, "max_runs": 3},
 "lab": {"defaults": {"memory_mb": 4096}, "ceiling": {"memory_mb": 16384}}}
```

## Security

Generated code is untrusted. Each run is a fresh container started from a fixed
argument array:

- no network, no host mounts, no host environment, no Docker socket;
- uid 10001 without capabilities, `no_new_privs`, no setuid binaries;
- read-only root; a size-limited `noexec` `/tmp` is the only writable place;
- cgroup memory, CPU and process limits, a wall-clock deadline and a CPU-time
  backstop; output is bounded while it streams;
- the container is removed on success, error, timeout, cancellation and Ctrl+C,
  and orphans of a killed session are removed at the next start.

The harness, not the model, binds the policy: the user's own command binds
python-lab, every automatic context binds the strict sanity policy. Tool
arguments can only lower limits, and the Assistant cannot delegate to
`python_lab` or `sanity_check`.

Snap-packaged Docker refuses `exec` under Docker's `no-new-privileges` option;
`doctor` then reports `"no_new_privileges": "process"`, because the sandbox
runner sets `no_new_privs` itself before any untrusted code runs.

Run files are served by `/api/artifacts/<run>/<file>`, only when the run's record
lists them, with inert headers: SVG and data files under a CSP sandbox, HTML only
with its own scripts in an opaque origin, framed by the interface alone.

## Experiments

Each run is saved under `.mathagent/experiments/<id>/`: `record.json` (code,
seed, parameters, image, limits, status, output, `RESULT`, files), `code.py` and
the files it produced. The oldest records are pruned beyond 200 runs or 256 MB.
Inside a script, `SEED`, `PARAMS`, `OUTPUT_DIR` (the working directory) and
`RESULT` (any JSON-friendly value) are available.

```bash
python -m mathagent.numerics list --workspace DIR
python -m mathagent.numerics show ID --workspace DIR
python -m mathagent.numerics rerun ID --workspace DIR   # same code, seed, parameters and limits
python -m mathagent.numerics clean                       # remove leftover sandbox containers
```

`tests/test_python_sandbox.py` checks isolation, limits and cleanup in a real
container, runs the skills' worked examples, and drives both skills through the
interface (it skips the container tests without the image).
`scripts/test-numerics-live.py --host URL --model ID` is an opt-in check with a
real model.

## Limitations

- Docker is required; Apptainer and Podman are not supported backends.
- Each run is stateless: there is no persistent kernel.
- Whether a check is worthwhile is the model's judgment, guided by the skill; the
  harness enforces the budget, not the mathematics.
