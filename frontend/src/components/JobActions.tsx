// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Job page actions that change a saved job: resuming with an explicit extra allowance, and deletion.
// Both are explicit user actions; the server records an addition in the job's state and report.
// StopTurn ends the Assistant turn that is running the job.
import { useState } from 'react'
import { api, type AllowanceAdditions, type Budget, type TaskSummary } from '../api'
import { count, duration } from '../format'
import { go } from '../router'
import { refreshLists } from '../store'
import { ErrorNote, Modal, NumberField } from './common'
import { PlayIcon, StopIcon, TrashIcon } from './Icons'

type Usage = { tokens: Budget; seconds: Budget; rounds?: Budget }

const nearLimit = (budget: Budget) => budget.limit !== null && budget.limit > 0
  && budget.used + (budget.reserved ?? 0) + (budget.uncertain ?? 0) >= 0.8 * budget.limit

// The server's bounds for one addition (proof.py, research.py).
const MAX_TOKENS = 20_000_000, MAX_MINUTES = 1440, MAX_ROUNDS = 20

/** A sensible first proposal: one more solve and review for a proof, a quarter of the allowance for research.
    Without a cumulative token ceiling there is nothing to raise, so no tokens are proposed. */
function proposal(kind: 'proof' | 'research', usage: Usage, tokenStep: number) {
  const roundUp = (value: number) => Math.min(MAX_TOKENS, Math.max(1000, Math.ceil(value / 1000) * 1000))
  return {
    tokens: usage.tokens.limit === null ? 0 : kind === 'proof' ? roundUp(tokenStep) : roundUp(usage.tokens.limit / 4),
    minutes: nearLimit(usage.seconds) ? 30 : 0,
    rounds: kind === 'proof' && usage.rounds && usage.rounds.limit !== null && usage.rounds.used >= usage.rounds.limit ? 1 : 0,
  }
}

export function ResumeMore({ kind, id, usage, tokenStep, disabled }: {
  kind: 'proof' | 'research'; id: string; usage: Usage; tokenStep: number; disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" className="btn" disabled={disabled} onClick={() => setOpen(true)}
      title="Continue the saved work with a larger allowance that you choose">
      <PlayIcon size={16} /> Resume with more budget…
    </button>
    {open && <ResumeMoreDialog kind={kind} id={id} usage={usage} tokenStep={tokenStep} onClose={() => setOpen(false)} />}
  </>
}

function ResumeMoreDialog({ kind, id, usage, tokenStep, onClose }: {
  kind: 'proof' | 'research'; id: string; usage: Usage; tokenStep: number; onClose: () => void
}) {
  const initial = proposal(kind, usage, tokenStep)
  const [tokens, setTokens] = useState(initial.tokens)
  const [minutes, setMinutes] = useState(initial.minutes)
  const [rounds, setRounds] = useState(initial.rounds)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const whole = (value: number, max: number) => Number.isInteger(value) && value >= 0 && value <= max
  const ceiling = usage.tokens.limit !== null
  const valid = whole(tokens, ceiling ? MAX_TOKENS : 0) && whole(minutes, MAX_MINUTES) && whole(rounds, MAX_ROUNDS) && tokens + minutes + rounds > 0
  const additions: AllowanceAdditions = {
    ...(tokens > 0 ? { add_tokens: tokens } : {}),
    ...(minutes > 0 ? { add_seconds: minutes * 60 } : {}),
    ...(kind === 'proof' && rounds > 0 ? { add_rounds: rounds } : {}),
  }
  const parts = [
    tokens > 0 && `${count(tokens)} output tokens`,
    minutes > 0 && `${duration(minutes * 60)} of working time`,
    kind === 'proof' && rounds > 0 && `${rounds} ${rounds === 1 ? 'attempt' : 'attempts'}`,
  ].filter(Boolean) as string[]
  const limit = (budget: Budget | undefined, show: (value: number) => string) =>
    budget ? `${show(budget.used)} used${budget.limit === null ? '' : ` of ${show(budget.limit)}`}` : ''
  async function resume() {
    if (!valid || saving) return
    setSaving(true); setError('')
    try {
      await (kind === 'proof' ? api.resumeProof(id, true, additions) : api.resumeResearch(id, true, 0, additions))
      refreshLists()
      onClose()
    } catch (reason) { setError((reason as Error).message); setSaving(false) }
  }
  return <Modal title="Resume with more budget" icon={<PlayIcon />} onClose={saving ? undefined : onClose}>
    <form onSubmit={(event) => { event.preventDefault(); void resume() }}>
      <p className="modal-lede">The {kind === 'proof' ? 'proof' : 'job'} continues from its saved work with the extra allowance below. Nothing is added unless you resume here.{ceiling ? '' : ' It has no cumulative output limit, so only time' + (kind === 'proof' ? ' and attempts' : '') + ' can be added.'}</p>
      <div className="field-grid">
        {ceiling && <NumberField label="Extra output tokens" value={tokens} onChange={setTokens} min={0} max={MAX_TOKENS} step={1000}
          hint={limit(usage.tokens, count) + (kind === 'proof' ? `. One solve and review take up to ${count(tokenStep)}.` : '.')} />}
        <NumberField label="Extra working time" suffix="min" value={minutes} onChange={setMinutes} min={0} max={MAX_MINUTES} step={5}
          hint={limit(usage.seconds, duration) + '.'} />
        {kind === 'proof' && <NumberField label="Extra attempts" value={rounds} onChange={setRounds} min={0} max={MAX_ROUNDS}
          hint={limit(usage.rounds, (value) => String(value)) + (usage.rounds && usage.rounds.limit !== null && usage.rounds.used >= usage.rounds.limit ? '. All attempts are used: add at least one.' : '.')} />}
      </div>
      <p className="muted small" role="status">{parts.length
        ? `Adds ${parts.join(', ')} to this ${kind === 'proof' ? 'proof' : 'job'}'s allowance. The addition is recorded in its saved state and report.`
        : 'Choose at least one addition.'}</p>
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        <button type="button" className="btn" disabled={saving} onClick={onClose}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={!valid || saving}>{saving ? 'Resuming…' : 'Resume with this budget'}</button>
      </div>
    </form>
  </Modal>
}

/** Beside Pause on the page of a job the Assistant is running: stop the Assistant's turn for good.
    A job started on its own page has no turn to close, so it only pauses. */
export function StopTurn({ task, onError }: { task?: TaskSummary; onError: (message: string) => void }) {
  if (task?.kind !== 'chat') return null
  return <button type="button" className="btn btn-delete" disabled={!!task.killed}
    title="Stop the Assistant's turn for good: it cannot be resumed. This job keeps its saved work."
    onClick={() => { onError(''); api.kill(task.id).catch((reason: Error) => onError(reason.message)) }}>
    <StopIcon size={16} /> {task.killed ? 'Stopping…' : 'Stop'}
  </button>
}

export function DeleteProof({ id, disabled }: { id: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  async function remove() {
    setDeleting(true); setError('')
    try {
      await api.deleteProof(id)
      refreshLists()
      go('prove')
    } catch (reason) { setError((reason as Error).message); setDeleting(false) }
  }
  return <>
    <button type="button" className="btn btn-quiet btn-delete" disabled={disabled}
      title={disabled ? 'Pause the proof before deleting it' : 'Move this proof to the trash'} onClick={() => setOpen(true)}>
      <TrashIcon size={15} /> Delete…
    </button>
    {open && <Modal title="Delete this proof?" icon={<TrashIcon />} tone="danger" onClose={deleting ? undefined : () => setOpen(false)}>
      <p>The proof leaves the proof history and its saved files move to this workspace's trash, where they can still be recovered by hand. Conversations that started it keep their messages.</p>
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        <button type="button" className="btn" disabled={deleting} onClick={() => setOpen(false)}>Cancel</button>
        <button type="button" className="btn btn-delete" disabled={deleting} onClick={() => void remove()}>
          <TrashIcon size={16} /> {deleting ? 'Deleting…' : 'Delete proof'}
        </button>
      </div>
    </Modal>}
  </>
}
