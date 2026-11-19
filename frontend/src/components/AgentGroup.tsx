// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, type Activity, type AgentStatus, type Chat, type RouteMode } from '../api'
import { activeAgent, agentLabels, agentSquares, deriveAgents, skillName, stamp, type AgentLane } from '../agents'
import { duration } from '../format'
import { coverOf, MODE_CALLS } from '../modeCalls'
import { openWorkPanel } from '../router'
import { useApp } from '../store'
import { activityCaption } from '../workNotice'
import { ErrorNote, useLoad } from './common'
import { BackIcon, ChevronIcon } from './Icons'
import { Glyph } from './Glyph'
import { Inline, Markdown } from './Markdown'
import { Square } from './Square'
import '../agentGroups.css'

export function AgentSquares({ agents }: { agents: { id: string; mode: string; status: AgentStatus }[] }) {
  return <span className="agent-squares">{agents.slice(0, 8).map(agent =>
    <Square key={agent.id} variant={agentSquares[agent.status]} size={12} label={skillName(agent.mode) + ' · ' + agentLabels[agent.status]} />
  )}{agents.length > 8 && <span>+{agents.length - 8}</span>}</span>
}

function skillCover(mode: string) {
  return MODE_CALLS.some(call => call.mode === mode) ? coverOf(mode as RouteMode) : 'free'
}

function SkillCover({ mode }: { mode: string }) {
  const cover = skillCover(mode)
  return <span className={'agent-cover mode-' + cover} aria-hidden="true"><Glyph mode={cover} /></span>
}

function laneEnd(lane: AgentLane, now: number) {
  return lane.ended_at ? stamp(lane.ended_at) : lane.status === 'running' ? now : NaN
}

function requestText(value: string) {
  return value.replace(/^\s*@\w+\s*/u, '').replace(/\s+/gu, ' ').trim()
}

function SingleAgent({ lane, prompt }: { lane: AgentLane; prompt?: string }) {
  const showObjective = !!lane.objective && requestText(lane.objective) !== requestText(prompt || '')
  return <section className="agent-single" aria-label={skillName(lane.mode) + ' activity'}>
    <SkillCover mode={lane.mode} />
    <div className="agent-single-copy">
      <h3>{skillName(lane.mode)}</h3>
      {showObjective && <div className="agent-single-question"><Inline mathProse limit={210}>{lane.objective}</Inline></div>}
      <p className="agent-single-phase"><Square variant={agentSquares[lane.status]} size={12} />{lane.step || lane.stateLabel}</p>
      <div className="agent-single-actions">
        {lane.notebook && <NotebookAction lane={lane} primary />}
        <button type="button" className="agent-single-activity" onClick={() => openWorkPanel('agents', lane.id)}>
          View activity <ChevronIcon size={12} />
        </button>
      </div>
    </div>
  </section>
}

export function AgentGroup({ lanes, prompt }: { lanes: AgentLane[]; prompt?: string }) {
  return lanes.length === 1 && activeAgent(lanes[0])
    ? <SingleAgent lane={lanes[0]} prompt={prompt} /> : <AgentBatch lanes={lanes} />
}

function AgentBatch({ lanes }: { lanes: AgentLane[] }) {
  const id = useId()
  const active = lanes.some(activeAgent)
  const running = lanes.filter(lane => lane.status === 'running').length
  const [folded, setFolded] = useState<{ active: boolean; value: boolean } | null>(null)
  const open = folded?.active === active ? folded.value : active
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
  const interrupted = lanes.filter(lane => lane.status === 'failed' || lane.status === 'stopped').length
  const title = active ? (running > 1 ? 'Working in parallel' : running ? 'Work in progress' : 'Waiting to start')
    : interrupted === lanes.length ? 'Work interrupted' : interrupted ? 'Work summary' : 'Completed work'
  return <section className="agent-group" aria-label={title}>
    <button type="button" className="agent-group-toggle" aria-expanded={open} aria-controls={id}
      onClick={() => setFolded({ active, value: !open })}>
      <span className="agent-group-title">{title}</span>
      <span className="agent-group-count">{lanes.length} {lanes.length === 1 ? 'task' : 'tasks'}</span>
      <ChevronIcon size={15} />
    </button>
    {open ? <ul id={id} className="agent-lanes">{lanes.map(lane => {
      const a = stamp(lane.started_at), b = laneEnd(lane, now)
      const timed = Number.isFinite(a) && Number.isFinite(b)
      return <li key={lane.id}>
        <div className="agent-lane">
          <SkillCover mode={lane.mode} />
          <button className="agent-row-link" type="button" onClick={() => openWorkPanel('agents', lane.id)}>
            {skillName(lane.mode)}<span className="agent-sr-only"> · {lane.objective}</span>
          </button>
          <span className="agent-state"><Square variant={lane.result.variant} size={13} />
            {lane.status === 'finished' ? lane.result.label : lane.stateLabel}
            {timed && <time>{duration(Math.max(0, b - a) / 1000)}</time>}
          </span>
          <span className="agent-objective"><Inline mathProse limit={210}>{lane.objective}</Inline></span>
          {lane.step && lane.step !== lane.result.label && activeAgent(lane) && lane.status !== 'waiting' &&
            <span className="agent-step">{lane.step}</span>}
          {lane.notebook && <NotebookAction lane={lane} primary={activeAgent(lane)} />}
          {timed && lanes.length > 1 && <span className="agent-axis" aria-hidden="true">
            <i className={'mode-' + skillCover(lane.mode)} style={{
              left: ((a - start) * 100 / span) + '%', width: Math.max(.7, (b - a) * 100 / span) + '%',
            }} /></span>}
        </div>
      </li>
    })}</ul> : <ul id={id} className="agent-results">{lanes.map(lane => {
      const content = <><SkillCover mode={lane.mode} /><span>{lane.notebook?.label || skillName(lane.mode)}</span>
        <span className="agent-chip-outcome"><Square variant={lane.result.variant} size={12} />{lane.result.label}</span>
        <ChevronIcon size={12} /></>
      return <li key={lane.id}>{lane.notebook
        ? <a className="agent-chip" href={lane.notebook.url}>{content}</a>
        : <button type="button" className="agent-chip" onClick={() =>
          openWorkPanel(lane.result.outputId ? 'outputs' : 'agents', lane.result.outputId || lane.id)}>{content}</button>}</li>
    })}</ul>}
  </section>
}

function NotebookAction({ lane, primary = false }: { lane: AgentLane; primary?: boolean }) {
  if (!lane.notebook) return null
  return <a className={'agent-native-link' + (primary ? ' btn btn-primary btn-small' : '')} href={lane.notebook.url}>
    {lane.notebook.label} <ChevronIcon size={14} />
  </a>
}

function SavedAnswer({ chat, id }: { chat: string; id: string }) {
  const [open, setOpen] = useState(false)
  const { data, error } = useLoad(() => open ? api.savedChatAnswer(chat, id) : Promise.resolve(null), [open, chat, id])
  return <div className="agent-answer"><button className="btn btn-small" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
    {open ? 'Hide answer' : 'Read answer'}</button>
    {open && (error ? <ErrorNote>{error}</ErrorNote> : data ? data.answer ? <Markdown>{data.answer}</Markdown> :
      <p className="muted">No answer was saved.</p> : <p className="muted">Loading…</p>)}</div>
}

function timeline(lane: AgentLane, current: Activity[]) {
  const events = [...lane.timeline || [], ...current.filter(e => e.child_id === lane.id ||
    (lane.card_index != null && e.card_index === lane.card_index) || (lane.job_id && e.id === lane.job_id))]
  const observations = new Map<string, { time: number; text: string }>()
  for (const event of events) {
    const text = activityCaption(event)
    if (text && Number.isFinite(event.time)) observations.set(event.time + '/' + text, { time: event.time, text })
  }
  return [...observations.values()].sort((a, b) => a.time - b.time)
}

function TaskList({ title, lanes, onSelect }: { title: string; lanes: AgentLane[]; onSelect: (id: string) => void }) {
  if (!lanes.length) return null
  return <section className="agent-task-section"><h3>{title}</h3>
    <ul className="agent-picker">{lanes.map(lane => <li key={lane.id} className="agent-choice">
      <SkillCover mode={lane.mode} />
      <span className="agent-choice-copy">
        <button type="button" className="agent-row-link" data-task={lane.id} onClick={() => onSelect(lane.id)}>
          {skillName(lane.mode)}<span className="agent-sr-only"> · {lane.objective}</span>
        </button>
        <span className="agent-choice-objective"><Inline mathProse limit={150}>{lane.objective}</Inline></span>
        <span className="agent-choice-status"><Square variant={lane.result.variant} size={12} />
          {activeAgent(lane) && lane.step ? lane.step : lane.result.label}</span>
        {lane.notebook && <NotebookAction lane={lane} primary={activeAgent(lane)} />}
      </span>
      <ChevronIcon size={14} />
    </li>)}</ul>
  </section>
}

export function AgentsTab({ chat, selected, onSelect }: { chat: Chat; selected: string | null; onSelect: (id: string | null) => void }) {
  const app = useApp()
  const lanes = useMemo(() => deriveAgents(chat, app.proofs || [], app.research || []), [chat, app.proofs, app.research])
  const lane = selected ? lanes.find(l => l.id === selected || l.job_id === selected || l.answer_id === selected ||
    (l.card_index != null && ['card-' + l.card_index, 'c-' + l.card_index].includes(selected))) : undefined
  const task = app.snapshot.tasks?.find(t => t.task.origin_chat === chat.id || t.task.target === chat.id)
  const backButton = useRef<HTMLButtonElement>(null)
  const inspector = useRef<HTMLDivElement>(null)
  const lastSelected = useRef<string | null>(selected)
  useEffect(() => {
    const previous = lastSelected.current
    lastSelected.current = selected
    if (previous === selected) return
    if (selected) backButton.current?.focus({ preventScroll: true })
    else if (previous) inspector.current?.querySelector<HTMLButtonElement>('[data-task="' + CSS.escape(previous) + '"]')?.focus({ preventScroll: true })
  }, [selected])
  if (!lanes.length) return <p className="empty-note">No specialist work in this conversation yet.</p>
  if (!selected) return <div className="agent-inspector" ref={inspector}>
    <TaskList title="In progress" lanes={lanes.filter(l => l.status === 'running')} onSelect={onSelect} />
    <TaskList title="Waiting" lanes={lanes.filter(l => l.status === 'waiting')} onSelect={onSelect} />
    <TaskList title="Work history" lanes={lanes.filter(l => !activeAgent(l)).sort((a, b) =>
      (stamp(b.ended_at || b.started_at) || 0) - (stamp(a.ended_at || a.started_at) || 0))} onSelect={onSelect} />
  </div>
  const events = lane ? timeline(lane, task?.activity || []) : []
  return <div className="agent-inspector">
    <button className="agent-back" ref={backButton} onClick={() => onSelect(null)} type="button"><BackIcon size={15} /> All activity</button>
    {lane ? <section className="agent-details" key={lane.id} aria-label={skillName(lane.mode) + ' activity'}>
      <header><SkillCover mode={lane.mode} /><h3>{skillName(lane.mode)}</h3></header>
      {lane.notebook && <NotebookAction lane={lane} primary />}
      <div className="agent-question"><Markdown mathProse>{lane.objective}</Markdown></div>
      <p className="agent-outcome"><Square variant={lane.result.variant} size={15} /> {lane.result.label}</p>
      {lane.step && lane.step !== lane.result.label && <p className="agent-outcome-note">{lane.step}</p>}
      {lane.answer_id && <SavedAnswer key={lane.answer_id} chat={chat.id} id={lane.answer_id} />}
      {!lane.answer_id && !lane.notebook && lane.result.outputId && <button className="btn btn-small" onClick={() => openWorkPanel('outputs', lane.result.outputId)}>View result <ChevronIcon size={13} /></button>}
      {events.length > 0 && <details className="agent-observations"><summary>Recent activity</summary><ol className="agent-timeline" aria-label="Activity history">
        {events.map(e => <li key={e.time + e.text}><span>{e.text}</span>
          <time dateTime={new Date(e.time * 1000).toISOString()}>{new Date(e.time * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</time></li>)}
      </ol></details>}
    </section> : <p className="empty-note">This task is unavailable.</p>}
  </div>
}
