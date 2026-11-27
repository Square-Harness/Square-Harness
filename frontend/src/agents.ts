// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { Agent, AgentStatus, Chat, ProofListItem, ResearchListItem, TranscriptItem } from './api'
import { proofLook, researchLook, type Variant } from './components/Square'
import { conversationAnswers, researchModeName } from './outputs'
import { href } from './router'
import { VERDICTS } from './review'
import { PHASES, workProgressText } from './workProgress'
import { activityCaption, taskNotice, workNotice } from './workNotice'

export const agentLabels: Record<AgentStatus, string> = {
  waiting: 'Waiting', running: 'Working', finished: 'Done', failed: 'Interrupted', stopped: 'Stopped',
}
export const agentSquares: Record<AgentStatus, Variant> = {
  waiting: 'waiting', running: 'running', finished: 'complete', failed: 'halted', stopped: 'halted',
}
export const skillName = researchModeName
const summaryStages: Record<string, string> = {
  scope: 'Reading the conversation', plan: 'Planning the summary', outline: 'Outlining the summary',
  section: 'Writing the summary', write: 'Writing the summary', draft: 'Writing the summary',
  assemble: 'Assembling the summary', compile: 'Typesetting the summary', review: 'Checking the summary',
  repair: 'Adjusting the typesetting', export: 'Saving the summary',
}
export type AgentLane = Agent & {
  step: string
  stateLabel: string
  notebook: { url: string; label: string } | null
  result: { label: string; variant: Variant; outputId: string | null }
  /** The document a write-up, summary or literature review produced (its card's downloads), once saved. */
  documents: { job: string; outputs: NonNullable<TranscriptItem['outputs']> } | null
}

/** The durable child identity survives card creation, resume and chat reload. */
export function deriveAgents(chat: Chat, proofs: ProofListItem[] = [], research: ResearchListItem[] = []): AgentLane[] {
  const oldAnswers = conversationAnswers(chat)
  return (chat.agents || []).map(agent => {
    const item = agent.card_index == null ? chat.transcript.find(card => card.role === 'route' && card.child_id === agent.id)
      : chat.transcript[agent.card_index]
    const proof = proofs.find(p => p.id === agent.job_id)
    const job = research.find(j => j.id === agent.job_id)
    const status = agent.status
    const operational = status === 'failed' || status === 'stopped'
    let look = { label: agentLabels[status], variant: agentSquares[status] }
    // A worker stopped by pausing the Assistant waits for Resume: paused, not failed.
    const pausedHere = status === 'stopped' && chat.assistant_status === 'paused' && chat.assistant_resumable !== false
    if (pausedHere && agent.error?.code !== 'worker_budget_exhausted') look = { label: 'Paused', variant: 'paused' }
    else if (operational && agent.error?.code) {
      if (agent.error.code === 'worker_budget_exhausted') look = { label: 'Work limit reached', variant: 'spent' }
    } else if (status === 'finished') {
      if (proof) {
        look = proofLook(proof.status, proof.running, proof.version)
        if (proof.status === 'candidate_complete' && proof.version !== 1) look.label = 'Answer reviewed'
      } else if (job) look = (job.verdict && VERDICTS[job.verdict]) || researchLook(job.status, job.running, job.kind)
      else if (agent.mode === 'prove') look = { label: 'Review status unavailable', variant: 'uncertain' }
      else if (agent.answer_id || (agent.card_index != null && oldAnswers.has(agent.card_index))) look.label = 'Done'
      else if (agent.mode === 'check') look.label = 'Done'
    }
    const phaseId = agent.progress?.phase || job?.phase || ''
    const phase = (agent.mode === 'summarize' && summaryStages[phaseId]) ||
      (agent.progress && agent.progress.total > 1 ? workProgressText(agent.progress, status, status === 'running') : PHASES[phaseId])
    const latest = agent.timeline?.map(activityCaption).filter(Boolean).at(-1)
    const step = status === 'waiting' ? 'Waiting to start'
      : pausedHere && look.variant === 'paused' ? 'Paused with the Assistant; Resume continues it'
      : operational ? (agent.error?.code === 'worker_budget_exhausted' ? '' : taskNotice(agent.error?.message || agent.outcome || '', chat.investigation?.tasks || []))
      : status === 'running' ? phase || latest || 'Starting work' : workNotice(agent.outcome || '')
    const cardOutput = agent.card_index != null && (item?.outputs || oldAnswers.has(agent.card_index))
    const nativeOutput = agent.job_id && (agent.mode === 'check' || proof || job) ? agent.job_id : null
    const outputId = agent.answer_id || nativeOutput || (cardOutput ? 'card-' + agent.card_index : null)
    // A missing legacy job stays in the activity view. Only link to a native notebook that exists.
    const book = proof ? 'prove' : job?.kind
    const notebook = book && agent.job_id ? {
      url: book === 'literature' ? href(chat.mode, chat.id, null, null, { tab: 'outputs', selected: agent.job_id })
        : href(book, agent.job_id, null, chat.id),
      label: book === 'literature' ? 'Read literature review' : 'Open ' + skillName(agent.mode).toLowerCase(),
    } : null
    const documentJob = agent.job_id || item?.job_id
    const documents = documentJob && item?.outputs ? { job: documentJob, outputs: item.outputs } : null
    return { ...agent, step, notebook, stateLabel: look.label, result: { ...look, outputId }, documents }
  })
}

export function groupsOf(lanes: AgentLane[]) {
  const groups = new Map<string, AgentLane[]>()
  for (const lane of lanes) {
    const id = lane.turn_id + '/' + lane.batch_id
    const group = groups.get(id)
    if (group) group.push(lane)
    else groups.set(id, [lane])
  }
  return [...groups.values()]
}

export const activeAgent = (agent: { status: AgentStatus }) => agent.status === 'running' || agent.status === 'waiting'
export const stamp = (value: string | null) => value ? Date.parse(value) : NaN
