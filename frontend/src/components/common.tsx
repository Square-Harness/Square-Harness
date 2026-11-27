// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api, type Budget, type WorkProgress, type WorkspaceFile } from '../api'
import { meterDisplay } from '../usage'
import { workProgressText } from '../workProgress'
import { taskById } from '../concurrency'
import { bytes, count, duration } from '../format'
import { useApp, useTick } from '../store'
import { useRoute } from '../router'
import { CheckIcon, CloseIcon, CopyIcon, FileIcon, PinIcon } from './Icons'
import { isImagePath, PinnedImages } from './ImageAttachments'

/** A changing revision queues a single trailing refresh. Changing the resource discards its old requests. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[], revision?: string | number) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const current = useRef<(() => void) | null>(null)
  const reload = useCallback(() => { current.current?.() }, [])
  const request = useCallback(load, deps)
  useEffect(() => {
    let active = true
    let busy = false
    let again = false
    current.current = async () => {
      if (busy) { again = true; return }
      busy = true
      do {
        again = false
        try {
          const value = await request()
          if (active) { setData(value); setError('') }
        } catch (reason) {
          if (active) setError((reason as Error).message)
        }
      } while (active && again)
      busy = false
    }
    return () => { active = false; current.current = null }
  }, [request])
  useEffect(reload, [request, revision, reload])
  return { data, error, reload }
}

/** `calm`: reaching the limit is normal (one attempt of one used), so the bar never turns red. */
export function Meter({ label, used, limit, unit = '', decimals = 0, title, format, calm = false }: { label: string; used: number; limit: number | null; unit?: string; decimals?: number; title?: string; format?: (value: number) => string; calm?: boolean }) {
  const display = meterDisplay({ used, limit }, unit, decimals, format)
  const tone = display.ratio === null || calm ? '' : display.ratio >= 1 ? ' meter-full' : display.ratio > 0.85 ? ' meter-high' : ''
  return (
    <div className={'meter' + tone} title={title}>
      <div className="meter-text">
        <span>{label}</span>
        <span className="meter-value">{display.used}{display.limit && <> <span className="meter-of">of {display.limit}</span></>}</span>
      </div>
      {limit !== null && <div className="meter-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}>
        <div className="meter-bar" style={{ transform: `scaleX(${display.ratio})` }} />
      </div>}
    </div>
  )
}

/** Usage is cumulative; a reservation is capacity for an unfinished call. */
export function UsageMeters({ budget }: { budget: { tokens: Budget; input_tokens?: Budget; seconds: Budget; context?: Budget } }) {
  return <>
    <Meter label="Reported output tokens" {...budget.tokens} title="Cumulative output reported by the model, including thinking." />
    {!!budget.tokens.reserved && <p className="muted small">{count(budget.tokens.reserved)} tokens held for active calls.</p>}
    {!!budget.tokens.uncertain && <p className="muted small">{count(budget.tokens.uncertain)} tokens held for calls with unknown usage.</p>}
    {budget.input_tokens && <Meter label="Cumulative input tokens" {...budget.input_tokens} title="Input summed across model calls; repeated text counts again. This is not the current context size." />}
    {budget.context && <Meter label="Current input context" {...budget.context} title="Input to the current model call, against its context capacity. Generated output also uses this capacity." />}
    <Meter label="Working time" used={budget.seconds.used} limit={budget.seconds.limit} format={duration} />
  </>
}

export function ModelWaitNote({ taskId }: { taskId?: string }) {
  const { snapshot } = useApp()
  const waiting = taskById(snapshot, taskId)?.task.waiting_model_calls || 0
  if (!waiting) return null
  return <p className="muted small" role="status">{waiting === 1
    ? 'The model provider accepted the request. Waiting for its first output.'
    : `The model provider accepted ${waiting} requests that are still waiting for their first output.`}</p>
}

export function WorkProgressNote({ progress, status, running = false, pipeline, taskId }: { progress?: WorkProgress | null; status: string; running?: boolean; pipeline?: string | null; taskId?: string }) {
  return <>{progress && <div className="work-progress" role="status" title={progress.scope || undefined}>
    <p className="muted small">{workProgressText(progress, status, running, pipeline)}</p>
  </div>}{running && taskId && <ModelWaitNote taskId={taskId} />}</>
}

export function Collapse({ summary, children, open, className }: { summary: ReactNode; children: ReactNode; open?: boolean; className?: string }) {
  return (
    <details className={'collapse' + (className ? ' ' + className : '')} open={open}>
      <summary>{summary}</summary>
      <div className="collapse-body">{children}</div>
    </details>
  )
}

export type Tab = { id: string; label: string; badge?: ReactNode; hidden?: boolean; narrowOnly?: boolean }

export function Tabs({ tabs, active, onChange }: { tabs: Tab[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" aria-label="Page sections">
      {tabs.filter((tab) => !tab.hidden).map((tab) => (
        <button key={tab.id} type="button" aria-pressed={tab.id === active}
          className={'tab' + (tab.id === active ? ' tab-active' : '') + (tab.narrowOnly ? ' tab-narrow' : '')}
          onClick={() => onChange(tab.id)}>
          {tab.label}{tab.badge !== undefined && <span className="tab-badge">{tab.badge}</span>}
        </button>
      ))}
    </div>
  )
}

export function NumberField({ label, value, onChange, min, max, step = 1, hint, suffix }: {
  label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; hint?: string; suffix?: string
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <span className="field-input">
        <input type="number" inputMode="numeric" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step}
          onChange={(event) => onChange(event.target.valueAsNumber)} />
        {suffix && <span className="field-suffix">{suffix}</span>}
      </span>
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

/** A small whole number chosen on a row of cells, like squares filled on grid paper. */
export function CellScale({ label, value, onChange, min = 1, max, hint, values }: {
  label: string; value: number; onChange: (value: number) => void; min?: number; max: number; hint?: string
  /** The values offered, when not every integer from min to max (a long range). */
  values?: number[]
}) {
  const id = useId()
  const cells = values ?? Array.from({ length: max - min + 1 }, (_, index) => min + index)
  const row = useRef<HTMLDivElement>(null)
  const keys = (event: ReactKeyboardEvent) => {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowUp' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 0
    if (!step) return
    event.preventDefault()
    const at = Math.max(0, cells.findIndex((cell) => cell >= value))
    const index = Math.min(cells.length - 1, Math.max(0, at + step))
    onChange(cells[index])
    row.current?.querySelectorAll<HTMLButtonElement>('button')[index]?.focus()
  }
  return (
    <div className="field">
      <span className="field-label" id={id}>{label}</span>
      <div className="cells" role="radiogroup" aria-labelledby={id} ref={row} onKeyDown={keys}>
        {cells.map((cell) => (
          <button key={cell} type="button" role="radio" aria-checked={cell === value} tabIndex={cell === value ? 0 : -1}
            className={'cell' + (cell <= value ? ' cell-on' : '') + (cell === value ? ' cell-current' : '')}
            onClick={() => onChange(cell)}>{cell}</button>
        ))}
      </div>
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

export function OptionalNumberField({ label, value, onChange, step = 1000, hint }: {
  label: string; value: number | null; onChange: (value: number | null) => void; step?: number; hint?: string
}) {
  return <label className="field">
    <span className="field-label">{label}</span>
    <span className="field-input">
      <input type="number" inputMode="numeric" value={value === null ? '' : value} min={step} step={step} placeholder="No limit"
        onChange={(event) => onChange(event.target.value === '' ? null : event.target.valueAsNumber)} />
    </span>
    {hint && <span className="field-hint">{hint}</span>}
  </label>
}

export function Toggle({ label, checked, onChange, disabled, hint }: {
  label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean; hint?: string
}) {
  return (
    <label className={'toggle' + (disabled ? ' toggle-disabled' : '')}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span className="toggle-track" aria-hidden="true"><span className="toggle-thumb" /></span>
      <span className="toggle-text">
        <span>{label}</span>
        {hint && <span className="field-hint">{hint}</span>}
      </span>
    </label>
  )
}

export function FilePicker({ selected, onChange, allowPdf = false, allowImages = false, accept, label = 'Pin workspace files' }: {
  selected: string[]; onChange: (files: string[]) => void; allowPdf?: boolean; allowImages?: boolean; accept?: string[]; label?: string
}) {
  const app = useApp()
  const route = useRoute()
  const chatId = ['free', 'critic', 'explore'].includes(route.mode) ? route.id : route.from
  const projectId = chatId ? app.chats?.find((chat) => chat.id === chatId)?.project_id : app.projectFilter
  const project = app.projects?.find((item) => item.id === projectId)
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [error, setError] = useState('')
  const changed = useTick('files')
  useEffect(() => { setFiles(null); setTruncated(false); setError('') }, [changed])  // a dropped file or another folder
  useEffect(() => {
    if (!open || files) return
    api.files().then((result) => { setFiles(result.files); setTruncated(!!result.truncated); setError('') }, (reason: Error) => setError(reason.message))
  }, [open, files])
  const preferred = (file: WorkspaceFile) => !!project && file.path.startsWith(project.folder.replace(/\/$/, '') + '/')
  const available = (files || []).filter((file) => (isImagePath(file.path) ? allowImages : file.kind === 'text' || (allowPdf && file.kind === 'pdf'))
    && (!accept || accept.some((suffix) => file.path.toLowerCase().endsWith(suffix))))
    .sort((a, b) => Number(preferred(b)) - Number(preferred(a)) || a.path.localeCompare(b.path))
  const toggle = (path: string) =>
    onChange(selected.includes(path) ? selected.filter((item) => item !== path) : [...selected, path])
  return (
    <div className="picker">
      <div className="picker-row">
        {selected.filter((path) => !allowImages || !isImagePath(path)).map((path) => (
          <span key={path} className="chip">
            <FileIcon size={14} />
            <span>{path}</span>
            <button type="button" className="chip-remove" aria-label={`Unpin ${path}`} onClick={() => toggle(path)}>
              <CloseIcon size={13} />
            </button>
          </span>
        ))}
        <button type="button" className="btn btn-quiet" onClick={() => setOpen(!open)} aria-expanded={open}>
          <PinIcon size={16} /> {open ? 'Done' : label}
        </button>
      </div>
      {allowImages && <PinnedImages paths={selected} onRemove={(path) => onChange(selected.filter((item) => item !== path))} />}
      {allowImages && app.status?.image_support === false && <p className="muted small">This model cannot read images. Choose text sources or use a vision-capable model.</p>}
      {open && (
        <div className="picker-list">
          {project && <p className="muted small picker-project-hint">Files from {project.name} are listed first. All workspace files remain available.</p>}
          {error && <p className="error-note">{error}</p>}
          {truncated && <p className="warning-note" role="status">This folder has more files than the picker can list. Open a smaller folder to see the rest.</p>}
          {!files && !error && <p className="muted">Reading the workspace…</p>}
          {files && !available.length && (
            <p className="muted">No matching files in this workspace. Drop files here to add them{allowImages ? ', or paste an image' : ''}.</p>
          )}
          {available.map((file) => (
            <label key={file.path} className="picker-item">
              <input type="checkbox" checked={selected.includes(file.path)} disabled={!selected.includes(file.path) && isImagePath(file.path) && !app.status?.image_support} onChange={() => toggle(file.path)} />
              <span className="picker-path">{file.path}</span>
              <span className="muted">{bytes(file.size)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

let openModalCount = 0
let backgroundWasInert = false

export type ModalTone = 'ink' | 'danger' | 'caution'

/** A dialog is a small notebook page: its mark in the margin, the red rule, and actions on the bottom edge. */
export function Modal({ title, onClose, children, wide, icon, tone = 'ink' }: {
  title: ReactNode; onClose?: () => void; children: ReactNode; wide?: boolean; icon?: ReactNode; tone?: ModalTone
}) {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const root = document.getElementById('root')
    if (openModalCount++ === 0) backgroundWasInert = !!root?.inert
    if (root) root.inert = true
    const focusable = () => [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
    ;(focusable().find((element) => !element.classList.contains('modal-close')) || focusable()[0] || dialog).focus()
    const key = (event: KeyboardEvent) => {
      if ([...document.querySelectorAll('[role="dialog"]')].at(-1) !== dialog) return
      if (event.key === 'Escape' && closeRef.current) { event.preventDefault(); closeRef.current() }
      if (event.key !== 'Tab') return
      const controls = focusable()
      const first = controls[0], last = controls[controls.length - 1]
      if (!first) { event.preventDefault(); dialog.focus() }
      else if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('keydown', key)
      openModalCount--
      if (root && openModalCount === 0) root.inert = backgroundWasInert
      if (opener?.isConnected) opener.focus()
    }
  }, [])
  // A portal: a dialog opened from the mobile drawer must not inherit its transform.
  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose?.()}>
      <div ref={dialogRef} tabIndex={-1} className={'modal paper' + (wide ? ' modal-wide' : '')} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="modal-head">
          <span className={'modal-mark modal-mark-' + tone} aria-hidden="true">{icon || <span className="modal-mark-square" />}</span>
          <h2 id={titleId}>{title}</h2>
          {onClose && (
            <button type="button" className="icon-btn modal-close" aria-label="Close" onClick={onClose}><CloseIcon /></button>
          )}
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return children ? <p className="error-note" role="alert">{children}</p> : null
}

/** Copy text to the clipboard. The Clipboard API needs a secure page (localhost); a phone on
    the local network reaches the interface over plain http, so fall back to a hidden selection. */
export async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return }
  } catch { /* fall back below */ }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  const ok = document.execCommand('copy')
  area.remove()
  if (!ok) throw new Error('The browser refused to copy. Select the text and copy it by hand.')
}

/** A small button that copies its text and says so for a moment. */
export function CopyButton({ text, label, title, className = '' }: { text: string | (() => string); label: string; title?: string; className?: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), 1600)
    return () => window.clearTimeout(timer)
  }, [state])
  return (
    <button type="button" className={'btn btn-quiet btn-small copy-btn ' + className} title={state === 'failed' ? 'The browser refused to copy' : title || label}
      aria-label={title || label}
      onClick={() => { copyText(typeof text === 'function' ? text() : text).then(() => setState('copied'), () => setState('failed')) }}>
      {state === 'copied' ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
      <span>{state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : label}</span>
      <span className="agent-sr-only" role="status">{state === 'copied' ? 'Copied to the clipboard' : ''}</span>
    </button>
  )
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
  return <p className="loading muted">{children}</p>
}

export function SourceLines({ content, highlight, start = 1 }: { content: string; highlight?: [number, number][]; start?: number }) {
  const lines = content.split('\n')
  const read = (n: number) => highlight?.some(([a, b]) => n >= a && n <= b)
  return (
    <pre className="source">
      {lines.map((line, index) => {
        const number = index + start
        return (
          <div key={number} className={'source-line' + (highlight ? (read(number) ? ' source-read' : ' source-unread') : '')}>
            <span className="source-number">{number}</span>
            <span className="source-text">{line || ' '}</span>
          </div>
        )
      })}
    </pre>
  )
}
