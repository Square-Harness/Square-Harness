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
uses DeepSeek V4.1 Flash 1017 through DeepInfra, and `--api-profile openrouter`
uses the same model through OpenRouter, which routes each call among a fixed list
of providers that keep no data (Crusoe first, then Cohere, CoreWeave, DeepInfra,
Parasail; change it with `SH_OPENROUTER_PROVIDERS`), so a busy provider is replaced
by the next one. The provider is only a setting: `scripts/start-deepinfra.py
--provider openrouter` (or `deepinfra`, the default), each with its own key
(`scripts/setup-deepinfra.py`, `scripts/setup-openrouter.py`). The harness and
saved jobs run on the computer or server hosting SH; model prompts, selected source
text and tool context are sent to the provider. Its retention policy applies to that
processing: with OpenRouter, every request asks for zero data retention and no
data collection; also turn on Zero Data Retention for all models and keep prompt
logging off in your OpenRouter privacy settings. You can give the key a credit limit
for hosted use. New Assistant turns remember the provider and endpoint that
started them. If you switch providers, select the original one again to resume
a paused turn. An older Assistant turn without a recorded endpoint asks you to
confirm the destination before sending its saved conversation. The provider
credential stays on the backend.
Use `--allow-remote-model` to authorize a non-loopback model endpoint. See the hosted-API setup in [llm-install.md](llm-install.md).

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
Each call is charged when it ends: on OpenRouter the price OpenRouter reports for
it, on DeepInfra its reported tokens at these rates (cached input uses the uncached
price, so the real bill may be lower). Nothing is set aside before a call: any number
of calls can run at once while the allowance is not spent, and a new call is refused
only once it is. Calls already running at that moment finish and are charged.
The overshoot depends on their combined cost. A request the provider refuses before
streaming (HTTP 429 or 5xx) costs nothing. A stream that fails without reporting its
usage (a silent, stalled or broken stream) is charged an estimate: its whole input
and every streamed character plus a margin. This pilot limit is separate from each
Proof job's existing solve/review budgets.
The account menu shows used and remaining amounts, with actual input/output counts
and the same output-equivalent token meter.

If DeepInfra accepts a request and takes time to produce its first output, the
interface shows that it is waiting. If DeepInfra then reports a temporary
internal error before producing any output, Square Harness can try the call
once more within the remaining time and allowance. A call with unknown usage is
charged its estimate toward that allowance.

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
remaining budget. Proof fits each solve and independent verification to the available
context and remaining output. New jobs use the High workflow profile.

| Proof effort | Solver passes | Solve / repair | Review | Total output allowance |
| --- | ---: | ---: | ---: | ---: |
| High | 5 | 384,000 | 384,000 | 5,760,000 |

These are ceilings, not target lengths. The total allows one solve and up to
two reviews per pass: a review may be retried once if its response format is
invalid or it is truncated. A repair or output-limit continuation consumes another solver
pass. Resuming an interrupted call keeps the same solver pass; interrupted
reviews do not consume the one format retry. A call you pause (or that a
shutdown or crash cuts off) has no usage report, so it is charged an estimate:
every character it streamed, reasoning included, counts as a token, plus a margin
of 1,024 tokens, never more than its reservation. A call cut off by a provider
failure keeps its whole reservation. Jobs saved by an earlier version, whose paused
calls were charged in full, are re-charged from their saved streams when resumed;
the report lists each change. A successful review ends the run early; an uncertain review preserves
the uncertainty. Reasoning tokens count toward generated-token usage.

DS4 enables thinking for the solver and verifier. Its provider reasoning effort
defaults to High and is separate from workflow limits. Each Proof call can use up
to three hours by default, configurable with `--proof-call-seconds`. Five
passes allow up to 5,760,000 generated tokens and 45 hours of active work.
Explicit numeric budgets and the provider's spending allowance can stop the job
earlier. With a smaller budget, Proof gives both the solver and verifier a share.
The verifier must receive the exact task and the entire candidate. If they cannot
fit, Proof retains the candidate and reports that verification was unavailable.
A paused job keeps its saved allowances.

Pause interrupts an active model-response stream even if the provider stops
sending data. The partial response remains unfinished and resumable. When the
hosted model stops sending output for three minutes in the middle of an answer,
the call is ended and the work pauses with that notice; **Resume** tries again. If no
final usage arrived, the call is charged its streamed estimate (see above), or
its whole reservation after a provider failure; that is conservative harness
accounting, not a measurement of the API bill.
Waiting for an inference slot does not reserve Proof output or consume its
active working time. After a crash, resume charges saved active time, plus the
time an interrupted call kept writing its saved stream, never the hours the
program was stopped. SIGTERM and terminal hangup request the
same saved shutdown as Ctrl+C.

For DS4 conversations, a short routing decision distinguishes very elementary
facts (such as expanding a square) from nontrivial proof requests. The latter
start the saved Proof workflow before the Assistant attempts an argument.
Explicit mode selections take priority, and a request to use the raw model
directly is respected. The routing decision and its usage are saved with the
turn; resuming does not create a second completed Proof job. A malformed or invalid
routing decision pauses the work instead of authorizing an unchecked direct proof.
When the model provider is busy and the routing call cannot get an answer within
about 20 seconds, the message is answered directly instead (the Assistant can still
start Proof or another workflow itself), and a notice says so; if that turn is
paused and resumed before its answer, the routing is tried again.

## TeX source archives

Attach an arXiv source download with **Attach files**, or pin a `.tar.gz`, `.tgz`,
`.tar` or `.gz` file already in your workspace. The same sources work in the
terminal through `--proof-file` or `--research-file`. Both a tar bundle and
arXiv's gzipped single TeX file are supported. Archives use the **LaTeX** read
setting and remain read-only.

SH reads the source files with their filenames and a file manifest. TeX,
bibliography and local style/class files are available as text; figures, code
and other binary assets are listed as omitted. The metadata files that macOS
`tar` and Finder add (`._main.tex`, `__MACOSX/`) are ignored. Nothing inside the archive is
executed or unpacked into your working folder. The archive must contain readable
TeX, fit within 10 MB, expand to at most 32 MB and stay within the member and
source-text bounds. Unsafe or oversized bundles are rejected before an upload
is saved.

Nothing is compiled. The manuscript skills (Proof Check, Review, Typo,
Explain) assemble the paper as text: the file with `\documentclass` is the main
document, and each `\input`, `\include`, `\subfile` or `\import` of a file in the
archive is replaced by that file's text, so the whole paper is reviewed with its
numbering. Each location in the report names the archive file and its line
(`sections/results.tex, line 20`). An inclusion whose file is not in the archive
is listed as outside the review; macros are not expanded. Ask about a named file
or result when a bundle contains several documents.

An explicit arXiv source URL such as `https://arxiv.org/src/2207.09284v1` requests
source rather than the rendered paper. Downloads keep the existing HTTPS,
robots and request-budget rules. The current [arXiv robots rules](https://arxiv.org/robots.txt)
disallow automated access to its source endpoints; when blocked, SH explains how to attach a source download
instead. Ordinary arXiv paper links still use the HTML/PDF reader. Cached source
text remains available offline.

## Images

With **DS4.1 Flash 1017** on DeepInfra or OpenRouter, attach PNG, JPEG or static WebP
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
replace your draft. You may also edit `PROJECT.md` in another editor. If its
first line (the project header) is removed or damaged, the project stays listed,
the editor shows a notice and saving the notes writes the header again. Longer
notes can be saved and are subject to the document storage and model context limits.

Deleting a conversation keeps its saved proofs, reports and runs in its
project's research record; only its short worker answers are deleted with it.

New turns receive a bounded copy of the selected project's notes; their workers
receive that same context. In a chat with no project, no project instructions
apply. You can ask “What does the Fourier series notebook say?” to display
another project's notes verbatim. If you ask for a summary, the Assistant
receives those notes as source material for that turn.
Its file tools skip project notebooks during ordinary searches and can still
read papers in project folders. Switching projects or editing notes does not alter
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
original destination if you navigate elsewhere. **Files** on the project card
lists what the project's folder holds; **Attach** adds a file to your next
message. The folder picker shows a project's folder by the project's name and
does not open it as a separate folder. **Delete project…** in **Project notes**
removes the project from the list after a confirmation: its conversations move
to *No project* with their messages, and its files stay in its folder. A project
with running or queued work cannot be deleted.

Projects organize a single user's existing workspace. They do not add a new
filesystem sandbox between that user's projects. Hosted testers continue to
have separate private workspaces.

After a conversation finishes, an optional small model call proposes a short
title once a model request slot is free and no other request is waiting. It
works from a bounded excerpt of the first substantive message, has thinking
disabled and a separate 64-output-token ceiling; it does not consume a Proof
workflow's budget. Hosted provider usage
still counts against the user's allowance. A failed title leaves the prompt
preview and is reported in the terminal; a title entered manually in the conversation menu always takes
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
credential fields, recognizable secret patterns, long key-like strings after
words such as "key" or "token", and the exact values of the credentials the
server holds (the DeepInfra key, also when read from `DEEPINFRA_API_KEY_FILE`,
the interface token and the Slack delivery credentials) are redacted. You can inspect
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

**Working budgets.** New jobs use High effort. You can set numeric limits on attempts,
working time, tokens and external requests. Saved jobs retain their original allowances.

**Text is data, never instructions.** Manuscripts, papers, web pages and model
answers cannot change what the harness is allowed to do:

- writing a file needs your approval;
- research tools can consult online sources. Referees use public bibliographic queries to look up cited papers;
- generated Python runs in a locked-down sandbox. Unsandboxed Python exists
  only as an opt-in launch option (`--allow-python`): the
  Assistant and Explore chats may then propose `run_python`, and you approve
  each call after reading its code. Approved code runs with your account's
  permissions and the server's environment.

**Simple for mathematicians.** A conversation is the entry point. Mathematics is
typeset with LaTeX throughout the interface. Square Harness is a
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
- **Search** sits above the conversation list (Ctrl+K, or ⌘K on a Mac). It
  finds conversations whose title or messages contain your words, ignoring case
  and accents, shows the matching passage, and stays within the open project.
- **Copy** under an answer copies it as Markdown with its LaTeX source; code
  blocks have their own **Copy**. Selecting typeset mathematics and copying it
  gives its LaTeX between `$` signs.
- **While the Assistant works**, you can type your next message and **Queue** it:
  it starts when the current turn ends, and **Cancel** removes it.
- **Call a skill yourself** by typing `@` in the text box: `@prove`, `@check`,
  `@literature`, `@quick_review`, `@referee`, `@typo_check`, `@explain`,
  `@writeup`, `@summarize`, `@explore`, `@python_lab`, `@sanity_check`.
  Hover over the chip above the text box to read what the skill does. New work uses High effort, including automatic
  selections and jobs delegated by the Assistant. The former `@detailed_review` command still starts a Review.
  This is for users who know the skills: each one is built for a specific goal,
  while the Assistant divides a large task into jobs the skills can handle.
  A skill forced onto a larger task can fail in unexpected ways (for example,
  `@writeup` on a whole 80-page paper). The start page therefore does not list
  the skills; it points to **About**, which does.
- **Your workspace** is shown above the text box: the agent has access to the
  files in the active workspace. Click it to select another workspace, a folder
  below `--gui-root` (by default only the workspace itself). Other open tabs
  follow the new workspace and say so. Drop files onto the
  window to add them to the folder (`.tex .sty .cls .bib .md .txt .pdf`, code
  such as `.py .jl .rs .c .cpp .r .m .js .sh`, data in `.json .yaml .toml .csv`,
  and `.png .jpg` figures for write-ups; up to 20 MB). An existing file is never
  replaced: the new one is renamed. Empty files are refused, and so is a `.pdf`
  that is not a PDF (a LaTeX file named `.pdf`) or is cut short. Dropped files
  are attached to your next message. **Attach files** opens a menu: **From my
  device** uploads files the same way, and **From the workspace** opens a file
  browser on the files already in the folder, to attach some of them. **See my
  files**, at the bottom of the folder window, opens the same browser on all your
  folders (everything below `--gui-root`) and their text, PDF and image files,
  with their extension, size and modification date. Both open in the open folder
  and can go up to the others. The model reads only the open folder, so a file
  attached from another folder is first copied into the open folder (into the
  project's folder in a project's conversation), with the upload rules: same
  types and size limit, an existing file is never replaced, and attaching the
  same file again reuses its copy. The browser works like a desktop file
  manager: double-click a folder (or press Enter) to open it, go back with **Up**,
  Backspace or the folder path; click a file to select it, Ctrl-click (⌘ on a
  Mac) to add or remove one, Shift-click to select a range; sort by name, size or
  date; the search box searches every folder. Double-click a file to attach it to
  your next message. Point at a file to show **Download** and **View file**.
  **View file**, or a double click on a file attached above the text box or in a
  message, opens the file viewer beside the conversation, in the notebook's
  place (the notebook comes back when you close the file). Resize it by its left
  edge like the notebook, up to 75% of the window, and keep writing your message
  while you read. In a conversation answer, click a paper citation such as
  **draft.tex:42–48** to open the source at those lines. You can edit a matching
  workspace text file there. For a published paper or a manuscript that has
  since changed, the viewer shows the text the worker actually cited. The
  numbered editor preserves the edition’s line numbers and colors LaTeX
  formulas in arXiv passages.
  The file viewer shows a PDF in the browser's PDF viewer, fitted to the
  viewer's width, Markdown rendered with its mathematics, an image as is, and
  other text files as text, coloured as in VS Code for TeX, BibTeX, Python,
  Julia, Rust, JavaScript, C and C++, shell, JSON, TOML and YAML. Archives and
  other files are marked as not supported. The viewer can open and save text
  files up to the 20 MiB per-file upload allowance. To edit Markdown or text, click in the text
  (on a Markdown page, click the rendered text or **Edit**), type, then **Save**
  or Ctrl+S (⌘S on a Mac). The viewer's header holds these buttons, then
  **Download** (the file as last saved) and the close button, on one line. The file
  keeps its encoding. If the file changed on disk after you opened it (the model
  edited it, for example), it is not overwritten: reload it, or copy your text
  first. Text files (code, TeX, Markdown being edited) show line numbers in the
  margin. Closing the viewer, opening another file or leaving the conversation
  with unsaved changes asks first. Text files are best saved as UTF-8; older files in Latin-1 or
  Windows-1252 (accents saved by an older editor) are read too, in the encoding
  their `\usepackage[…]{inputenc}` declares when they declare one. UTF-16 files
  with a byte-order mark (as some Windows editors save them) are accepted and
  read as UTF-16; the confirmation says so. A file that
  cannot be read as text is named in the error so that you can remove it.
  Approved file edits use the replacement text's encoding declaration, or UTF-8
  if none is present. Changing a declaration to `utf8` also converts the saved
  bytes; characters unavailable in the requested encoding are refused before
  the file changes. Saved template copies use UTF-8, including UTF-16 originals.
- **Learn my writing style**, at the top of **Personalization**, teaches write-ups how you
  write (see [Writing styles](#writing-styles)).
- **Job pages** show live output, every attempt and review, sources, budgets and
  saved files. **Pause** saves at the next checkpoint and **Resume** continues with
  the remaining budget. When a proof or literature job stopped on a limit,
  **Resume with more budget…** lets you add output tokens, working time and (for
  a proof) attempts; the page says what will be added, and the addition is
  recorded in the job and its report. **Delete…** on a proof page moves an idle
  proof to the workspace trash after a confirmation. A paused conversation shows **Resume** while the text box
  is empty; type a new message instead to set the paused turn aside. **Stop**, beside
  **Pause** while a turn runs and beside **Resume** once it is paused, ends the turn
  for good: it cannot be resumed, the conversation is ready for a new message, and
  the jobs the turn started keep their saved work on their own pages. Stopping an
  investigation finishes it with a report on the evidence collected so far. While a turn
  is running you can queue one follow-up message: it starts when the turn ends
  (after a turn you paused, only once you resume or stop it), and you can cancel it in
  **Parallel work** before it starts. In an empty text box, the up arrow brings back
  the last message you sent, as in a terminal; the up and down arrows then walk
  through the earlier messages of the conversation (in a new conversation, those
  sent from this browser tab). **Esc** in the text box pauses the running turn. A
  message you have not sent yet is kept with its conversation while the browser tab
  stays open, so it is still there after a reload or a visit to another
  conversation (attachments are not kept). When work ends while you are looking at
  another tab, the tab's title says so (for example "∎ Proof finished") until you
  come back. "Back to
  conversation" returns to the chat that started the job. The standalone forms (New proof, New literature review, New review, New
  write-up) start a job without a conversation.
- **The sidebar footer** shows the model and its status, the theme and phone
  pairing, then four buttons:
  - **Parallel work** shows running and queued jobs, with controls to pause,
    stop or cancel them. Set how many jobs and model requests may run at once there
    (1–8 each; 2 and 2 by default). The values are saved and apply again after
    a restart, unless you launch with `--gui-concurrency` or
    `--gui-model-concurrency`. The button shows the count while work is
    running, and an animated square marks active conversations in the sidebar.
    A conversation turn starts at once and takes a job place only when it
    starts its first worker: a direct answer never waits behind long jobs, and
    a turn that needs a worker waits in the queue (in order of request) for a
    place; a turn you resume goes ahead of new work. Cancelling that wait
    pauses the turn, which keeps its work.
  - **Personalization** opens **Learn my writing style** and saves standing chat
    instructions and LaTeX commands across folders under the same GUI root. New chat turns and write-ups apply
    these preferences alongside the current request and any project notes.
  - **About** lists the skills with their `@` commands, explains which ones the
    Assistant can call itself, and warns that forcing a skill can lead to errors.
- **On a phone**: launch with `--gui-host 0.0.0.0` and open the printed address
  once; the phone stays paired. This is plain HTTP on your network: use it only
  on a trusted network, or through an SSH tunnel or a VPN.

In a new workspace the model may list, search and read TeX and BibTeX, PDF,
Markdown and text, code (Python, Julia, Rust, C and C++, R, MATLAB, JavaScript,
shell) and data files. Folders with saved file-type restrictions keep those
choices. In an older restricted folder the new code group requires explicit
permission through `POST /api/settings`. Explicitly attached files stay
available to their conversation and its fresh review. Ask the
Assistant to save text from the conversation ("save this as notes.tex") and it
shows the file and its diff for your approval; it does not replace an existing
file unless your message names it, and it never writes because a document or web
page asks. To compose a document, ask for a write-up. When approving
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
limit, the agent shows the failure and its partial text remains readable. A
worker that produced a document (a write-up, a summary, a literature review)
shows **View** and its downloads in its box once it is done: **View** opens the
document in the file viewer beside the conversation, like any file (its name on
top, then the document alone), and the downloads are the PDF, the sources, the
LaTeX file, the review or the BibTeX file it saved. Documents chosen in the
notebook's **Results** (a PDF, LaTeX or BibTeX file, a review, a figure) open in
the same viewer, which has more room than the notebook; closing it brings the
notebook back. Answers, proofs and experiments
are read in the notebook.

- **Agents** shows what each specialist is doing. From a Literature task,
  **Open literature review** takes you into the review itself.
- **Results** collects investigation reports, saved answers, proof attempts, reviews, experiments and
  Write-Up downloads.
- **Findings** shows results and sources available in this conversation or project.
  Work that has produced nothing yet (a proof still without a candidate, an
  answer that came back empty, a sandbox run that failed without printing
  anything) is not listed, nor is a run that the finished answer of its own
  exploration replaced by a later run. A run that failed or stopped at its time
  limit is labelled so, with what it printed before it stopped.
- **Plan** lists the research question, the steps and their dependencies in
  mathematical notation. Open **Time and usage** for the resource figures.

On a wide screen the notebook opens beside the conversation. A file opened in
the viewer takes its place until you close the file. Drag its divider
or focus the divider and use the arrow keys to resize it (End widens it fully).
It can cover up to 75% of the window; the conversation narrows to make room and
switches to compact margins and controls. On a smaller screen
the panel overlays the page. On a phone it fills the screen. Close it with
Escape or its close button. The URL identifies the tab and selected item, so
you can reload or share a link within the same authorized workspace.

### The terminal

`square-harness --workspace <folder>` opens a prompt. Proof is the default: type
a statement, or `/prove <statement>`. Other commands:

| Command | Purpose |
| --- | --- |
| `/explore` | Switch to exploration chat |
| `/check <result>` | Find or verify a reference using scholarly databases and cached sources. |
| `/literature <topic>` | Write a literature review of a topic |
| `/quick_review`, `/referee`, `/typo_check`, `/explain` | The review kinds (the manuscript comes from `--research-file`; `/detailed_review` is `/referee`) |
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
square-harness --workspace ~/research/my-paper --mode referee \
  --research-file paper.tex --prompt "Referee this paper." --output report.md
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
| Review | Referee report: referees with tools (Python, cited papers), an independent judge on every reported problem, a recommendation | `@referee`, New review | `/referee` |
| Typo check | The typos and presentation problems of a manuscript, each correction checked | `@typo_check`, New review | `/typo_check` |
| Explain | A result and its proof, a page or a section explained step by step | `@explain` | `/explain` |
| Write-up | Your notes or results as LaTeX in your own style | `@writeup`, `@summarize`, New write-up | `/writeup` |
| Python Lab | Your computations and plots, run in a sandbox | `@python_lab` | `/python-lab` |
| Sanity check | An attempt to falsify a claim numerically | `@sanity_check`, automatic | `/sanity-check` |

The Assistant and all workers use a sampling temperature of 1 by default, including
on retries. To run an experiment at a different temperature, pass `--temperature`
when starting Square Harness. Proof also accepts `--proof-verify-temperature` for
its verifier and restores its saved settings when resumed.

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
later turn. A run that stopped (at its time limit, on an error or at the memory
limit) is reported too, with its status and what it printed before it stopped.
A worker that writes nothing after its tool results is asked once
for its conclusion. If it still returns nothing, its card says that it did not
finish, with the reason, and no answer is saved for it. A worker that has used
all its tool rounds is asked for its results; a reply that only announces more
work is marked unfinished and keeps what its runs printed.

Before starting a Proof worker, the Assistant briefly prepares the task, with
either Qwen or DS4. Preparation allows at most two model calls, each capped at
2,048 generated tokens: 4,096 tokens in total. The first call may read relevant
context; the next must hand the task to Proof. Preparation input is limited to
16,384 tokens per call and 32,768 in total, using the backend's count or its
labelled estimate. Larger material goes to Proof without trimming the original
request or hypotheses. These limits apply to preparation, not Proof's context.
The model-written brief gives the precise
objective, definitions, conventions and any unresolved ambiguity. It may add
one short natural direction, clearly labelled as an unproved suggestion; it
must not begin the proof or add a missing hypothesis.

If preparation reaches its limit or fails to produce a usable delegation,
Proof starts from the original request and saved context instead of extending
the preparation. The same happens when the prepared task leaves out what your
request states, for example a statement taken from another conversation of the
project. Unfinished preparation is not adopted as a proof or task
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

When you select **@prove** (or DS4 sends your message to Proof) and the reviewer
finds no issue, the reply in the conversation is the reviewed text itself, under
one line saying whether it is a model-reviewed proof or a model-reviewed
refutation (counterexample); the Proof card says the same. Otherwise the
Assistant reports the result in its own words.

A request to prove that names no statement ("Prove the lemma.", with nothing in
the message, its files or the conversation to say which) gets a question asking
for the statement instead of a Proof job. When DS4 recognises a famous open
problem (for example the Riemann hypothesis), it proposes Proof for you to start: the reply says what is known about the problem, and a Proof card
below it states the time and tokens such a run may use. Select **Start** on the
card to run it anyway. Selecting **@prove** always starts Proof.

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

An investigation can combine Explore, Check, Literature, Proof and Quick review.
All new tasks use High effort. Workers and the writer share the available
output, input and working time. The plan can contain multiple Proof tasks, and
independent tasks can run together when the model server supports it.
Proof waits for the preceding steps listed in its plan. When the plan calls
for a special case, the controller selects a precise statement from the evidence
or uses the case proposed in the plan. A change in hypotheses gets its own statement and hash. Numerical
experiments are available through Explore when the numerical sandbox is enabled.
An investigation that explicitly asks for prior literature includes a
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
it depends on. These pins identify the workspace files the proof may use.

For a delegated proof, the solver and verifier receive the same pinned source
passages, including pages read during that turn. Earlier attempts and numerical
leads supplied through the research index go only to the solver. For a new
source-based or delegated proof, the app checks that the model has room for
a candidate and its review before starting the solver.

How it works: **solve → verify → repair**.

1. The solver may give a proof, construct a rigorous counterexample or identify
   the precise step it could not justify. It chooses how to investigate the
   statement. Either role can read cached papers, search scholarly sources online,
   and run an isolated Python experiment when the
   automatic numerical sandbox is available. Proof shares its job allowance across
   computations. Setting `sanity.max_runs` in the sandbox configuration adds an
   explicit run count across both roles, including after resume. A straightforward proof can finish
   in one solve and one review without using any of these tools.
2. A fresh verifier, which has not seen the solver's reasoning, reads the whole
   written proof. It
   avoids unnecessary repeated derivation of routine steps and
   concentrates effort on delicate implications, theorem hypotheses, quantifiers,
   domains, constant dependence and limits. It records the essential
   dependencies, distinguishing supplied facts, checked deductions, standard
   results and unresolved claims. A theorem name alone does not establish an
   essential input. The verifier chooses how to check the dependencies that
   determine validity. It can inspect a cited passage itself or reproduce a
   calculation.
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
   Later repairs can also receive earlier located objections when they fit;
   these remain fallible and are marked not reassessed, rather than assumed
   resolved because a later review omits them. The solver gives a short
   mathematical reason for repairing, rebutting or retaining an objection. The new
   version is reviewed afresh without this record or the earlier reviews.
4. An uncertain review stops the job. If the earlier answer had a concrete
   objection, the uncertain repaired answer becomes the export; the original
   and both reviews remain available.

Either role can request help with a specific reference or a broader literature
question. Proof saves the request and continues the same attempt when the
evidence returns. A missing reference can still leave the proof unresolved.
The time and output spent on this work count toward the job allowance. After
a role uses tools, Proof reserves output for writing and reviewing the answer.
An inconclusive reference check can return to the same role with the evidence
it obtained.

Both roles can search scholarly databases directly, open a paper and inspect the
relevant passage. A request to use only the attached manuscript also applies
to evidence gathered during that proof.

Every version is kept, and the first answer is never lost. The default is five solver attempts. You can change the attempt and time limits. “Response reviewed” means the verifier found
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

Square Harness checks a passing review in code before calling the response a
model-reviewed proof or refutation. A response that says the target is missing,
open or out of reach and whose review agrees is recorded as uncertain. A
claim that relies on executing code must be backed by the recorded experiment.
The verifier still has to check what the calculation establishes. Sampling a
range of values alone cannot prove a universal bound. A proof request with no statement at all (only
"@prove", with no earlier message or file) is refused before any solve. When the
Assistant prepares the task, the deliverable stays a proof or refutation of your
statement. If an explicit or routed Proof stops without a reviewed answer, the
Assistant reports why and what was saved, instead of writing its own proof.

If every solve stops at its output allowance before writing any text, the job
stops with an output-limit message rather than "attempts used up": start a new
proof with a larger solve allowance.

**Resume with more budget.** A proof that stopped on its allowance (tokens, time
or attempts) can continue with an explicit extra allowance: more output tokens,
seconds or attempts (`POST /api/proofs/<id>/resume` with `add_tokens`,
`add_seconds` or `add_rounds`; in the terminal, `--resume-add-tokens`,
`--resume-add-seconds` and `--resume-add-rounds` with `/resume`). Literature and
review jobs accept extra tokens and seconds the same way. Each addition is
recorded in the job and its report; nothing is added otherwise. Resuming a proof
that finished with a result is refused, as is resuming a proof that a running
conversation is using: pause the conversation first.

**Deleting a proof** (`DELETE /api/proofs/<id>`) moves its folder to
`.mathagent/trash/proofs/` in the workspace, named by its id and the deletion
time; to restore it, move it back into `.mathagent/proofs/` under its id alone. A running or queued proof cannot be deleted.

Paused jobs using recognized Proof policies 7 through 12 are upgraded to policy 13
on resume. The report records the old and new policy hashes; completed calls,
candidate versions and saved allowances are retained. Completed old jobs remain
readable without an upgrade.

### Explore

With `@explore`, attached text, BibTeX and extracted PDF text stay with that worker.
When the complete set fits the model request and the remaining job allowance, Explore
receives every file together. Larger sets are read in successive portions. The run
saves the next source position and its findings. On resuming, it checks that the
source still matches. The source record says which lines reached the model and gives
the next unread line and column if it stopped. A request to locate or state one
numbered result can finish as soon as Explore finds an exact supporting quotation
in the source. The answer includes the quotation, its locator and reading coverage.
Whole-document requests continue through the selected sources. The mathematical answer states which
proofs or claims the worker actually checked.

Ordinary mathematical chat with read access to your folder (`.tex`, `.bib`, PDF
text, …). **Explore** gives promising approaches early, with the first useful
step and the main obstacle for each. It labels heuristic steps and may run a
quick numerical sanity check. Use **Proof Check** to check a proof or **Fresh
review** to audit a previous answer. Saved Critique conversations can still be
read and continued as ordinary mathematical chats.

### Literature check

Use it for "a reference for the tubular neighbourhood theorem", or "is Theorem
9.26 of Brezis about this?".

How it works:

1. The model recalls up to three candidate books or papers and, for a book, the
   chapter or section where it remembers the result. The answer gives that place
   as memory ("By memory, I think it is in Chapter 3, Section 3.5"), never as
   checked.
2. The code checks that they exist in bibliographic indexes (zbMATH, Crossref,
   arXiv), and the model picks the records that fit the question. Only when none
   fits does the code search once more by topic.
3. For a precise request (an exact or numbered theorem, page or edition, a
   result's exact statement, or checking a given citation), the code opens
   open-access papers that cite the work and reads how they cite it, and the
   work itself when it is open (an arXiv paper). A statement read in the work is
   quoted in the answer. Your own words decide whether a check is precise, even
   when the Assistant words the request it passes on.

A DOI or arXiv identifier in your question is looked up first, in Crossref or
the DOI registry and in arXiv's own records (title, authors, journal reference,
comments, latest version, withdrawal notice): one request shows whether it
exists and which work it names. A work your question cites by authors and quoted
title ("Theorem 1 of T. E. Harris, 'A lower bound for …'") is always one of the
candidates checked. Links in the question are opened as given; a refusal (such
as arXiv's rule against automated source downloads) is reported as it is.

A pasted reference list or an attached `.bib` with `@check` is checked entry by
entry against the registries, without reading the papers: each entry is
reported as matching a record, matching with differences (year, authors,
title), naming a nonexistent identifier, or without a matching record. Entries
the request budget does not reach are listed as not checked.

For the latest papers by a named author, it searches arXiv's author field and
orders matching records by their original submission date. Revisions are listed
separately. The result lists the retrieved records and the query scope.
References whose requested scope is only partially supported remain labelled
as unconfirmed leads; rejecting a lead in a later check removes its recommendation.
A broad reference request checks relevance from indexed descriptions. Exact
theorems, pages and hypotheses require a precise check when you ask for them.

Each reference gets an evidence level:

- **read**: the statement was read in the work itself (a numbered or lettered
  place such as "Theorem 9.26" or "Theorem A");
- **cited**: another paper was read that cites the work at that place for that
  result;
- **located**: the work exists, but the exact place is unconfirmed;
- **contradicted**: a passage that was read, in the work or citing it there,
  attributes that place to another statement;
- **not found**: no evidence;
- **inconclusive**: no record was found while a source was failing, so the
  absence proves nothing.

The code lowers any level that the passages read do not support. A chapter,
section or theorem number the model remembers stays labelled as memory. If sources
are unreachable and cached records supply no evidence, the check reports an inconclusive result. A
question about the sources of a literature review shown in the conversation
uses that review's saved records and the full texts it cached.

### Literature review

Ask the mathematical question you want to investigate. You can specify an audience, a length, or a particular setting. Literature review opens with the problem and what the sources establish. It organises the remaining results around the differences that matter for the answer, such as competing hypotheses, a counterexample or a later improvement. It explains the decisive results in detail and situates the other works on the topic in a sentence each. A focused question normally calls for 600–1,500 words. A broad survey can use 3,000–4,000. A comparison table is optional. The chat shows the review itself and offers Markdown and BibTeX downloads: **Download review (.md)** is a standalone Markdown file (title, text with numbered citations, numbered reference list with DOI and arXiv links, mathematics as `$…$`), and **BibTeX (.bib)** holds the works the text cites, in citation order, with keys such as `erdos1947theory`.

The search uses zbMATH, arXiv, Crossref and Semantic Scholar. For a key paper available by DOI, it can use OpenAIRE to locate a deposited manuscript. It follows references from relevant works, including papers with different terminology. Search terms, claims in a supplied brief and works recalled by the model are leads to investigate. Each citation in the review points to a retrieved record. A dagger in the references identifies works for which the review used only an abstract or a bibliographic record. The other entries used passages from the paper. After them, **Further reading** lists works the plan placed in the review that the checked text does not discuss; the review makes no claim about them. A bibliography entry establishes the identity of a work but a precise theorem still depends on its hypotheses and on the passage supporting it.

Every sentence of a section is checked against the available evidence. A first pass names the source sentences that carry it, and a second compares it with those passages. A checker can correct a local error or withhold a sentence it cannot support. When a check fails, the export withholds unchecked claims and keeps the underlying source material accessible. A partial result identifies gaps, including a review based entirely on abstracts, an unavailable section check or an interrupted search. Neither a completed check nor a search that returned no later paper establishes that a question is open. When the question names an object after people (a "Boulard–Carillo conjecture") that no retrieved record mentions, the review's title and first paragraph say that no work defining or studying it was found, and the works it discusses are presented as nearby literature only. When the request excludes online research, the review identifies the cached sources it used; an arXiv rate limit (HTTP 429) is reported as such, with the advice to resume later. Open **Evidence** on the job page to inspect a numbered reference. Each entry links to the paper or registry and shows the saved source passage with its line range, the source-record abstract excerpt, or a bibliographic record. Papers already read appear there while the review runs.

When a model stream stops during screening, the review keeps finished classifications, continues with other candidates, and can retry the affected candidates in smaller groups. A request that the provider accepts without starting to answer is submitted again after 25 seconds, on every key. If the provider repeatedly fails, the review pauses. Before final selection, the Markdown and BibTeX downloads identify provisional references from the retrieved registry records. The token meter shows how much output the provider reported and how much of the allowance is held for calls with unknown usage.

New Literature reviews use High effort in the Assistant, the standalone form,
the terminal and the companion survey beside a Referee report. The companion
searches using its public question. It receives no manuscript attachments.
The review screens the candidates it collects for the question, ranks relevant
works, reuses cached papers and retrieves full texts while its reading time
remains. A follow-up reading pass considers works assigned to the outline.
An operator can set a source-request or evidence allowance. With the defaults,
source access continues until the workflow deadline, cancellation or another
configured budget stops it. Local profiles normally allow 20 minutes. Hosted
DS4 uses the workflow allowance described above. Saved reviews keep their
original search breadth and budget.
The job report shows the time spent at each step, including waits between
requests to arXiv.

### When the model provider is busy

With the hosted model (DS4 on DeepInfra), every mode and every key handles provider
failures the same way. DeepInfra sometimes refuses calls for minutes at a time because
the model is busy for all its customers ("Model busy, retry later"; on 26 November 2026
about a third of all calls, whatever the request). Such a refusal (HTTP 429 when the
model is busy or the provider limits requests for a moment, or HTTP 500, 502, 503 or 504
before answering) costs nothing, so the call keeps trying for up to five minutes from its
start, after pauses of about 2, 5, 15, then 30 seconds (or the longer wait the provider asks
for, up to two minutes), and says so in the conversation (the Assistant's answer) or on the
card of a worker or a literature review: "DeepInfra is busy (…); trying again in 15 s", at the
first refusal and then at most every 45 seconds. A few short side calls (a conversation title,
the position of a result in the literature) wait the same way without a notice.
The other calls of the server wait out that pause too, and that wait counts in the five
minutes. Only if the model is still busy after five minutes does the turn or job pause;
**Resume** then goes on with the saved work and the remaining budget. An HTTP 500 may come
from the request itself, so it is retried only four times, as before. Choosing a workflow
for a message waits at most about 20 seconds: if DeepInfra is still busy, the message is
answered directly (the Assistant can still start a workflow itself) and a notice says so. A request the provider
accepts but does not start to answer within about 30 seconds (longer for a long prompt) is
closed and sent again at once; the same "busy" error arriving at the start of an accepted
stream is sent again too. A stream that stalls or breaks in the middle of its answer is
sent again by a background job (Proof, the reviews, write-ups, literature checks), at
most twice; in a conversation, where the answer was already shown, it is not. These
replacements are at most four per logical call within its time allowance; refusals do not
count against them.

### Quick review, Review, Typo check, Explain

The review kinds work on a proof you paste, or on a manuscript you attach
(TeX gives the best results: formulas extracted from a PDF can be garbled).

- **Proof Check** (`@quick_review`): is this proof correct? It checks one result. Asked about a
  whole document with several proofs ("is this note correct?"), it checks nothing,
  lists the proofs and asks which one, or suggests a whole-paper Review; when it checks
  one result of a longer document, the report says which proofs were not checked.
  It needs a proof: statements or formulas without one are not judged (use
  `@sanity_check` to test a formula). Independent verifier passes read it
  through different lenses: line by line, adversarially, the results it uses.
  A new Proof Check uses the High profile. Its verifier passes examine distinct
  aspects of the proof, and a fresh call rechecks each alleged error. The verdict is *no issue found*, *issues found* or
  *uncertain*; *uncertain* always names the step that was not settled. Each issue
  quotes the exact proof lines and comes with a suggested repair. The proof itself
  is never rewritten. When the proof is in an attached file and the model has a
  large context (DS4), every pass also sees the whole file, so definitions and
  standing assumptions made elsewhere are in force.
- **Review** (`@referee`): a referee report on the whole paper, verdict first.
  It has one depth and no effort to choose. With DS4 (the whole paper fits the
  model's context), a **lead referee** and up to **five co-referees** work at the
  same time, each reading the whole paper and deciding itself what to check. The
  lead does what only a referee of the whole paper can do: it tries to break each
  main statement (degenerate parameters, low dimension, boundary cases, a known
  result it would contradict, with Python where a computation decides), checks
  the numerics and examples against the hypotheses of the results they
  illustrate, the claims of the abstract, introduction and remarks against what
  the paper proves and what the cited works say, and the constants and
  conventions across sections, and the paper's own citations: every place where
  it invokes one of its results ("by Lemma 2.4", a `\ref` or `\eqref` to a
  result) is listed by code, and each use is checked on two points, whether the
  cited result states exactly what is used there and whether its hypotheses
  hold there (a label that points nowhere, at another kind of result, or a
  circular use is flagged; "Lemma 3.3" written out counts only when no citation
  of another work governs its sentence, and a doubtful one is shown as such);
  the uses inside the proofs the focused readers read go to those readers, who
  check them first, the uses in a co-referee's group to that co-referee, and the
  rest (introduction, remarks, numerics, every flagged or doubtful use) to the
  lead; a problem reported at a use counts as its check. Each
  co-referee checks the proofs of one group
  (the main results' proofs, then the others by position in the paper) and
  reports anything else it notices. Beside them, from the start, **focused
  readers** read the proofs of the main results and of what they use (up to
  twelve proofs, a long one in parts) line by line, one reasoning call each,
  without seeing what the referees report. A reading cut at its output limit
  keeps the problems it had written, or writes its answer from its notes in one
  short call; its proof is then not counted as checked, and a reading that gave
  nothing is named under "What could not be checked". A co-referee that finishes early is
  asked once for a second look at the step of its group the main results depend
  on most, and a referee that stops without finishing is asked once to go on. They
  have tools: they read and search the manuscript, **run Python** in the sandbox
  to recompute a constant or test a counterexample, and find and read cited
  papers to check a theorem's hypotheses. The review rejects public queries that
  repeat eight consecutive manuscript words. Each problem is reported as soon as it is found, with
  its lines and an exact quote that the code checks, and every result of a
  referee's group must get a status (no error found, issue, not checked) before
  it can finish. A referee's first two rounds are short, so it starts with its
  tools instead of thinking for minutes; a referee can take back a report of
  its own that turns out not to be a problem. Every reported problem then goes
  to an **independent judge**,
  which sees the paper and the report but not the referee's reasoning, checks it
  (with Python or the cited paper when the claim rests on them, and always with
  tools for an error reported on a main result or on what a main result uses)
  and decides: confirmed, refuted or undecided, with its effect (the statement
  is false, the proof fails, a gap an expert can fill, an imprecision, a
  misprint). When another referee checked the same result and found no error,
  the judge sees both sides. The judge is there to keep false alarms out, never
  to make a real finding smaller: a gap whose repair needs a fact the paper
  neither proves nor cites (a density, regularity up to the boundary or the
  initial time, a limit, a uniform bound, a new lemma) is a failed proof, and a
  reported error the judge rates lower stays an error unless the judge writes
  the complete repair and it needs nothing new (then it is filed as a gap, with
  the judge's argument shown). Meanwhile the code compiles the TeX source (a
  figure that is not attached becomes a box) and checks the bibliography. The
  referees work within the job’s active-time allocation. The scheduler reserves
  the final portion for judgments and writing the report from the recorded findings. If the model
  provider refuses the calls (an expired credential or no credit), the review
  stops at once and the report is written from what was recorded.

  The report has, in order: the verdict (a recommendation that follows the
  judged findings by rule: a confirmed error, or an error on the main results
  that nobody settled, means a major revision; then one line per such error and
  a short paragraph written after the judge from the judged findings only; the
  code checks that it names every confirmed error and never calls a main result
  free of error against its status, and writes the paragraph itself otherwise),
  the main results with their status, computed by the code through the
  dependencies between the results (an error in its own proof, a result it uses
  with a confirmed error, an unsettled error, not fully checked, or no error
  found only when its proof and everything it uses were checked), how they were checked and the
  counterexamples tried, the errors (location, quote, the problem in at most four
  sentences, the fix, the results affected, how it was verified), gaps and
  imprecisions, questions to the authors (problems the judge could not decide,
  a rejection that two referees or an unrepeated computation dispute, and
  problems not judged, with the reason), formula misprints and wrong references
  by section, compilation and bibliography, what was verified by computation (a
  checklist, with the experiment ids, which `python -m mathagent.numerics show`
  opens) and what could not be checked, text replacements, and last, one line
  each, the problems a referee raised and the judge rejected, so that a wrong
  rejection can be caught. Whenever a referee and the judge disagree the report
  says so. An unfinished review (a referee stopped, problems not judged) and an
  error on the main results that nobody settled, and a main result not fully
  checked, make the recommendation provisional; the verdict then says what was not checked. It is rewritten as
  the review goes on. Language typos are left to the typo check, which you can start at the
  same time on the same file.

  The referees and judges run at once (up to the model-request limit in Settings,
  200 by default with DS4), also when the review is started in a
  conversation. If the allowance runs short, the referees and readers stop first,
  so that every reported problem is still judged. A review of a 3,000-line paper took about 14 minutes in a test.

  When the whole paper does not fit the model's context (a small local model),
  the review checks the proofs one by one instead, as described under "How it
  works" below.

  The referees can check works the manuscript cites. A separate literature search places its results in context.
  In a conversation, the Assistant starts **two jobs at once** when you ask for a
  review: the review and a High-effort literature review, each with its
  own card and page. The question of that literature review is written by the
  Assistant from the title, abstract and introduction of your manuscript, in
  public mathematical words; no manuscript text is pasted into it, and the
  manuscript is not handed to the search. When both have returned, the answer is
  the review's report as it is, followed by **Position in the literature**: for
  each main result, the closest earlier result the literature review found, what
  differs, and whether that changes the recommendation. This part is written
  only from the literature review's checked text and verified records; the
  references are printed by the code, a work with exactly your manuscript's
  title and one of its authors is labelled as the manuscript itself (your own
  preprint), and a work whose authors are all among yours (all but one for three
  or more authors) as a work of the manuscript's authors; neither counts as
  prior work. If the literature review
  failed or did not finish, the answer says that the position is missing; none
  is written from memory. The literature review shares the provider's
  throughput with the review, so the two together take longer than the review
  alone. A review started from the New review form or the terminal runs alone.
- **Typo check** (`@typo_check`): the typos and presentation problems of a
  manuscript, and nothing else. The paper is proofread in parts of about 220
  lines (each call with the whole paper in view) for typos, misprinted formulas,
  undefined symbols, wrong cross-references and unclear sentences; every
  suggested correction is then checked against the paper (a formula change with
  its computation; names and bibliography entries are never "corrected"). No
  proof is checked and there is no recommendation. About 16 calls and 2.5
  minutes with DS4 for a 3,000-line paper.

  How the Review works without the whole paper in context. The code maps the paper: its statements and proofs (theorem
  numbers come from your `.sty` and, when present, the `.aux` file), proofs that
  run over a whole section ("Proof of Theorem 1", "In this section we prove
  Theorem 1.2"), and which result uses which. A first call writes the overview,
  names the main results (those proved only in an appendix stay main) and locates
  the proofs the map missed. Then, first of all, the **statement** of each main
  result (four at most) is attacked by a reasoning call that sees the whole
  paper: degenerate parameters, low dimension, boundary cases, a known result the
  statement would contradict, the simplest example the hypotheses allow. A
  counterexample counts only when it is explicit and checked against every
  hypothesis; it then becomes a finding that the statement is false, which gets a
  second opinion like any other. A statement that fails only in an edge case is
  reported under the main-results table. All the calls run in one pool, the main results
  and what they use first: a check of each
  proof (a long proof line by line in parts, with the whole paper in view), and
  reads across the paper for inconsistent constants or conventions, claims never
  established and cited results applied outside their hypotheses. Each check
  reports every problem with its effect: the statement is false, the proof fails,
  a gap an expert can fill, an imprecision, or a misprint. As soon as a proof's
  checks are done, each of its findings gets a **second opinion**: a defence of the
  paper, then an independent judge. A finding said to make a statement false or a
  proof fail always goes to the judge, even when the defence gives way (the
  defence answers at once; the judge reasons); other findings go to the judge when
  the defence holds the paper right or makes the finding less serious. The
  judge's verdict is final. Minor findings that got no second opinion are checked against the paper, twenty
  per call: a finding the paper does not support leaves the report, and so does a
  finding whose quoted words are not in the paper (the scope at the end of the
  report counts both; the saved job keeps them).

  The report is **rewritten after every call**: within minutes it shows the
  overview, the first findings and the main-results table (with, for each main
  result, what the search for a counterexample gave), then a first prose
  report once the main results are checked and judged, and the final report at
  the end. It has a summary, the status of each main result (checked with no
  issue, affected by a comment and where it comes from, open, or not checked), an
  assessment, numbered major and minor comments (assembled by the code from the
  findings: the prose cannot add, drop or renumber one; the same problem found
  twice is one comment), questions to the authors, a
  recommendation and a confidential note to the editor. A major comment upheld
  after a defence of the paper rules out "accept" and "minor revision"; the
  recommendation is provisional while a main result rests on an unchecked proof or
  an open point. With a small local context the paper is read in parts and each
  check sees one proof with the statements and equations it cites (what that
  reading notices about the writing is listed in the report); the sweeps
  across the paper need a large context (DS4). On DS4 a review of a
  3,000-line paper makes several dozen calls, each reading the whole paper; how
  long it takes depends on how many run at once (the model-request limit in
  Settings, up to 200, DeepInfra's account limit; 200 by default with DS4).
- **Explain**: one result and its proof, or a page, a section or a short text,
  for a mathematician from another field: what it says, the idea, a walk through
  the proof or the passage (every line of a short one), the standard facts used,
  and where each hypothesis matters. An accuracy pass compares the explanation
  with the source. Name a result ("Theorem 4.4", found with its proof even in an
  appendix), a page of a PDF ("page 9"), a section ("Section 3", "Appendix A"),
  or say "explain this page" for a short attachment. When the text states a result
  in prose or proves it elsewhere, the model locates the lines first. When no
  file or message of the conversation holds the proof ("explain the proof of
  Brouwer's fixed point theorem"), the explanation comes from Explore instead.

Saved reviews stay readable whatever their age. A review saved before the
referees and the judge existed resumes with its own pipeline. Resuming one saved at Low effort
continues it as a typo check; one saved at Medium or High, or before the review
and the detailed review became one, continues as the review (it no longer reads
for typos, and keeps the remarks it had).

To discuss a completed report, select `@referee` again and explain which comments
you dispute. When the target is unambiguous, the follow-up reuses the reviewed
manuscript and keeps the original report. It addresses the contested comments
rather than silently replacing the earlier review; each answer is headed with the
comment's number in the saved report. Ask explicitly for a full new
review to reread the whole paper. New attachments take precedence; when the target
is ambiguous, the interface asks which manuscript to use. Review also asks when several manuscripts are attached and your message names
none of them. Explain asked for a section the document does not have lists the
sections and results it does have.

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
and keeps its equations, headings and references in source order. A character
pdfLaTeX cannot print becomes its math command (≤, α) or else `?` (such as the
replacement character a broken encoding leaves in a retrieved title); the reply
names it. The card
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
  template's macros and theorem environments; then your Personalization macros;
  then those of your saved style. Your Personalization's standing instructions
  reach the planner, the writer and the reviewers too, after the request.
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
   asks you which one to keep, with both versions. When the conversation has
   already settled it (a review found the statement false and gave the
   corrected one), the document uses the settled version and asks nothing.
   When you attach source material, Write-Up drafts from it. A possible gap
   flagged by the planner stays visible in the outline. The writer checks it
   against the source and marks a genuine gap in the draft. For long documents,
   the DeepInfra planner reads the whole attachment when the context and job
   budget allow. If it reads only the beginning, its suggestions cannot stop
   the job. The writer still receives the remaining material in later parts.
   A write-up based on a conversation can pause before drafting if a section
   depends on missing material. The Assistant is asked to check the relevant
   conversation or file passage, obtain what it can and request the write-up
   again. You can also choose **Write anyway** or **Stop** on the write-up page.
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
6. Then the document is reviewed, as Proof reviews a proof. A fresh check compares it with your material and request: a result,
   hypothesis or constant that changed, something left out, mathematics added
   where the text restates your notes, a generated exercise or solution that is
   wrong, a requirement not met. A style check reads it section by section
   against the guidelines, and each required part (an exam's header, an
   introduction's motivation…) is asked about on its own. Issues that can be
   fixed by rewording are revised one section at a time; a revision that
   changes a formula, loses a label, adds a citation or alters a statement is
   refused. Only a fidelity finding (the text differs from your material or
   request) may change the mathematics of a theorem, lemma or definition; a
   style or wording finding may reword a statement but never drop or change one
   of its formulas, and never deletes a formula elsewhere. When a fidelity fix
   changes a statement's mathematics, the reply says so and the report shows it
   before and after. A correction that would change a formula of your material is never
   made for you: it is put to you with both versions. Generated mathematics (an
   exercise, a solution, exam questions) is corrected when the check finds it
   wrong, and checked again; once a check has passed over it, it no longer
   counts as unchecked. The three kinds of checks are run in order as the job allows: first
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
Compilation failures are reported as unfinished drafts, with the compiler error
in plain words. When your template itself fails (a package or class that is not
installed, or an error in its preamble), no repair call is spent on the text:
the reply names the cause, for example the missing package, and what to change.
A write-up with no text under its title is never Ready. When the request is not
met as asked (pages, sections, parts it names), the reply says so first, with
what was produced; it also says when the proof it writes out comes from a Proof
job (model-reviewed, never certified), when work the request calls earlier is
not in the conversation, and which of two incompatible demands the document
followed.
In the web app, a template can be saved under a
name and reused in any folder.

### Writing styles

On **Learn my writing style**, drop your articles onto the page, or click the
drop area to select them from your computer. The files appear below it. You can
also **Choose from workspace** to use papers you uploaded earlier. Keep the
proposed name or type your own, and click **Learn my style**. One article is
enough to start. **More options** holds what most people leave alone: a LaTeX
template, a note about the articles, and the time and token limits. A style is
always learnt with the High limits; the form has no effort level.

PDFs with selectable text provide examples of phrasing and explanation. The
local PDF reader accepts files up to 10 MB.
For a scanned PDF, use an OCR copy with selectable text, the TeX source, or
screenshots of representative pages with a vision-capable model. Supply the
`.tex` files or a TeX source archive to teach exact macros and typesetting.
The articles are sorted into kinds of paper (by field and
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
writes it, even when the model dropped its backslashes. DS4 can read the whole
body of a `.tex` article up to the 1 MB limit. When an article's text exceeds
the model's available context, the learner takes representative passages and
names the article in the job's warnings. Choose main
article files, ideally two or more per kind of paper; `\input` parts are not
followed, so pin them as well (a pinned macro file counts). A style can also keep a LaTeX template (an
article's preamble, `.sty`, `.cls` or `.bib` files), which write-ups in that
style build on unless you attach one. An article whose text cannot be read
is left out and named in the job's warnings; the style is
learnt from the others. If no article or a template file cannot be read, the
job does not start and lists every such file.

While a style is being learnt, its page says what the work is doing ("Reading
article 2 of 3"), and you can leave it: the style is saved when the work ends.
**Show the model's output** unfolds the raw answer being written. When the work
ends, the page says under which name the style was saved and whether your
write-ups follow it, then shows the style guide. **Notes**, beside it, lists
the job's warnings; quotations the model gave that do not occur word for word
in your articles are only counted there, as they are left out by design.
**Usage** unfolds the token and time meters.

With the DS4.1 vision profile you can also choose screenshots of articles to
learn visible phrasing and layout. Exact macro and environment definitions
still require their `.tex` sources; they are not reconstructed from images.

Once a style is saved, the same page lists **Your styles**, where you choose
the style write-ups use:

- **Automatic**: a short call picks the learnt style that fits the material, or
  the default style when none fits.
- a learnt style; when it has several kinds of paper, **Kind of paper** keeps
  the one that fits each write-up or fixes one of them. **Read the style guide**
  shows what was learnt (with its mathematics typeset) and holds the style's
  LaTeX template. **Edit the guide** lets you correct what does not sound like
  you: change, remove or add items, untick a guideline the style should no
  longer set aside or a macro write-ups should not use. **Rename…** and
  **Delete…** are beside it; write-ups that follow a renamed style keep
  following it, and after a deletion they choose automatically. Write-ups
  already made are not affected.
- **Default style**: no rules of its own; write-ups follow the write-up
  guidelines as they are. It is always there, for instance when a learnt style
  turns out to copy habits you do not want.

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
  request: the approval shows the new limits, the run's stated purpose and the
  start of its code.
  A syntax error costs no run. Selected and routed Python Lab jobs can keep running useful experiments within the job budget. Set `lab.max_runs` to a positive integer for an explicit run allowance.
- **Sanity check** tries to falsify a claim with cheap experiments: small cases,
  boundaries, random sampling, minimising the margin. When the sandbox is
  installed and the call has room for its tools, it is offered automatically in Explore and the Assistant (5 s, 2 GB, 1 CPU, 3 runs
  per turn; `--no-numerical-checks` turns this off). The result is exactly one of *exact
  counterexample*, *numerically verified candidate*, *no counterexample detected*
  or *inconclusive*. Passing tests never count as a proof.
  Selecting **@sanity_check** allows further useful experiments within the job budget. Set `sanity.explicit_max_runs` for an explicit run allowance. A selected check without a classification is recorded as *unclassified*.
- **The Review** binds the sanity-check skill with its own policy, *review*:
  30 s, 2 GB and 1 CPU a run; 20 runs for the lead referee, 8 for each
  co-referee and 3 for each judge of one review; two runs at a time; no
  escalation. `--no-numerical-checks` turns it off too: the referees then compute
  by hand and the report says that no computation was run.

A run stopped at its time or memory limit keeps what it printed and the files it
saved before it stopped. Its record keeps the error, the limits, why it stopped
and how long it ran. At most two sandbox containers run at once
(`--python-concurrency`, 1–8); further runs wait their turn.

Change the limits with `--python-limits limits.json`. Values are bounded by
built-in maxima: for automatic checks 30 s, 4 GB, 2 CPUs and 10 runs; for anything
600 s, 32 GB and 4 CPUs.

```json
{"sanity": {"limits": {"timeout_seconds": 5, "memory_mb": 2048}, "max_runs": 3},
 "lab": {"defaults": {"memory_mb": 4096}, "ceiling": {"memory_mb": 16384}}}
```

The experiment record identifies a selected skill as `explicit`, a requested Python Lab run started by the Assistant as `routed`, and an automatic check as `automatic`.

Each run is saved under `.mathagent/experiments/<id>/`. At 256 MB the oldest healthy artifact directories rotate. The evidence index retains each new run’s full returned values, code, parameters, diagnostics and artifact descriptions. The binary figures and files require the native directory. Older retained records can contain explicitly shortened fields.
The model can page through a record using `read_experiment` and the returned `next_offset`. A structured `RESULT` that fits the per-run output allowance is returned in full. A larger one shares the available artifact bytes with plots and files. If the value exceeds both allowances, the record says so and the experiment can return it in separate runs.
Manage runs with `python -m mathagent.numerics list`,
`show <id>`, `rerun <id>` (same code, seed, parameters and limits) and `clean`
(remove stopped containers and those left by a server that is no longer
running; a live run of another interface is never touched), adding
`--workspace <folder>` to `list`, `show` and `rerun`. `--no-python-sandbox`
disables the sandbox entirely. The full numerical-result format requires image `square-harness/python-numerics:3`. The sandbox image is labelled with a digest of
its build files; after an update that changes them, the harness asks you to
rebuild it with `python -m mathagent.numerics build`.

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
3. **The model calls.** Each one has one task: for example, in a review, the
   check of one proof, with the whole paper in view. The answer comes back
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

High is the workflow profile for all new jobs, whether you start one in the
interface, in the terminal or through the Assistant. In Proof it allows five
solver attempts by default. On a local model the general High time allowance
is 30 minutes. Whole-manuscript Review and Typo check allow up to an hour by
default. Each mode uses its own working method. You can set numeric limits for a
particular job or launch. The model context still limits each request, and
research tools respect any explicit request and source-character budgets.

Resuming a saved job preserves its recorded numeric allowances, including historical
Low, Medium, Extra high and Poincaré jobs. The **Resume with more budget** action
records any explicit increase.

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
| `index/experiments/` | Experiment records retained when older sandbox artifacts are pruned |
| `experiments/` | Sandbox runs |
| `literature/` | Cache of downloaded sources |

The web app's access token is in the
`.mathagent/` folder of `--gui-root` (the workspace by default). Git ignores
`.mathagent/`, but a backup or a copied folder still contains your unpublished
work.

What leaves your computer:

- **Search requests**: short public queries to arXiv, Semantic Scholar, zbMATH,
  Crossref, OpenCitations and Open Library (book metadata); downloads of open-access papers; web pages you ask
  the Assistant to open; and, only with a `BRAVE_SEARCH_API_KEY`, general web
  search. In both the web app and terminal, research workers can consult online
  sources or reuse papers already cached in the workspace. Source-only tasks
  such as a typo check or a write-up use their supplied material.
- **Prompts and selected file contents** go to the model server you configure.
  Use `--allow-remote-model` to authorize a non-loopback endpoint.
  A model reached through an SSH tunnel receives its inputs on the
  remote machine even though the harness connects to a local address.

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
  Reviews follow `\input`/`\include` inside a TeX source archive only; a
  separate `.tex` file's inputs are reported as unread.
- Linux and macOS only; native Windows is not supported.
