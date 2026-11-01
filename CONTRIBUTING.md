# Contributing

Square Harness is an experimental mathematical research project. Focused fixes,
clear failure cases and documentation improvements are welcome. The current
priority is a dependable solve–verify–repair loop before adding more orchestration.

The [development guide](docs/llm-development.md) describes the architecture,
the rules every change follows and how to add a feature. It is written so that
coding agents can follow it, and it applies to everyone.

## Development

Use Python 3.10+ on Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[research]'
python -m unittest discover -s tests -v
```

Tests use scripted model servers and temporary workspaces; they do not need a
real model. The suite is deliberately small: add a test only when it drives real
entry points and would catch a genuine regression. For inference-dependent
changes, record the model/weights revision, server version, settings, exact
source revision and the manual check performed.

The visual interface's source lives in `frontend/` and is built into
`mathagent/gui/static`, which is committed so that installing needs no Node.js.
After changing it, run `npm ci`, `npm test` and `npm run build` in `frontend/`
(Node.js 22) and commit the rebuilt files with your source change.

## Useful contributions

- Explain the observed behaviour and the intended change. Discuss a large design
  change in an issue before implementing it.
- For mathematical failures, provide a shareable statement, candidate proof,
  relevant review and the first false or unjustified inference. Distinguish
  demonstrated errors from unresolved doubts.
- Test reproducible controller bugs without inference when possible. Preserve
  original candidates and do not turn operational errors into mathematical verdicts.
- Keep private manuscripts, credentials and unredacted research logs out of commits.

Maintainers decide what is merged. Public access allows proposed changes and
forks; it does not grant write access to this repository. Contributions use the
project's [Apache License 2.0](LICENSE).
