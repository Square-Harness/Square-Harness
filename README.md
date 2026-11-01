# Square Harness

**An open-source harness for mathematical research with open-weights models.**

Square Harness turns a language model running on your computer, or on a GPU in
your lab, into a research assistant for mathematics. Its core is a simple proof
workflow, **solve → verify → repair when needed**. It keeps the original answer,
reviews the complete proof, and saves every candidate and objection for
inspection. Around it, the same principles serve literature checks, reading
lists, manuscript reviews, explanations, LaTeX write-ups and numerical
experiments.

**Version 0.5.1 · experimental.** A model's approval is not a mathematical
certificate; independent checking remains necessary.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Square-Harness/Square-Harness/main/docs/images/interface-dark.png">
  <img alt="The Square Harness web app with a conversation" src="https://raw.githubusercontent.com/Square-Harness/Square-Harness/main/docs/images/interface-light.png">
</picture>

## Quick start

Ask a coding agent (Claude Code, Codex, …) in a terminal:

```text
Install Square Harness following https://github.com/Square-Harness/Square-Harness/blob/main/docs/llm-install.md
```

It covers a local model (a tiny Qwen for a test, or a larger one on a gaming
GPU or an Apple Silicon Mac) and HyperQwen, the team's setup, on a cluster GPU
reached by SSH. By
hand, with Python 3.10+ on Linux or macOS and [Ollama](https://ollama.com)
running:

```bash
git clone https://github.com/Square-Harness/Square-Harness.git
cd Square-Harness
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[research]'
ollama pull qwen3.8:27b
mkdir -p ~/research/square-workspace
.venv/bin/square-harness --gui --workspace ~/research/square-workspace
```

Your browser opens on the web app. Without `--gui`, the same harness runs in the
terminal:

```text
/prove Prove that every bounded sequence in H¹(0,1) has a subsequence converging strongly in L²(0,1).
/proofs
/proof-report
```

`qwen3.8:27b` (18 GB) with the default 40,960-token context needs a large GPU or
Mac. For smaller machines and other model servers, see the
[install guide](docs/llm-install.md).

## What it does

- **Conversations**: ask in plain words. The Assistant answers, or starts
  focused jobs and reads their results before continuing.
- **Prove**: solve, verify and repair, with every attempt kept.
- **Literature check**: references with an evidence level (read, cited,
  located, contradicted, not found).
- **Reading lists**: built from verified records.
- **Reviews**: a quick check of a proof, a referee report, a detailed review that
  also checks the proofs, and step-by-step explanations.
- **Write-ups**: LaTeX in your own style (a template, or a style learnt from your
  articles), with numbered equations and checked references.
- **Python Lab and Sanity check**: run in a Docker sandbox.

Every job has a bounded budget, can be paused and resumed, and saves what the
model saw and wrote under `.mathagent/` in your folder. The web app runs locally,
also works on a phone, and shares its jobs with the terminal. The
[guide](docs/guide.md) explains each skill and how it is built.

## Documentation

- [Guide](docs/guide.md): philosophy, every skill, how skills are made, effort,
  data and privacy.
- [Install guide](docs/llm-install.md): local and cluster plans, written for a
  coding agent.
- [Development guide](docs/llm-development.md): architecture, rules, how to add a
  feature, benchmarks.
- [Changelog](CHANGELOG.md).

Ollama, llama.cpp, vLLM and MLX servers are supported. The solver and verifier
currently use the same model in separate contexts and can share errors. Tests
check software behaviour; they do not establish mathematical performance.

## Contributing and license

Created by **Vincent Boulard**, with **Louis Carillo**. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CITATION.cff](CITATION.cff). Maintainers
decide which contributions are merged.

[Apache License 2.0](LICENSE): free scientific and commercial reuse under its terms.
Model weights, dependencies and retrieved papers retain their own licenses.
See [NOTICE](NOTICE) for attribution.
