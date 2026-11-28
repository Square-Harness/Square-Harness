// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { api, type Chat, type ProofListItem, type ResearchListItem, type ResearchDetail, type RecordEntry, type TranscriptItem } from './api'
import { artifactUrl, type Artifact } from './artifacts'
import { MODE_CALLS } from './modeCalls'
import { href, resultHref } from './router'

export type OutputKind = 'investigation' | 'answer' | 'proof' | 'report' | 'check' | 'pdf' | 'tex' | 'source' | 'body' | 'bib' | 'file' | 'conversation' | 'experiment'
export type WorkOutput = {
  id: string; aliases: string[]; job: string; kind: OutputKind; name: string; producer: string; mode: string
  status: string; time: string; objective: string; text?: string; page?: string; url?: string; download?: string; file?: Artifact
  synthesis?: string; synthesis_steps?: string[]
  /** The document's file name, for the file viewer's title (a write-up's .tex and .pdf, review.md, references.bib). */
  fileName?: string
}

export function modeLabel(mode?: string) {
  return MODE_CALLS.find(item => item.mode === mode)?.label || mode?.replace(/_/g, ' ') || 'Assistant'
}

const researchNames: Record<string, string> = {
  prove: 'Proof', explore: 'Exploration', check: 'Literature check', literature: 'Literature review',
  quick_review: 'Proof check', detailed_review: 'Manuscript review', referee: 'Manuscript review', typo_check: 'Typo',
  explain: 'Explanation', writeup: 'Write-up', summarize: 'Summary', numerics: 'Numerical check',
  python_lab: 'Python lab', sanity_check: 'Numerical check',
}

export function researchModeName(mode: string) {
  return researchNames[mode] || modeLabel(mode)
}

export function taskReference(tasks: { id: string; mode: string }[], id: string) {
  const index = tasks.findIndex(task => task.id === id)
  if (index < 0) return 'an earlier step'
  const { mode } = tasks[index]
  const label = researchModeName(mode)
  return tasks.filter(task => task.mode === mode).length > 1 ? label + ' (step ' + (index + 1) + ')' : label
}

function object(value: unknown): Record<string, unknown> | null {
  if (typeof value === 'string') {
    try { value = JSON.parse(value) } catch { return null }
  }
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

/** Match saved pre-receipt answers only within the exact delegating turn. */
export function conversationAnswers(chat: Chat) {
  const answers = new Map<number, { text: string; aliases: string[] }>()
  let cards: number[] = []
  let calls: (Record<string, unknown> | null)[] = []
  chat.transcript.forEach((item, index) => {
    if (item.role === 'user') { cards = []; calls = [] }
    if (item.role === 'route' && item.orchestrated && !item.discarded) cards.push(index)
    if (item.role === 'assistant') {
      calls = (item.tool_calls || []).filter(call => call.function.name === 'delegate')
        .map(call => object(call.function.arguments))
    }
    if (item.role !== 'tool' || item.tool_name !== 'delegate') return
    const call = calls.shift()
    const payload = object(item.content)
    const result = object(payload?.result)
    if (typeof result?.answer !== 'string' || !result.answer.trim()) return
    const mode = payload?.mode || call?.mode
    const eligible = cards.filter(i => !answers.has(i) && chat.transcript[i].mode === mode)
    const bound = eligible.find(i => {
      const card = chat.transcript[i]
      return (!!card.child_id && card.child_id === payload?.child_id) ||
        (!!card.job_id && card.job_id === payload?.job_id) || (!!card.answer_id && card.answer_id === payload?.job_id)
    })
    const matches = eligible.filter(i => call?.request === chat.transcript[i].request)
    const card = bound ?? (matches.length === 1 ? matches[0] : eligible.length === 1 ? eligible[0] : undefined)
    if (card !== undefined) answers.set(card, { text: result.answer,
      aliases: [payload?.job_id, payload?.child_id].filter((id): id is string => typeof id === 'string' && !!id) })
  })
  return answers
}

export type JobFormats = Record<string, NonNullable<ResearchDetail['outputs']>>

export function literatureReviewState(job?: ResearchListItem, cardOutputs?: TranscriptItem['outputs']) {
  const outputs = { ...cardOutputs, ...job?.outputs }
  return { readable: !!(job?.has_review || outputs?.review), outputs }
}

export function literatureReviewLink(mode: Chat['mode'], chat: string, job: string, readable: boolean) {
  return readable
    ? { url: resultHref(mode, chat, job), label: 'Read literature review' }
    : { url: href('literature', job, null, chat), label: 'View review progress' }
}

export function nativeResultPage(mode: string, job: string, chat: string) {
  const screen = mode === 'prove' || mode === 'literature' ? mode
    : ['quick_review', 'detailed_review', 'referee', 'typo_check', 'explain'].includes(mode) ? 'referee'
    : ['writeup', 'summarize'].includes(mode) ? 'writeup' : undefined
  return screen ? href(screen, job, null, chat) : undefined
}

export function chatOutputs(chat: Chat, proofs: ProofListItem[] = [], research: ResearchListItem[] = [],
  entries: RecordEntry[] = [], discovered: JobFormats = {}): WorkOutput[] {
  const outputs = new Map<string, WorkOutput>()
  const answers = conversationAnswers(chat)
  const jobs = new Map([...proofs, ...research].map(job => [job.id, job]))
  for (let card = 0; card < chat.transcript.length; card++) {
    const item = chat.transcript[card]
    if (item.role === 'assistant' && item.investigation_id && item.content) {
      const id = item.investigation_id
      const plan = chat.investigation?.id === id ? chat.investigation : null
      outputs.set(id, { id, job: id, aliases: [], kind: 'investigation', name: 'Investigation report',
        mode: 'investigate', producer: 'Investigate', status: 'saved', time: item.time, objective: plan?.statement || '', text: item.content,
        synthesis: item.synthesis, synthesis_steps: item.synthesis_steps })
      continue
    }
    const agent = chat.agents?.find(worker => worker.card_index === card || (item.child_id && worker.id === item.child_id))
    const producer = researchModeName(item.mode || agent?.mode || '')
    const time = agent?.ended_at || item.time
    if (item.role === 'experiment' && item.experiment) {
      for (const file of item.artifacts || []) {
        const url = artifactUrl(item.experiment, file.filename)
        const id = item.experiment + ':' + file.filename
        outputs.set(id, { id, aliases: [file.artifact_id], job: item.experiment, kind: 'file', name: file.filename,
          mode: agent?.mode || 'numerics', producer: agent ? researchModeName(agent.mode) : 'Numerical experiment', status: item.run_status || 'saved',
          time, objective: item.purpose || '', file, url, download: url, fileName: file.filename })
      }
      continue
    }
    if (item.role !== 'route' || item.discarded || !item.mode) continue
    const receipt = answers.get(card)
    const job = item.answer_id || item.job_id || (receipt ? 'card-' + card : null)
    if (!job) continue
    const record = jobs.get(job)
    const mode = item.mode
    const review = mode === 'literature' ? literatureReviewState(record?.kind === 'literature' ? record : undefined, item.outputs) : null
    const formats = review?.outputs || item.outputs || discovered[job]
    const page = nativeResultPage(mode, job, chat.id)
    let primary = true
    const named = (value: unknown, fallback: string) => typeof value === 'string' && value ? value : fallback
    const stem = named(formats?.download || formats?.tex, 'writeup.tex').replace(/\.tex$/i, '')
    const fileNames: Partial<Record<OutputKind, string>> = {
      pdf: stem + '.pdf', tex: named(formats?.download || formats?.tex, stem + '.tex'), body: named(formats?.body, stem + '-body.tex'),
      bib: named(formats?.bib, 'references.bib'), report: named(formats?.review, mode === 'writeup' || mode === 'summarize' ? 'report.md' : 'review.md'),
    }
    function add(kind: OutputKind, name: string, url?: string, download?: string, text?: string) {
      const id = primary ? job! : job + ':' + kind
      const aliases = primary ? ['card-' + card, ...(agent ? [agent.id] : []), ...(receipt?.aliases || []), job + ':' + kind] : []
      outputs.set(id, { id, aliases, job: job!, kind, name, producer, mode, status: record?.status || item.status || 'saved',
        time: record?.updated_at || time, objective: item.request || '', page, url, download, text, fileName: fileNames[kind] })
      primary = false
    }
    if (!item.answer_id && !item.job_id && receipt) add('conversation', 'Answer in conversation', undefined, undefined, receipt.text)
    else if (item.answer_id) add('answer', mode === 'explore' ? 'Exploration notes' : researchModeName(mode) + ' answer')
    else if (mode === 'prove') {
      add('proof', 'Proof response')
      add('report', 'Proof review')
    } else if (mode === 'check') add('check', 'Literature check')
    else if (mode === 'writeup' || mode === 'summarize') {
      if (formats?.pdf) add('pdf', 'Compiled PDF', api.researchPdfUrl(job), api.researchPdfDownloadUrl(job))
      if (formats?.tex || formats?.download) add('tex', 'LaTeX document', api.researchTexUrl(job), api.researchTexUrl(job))
      if (formats?.body) add('body', 'LaTeX body', api.researchBodyUrl(job), api.researchBodyUrl(job))
      if (formats?.source) add('source', 'Source archive', undefined, api.researchSourceUrl(job))
      if (primary) add('report', 'Write-up report')
    } else if (page) {
      if (review && !review.readable) primary = false // Reserve the job ID for the review; its bibliography has its own ID.
      else add('report', mode === 'literature' ? 'Literature review' : 'Review report',
        undefined, formats?.review ? api.reviewMarkdownUrl(job) : undefined)
      if (formats?.bib) add('bib', 'Bibliography (BibTeX)', undefined, formats?.review ? api.reviewBibtexUrl(job) : api.researchBibDownloadUrl(job))
    }
  }
  // Worker-internal checks and experiments may have a native record but no chat card or figure.
  for (const entry of entries) {
    if (entry.chat_id !== chat.id || outputs.has(entry.job_id)) continue
    const kind = entry.folder === 'experiments' ? 'experiment' : entry.folder === 'checks' ? 'check' : undefined
    if (!kind) continue
    outputs.set(entry.job_id, { id: entry.job_id, job: entry.job_id, aliases: [entry.id], kind,
      name: kind === 'experiment' ? 'Numerical experiment' : 'Literature check', mode: entry.mode, producer: researchModeName(entry.mode),
      status: entry.status, time: entry.created_at, objective: entry.task.text })
  }
  return [...outputs.values()].sort((a, b) => (b.time || '').localeCompare(a.time || ''))
}
