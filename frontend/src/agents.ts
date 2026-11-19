// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { Agent, AgentStatus, Chat, ProofListItem, ResearchListItem, TranscriptItem } from './api'
import { proofLook, researchLook, type Variant } from './components/Square'
import { MODE_CALLS } from './modeCalls'
import { conversationAnswers } from './outputs'
import { VERDICTS } from './review'
import { workProgressText } from './workProgress'

export const agentLabels: Record<AgentStatus, string> = {
  waiting: 'Waiting', running: 'Running', finished: 'Finished', failed: 'Failed', stopped: 'Stopped',
}
export const agentSquares: Record<AgentStatus, Variant> = {
  waiting: 'waiting', running: 'running', finished: 'complete', failed: 'halted', stopped: 'halted',
}
export const skillName = (mode: string) => MODE_CALLS.find(m => m.mode === mode)?.label ?? mode.replaceAll('_', ' ')
export type AgentLane = Agent & {
  step: string
  stateLabel: string
  item?: TranscriptItem
  result: { label: string; variant: Variant; outputId: string | null }
}

/** The durable child identity survives card creation, resume and chat reload. */
export function deriveAgents(chat: Chat, proofs: ProofListItem[] = [], research: ResearchListItem[] = []): AgentLane[] {
  const oldAnswers = conversationAnswers(chat)
  return (chat.agents || []).map(agent => {
    const item = agent.card_index == null ? undefined : chat.transcript[agent.card_index]
    const proof = proofs.find(p => p.id === agent.job_id)
    const job = research.find(j => j.id === agent.job_id)
    const status = agent.status
    const operational = status === 'failed' || status === 'stopped'
    let look = { label: agentLabels[status], variant: agentSquares[status] }
    if (operational && agent.error?.code) {
      look.label += ' · ' + agent.error.code.replace(/^worker_/, '').replaceAll('_', ' ')
    } else if (status === 'finished') {
      if (proof) look = proofLook(proof.status, proof.running, proof.version)
      else if (job) look = (job.verdict && VERDICTS[job.verdict]) || researchLook(job.status, job.running, job.kind)
      else if (agent.answer_id || (agent.card_index != null && oldAnswers.has(agent.card_index))) look.label = 'Answer saved'
      else if (agent.mode === 'check') look.label = 'Reference check'
    }
    const phase = agent.progress ? workProgressText(agent.progress, status, status === 'running')
      : job?.phase ? job.phase.replaceAll('_', ' ') : ''
    const latest = agent.timeline?.at(-1)?.text
    const step = status === 'waiting' ? 'Waiting to start'
      : operational ? agent.error?.message || agent.outcome || agentLabels[status]
      : status === 'running' ? phase || latest || 'Starting work' : agent.outcome || look.label
    const cardOutput = agent.card_index != null && (item?.outputs || oldAnswers.has(agent.card_index))
    const nativeOutput = agent.job_id && (agent.mode === 'check' || proof || job) ? agent.job_id : null
    const outputId = agent.answer_id || nativeOutput || (cardOutput ? 'card-' + agent.card_index : null)
    return { ...agent, item, step, stateLabel: agentLabels[status], result: { ...look, outputId } }
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
