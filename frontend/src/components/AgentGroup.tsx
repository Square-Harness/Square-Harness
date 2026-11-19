// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useMemo, useState } from 'react'
import { api, type Activity, type AgentStatus, type Chat, type ProofDetail, type ResearchDetail, type RouteMode } from '../api'
import { activeAgent, agentLabels, agentSquares, deriveAgents, skillName, stamp, type AgentLane } from '../agents'
import { count, duration } from '../format'
import { coverOf } from '../modeCalls'
import { href, openWorkPanel } from '../router'
import { useApp, useTick } from '../store'
import { useLiveStream, StreamBody } from '../views/shared'
import { ErrorNote, useLoad } from './common'
import { Inline, Markdown } from './Markdown'
import { Square } from './Square'
import '../agentGroups.css'

export function AgentSquares({ agents }: { agents: { id: string; mode: string; status: AgentStatus }[] }) {
  return <span className="agent-squares">{agents.slice(0, 8).map(agent =>
    <Square key={agent.id} variant={agentSquares[agent.status]} size={12} label={skillName(agent.mode) + ' · ' + agentLabels[agent.status]} />
  )}{agents.length > 8 && <span>+{agents.length - 8}</span>}</span>
}

export function AgentHeader({ lanes }: { lanes: AgentLane[] }) {
  const lastTurn = lanes.at(-1)?.turn_id
  const current = lanes.filter(lane => lane.turn_id === lastTurn)
  const running = current.filter(lane => lane.status === 'running').length
  const waiting = current.filter(lane => lane.status === 'waiting').length
  const text = running || waiting ? [running && running + ' running', waiting && waiting + ' waiting'].filter(Boolean).join(' · ')
    : current.length ? current.length + (current.length === 1 ? ' agent' : ' agents') : 'Work panel'
  const announcement = current.map(s => skillName(s.mode) + ' ' + s.stateLabel.toLowerCase()).join('. ')
  return <header className="agent-header">
    <button type="button" className="agent-trigger" data-work-trigger onClick={() => openWorkPanel(current.length ? 'agents' : 'record')}
      aria-label={'Open work panel' + (current.length ? ', ' + text : '')}>
      <AgentSquares agents={current} />{text}<span aria-hidden="true">›</span>
    </button>
    <span className="agent-announcement" aria-live="polite" aria-atomic="true">{announcement}</span>
  </header>
}

function laneEnd(lane: AgentLane, now: number) {
  return lane.ended_at ? stamp(lane.ended_at) : lane.status === 'running' ? now : NaN
}

export function AgentGroup({ lanes }: { lanes: AgentLane[] }) {
  const active = lanes.some(activeAgent)
  const running = lanes.some(lane => lane.status === 'running')
  const [folded, setFolded] = useState<{ active: boolean; value: boolean } | null>(null)
  const open = folded && folded.active === active ? folded.value : active
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!open || !running) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [open, running])
  const starts = lanes.map(lane => stamp(lane.started_at)).filter(Number.isFinite)
  const ends = lanes.map(lane => laneEnd(lane, now)).filter(Number.isFinite)
  const start = Math.min(...starts), span = Math.max(1, Math.max(...ends) - start)
  const failed = lanes.some(lane => lane.status === 'failed' || lane.status === 'stopped')
  const headline = lanes.length + (lanes.length === 1 ? ' agent ' : ' agents ') +
    (active ? running ? 'working' : 'waiting' : failed ? 'finished with interruptions' : 'finished')
  return <section className="agent-group" aria-label={headline}>
    <div className="agent-group-head">
      <button type="button" className="agent-group-toggle" aria-expanded={!!open} onClick={() => setFolded({ active, value: !open })}>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span> {headline}
      </button>
      {!open && lanes.map(lane => <button type="button" key={lane.id} className="agent-chip" title={lane.result.label}
        onClick={() => openWorkPanel(lane.result.outputId ? 'outputs' : 'agents', lane.result.outputId || lane.id)}>
        <Square variant={lane.result.variant} size={13} /> {skillName(lane.mode)} · {lane.result.label}
      </button>)}
    </div>
    {open && <ul className="agent-lanes">{lanes.map(lane => {
      const a = stamp(lane.started_at), b = laneEnd(lane, now)
      const timed = Number.isFinite(a) && Number.isFinite(b)
      return <li key={lane.id}>
        <button type="button" className="agent-lane" onClick={() => openWorkPanel('agents', lane.id)}>
          <span className={'agent-cover mode-' + coverOf(lane.mode as RouteMode)} aria-hidden="true" />
          <span className="agent-lane-top"><strong>{skillName(lane.mode)}</strong><span>{lane.stateLabel}</span>
            <Square variant={agentSquares[lane.status]} size={13} />{timed && <time>{duration(Math.max(0, b - a) / 1000)}</time>}</span>
          <span className="agent-objective"><Inline limit={150}>{lane.objective}</Inline></span>
          <span className="agent-step">{lane.step}</span>
          {timed && <span className="agent-axis" aria-hidden="true"><i style={{
            left: ((a - start) * 100 / span) + '%', width: Math.max(.5, (b - a) * 100 / span) + '%',
          }} /></span>}
        </button>
      </li>
    })}</ul>}
  </section>
}

function LiveWorker({ job, id, live }: { job: 'proof' | 'research'; id: string; live: NonNullable<ProofDetail['live']> }) {
  const stream = useLiveStream(job, id, live.file)
  return <StreamBody stream={stream} role={live.role} live />
}

function JobWork({ id, kind, chat }: { id: string; kind: 'proof' | 'research'; chat: string }) {
  const tick = useTick(id)
  const { data, error } = useLoad<ProofDetail | ResearchDetail>(() => kind === 'proof' ? api.proof(id) : api.research(id), [kind, id, tick])
  if (error) return <ErrorNote>{error}</ErrorNote>
  if (!data) return null
  return <div className="agent-job">
    {data.kind === 'proof' && data.candidates.length > 0 && <details><summary>Proof attempts ({data.candidates.length})</summary>
      <ol>{data.candidates.map(c => <li key={c.id}>{c.id} · {c.review_status.replaceAll('_', ' ')}</li>)}</ol></details>}
    {data.live && data.running && <LiveWorker job={kind} id={id} live={data.live} />}
    <a className="text-link" href={href(data.kind === 'proof' ? 'prove' : data.kind, id, null, chat)}>Open full job</a>
  </div>
}

function SavedAnswer({ chat, id }: { chat: string; id: string }) {
  const [open, setOpen] = useState(false)
  const { data, error } = useLoad(() => open ? api.savedChatAnswer(chat, id) : Promise.resolve(null), [open, chat, id])
  return <div><button className="btn btn-quiet btn-small" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
    {open ? 'Hide worker answer' : 'Read worker answer'}</button>
    {open && (error ? <ErrorNote>{error}</ErrorNote> : data ? data.answer ? <Markdown>{data.answer}</Markdown> :
      <p className="muted">No answer saved · {data.status}</p> : <p className="muted">Loading…</p>)}</div>
}

function timeline(lane: AgentLane, current: Activity[]) {
  const events = [...(lane.timeline || []), ...current.filter(e => e.child_id === lane.id ||
    (lane.card_index != null && e.card_index === lane.card_index) || (lane.job_id && e.id === lane.job_id))]
  const unique = new Map(events.map(e => [[e.time, e.kind, e.text].join('\\n'), e]))
  return [...unique.values()].sort((a, b) => a.time - b.time)
}

export function AgentsTab({ chat, selected, onSelect }: { chat: Chat; selected: string | null; onSelect: (id: string) => void }) {
  const app = useApp()
  const lanes = useMemo(() => {
    const rank: Record<AgentStatus, number> = { running: 0, waiting: 1, stopped: 2, failed: 2, finished: 2 }
    return deriveAgents(chat, app.proofs || [], app.research || []).sort((a, b) => rank[a.status] - rank[b.status])
  }, [chat, app.proofs, app.research])
  const lane = selected ? lanes.find(l => l.id === selected || l.job_id === selected || l.answer_id === selected ||
    (l.card_index != null && ['card-' + l.card_index, 'c-' + l.card_index].includes(selected))) : lanes[0]
  if (!lanes.length) return <p className="muted">This conversation has no delegated agents.</p>
  const task = app.snapshot.tasks?.find(t => t.task.origin_chat === chat.id || t.task.target === chat.id)
  const events = lane ? timeline(lane, task?.activity || []) : []
  return <div className="agent-inspector"><ul className="agent-picker">{lanes.map(l => <li key={l.id}>
    <button type="button" aria-pressed={lane?.id === l.id} onClick={() => onSelect(l.id)}>
      <Square variant={agentSquares[l.status]} size={14} /><strong>{skillName(l.mode)}</strong><span>{l.stateLabel}</span>
      <small><Inline limit={85}>{l.objective}</Inline></small>
    </button>
  </li>)}</ul>
    {lane ? <section className="agent-details" aria-label={'Selected ' + skillName(lane.mode) + ' agent'}>
      <h3>{skillName(lane.mode)}</h3><Markdown>{lane.objective}</Markdown>
      <p><Square variant={lane.result.variant} size={13} /> {lane.result.label}</p>
      {lane.step !== lane.result.label && <p className="muted">{lane.step}</p>}
      {(lane.usage?.output_tokens != null || lane.effort) && <p className="agent-metadata">
        {[lane.effort && lane.effort + ' effort', lane.usage?.output_tokens != null && count(lane.usage.output_tokens) + ' output tokens'].filter(Boolean).join(' · ')}
      </p>}
      {events.length > 0 && <ol className="agent-timeline" aria-label="Agent activity">{events.map((e, i) => <li key={i}>
        <time>{new Date(e.time * 1000).toLocaleTimeString()}</time><span>{e.text}</span></li>)}</ol>}
      {lane.answer_id && <SavedAnswer key={lane.answer_id} chat={chat.id} id={lane.answer_id} />}
      {lane.job_id && ((app.proofs || []).some(j => j.id === lane.job_id) || (app.research || []).some(j => j.id === lane.job_id)) &&
        <JobWork key={lane.job_id} id={lane.job_id} kind={lane.mode === 'prove' ? 'proof' : 'research'} chat={chat.id} />}
      {!lane.answer_id && lane.result.outputId && <button className="btn btn-small" onClick={() => openWorkPanel('outputs', lane.result.outputId)}>View output</button>}
    </section> : <p className="muted">The selected agent is unavailable.</p>}
  </div>
}
