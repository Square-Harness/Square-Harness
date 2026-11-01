# Optional chat, research and write-up workflows

These existing tools remain available alongside the focused
[proof workflow](usage.md). They do not participate in proof-mode benchmarks.

## Manuscript chat

```bash
square-harness --workspace ~/research/my-paper --mode critic \
  --prompt "Read manuscript.tex and identify the first unsupported implication."
```

Use `/explore` for approaches and heuristics, `/critic` for objections, and
`/review` for a fresh-context critique of the last chat answer. Chat can list,
read and search supported text files in the workspace (including `.sty` and
`.cls`); `read_file` also returns the locally extracted text of workspace PDFs.
Writes display a diff and require approval. Text excerpts are bounded; the model
must not treat an excerpt as the whole manuscript.

In ordinary terminal chat, `--predict` bounds output including thinking and
`--max-rounds` bounds tool rounds. Specialist workflows allocate output for each
phase after counting the complete request; they do not inherit the ordinary-chat
output limit. An optional `--specialist-output-tokens` value caps each specialist
call. `/think on|off` changes chat reasoning, and `/context N` changes the
context allocation for new work. `/clear` clears chat history; `/save session.json`
exports a transcript. Chat transcript import is not implemented.

## Literature and referee reports

Install PDF support when needed:

```bash
python -m pip install -e '.[research]'
```

For an online literature investigation:

```bash
square-harness --online --workspace ~/research/my-paper --mode literature \
  --prompt "Compare observability inequalities for strongly continuous semigroups. State the hypotheses and identify passages actually read." \
  --output literature-review.md
```

For a manuscript review, see [Reviews](#reviews-quick-check-manuscript-review-detailed-review-explanation) below.

Repeat `--research-file` for additional sources. These files are pinned on job
creation. `--output` is relative to the workspace and does not silently overwrite
an existing file. Without it, the report remains in the saved job directory.
An offline report can use local and cached sources only. External research
requires `--online`; model output cannot grant network access.

The bundled check, explain, literature, referee, review, review-quick and
writeup skills guide bounded investigation, report drafting and model review. They require source identifiers and passage
locations. Search hits and abstracts are leads, not evidence that a theorem
supports the requested assertion. PDF extraction can corrupt formulas; inspect
important passages in the original source.

Search tools support arXiv, Semantic Scholar and zbMATH; reading lists and
literature checks also use Crossref and the OpenCitations index. Public web
discovery uses Brave Search. Optional keys are `SEMANTIC_SCHOLAR_API_KEY` and
`BRAVE_SEARCH_API_KEY`. Do not place keys in prompts or
manuscripts. No key is required to begin with arXiv; service limits still apply.
Cached sources live under `.mathagent/literature/`.

## Reviews: quick check, manuscript review, detailed review, explanation

Review comes in four kinds. In the interface, type `@` in an Assistant message and
confirm `@quick_review`, `@referee`, `@detailed_review` or `@explain` (attach the file,
and name the result for a quick review or an explanation); the Assistant can also choose
them itself. In the terminal they are `/quick_review`, `/referee`, `/detailed_review` and
`/explain`.

An article overview, its main results or its authors' claimed contribution uses
Explore, with a source index and selected exact excerpts. Explain handles one
specified result and its proof. An overview must identify unread material and
distinguish the authors' novelty claims from independently checked prior work.
It is not a whole-paper proof audit. Explicit mode selections take precedence.
For a follow-up about the same article, a single previous user attachment can
be reused; a named file or new attachment takes precedence, and ambiguous
multiple attachments require a source choice.

- **Quick check** (`quick_review`): is this proof correct? Paste the statement and its
  proof, or pin a file and name the result (`Lemma 3.2`, a label such as `lem:compact`).
  Independent verifier passes read the proof through different lenses (line by line,
  adversarial, results used, overall structure); each alleged error is re-checked by a
  fresh call; the verdict is *no issue found*, *issues found* or *uncertain*, and every
  objection cites the proof lines with an exact quote. The proof is never rewritten; each
  issue comes with a suggested repair.
- **Review** (`referee`): a referee report on a pinned manuscript for understanding and
  presentation. The whole paper is read part by part: an overview (question, main results,
  proof strategy, how the sections fit, assumptions), part-by-part notes, and typos and
  presentation problems with corrections, then an assessment, a recommendation and a note
  to the editor. The proofs are not checked. From a PDF, only comments on words and
  sentences are kept, since its formulas are garbled; a TeX source gives the full check.
- **Detailed review** (`detailed_review`): the review, then the proofs are checked one at a
  time, main results first, as far as the effort allows, with twice the working
  time of a review. An explicit total token cap is not multiplied.
  The controller maps the manuscript (sections, numbered statements, their proofs, the
  results and equations each proof uses, its citations). Numbered major and minor comments
  on the proofs come from checked findings, with questions to the authors and a table of
  what was checked and what was not; when a main result's proof was not checked, the
  recommendation says it is provisional. Online, results the proofs cite are checked with
  the literature check and the contribution is compared with search results (titles and
  abstracts only).
- **Explanation** (`explain`): a precise explanation of a result and its proof for a
  mathematician from another field: what it says, the idea, a sentence-by-sentence
  walkthrough (every line of a short proof is covered), the standard facts used, and
  where each hypothesis matters. An accuracy pass checks it against the source.

```bash
square-harness --offline --workspace ~/research/my-paper --mode referee \
  --research-file manuscript.tex --prompt "Referee this manuscript." --output referee-report.md
square-harness --offline --workspace ~/research/my-paper --mode detailed_review \
  --research-file manuscript.tex --prompt "Check the proofs too." --output detailed-report.md
square-harness --offline --workspace ~/research/my-paper --mode quick_review \
  --research-file manuscript.tex --prompt "Lemma 3.2"
```

Effort sets the depth: the number of verifier passes for a quick check (1 at Low to 5 at
Poincaré, with re-checks from Medium), and for a detailed review how far down the
dependency graph the checks go (Low: main results; Medium: and what their proofs use;
High: every proof in the body; Extra high and Poincaré: appendices too, with two passes
on the main results). Each model call sees one proof with only what it needs, so small
contexts work; on a vLLM server (`--backend openai`) independent checks run
`--assistant-concurrency` at a time. Checks think before they answer when the output
ceiling leaves room. The manuscript map reads TeX best; for PDFs it relies on the text
extraction, whose formulas can be garbled.

The selected depth produces a finite saved plan of reading parts and proof or
explanation checks. A long proof can be split into source packets with its
statement, hypotheses and required dependencies. Completed usable parts are
reused on resume. An unusable explanation can receive a larger output allowance
or a smaller complete source packet, within bounded recovery; an unchanged
failed call is not retried indefinitely. If essential dependencies cannot fit,
the worker reports that limitation rather than silently removing them.

The interface shows finished and pending plan parts separately from source lines
read. These counts measure operational coverage, not correctness. A final partial
report names missing checks; it is not shown as a completed audit. A user pause
preserves pending work for resume, while a terminal failure or exhausted recovery
is reported as such instead of presenting an ineffective Resume action.

## LaTeX write-ups

The write-up workflow turns rough notes, a draft or a PDF into a LaTeX document in
your own style. It is exposition, not research: the model must keep every
statement, hypothesis and constant from the notes, add no results, and mark
unclear points with `% TODO:` comments for you to settle.

```bash
square-harness --workspace ~/research/my-paper --mode writeup \
  --research-file notes.md --template-file mymacros.sty --template-file template.tex \
  --output lemma.tex --prompt "A short section stating the lemma and its proof. Keep my notation."
```

Template files (`.sty`, `.cls`, `.tex`, `.bib`) are pinned like the notes. The
controller reads them in full and gives the model their macro definitions and
theorem environments to use; a template `.tex` contributes its preamble.

One writing call sees all the material at once, with your request, your
notation and a fitting saved style, and writes the whole document body. It
reasons first (Qwen's recommended sampling, temperature 0.6 and top-p 0.95; on a
server with a thinking budget, at most 8,192 of its 24,576 output tokens go to
reasoning). Only material longer than one call can read is written in
consecutive parts, each told what the previous parts covered; a part whose text
outgrows its call is written again as two halves. The controller builds the
preamble itself, adds macros the model defined (never over yours), and compiles
with `latexmk` in a temporary folder without shell escape and with TeX's
paranoid file access (`openin_any=p`, `openout_any=p`). A document that does not
compile gets one repair call on the whole text, told the errors and the lines
around them. There is no model review of the content: write up results you have
checked.

The `.tex` file (and the PDF when it compiled) is written to the workspace under
the `--output` name, or `<notes>-writeup.tex`; an existing file is never
replaced. A job is `complete` when it compiled and every part was written;
otherwise it is `partial`, with the reasons in its report. Resume and budgets
work as for literature reports. In the interface, template files can be
remembered under a name and reused in any folder.

### From a conversation: `@writeup` and `@summarize`

In an Assistant conversation, type `@writeup` (pick it from the menu, or type it out
in full followed by a space: the *Selected for this message* chip shows it is a
call) followed by what you want: for
example `@writeup a short note with Lemma 2 and the reviewed proof, for my
students`. The material is the conversation so far, as you saw it: your
messages, the answers and the full final text of each finished job (the
selected proof with its review status, reports, documents). Attach a draft
(`.tex`, `.md`, `.txt` or `.pdf`) to the same message to write it up as well.
Your instructions set the goal and the scope: unlike a write-up from notes, the
document need not cover every line, and superseded attempts are left out.

`@summarize`, or `@writeup` with nothing after it, writes `summary-<title>.tex`: each
important result with its exact statement, its status as recorded (a model
review is not a certificate), the key idea of its proof, the references found
and the open questions.

Both go through the same steps as other write-ups, with the conversation as
their material, but nothing is written into your folder: the reply is one line saying the document is ready,
and the card has **Download .tex** (and **Download PDF** when it compiled). The
document is kept with its job, under `.mathagent/research/`, until you delete the
conversation it was made in. No model pass assesses or restates it in the reply.

Notation you give in the request wins. Write the definitions in the message, for
example `@writeup the proof, with \newcommand{\dt}{\Delta t} and
\newcommand{\bbone}{\mathbf{1}}`: the model is told to use exactly these
commands, they go into the preamble, a saved style's macro for the same object
(same name or same meaning) is dropped, and a macro the model defines never
replaces them. Macros whose replacement is several tokens are defined with braces
(`{{\Delta t}}`), so `\tau^\dt`, `\sqrt\dt` and `\dt^{1/10}` mean what they say.
When the text uses a command nothing defines, compilation supplies it from a
saved style or from its name (`\bbP` as `\mathbb{P}`, `\rmd` as `\mathrm{d}`,
`\cL` as `\mathcal{L}`), and an undefined environment as a theorem-like one,
and lists them for you to check; other undefined commands are named to the
repair call.

**Style used by write-ups**, on the style page, decides which saved style
write-ups and summaries use: *Choose automatically* (the default), *None* (the
usual style of the field), a style (the model picks its domain that fits best),
or one domain of a style. It is kept in `.mathagent/style-choice.json` under the
interface root, like the styles. In the automatic setting, a short call first
picks the domain style that fits the material, or none: a style fits only when the material is the same kind
of mathematics. The writing call then gets that domain's explanation habits and
phrasings (not the structure of your papers: the request sets the structure) and
its macros, minus any that clash with yours; the macros the document uses join
the preamble. Without a fitting style, the model writes as a careful author in
the material's field would. The page shows which was used.

## Writing styles

In the interface, **Learn my writing style** (below *New conversation* in the
sidebar) opens a page that condenses the `.tex` sources of your earlier articles
into a style. If the open folder has no `.tex` files, the page asks you to drop a
few articles or to open the folder that holds them. Choose main article files,
ideally two or more for each kind of paper you write; `\input` parts are not
followed, so pin them as well.

The controller reads the articles exactly: macro definitions (`\newcommand`,
`\def`, `\DeclareMathOperator`, `\DeclarePairedDelimiter`) with how often each
article's body uses them, theorem environments, titles, abstracts and section
headings. Unused macros are dropped. The model then works in three small steps,
all from the pinned articles and never online: it sorts the articles by domain
(one call), describes how each article is written (one call per article, from
its opening, section openings, one proof and its end), and merges the articles
of each domain (one call per domain with more than one article). Quoted
phrasings are kept only when they occur verbatim in the articles.

A style is saved as `.mathagent/styles/<name>.json` under the interface root, so
every folder can use it. A new job with the same name replaces it. Its fields
are bounded, so one domain renders to about 2,000 characters
(`mathagent.style.style_prompt(profile, domain)`), and `style_sty` gives the
domain's macros as a `.sty`:

```json
{"format": "square-style", "version": 1, "name": "My papers",
 "articles": [{"path": "scheme.tex", "sha256": "…", "title": "…", "domain": "Numerical analysis"}],
 "macros": [{"name": "R", "definition": "\\newcommand{\\R}{\\mathbb{R}}", "uses": 42, "articles": ["scheme.tex"]}],
 "environments": [{"name": "lemma", "definition": "\\newtheorem{lemma}[theorem]{Lemma}", "uses": 9}],
 "domains": [{"name": "Numerical analysis", "description": "…", "articles": ["scheme.tex"],
              "aim": "…", "structure": ["Introduction: …"], "habits": ["…"], "phrases": ["We now turn to"]}]}
```

A style can also carry a LaTeX template: choose it when learning the style, or
later under **Saved styles** (*Choose template files*, *Remove the template*).
It is a `.tex` whose preamble write-ups should use (its document class,
packages, macros and theorem environments; one of your articles works, and its
title is replaced), and/or `.sty`, `.cls` and `.bib` files. The files are copied
into the style file (`"template": [{"name", "content"}]`), so it stays
self-contained; learning the style again keeps the template. A write-up that
picks the style builds on its template unless you gave the write-up a template
of your own, which always wins; the template's macros then count as template
notation (your request's notation still comes first).

The description is a model's reading of your style: open the file and correct
anything that does not sound like you. The job's report, articles and model
calls stay under `.mathagent/research/<id>/` like other jobs.

## Saved reports and limits

Reports live under `.mathagent/research/<id>/`, with state, `report.md` and
artifacts; write-ups are saved there too. Use `/researches`, `/research-report <id>`
and `/research-resume <id>` to inspect or continue. Inspection needs no running model. Ctrl+C saves progress;
resume uses the original remaining allowance. A normal resume never increases a
saved cap. Near the time limit, the interface explicitly offers **Resume (+N min)**,
which adds the displayed time allowance. The terminal equivalent is
`--resume-add-seconds <seconds>` when resuming a saved research job. Extra time
does not increase token or request caps.

| Option | Default |
| --- | ---: |
| `--research-rounds` | 3 for quick review and Explain, 6 otherwise |
| `--research-tokens` | no cumulative cap; optional generated-token limit |
| `--research-input-tokens` | no cumulative cap; optional cumulative input-token limit |
| `--research-seconds` | 900 seconds |
| `--research-requests` | Derived from the reading-list search plan, 12 otherwise |
| `--research-chars` | 30,000 returned evidence characters |

Explicit request caps accept 0 to 1,000 retrievals. For review variants, round
values 1, 3, 4, 6 and 10 select Low, Medium, High, Extra high and Poincaré effort.

Planning, research, drafting and review share working time and any explicit caps.
Effort sets depth and a finite work plan, not a fixed cumulative token allowance.
Explicit numeric caps and usage in older saved jobs remain unchanged on resume.
Per-call context capacity, phase output allowances and request timeouts still
apply when cumulative tokens are uncapped.

The interface's **Output** counter separates settled charged output from temporary
reservations. **Cumulative input tokens** includes repeated text across calls;
it is not context occupancy. **Current input context** is shown only when a
per-call measurement is recorded, and output also occupies that context.
Interrupted calls without usage reports retain their reserved charge.

A model-reviewed report is not a verified proof or an exhaustive literature survey. Failure to
find a result does not establish novelty.

## Permissions

Online queries and requested URLs leave the computer. An explicitly chosen
remote model server receives prompts and file excerpts. Keep unpublished or
confidential material out of external queries.

The optional `--allow-python` tool requires `--online` and per-snippet approval.
It runs as your account and is **not sandboxed**; workspace file-tool boundaries
do not restrict arbitrary Python. Install `.[math]` for NumPy/SymPy support and
inspect any snippet before approving it. Only the write-up workflow compiles
LaTeX, in the restricted `latexmk` run described above.
