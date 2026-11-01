# Changelog

## Unreleased

- Restore the sidebar notebook selector and standalone job forms, saved-job
  access, effort controls and write-up templates.
- Add the MLX OpenAI-compatible profile and local protocol smoke test, improve
  bounded reference retrieval and recovery from malformed tool calls.
- Harden Assistant delegation, source selection, partial reports, interrupted
  proof resumes and model response parsing, with regression coverage.
- Add a Docker Python sandbox with two skills: `python-lab` runs requested
  computations and plots (`/python-lab`, `@python_lab`); `numerical-sanity-check`
  lets chats and the Assistant try to falsify claims with small bounded
  experiments (`/sanity-check`, `@sanity_check`; `--no-numerical-checks`).
  Generated figures and files appear in the conversation. See
  [docs/python-numerics.md](docs/python-numerics.md).

- The style page sets which saved style write-ups use: automatic (default), none,
  a style (its best-fitting domain, never none), or one domain of a style
  (`POST /api/styles/choice`, kept in `.mathagent/style-choice.json`). A chosen
  style is used without a style call when it leaves one domain; a style that is no
  longer saved falls back to the automatic choice with a warning.
- Writing styles can carry a LaTeX template (a preamble `.tex`, such as one of the
  author's articles, and/or `.sty`, `.cls`, `.bib` files), chosen when learning a
  style or set and removed later on the Style page (`POST /api/styles/template`).
  It is stored inside the style file. A write-up that picks the style builds on
  it unless given its own template; the template's `\title` is replaced by the
  document's. A template `.tex` without `\begin{document}` now joins the preamble
  as written (before, only its macros reached the prompt).
- Write-ups are rebuilt around one writing call: all the material, the request,
  the user's notation and a short style digest in one prompt, with reasoning on
  (Qwen's sampling, temperature 0.6, top-p 0.95, at most 8,192 reasoning tokens of
  24,576) and the write-up skill as the only system text. The outline call,
  section-by-section writing, `% src` provenance marks, the notation pass and
  per-section repairs are gone; material too long for one call is written in
  consecutive parts. The controller builds the preamble (user's notation first,
  braced multi-token macros, the model's own definitions never over the user's),
  supplies undefined commands and environments, and makes one repair call on the
  whole document if it does not compile. A saved style is chosen by a short call
  of its own and contributes explanation habits, phrasings and macros, not the
  structure of the author's papers. On the replayed test proposition: two calls
  instead of nine, compiled at the first attempt, the user's notation throughout.
- A mode typed out in full (`@writeup ` followed by a space, or a message that is
  only `@summarize`) is the call, as if picked from the menu; clearing its chip
  keeps it as text. A write-up or summary ends the Assistant's turn even when the
  Assistant chose it: its ready message is the reply, with no critique or second
  version, and the Assistant is told to pass the user's own words as its request.
- Write-ups follow the user's notation: `\newcommand` definitions in the request
  are required notation, join the preamble and outrank template and style macros
  (a style macro with the same name or meaning is dropped). Multi-token macros are
  defined braced so scripts and arguments keep their meaning. Commands the sources
  use without defining them are supplied at compile time from saved styles or
  their names (`\bbP`, `\rmd`, `\cL`) and reported. Write-up calls no longer
  mention tools, and an answer that only announces what it would do is retried,
  never kept as the document or a repair.
- Write-ups and summaries no longer get a model review: they are for results the
  user has checked.
- `@writeup` and `@summarize` deliver by download: the conversation card offers
  the `.tex` (and the PDF when it compiled), nothing is written into the folder,
  and the kept copy is deleted with its conversation. The reply is a one-line
  ready message from the controller, not a second model pass.
- Progress lines such as "Delegating a mathematical task…" disappear when the next
  output starts and are no longer saved in the conversation.
- `@writeup` and `@summarize` in Assistant conversations. A write-up's material
  is the conversation so far (messages, answers and the full results of finished
  proof, review and literature jobs, as `conversation.md`) plus any attached
  draft; the instructions after `@writeup` set its goal and scope.
  `@summarize`, or a bare `@writeup`, writes `summary-<title>.tex` with each
  result's statement, recorded status, proof idea, references and open
  questions. A short call picks a fitting saved writing style, whose digest
  reaches the writing call and whose used macros join the preamble; otherwise
  the usual style of the material's field. Write-up workers get the conversation
  once, as this source, instead of the raw history as reference context.
- Write-ups also: say when an attached complete `.tex` was used as a template
  (its preamble), and how to write from it instead; count the results finished
  earlier in the same Assistant turn as material; write up the attached files
  when `@writeup` is sent alone with attachments; move multi-line definitions
  whole; and resume jobs saved by the earlier section-by-section pipeline by
  writing them again.
- Write-up bibliographies: with a `.bib` attached, the text cites only its keys
  (another work becomes `% TODO: reference: …`, and BibTeX the model writes is
  removed); with none, the model gives entries only for works the material gives
  with a DOI, arXiv or zbMATH identifier, an entry is kept only when that
  identifier is in the material, and the kept ones are embedded in the `.tex`
  (`filecontents*`) with `\bibliography` (or `\printbibliography` with biblatex),
  so a downloaded file compiles on its own.
- Write-ups number and reference their equations: displays the text uses again
  and key identities get `equation`/`align` with `\label{eq:…}` and are cited with
  `\eqref`, never "the equation above"; the controller then keeps a number only
  where the text refers to it (labels and displays from the author's material are
  never touched), turns `(\ref{x})` into `\eqref{x}`, and lists references to
  undefined labels, duplicate labels and positional references among the points to
  check. `cleveref` is used when the author's preamble loads it.
- Write-up fixes found by live tests on Qwen 3.8 27B: figures the document
  includes are copied from the pinned files' folders (and `.png`/`.jpg` can be
  uploaded); a figure that is missing becomes a framed placeholder instead of a
  fatal error. A `\renewcommand` or a definition inside an environment stays
  where it is, and numbers, lengths and environment shortcuts are no longer
  braced (`\renewcommand{\arraystretch}{2.2}` broke tables). The document starts
  with the points left to check: its `% TODO` comments, citations that are not in
  the author's `.bib`, and missing figures (comments alone do not show in the
  PDF). The writing call sees the author's bibliography keys and may cite only
  them or works the material gives with an identifier. The request's notation
  now really outranks a template's (`\renewcommand` after the template). A
  template without theorems no longer gets `\newtheorem{theorem}[theorem]`, a
  file the template `\input`s is not pasted twice, `\begin{enumerate}[(i)]`
  loads `enumitem`, a writing call that stops early is retried once, and a
  document written within the time limit is always compiled and offered (the
  repair call is skipped when time is out).
- Writing styles: a Style page in the interface (`#/style`, "Learn my writing
  style" in the sidebar) condenses the .tex sources of the author's earlier
  articles into one small JSON file per name, shared by every folder like saved
  templates. The controller keeps the macros the articles actually use and their
  theorem environments; the model sorts the articles by domain and describes, per
  domain, the papers' aim, usual structure, explanation habits and phrasings,
  which are kept only when found verbatim. `style_prompt` renders one domain in
  about 2,000 characters for a later write-up. The page asks for example articles
  when the folder has none.
- Review has four kinds, started with `@quick_review`, `@referee`, `@detailed_review`
  and `@explain` in the Assistant: a quick check of a proof (independent
  verifier passes through different lenses, each alleged error re-checked, a
  verdict with located issues and suggested repairs; the proof is never
  rewritten); a review of a manuscript (the whole paper read part by part for an
  overview and its typos and presentation problems, without proof checks); a
  detailed review that then checks the proofs one at a time, main results first,
  from a map of the manuscript's statements, proofs and dependencies, at twice
  the working time; and a precise explanation of a result and its proof. Independent
  calls run concurrently on vLLM. The Assistant and the terminal start them as
  `quick_review`, `referee`, `detailed_review` and `explain`.
- An independent research review that only announces what it will check no
  longer counts as a review: the report stays `partial`.

- Literature check: one or two precise references for a known result, each
  graded (read, cited, located, contradicted, not found) from the passages
  actually read, with papers that cite the work found through Crossref, the
  OpenCitations index and arXiv. The Assistant delegates it as a `check` worker;
  critic and explore answers can call `check_reference`; `/check` in the terminal.
- Literature reports are rebuilt as verified, themed reading lists: code sweeps
  Crossref, arXiv, zbMATH and Semantic Scholar and follows citations, and the
  model scopes, screens, organises and annotates only records a source returned.

- Saved conversations can be deleted from the Assistant sidebar after
  confirmation. Active and queued work is protected, other open clients update
  immediately, and saved proof/report jobs and attached files are retained.

- Visual interface (`square-harness --gui`): a local web app for conversations,
  proofs, reading lists, manuscript reviews and write-ups, with typeset LaTeX, live model output,
  proof candidates with their whole-proof reviews, citation checks, pausing and
  approval dialogs. The same code serves desktop and phone browsers; it listens
  on loopback by default and every request needs the workspace's access token.
- Proof pages follow the v0.5 engine: each attempt shows its written answer and
  the verifier's verdict and located issues; the selected answer and its review
  status stay visible. Legacy v1 proof jobs are listed and readable, not resumable.
- Critic and explore conversations started in the interface are saved in
  `.mathagent/chats/`.
- Assistant replaces Default: a main mathematical agent answers elementary
  questions directly or delegates precise tasks to the existing modes, then
  inspects their results and follows up. Independent workers can run concurrently
  on vLLM (two by default, configurable from one to four), within one shared
  token/time budget. Paused work retains child identities, results and accounting.
  Standalone proof mode and its direct-inference benchmark are unchanged.
- Assistant answers after delegated work have room for a full response. Resuming
  a capped answer reuses the saved results with a larger, context-fitted output
  ceiling, without launching the completed proof again.
- Assistant can read supplied public web pages and their links or paginated
  bibliographies directly, without starting a literature report. Entries without
  hyperlinks are preserved, and extraction coverage is explicit.
- Delegated research, review and write-up objectives are separate from their
  exact saved conversation context. Long earlier reports no longer count toward
  the objective length limit. Typed permanent failures persist across resume
  and prevent repeated worker launches with paraphrased requests or more effort.
- Write-up workflow (`--mode writeup`, `/writeup`, interface tab): LaTeX from
  notes, drafts or PDFs in the user's `.sty`/`.cls`/`.tex` template, with source
  comments checked against read lines, a sandboxed latexmk compile, one repair
  round and a fresh review. Output files never replace existing ones.
- The interface can open other folders below `--gui-root`, choose per folder which
  file types the model may read, and save dropped files into the workspace.
- `read_file` reads locally extracted PDF text; `.sty` and `.cls` are text files.
- Effort levels (Low to Poincaré) replace budget fields in the interface; online
  search is a per-conversation and per-report switch, locked off by `--offline`.
- The interface uses the model server chosen with `--backend` (Ollama, vLLM or
  llama.cpp), the sampling options and the `--proof-*` limits; pausing works
  with every backend.
- Interface notebooks are now Assistant, Prove, Literature, Review and Write-up.
  Critic and explore are no longer notebooks; Assistant can delegate to them, and
  earlier critique and exploration conversations stay listed and readable there.
  Referee reports are called reviews in the interface (the engine mode is still
  `referee`).
- Effort levels set depth and working time, from Low to Poincaré. New jobs have
  no cumulative token ceiling unless explicitly configured; advanced limits
  remain available in the standalone forms.
- Any job can be started while the model is busy: proof, report and write-up
  forms, suggestions and resume buttons add it to the queue instead of refusing.
- Online search is on by default in the interface for new conversations and
  reports; untick it per conversation or report. An explicit `--offline` launch
  still locks it off. Write-ups never search.
- The job list and a job page's overview (budget, candidates, checks) can slide
  away to a thin spine on wide screens; the choice is remembered per browser.
- The sidebar footer links the licence and bundled third-party notices.

## 0.5.1 — 2026-09-27

Reliability fixes for the solve → verify → repair engine; no new model roles and
no extra reviews. One review still decides, and uncertainty still stops the job.

- Repairs and continuations fit their output allowance to the context (floor:
  `--proof-min-solve-tokens`), so they no longer fail when the input would not fit
  beside a full 32k allowance, including on the conservative byte estimate used
  without an exact tokenizer. The initial solve is unchanged and still identical
  to direct inference.
- Continuations keep the written text and a marked final excerpt of the saved
  notes; when nothing useful fits, the original problem is solved afresh.
- The verifier samples at the solver temperature by default
  (`--proof-verify-temperature`) instead of greedy decoding.
- Verifier responses are bounded (explanation 4,000 characters, at most five
  issues), so they fit the room left after thinking.
- The verifier receives the solver's task as quoted reference, not as its own
  instruction.
- Default request timeout 1,800 s; another attempt starts only if the measured
  first cycle fits in the remaining time. CLI notice for unpinned files.
- Separate repair allowance (`--proof-repair-tokens`). The HyperQwen A10 profile now
  uses 80,000 tokens per job: a 32,768-token solve and 16,384-token review, then at
  most one repair of 14,000 tokens plus its review. `run-hyperqwen-benchmark.py --arms`.

## 0.5.0 — 2026-09-27

A simpler proof engine: solve the complete problem, verify the written proof,
and repair a concrete objection. This release defines a new experimental
protocol; mathematical gains have not yet been established by a live benchmark.

- A substantial initial solve with thinking enabled; the benchmark matches its
  initial prompt and sampling settings to the direct-inference baseline.
- One whole-proof verifier with explicit verdicts, explanations and located
  issues. Positive explanation text no longer counts as an objection.
- Targeted repair or rebuttal, followed by another full review. Protocol failures
  remain distinct from mathematical objections.
- Immutable initial answers and retained revisions. Partial later work cannot
  silently replace a complete response in the exported answer.
- Deterministic recording, persistent usage accounting and bounded resume.
- Removal of cooperative and portfolio proof modes, model recorder/planner,
  checkpoint summarization and proof-mode tool loops.
- Separate solve and verification ceilings; default job allowance 120,000
  generated tokens over at most three attempts, with a 40,960-token context.
- Two benchmark arms, `raw-single` and `proof`, with explicit additional compute
  for verification and repair. Retained hardware profiles are separate conditions.
- Shorter README and manuals, updated citation metadata and `--version`.

Compatibility: old proof artifacts remain inspectable, but cannot resume in the
new engine. Removed strategy and worker options are not silently mapped to new
behaviour. Keep original code and settings when reproducing older experiments.

## 0.4.0 — initial public release candidate

Early local-first mathematical research workflows: manuscript critique,
exploration, saved proof attempts, literature/referee drafts, Apache-2.0 license,
citation metadata and automated tests.

Subsequent v0.4 development explored proof portfolios, cooperative advisors,
OpenAI-compatible and llama.cpp transports, GPU deployment profiles and saved
benchmarks. These experiments motivated the v0.5 simplification; their original
results and source snapshots should retain their original version identity.

Tests validate software behaviour, not mathematical correctness. Interfaces and
saved-state formats may evolve while the project remains experimental.
