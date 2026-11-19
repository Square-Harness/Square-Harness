// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type Chat, type RouteMode } from '../api'
import { readInvestigationReport, type Finding, type ReportStep } from '../investigationText'
import { coverOf } from '../modeCalls'
import { researchModeName, taskReference } from '../outputs'
import { href, openWorkPanel } from '../router'
import { useApp } from '../store'
import { taskNotice } from '../workNotice'
import { Glyph } from './Glyph'
import { Markdown } from './Markdown'
import { Square } from './Square'
import './InvestigationReading.css'

function ModeMark({ mode }: { mode: RouteMode }) {
  const cover = coverOf(mode)
  return <span className={'investigation-mode-mark route-cover mode-' + cover} aria-hidden="true"><Glyph mode={cover} /></span>
}

type Plan = NonNullable<Chat['investigation']>
type PlanStep = Pick<Plan['tasks'][number], 'id' | 'mode' | 'objective' | 'after' | 'claim'> & { status?: string }

export function PlanSteps({ tasks, renderAfter }: { tasks: PlanStep[]; renderAfter?: (taskId: string) => ReactNode }) {
  const roots = tasks.filter(task => !task.after.length)
  return <ol className="investigation-steps">{tasks.map(task => <li key={task.id}>
    <ModeMark mode={task.mode as RouteMode} />
    <div><header><strong>{researchModeName(task.mode)}</strong>
      {task.status && <span>{stateLabel[task.status] || task.status}</span>}</header>
      <Markdown mathProse>{task.objective}</Markdown>
      {(task.after.length > 0 || roots.length > 1 || task.claim === 'variant') && <p className="investigation-timing">{
        [task.after.length ? 'After ' + task.after.map(id => taskReference(tasks, id)).join(' and ') : roots.length > 1 ? (task.status && ['complete', 'skipped', 'failed'].includes(task.status) ? 'Independent step' : 'Can start in parallel') : '',
         task.claim === 'variant' ? 'Proof of a special case' : ''].filter(Boolean).join(' · ')}</p>}
      {renderAfter?.(task.id)}
    </div>
  </li>)}</ol>
}

export function ConversationPlan({ plan, finished }: { plan: Plan; finished: boolean }) {
  const [choice, setChoice] = useState<boolean | null>(null)
  const open = choice ?? (!finished && !plan.tasks.some(task => task.status === 'complete'))
  if (!plan.statement && !plan.tasks.length && finished) return null
  return <div className="entry message message-model investigation-plan-entry">
    <span className="in-margin speaker speaker-model mode-investigate" title="Investigation"><Glyph mode="investigate" /></span>
    <div className="message-body investigation-reading investigation-plan">
      <button type="button" className="investigation-plan-toggle" aria-expanded={open} onClick={() => setChoice(!open)}>
        <span className="investigation-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <strong>Research plan</strong>
        {plan.tasks.length > 0 && <small>{plan.tasks.length} {plan.tasks.length === 1 ? 'step' : 'steps'}</small>}
      </button>
      {open && <div className="investigation-plan-body">
        {plan.statement ? <Markdown mathProse className="investigation-report-statement">{plan.statement}</Markdown>
          : <p role="status">Preparing the research plan…</p>}
        {plan.reading && <Markdown mathProse className="investigation-approach">{plan.reading}</Markdown>}
        {!!plan.tasks.length && <PlanSteps tasks={plan.tasks} />}
        <button type="button" className="investigation-plan-link" onClick={() => openWorkPanel('plan')}>View in notebook <span aria-hidden="true">→</span></button>
      </div>}
    </div>
  </div>
}

const evidenceLabel: Record<string, string> = {
  'passages read': 'Passages read', 'abstract only': 'Abstract only', 'record only': 'Bibliographic record only',
  'model-reviewed proof': 'Model-reviewed proof', 'model-reviewed refutation': 'Model-reviewed refutation',
  'lead (model-written)': 'Exploratory finding', 'ran': 'Experiment run',
}

const stateLabel: Record<string, string> = { complete: 'Finished', skipped: 'Skipped', failed: 'Interrupted', pending: 'Waiting', running: 'In progress', interrupted: 'Paused', stopped: 'Stopped' }

// Choose a recorded conclusion or result. Old answers often open with their task or
// tool log. Preserve the author's qualifications and avoid manufacturing a summary.
function nativeSections(source: string) {
  const sections: { title: string; body: string }[] = []
  let section = { title: '', body: '' }
  sections.push(section)
  let fence = ''
  for (const line of source.split('\n')) {
    const delimiter = /^\s*(`{3,}|~{3,})/.exec(line)?.[1]
    if (delimiter) { fence = fence ? '' : delimiter; continue }
    if (fence) continue
    const heading = /^#{1,6}\s+(.+)$/.exec(line)
    if (heading) { section = { title: heading[1], body: '' }; sections.push(section) }
    else section.body += line + '\n'
  }
  return sections
}

function resultPassage(source: string) {
  const sections = nativeSections(source)
  const bottomLine = /where (?:the )?.{0,35}question stands|bottom line|conclusions?\b|main (?:answer|finding|result)|^.{0,6}(?:summary|answer)\b/i
  const meaningful = sections.filter(s => !/references|bibliography|further reading|scope.{0,18}tools|tools used|original (?:task|request|question)$|(?:^|\s)(?:tasks?|objectives?|assignments?|instructions?|methods?|budget|setup|plan|next steps|proposed work|to[- ]?do)(?:$|\s|:)/i.test(s.title))
  const ordered = [...meaningful.filter(s => bottomLine.test(s.title)),
    ...meaningful.filter(s => /^.{0,5}(?:results?|findings?)(?:$|\s)/i.test(s.title)), ...meaningful]
  for (const { title, body } of new Set(ordered)) {
    const blocks = body.trim().split(/\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)/).map(p => p.replace(/^\s*(?:[-*]|\d+\.)\s+/, '').trim())
      .filter(p => p.length > 35 && p.length < 2600 && !/^(?:[>#|`]|!\[|(?:\*{0,2})(?:task|objective|tools? used|scope|validation|references|further reading)\s*[:.])/i.test(p) &&
        !p.endsWith('?') && !/^(?:Survey|Numerically probe|Investigate|Attempt|Check|Find|Verify|Prove|Compute|Determine|Explore|Test whether|Search for|Your (?:task|objective)|You (?:must|should)|I (?:will|am going to))\b/i.test(p) &&
        !/(?:exp-\d{8}-\d{6}-|\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3})/.test(p))
    // A worker may distinguish an exact result from a conjecture about the original question.
    const explicit = blocks.find(p => /^\*{0,2}(?:headline|bottom line|answer to the original question|conjecture,? (?:not proved|unproved)|original question untouched)\b/i.test(p))
    const block = explicit || blocks[0]
    if (!block) continue
    if (explicit || bottomLine.test(title) || block.length <= 410) return block
    // Bibliographic prose and exploratory introductions can be long. Use complete
    // sentences and keep qualifications that are expressed in the following sentence.
    let ending = 0
    for (const match of block.matchAll(/[.!?](?:[”’]\*{0,2}|\*{1,2})?(?=\s+[A-Z])/g)) {
      const excerpt = block.slice(0, match.index + match[0].length)
      if (excerpt.length < 75) continue
      const math = excerpt.match(/(?<!\\)\$\$?|\\[()[\]]/g) || []
      if (math.filter(m => m === '$').length % 2 || math.filter(m => m === '$$').length % 2 ||
        math.filter(m => m === '\\(').length !== math.filter(m => m === '\\)').length ||
        math.filter(m => m === '\\[').length !== math.filter(m => m === '\\]').length) continue
      ending = excerpt.length
      if (!/^(?:However|But|This (?:does not|only)|Nevertheless|Except|Yet|In contrast|Conversely)\b/.test(block.slice(ending).trimStart())) return excerpt
    }
    return ending ? block.slice(0, ending) : block
  }
  return ''
}

// Numerical sections can contain inferences as well as measurements. Only surface
// a self-contained observation with a named quantity, numbers and its stated range.
function numericalObservation(source: string) {
  const sections = nativeSections(source).filter(section => /(?:numerical|experimental|computational) (?:results?|observations?|findings?)/i.test(section.title))
  for (const section of sections) {
    for (const paragraph of section.body.trim().split(/\n\s*\n/)) {
      const text = paragraph.trim()
      if (!text || /^(?:#|[|>`]|validation\b|[-*]\s|\*\*)/i.test(text) || text.includes('\n')) continue
      if (!/(?:\b(?:min(?:imum)?|max(?:imum)?|mean|median|count)\b|\bobserved\b|\bmeasured\b)/i.test(text) || !/\d/.test(text)) continue
      // A semicolon often separates measurements from an interpretation. Keep
      // the scope and exact numbers, including any restriction in the next clause.
      const split = text.indexOf('; ')
      const only = split > 0 && !/^(?:but|however|except|although|limited|only|caution)\b/i.test(text.slice(split + 2)) ? text.slice(0, split) + '.' : text
      if (only.length <= 420 && /\b(?:at|for|among|over|within|between|first)\b/i.test(only) && !/\b(?:exp-|timeout|tool|budget)\b/i.test(only)) return only
    }
  }
  return ''
}

function ResultExcerpt({ chatId, step }: { chatId: string; step: Pick<ReportStep, 'mode' | 'job'> & { excerpt?: string } }) {
  const region = useRef<HTMLDivElement>(null)
  const [text, setText] = useState<string | null>(null)
  const [observation, setObservation] = useState('')
  useEffect(() => {
    const node = region.current
    if (!node || !step.job || !['explore', 'literature'].includes(step.mode)) return
    setText(null)
    setObservation('')
    const controller = new AbortController()
    const read = async () => {
      const literature = step.mode === 'literature'
      try {
        const url = literature ? api.researchReviewDownloadUrl(step.job) : '/api/chats/' + chatId + '/answers/' + step.job
        const response = await fetch(url, { credentials: 'same-origin', signal: controller.signal })
        if (!response.ok) throw new Error('The native result is unavailable.')
        const body = literature ? await response.text() : (await response.json()).answer
        if (!controller.signal.aborted) {
          setText(typeof body === 'string' ? resultPassage(body) : '')
          if (!literature && typeof body === 'string') setObservation(numericalObservation(body))
        }
      } catch { if (!controller.signal.aborted) setText('') }
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); void read() }
    }, { rootMargin: '180px' })
    observer.observe(node)
    return () => { observer.disconnect(); controller.abort() }
  }, [chatId, step.mode, step.job])
  // Older saved chats can outlive their native answer. Offer the original, explicitly
  // partial lead if it contains a readable paragraph without internal record numbers.
  const fallback = text === '' && step.excerpt ? resultPassage(step.excerpt) : ''
  return <div ref={region} className="investigation-excerpt">{text ? <Markdown mathProse>{text}</Markdown> :
    fallback ? <details className="investigation-sources"><summary>Excerpt from the saved exploration</summary><Markdown mathProse>{fallback}</Markdown></details> :
    text === '' && <p className="muted small">No finding is available to quote from this step.</p>}
    {observation && observation !== text && <div className="investigation-measurement"><small>Numerical observation</small><Markdown mathProse>{observation}</Markdown></div>}</div>
}

function humanReason(text: string) {
  if (text === 'No model-declared variant was registered for this slot.') return 'No separate statement was selected for this proof.'
  if (text === 'A prerequisite failed or was skipped.') return 'An earlier step this depended on was interrupted or skipped.'
  return text.replace(/^Model decision: /, '').replace(/^Model requested early stop: /, '')
}

function linkedResult(chat: string, job: string) {
  return href('free', chat, null, null, { tab: 'outputs', selected: job })
}

type ReadingStep = { id: string; mode: string; job?: string | null }

// The reader supplies the title and a separate assessment of the exact statement.
function answerProse(text?: string) {
  return (text || '').trim().replace(/^#{1,2}\s+(?:Research answer|Working answer|Answer)\s*\n+/i, '')
    .replace(/\s*\(`exact_target_review: true`\)/g, '')
}

function FindingLinks({ steps, chat, covered }: { steps: ReadingStep[]; chat: string; covered?: string[] }) {
  const results = steps.filter(step => step.job)
  if (!results.length) return null
  return <nav className="investigation-readings" aria-label="Read supporting work">
    {results.map(step => <a key={step.id} href={linkedResult(chat, step.job!)}>
      <ModeMark mode={step.mode as RouteMode} /><span>{taskReference(steps, step.id)}</span>
      {covered && !covered.includes(step.id) && <small>New</small>}
      <span aria-hidden="true" className="reading-arrow">→</span>
    </a>)}
  </nav>
}

export function WorkingAnswer({ plan, chatId }: { plan: Plan; chatId: string }) {
  const complete = plan.tasks.filter(task => task.status === 'complete')
  const text = answerProse(plan.synthesis)
  if (!text && !complete.length) return null
  const covered = plan.synthesis_steps || []
  const names = plan.tasks.filter(task => covered.includes(task.id)).map(task => taskReference(plan.tasks, task.id))
  const waiting = complete.some(task => !covered.includes(task.id))
  const reviews = plan.target_reviews || []
  const reviewStatus = reviews.length > 1 ? 'The saved proof reviews disagree on the original statement.'
    : reviews[0] === 'model-reviewed refutation' ? 'Proof returned a reviewed refutation of the original statement.'
    : reviews[0] === 'model-reviewed proof' ? 'Proof returned a reviewed argument for the original statement.'
    : plan.tasks.some(task => task.mode === 'prove') ? 'No proof review of the original statement has been recorded yet.' : ''
  const first = !text && complete.find(task => task.job_id && ['explore', 'literature'].includes(task.mode))
  return <div className="entry message message-model investigation-draft-entry">
    <span className="in-margin speaker speaker-model mode-investigate" title="Investigation"><Glyph mode="investigate" /></span>
    <article className="message-body investigation-reading investigation-report" aria-label="Working answer">
      <div className="investigation-answer-head"><h2>Working answer</h2>
        <small role="status"><Square variant={plan.can_stop ? 'paused' : 'running'} size={13} />{plan.can_stop ? 'Paused' : 'In progress'}</small></div>
      {text && <Markdown className="investigation-synthesis" mathProse>{text}</Markdown>}
      {first && <section className="investigation-first-reading"><h3>From the {researchModeName(first.mode).toLowerCase()}</h3>
        <ResultExcerpt chatId={chatId} step={{ mode: first.mode as RouteMode, job: first.job_id! }} /></section>}
      <p className="investigation-coverage" aria-live="polite">{[
        names.length && text ? 'Based on ' + names.join(', ') + '.' : '',
        reviewStatus,
        plan.can_stop ? '' : !text ? 'The first findings are ready to read while the investigation brings them together.'
          : waiting ? 'Another finding is ready to read below.' : '',
      ].filter(Boolean).join(' ')}</p>
      <FindingLinks steps={plan.tasks.map(task => ({ ...task, job: task.status === 'complete' ? task.job_id || task.child_id : null }))}
        chat={chatId} covered={text ? covered : undefined} />
    </article>
  </div>
}

function Sources({ evidence, more }: { evidence: Finding[]; more?: string }) {
  return <details className="investigation-sources"><summary>{evidence.length} {evidence.length === 1 ? 'source' : 'sources'} listed</summary>
    <ul>{evidence.map((e, i) => <li key={i}><span>{e.summary || 'Source details unavailable'}</span>
      <small>{evidenceLabel[e.label] || e.label}</small></li>)}</ul>
    {more && <p className="muted small">{more.replace('native record', 'full review')}</p>}
  </details>
}

function EvidenceStep({ step, chat, tasks, anchor }: { step: ReportStep; chat: string; tasks: ReportStep[]; anchor: string }) {
  const { research } = useApp()
  const partialReview = step.mode === 'literature' && research?.some(item => item.id === step.job && item.status === 'partial')
  const sources = step.evidence.filter(e => e.kind === 'L')
  const experiments = step.evidence.filter(e => e.kind === 'E')
  const main = step.evidence.find(e => !['L', 'E'].includes(e.kind))
  const scope = main?.qualifiers.filter(q => q !== 'worded by a model brief')
    .map(q => q === 'relative to pinned context' ? 'under the supplied assumptions' : q)
  return <li className="investigation-finding" id={anchor} tabIndex={-1}>
    <ModeMark mode={step.mode} />
    <div><header><strong>{researchModeName(step.mode)}</strong><span>{partialReview ? 'Partial review' : stateLabel[step.state] || step.state}</span>
      {step.job && <a className="investigation-result-link" href={linkedResult(chat, step.job)}>{step.mode === 'prove' ? 'Read proof attempt' :
        step.mode === 'literature' ? 'Read literature review' : step.mode === 'explore' ? 'Read exploration' : 'Read result'} <span aria-hidden="true">→</span></a>}</header>
      {step.claim && step.claim !== 'target' && <small className="investigation-timing">Special case</small>}

      {main && <p className="investigation-evidence-label">{[evidenceLabel[main.label] || main.label, ...(scope || [])].join(' · ')}</p>}
      {step.job && ['explore', 'literature'].includes(step.mode) && <ResultExcerpt chatId={chat} step={step} />}
      {step.mode !== 'explore' && step.mode !== 'literature' && main?.summary && <Markdown mathProse>{main.summary}</Markdown>}
      {step.notes.filter(n => !/^\d+ further entries in the native record\.$/.test(n)).map((note, i) =>
        step.state === 'skipped' ? <details key={i} className="investigation-sources"><summary>Reason recorded at the time</summary>
          <Markdown>{taskNotice(humanReason(note), tasks)}</Markdown></details> :
          <Markdown key={i} className="investigation-step-note">{taskNotice(humanReason(note), tasks)}</Markdown>)}
      {sources.length > 0 && <Sources evidence={sources} more={step.notes.find(n => /^\d+ further entries in the native record\.$/.test(n))} />}
      {experiments.length > 0 && <details className="investigation-sources"><summary>{experiments.length} numerical {experiments.length === 1 ? 'experiment' : 'experiments'}</summary>
        <ul>{experiments.map((e, i) => <li key={i}><a href={linkedResult(chat, e.job)}>Experiment {i + 1}</a>
          <span>{e.summary || evidenceLabel[e.label] || e.label}</span></li>)}</ul></details>}
      {!step.job && !step.notes.length && !step.evidence.length && <p className="muted small">No finding was recorded for this step.</p>}
    </div>
  </li>
}

export function InvestigationReport({ content, chatId, synthesis, covered }: { content: string; chatId: string; synthesis?: string; covered?: string[] }) {
  const reportKey = useId()
  const report = useMemo(() => readInvestigationReport(content), [content])
  if (!report) return <Markdown className="answer">{content}</Markdown>
  const { reviewed, unresolved, statement, approved, reason } = report
  const finished = report.work.filter(step => step.state === 'complete').length
  const skipped = report.work.filter(step => step.state === 'skipped').length
  const activeSteps = report.work.filter(step => step.job)
  const outcome = [...new Set(reviewed.map(e => evidenceLabel[e.label] || e.label))].join(' · ')
  const narrative = answerProse(synthesis || report.synthesis)
  const partial = !!covered && report.work.some(step => step.state === 'complete' && !covered.includes(step.id))
  const evidence = <ul className="investigation-steps">{report.work.map(step => <EvidenceStep key={step.id} step={step} chat={chatId}
    tasks={report.work} anchor={reportKey + step.id} />)}</ul>
  return <article className="investigation-reading investigation-report" aria-label="Investigation report">
    <h2>{narrative ? 'Research answer' : 'Investigation report'}</h2>
    {narrative ? <>
      {partial && <p className="investigation-coverage">This answer covers part of the investigation. The other findings are available below.</p>}
      <Markdown className="investigation-synthesis" mathProse>{narrative}</Markdown>
      <FindingLinks steps={report.work} chat={chatId} />
    </> : statement && <Markdown mathProse className="investigation-report-statement">{statement}</Markdown>}
    {(outcome || unresolved) && <div className="investigation-conclusion">
      <Square variant={unresolved ? 'uncertain' : 'reviewed'} size={18} />
      <div><strong>{outcome || 'Full statement unresolved here'}</strong>
        <p>{unresolved ? [(finished === 1 ? '1 step finished' : finished + ' steps finished'), skipped ? skipped + ' skipped' : ''].filter(Boolean).join(' · ') : 'The model reviewed this exact statement. Formal verification is separate.'}</p></div>
    </div>}
    {!narrative && activeSteps.length > 1 && <nav className="investigation-jumps" aria-label="Jump to a finding">
      {activeSteps.map(step => <button key={step.id} type="button" onClick={() => {
        const finding = document.getElementById(reportKey + step.id)
        finding?.focus({ preventScroll: true }); finding?.scrollIntoView({ block: 'start' })
      }}>{taskReference(report.work, step.id)} <span aria-hidden="true">↓</span></button>)}
    </nav>}
    {reason && !/^The (?:approved )?research plan finished\.$/.test(reason) && <Markdown>{taskNotice(humanReason(reason), report.work)}</Markdown>}
    {report.variants && <section><h3 className="investigation-subhead">Special cases</h3><Markdown mathProse>{report.variants}</Markdown></section>}
    {report.work.length > 0 && (narrative ? <details className="investigation-sources"><summary>Evidence and scope</summary>
      {statement && <Markdown mathProse className="investigation-report-statement">{statement}</Markdown>}{evidence}</details>
      : <section><h3 className="investigation-subhead">Findings</h3>{evidence}</section>)}
    {report.original && <details className="investigation-sources"><summary>Original question</summary><Markdown mathProse>{report.original}</Markdown></details>}
    {!approved && !reason && <p>The proposed question was not approved.</p>}
  </article>
}
