// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, type WorkspaceFile } from '../api'
import { WorkspaceSwitch } from '../components/Shell'
import { Collapse, ErrorNote, FilePicker, NumberField, OptionalNumberField } from '../components/common'
import { describeLimits, effortLimits } from '../effort'
import { Markdown } from '../components/Markdown'
import { useDropTarget } from '../drop'
import { go } from '../router'
import { useApp, useTick } from '../store'
import { capacityFull } from '../concurrency'
import { BusyNote, queuedMessage } from './shared'

// File names in the statement, as the terminal notices them (proof jobs have no file tools).
const MENTIONED = /(?<![\w/])[\w./-]+\.(?:tex|md|txt|pdf)\b/g

export function ProofNew() {
  const { status, snapshot } = useApp()
  const defaults = status?.defaults
  const [goal, setGoal] = useState('')
  const [files, setFiles] = useState<string[]>([])
  const [rounds, setRounds] = useState(5)
  const [tokens, setTokens] = useState<number | null>(null)
  const [minutes, setMinutes] = useState(30)
  const [solveTokens, setSolveTokens] = useState(32768)
  const [verifyTokens, setVerifyTokens] = useState(16384)
  const [ctx, setCtx] = useState(40960)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuedNote, setQueuedNote] = useState('')
  const [workspace, setWorkspace] = useState<WorkspaceFile[]>([])
  const changed = useTick('files')

  const uploading = useDropTarget('Pinned to this proof as sources', (paths) => {
    setFiles((items) => [...new Set([...items, ...paths])])
  })

  useEffect(() => {
    if (!defaults) return
    const limits = effortLimits('proof', defaults)
    setRounds(limits.rounds!)
    setTokens(limits.tokens)
    setMinutes(Math.round(limits.seconds / 60))
    setSolveTokens(limits.solve_tokens!)
    setVerifyTokens(limits.verify_tokens!)
    setCtx(defaults.ctx)
  }, [defaults])

  useEffect(() => {
    api.files().then((result) => setWorkspace(result.files), () => setWorkspace([]))
  }, [changed])

  const unpinned = useMemo(() => {
    const known = new Set(workspace.filter((file) => file.kind === 'text' || file.kind === 'pdf').map((file) => file.path))
    return [...new Set(goal.match(MENTIONED) || [])].filter((name) => known.has(name) && !files.includes(name))
  }, [goal, files, workspace])


  const running = capacityFull(snapshot, status)
  // The rules the server applies, said with the labels of this form.
  const invalid = (value: number, low: number, high = Number.MAX_SAFE_INTEGER) => !Number.isInteger(value) || value < low || value > high
  const limitError = invalid(rounds, 1) ? 'Attempts must be a positive whole number.'
    : tokens !== null && invalid(tokens, 512) ? 'The total output token limit must be at least 512 tokens, or blank for no limit.'
    : invalid(minutes, 1) ? 'Time must be at least 1 minute.'
    : invalid(solveTokens, 128) ? 'The solve output ceiling must be at least 128 tokens.'
    : invalid(verifyTokens, 128) ? 'The review output ceiling must be at least 128 tokens.'
    : invalid(ctx, 2048) ? 'Context must be at least 2,048 tokens.' : ''
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (uploading) return
    if (limitError) { setError(limitError); return }
    setBusy(true)
    setError('')
    setQueuedNote('')
    try {
      const result = await api.startProof({
        goal, source_files: files, rounds, tokens, seconds: minutes * 60,
        solve_tokens: solveTokens, verify_tokens: verifyTokens, ctx: ctx !== defaults?.ctx ? ctx : undefined,
        queue: true,
      })
      if (result.task.state === 'queued') {
        // Waiting for the model: clear the form for the next job.
        setQueuedNote(queuedMessage(result.task.title))
        setGoal('')
        setFiles([])
      } else if (result.id) go('prove', result.id)
      else setError(result.task.error || 'The proof did not start.')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sheet paper">
      <form className="sheet-inner compose" onSubmit={submit} noValidate>
        <div className="entry"><span className="in-margin" /><WorkspaceSwitch /></div>
        <div className="entry">
          <span className="in-margin margin-glyph" aria-hidden="true">⊢</span>
          <div>
            <h1 className="page-title">Prove a statement</h1>
            <p className="lede">
              The solver works on your exact statement. A fresh verifier checks the entire proof or counterexample
              and reviews any repair. Both roles can consult published papers and, when the Python sandbox is
              available, check a calculation. They use the same model in separate contexts. You can inspect the
              candidate, the evidence and the verifier’s judgment.
            </p>
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <label className="field">
            <span className="field-label">Statement and instructions</span>
            <textarea className="goal-input" rows={6} value={goal} onChange={(event) => setGoal(event.target.value)}
              placeholder={'Let $(u_n)$ be bounded in $H^1(0,1)$. Prove that $(u_n)$ has a subsequence converging strongly in $L^2(0,1)$.'}
              required />
            <span className="field-hint">Write LaTeX between dollar signs. The job does not see earlier conversations, so include every hypothesis.</span>
          </label>
        </div>

        {goal.includes('$') || goal.includes('\\(') ? (
          <div className="entry">
            <span className="in-margin muted small">Preview</span>
            <Markdown className="goal-preview">{goal}</Markdown>
          </div>
        ) : null}

        <div className="entry">
          <span className="in-margin" />
          <div className="field">
            <span className="field-label">Pinned sources</span>
            <FilePicker selected={files} onChange={setFiles} allowImages allowPdf />
            <span className="field-hint">The job saves a fixed snapshot of each pinned file. PDF extraction can alter mathematical notation.</span>
            {unpinned.length > 0 && (
              <p className="warning-note">
                Named in the statement but not pinned, so the model will not see {unpinned.length === 1 ? 'it' : 'them'}: {unpinned.join(', ')}.{' '}
                <button type="button" className="link-btn" onClick={() => setFiles([...files, ...unpinned])}>Pin {unpinned.length === 1 ? 'it' : 'them'}</button>
              </p>
            )}
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div>
            <p className="field-hint">{describeLimits('proof', { rounds, tokens, seconds: minutes * 60, solve_tokens: solveTokens, verify_tokens: verifyTokens })}.</p>
            <Collapse summary="Advanced limits">
              <div className="field-grid">
                <NumberField label="Attempts" value={rounds} onChange={setRounds} min={1}
                  hint="The initial solve and each repair or continuation" />
                <OptionalNumberField label="Total output token limit" value={tokens} onChange={setTokens}
                  hint="Optional. Includes thinking across all calls; leave blank for no cumulative limit." />
                <NumberField label="Time" value={minutes} onChange={setMinutes} min={1} suffix="min" />
                <NumberField label="Solve output ceiling" value={solveTokens} onChange={setSolveTokens} min={128} step={128}
                  hint="Tokens per solve, including thinking" />
                <NumberField label="Review output ceiling" value={verifyTokens} onChange={setVerifyTokens} min={128} step={128}
                  hint="Tokens per whole-proof review, including thinking" />
                <NumberField label="Context" value={ctx} onChange={setCtx} min={2048} step={1024}
                  hint="At least 2,048 tokens. Must hold the problem, the pinned text and a full answer." />
              </div>
            </Collapse>
          </div>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div className="form-actions">
            <ErrorNote>{error}</ErrorNote>
            {queuedNote && <p className="queued-note" role="status">{queuedNote}</p>}
            {running && <BusyNote queue />}
            <button type="submit" className="btn btn-primary btn-large" disabled={busy || !!uploading || !goal.trim()}>
              {uploading ? 'Uploading…' : busy ? 'Starting…' : running ? 'Add proof to the queue' : 'Start proof'}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
