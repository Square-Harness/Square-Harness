// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type Chat, type ProofDetail, type ResearchDetail } from '../api'
import { displayOf } from '../artifacts'
import { ago, plural } from '../format'
import { coverOf, MODE_CALLS } from '../modeCalls'
import { Glyph } from './Glyph'
import { InvestigationReport } from './InvestigationReading'
import { BackIcon, FileIcon } from './Icons'
import { reviewStatusLook, Square } from './Square'
import { chatOutputs, type WorkOutput, type JobFormats } from '../outputs'
import { useApp, useTick } from '../store'
import { Report, Passage, flaggedCitations } from '../views/ResearchView'
import { TexSource } from '../views/WriteupView'
import { ArtifactView } from '../views/ChatView'
import { ErrorNote, useLoad } from './common'
import { Inline, Markdown, MathMacrosProvider } from './Markdown'
import './workTabs.css'

type Props = { chat: Chat; selected: string | null; onSelect: (id: string | null) => void }

const resultStates: Record<string, string> = {
  done: 'Saved', ok: 'Completed', candidate_complete: 'Reviewed', complete: 'Completed', completed: 'Completed',
  ran: 'Completed', ready: 'Ready', paused: 'Paused', uncertain: 'Uncertain', failed: 'Interrupted',
  review_unavailable: 'Review unavailable', partial: 'Partial', attempts_exhausted: 'Attempts used',
  budget_exhausted: 'Allowance reached', budget_violation: 'Allowance exceeded', needs_input: 'Needs input',
}

function resultState(status: string) {
  return resultStates[status] || (status.charAt(0).toUpperCase() + status.slice(1)).replace(/_/g, ' ')
}

function ResultMark({ mode }: { mode: string }) {
  const call = MODE_CALLS.find(item => item.mode === mode || (mode === 'numerics' && item.mode === 'python_lab'))
  const cover = call && coverOf(call.mode)
  return <span className={'work-result-mark' + (cover ? ' mode-' + cover : '')} aria-hidden="true">
    {cover ? <Glyph mode={cover} /> : <FileIcon size={18} />}
  </span>
}

export function OutputsTab({ chat, selected, onSelect }: Props) {
  const { proofs, research } = useApp()
  const tick = useTick(chat.id)
  const index = useLoad(() => api.index({ chat: chat.id }), [chat.id, chat.project_id, tick])
  const [formats, setFormats] = useState<JobFormats>({})
  const legacy = [...new Set(chat.transcript.filter(item => item.role === 'route' && !item.outputs && item.job_id &&
    ['writeup', 'summarize', 'literature'].includes(item.mode || '')).map(item => item.job_id!))].join(',')
  useEffect(() => {
    let active = true
    async function discover() {
      const found: JobFormats = {}
      const ids = legacy ? legacy.split(',') : []
      for (let start = 0; active && start < ids.length; start += 3) {
        await Promise.all(ids.slice(start, start + 3).map(async id => {
          try { found[id] = (await api.research(id)).outputs || {} }
          catch { /* The job report still opens and shows its native error. */ }
        }))
      }
      if (active) setFormats(found)
    }
    discover()
    return () => { active = false }
  }, [chat.id, legacy])
  const outputs = useMemo(() => chatOutputs(chat, proofs || [], research || [], index.data?.entries, formats),
    [chat, proofs, research, index.data, formats])
  const chosen = outputs.find(item => item.id === selected || item.aliases.includes(selected || ''))
  const panel = useRef<HTMLDivElement>(null)
  const restore = useRef<string | null>(null)
  const focusPending = useRef(false)
  useEffect(() => {
    if (!focusPending.current || !panel.current) return
    const target = selected ? panel.current.querySelector<HTMLElement>('.work-back') :
      [...panel.current.querySelectorAll<HTMLButtonElement>('.work-output-list button')].find(button => button.dataset.output === restore.current)
    target?.focus()
    focusPending.current = false
  }, [selected])
  function choose(id: string | null) {
    if (id) restore.current = id
    else if (chosen) restore.current = chosen.id
    focusPending.current = true
    onSelect(id)
  }
  if (!outputs.length && !index.data && !index.error) return <p role="status">Loading results…</p>
  if (!outputs.length) return <><ErrorNote>{index.error}</ErrorNote><p className="empty-note">Results will appear here when an agent saves its work.</p></>
  return <div className="work-tab-content work-results" ref={panel}>
    <MathMacrosProvider definitions={chat.math_macros}>
      {chosen ? <>
        <button type="button" className="work-back" onClick={() => choose(null)}><BackIcon size={15} /> All results</button>
        <OutputPreview key={chat.id + ':' + chosen.id} output={chosen} chat={chat.id} />
      </> : <>
        <div className="work-section-head"><h3>Saved results</h3><small>{plural(outputs.length, 'item')}</small></div>
        {selected && <p role="status" className="muted">{index.data ? 'This result is unavailable in the current conversation.' : 'Loading the saved result…'}</p>}
        <ResultsList outputs={outputs} choose={choose} />
      </>}
    </MathMacrosProvider>
  </div>
}

function ResultByline({ item, showProducer }: { item: WorkOutput; showProducer: boolean }) {
  return <small>{showProducer && item.producer + ' · '}{resultState(item.status)}{item.time && <> · <time dateTime={item.time}
    title={new Date(item.time).toLocaleString()}>{ago(item.time)}</time></>}</small>
}

function ResultsList({ outputs, choose }: { outputs: WorkOutput[]; choose: (id: string) => void }) {
  const groups = new Map<string, WorkOutput[]>()
  for (const output of outputs) {
    const group = groups.get(output.job)
    if (group) group.push(output)
    else groups.set(output.job, [output])
  }
  return <ul className="work-output-list" aria-label="Saved results">{[...groups.values()].map(items => {
    const first = items[0]
    const grouped = items.length > 1
    const title = <><ResultMark mode={first.mode} /><span className="work-output-title">
      <strong>{grouped ? first.producer : first.name}</strong>
      {first.objective && <Inline mathProse limit={130}>{first.objective}</Inline>}
      <ResultByline item={first} showProducer={!grouped} /></span></>
    return <li key={first.job}>{grouped ? <>
      <div className="work-output-group-title">{title}</div>
      <ul className="work-output-formats">{items.map(item => <li key={item.id}>
        <button type="button" data-output={item.id} onClick={() => choose(item.id)}>
          {item.name}<span aria-hidden="true" className="work-entry-arrow">›</span>
        </button></li>)}</ul>
    </> : <button type="button" data-output={first.id} onClick={() => choose(first.id)}>
      {title}<span aria-hidden="true" className="work-entry-arrow">›</span>
    </button>}</li>
  })}</ul>
}

type Preview = { text: string; proof?: ProofDetail; research?: ResearchDetail }

async function previewText(output: WorkOutput, chat: string): Promise<Preview> {
  switch (output.kind) {
    case 'conversation': return { text: output.text || '' }
    case 'experiment': {
      return { text: (await api.chatExperiment(chat, output.job)).answer }
    }
    case 'answer': return { text: (await api.savedChatAnswer(chat, output.job)).answer }
    case 'check': return { text: (await api.chatCheck(chat, output.job)).answer }
    case 'proof': {
      const proof = await api.proof(output.job)
      return { proof, text: proof.answer || '' }
    }
    case 'report': {
      if (output.mode === 'prove') {
        const [proof, report] = await Promise.all([api.proof(output.job), api.proofReport(output.job)])
        return { proof, text: report.report }
      }
      const research = await api.research(output.job)
      return { research, text: research.draft || research.report }
    }
    case 'tex': case 'body': case 'bib': {
      const [response, research] = await Promise.all([fetch(output.url || output.download!, { credentials: 'same-origin' }),
        output.kind === 'bib' ? undefined : api.research(output.job)])
      if (!response.ok) throw new Error('The source file could not be opened.')
      return { text: await response.text(), research }
    }
    default: return { text: '' }
  }
}

function ProofResponse({ proof }: { proof: ProofDetail }) {
  const candidate = proof.candidates.find(item => item.id === proof.selected_candidate)
  const look = candidate && reviewStatusLook(candidate.review_status)
  return <div className="work-proof-reading">
    {look && <p className="work-assessment"><Square variant={look.variant} size={14} />Model review · {look.label}</p>}
    <Markdown>{proof.answer || ''}</Markdown>
  </div>
}

function ProofReviews({ proof }: { proof: ProofDetail }) {
  if (!proof.reviews.length) return <p className="muted">No review has been recorded for this response.</p>
  return <div className="work-proof-reviews">{proof.reviews.map((item, i) => {
    const response = item.response
    const look = reviewStatusLook(response?.verdict || 'review_unavailable')
    return <section key={item.id}>
      <h4>{proof.reviews.length === 1 ? 'Review' : 'Review ' + (i + 1)}</h4>
      <p className="work-assessment"><Square variant={look.variant} size={14} />{look.label}</p>
      {response && <>
        <Markdown>{response.explanation}</Markdown>
        {response.issues.map((issue, n) => <div className="work-review-issue" key={n}>
          <Markdown>{issue.location}</Markdown><Markdown>{issue.evidence}</Markdown></div>)}
        {!!response.critical_dependencies?.length && <details className="work-fold">
          <summary>Steps checked</summary><ul>{response.critical_dependencies.map((dependency, n) =>
            <li key={n}><Markdown>{dependency.claim}</Markdown><Markdown>{dependency.check}</Markdown></li>)}</ul>
        </details>}
        {!!response.notes?.length && <details className="work-fold"><summary>Review notes</summary>
          {response.notes.map((note, n) => <Markdown key={n}>{note}</Markdown>)}</details>}
      </>}
    </section>
  })}</div>
}

function ExperimentPreview({ text }: { text: string }) {
  let data
  try { data = JSON.parse(text) } catch { return <pre className="work-output-source">{text}</pre> }
  if (!data || typeof data !== 'object') return <pre className="work-output-source">{text}</pre>
  const output = typeof data.stdout === 'string' ? data.stdout.trimEnd() : ''
  const result = data.result == null ? '' : typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2)
  return <div className="work-experiment">
    {output ? <><h4>Observations</h4><pre className="work-output-source">{output}</pre></> :
      result ? <><h4>Recorded result</h4><pre className="work-output-source">{result}</pre></> :
      <p className="muted">No observations were recorded.</p>}
    {data.truncated && <p className="muted small">Only part of the original experiment is available.</p>}
    {typeof data.stderr === 'string' && data.stderr.trim() && <details className="work-fold"><summary>Messages from the experiment</summary>
      <pre className="work-output-source">{data.stderr}</pre></details>}
    {output && result && <details className="work-fold"><summary>Recorded data</summary>
      <pre className="work-output-source">{result}</pre></details>}
    {typeof data.code === 'string' && data.code && <details className="work-fold"><summary>Calculation</summary>
      <pre className="work-output-source">{data.code}</pre></details>}
  </div>
}

// The saved Check download includes the harness log. The answer is separately delimited in that report.
function checkReading(report: string) {
  if (!report.startsWith('# Literature check\n')) return report
  const answer = /(?:^|\n)## Answer\n([\s\S]*?)(?=\n## (?:Controller notes|Guesses and verdicts|Search and reading log)\s*(?:\n|$)|$)/.exec(report)
  return answer?.[1].trim() || report
}

function CitedPassage({ data, cite, onClose }: { data: ResearchDetail; cite: string; onClose: () => void }) {
  const sources = useLoad(() => api.researchSources(data.id), [data.id])
  return <><ErrorNote>{sources.error}</ErrorNote><Passage refText={cite} data={data}
    sources={sources.data?.sources || null} onClose={onClose} /></>
}

function OutputPreview({ output, chat }: { output: WorkOutput; chat: string }) {
  const tick = useTick(output.job)
  const isText = ['conversation', 'experiment', 'answer', 'check', 'proof', 'report', 'tex', 'body', 'bib'].includes(output.kind)
  const [value, setValue] = useState<Preview | null>(null)
  const [cite, setCite] = useState('')
  const { proof, research, text } = value || {}
  const [error, setError] = useState('')
  const [localDownload, setLocalDownload] = useState('')
  useEffect(() => {
    if (!isText) return
    let active = true
    previewText(output, chat).then(value => { if (active) { setError(''); setValue(value) } },
      reason => { if (active) setError((reason as Error).message) })
    return () => { active = false }
  }, [output.id, output.status, chat, isText, tick])
  useEffect(() => {
    if (!text || output.download) return
    const url = URL.createObjectURL(new Blob([text], { type: output.kind === 'experiment' ? 'application/json' : 'text/markdown;charset=utf-8' }))
    setLocalDownload(url)
    return () => URL.revokeObjectURL(url)
  }, [text, output.download, output.kind])
  const display = output.file ? displayOf(output.file.mime_type) : null
  const download = output.download || localDownload
  if (output.kind === 'investigation') return <InvestigationReport content={output.text || ''} chatId={chat} />
  return <section className="work-output-preview" aria-label={output.name}>
    <div className="work-section-head"><h3>{output.name}</h3></div>
    <p className="work-result-byline">{output.producer} · {resultState(output.status)}</p>
    {output.objective && <Markdown mathProse className="work-output-objective">{output.objective}</Markdown>}
    <div className="work-tab-actions">
      {download && <a className="btn btn-small" href={download} download={localDownload ? output.kind + (output.kind === 'experiment' ? '.json' : '.md') : true}>Download</a>}
      {output.page && <a className="btn btn-small" href={output.page}>Open {output.producer.toLowerCase()} <span aria-hidden="true">→</span></a>}
    </div>
    <ErrorNote>{error}</ErrorNote>
    {isText && !error && (text === undefined ? <p role="status">Loading output…</p> : !text ? <p>No text has been saved yet.</p> :
      proof && output.kind === 'report' ? <ProofReviews proof={proof} /> :
      proof ? <ProofResponse proof={proof} /> :
      output.kind === 'experiment' ? <ExperimentPreview text={text} /> :
      output.kind === 'check' ? <Markdown>{checkReading(text)}</Markdown> :
      research && output.kind === 'report' ? <Report data={research} flagged={flaggedCitations(research)}
        issues={[...new Set([...research.citation_issues, ...research.review_context_issues, ...research.warnings])]} onCite={setCite} /> :
      research && (output.kind === 'tex' || output.kind === 'body') ? <TexSource text={text}
        onCite={setCite} flagged={flaggedCitations(research)}
        errorLines={new Set(output.kind === 'body' ? [] : (research.compile?.errors || []).flatMap(
          err => err.file === 'document.tex' && err.line ? [err.line] : []))} /> :
      ['tex', 'body', 'bib'].includes(output.kind) ? <pre className="work-output-source">{text}</pre> : <Markdown>{text}</Markdown>)}
    {cite && research && <CitedPassage data={research} cite={cite} onClose={() => setCite('')} />}
    {output.file && output.url && <ArtifactView artifact={{ ...output.file, url: output.url }} purpose={output.objective} />}
    {(output.kind === 'pdf' || display === 'document') && <iframe className="work-output-frame" title={output.name} src={output.url} />}
    {output.kind === 'source' && <p className="muted">Download the ZIP to inspect the source files.</p>}
  </section>
}
