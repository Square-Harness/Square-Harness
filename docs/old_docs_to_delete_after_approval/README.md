# Old documentation, to delete after approval

On 1 November 2026 the documentation was condensed into three guides, plus a
pointer for coding agents:

- [guide.md](../guide.md), for people: the philosophy, every skill and how
  skills are made;
- [llm-install.md](../llm-install.md), for a coding agent: installation with a
  local model or a cluster GPU;
- [llm-development.md](../llm-development.md), for coding agents and
  developers: architecture, rules, and how to add features without breaking
  saved work;
- [AGENTS.md](../../AGENTS.md), which sends coding agents to the right guide.

The eight files in this folder are the previous documentation, moved here
unchanged. Don't follow them: parts are out of date, and their relative links
now point one folder too high. Vincent wrote most of them; jb-himbert wrote
`python-numerics.md`.

**Once the authors approve**, delete this folder and its line in `AGENTS.md`.
Git history keeps every file, for example `git show e52e1eb:docs/usage.md`.

## What each file was worth, and where it went

Flags, defaults, paths and versions were almost all correct. What had drifted
was the interface, reshaped in `eff6d27`. Some facts had never been written
down.

| File | State on 1 November 2026 | Useful content now in |
| --- | --- | --- |
| `usage.md` | Proof engine and budgets right. Interface out of date: notebook selector, version and licence line, folder in the sidebar, Effort picker (now the `@mode` menu with Auto), "Resume assistant", search switch per conversation. One notebook had three names (Assistant, ∀, Default). | `guide.md` (use), `llm-install.md` (servers, flags), `llm-development.md` (invariants) |
| `research.md` | Right, but lists 7 of the 9 skills (no Python Lab, no Sanity check). | `guide.md` (skills), `llm-development.md` (how a skill runs) |
| `python-numerics.md` | Right. `requirements.in` sent readers here for a lock-file regeneration command it never contained. | `guide.md` (limits, commands), `llm-install.md` (image build), `llm-development.md` (isolation rules) |
| `benchmark.md` | Consistent with the code. The September protocol on rented servers. | `llm-development.md`, Benchmarks |
| `hyperqwen-a10.md` | Consistent with the code. The 24 GB HyperQwen profile. | `llm-install.md` Plan B, `llm-development.md` (frozen profiles) |
| `a10.md`, `ovh.md` | Consistent with the code. Rented A10 and V100S servers, no longer used. | `llm-development.md` (frozen profiles) |
| `audit-2026-11-01.md` | Out of date: says the test reduction was not applied, that licence links are visible and that CI checks 7 skills. | Nothing (history only) |

## Other changes on this branch

- `README.md` is now a short landing page, and `CONTRIBUTING.md` points to the
  development guide.
- `CHANGELOG.md` has an entry for the new guides. Three wrong lines are fixed:
  the notebook selector "restored", a search switch per conversation, and
  licence links in the sidebar.
- Three strings that pointed to `python-numerics.md` now point to `guide.md`:
  the sandbox option group in `mathagent/cli.py`, the limits error in
  `mathagent/numerics/policy.py`, and the comment in
  `mathagent/numerics/docker/requirements.in`.

## Facts worth keeping

- **Benchmark, one run.** Qwen 3.8 27B served by HyperQwen on one NVIDIA L40
  (48 GB), 22 lemmas from arXiv papers one week old: 3/22 answering directly,
  11/22 with the harness (14% → 50%). The only source is the October 2026 lab
  talk. The run records and the server settings are not in the repository. No document mentioned the
  result before; `guide.md` and `llm-development.md` now report it.
- **The install guide was tested.** A fresh coding agent, given only "Install
  Square Harness following docs/llm-install.md", completed Plan A on CPU with
  `qwen3.5:2b`: a chat answer in 26 s, a short proof in 3.5 min, and the web app
  answering. The test showed that `--ctx 8192` no longer works with Ollama: the
  harness estimates the prompt from its bytes, and the estimate exceeds 8192
  tokens. The small-model settings therefore use 16384. Plan B (cluster) is
  generalised from the scripts the team runs on its cluster, and the local
  HyperQwen option follows HyperQwen's own Docker Compose setup; neither was
  run from the guide.
- **MLX check.** The 19 October report (LFM2.5-8B on an M1 Max: all 7 protocol
  checks passed, proof and repair worked) was removed by `9e22300`. Read it with
  `git show 94e5e76:docs/compatibility/local-mlx-lfm.md`.
- **Tests.** The suite went from 761 Python tests to 8 end-to-end and 4 sandbox
  tests, and from 55 frontend tests to 1. `llm-development.md` lists what is no
  longer covered.
- **Outside the repository.** The L40S (48 GB) HyperQwen profile, its Slurm
  scripts and its lock file exist only on the cluster. `llm-install.md` has
  generic templates instead.

## To decide before deleting

1. Approve the deletion of these eight files.
2. Reread `README.md` and `guide.md`, which speak for the authors, including
   the benchmark.
3. Retake the README screenshots: they show the old sidebar.
4. Decide where the interface links to the licence and third-party notices
   (served at `/LICENSE.txt` and `/THIRD-PARTY-NOTICES.txt`).
5. Done in the write-up merge: the new write-up flow (`@summarize`, styles,
   bibliographies, equation numbering) is in `guide.md` (Write-up, Writing
   styles) and `llm-development.md`; `research.md` here stays as it was.
6. Commit cleaned-up cluster scripts, or keep the templates in
   `llm-install.md`.
