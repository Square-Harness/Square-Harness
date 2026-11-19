// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useState } from 'react'
import { api, type Chat } from '../api'
import { chatWork, taskById } from '../concurrency'
import { count, duration } from '../format'
import { modeLabel } from '../outputs'
import { useApp } from '../store'
import { ErrorNote, UsageMeters } from './common'
import { Markdown } from './Markdown'
import { Square, type Variant } from './Square'

function taskMark(status: string): Variant {
  switch (status) {
    case 'running': return 'running'
    case 'complete': case 'completed': case 'done': case 'finished': return 'complete'
    case 'failed': case 'error': return 'halted'
    case 'paused': case 'interrupted': return 'paused'
    case 'skipped': case 'blocked': case 'stopped': case 'cancelled': return 'halted'
    default: return 'waiting'
  }
}

export function PlanTab({ chat }: { chat: Chat }) {
  const investigation = chat.investigation
  const { snapshot } = useApp()
  const work = chatWork(snapshot, chat.id)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  if (!investigation) return <p className="empty-note">No investigation has been registered for this conversation.</p>
  const ceiling = investigation.ceiling || {}
  const activeRun = investigation.current && investigation.approved && investigation.phase === 'running'
  const stoppingAllowed = investigation.current && (investigation.can_stop || (!!work.active && activeRun))
  async function stop() {
    if (!investigation || !stoppingAllowed || pending) return
    setPending(true)
    setError('')
    try {
      if (work.active && activeRun) {
        const taskId = work.active.task.id
        await api.pause(taskId, investigation.id, chat.id)
        const deadline = Date.now() + 90_000
        while (taskById(await api.task(), taskId)) {
          if (Date.now() >= deadline) throw new Error('The workers are still pausing. Try again when their checkpoints are saved.')
          await new Promise(resolve => setTimeout(resolve, 900))
        }
      }
      await api.stopInvestigation(chat.id, investigation.id)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setPending(false)
    }
  }
  return <section className="work-tab-content">
    <h3>{investigation.approved ? 'Confirmed statement' : 'Proposed statement'}</h3>
    {investigation.statement ? <Markdown>{investigation.statement}</Markdown> : <p className="muted">Preparing the statement…</p>}
    <p className="work-tab-context" role="status">{investigation.phase.replace(/_/g, ' ')}</p>
    <ol className="work-plan-list">{investigation.tasks.map(task => <li key={task.id}>
      <Square variant={taskMark(task.status)} label={task.status} size={17} /><div><strong>{modeLabel(task.mode)}</strong>
      <span className="work-plan-state">{task.status.replace(/_/g, ' ')}</span><p>{task.objective}</p>
      <small>{task.id}{task.effort ? ' · ' + task.effort + ' effort' : ''}
      {task.after.length > 0 && ' · after ' + task.after.join(', ')}</small>
      {task.reason && <p className="muted small">{task.reason}</p>}</div></li>)}</ol>
    {Object.keys(ceiling).length > 0 && <div className="work-plan-limits"><h3>{investigation.approved ? 'Approved limits' : 'Proposed limits'}</h3>
      <dl>{ceiling.tokens != null && <><dt>Output tokens</dt><dd>{count(ceiling.tokens)}</dd></>}
        {ceiling.input_tokens != null && <><dt>Input tokens</dt><dd>{count(ceiling.input_tokens)}</dd></>}
        {ceiling.seconds != null && <><dt>Active time</dt><dd>{duration(ceiling.seconds)}</dd></>}
        {ceiling.requests != null && <><dt>Source requests</dt><dd>{count(ceiling.requests)}</dd></>}
      </dl></div>}
    {investigation.usage && <div className="work-plan-meters"><h3>Usage</h3><UsageMeters budget={investigation.usage} /></div>}
    {stoppingAllowed && <button type="button" className="btn" disabled={pending} onClick={stop}>{pending ? 'Finishing the report…' : 'Stop and report'}</button>}
    <ErrorNote>{error}</ErrorNote>
  </section>
}
