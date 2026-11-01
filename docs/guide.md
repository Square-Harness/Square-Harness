# Square Harness guide

What Square Harness is for, the ideas behind it, and every skill: what it does,
how to call it and how it works inside.

- To install it, see [llm-install.md](llm-install.md). It is written for a coding
  agent, so you can ask one to do the installation (see [Install](#install)).
- To change the code, see [llm-development.md](llm-development.md).

## What it is

Square Harness is open-source software that turns an open-weights language
model into a research assistant for mathematics. The model runs on your
computer or on a GPU in your lab. The assistant proves statements and checks the
proofs, finds and verifies references, builds reading lists, referees
manuscripts, explains results, writes up notes in LaTeX and runs numerical
experiments. You use it in a web app (on a computer or a phone) or in the
terminal. It is experimental (version 0.5.1).

## Philosophy

**AI on our own terms.** Open-weights models are now good enough to help, and one
GPU can run them. A raw model is not yet a tool: it answers once and never checks
itself. The missing piece is the software around the model, the *harness*:

> open model + harness + a GPU in the lab = AI help for research that mathematicians control.

This independence matters in three ways:

- **Price:** no subscription per researcher.
- **Property:** unpublished ideas and proofs stay on your machines.
- **Dependence:** no provider can change access or terms overnight.

**The harness decides, the model works.** The harness is ordinary code. It
decides what the model reads, what it writes, when it is called again and what
is kept (the model is the engine, the harness is the car). Each model call has
one focused job, a bounded budget and a fixed answer format. Before relying on an
answer, the code checks it: that a quoted line exists, that a citation was really
read, that the answer was not cut off. This is how a 27B model can do work it
could not do in a single answer.

**Honest results.** A model's approval is not a proof, so every result says how
far it was checked:

- a proof is reported as reviewed with no issue found, never as correct;
- a reference is *read*, *cited*, *located*, *contradicted* or *not found*;
- a partial report names what it did not cover.

When the software fails (a timeout, an exhausted budget, malformed output), the
result says so. It never turns that failure into a mathematical verdict.

**Nothing is lost.** Every attempt, review, model request and source passage is
saved in your folder. You can pause work and resume it later with the budget it
had left, and you can always see exactly what the model saw and wrote.

**Bounded work.** Every job has a budget of attempts, working time and retrieval
requests. You pick an effort level, from Low to Poincaré, instead of tuning token
counts. Nothing runs without limit by accident.

**Text is data, never instructions.** Manuscripts, papers, web pages and model
answers cannot change what the harness is allowed to do:

- writing a file needs your approval;
- online search sends short public queries, never your manuscript;
- generated Python runs in a locked-down sandbox. Unsandboxed Python exists
  only as an opt-in launch option (`--allow-python`), with approval for each call.

**Simple for mathematicians.** A conversation is the entry point. Mathematics is
typeset with LaTeX everywhere, and one menu sets the effort. Square Harness is a
practical tool for daily research. It will not solve Navier–Stokes overnight.

## Install

Open a coding agent (Claude Code, Codex, …) in a terminal and ask:

```text
Install Square Harness following https://github.com/Square-Harness/Square-Harness/blob/main/docs/llm-install.md
```

The guide has two plans:

- **Local:** a small Qwen for a quick test, or a larger one on a gaming GPU or an
  Apple Silicon Mac, served by Ollama. On Linux with an NVIDIA GPU of 24 GB or
  more, HyperQwen can serve Qwen 3.8 27B instead.
- **Cluster:** HyperQwen, the setup the team uses, on a GPU of a computing
  cluster reached through SSH.

HyperQwen serves Qwen 3.8 27B with 4-bit weights, so it fits a 24 GB GPU, and
with speculative decoding, which makes answers faster.

By hand, with [Ollama](https://ollama.com) running:

```bash
git clone https://github.com/Square-Harness/Square-Harness.git && cd Square-Harness
python3 -m venv .venv && .venv/bin/python -m pip install -e '.[research]'
ollama pull qwen3.8:27b          # 18 GB; for a quick test: qwen3.5:2b (see llm-install.md for settings)
mkdir -p ~/research/my-paper
.venv/bin/square-harness --gui --workspace ~/research/my-paper
```

## Using it

### The web app

`square-harness --gui --workspace <folder>` serves the interface at
`http://127.0.0.1:8765` and opens your browser. The terminal prints an `Open:`
address that carries the access token.

- **Conversations** are the entry point (the sidebar title is "Square Harness").
  Ask in plain words. The Assistant answers directly, or starts focused jobs
  (a proof, a literature check, a review, …), reads their results and continues.
  Each job has a card in the conversation and a page of its own showing
  everything it did.
- **Call a skill yourself** by typing `@` in the text box: `@prove`, `@check`,
  `@literature`, `@quick_review`, `@referee`, `@detailed_review`, `@explain`,
  `@writeup`, `@summarize`, `@critic`, `@explore`, `@python_lab`, `@sanity_check`.
  Hover over the chip above the text box to read what the skill does and to
  choose its effort. **Auto** means Medium, or High for a detailed review.
- **Your folder** is shown above the text box. Click it to open another folder
  (below `--gui-root`; by default only the workspace itself). Drop files onto the
  window to add them to the folder (`.tex .sty .cls .bib .md .txt .pdf .py`, and
  `.png .jpg` figures for write-ups; up to 20 MB). An existing file is never
  replaced: the new one is renamed. Dropped files are attached to your next
  message.
- **Learn my writing style**, below New conversation, teaches write-ups how you
  write (see [Writing styles](#writing-styles)).
- **Job pages** show live output, every attempt and review, sources, budgets and
  saved files. **Pause** saves at the next checkpoint and **Resume** continues with
  the remaining budget. "Back to conversation" returns to the chat that started
  the job. The standalone forms (New proof, New reading list, New review, New
  write-up) start a job without a conversation.
- **The sidebar footer** shows:
  - the model and its status, the theme, and phone pairing;
  - which files the model may open by itself in this folder ("Reads all file
    types", untick a type to hide it);
  - **Parallel work**: how many conversations or jobs, and how many model
    requests, run at once (1–8 each; 2 and 2 by default). Extra work waits in a
    queue.
- **On a phone**: launch with `--gui-host 0.0.0.0` and open the printed address
  once; the phone stays paired. This is plain HTTP on your network: use it only
  on a trusted network, or through an SSH tunnel or a VPN.

### The terminal

`square-harness --workspace <folder>` opens a prompt. Proof is the default: type
a statement, or `/prove <statement>`. Other commands:

| Command | Purpose |
| --- | --- |
| `/critic`, `/explore` | Switch to critique or exploration chat |
| `/check <result>` | Find or verify a reference (needs `--online`) |
| `/literature <topic>` | Build a verified reading list |
| `/quick_review`, `/referee`, `/detailed_review`, `/explain` | The four review kinds (the manuscript comes from `--research-file`) |
| `/writeup <instructions>` | LaTeX write-up of `--research-file` notes in the `--template-file` style |
| `/python-lab`, `/sanity-check` | Python in the sandbox |
| `/review` | Fresh-context critique of the last answer |
| `/proofs`, `/proof-report <id>`, `/resume <id>` | List, read and resume proofs (reading needs no model) |
| `/researches`, `/research-report <id>`, `/research-resume <id>` | The same for reports and other jobs |
| `/experiments`, `/skills`, `/status`, `/help` | Saved experiments, skill files, settings, help |

For scripts, run one job and exit:

```bash
square-harness --workspace ~/research/my-paper --mode detailed_review \
  --research-file paper.tex --prompt "Check the proofs too." --output report.md
```

The exit status is 0 when the job completed, 1 when it was unsuccessful and 130
when it was paused or interrupted. Jobs started in the terminal appear in the web
app and the other way round: both use the same saved files.

## The skills

| Skill | You get | Web app | Terminal |
| --- | --- | --- | --- |
| Conversation | Answers, with focused jobs when needed | type a message | web app only |
| Prove | A proof, its review and every attempt | `@prove`, New proof | `/prove` (default) |
| Critique, Explore | Objections; approaches with heuristics labelled | `@critic`, `@explore` | `/critic`, `/explore` |
| Literature check | 1–2 references, each with its evidence level | `@check` | `/check` |
| Reading list | A themed, verified reading list | `@literature`, New reading list | `/literature` |
| Quick review | Is this proof correct? Verdict and located issues | `@quick_review` | `/quick_review` |
| Review | Referee report: overview, typos, presentation | `@referee`, New review | `/referee` |
| Detailed review | The review, plus checks of the proofs | `@detailed_review` | `/detailed_review` |
| Explain | A result and its proof explained step by step | `@explain` | `/explain` |
| Write-up | Your notes or results as LaTeX in your own style | `@writeup`, `@summarize`, New write-up | `/writeup` |
| Python Lab | Your computations and plots, run in a sandbox | `@python_lab` | `/python-lab` |
| Sanity check | An attempt to falsify a claim numerically | `@sanity_check`, automatic | `/sanity-check` |

### Conversation (the Assistant)

Use it for anything: a question, a doubt about a step, a request mixing several
tasks. It exists in the web app only; in the terminal, `/critic` and `/explore`
are ordinary chats without workers. The Assistant answers short questions itself. Otherwise it gives a precise
objective to a worker: a proof, a critique, an exploration, a reference check, a
reading list, a review, an explanation or a write-up. It then reads the result,
its status and what remains open, and decides what to do next. Two independent
workers can run at the same time on a vLLM server (`--assistant-concurrency`,
1–4). All workers share the conversation's budget, and a worker cannot start
other workers. Only you can start Python Lab and an explicit sanity check: the
Assistant cannot. It can open a web address you give it and read the page or its
bibliography.

### Prove

Use it for a statement you want proved, with all its hypotheses. Pin the files
it depends on: proof jobs read nothing by themselves.

How it works: **solve → verify → repair**.

1. The solver makes one full attempt, thinking first, with no tools.
2. A fresh verifier, which has not seen the solver's reasoning, reads the whole
   written proof. It returns a verdict and objections, each located in the proof.
3. If it finds no issue, that proof is returned unchanged. Concrete objections go
   back to the solver, which repairs the proof or argues the objection is
   mistaken. The new version is reviewed again.
4. An uncertain review stops the job and keeps the answer.

Every version is kept, and the first answer is never lost. Effort sets the number
of attempts and the working time.

### Critique and Explore

Ordinary mathematical chat with read access to your folder (`.tex`, `.bib`, PDF
text, …). **Critique** looks for unjustified steps and counterexamples. **Explore**
proposes approaches and labels heuristics as heuristics. Both may run a quick
numerical sanity check (see below). To write a file, the model must ask, and you
see the change before approving it.

### Literature check

Use it for "a reference for the tubular neighbourhood theorem", or "is Theorem
9.26 of Brezis about this?".

How it works:

1. The model recalls up to three candidate books or papers.
2. The code checks that they exist in bibliographic indexes (zbMATH, Crossref,
   arXiv). If none fits, it searches by topic.
3. For a precise request (an exact theorem, page or edition, or checking a given
   citation), the code opens open-access papers that cite the work and reads how
   they cite it.

Each reference gets an evidence level:

- **read**: the statement was read in the work itself;
- **cited**: another paper was read that cites the work at that place for that
  result;
- **located**: the work exists, but the exact place is unconfirmed;
- **contradicted**: a source attributes that place to another statement;
- **not found**: no evidence.

The code lowers any level that the passages read do not support. A theorem
number the model remembers stays labelled as a guess. Needs online search.

### Reading list

Use it to get oriented in a topic. The output is an orientation, 1–4 entry points
(surveys, textbooks, lecture notes), 2–5 themes with a core and an optional "go
deeper" part, a reading order and the gaps.

The code searches Crossref, arXiv, zbMATH and Semantic Scholar and follows
citations: what the key works cite, what cites them, what the pool keeps
citing. The model only screens, organises and annotates the works that were
found, and refers to them by number. It cannot invent a reference: works it
remembers are kept only if a source returns them.

### Quick review, Review, Detailed review, Explain

The four review kinds work on a proof you paste, or on a manuscript you attach
(TeX gives the best results: formulas extracted from a PDF can be garbled).

- **Quick review**: is this proof correct? Independent verifier passes read it
  through different lenses: line by line, adversarially, the results it uses, its
  overall structure. Effort sets the number of passes, from 1 to 5. From Medium
  up, each alleged error is re-checked by a fresh call. The verdict is *no issue
  found*, *issues found* or *uncertain*. Each issue quotes the exact proof lines
  and comes with a suggested repair. The proof itself is never rewritten.
- **Review**: a referee report for understanding and presentation. The whole paper
  is read part by part. The report has an overview (question, main results,
  strategy, how the sections fit), notes on each part, typos and presentation
  problems with corrections, an assessment, a recommendation and a confidential
  note to the editor. The proofs are not checked.
- **Detailed review**: the review, then checks of the proofs, main results first.
  The code first maps the manuscript: sections, numbered statements, their proofs,
  and what each proof uses. Each check sees one proof with only what it needs, so
  a moderate context suffices. The numbered comments are assembled by the code
  from checked findings: the prose cannot add or drop an objection. If a main
  result's proof was not checked, the recommendation says it is provisional. It
  has twice the working time of a Review.
- **Explain**: one result and its proof, for a mathematician from another field:
  what it says, the idea, a walk through the proof (every line of a short proof),
  the standard facts used, and where each hypothesis matters. An accuracy pass
  compares the explanation with the source.

### Write-up

Turns rough notes, a draft, a PDF or the results of a conversation into a LaTeX
document in your own style. It is exposition, not research: every statement,
hypothesis and constant comes from your material, and nothing reviews the
mathematics, so write up results you have checked.

Use it for:

- a clean note from your notes or from a proof the Assistant found:
  `@writeup a short note with Lemma 2 and the reviewed proof, for my students`;
- a summary of a conversation: `@summarize` (or `@writeup` with nothing after it
  and no attachment) gives each
  result's statement, its recorded status, the proof idea, the references found
  and the open questions;
- a missing part of a paper: attach its sections (without `\begin{document}`),
  your `.sty` and your `.bib`, and say what to write.

What it uses:

- **Material**: the conversation so far (messages, answers and the full results
  of finished jobs, including those of the current turn) and the files you
  attach. A complete `.tex` file (with `\begin{document}`) is used as a
  **template**: its preamble and layout, not its text, and the reply says so. To
  write from a draft, attach it without `\begin{document}`, or as `.md` or `.pdf`.
  `.sty`, `.cls` and `.bib` files are templates too.
- **Notation**: `\newcommand` definitions you type in the request win; then your
  template's macros and theorem environments; then those of your saved style.
- **References**: with a `.bib` attached, only its keys are cited, and any other
  work becomes a `% TODO: reference` comment. Without one, the document cites only
  works the material gives with a DOI, arXiv or zbMATH identifier, and their
  bibliography is made inside the `.tex`. A work the literature search could not
  confirm is never cited.
- **Figures** the text includes are taken from the folders of the attached files
  or from your folder (`.pdf`, `.png`, `.jpg`). A missing figure is drawn as a
  placeholder.

How it works:

1. One writing call reads all the material, your request, your notation and your
   style, and writes the document. Long material is written in consecutive parts.
2. Equations the text uses again are numbered and cited with `\eqref`; displays
   nothing refers to stay unnumbered. Labels from your own material are kept.
3. The document is compiled with `latexmk` when it is installed, in a temporary
   folder, without shell escape and with restricted file access. If it does not
   compile, one repair call fixes the reported errors.
4. The document opens with **Points to check**: its `% TODO` comments, citations
   and figures it could not resolve, and references to undefined labels. Delete
   that block once they are settled.

From a conversation, the card offers **Download .tex** (and **Download PDF** when
it compiled); the copy is deleted with the conversation. From the New write-up
form or the terminal, the `.tex` and the PDF are written in your folder, and an
existing file is never replaced. In the web app, a template can be saved under a
name and reused in any folder.

### Writing styles

**Learn my writing style** condenses the `.tex` sources of your earlier articles
into a named style: the macros you actually use, your theorem environments and,
for each kind of paper you write, how you announce, explain and phrase things.
Choose main article files, ideally two or more per kind of paper; `\input` parts
are not followed, so pin them as well. A style can also keep a LaTeX template (an
article's preamble, `.sty`, `.cls` or `.bib` files), which write-ups in that
style build on unless you attach one.

On the same page, **Style used by write-ups** chooses: automatically (a short
call picks the style that fits the material, or none), none, one style, or one
of its domains. A style gives phrasing and macros, never the structure of your
papers: the request sets the structure. Styles and this choice are kept in the
`.mathagent/` folder of `--gui-root` and shared by every folder.

### Python Lab and Sanity check

Both run Python in one local Docker sandbox: a fresh container for each run, with
no network, no access to your files and strict limits. Each run is saved
(code, seed, results, figures) and can be re-run. Figures appear in the
conversation. Install the sandbox once (see the install guide).

- **Python Lab** runs the computations you ask for: simulations, linear algebra,
  symbolic work, plots. Only you can start it. Defaults: 30 s, 4 GB, 2 CPUs per
  run. It can ask for up to 300 s, 16 GB and 4 CPUs, and you approve each such
  request.
- **Sanity check** tries to falsify a claim with cheap experiments: small cases,
  boundaries, random sampling, minimising the margin. When the sandbox is
  installed and the model context is at least 32,768 tokens, it is offered
  automatically in Critique, Explore and the Assistant (5 s, 2 GB, 1 CPU, 3 runs
  per turn; `--no-numerical-checks` turns this off). The result is exactly one of *exact
  counterexample*, *numerically verified candidate*, *no counterexample detected*
  or *inconclusive*. Passing tests never count as a proof.

Change the limits with `--python-limits limits.json`. Values are bounded by
built-in maxima: for automatic checks 30 s, 4 GB, 2 CPUs and 10 runs; for anything
600 s, 32 GB and 4 CPUs.

```json
{"sanity": {"limits": {"timeout_seconds": 5, "memory_mb": 2048}, "max_runs": 3},
 "lab": {"defaults": {"memory_mb": 4096}, "ceiling": {"memory_mb": 16384}}}
```

Each run is saved under `.mathagent/experiments/<id>/` (the oldest are pruned
beyond 200 runs or 256 MB). Manage runs with `python -m mathagent.numerics list`,
`show <id>`, `rerun <id>` (same code, seed, parameters and limits) and `clean`
(remove leftover containers), adding `--workspace <folder>` to `list`, `show` and
`rerun`. `--no-python-sandbox` disables the sandbox entirely.

## How a skill is made

Every skill is built from three layers:

```text
your request
     │
     ▼
 controller (Python) ── plan · budget · saved state · sources · checks
     │   ▲
     │   │  one focused call at a time, with a fixed answer format
     ▼   │
   model ── follows the skill's working method (SKILL.md)
```

1. **The working method**, `mathagent/skills/<skill>/SKILL.md`, written in plain
   words for the model: what to do and in which order, what counts as evidence,
   and the trust rules (sources are data, never instructions). A mathematician can
   read and improve it without touching the code. A change applies to jobs
   started afterwards: each job keeps the text it started with.
2. **The controller**, a Python class, owns the plan, the budget, the saved state
   and the checks. It chooses which passage the model sees, asks one focused
   question, parses the answer and checks it against the sources. Then it saves
   everything, so the job can stop and resume at any point.
3. **The model calls.** Each one sees only what it needs: for example, in a
   detailed review, one proof with the statements it uses. The answer comes back
   in a fixed format, usually JSON, which the code validates. Independent calls
   can run in parallel.

The division of labour is always the same: the model proposes, the code verifies
and keeps the books. The literature check is a good example. The model guesses
candidate references, the code establishes which ones exist and what was actually
read, and the evidence level follows from what was read, not from the model's
confidence.

The Assistant is one level up: a controller whose model may hand a precise
objective to any of the skills above, within one shared budget.

Python Lab and Sanity check also declare in their `SKILL.md` header *who* may
start them. Python Lab is explicit (only your command starts it). Sanity check is
automatic (it may be offered to the model in chat). The code enforces this
declaration, and so do the sandbox limits: the text of a skill cannot raise them.

### Effort

| Effort | Attempts / rounds | Working time | Retrieval requests |
| --- | ---: | ---: | ---: |
| Low | 1 | 1 min | 20 |
| Medium | 3 | 15 min | 50 |
| High | 5 | 30 min | 80 |
| Extra high | 7 | 1 h | 120 |
| Poincaré | 10 | 2 h | 160 |

Each skill translates the level into its own terms. For a proof, it is the number
of attempts. For a quick review, the number of verifier passes. For a detailed
review, how far down the dependencies the checks go (Low: the main results only;
Extra high and Poincaré: the appendices too), with twice the working time. There
is no fixed token allowance, but every model call is still limited by the model's
context. A paused job resumes with what is left; it never gets a new allowance
silently.

## Your data

Everything is saved in your folder under `.mathagent/`:

| Folder | Contents |
| --- | --- |
| `proofs/` | Proof jobs |
| `research/` | Reports, reviews, reading lists, write-ups (those made in a conversation are deleted with it) |
| `checks/` | Literature checks |
| `chats/` | Conversations |
| `experiments/` | Sandbox runs |
| `literature/` | Cache of downloaded sources |

Each folder keeps its file-access choices in its own
`.mathagent/gui-settings.json`. The web app's access token is in the
`.mathagent/` folder of `--gui-root` (the workspace by default). Git ignores
`.mathagent/`, but a backup or a copied folder still contains your unpublished
work.

What leaves your computer:

- **Search requests**: short public queries to arXiv, Semantic Scholar, zbMATH,
  Crossref and OpenCitations; downloads of open-access papers; web pages you ask
  the Assistant to open; and, only with a `BRAVE_SEARCH_API_KEY`, general web
  search. In the web app this is on unless you launch with `--offline`; the
  reading-list and review form can also turn it off for one job. In the terminal
  it is off unless you launch with `--online`. Proofs and write-ups never search.
- **Your prompts and files**, only if you choose a model server on another
  machine with `--online`. A model on a cluster reached through an SSH tunnel
  counts as local.

Nothing else leaves your computer. The web app listens on `127.0.0.1` only by
default. Each request needs the token, and the interface loads nothing from the
internet (fonts and KaTeX are bundled). Delete `.mathagent/gui-token` to unpair
all browsers and phones.

## Does it help?

A first benchmark, presented in a lab talk in October 2026. Qwen 3.8 27B, served
by HyperQwen, ran on one NVIDIA L40 (48 GB). The test set was 22 lemmas from arXiv papers one week old,
so they cannot be in the model's training data. The same model on the same GPU
proved **3 of 22** answering directly and **11 of 22** inside Square Harness
(14% → 50%). This is one run on a small set. The protocol for reproducible runs is
in [llm-development.md](llm-development.md#benchmarks).

## Limits

- The solver and the verifier are the same model in separate contexts, so they
  can make the same mistake. Check important proofs yourself.
- Not finding a reference does not establish novelty.
- Formulas extracted from PDFs can be garbled: give TeX sources when you can.
  Reviews do not follow `\input`/`\include`; such files are reported as unread.
- Linux and macOS only; native Windows is not supported.
