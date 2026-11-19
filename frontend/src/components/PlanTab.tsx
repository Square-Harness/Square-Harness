// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useState } from 'react'
import { api, type Chat } from '../api'
import { chatWork, taskById } from '../concurrency'
import { count, duration, plural } from '../format'
import { PlanSteps } from './InvestigationReading'
import { taskNotice } from '../workNotice'
import { openWorkPanel } from '../router'
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

const planStates: Record<string, string> = {
  frame: 'Preparing', planned: 'Ready', approval: 'Preparing', running: 'In progress', done: 'Finished',
  failed: 'Interrupted', declined: 'Declined', stopped: 'Stopped', refused: 'Plan unavailable',
  clarification: 'Clarification needed',
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
  const paused = investigation.current && chat.assistant_status === 'paused'
  const phase = paused ? 'Paused' : planStates[investigation.phase] || 'Preparing'
  const tasks = investigation.tasks
  const finished = tasks.filter(task => taskMark(task.status) === 'complete').length
  return <section className="work-tab-content work-plan">
    <div className="work-section-head"><h3>Research question</h3>
      <span className="work-state" role="status"><Square variant={paused ? 'paused' : taskMark(investigation.phase)} size={13} /> {phase}</span>
    </div>
    {investigation.statement ? <div className="work-question"><Markdown mathProse>{investigation.statement}</Markdown></div> :
      <p className="muted">Preparing the statement…</p>}
    {tasks.length > 0 && <>
      <div className="work-section-head work-step-head"><h3>Planned work</h3>
        <small>{finished ? finished + ' of ' + tasks.length + ' finished' : plural(tasks.length, 'step')}</small></div>
      <PlanSteps tasks={tasks} renderAfter={id => {
        const task = tasks.find(item => item.id === id)
        if (!task) return null
        return <>
          {task.reason && task.status !== 'complete' && <Markdown className="work-step-note">{taskNotice(task.reason, tasks)}</Markdown>}
          {task.child_id && (chat.agents || []).some(agent => agent.id === task.child_id) && <button type="button"
            className="work-text-link" onClick={() => openWorkPanel('agents', task.child_id)}>View activity <span aria-hidden="true">→</span></button>}
        </>
      }} />
    </>}
    {chat.transcript.some(item => item.investigation_id === investigation.id && item.content) &&
      <button type="button" className="btn btn-small" onClick={() => openWorkPanel('outputs', investigation.id)}>
        Read investigation report <span aria-hidden="true">→</span>
      </button>}
    {stoppingAllowed && <button type="button" className="btn work-stop" disabled={pending} onClick={stop}>{pending ? 'Preparing the report…' : 'Stop and report'}</button>}
    <ErrorNote>{error}</ErrorNote>
    {(Object.keys(ceiling).length > 0 || investigation.usage) && <details className="work-fold work-resources">
      <summary>Time and usage</summary>
      {Object.keys(ceiling).length > 0 && <div className="work-plan-limits"><h4>Planned limits</h4>
        <dl>{ceiling.seconds != null && <><dt>Active time</dt><dd>{duration(ceiling.seconds)}</dd></>}
          {ceiling.requests != null && <><dt>Source requests</dt><dd>{count(ceiling.requests)}</dd></>}
          {ceiling.tokens != null && <><dt>Output tokens</dt><dd>{count(ceiling.tokens)}</dd></>}
          {ceiling.input_tokens != null && <><dt>Input tokens</dt><dd>{count(ceiling.input_tokens)}</dd></>}
        </dl></div>}
      {investigation.usage && <div className="work-plan-meters"><h4>Used so far</h4><UsageMeters budget={investigation.usage} /></div>}
    </details>}
  </section>
}
