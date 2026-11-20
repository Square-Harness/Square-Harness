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
their worst-case allowance. Complete usage releases the unused part. When DeepInfra
rejects a generation request with HTTP 429 before streaming, the entire reservation
is released. An interrupted stream with unknown usage retains its reservation.
A small remaining balance may therefore
be insufficient to start a full-length call. This pilot limit is separate from
each Proof job's existing solve/review budgets.
The account menu separates used, reserved and available amounts, with actual
input/output counts and the same output-equivalent token meter.

If DeepInfra accepts a request and takes time to produce its first output, the
interface shows that it is waiting. If DeepInfra then reports a temporary
internal error before producing any output, Square Harness can try the call
once more within the remaining time and allowance. A reservation with unknown
usage still counts toward that allowance.

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
workflow allowances. New DS4 jobs allow up to 384,000 output tokens per substantive model call.
This includes Assistant replies, Explore, Review and Explanation, Literature review,
reference checking, Write-up, Summary, Writing Style and the Python modes. Short
control operations such as routing and generating a conversation title have smaller
limits. Research workers fit each request to its available context, explicit limits and
remaining budget. Proof reserves room for each complete solve and verification.
The Proof presets are

| Proof effort | Solver passes | Solve / repair | Review | Total output allowance |
| --- | ---: | ---: | ---: | ---: |
| Low | 1 | 384,000 | 384,000 | 1,152,000 |
| Medium | 3 | 384,000 | 384,000 | 3,456,000 |
| High | 5 | 384,000 | 384,000 | 5,760,000 |

These are ceilings, not target lengths. The total allows one solve and up to
two reviews per pass: a review may be retried once if its response format is
invalid or it is truncated. A repair or output-limit continuation consumes another solver
pass. Resuming an interrupted call keeps the same solver pass; interrupted
reviews do not consume the one format retry. Their unknown token usage remains
reserved, so repeated interruptions can still exhaust the saved token budget. A successful review ends the run early; an uncertain review preserves
the uncertainty. Reasoning tokens count toward generated-token usage.

Low, Medium and High select workflow allowances, separately from the provider's
reasoning effort. DS4 uses thinking for both solving and review, with provider
reasoning effort High by default; selecting Low here does not lower that setting.

All three levels use the same per-call time guard; the workflow time
allowance grows with the permitted number of calls. The default guard is three
hours per call, configurable for Proof with `--proof-call-seconds`.
The default cumulative DS4 worker allowances are 1,152,000, 3,456,000 and
5,760,000 output tokens, with 9, 27 and 45 hours of active work. Explicit launch or Advanced limits and the provider's
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

## TeX source archives

Attach an arXiv source download with **Attach files**, or pin a `.tar.gz`, `.tgz`,
`.tar` or `.gz` file already in your workspace. The same sources work in the
terminal through `--proof-file` or `--research-file`. Both a tar bundle and
arXiv's gzipped single TeX file are supported. Archives use the **LaTeX** read
setting and remain read-only.

SH reads the source files with their filenames and a file manifest. TeX,
bibliography and local style/class files are available as text; figures, code
and other binary assets are listed as omitted. Nothing inside the archive is
executed or unpacked into your working folder. The archive must contain readable
TeX, fit within 10 MB, expand to at most 32 MB and stay within the member and
source-text bounds. Unsafe or oversized bundles are rejected before an upload
is saved.

This reads source files; it does not compile the paper or expand `\input`,
`\include` and macros. Manuscript workflows pin the textual members separately
and flag unresolved inclusion order and cross-file numbering. Ask about a named
file or result when a bundle contains several documents.

An explicit arXiv source URL such as `https://arxiv.org/src/2207.09284v1` requests
source rather than the rendered paper. Downloads keep the existing HTTPS,
robots and request-budget rules. The current [arXiv robots rules](https://arxiv.org/robots.txt)
disallow automated access to its source endpoints; when blocked, SH explains how to attach a source download
instead. Ordinary arXiv paper links still use the HTML/PDF reader. Cached source
text remains available offline.

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

Use **New project** (the `+` beside **Projects** in the sidebar) to group
conversations around one research problem. The sidebar lists your projects
above all conversations, each with its number of conversations; a conversation
that belongs to a project shows the project's name. Opening a project shows
only its conversations, under a card with the project's name and its
**Project notes**; **All conversations** leads back. Open **Project notes** to
write Markdown in the editor, with a live preview beside it (below it on a
narrow screen). The placeholder shows `#` and
`##` headings. A useful notebook states the goal, definitions,
notation, assumptions, established results with their sources, conjectures and
open questions. Saving writes a `PROJECT.md` in the project's file folder.
Edits from another tab are detected before saving so they cannot silently
replace your draft.

New turns receive a bounded copy of the selected project's notes; their workers
receive that same context. Switching projects or editing notes does not alter
work already running. Resume uses the original saved copy. Project notes are
user-maintained working context and are never updated
automatically from model answers. The Assistant can also consult a bounded index
of saved research from the same project. It receives short, labelled findings and
can read their backing records on demand. When the record outgrows that index,
proofs and reviews are kept first, then checks, exploratory answers and
experiments; the sources of the literature reviews share the remaining places.
An unassigned conversation uses its own
chat scope. Moving a conversation changes where future turns find its records. You can move an
idle conversation to a project using the **Project** selector above its message
box, including conversations that have no project yet. The conversation menu
also offers this action. The top-level list includes unassigned chats.
Opening a project in the sidebar also opens a new conversation in that project;
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
  `@writeup`, `@summarize`, `@explore`, `@python_lab`, `@sanity_check`.
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
  conversation" returns to the chat that started the job. The standalone forms (New proof, New literature review, New review, New
  write-up) start a job without a conversation.
- **The sidebar footer** shows the model and its status, the theme and phone
  pairing, then four buttons:
  - **Parallel work** shows running and queued jobs, with controls to pause or
    cancel them. Set how many jobs and model requests may run at once there
    (1–8 each; 2 and 2 by default). The button shows the count while work is
    running, and an animated square marks active conversations in the sidebar.
  - **File access** sets which kinds of files the model may open by itself in
    this folder (switch a type off to hide it).
  - **Personalization** saves standing chat instructions and LaTeX commands
    across folders under the same GUI root. New chat turns apply these
    preferences alongside the current request and any project notes.
  - **About** explains the skills and which ones the Assistant can call itself.
- **On a phone**: launch with `--gui-host 0.0.0.0` and open the printed address
  once; the phone stays paired. This is plain HTTP on your network: use it only
  on a trusted network, or through an SSH tunnel or a VPN.

Explicitly attached files stay available to their conversation and its fresh
review even when that file type is hidden from general discovery. When approving
a file edit, SH refuses the write if the file changed since the displayed diff.
Chat mathematics and sidebar previews use supported macros from your requests,
attachments, Personalization, selected writing style and an unambiguous saved
template. Request and attachment notation outrank personal defaults. Use
`\newcommand{\RR}{\mathbb{R}}` or a command with a declared number of arguments
in Personalization. KaTeX's rendering and expansion limits still apply.
You can reuse a PDF from an earlier turn in a review or write-up by referring to
it as the earlier PDF or by giving its filename. If several uploads could match,
give the filename you want to use.

Temporary model or source outages leave saved work resumable. Resuming retries
the same child job; cancelled queued turns leave a clear notice in the chat.

### Following agents in a conversation

Open **Notebook** on the right of the conversation. On desktop its four shortcuts
take you straight to a section. Workers that start together share a group in the
conversation. Select a specialist to follow its work or open its result. Timing
bars share a scale so you can see when two tasks overlapped. A dependent task
appears as waiting until its prerequisite returns. A finished agent’s answer
can be opened while the other agents continue. If an answer reached its output
limit, the agent shows the failure and its partial text remains readable.

- **Agents** shows what each specialist is doing. From a Literature task,
  **Open literature review** takes you into the review itself.
- **Results** collects investigation reports, saved answers, proof attempts, reviews, experiments and
  Write-Up downloads.
- **Findings** shows results and sources available in this conversation or project.
  Work that has produced nothing yet (a proof still without a candidate, an
  answer that came back empty) is not listed.
- **Plan** lists the research question, the steps and their dependencies in
  mathematical notation. Open **Time and usage** for the resource figures.

On a wide screen the notebook opens beside the conversation. Drag its divider
or focus the divider and use the arrow keys to resize it. On a smaller screen
the panel overlays the page. On a phone it fills the screen. Close it with
Escape or its close button. The URL identifies the tab and selected item, so
you can reload or share a link within the same authorized workspace.

### The terminal

`square-harness --workspace <folder>` opens a prompt. Proof is the default: type
a statement, or `/prove <statement>`. Other commands:

| Command | Purpose |
| --- | --- |
| `/explore` | Switch to exploration chat |
| `/check <result>` | Find or verify a reference (needs `--online`) |
| `/literature <topic>` | Write a literature review of a topic |
| `/quick_review`, `/referee`, `/detailed_review`, `/explain` | The four review kinds (the manuscript comes from `--research-file`) |
| `/writeup <instructions>` | LaTeX write-up of `--research-file` notes in the `--template-file` style |
| `/python-lab`, `/sanity-check` | Python in the sandbox |
| `/review` | Fresh-context critique of the last answer |
| `/proofs`, `/proof-report <id>`, `/resume <id>` | List, read and resume proofs (reading needs no model) |
| `/researches`, `/research-report <id>`, `/research-resume <id>` | The same for reports and other jobs |
| `/investigate <question>`, `/investigations` | Start a guided investigation or list saved runs |
| `/investigate-resume <id>`, `/investigate-report <id>` | Resume or finish with the evidence already saved |
| `/record`, `/record all`, `/record <chat-or-project-uuid>` | Inspect unfiled workspace jobs, every scope, or the research record of one conversation or project. A filed conversation opens its project’s record. As in the Findings tab, work that has produced nothing yet is not listed. |
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
| Guided investigation | A research plan and an evidence-linked report | `@investigate` | `/investigate` |
| Prove | A proof, its review and every attempt | `@prove`, New proof | `/prove` (default) |
| Explore | Approaches and useful first steps with heuristics labelled | `@explore` | `/explore` |
| Literature check | 1–2 references, each with its evidence level | `@check` | `/check` |
| Literature review | A survey of the requested question, with theorems in their settings, proof ideas and linked references | `@literature`, New literature review | `/literature` |
| Quick review | Is this proof correct? Verdict and located issues | `@quick_review` | `/quick_review` |
| Review | Referee report: overview, typos, presentation | `@referee`, New review | `/referee` |
| Detailed review | The review, plus checks of the proofs | `@detailed_review` | `/detailed_review` |
| Explain | A result and its proof explained step by step | `@explain` | `/explain` |
| Write-up | Your notes or results as LaTeX in your own style | `@writeup`, `@summarize`, New write-up | `/writeup` |
| Python Lab | Your computations and plots, run in a sandbox | `@python_lab` | `/python-lab` |
| Sanity check | An attempt to falsify a claim numerically | `@sanity_check`, automatic | `/sanity-check` |

### Conversation (the Assistant)

Use it for anything: a question, a doubt about a step, a request mixing several
tasks. It exists in the web app. In the terminal, `/explore` is ordinary chat
without workers. The Assistant answers short questions itself. Otherwise it gives a precise
objective to a worker: a proof, an exploration, a reference check, a
literature review, a manuscript review, an explanation or a write-up. It then reads the result,
its status and what remains open, and decides what to do next. Independent
workers can run at the same time on an OpenAI-compatible server
(`--assistant-concurrency`: 1–4 for the original profiles, 1–5 for DS4). All
workers share the conversation's budget, and a worker cannot start
other workers. Python Lab starts when you select **@python_lab**, or, with DS4,
when your message itself asks for a computation, a simulation or a plot to be
run ("simulate…", "plot…", "compute… numerically"). A suggestion in a document,
in an earlier turn or from the model never starts it, and neither does a
message that declines it ("do not run code", "by hand"). Only you can start an
explicit sanity check. The Assistant can open a web address you give it and read
the page or its bibliography.

The Assistant receives each worker's written answer in full. For a Python Lab
run it also receives what the run printed, up to the model's input allowance; a
longer output is cut at a line, says how much is shown and names the call that
reads the rest, and the same label appears when that result is read again in a
later turn. A worker that writes nothing after its tool results is asked once
for its conclusion. If it still returns nothing, its card says that it did not
finish, with the reason, and no answer is saved for it.

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
brief. Proof's solve and review allowances are unchanged. For new delegated jobs, both solver and reviewer receive the original user
request, your earlier messages, the Assistant's earlier answers and the same
pinned source text, so "prove the lemma you stated above" reaches both with its
statement. Pinned sources are the files attached to or named in your message (a
PDF as extracted text, which can damage formulas) and the passages that a
reference check or a literature review of the same message read; both can
inspect the cited theorem and check its hypotheses. The solver can also use
labelled ideas from earlier work, including results obtained earlier in the
same message and earlier proof attempts with their objections. The independent
reviewer receives none of these, nor an answer that reported an earlier proof
or review: it sees the candidate and verifies it against the shared task and
sources. A proof whose sources cannot fit the model's context with its review
is refused before it starts. The Proof page's **Sources** tab shows **Original request**
and, when the delegation differs from it, **Prepared task**.

Preparation is charged to the shared Assistant budget and uses the existing
saved delegation mechanism. Its call and token limits persist across pause and
resume; an older turn already over these limits proceeds to Proof instead of
getting a fresh preparation allowance. Resume reuses work already delegated;
it does not rewrite the task of an existing Proof job. When Proof has been selected, the
Assistant must call that worker rather than substitute a direct proof.
Standalone Proof jobs and raw benchmark calls retain their direct path.

### Guided investigations

In an Assistant conversation, type **@investigate** followed by a question.
For example, ask whether a proposed identity holds for all square matrices and
which hypotheses suffice. The planner can explore counterexamples and check a
restricted case, then give Proof an exact statement. The plan appears in the
conversation and work starts automatically. You can follow the steps in the
notebook or pause while the workers are running. DS4 can also select an investigation
from an ordinary Assistant question that calls for several lines of research.
A simple question can receive a direct answer. Selecting **@prove** still
starts the usual proof workflow. A planner’s clarification appears as a question in the conversation.

You can also ask DS4 for independent pieces of work in one Assistant message.
For example, ask for a reference on Markov chains and, in the same message,
ask Python Lab to simulate a specified chain. The workers can start together.
When one piece needs the result of another, it starts after it: "find a
reference for this theorem, then write a short LaTeX note that cites it" runs
the reference check first, and the note is then written by Write-up with that
reference. When one objective needs clarification, the other can still proceed.
A Python result identifies whether the computation actually ran in the enabled
sandbox. Asking for a short recap of project or conversation findings returns
an answer in the chat. For a project recap the Assistant consults that project’s
notebook and findings. You can still ask it to search the workspace.
Select **@summarize** to create a separate document.

You can include a reading task in the same message. For example, attach your
lecture notes and ask for a PDE reference, a simulation, and a summary of the
notes. The reading worker receives the named file. A document that fits the
model's input is read whole. A longer one is read in part (its opening, main
statements and as much of the rest as fits), and the answer says that the
reading was partial. The reader of a partly read document can make one round of
targeted reads in it before answering. When your whole message is a question
about the attached documents, the reader's answer is the reply itself: the
Assistant does not restate it. In a later message you can
refer to the same PDF without selecting it again. When you ask whether the
Assistant can read it, the Assistant checks the file and can answer from an
actual passage. If several earlier PDFs match, name the one you mean.
When the PDF extraction preserves page breaks, the reader can cite the physical page in the file. The printed page label may differ.
If the chosen PDF has no extractable text, supply an OCR copy or the TeX source.
The reader stays with the selected documents unless you ask it to search the folder.
Asking for a LaTeX conversion of a finished investigation uses the research
answer shown in the chat, including when several workers contributed.

In the chat, a file chip marks a file you selected for that message. A file
reused from the conversation appears under **Available for this reply**. You can
also attach one document and name another readable workspace document in your
message. A name alone follows the workspace’s file-type permissions.
Older messages show a neutral source label because they did not record how
the file was selected.

Up to six tasks can draw on Explore, Check, Literature,
Proof and Quick review. At most two are Proof tasks. A task can use up to Medium
effort. Independent tasks can run together when the model server supports it.
Proof waits for the preceding steps listed in its plan. When the plan calls
for a special case, the controller selects a precise statement from the evidence
or uses the case proposed in the plan. A change in hypotheses gets its own statement and hash. Numerical
experiments are available through Explore when the numerical sandbox is enabled.
An online investigation that explicitly asks for prior literature includes a
reference check or literature review in its plan.

Pause saves the investigation. If Explore has already run a numerical check,
its result remains available on Resume, including checks without figures. **Resume** continues the saved plan. **Stop and
report** finishes a paused run using its existing evidence, even when the model
is offline. If the model does not answer while the plan is being prepared, the
investigation pauses and shows the cause: **Resume** continues it once the
model is reachable. The report identifies the statements examined, the worker
records, and work that failed or remains open. The statement at the head of the
plan is worded by the planner and you are not asked to confirm it: the report
calls it the **proposed statement** and quotes your request beside it, so
compare the two. A model-reviewed proof is evidence about
its exact statement. An exploration or a numerical observation can suggest a
direction for later work. As findings arrive, the investigation drafts an answer
in the conversation and revises it using the newer evidence. A draft that is
finished when another step completes is shown at once and revised next. A
report requested while a revision was pending says which finished steps its
answer was written without. Finished findings
are readable immediately, including while a revision is being written. The
right-hand notebook lets you read a literature review or an exploration alongside
the answer. If the writing allowance runs out, the completed findings and the
latest saved answer remain available. Select **@summarize** for a separate LaTeX write-up.

In the terminal, use `/investigate <question>`, `/investigations`,
`/investigate-resume <id>`, and `/investigate-report <id>`. Terminal investigations
run one worker at a time. Ctrl+C during work requests a pause. When a run pauses
because the model or a worker failed, the terminal prints the cause above the
resume command. A terminal investigation uses the
workspace writer lock shared with the web interface.

### Prove

Use it for a statement you want proved, with all its hypotheses. Pin the files
it depends on: proof jobs read nothing by themselves.

For a delegated proof, the solver and verifier receive the same pinned source
passages, including pages read during that turn. Earlier attempts and numerical
leads supplied through the research index go only to the solver. For a new
source-based or delegated proof, the app checks that the model has room for
a candidate and its review before starting the solver.

How it works: **solve → verify → repair**.

1. The solver makes one full attempt, thinking first, with no tools. It explains
   the main argument and concentrates detail
   on the essential steps. If the statement is false, it may give a rigorous
   counterexample.
2. A fresh verifier, which has not seen the solver's reasoning, reads the whole
   written proof. It
   avoids unnecessary repeated derivation of routine steps and
   concentrates effort on delicate implications, theorem hypotheses, quantifiers,
   domains, constant dependence and limits. It records up to five essential
   dependencies, distinguishing supplied facts, checked deductions, standard
   results and unresolved claims. A theorem name alone does not establish an
   essential input. For the weakest nonroutine dependency, it attempts one short
   independent check of the claim itself, such as a small example, limiting
   regime or precise known result. Routine standard deductions are not re-proved.
   For a counterexample it first checks admissibility under all supplied
   relations, endpoints and constant quantifiers; an ambiguous hypothesis is
   flagged rather than silently reinterpreted. Acceptance requires checking the
   whole logical chain and
   completing the original task, either by a proof or by a counterexample
   satisfying all the hypotheses.
3. If it finds no essential issue, that proof is returned unchanged. Routine
   omitted computations, harmless local typos and removable unused remarks can
   receive nonblocking notes without triggering a new attempt. A false claim
   needed by the argument still blocks acceptance. Essential objections go
   back to the solver, which repairs the proof or argues the objection is
   mistaken. The solver receives the latest full written candidate and review.
   Later repairs also receive a short record of earlier located objections;
   these remain fallible and are marked not reassessed, rather than assumed
   resolved because a later review omits them. The solver gives a short
   mathematical reason for repairing, rebutting or retaining an objection. The new
   version is reviewed afresh without this record or the earlier reviews.
4. An uncertain review stops the job. If the earlier answer had a concrete
   objection, the uncertain repaired answer becomes the export; the original
   and both reviews remain available.

Every version is kept, and the first answer is never lost. Effort sets the number
of attempts and the working time. “Response reviewed” means the verifier found
no blocking mathematical issue in the selected response; any nonblocking notes
remain visible in its review and report. The response can prove the statement or
refute it with a counterexample; this status does not assert the statement is
true and is never a formal certificate. Read the response and its attributed
review. Conflicting JSON verdicts or retractions are not accepted as a pass.
New reviews return one task assessment; the controller derives the operational
verdict after checking its consistency with essential issues and dependencies.
This avoids asking the model for two overlapping status labels. An inconsistent
assessment remains unavailable rather than being promoted to acceptance.

The repair record is built from saved reviews, with no extra model call or new
solver output format. It does not retain unwritten failed approaches or replay
the solver's reasoning. The record is bounded and can be omitted to preserve
space for the original inputs, current candidate, latest review and solver
output. An unfinished generation follows a separate continuation path, which
does reuse saved reasoning notes, or a labelled ending excerpt when necessary.

Paused jobs using recognized Proof policies 7, 8, 9 or 10 are upgraded to policy 11
on resume. The report records the old and new policy hashes; completed calls,
candidate versions and saved allowances are retained. Completed old jobs remain
readable without an upgrade.

### Explore

Ordinary mathematical chat with read access to your folder (`.tex`, `.bib`, PDF
text, …). **Explore** gives promising approaches early, with the first useful
step and the main obstacle for each. It labels heuristic steps and may run a
quick numerical sanity check. Use **Quick Review** to check a proof or **Fresh
review** to audit a previous answer. Saved Critique conversations can still be
read and continued as ordinary mathematical chats.

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

### Literature review

Ask the mathematical question you want to investigate. You can specify an audience, a length, or a particular setting. Literature review opens with the problem and what the sources establish. It organises the remaining results around the differences that matter for the answer, such as competing hypotheses, a counterexample or a later improvement. It explains the decisive results in detail and situates the other works on the topic in a sentence each. A focused question normally calls for 600–1,500 words. A broad survey can use 3,000–4,000. A comparison table is optional. The chat shows the review itself and offers Markdown and BibTeX downloads.

The search uses zbMATH, arXiv, Crossref and Semantic Scholar. For a key paper available by DOI, it can use OpenAIRE to locate a deposited manuscript. It follows references from relevant works, including papers with different terminology. Search terms, claims in a supplied brief and works recalled by the model are leads to investigate. Each citation in the review points to a retrieved record. A dagger in the references identifies works for which the review used only an abstract or a bibliographic record. The other entries used passages from the paper. After them, **Further reading** lists works the plan placed in the review that the checked text does not discuss; the review makes no claim about them. A bibliography entry establishes the identity of a work but a precise theorem still depends on its hypotheses and on the passage supporting it.

Every sentence of a section is checked against the available evidence. A first pass names the source sentences that carry it, and a second compares it with those passages. A checker can correct a local error or withhold a sentence it cannot support. When a check fails, the export withholds unchecked claims and keeps the underlying source material accessible. A partial result identifies gaps, including a review based entirely on abstracts, an unavailable section check or an interrupted search. Neither a completed check nor a search that returned no later paper establishes that a question is open. Open **Evidence** on the job page to inspect a numbered reference. Each entry links to the paper or registry and shows the saved source passage with its line range, the source-record abstract excerpt, or a bibliographic record. Papers already read appear there while the review runs.

When a model stream stops during screening, the review keeps finished classifications, continues with other candidates, and can retry the affected candidates in smaller groups. On a personal DeepInfra key, a request that the provider accepts without starting to answer is submitted again after 25 seconds; hosted tester accounts wait for the provider's own answer. If the provider repeatedly fails, the review pauses. Before final selection, the Markdown and BibTeX downloads identify provisional references from the retrieved registry records. The token meter shows how much output the provider reported and how much of the allowance is held for calls with unknown usage.

Low, Medium and High allow up to 55, 110 and 165 public source requests. Local-model profiles normally allow 6, 12 and 20 minutes respectively. Hosted DS4 uses the workflow time allowances described above. All three efforts check the written sections. Higher effort broadens discovery and allows more source passages: Low, Medium and High screen up to 100, 250 and 500 candidates and plan the reading of 3, 9 and 14 papers; the works the plan relies on can add up to three more, and papers already in the workspace cache are reused. Most of a review's time is spent waiting for sources, because arXiv asks for a pause between two requests; the job report lists the time of each step. Explicit time and request limits apply.

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
document in your own style: an article, cleaned-up notes, lecture notes, an
exercise sheet, an exam, or one part of a paper (an introduction, a section). It
is exposition, not research: every statement, hypothesis and constant of the
material is kept as it is, and nothing reviews the mathematics, so write up
results you have checked. What the request asks for beyond the material (an
introduction, exercises, solutions, exam questions) is written in sections of
its own, and any new mathematics in them is marked as generated: a document
that contains some is a draft until it has been checked.

To convert a finished review, ask “Write it in a LaTeX document please” directly
after the review in the same conversation. You can also ask this after a written
Markdown answer, or attach a Markdown file and ask to convert it. Write-up uses
the saved text directly, including any coverage warnings in a partial review,
and keeps its equations, headings and references in source order. The card
provides the LaTeX source and a PDF when compilation succeeds. When the review
has a matching saved BibTeX file, the source ZIP includes it.
The PDF preview recognizes standard LaTeX and AMS mathematical notation,
including probability operators, conditional bars and relation symbols.
After a Write-Up has returned its sources, ask “compile it” to build the saved
document and its figures again. The reply reports the compiler result. The
source ZIP includes the original UTF-8 code used in an illustrated write-up.
When the preceding answer includes a simulation plot, Write-Up takes the
experiment files with it so the figure can appear in the document.
A formula that needs a user-defined command stays in the source for compilation with your
own preamble. Requests for editing or a selected template use the writing workflow.

Use it for:

- a clean note from your notes or from a proof the Assistant found:
  `@writeup a short note with Lemma 2 and the reviewed proof, for my students`;
- a summary of a conversation: `@summarize` (or `@writeup` with nothing after it
  and no attachment) gives each
  result's statement, recorded status, proof idea and open questions, with each
  source's role and recorded verification status;
- a missing part of a paper: attach its sections (without `\begin{document}`),
  your `.sty` and your `.bib`, and say what to write.

What it uses:

- **Material**: the conversation so far (messages, answers and the full results
  of finished jobs, including those of the current turn) and the files you
  attach. With nothing else to work from, notes pasted in the message under the
  request (after a blank line, or a message longer than 400 characters) are the
  material: `@writeup a short note from these notes:`, a blank line, then the
  notes. A complete `.tex` file (with `\begin{document}`) is used as a
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

1. A first, short call plans the document: what kind it is and its language,
   what the request asks for, what the material contains (each result with its
   status: proved, sketched, only stated, uncertain, withdrawn), where it is
   wrong or inconsistent, the sections, and what is missing (earlier work, a
   proof, a definition, data, a decision only you can make). The controller
   keeps a generated section only when the request, or a required part of that
   kind of document, asks for it. The plan is shown in the **Outline** tab.
   When your notes contradict themselves (a constant whose own derivation gives
   another one), the document keeps your version, marked TODO, and the reply
   asks you which one to keep, with both versions.
   In a conversation, when a section depends on something the material does
   not contain (earlier work to cite, a proof, a definition, data, or a choice
   only you can make about a problem in your notes), nothing is written: the
   Assistant gets the list, obtains what it can (a literature search, a check,
   a proof) or asks you, then asks for the write-up again. If it still lacks
   something, it tells you what. On the write-up's page, **Write anyway**
   writes it with those pieces marked as points to check, and **Stop** leaves
   it.
   Each kind follows written guidelines (`mathagent/skills/writeup/guidelines`):
   a paper's style rules for articles, notes and parts of papers; an
   introduction's required parts (motivation, literature, positioning, main
   result, method, organisation); an exam's header, points and recalled
   notation; rules for exercise sheets and lecture notes. Your request comes
   before these rules, and your active style comes between them (see
   [Writing styles](#writing-styles)): when the request asks in so many words
   for what a rule forbids, the plan notes it with your words, and that rule is
   neither applied nor checked. The report's **Rules set aside** says which
   rules your request or your style set aside.
   For an article, a part of a paper, notes or lecture notes of a section or
   more, a second short call then fixes the **skeleton** of the document: its
   sections, its results, the formulas cited at a distance and the figures,
   each with its label, and which result uses which. The labels your material
   already has are kept. Every later call reads the skeleton: the writer uses
   its labels and writes the linking sentences its connections call for, and
   no others; the reviewers check the text against it. The report prints it.
2. A whole paper (an article, or a part of a paper with its introduction) is
   written section by section: each call writes a few consecutive sections of
   the body, with the skeleton, the text already written and the guidelines of
   the body only. The introduction comes last, written from the finished body:
   it cites the main result, the experiments and, when the body has three
   sections or more, each section in a closing plan. When your material has its
   own introduction in LaTeX, it is kept: one call only adds those links and
   repairs its references. The abstract is written after the introduction,
   checked once against the main result, and written again once if it fails.
   A section runs two to five pages: the plan says how long the document is and
   makes a step of an argument a subsection or plain text, and is asked again
   when it has too many or too few sections; after the compile, a section
   outside two to five pages is listed for you (the introduction and a closing
   discussion may be shorter).
   When you ask to merge finished LaTeX texts (attached without
   `\begin{document}`), they are assembled, not rewritten: the skeleton says
   which text each section comes from (each text is one section: its sections
   become subsections, unless you ask to keep them; read by code from the
   texts' headings;
   the skeleton is then read from the texts and the model only annotates it),
   the texts are put together by code (a
   label or a command two texts both define is renamed in the later one,
   bibliographies are united) and the report lists each change. The model
   writes only what is new: one notation where the texts differ (a table of
   symbols that code applies inside the formulas; the text that proves the
   main result keeps its own notation, unless your request names another; the
   abstract is written after this, with the same symbols; a
   replacement that would clash with a symbol already used is put to you), an
   edit of a few lines that replaces a statement another text already gives
   by a sentence citing it, the opening of each section,
   the introduction's links and the abstract. The fidelity check then reads
   only the passages that changed.
   Any other document is written as follows.
   One writing call reads all the material, the plan, the guidelines, your
   request, your notation and your style, and writes the document in the
   requested language. Material too long for one call is written in parts that
   follow the plan's sections; each part knows the labels and commands the
   earlier parts defined. Long material is written in consecutive parts.
   With at most a minute left, it writes directly without a hidden reasoning
   prelude. If time runs out, the text already written is compiled and offered
   as a partial draft. A conversation's sandbox results supply the executed
   Python code and their recorded figures, even if the answer did not repeat them.
   Without a template, the page is A4 with 2.5 cm margins; an exam or an
   exercise sheet uses the `article` class with `exercise`, `solution` and
   `\points{n}` defined; another language than
   English gets babel and theorem names in that language (French, German,
   Spanish, Italian); a long title gets a short running title.
3. Equations the text uses again are numbered and cited with `\eqref`; displays
   nothing refers to stay unnumbered. Labels from your own material are kept.
   Typographic punctuation is written as LaTeX input (`---`, `--`, ``` ``…'' ```,
   `\dots`, `~`), outside code listings.
4. The document is compiled with `latexmk` when it is installed, in a temporary
   folder, without shell escape and with restricted file access; your own
   `latexmkrc` is not read. If it does not
   compile, one repair call fixes the reported errors (passage by passage for a
   long document). A repair that changes more than LaTeX (a label, a citation,
   a figure, a listing, a theorem or a display) is refused, and the errors are
   left on the document's page.
   Missing supported commands are supplied within one bounded compile allowance;
   pausing also stops compilation promptly. So are undefined theorem-like
   environments, including `exercise`, `problem` and `question` (numbered on
   their own) and `solution` (set like a proof).
   References are clickable: the harness's preamble loads `hyperref` (blue
   links) and, when the text uses `\cref`, `cleveref`; your own template gets a
   bare `hyperref` unless it loads it, and if it then fails to compile it is
   compiled without and the report says links are not clickable. The abstract is
   printed as front matter, in the title block for the AMS classes.
   Once the PDF is built, every warning of the log is read and classified. What
   code can fix is fixed (the report lists each fix under "Fixed
   automatically"): a reference to a label written slightly differently
   (`sec:numerical` for `sec:numerics`), a citation key one letter off your
   `.bib`'s, a label defined twice (the second gets a new name and the
   references after it follow), mathematics in a section title, figures and
   tables placed `[h]`, a figure too tall for the page, a line running into
   the margin (its passage is set again, symbols unchanged). A reference with
   no close label goes to one short call that may only pick an existing label
   whose kind fits ("Theorem~\ref" needs a theorem); what stays unresolved is
   put to you as a question ("`\ref{sec:numerics}` in the section
   Introduction has no target: which section is meant?"). This applies to an
   introduction you supply too. Ask to keep your LaTeX as it is and nothing is
   changed; the report lists what would have been fixed.
5. A source checker reads the LaTeX and the compile log, with no model call. It
   reports what keeps the text from going into a paper as it is: `% TODO`
   comments, a draft notice, equation numbers typed by hand ("by (3)"), text that
   talks about its notes ("the notes record…"), mathematics typeset wrongly
   (`$$`, Unicode symbols such as `≤`, `<=`, `sin` without a backslash,
   `\mathrm{Z}`, words without `\text`, `...`), leftovers of rough notes or
   Markdown (`wlog`, `**bold**`, `\textbf{Theorem}`, straight quotes), and lines
   more than 5pt into the margin. Numbers in the mathematics that your material
   does not contain are listed in the report as points to look at; they never
   block. These are heuristics, not a proofreading.
6. Then the document is reviewed, as Proof reviews a proof, unless the effort
   is Low. A fresh check compares it with your material and request: a result,
   hypothesis or constant that changed, something left out, mathematics added
   where the text restates your notes, a generated exercise or solution that is
   wrong, a requirement not met. A style check reads it section by section
   against the guidelines, and each required part (an exam's header, an
   introduction's motivation…) is asked about on its own. Issues that can be
   fixed by rewording are revised one section at a time; a revision that
   changes a formula, loses a label, adds a citation or alters a statement is
   refused. A correction that would change a formula of your material is never
   made for you: it is put to you with both versions. Generated mathematics (an
   exercise, a solution, exam questions) is corrected when the check finds it
   wrong, and checked again; once a check has passed over it, it no longer
   counts as unchecked. Up to three rounds, each with its own focus: first
   fidelity and the required parts, then how the whole reads, then the
   sentences. Every finding is kept with what became of it (fixed, set aside,
   for you, its passage gone) and is not raised again. The writer answers each
   one with an edit or a rebuttal, and a separate judge rules on both: an edit
   is kept only if the passage is better (partly better: kept, and the finding
   stays open with what remains), an upheld rebuttal closes the finding, and a
   rejected one is put to you in the report. A finding left without an answer
   is asked again, alone. Findings about how the text reads, and those a first
   edit did not fix, are answered by a rewrite of their section in which every
   display, statement and formula is frozen as a token (a generated passage's
   sentences stay visible; only its markers are frozen): the rewrite can reorder
   and rephrase, never change the mathematics, and the judge keeps the old
   section when the new one is worse. A revision that does not compile is
   undone, its findings reopened, and the review goes on; the model repairs only
   a first draft. Rules that code checks exactly are not
   asked of the reviewers. The report has a table of every rule of the
   guidelines (followed, open and for whom, not applicable, or not checked and
   why) and groups the findings by outcome. The best version is kept.
7. The result is **Ready** only when it compiles, every reference and citation
   resolves, no label is defined twice, the checker found no blocking or
   mathematical problem, nothing is left to settle (no `% TODO`, no missing
   figure, no text cut short by time, no generated mathematics left unchecked,
   nothing missing that a section depends on), no line runs into the margin,
   and the log has no class warning and no float problem. Other warnings
   (packages, underfull boxes, fonts) are listed in the report.
   The review must have found nothing serious against your material or the
   guidelines, and every required part must be there (in an introduction you
   wrote, a missing part is put to you instead: the text is yours).
   When the document has a skeleton, each of its labels that something refers
   to (and the main result's) must be defined once. A result whose proof does
   not cite a result it uses, or a result cited before it is stated, is sent to
   the reviser and, if it stays, listed for you; it does not keep the document
   a draft (nor does a lemma cited nowhere or a listed formula never cited).
   For an exam, the points must add up to the total the request asks for. A
   name of a person or work that only the plan mentions, with no source, keeps
   the document a draft.
   Otherwise it is a **Draft**: the reply starts with `Draft:` and says why, and
   the document opens with a short **Points to check** notice, in its language,
   counting the `% TODO` comments and the other points to settle; the report on
   the write-up's page lists them. Delete the notice once they are settled. A
   ready document has no such block. "Ready" means these checks passed: read it before using it.
   When you attach a complete `.tex` of your paper, three more checks apply,
   because the text is meant to be pasted into it: a label your paper already
   uses, a citation outside your bibliography, and a command or environment your
   paper does not define each keep it a draft.

From a conversation, the card offers **Download PDF** when compilation succeeds
and **Sources (.zip)** containing the LaTeX and the exact code, figures and
template files it needs. Extract that ZIP into a folder to compile it yourself.
**Main .tex only** gives only the main file, without those dependencies.
**Body .tex** gives the text alone, without preamble, title, block,
bibliography commands or the review's comments, to paste into your paper or
`\input`; definitions it uses that your preamble may lack are listed at its
top, as comments, and a draft says there how many `% TODO` comments it still
holds.
The saved copy is deleted with the conversation. From the New write-up
form or the terminal, the `.tex`, the PDF and the body alone (`-body.tex`) are
written in your folder, and an existing file is never replaced. A sources ZIP,
which also holds the body as `document-body.tex`, is saved alongside them. Code
listings use the executed sandbox source rather than a model's reconstruction.
Compilation failures are reported as unfinished drafts, with the compiler error.
In the web app, a template can be saved under a
name and reused in any folder.

### Writing styles

**Learn my writing style** condenses the `.tex` sources of your earlier articles
into a named style. The articles are sorted into kinds of paper (by field and
readership), and for each kind the style answers, from what your papers actually
do:

- who the papers are written for, and what that changes;
- how and when you introduce definitions;
- how you motivate the work and which results the introduction puts forward;
- how and when you motivate a proof strategy;
- how you organise proofs (for example the strategy first, the key results from
  propositions and lemmas, the lemma proofs at the end);
- what you leave out or take as given, and when you give references;
- how you point the reader to the core of the contribution;
- your tone and voice.

It also keeps your names for things that have several names (Cauchy–Lipschitz or
Picard–Lindelöf), your typesetting habits (such as `\left(\!`), your phrasings,
the packages your articles load and the macros you use, with what each is used
for when the definition does not say it and the packages it needs. Each article
is also compared with the write-up guidelines: a rule your papers deliberately
do not follow (contributions as a bulleted list, sections that open by saying
what they do, all definitions in a preliminaries section) is a departure, kept
for a kind of paper when most of its articles show it (both of two, two of
three). Names, typesetting examples, phrasings and departure examples are kept
only when they occur in your articles; a LaTeX quotation is kept as your article
writes it, even when the model dropped its backslashes. With the DS4 profile each article is read whole (any `.tex` the
harness can read, up to 1 MB); smaller models read representative passages, and
the job's warnings name every article that was not read whole. Choose main
article files, ideally two or more per kind of paper; `\input` parts are not
followed, so pin them as well (a pinned macro file counts). A style can also keep a LaTeX template (an
article's preamble, `.sty`, `.cls` or `.bib` files), which write-ups in that
style build on unless you attach one. An article that cannot be read as text
(binary, over 1 MB) is left out and named in the job's warnings; the style is
learnt from the others. If no article or a template file cannot be read, the
job does not start and lists every such file.

With the DS4.1 vision profile you can also choose screenshots of articles to
learn visible phrasing and layout. Exact macro and environment definitions
still require their `.tex` sources; they are not reconstructed from images.

At the top of the same page, **Active style** chooses the style write-ups use:

- **Default style**: no rules of its own; write-ups follow the write-up
  guidelines as they are. It is always there, for instance when a learnt style
  turns out to copy habits you do not want.
- a learnt style, or one of its domains;
- **Automatic**: a short call picks the learnt style that fits the material, or
  the default style when none fits.

Write-ups apply an order of priority: what your request says, then your style,
then the guidelines. A rule your active style departs from is set aside: it is
not applied and not checked, and the report lists it. Your request comes before
both: ask in so many words to break a rule ("list the contributions as bullet
points") or to keep one your style departs from ("no sentence that only
announces a section"), and the write-up does so. Three rules are never set
aside: a statement keeps all its hypotheses (P4), the text states mathematics and
never talks about its notes or source (P7), and no two passages contradict each
other (P15). The introduction's rules (I1 to I12) are set aside only by your
request, never by a style: an introduction is written for this document's
readers. Every other rule follows the order above, at your responsibility.
Neither the request nor the style changes the mathematics, its fidelity to your
material, or the citations.
A write-up also uses your macros for what you use them for and loads the
packages they need. Styles learnt with earlier versions keep their shorter
description, have no departures and still work; learn them again to get the
full guide. A departure learnt against a rule that has changed since is not
applied, and the write-up says so. Styles and the active style are kept in the
`.mathagent/` folder of `--gui-root` and shared by every folder.

### Python Lab and Sanity check

Both run Python in one local Docker sandbox: a fresh container for each run, with
no network, no access to your files and strict limits. Each run is saved
(code, seed, results, figures) and can be re-run. Figures appear in the
conversation. Install the sandbox once (see the install guide).

- **Python Lab** runs the computations you ask for: simulations, linear algebra,
  symbolic work, plots. It starts from **@python_lab**, or, with DS4, from a
  message of yours that asks for a computation, simulation or plot to be run;
  nothing else starts it. Defaults: 30 s, 4 GB, 2 CPUs per
  run. It can ask for up to 300 s, 16 GB and 4 CPUs, and you approve each such
  request.
- **Sanity check** tries to falsify a claim with cheap experiments: small cases,
  boundaries, random sampling, minimising the margin. When the sandbox is
  installed and the model context is at least 32,768 tokens, it is offered
  automatically in Explore and the Assistant (5 s, 2 GB, 1 CPU, 3 runs
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

The experiment record identifies a selected skill as `explicit`, a requested Python Lab run started by the Assistant as `routed`, and an automatic check as `automatic`.

Each run is saved under `.mathagent/experiments/<id>/` (the oldest are pruned
beyond 200 runs or 256 MB; a run whose record is damaged is left in place).
Manage runs with `python -m mathagent.numerics list`,
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
start them. Python Lab is explicit (your command starts it, or a message of
yours that asks for a run, as described under Conversation). Sanity check is
automatic (it may be offered to the model in chat). The code enforces this
declaration, and so do the sandbox limits: the text of a skill cannot raise them.

### Effort

The following table describes the original local-model profiles, including
Qwen. The hosted DS4 profile uses the generation and workflow allowances
described above for Proof and the other modes.

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
| `research/` | Reports, reviews, literature reviews, write-ups (those made in a conversation are deleted with it) |
| `checks/` | Literature checks |
| `chats/` | Conversations and saved Assistant checkpoints |
| `investigations/` | Investigation statements, task records and final reports, removed with their owning conversation |
| `answers/` | Delegated chat-worker answers, removed with their owning conversation |
| `index/experiments/` | Compact experiment records retained when older sandbox artifacts are pruned |
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

For a new comparison, the benchmark runner gives raw inference and Proof the same
initial instructions, model, sampling and output allowance. Raw inference makes
one call; Proof can review and repair its answer, spending more compute. The
initial calls are sampled separately, so matching seeds do not ensure identical
answers. Record correctness, failures, tokens and time, and agree on the grading
rubric before reading the answers.

LemmaBench JSONL data can be prepared with `python -m mathagent.lemmabench prepare`.
Choose explicitly between all rows and rows accepted by every recorded dataset
judge. Preparation preserves the lemma as the full statement when it already
contains the exact assumptions; otherwise it includes both without rewriting
them. Original records and judging metadata stay in a separate private evaluation
directory. This filter records a dataset selection, not a mathematical guarantee.
After a run, `python -m mathagent.lemmabench export` writes solution JSONL files
with each exact written answer in `proof_steps`. It can also export Proof's first
candidate for comparison with its final answer, and anonymize submissions for a
grader. Its failure ledger distinguishes missing or failed jobs from solutions;
it never invents a proof for an empty response. Preparation and export are local
file operations: neither starts inference or evaluates mathematical correctness.
See the [benchmark preparation commands](llm-development.md#lemmabench-preparation-and-export).

## Limits

- The solver and the verifier are the same model in separate contexts, so they
  can make the same mistake. Check important proofs yourself.
- Not finding a reference does not establish novelty.
- Formulas extracted from PDFs can be garbled: give TeX sources when you can.
  Reviews do not follow `\input`/`\include`; such files are reported as unread.
- Linux and macOS only; native Windows is not supported.
