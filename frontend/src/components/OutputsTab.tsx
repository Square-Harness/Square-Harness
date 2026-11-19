// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useMemo, useState } from 'react'
import { api, type Chat, type ProofDetail, type ResearchDetail } from '../api'
import { displayOf } from '../artifacts'
import { chatOutputs, type WorkOutput, type JobFormats } from '../outputs'
import { useApp, useTick } from '../store'
import { SelectedAnswer } from '../views/ProofView'
import { Report, Passage, flaggedCitations } from '../views/ResearchView'
import { TexSource } from '../views/WriteupView'
import { ArtifactView } from '../views/ChatView'
import { ErrorNote, useLoad } from './common'
import { Markdown, MathMacrosProvider } from './Markdown'
import './workTabs.css'

type Props = { chat: Chat; selected: string | null; onSelect: (id: string) => void }

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
  if (!outputs.length && !index.data && !index.error) return <p role="status">Loading saved outputs…</p>
  if (!outputs.length) return <><ErrorNote>{index.error}</ErrorNote><p className="empty-note">This conversation has no saved outputs yet.</p></>
  return <div className="work-tab-content">
    <ul className="work-output-list" aria-label="Saved outputs">{outputs.map(item =>
      <li key={item.id}><button type="button" aria-pressed={chosen?.id === item.id} onClick={() => onSelect(item.id)}>
        <strong>{item.name}</strong><span>{item.producer} · {item.status.replace(/_/g, ' ')}</span>
        {item.time && <time dateTime={item.time}>{new Date(item.time).toLocaleString()}</time>}
      </button></li>)}</ul>
    {chosen ? <MathMacrosProvider definitions={chat.math_macros}><OutputPreview key={chat.id + ':' + chosen.id} output={chosen} chat={chat.id} /></MathMacrosProvider>
      : selected ? <p role="status" className="muted">{index.data ? 'This output is unavailable in the current conversation.' : 'Loading the saved output…'}</p> : null}
  </div>
}

type Preview = { text: string; proof?: ProofDetail; research?: ResearchDetail }

async function previewText(output: WorkOutput, chat: string): Promise<Preview> {
  switch (output.kind) {
    case 'conversation': return { text: output.text || '' }
    case 'experiment': {
      const { answer } = await api.chatExperiment(chat, output.job)
      try { return { text: JSON.stringify(JSON.parse(answer), null, 2) } } catch { return { text: answer } }
    }
    case 'answer': return { text: (await api.savedChatAnswer(chat, output.job)).answer }
    case 'check': return { text: (await api.chatCheck(chat, output.job)).answer }
    case 'proof': {
      const proof = await api.proof(output.job)
      return { proof, text: proof.answer || '' }
    }
    case 'report': {
      if (output.producer === 'Prove') return { text: (await api.proofReport(output.job)).report }
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
  const candidate = proof?.candidates.find(item => item.id === proof.selected_candidate)
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
  return <section className="work-output-preview" aria-label={output.name}>
    <h3>{output.name}</h3>
    {output.objective && <p className="work-output-objective">{output.objective}</p>}
    <div className="work-tab-actions">
      {download && <a className="btn btn-small" href={download} download={localDownload ? output.kind + (output.kind === 'experiment' ? '.json' : '.md') : true}>Download</a>}
      {output.page && <a className="btn btn-small btn-quiet" href={output.page}>Open full job</a>}
    </div>
    <ErrorNote>{error}</ErrorNote>
    {isText && !error && (text === undefined ? <p role="status">Loading output…</p> : !text ? <p>No text has been saved yet.</p> :
      proof && candidate ? <SelectedAnswer data={proof} candidate={candidate} /> :
      research && output.kind === 'report' ? <Report data={research} flagged={flaggedCitations(research)}
        issues={[...new Set([...research.citation_issues, ...research.review_context_issues, ...research.warnings])]} onCite={setCite} /> :
      research && (output.kind === 'tex' || output.kind === 'body') ? <TexSource text={text}
        onCite={setCite} flagged={flaggedCitations(research)}
        errorLines={new Set(output.kind === 'body' ? [] : (research.compile?.errors || []).flatMap(
          err => err.file === 'document.tex' && err.line ? [err.line] : []))} /> :
      ['experiment', 'tex', 'body', 'bib'].includes(output.kind) ? <pre className="work-output-source">{text}</pre> : <Markdown>{text}</Markdown>)}
    {cite && research && <CitedPassage data={research} cite={cite} onClose={() => setCite('')} />}
    {output.file && output.url && <ArtifactView artifact={{ ...output.file, url: output.url }} purpose={output.objective} />}
    {(output.kind === 'pdf' || display === 'document') && <iframe className="work-output-frame" title={output.name} src={output.url} />}
    {output.kind === 'source' && <p className="muted">Download the ZIP to inspect the source files.</p>}
  </section>
}
