# Square Harness guide

What Square Harness is for, the ideas behind it, and every skill: what it does,
how to call it and how it works inside.

- To install it, see [llm-install.md](llm-install.md). It is written for a coding
  agent, so you can ask one to do the installation (see [Install](#install)).
- To change the code, see [llm-development.md](llm-development.md).

## What it is

Square Harness is source-available software, free for noncommercial use, that
turns an open-weights language model into a research assistant for mathematics.
The model runs on your computer or on a GPU in your lab. The assistant proves
statements and checks the proofs, finds and verifies references, builds reading
lists, referees manuscripts, explains results, writes up notes in LaTeX and runs
numerical experiments. You use it in a web app (on a computer or a phone) or in
the terminal. The current release is version 1.1.0.

Self-hosting on your own computer or research GPU is the primary setup. For a
temporary hosted-model test, `--backend openai --api-profile deepinfra`
uses DeepSeek V4.1 Flash 1017 through DeepInfra. The harness and saved jobs run
on the computer or server hosting SH; model prompts, selected source text and tool context
are sent to DeepInfra. Its retention policy applies to that processing. Keep
the provider credential on the backend, never in the browser or a saved job.
`--allow-remote-model --offline` permits those model calls while locking web
research off. See the hosted-API setup in [llm-install.md](llm-install.md).

The current hosted checkpoint is `deepseek-ai/DeepSeek-V4.1-Flash-1017`.
Existing V4 Flash 0731 jobs keep their saved model and allowances; resuming
them requires a credential that also permits that older checkpoint.

A hosted pilot can use Cloudflare Access email codes before opening the same
interface. The verified email selects the private workspace on the server;
the browser never receives the model key or the workspace pairing credential.
Conversations and files remain saved there when the browser closes. Separate
service accounts and sandbox runtimes are required for isolation between testers;
the Access login alone does not provide that isolation.

In a hosted pilot, the account button at the bottom of the sidebar shows the
verified email and opens the user's allowance details and Sign out action.
Signing out ends the browser's Access session; saved files, conversations and
running work remain on the server. A local installation without the hosted
gateway keeps its existing interface.

Hosted testers may have a separate allowance shared across their conversations.
Its meter counts output-equivalent tokens, including thinking, with input
weighted by the configured input/output price ratio. At conservative Standard
list prices of $0.20/M input and $0.60/M output, a $2 allowance is 3.333M
output-equivalent tokens; each input token consumes one third of an output token.
This is conservative allowance accounting, not the provider's bill: cached input
uses the uncached price and promotional charges may be lower. Active calls reserve
their worst-case allowance; complete usage releases the unused part. Unreported
or interrupted calls retain reservations. A small remaining balance may therefore
be insufficient to start a full-length call. This pilot limit is separate from
each Proof job's existing solve/review budgets.
The account menu separates used, reserved and available amounts, with actual
input/output counts and the same output-equivalent token meter.

The DS4 launcher uses Standard service by default. Add
`--service-tier priority` to request DeepInfra's Priority service for all model
calls, at a 50% per-token premium. Priority can improve response time during
busy periods, but does not guarantee a particular generation speed. This choice
does not change Proof budgets; it applies to subsequent calls after restarting
the local server. The option is only available with `--profile ds4`.

The DS4 profile starts with capacity for five active conversations or jobs,
five simultaneous model requests across the interface, and up to five
independent workers within one Assistant turn. All conversations and worker
roles share those five model slots. Within one Proof run, solving and review
remain sequential; this setting does not launch five attempts of the same
proof. The Qwen launcher keeps its previous single-job settings.

The DS4 profile uses a 1,048,576-token context with separate generation and
workflow allowances. From Medium upwards, Proof gives every solve, repair and
review the same 384,000-token output ceiling. Effort changes the maximum number
of solver passes, not the length available to each pass:

| Proof effort | Solver passes | Solve / repair | Review | Total output allowance |
| --- | ---: | ---: | ---: | ---: |
| Low | 1 | 32,768 | 16,384 | 65,536 |
| Medium | 3 | 384,000 | 384,000 | 3,456,000 |
| High | 5 | 384,000 | 384,000 | 5,760,000 |

These are ceilings, not target lengths. The total allows one solve and up to
two reviews per pass: a review may be retried once if its response format is
invalid or it is truncated. A repair or output-limit continuation consumes another solver
pass. Resuming an interrupted call keeps the same solver pass; interrupted
reviews do not consume the one format retry. Their unknown token usage remains
reserved, so repeated interruptions can still exhaust the saved token budget. A successful review ends the run early; an uncertain review preserves
the uncertainty. Reasoning tokens count toward generated-token usage.

Medium and higher levels use the same per-call time guard; the workflow time
allowance grows with the permitted number of calls. The default guard is three
hours per call, configurable with `--proof-call-seconds`. Low retains a short
ten-minute workflow. Explicit launch or Advanced limits and the provider's
spending allowance can stop work earlier. Full input plus output must fit the
context; the harness reports an insufficient allowance rather than silently
shrinking a DS4 Proof call. These settings have not been established as optimal
by a mathematical benchmark. Existing Qwen profiles keep their previous budget
rules, and paused jobs keep their saved limits when resumed.

Pause interrupts an active model-response stream even if the provider stops
sending data. The partial response remains unfinished and resumable. If no
final usage arrived, its reservation stays charged to the local allowance;
that is conservative harness accounting, not a measurement of the API bill.
Waiting for an inference slot does not reserve Proof output or consume its
active working time. After a crash, resume charges saved active time, never
the hours the program was stopped. SIGTERM and terminal hangup request the
same saved shutdown as Ctrl+C.

For DS4 conversations, a short routing decision distinguishes very elementary
facts (such as expanding a square) from nontrivial proof requests. The latter
start the saved Proof workflow before the Assistant attempts an argument.
Explicit mode selections take priority, and a request to use the raw model
directly is respected. The routing decision and its usage are saved with the
turn; resuming does not create a second completed Proof job. A failed routing
decision pauses the work instead of authorizing an unchecked direct proof.

## Images

With the DeepInfra **DS4.1 Flash 1017** profile, attach PNG, JPEG or static WebP
images using **Attach files**, paste a screenshot, or drop files into the work
pane. Check the thumbnails before sending; Send/Start waits for uploads to
finish. Images work in conversations and all specialist modes, including Proof,
reviews, literature work, simulations,
write-ups and learning a writing style. Source selectors also accept images;
LaTeX templates still need their original text files.

Each conversation or job accepts up to eight images, 8 MiB each and 24 MiB
combined, with at most 8,192 pixels per side and 32 megapixels per image.
The model receives the actual images. Their immutable private copies
are kept with the workspace: saved chats, follow-up questions and paused jobs
retain the original bytes even if the uploaded file changes or is deleted.
Images stay with the message where you attached them. Sending a text-only
follow-up does not attach them again; the model can still refer to them in the
earlier conversation context. Attach an image again only when you want it on a
new message.
Use a new conversation for a further batch. The existing Qwen profile does not
support native images and reports that explicitly.

Proof sees the images directly. Workflows that need a text manuscript first
save a labelled visual transcription, within their existing allowance, and
continue to receive the original images. The transcription links back to its
source images. Screenshots teach visible writing style; exact LaTeX macros
require `.tex` sources.
An image sent without a written question receives the visible instruction
"Please examine the attached images." An explicit `@` skill selection is kept.
Image processing counts toward input usage; preflight labels its conservative
vision estimate, while completed calls record the provider's reported usage.
No effort or Proof budgets are increased by adding images.

## Projects, conversation titles and feedback

Use **New project** in the sidebar to group conversations around one research
problem. Open **Project notes** to write Markdown in the editor, with a live
preview beside it (below it on a narrow screen). The placeholder shows `#` and
`##` headings. A useful notebook states the goal, definitions,
notation, assumptions, established results with their sources, conjectures and
open questions. Saving writes a `PROJECT.md` in the project's file folder.
Edits from another tab are detected before saving so they cannot silently
replace your draft.

New turns receive a bounded copy of the selected project's notes; their workers
receive that same context. Switching projects or editing notes does not alter
work already running. Resume uses the original saved copy. Project notes are
user-maintained working context and are never updated
automatically from model answers. Only that project's note is added; earlier
conversations and all project files are not silently imported. You can move an
idle conversation to a project using the **Project** selector above its message
box, including conversations that have no project yet. The conversation menu
also offers this action. **All conversations** includes unassigned chats.
Selecting a project in the sidebar opens a new conversation in that project;
an unsent draft is not discarded without confirmation. Dropped or pasted files
are saved in that project's folder. An upload already started stays with its
original destination if you navigate elsewhere.

Projects organize a single user's existing workspace. They do not add a new
filesystem sandbox between that user's projects. Hosted testers continue to
have separate private workspaces.

After a conversation finishes and the interface is idle, an optional small
model call proposes a short title from a bounded excerpt of its first
substantive message. It has thinking disabled and a separate 64-output-token
ceiling; it does not consume a Proof workflow's budget. Hosted provider usage
still counts against the user's allowance. A failed title leaves the prompt
preview; a title entered manually in the conversation menu always takes
precedence. Request a further critique or review through the conversation or an
explicit `@` skill. Saved reviews and Proof's solver/reviewer cycle remain
available.

The small **Feedback** button stays visible in the work area's upper-right
corner, including when the sidebar is hidden. It accepts a bug report,
suggestion or a problem with a mathematical answer. You can paste or attach up to
three screenshots. On browsers
that support screen capture, **Take screenshot** temporarily hides the form:
choose the tab or window in the browser's sharing dialog, then select a region
from the captured image. Confirm the region to attach it, or cancel to return to
the unchanged draft. Capture stops after one frame; the image is sent only when
you submit the report. Upload and paste remain available on other browsers.
When a conversation is open, **Include this conversation and its diagnostic
trace** is off by default. If selected, you can send immediately. Previewing the
file list and downloading the diagnostic ZIP are optional. It includes the
selected conversation and its linked worker traces, not original workspace
documents or unrelated chats. Original model-input image bytes are not copied
into that ZIP automatically; attach a feedback screenshot separately if needed.
Text already quoted inside that conversation or its model calls is included;
credential fields and recognizable secret patterns are redacted. You can inspect
the bundle before sharing unpublished material. An oversized trace is refused
rather than silently shortened.

Reports are saved privately before delivery. With Slack file delivery configured,
the private feedback channel receives the formatted report text, screenshots as
native image attachments, and the complete report ZIP in its thread. The ZIP
includes the selected trace only with your consent. Channel members can read
these submitted files directly in Slack. The interface distinguishes completed
delivery from a report that was only saved on the server. Older webhook-only
installations send a summary and protected archive link instead.

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

**Recorded results.** Each workflow keeps its findings and status:

- a proof keeps the candidate, review verdict and located objections;
- a reference is *read*, *cited*, *located*, *contradicted*, *not found* or *inconclusive*;
- a partial report names what it did not cover.

When the software fails (a timeout, an exhausted budget, malformed output), the
result says so. It never turns that failure into a mathematical verdict.

**Nothing is lost.** Every attempt, review, model request and source passage is
saved in your folder. You can pause work and resume it later with the budget it
had left, and you can always see exactly what the model saw and wrote.

**Bounded work.** Every job has a budget of attempts, working time and retrieval
requests. You pick an effort level, Low, Medium or High, instead of tuning token
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
address that carries the access token. One interface serves a folder: a second
one on the same folder refuses to start, and a busy port moves to the next free
one with a message.

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
  message. Text files are best saved as UTF-8; older files in Latin-1 or
  Windows-1252 (accents saved by an older editor) are read too, in the encoding
  their `\usepackage[…]{inputenc}` declares when they declare one. A file that
  cannot be read as text is named in the error so that you can remove it.
  Approved file edits use the replacement text's encoding declaration, or UTF-8
  if none is present. Changing a declaration to `utf8` also converts the saved
  bytes; characters unavailable in the requested encoding are refused before
  the file changes. Saved template copies use UTF-8, including UTF-16 originals.
- **Learn my writing style**, below New conversation, teaches write-ups how you
  write (see [Writing styles](#writing-styles)).
- **Job pages** show live output, every attempt and review, sources, budgets and
  saved files. **Pause** saves at the next checkpoint and **Resume** continues with
  the remaining budget. A paused conversation shows **Resume** while the text box
  is empty; type a new message instead to set the paused turn aside. "Back to
  conversation" returns to the chat that started the job. The standalone forms (New proof, New reading list, New review, New
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

Explicitly attached files stay available to their conversation and its fresh
review even when that file type is hidden from general discovery. When approving
a file edit, SH refuses the write if the file changed since the displayed diff.
Chat mathematics and sidebar previews use supported macros from your requests,
attachments, selected writing style and an unambiguous saved template. Request
notation takes precedence, and one conversation's definitions do not leak into
another. KaTeX's restrictions on unsafe commands still apply.

Temporary model or source outages leave saved work resumable. Resuming retries
the same child job; cancelled queued turns leave a clear notice in the chat.

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
its status and what remains open, and decides what to do next. Independent
workers can run at the same time on an OpenAI-compatible server
(`--assistant-concurrency`: 1–4 for the original profiles, 1–5 for DS4). All
workers share the conversation's budget, and a worker cannot start
other workers. Only you can start Python Lab and an explicit sanity check: the
Assistant cannot. It can open a web address you give it and read the page or its
bibliography.

Before starting a Proof worker, the Assistant briefly prepares the task, with
either Qwen or DS4. Preparation allows at most two model calls, each capped at
2,048 generated tokens: 4,096 tokens in total. The first call may read relevant
context; the next must hand the task to Proof. Preparation input is limited to
16,384 tokens per call and 32,768 in total, using the backend's count or its
labelled estimate. Larger material goes to Proof without trimming the original
request or hypotheses. These limits apply to preparation, not Proof's context.
The model-written brief is limited to 4,000 characters and gives the precise
objective, definitions, conventions and any unresolved ambiguity. It may add
one short natural direction, clearly labelled as an unproved suggestion; it
must not begin the proof or add a missing hypothesis.

If preparation reaches its limit or fails to produce a usable delegation,
Proof starts from the original request and saved context instead of extending
the preparation. Unfinished preparation is not adopted as a proof or task
brief. Proof's solve and review allowances are unchanged. The solver
and reviewer receive the original request and exact conversation as
authoritative reference context. The Proof page's **Sources** tab shows **Original request**
and, when the delegation differs from it, **Prepared task**.

Preparation is charged to the shared Assistant budget and uses the existing
saved delegation mechanism. Its call and token limits persist across pause and
resume; an older turn already over these limits proceeds to Proof instead of
getting a fresh preparation allowance. Resume reuses work already delegated;
it does not rewrite the task of an existing Proof job. When Proof has been selected, the
Assistant must call that worker rather than substitute a direct proof.
Standalone Proof jobs and raw benchmark calls retain their direct path.

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
4. An uncertain review stops the job. If the earlier answer had a concrete
   objection, the uncertain repaired answer becomes the export; the original
   and both reviews remain available.

Every version is kept, and the first answer is never lost. Effort sets the number
of attempts and the working time. “Response reviewed” means the verifier found
no issue in the selected response. The response can prove the statement or
refute it with a counterexample; this status does not assert the statement is
true and is never a formal certificate. Read the response and its attributed
review. Conflicting JSON verdicts or retractions are not accepted as a pass.

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

For the latest papers by a named author, it searches arXiv's author field and
orders matching records by their original submission date. Revisions are listed
separately. The result lists the retrieved records and the query scope.
References whose requested scope is only partially supported remain labelled
as unconfirmed leads; rejecting a lead in a later check removes its recommendation.
A broad reference request checks relevance from indexed descriptions. Exact
theorems, pages and hypotheses require a precise check when you ask for them.

Each reference gets an evidence level:

- **read**: the statement was read in the work itself;
- **cited**: another paper was read that cites the work at that place for that
  result;
- **located**: the work exists, but the exact place is unconfirmed;
- **contradicted**: a passage that was read, in the work or citing it there,
  attributes that place to another statement;
- **not found**: no evidence;
- **inconclusive**: no record was found while a source was failing, so the
  absence proves nothing.

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
remembers are kept only if a source returns them. A reading list needs at least
4 minutes and 40 source requests, so Low is raised to that and says so; a list
cut short by time is saved as far as it got and resumes with more time.
Unfinished source queries and seed checks are retried on resume rather than
marked complete by a timeout. An arXiv record and its arXiv DOI count as one work.

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

To discuss a completed report, select `@referee` again and explain which comments
you dispute. When the target is unambiguous, the follow-up reuses the reviewed
manuscript and keeps the original report. It addresses the contested comments
rather than silently replacing the earlier review. Ask explicitly for a full new
review to reread the whole paper. New attachments take precedence; when the target
is ambiguous, the interface asks which manuscript to use.

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
  bibliography is made inside the `.tex`. References already retrieved in the
  conversation are passed as a pinned bibliography with fixed keys; the writer
  does not need to recreate their metadata. Your own `.bib` takes precedence.
  A work the literature search could not confirm is never cited.
- **Figures** the text includes are taken from the folders of the attached files
  or from your folder (`.pdf`, `.png`, `.jpg`). A missing figure is drawn as a
  placeholder.

How it works:

1. One writing call reads all the material, your request, your notation and your
   style, and writes the document. Long material is written in consecutive parts.
   With at most a minute left, it writes directly without a hidden reasoning
   prelude. If time runs out, the text already written is compiled and offered
   as a partial draft. A conversation's sandbox results supply the executed
   Python code and their recorded figures, even if the answer did not repeat them.
2. Equations the text uses again are numbered and cited with `\eqref`; displays
   nothing refers to stay unnumbered. Labels from your own material are kept.
3. The document is compiled with `latexmk` when it is installed, in a temporary
   folder, without shell escape and with restricted file access. If it does not
   compile, one repair call fixes the reported errors.
   A PDF with undefined citations, unresolved references or duplicate labels is
   offered as a partial draft, with the remaining keys listed. Producing a PDF
   alone does not make the document complete.
   Missing supported commands are supplied within one bounded compile allowance;
   pausing also stops compilation promptly.
4. The document opens with **Points to check**: its `% TODO` comments, citations
   and figures it could not resolve, and references to undefined labels. Delete
   that block once they are settled.

From a conversation, the card offers **Download PDF** when compilation succeeds
and **Sources (.zip)** containing the LaTeX and the exact code, figures and
template files it needs. Extract that ZIP into a folder to compile it yourself.
**Main .tex only** gives only the main file, without those dependencies.
The saved copy is deleted with the conversation. From the New write-up
form or the terminal, the `.tex` and the PDF are written in your folder, and an
existing file is never replaced. A sources ZIP is saved alongside them. Code
listings use the executed sandbox source rather than a model's reconstruction.
Compilation failures are reported as unfinished drafts, with the compiler error.
In the web app, a template can be saved under a
name and reused in any folder.

### Writing styles

**Learn my writing style** condenses the `.tex` sources of your earlier articles
into a named style: the macros you actually use, your theorem environments and,
for each kind of paper you write, how you announce, explain and phrase things.
Choose main article files, ideally two or more per kind of paper; `\input` parts
are not followed, so pin them as well. A style can also keep a LaTeX template (an
article's preamble, `.sty`, `.cls` or `.bib` files), which write-ups in that
style build on unless you attach one. An article that cannot be read as text
(binary, over 1 MB) is left out and named in the job's warnings; the style is
learnt from the others. If no article or a template file cannot be read, the
job does not start and lists every such file.

With the DS4.1 vision profile you can also choose screenshots of articles to
learn visible phrasing and layout. Exact macro and environment definitions
still require their `.tex` sources; they are not reconstructed from images.

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

For example, Literature check combines model-generated candidates with
bibliographic lookups and source passages. The controller stores the retrieved
records and assigns evidence levels from those passages.

The Assistant is one level up: a controller whose model may hand a precise
objective to any of the skills above, within one shared budget.

Python Lab and Sanity check also declare in their `SKILL.md` header *who* may
start them. Python Lab is explicit (only your command starts it). Sanity check is
automatic (it may be offered to the model in chat). The code enforces this
declaration, and so do the sandbox limits: the text of a skill cannot raise them.

### Effort

The following table describes the original local-model profiles, including
Qwen. The hosted DS4 Proof profile uses the separate table above; its other
skills retain their own limits.

| Effort | Attempts / rounds | Working time | Retrieval requests |
| --- | ---: | ---: | ---: |
| Low | 1 | 1 min | 20 |
| Medium | 3 | 15 min | 50 |
| High | 5 | 30 min | 80 |

Each skill translates the level into its own terms. For a proof, it is the number
of attempts. For a quick review, the number of verifier passes. For a detailed
review, how far down the dependencies the checks go (Low: the main results only;
High: deeper dependencies), with twice the working time. There
is no fixed token allowance for the original profiles unless one was supplied
explicitly, but every model call is still limited by the model's context. A
paused job resumes with what is left; it never gets a new allowance silently.
Older saved Extra high and Poincaré jobs remain readable and retain their saved allowances.

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
  Crossref, OpenCitations and Open Library (book metadata); downloads of open-access papers; web pages you ask
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
