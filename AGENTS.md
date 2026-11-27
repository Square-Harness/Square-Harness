# Instructions for coding agents

- **To install or start Square Harness**, follow
  [docs/llm-install.md](docs/llm-install.md) from the top. It says what to ask
  the user, how to choose between a local model and a cluster GPU, and how to
  check each step.
- **To change code, tests or documentation**, read
  [docs/llm-development.md](docs/llm-development.md) first: the rules (no push
  without approval), the invariants that protect saved work, and the checks to
  run before you finish.
- Keep the Python suite small. Add a test case only when a maintainer explicitly
  requests one. Verify routine fixes with the existing suite and a focused manual check.
- **To explain the harness to someone**, use [docs/guide.md](docs/guide.md).
- **Ignore `docs/old_docs_to_delete_after_approval/`.** It holds the previous,
  partly outdated documentation until the maintainers approve its deletion.
