// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useCallback, useMemo, useState, type ReactElement } from 'react'
import { api, LIST_PIPELINE, REVIEW_PIPELINE, type Evidence, type ResearchDetail, type ReviewCoverage } from '../api'
import { Collapse, ErrorNote, Loading, Meter, SourceLines, Tabs, UsageMeters, WorkProgressNote, useLoad } from '../components/common'
import { PauseIcon, PlayIcon } from '../components/Icons'
import { ResumeMore, StopTurn } from '../components/JobActions'
import { Markdown } from '../components/Markdown'
import { researchLook, researchResumable, Square, type Variant } from '../components/Square'
import { ago, count, firstLine } from '../format'
import { isPanelHidden } from '../panels'
import { go, href, useRoute } from '../router'
import { activeTasks, capacityFull, taskForJob } from '../concurrency'
import { useApp, useTick } from '../store'
import { Files } from './ProofView'
import { ActivityLog, ArtifactViewer, BusyNote, JobAside, StreamBody, roleInfo, useJobClass, useLiveStream } from './shared'
import { FIXED_VARIANTS, PIPELINE_STEPS, VARIANT_CAVEATS, VARIANT_NAMES, VARIANT_STEP_NAMES, VARIANT_STEPS, VERDICTS, resumeAdditionalSeconds } from '../review'

const STEPS = ['plan', 'investigate', 'draft', 'review', 'revise', 'done']
// A literature review: code searches and follows citations; the model screens the records,
// reads the key papers, plans and writes the review, and checks each section.
const REVIEW_STEPS = ['scope', 'search', 'screen', 'expand', 'rank', 'read', 'outline', 'write', 'check', 'assemble', 'done']
// Older literature jobs built a reading list: code searched and followed citations, the model screened and organised.
const LIST_STEPS = ['scope', 'sweep', 'graph', 'screen', 'organise', 'annotate', 'write', 'done']
const STEP_NAMES: Record<string, string> = {
  plan: 'Plan', investigate: 'Investigate', draft: 'Draft', review: 'Review', revise: 'Revise', done: 'Done',
  scope: 'Scope', sweep: 'Search', graph: 'Citations', screen: 'Screen', organise: 'Organise', annotate: 'Annotate', write: 'Write',
  search: 'Search', expand: 'Citations', rank: 'Select', read: 'Read', outline: 'Outline', check: 'Check', assemble: 'Bibliography',
}
// Coverage names the sources by key; show them as they write themselves.
const SOURCE_NAMES: Record<string, string> = {
  zbmath: 'zbMATH', arxiv: 'arXiv', crossref: 'Crossref', s2: 'Semantic Scholar', semantic_scholar: 'Semantic Scholar', semanticscholar: 'Semantic Scholar',
}
type Sources = { id: string; path: string; sha256: string; content: string }[]

export function ResearchView({ id, tab }: { id: string; tab: string | null }) {
  const tick = useTick(id)
  const app = useApp()
  const route = useRoute()
  const { data, error } = useLoad(() => api.research(id), [id, tick])
  const sources = useLoad(() => api.researchSources(id), [id, tick])
  const [artifact, setArtifact] = useState<string | null>(null)
  const [cite, setCite] = useState<string | null>(null)
  const active = tab || 'report'
  const jobClass = useJobClass()
  const task = taskForJob(app.snapshot, 'research', id)?.task || data?.owner_task || (data?.running && route.from ? activeTasks(app.snapshot).find((entry) => entry.task.kind === 'chat' && entry.task.target === route.from)?.task : undefined)
  const ours = !!data?.running && !!task && ['starting', 'running', 'pausing'].includes(task.state)

  const openCite = useCallback((ref: string) => {
    setCite(ref)
    if (isPanelHidden('aside') || window.matchMedia('(max-width: 1179px)').matches) go(data?.kind || 'literature', id, 'checks')
  }, [data?.kind, id])

  if (!data) {
    return <div className="sheet paper"><div className="sheet-inner">{error ? <ErrorNote>{error}</ErrorNote> : <Loading />}</div></div>
  }
  const flagged = flaggedCitations(data)
  const issues = [...new Set([...data.citation_issues, ...data.review_context_issues, ...data.warnings])]
  return (
    <div className={jobClass}>
      <div className="job-main sheet paper">
        <div className="sheet-inner">
          <ResearchHeader data={data} ours={ours} pausing={task?.state === 'pausing'} />
          <Tabs active={active} onChange={(next) => go(data.kind, id, next === 'report' ? null : next)} tabs={[
            { id: 'report', label: 'Report' },
            { id: 'checks', label: 'Checks', badge: issues.length || undefined, narrowOnly: true },
            { id: 'review', label: 'Model review', hidden: !data.review },
            { id: 'notes', label: 'Plan and notes', hidden: !!data.variant },
            { id: 'evidence', label: 'Evidence', badge: (data.review_evidence_count ?? data.evidence.length) || undefined },
            { id: 'sources', label: 'Sources', badge: data.sources.length || undefined },
            { id: 'files', label: 'Files', badge: data.artifacts.length || undefined },
          ]} />
          {active === 'report' && <Report data={data} flagged={flagged} issues={issues} onCite={openCite} />}
          {active === 'checks' && <div className="ledger-tab"><Checks data={data} sources={sources.data?.sources || null} issues={issues} cite={cite} onCite={setCite} /></div>}
          {active === 'review' && <Markdown>{data.review}</Markdown>}
          {active === 'notes' && <Notes data={data} />}
          {active === 'evidence' && (data.pipeline === REVIEW_PIPELINE ? <ReviewEvidenceList key={data.id} data={data} /> : <EvidenceList evidence={data.evidence} />)}
          {active === 'sources' && <>{sources.error ? <><ErrorNote>{sources.error}</ErrorNote><button type="button" className="btn" onClick={sources.reload}>Retry loading sources</button></> : <SourcesTab data={data} sources={sources.data?.sources || null} />}</>}
          {active === 'files' && <Files names={data.artifacts} onOpen={setArtifact} />}
        </div>
      </div>
      <JobAside label="Checks">
        <Checks data={data} sources={sources.data?.sources || null} issues={issues} cite={cite} onCite={setCite} />
      </JobAside>
      {artifact && <ArtifactViewer job="research" id={id} name={artifact} pipeline={data.pipeline} onClose={() => setArtifact(null)} />}
    </div>
  )
}

export function flaggedCitations(data: ResearchDetail): Set<string> {
  const refs = new Set<string>()
  for (const issue of [...data.citation_issues, ...data.review_context_issues]) {
    const match = issue.match(/\[([^\]]+)\]/)
    if (match) refs.add(match[1])
  }
  return refs
}

function ResearchHeader({ data, ours, pausing }: { data: ResearchDetail; ours: boolean; pausing: boolean }) {
  const look = researchLook(data.status, data.running, data.kind)
  const app = useApp()
  const [error, setError] = useState('')
  const busy = capacityFull(app.snapshot, app.status)
  const route = useRoute()
  const ownTask = taskForJob(app.snapshot, 'research', data.id)?.task || data.owner_task || (data.running && route.from ? activeTasks(app.snapshot).find((entry) => entry.task.kind === 'chat' && entry.task.target === route.from)?.task : undefined)
  const queued = app.snapshot.queue.some((item) => item.target === data.id)
  const additionalSeconds = resumeAdditionalSeconds(data.budget.seconds)
  const additionalMinutes = additionalSeconds / 60
  const extensionLabel = Number.isInteger(additionalMinutes) ? additionalMinutes : additionalMinutes.toFixed(1)
  const resumable = (data.progress?.resumable ?? researchResumable(data.status)) || (data.status === 'budget_exhausted' && additionalSeconds > 0)
  const act = (action: Promise<unknown>) => { setError(''); action.catch((reason: Error) => setError(reason.message)) }
  const steps = data.variant ? (data.pipeline && PIPELINE_STEPS[data.pipeline]) || VARIANT_STEPS[data.variant] : data.pipeline === REVIEW_PIPELINE ? REVIEW_STEPS : data.pipeline ? LIST_STEPS : STEPS
  const names = data.variant ? VARIANT_STEP_NAMES : STEP_NAMES
  const current = steps.indexOf(data.phase === 'plan' && data.pipeline && !data.variant ? 'scope' : data.phase)
  return (
    <header className="job-head">
      <div className="job-status">
        <Square variant={look.variant} size={18} label={look.label} />
        <span className="job-status-label">{look.label}</span>
        <span className="job-stop">{data.variant ? VARIANT_NAMES[data.variant] : data.kind === 'referee' ? 'Review' : data.pipeline === REVIEW_PIPELINE ? 'Literature review' : data.pipeline ? 'Reading list' : 'Literature report'}</span>
      </div>
      <div className="job-goal"><Markdown>{data.goal}</Markdown></div>
      <ol className="phases phases-wide" aria-label="Workflow">
        {steps.map((step, index) => {
          const variant: Variant = index < current || data.phase === 'done' ? 'complete' : index === current ? (data.running ? 'running' : 'paused') : 'ready'
          return <li key={step} className={'phase phase-' + variant}><Square variant={variant} size={10} />{data.variant === 'explain' && step === 'check' ? 'Accuracy' : names[step]}</li>
        })}
      </ol>
      <WorkProgressNote progress={data.progress} status={data.status} running={data.running} pipeline={data.pipeline} taskId={ownTask?.id} />
      <details className="job-settings-note">
        <summary>Run details</summary>
        <div className="job-facts">
          {data.sources.map((source) => <span key={source.id} className="fact-file">{source.path === 'pasted-text.md' ? 'pasted text' : source.path}</span>)}
          {data.unit && <span>{data.unit}</span>}
          {data.level && !(data.variant && FIXED_VARIANTS.includes(data.variant)) && <span>{data.level === 'poincare' ? 'Poincaré' : data.level === 'xhigh' ? 'Extra high' : data.level.charAt(0).toUpperCase() + data.level.slice(1)} effort</span>}
          <span>{String(data.settings.model)}</span>
          <span>Started {ago(data.created_at)}</span>
        </div>
      </details>
      {data.stop_reason && !data.running && <p className="muted small">{data.stop_reason}</p>}
      <div className="job-actions">
        {ours ? (
          <button type="button" className="btn" disabled={pausing} onClick={() => act(api.pause(ownTask!.id))}>{pausing ? <PlayIcon size={16} /> : <PauseIcon size={16} />} {pausing ? 'Pausing…' : 'Pause'}</button>
        ) : resumable ? (
          <button type="button" className="btn btn-primary" disabled={queued} onClick={() => act(api.resumeResearch(data.id, true, additionalSeconds))}>
            <PlayIcon size={16} /> {queued ? 'Waiting in the queue' : additionalSeconds ? `${busy ? 'Queue resume' : 'Resume'} (+${extensionLabel} min)` : busy ? 'Queue resume' : 'Resume saved work'}
          </button>
        ) : null}
        {ours && <StopTurn task={ownTask} onError={setError} />}
        {!ours && !data.running && (resumable || data.status === 'budget_exhausted') && data.kind !== 'style' &&
          <ResumeMore kind="research" id={data.id} disabled={queued} tokenStep={100_000} usage={{ tokens: data.budget.tokens, seconds: data.budget.seconds }} />}
        {data.running && (ownTask?.kind === 'chat' || route.from) && <span className="muted">Running for the Assistant in <a href={href('free', ownTask?.kind === 'chat' ? ownTask.target : route.from)}>{app.chats?.find((chat) => chat.id === (ownTask?.target || route.from))?.title || 'the conversation'}</a>.</span>}
        {data.running && !ours && !route.from && ownTask?.kind !== 'chat' && <span className="muted">Running in another process, such as a terminal.</span>}
      </div>
      {!ours && busy && resumable && !queued && <BusyNote queue />}
      <ErrorNote>{error}</ErrorNote>
    </header>
  )
}

export function Report({ data, flagged, issues, onCite }: { data: ResearchDetail; flagged: Set<string>; issues: string[]; onCite: (ref: string) => void }) {
  return (
    <div className={data.pipeline === REVIEW_PIPELINE ? 'report literature-review' : 'report'}>
      {data.running && data.live && <LiveResearch id={data.id} role={data.live.role} file={data.live.file} pipeline={data.pipeline} owner={data.owner_task} />}
      {issues.length > 0 && (
        <div className="checks-note">
          <p className="objection-label">{data.variant ? 'Notes from the controller' : data.pipeline ? 'The self-check found gaps' : 'The controller could not confirm every citation'}</p>
          <ul>{issues.slice(0, 6).map((issue) => <li key={issue}>{issue}</li>)}</ul>
          {issues.length > 6 && <p className="small">{issues.length - 6} more in Checks.</p>}
        </div>
      )}
      {data.variant === 'quick' && data.verdict && VERDICTS[data.verdict] && (
        <p className={'verdict verdict-' + VERDICTS[data.verdict].variant}>
          <Square variant={VERDICTS[data.verdict].variant} size={20} /> {VERDICTS[data.verdict].label}
        </p>
      )}
      {(data.draft || data.report) ? (
        <>
          <p className="caveat">{data.pipeline === 'review-v1' ? 'This earlier review assessed the presentation and gave an overview of the manuscript. Its workflow did not check the proofs.' : data.variant ? VARIANT_CAVEATS[data.variant] : data.pipeline === REVIEW_PIPELINE
            ? 'Every reference comes from a returned bibliographic record. Statements of results come from abstracts and, where marked, from full texts; this is a model-written survey to check against the papers.'
            : data.pipeline
            ? 'Every entry is a record returned by Crossref, arXiv, zbMATH or Semantic Scholar, with its identifier as returned. The model chose, grouped and annotated them from titles and abstracts.'
            : 'Model draft. Citations link to the passages the harness recorded; red ones cite lines that were never read.'}</p>
          {(data.outputs?.review || data.outputs?.bib) && (
            <div className="outputs">
              {data.outputs.review && <a className="btn btn-small btn-primary" href={api.reviewMarkdownUrl(data.id)} download>{data.status === 'complete' ? 'Download review (.md)' : 'Download partial review (.md)'}</a>}
              {data.outputs.bib && <a className={'btn btn-small' + (data.outputs.review ? '' : ' btn-primary')} href={data.outputs.review ? api.reviewBibtexUrl(data.id) : api.researchBibDownloadUrl(data.id)} download>BibTeX (.bib)</a>}
            </div>
          )}
          {data.coverage && <p className="caveat">{coverageText(data.coverage)}</p>}
          <Markdown className="report-text" onCite={onCite} flagged={flagged}>{data.draft || data.report}</Markdown>
        </>
      ) : (
        <p className="empty-note">No draft yet. {data.running
          ? (data.pipeline === REVIEW_PIPELINE ? 'The review is written once the key papers are read and the sections planned.' : data.pipeline ? 'The list is written once the candidates are screened and organised.' : 'The draft follows the investigation.')
          : 'The plan and working notes are under Plan and notes.'}</p>
      )}
    </div>
  )
}

/** One line on what a literature review searched and kept, and which sources failed. */
function coverageText(coverage: ReviewCoverage) {
  const kept = `Coverage: ${count(coverage.candidates ?? 0)} candidate records, ${count(coverage.screened ?? 0)} screened, ${count(coverage.selected ?? 0)} selected, ${count(coverage.cited ?? 0)} cited, ${count(coverage.full_text ?? 0)} with full-text passages consulted.`
  const failed = Object.entries(coverage.sources || {})
    .filter(([, source]) => source.answered < source.asked || source.errors?.length)
    .map(([key, source]) => `${SOURCE_NAMES[key.toLowerCase()] || key} (${source.answered < source.asked
      ? `${count(source.asked - source.answered)} of ${count(source.asked)} queries unanswered`
      : source.errors.length === 1 ? 'one error' : `${count(source.errors.length)} errors`})`)
  return failed.length ? `${kept} Sources that failed: ${failed.join(', ')}.` : kept
}

function LiveResearch({ id, role, file, pipeline, owner }: { id: string; role: string; file: string; pipeline?: string | null; owner?: ResearchDetail['owner_task'] }) {
  const stream = useLiveStream('research', id, file)
  const app = useApp()
  const task = taskForJob(app.snapshot, 'research', id)?.task || owner
  const ours = !!task
  return (
    <div className="live" id="live">
      <p className="live-head"><Square variant="running" size={14} /> {role === 'scope' && pipeline === LIST_PIPELINE ? 'Defining the scope of the reading list' : roleInfo(role, pipeline)?.active || role}</p>
      {ours && <ActivityLog items={activeTasks(app.snapshot).find((entry) => entry.task.id === task?.id)?.activity || []} />}
      <StreamBody stream={stream} role={role} pipeline={pipeline} live />
    </div>
  )
}

function Checks({ data, sources, issues, cite, onCite }: {
  data: ResearchDetail; sources: Sources | null; issues: string[]; cite: string | null; onCite: (ref: string | null) => void
}) {
  const b = data.budget
  return (
    <div className="ledger">
      {cite && <Passage refText={cite} data={data} sources={sources} onClose={() => onCite(null)} />}
      <section className="aside-section">
        <h3 className="aside-title">Citation checks</h3>
        {issues.length ? (
          <ul className="issue-list">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
        ) : (
          <p className="muted small">{data.pipeline === REVIEW_PIPELINE ? 'Every reference comes from a returned bibliographic record. Statements of results come from abstracts and, where marked, from full texts; this is a model-written survey to check against the papers.'
            : data.pipeline === LIST_PIPELINE ? 'Entries are checked against returned bibliographic records. This does not verify their mathematical claims or full text.' : data.draft ? 'No invalid source locators were detected. This checks locations, not mathematical correctness.' : 'Checks run when the draft is written.'}</p>
        )}
      </section>
      {data.sources.length > 0 && (
        <section className="aside-section">
        <h3 className="aside-title">Source reading</h3>
        <p className="muted small">Lines supplied to the model. This does not measure proof verification.</p>
          {data.sources.map((source) => (
            <Coverage key={source.id} id={source.id} path={source.path} lines={source.lines} ranges={data.manuscript_ranges[source.id] || []} />
          ))}
        </section>
      )}
      <section className="aside-section">
        <h3 className="aside-title">Usage</h3>
        <UsageMeters budget={b} />
        {!data.variant && <Meter label={data.pipeline ? "Model calls" : "Rounds"} used={b.rounds.used} limit={b.rounds.limit} />}
        {(!data.variant || data.variant === 'journal' || data.variant === 'review') && <Meter label="Web requests" used={b.requests.used} limit={b.requests.limit} />}
        {data.pipeline !== REVIEW_PIPELINE && (!data.variant || data.variant === 'journal' || data.variant === 'review') && <Meter label="Evidence characters" used={b.chars.used} limit={b.chars.limit} />}
      </section>
    </div>
  )
}

export function Coverage({ id, path, lines, ranges }: { id: string; path: string; lines: number; ranges: [number, number][] }) {
  const read = new Set<number>()
  ranges.forEach(([a, b]) => { for (let n = a; n <= Math.min(b, lines); n++) read.add(n) })
  return (
    <div className="coverage">
      <div className="coverage-text"><span>{id} {path}</span><span className="muted">{count(read.size)} of {count(lines)} lines read</span></div>
      <svg className="coverage-strip" viewBox={`0 0 ${Math.max(lines, 1)} 1`} preserveAspectRatio="none" role="img"
        aria-label={`${read.size} of ${lines} lines read`}>
        <rect x="0" y="0" width={Math.max(lines, 1)} height="1" className="coverage-track" />
        {ranges.map(([a, b], index) => <rect key={index} x={a - 1} y="0" width={Math.max(1, Math.min(b, lines) - a + 1)} height="1" className="coverage-read" />)}
      </svg>
    </div>
  )
}

export function Passage({ refText, data, sources, onClose }: { refText: string; data: ResearchDetail; sources: Sources | null; onClose: () => void }) {
  const [source, range] = refText.split(':L')
  const [start, end] = range ? range.split(/-L?/).map(Number) : [NaN, NaN]
  const last = Number.isFinite(end) ? end : start
  const manuscript = data.sources.find((item) => item.id === source)
  const readRanges = data.manuscript_ranges[source] || []
  const unread = Number.isFinite(start) ? Array.from({ length: Math.max(0, last - start + 1) }, (_, i) => start + i)
    .filter((n) => !readRanges.some(([a, b]) => n >= a && n <= b)) : []
  const passages = useMemo(() => findPassages(data.evidence, source, start, last), [data.evidence, source, start, last])
  return (
    <section className="aside-section passage">
      <div className="passage-head">
        <h3 className="aside-title">{refText}</h3>
        <button type="button" className="link-btn" onClick={onClose}>Close</button>
      </div>
      {manuscript ? (
        <>
          <p className="muted small">{manuscript.path}</p>
          {unread.length > 0 && <p className="objection small">The model cited {unread.length === 1 ? 'a line' : 'lines'} it never read: {firstLine(unread.join(', '), 80)}.</p>}
          {!sources && <Loading />}
          {sources && (() => {
            const text = sources.find((item) => item.id === source)?.content || ''
            const all = text.split('\n')
            const from = Number.isFinite(start) ? Math.max(1, start) : 1
            const to = Number.isFinite(start) ? Math.min(all.length, last) : Math.min(all.length, 40)
            return <SourceLines content={all.slice(from - 1, to).join('\n')} start={from} highlight={readRanges} />
          })()}
        </>
      ) : passages.length ? (
        passages.map((passage, index) => (
          <div key={index} className="passage-quote">
            <p className="muted small">{passage.label}</p>
            <pre className="source">{passage.text}</pre>
          </div>
        ))
      ) : (
        <p className="objection small">No saved passage covers this citation. It may cite search metadata only, or lines that were never read.</p>
      )}
    </section>
  )
}

function findPassages(evidence: Evidence[], source: string, start: number, end: number) {
  const found: { label: string; text: string }[] = []
  for (const item of evidence) {
    let value: Record<string, unknown>
    try { value = JSON.parse(item.result) } catch { continue }
    if (value.document_id !== source) continue
    const a = value.start_line as number | undefined
    const b = value.end_line as number | undefined
    if (typeof value.passage === 'string' && (!Number.isFinite(start) || (a !== undefined && b !== undefined && a <= end && b >= start))) {
      found.push({ label: `${item.id}: ${item.tool}, lines ${a}–${b}`, text: value.passage })
    }
    for (const match of (value.matches as { line: number; passage: string }[] | undefined) || []) {
      if (!Number.isFinite(start) || (match.line >= start && match.line <= end)) found.push({ label: `${item.id}: search hit, line ${match.line}`, text: match.passage })
    }
  }
  return found
}

function Notes({ data }: { data: ResearchDetail }) {
  return (
    <div className="notes">
      <section>
        <h3 className="section-title">{data.pipeline ? 'Scope' : 'Plan'}</h3>
        {data.plan ? <Markdown>{data.plan}</Markdown> : <p className="empty-note">No plan yet.</p>}
      </section>
      {data.notes.filter((note) => note.text.trim()).map((note, index) => (
        <section key={index}>
          <h3 className="section-title">{note.title || `Working note, round ${note.round}`}{note.complete ? '' : ', unfinished'}</h3>
          <Markdown>{note.text}</Markdown>
        </section>
      ))}
      {!data.notes.some((note) => note.text.trim()) && <p className="muted small">The investigation rounds left no written notes; their tool calls are under Evidence.</p>}
    </div>
  )
}

function ReviewEvidenceList({ data }: { data: ResearchDetail }) {
  const evidence = useLoad(() => api.reviewEvidence(data.id), [data.id, data.updated_at])
  if (evidence.error) return <ErrorNote>{evidence.error}</ErrorNote>
  if (!evidence.data) return <Loading />
  if (!evidence.data.items.length) return <p className="empty-note">{data.phase === 'done' ? 'This review contains no cited source.' : 'Source passages and abstracts appear as the review reads papers.'}</p>
  return (
    <div className="evidence">
      {evidence.data.items.map((item) => {
        const kind = item.passages.length ? 'Paper passage' + (item.passages.length === 1 ? '' : 's') : item.abstract ? 'Abstract' : item.review ? 'zbMATH review' : 'Bibliographic record'
        const links = [
          item.doi && ['DOI', 'https://doi.org/' + encodeURIComponent(item.doi).replace(/%2F/gi, '/')],
          item.arxiv && ['arXiv', 'https://arxiv.org/abs/' + encodeURIComponent(item.arxiv).replace(/%2F/gi, '/')],
          item.zbl && ['zbMATH', 'https://zbmath.org/?q=an:' + encodeURIComponent(item.zbl)],
          item.s2 && !item.doi && !item.arxiv && !item.zbl && ['Semantic Scholar', 'https://www.semanticscholar.org/paper/' + encodeURIComponent(item.s2)],
        ].filter((link): link is string[] => Array.isArray(link))
        return (
          <Collapse key={item.n} summary={<><strong>{item.k ? `[${item.k}] ` : ''}{item.title}</strong> <span className="muted small">{kind}</span></>}>
            {item.passages.map((passage) => <div className="passage-quote" key={passage.lines + passage.locator}>
              <span className="muted small">{passage.locator && passage.locator.replace(/\.$/, '') + ' · '}{passage.lines.replace(/^L(\d+)-L(\d+)$/, 'Lines $1–$2')}</span>
              <pre className="source">{passage.text}</pre>
            </div>)}
            {item.abstract && <div className="passage-quote"><span className="muted small">Abstract excerpt from the source record</span><pre className="source">{item.abstract}</pre></div>}
            {item.review && <div className="passage-quote"><span className="muted small">zbMATH review from the source record</span><pre className="source">{item.review}</pre></div>}
            {!item.passages.length && !item.abstract && !item.review && <p className="muted small">Bibliographic record only: no source passage, abstract or review was saved.</p>}
            {links.length > 0 && <p className="small">{links.map(([label, url], i) => <span key={label}>{i > 0 && ' · '}<a href={url} target="_blank" rel="noreferrer">{label}</a></span>)}</p>}
          </Collapse>
        )
      })}
    </div>
  )
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return <p className="empty-note">No source was read yet. Evidence appears as the investigation calls its tools.</p>
  return (
    <div className="evidence">
      {evidence.map((item) => {
        let body: ReactElement
        try {
          const value = JSON.parse(item.result)
          if (Array.isArray(value.lines)) body = <SourceLines content={value.lines.map((line: { text: string }) => line.text).join('\n')} start={value.start_line} />
          else if (typeof value.passage === 'string') body = <pre className="source">{value.passage}</pre>
          else if (value.error) body = <p className="objection small">{String(value.error)}</p>
          else body = <pre className="plain">{JSON.stringify(value, null, 2)}</pre>
        } catch {
          body = <pre className="plain">{item.result}</pre>
        }
        return (
          <Collapse key={item.id} summary={<><strong>{item.id}</strong> <span className="tool-line">{item.tool}</span> <span className="muted">{firstLine(JSON.stringify(item.arguments), 90)}</span></>}>
            {body}
          </Collapse>
        )
      })}
    </div>
  )
}

export function SourcesTab({ data, sources }: { data: ResearchDetail; sources: Sources | null }) {
  if (!data.sources.length) return <p className="empty-note">No manuscript was pinned for this job.</p>
  if (!sources) return <Loading />
  return (
    <div className="sources">
      {sources.map((source) => (
        <section key={source.id}>
          <h3 className="section-title">{source.id} {source.path}</h3>
          <p className="muted small">Snapshot saved when the job started. Lines the harness read are marked in the margin.</p>
          <SourceLines content={source.content} highlight={data.manuscript_ranges[source.id] || []} />
        </section>
      ))}
    </div>
  )
}
