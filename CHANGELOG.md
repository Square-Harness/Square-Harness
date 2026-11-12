# Changelog

## Unreleased

- Ask Proof's reviewer to challenge its weakest nonroutine premise and verify
  counterexample admissibility before checking the failed conclusion. Replace
  two model-written status labels with one assessment and derive the operational
  verdict after consistency checks. Preserve legacy review schemas and exact-hash
  migration from recognized policies 7/8/9/10 to policy 11 without changing saved
  calls, candidates, allowances or generation settings.
- Calibrate Proof reviews to essential mathematical validity: retain harmless
  corrections as nonblocking notes, check critical theorem dependencies and
  require a complete proof or admissible counterexample before acceptance.
  Show these checks and notes in the review and report. Keep repair obligations
  explicitly fallible, with reasoned dispositions, without extra model calls.
- Prepare external LemmaBench JSONL data as statement-only benchmark inputs,
  preserving reference and judging material separately. Export exact written
  answers in solution JSONL format, with optional initial Proof candidates,
  anonymized grading and explicit coverage/failure records. Keep preparation
  and export offline, and record the effective DS4 model, reasoning effort and
  service tier in benchmark plans.
- Focus Proof verification on mathematical risks while retaining whole-proof
  checking, and ask for clearer proof structure and rigorous counterexamples
  when appropriate. Give later repairs a bounded, fallible history of earlier
  located objections without extra model calls, raw reasoning replay or a new
  solver output format. Preserve existing reasoning effort and budgets. Upgrade
  recognized paused policy 7/8 jobs to policy 9 with recorded hashes and unchanged
  allowances; keep completed calls and candidates.
- Read arXiv TeX source bundles (`.tar.gz`, `.tgz`, `.tar`) and gzipped single
  TeX files through uploads, file tools and pinned workflows. Validate bounded
  archive contents in memory without extracting paths or executing source;
  preserve per-file hashes and warn about unexpanded includes and numbering.
  Recognize arXiv source URLs separately from rendered papers, retaining site
  access rules and offering upload when automated source downloads are denied.
- Keep new effort choices to Low, Medium and High, retaining saved older jobs.
- Repair Proof crash/pause recovery, queued-inference accounting, continuation
  packing, verdict parsing and uncertain-repair exports. Label reviewed responses
  without asserting the claim is true.
- Buffer stream journals with periodic synchronization, parse fragments linearly,
  avoid duplicate tokenizer requests and improve reusable research prefixes.
- Preserve literature allowances on resume; support bounded larger saved state
  and stream recovery; reject oversized initial jobs before creating them.
- Fix long arXiv HTML reading, source-outage classification, reference matching,
  DOI/arXiv canonicalization and cache freshness; disclose Open Library queries.
- Enforce read-type permissions on inferred sources, isolate PDF extraction,
  paginate file tools and add approved exact text replacements. Preserve CRLF,
  declared encodings and existing file permissions.
- Correct manuscript mapping for inactive TeX, nested numbering, repeated
  references and proof ownership; index equation tags once per document.
- Retain completed Assistant worker answers when synthesis cannot fit, fix
  context duplication and source follow-ups, and clarify usage and worker cards.
- Make benchmark cancellation retain results and stop queued work; require the
  same full first-solve cap in both arms and reject abbreviated CLI flags.

- Continue a manuscript review from the conversation without attaching the
  manuscript again. Replies to referee comments keep the previous review and
  reassess the disputed points in a linked follow-up.
- Keep Feedback visible as a small upper-right button when the sidebar is hidden,
  and retain the originating
  conversation when reporting from a worker result. Add optional screenshot
  capture with region selection, alongside existing screenshot paste and upload.
- Show skill descriptions on hover and keyboard focus, with consistent title
  capitalization across the interface.

- Follow the replacement text's encoding declaration for approved file edits,
  including conversions to UTF-8 and newly created files; undeclared text is
  saved as UTF-8. Normalize UTF-16 template copies' declarations to UTF-8 too.
- Read text files that are not UTF-8 instead of failing with a bare
  "'utf-8' codec can't decode byte 0xe9": a file declaring Latin-1 (or another
  8-bit encoding) through `inputenc` is read in that encoding; otherwise the
  bytes that are not UTF-8 are read as Windows-1252, the usual case of French
  accents. A file that cannot be read as text is named in the error, pinned
  and saved copies (style templates included) declare UTF-8, and model edits
  keep a file's declared encoding. Applies to styles, reviews,
  explanations, write-ups and the model's file tools. Learning a writing style
  leaves out an unreadable article with a warning naming it; when no article,
  or a template file, can be read, the error lists every such file.

## 1.1.0 — 2026-11-09

- License the current project distribution under the PolyForm Noncommercial
  License 1.0.0 (`LICENSE.md`, also served by the interface at `/LICENSE.txt`),
  with the contributors' agreement confirmed by the project maintainer. Remove
  the former project license copies and mixed-license metadata. Licenses granted
  for previously published versions are unaffected. Credit Vincent Boulard,
  Louis Carillo, and contributors. Source files carry an SPDX header, except
  the hash-pinned proof policy and model-facing skill files. Keep the proof
  policy unchanged so paused proofs remain resumable.
- Include the legal notices in the sandbox image and interface distribution.

- Keep image attachments on their original conversation message. Text-only
  follow-ups no longer inherit a new attachment or move earlier images to the
  latest model request; saved conversations and paused work retain their sources.

## 1.0.0 — 2026-11-08

- Present the three setup routes clearly: local self-hosting, a self-hosted
  remote GPU/cluster, and an optional hosted-API trial.

- Remove the conversation composer's fresh-review shortcut.

- Add native DS4.1 image attachments across conversations and specialist modes,
  with paste/drop, private thumbnails, immutable saved sources and pause/resume.
  Manuscript workflows retain a labelled transcription and original images;
  existing token budgets and Qwen behavior remain unchanged.
  Wait for uploads before submission and keep their original project destination
  when the user changes views.

- Make feedback trace preview optional and deliver formatted reports, screenshots
  and consented trace ZIPs directly to the configured private Slack thread.

- Reduce the automated suite from 50 cases to 11 core smoke checks; remove
  redundant test files and unused fixtures rather than combining or hiding them.
- Show the project selector in every conversation, simplify the project sidebar,
  and place a live Markdown preview beside the project notes editor.

- Add project notebooks with an ordinary-language editor, project conversation
  lists and file folders. Pin notebook revisions to turns and worker handoffs;
  preserve the original project context when paused work resumes.
- Add small, idle-time conversation titles and manual rename/move actions.
  Bound title input, output and hosted quota reservations independently of Proof.
- Add private feedback reports with screenshots, explicit conversation-trace
  consent, preview/export and redaction. Save reports durably before optional
  Slack notifications and protect cross-account downloads with maintainer access.
- Simplify the new-conversation page with a project selector, concise workflow
  explanation and skill choices. Add optional online tools and workspace
  selection to the local API launcher without changing model or Proof profiles.

- Add a hosted account menu with verified email, Access logout, dollar allowance
  details and input/output usage. Preserve local GUI behavior and all saved
  work, model settings and Proof budgets.

- Add an optional Cloudflare Access gateway for hosted pilots. Verify signed
  identities before routing to fixed, separate backend workspaces; keep pairing
  credentials server-side and preserve streamed responses and same-origin checks.
- Add opt-in durable tester allowances with atomic reservations across processes,
  conservative token accounting, and a global allowance meter. Keep ordinary
  launches and Proof budgets unchanged when the hosted configuration is absent.
- Bound heavy sandbox runs across separate hosted accounts with optional shared,
  cancellable admission slots, preserving the existing container isolation.

- Default the hosted DeepInfra profile to DeepSeek V4.1 Flash 1017, preserving
  Proof budgets, service-tier selection and concurrency. Keep V4 Flash 0731
  available for saved jobs without rewriting their model or allowances.

- Add DS4 launcher `--service-tier standard|priority`, defaulting to Standard.
  Apply Priority centrally to DeepInfra generation calls when selected, without
  changing Proof budgets or saved allowances; reject the option for Qwen.

- Bound conversational Proof preparation to two calls of at most 2,048 generated
  tokens each, 16,384 input tokens per call and 32,768 input tokens overall,
  then hand off to Proof. Keep model-written briefs within 4,000 characters.
  Preserve concise context and definitions, fall back to the original request
  when a brief is unavailable, and keep
  preparation from consuming the full solver allowance or growing into a proof.
  Retain preparation limits on resume and pass oversized input to Proof intact.

- Restore the main agent's normal task preparation before conversational
  Proof delegations with both Qwen and DS4. Preserve the original statement
  and exact context for solving and review, reuse native delegation and
  resume, and show the original and prepared tasks in Sources.

- Start the DS4 profile with five active jobs, five shared model-request slots
  and up to five independent Assistant workers; preserve Qwen's existing
  concurrency settings and sequential solve/review within each Proof run.

- Give DS4 Proof a full 384,000-token ceiling for solving, repair and review
  from Medium upwards; effort changes the maximum solver-pass count. Derive
  the workflow allowance from those calls, including bounded review retries,
  and preserve Qwen's previous rules and saved-job limits. Add an explicit
  DS4/Qwen choice to the isolated GUI launcher.

- Route DS4 proof requests through a bounded semantic decision before a long
  Assistant answer. Start a durable Proof worker for nontrivial proofs, retain
  direct elementary identities, and preserve the decision and worker on resume.

- Make GUI Pause interrupt a stalled model-response socket, preserving partial
  work and its unknown-usage reservation while releasing the model slot.

- Add an opt-in DeepInfra DS4 Flash 0731 profile for the local GUI and CLI,
  with private capped credentials, streamed reasoning and structured output.
  Separate remote-model consent from web research. Apply DS4-specific context,
  role and effort budgets while preserving explicit saved allowances.

- Preserve bibliography identifiers in Markdown links and pin retrieved
  references for conversation write-ups. Reuse their metadata and citation keys;
  keep PDFs with unresolved citations or labels explicitly partial.
- Keep later providers' matching references instead of dropping them behind an
  early result limit. For latest-author requests, search arXiv by author and
  order records by original submission date. Remove rejected recommendations
  and keep unresolved scope explicitly partial.
- Render chat and sidebar mathematics with request, attached-template and saved
  style macros, scoped to each conversation. Keep temporary worker outages
  resumable, bound reasoning recovery, and clear failed or cancelled progress.
- Preserve unfinished reading-list queries and seed checks on resume, and merge
  arXiv DOI aliases. Check webpage extraction deadlines and consult robots.txt.
- Replace the write-up compiler's fixed command-count limit with bounded,
  progress-based retries; stop compilation promptly on pause and remove every
  retained download when a re-run card's conversation is deleted.
- Refuse file writes when their approved diff is stale. Preserve explicit chat
  attachments for restricted file access and fresh reviews, and allow a new
  message after pausing the Assistant.
- Keep both endpoints of LaTeX range references, match complete bibliography
  identifiers, and deliver a source ZIP when write-ups need template files or
  figures. Apply explicit specialist caps to proof admission and enforce elapsed
  stream guards even during blank Ollama keepalives.
- Finish short write-ups without a hidden reasoning prelude. Preserve and offer
  partial LaTeX when their time allowance expires, instead of reporting a server
  outage. Conversation write-ups now receive executed sandbox code and can
  include that conversation's recorded figures.
- Compile code listings with their actual packages, retain the exact executed
  sandbox code, and resolve its recorded figures to portable source files.
  Offer the PDF and a Sources ZIP containing the LaTeX, code, figures and
  templates used by compilation. Short jobs can use their remaining time for
  repair; a compile failure is reported as an unfinished draft.
- Rewrite the documentation as three guides: `docs/guide.md` (philosophy, every
  skill and how skills are made), `docs/llm-install.md` (local and cluster
  installation, written for a coding agent) and `docs/llm-development.md`
  (architecture, rules, adding features, benchmarks). They replace `usage.md`,
  `research.md`, `python-numerics.md`, `benchmark.md`, `hyperqwen-a10.md`,
  `a10.md`, `ovh.md` and `audit-2026-11-01.md`. `AGENTS.md` sends coding agents
  to the install and development guides.
- Reshape the composer and sidebar: the workspace folder sits above the text box,
  an `@mode` chip offers the mode's description and effort (Auto, Low …
  Poincaré), and Resume replaces Send when the Assistant is paused. Search is
  always on in conversations unless launched with `--offline`. The sidebar keeps
  the list, model status, file access and Parallel work; the notebook selector
  is gone, and other notebooks open from `@` modes, cards and "Back to
  conversation".
- Restore the standalone job forms, saved-job access, effort controls and
  write-up templates.
- Add the MLX OpenAI-compatible profile and local protocol smoke test, improve
  bounded reference retrieval and recovery from malformed tool calls.
- Harden Assistant delegation, source selection, partial reports, interrupted
  proof resumes and model response parsing, with regression coverage.
- Add a Docker Python sandbox with two skills: `python-lab` runs requested
  computations and plots (`/python-lab`, `@python_lab`); `numerical-sanity-check`
  lets chats and the Assistant try to falsify claims with small bounded
  experiments (`/sanity-check`, `@sanity_check`; `--no-numerical-checks`).
  Generated figures and files appear in the conversation. See
  [docs/guide.md](docs/guide.md#python-lab-and-sanity-check).

- Robustness fixes found in live tests and code review: a structured answer
  that turns into whitespace is stopped and its complete fields kept (a reading
  list searched nothing in 20 s); a worker whose reasoning fills its output is
  asked for the answer instead of returning an empty one; a reply that only
  announces work ("Let me do targeted checks…") is continued, and a new message
  sets a paused turn aside; model-server outages pause jobs instead of failing
  them for good, and time-limited reading lists resume with more time;
  checking computations that mention a book is no longer a reference lookup;
  a second worker in a turn gets its own time; reference evidence is tied to
  the work actually cited ("Stein" no longer matches "Einstein"), contradictions
  need a read passage, and provider failures give *inconclusive*; reading lists
  judge relevance against the request and keep the maths in titles; one
  interface per folder.
- Writing styles: **Learn my writing style** (`#/style`) condenses the `.tex`
  sources of the author's articles into a named style shared by every folder:
  the macros they use, their theorem environments and, for each kind of paper,
  explanation habits and phrasings kept verbatim. A style can keep a LaTeX
  template. The same page chooses which style write-ups use: automatic, none, a
  style or one of its domains (`.mathagent/style-choice.json`).
- Write-ups are rebuilt around one writing call (all the material, the request,
  the user's notation and a short style digest, with reasoning; consecutive parts
  only for long material) instead of an outline, section calls, provenance marks,
  a notation pass and a model review: they are for results the user has checked.
  The controller builds the preamble (the request's `\newcommand` notation first,
  then the template's, then the style's), supplies commands the text uses without
  defining them, compiles with latexmk and makes one repair call. Figures come
  from the folder (`.png` and `.jpg` can now be uploaded) and a missing one is a
  placeholder. The document opens with the points left to check: TODO comments,
  unknown citations, missing figures and undefined labels.
- Write-ups number and reference their equations with `\eqref`, and keep a number
  only where the text refers to it; labels and displays from the author's
  material are never touched.
- Write-up bibliographies: with a `.bib` attached, only its keys are cited; with
  none, a bibliography is made from the works the material gives with a DOI,
  arXiv or zbMATH identifier, and embedded in the `.tex`. A work the literature
  search could not confirm is never cited.
- `@writeup` and `@summarize` in Assistant conversations write up the
  conversation's results (including jobs finished earlier in the same turn) and
  attached files, and deliver by download (the copy is deleted with the
  conversation); the reply is one line and the turn ends there. An attached
  complete `.tex` is used as a template, and the reply says so. A mode typed out
  in full counts as the call. Progress lines such as "Delegating a mathematical
  task…" are no longer saved.
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
  search is on in conversations and a per-job switch on the forms, locked off by
  `--offline`.
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
- Online search is on by default in the interface. Conversations always search;
  the reading-list and review form keeps its own switch. An explicit `--offline`
  launch still locks it off. Proofs and write-ups never search.
- The job list and a job page's overview (budget, candidates, checks) can slide
  away to a thin spine on wide screens; the choice is remembered per browser.
- The interface serves the licence and bundled third-party notices at
  `/LICENSE.txt` and `/THIRD-PARTY-NOTICES.txt`.

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
and repair a concrete objection, retaining complete candidates and review notes.

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
