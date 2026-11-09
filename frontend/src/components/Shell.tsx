// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Account, type ChatListItem, type TaskSummary, type TesterAllowance } from '../api'
import { activeTasks, chatWork, concurrencyLimits } from '../concurrency'
import { chatDeleteBlocked } from '../chatDeletion'
import { ago, count, duration } from '../format'
import { MODES } from '../modes'
import { setPanelHidden } from '../panels'
import { href, notebook, type Mode, type Route } from '../router'
import { applyTheme, deleteConversation, readTheme, refreshLists, refreshStatus, useApp, type AppState, type Theme } from '../store'
import { ProjectList } from './Projects'
import { FeedbackButton } from './Feedback'
import { ErrorNote, Meter, Modal, NumberField } from './common'
import { FileAccess, FolderBrowser } from './FolderBrowser'
import { Inline, MathMacrosProvider } from './Markdown'
import { Glyph } from './Glyph'
import { AutoIcon, ChevronIcon, CloseIcon, FileIcon, MenuIcon, MoonIcon, PanelLeftIcon, PauseIcon, PhoneIcon, PlayIcon, PlusIcon, SunIcon, TrashIcon } from './Icons'
import { proofLook, researchLook, reviewStatusLook, Square } from './Square'
import { VARIANT_NAMES, VERDICTS } from '../review'

export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="brand-mark">
      <rect width="64" height="64" rx="12" className="brand-cover" />
      <path d="M0 16h64M0 32h64M0 48h64M16 0v64M32 0v64M48 0v64" className="brand-grid" />
      <path d="M13 0v64" className="brand-margin" />
      <rect x="33" y="33" width="19" height="19" rx="1.5" className="brand-square" />
    </svg>
  )
}

/** Resolve a task destination for active and queued work. */
export function taskMode(task: TaskSummary | null, app: AppState): Mode | null {
  if (!task) return null
  if (task.kind === 'proof') return 'prove'
  if (task.kind === 'research') {
    const job = app.research?.find((item) => item.id === task.target)
    return job ? job.kind : task.label.startsWith('Review') ? 'referee' : task.label.startsWith('Writing style') ? 'style' : task.label.startsWith('Write') ? 'writeup' : 'literature'
  }
  const chat = app.chats?.find((item) => item.id === task.target)
  return chat ? chat.kind : null
}

export function taskHref(task: TaskSummary, app: AppState) {
  const mode = taskMode(task, app) || (task.kind === 'proof' ? 'prove' : 'free')
  return href(mode, task.target)
}

function ThemeButton() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  const next: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
  const label = { system: 'Theme follows the system', light: 'Light theme', dark: 'Dark theme' }[theme]
  return (
    <button type="button" className="icon-btn" title={label + ' (click to change)'} aria-label={label}
      onClick={() => { const value = next[theme]; setTheme(value); applyTheme(value) }}>
      {theme === 'light' ? <SunIcon /> : theme === 'dark' ? <MoonIcon /> : <AutoIcon />}
    </button>
  )
}

function ReviewTally({ reviews }: { reviews: Partial<Record<string, number>> }) {
  const order = ['no_issue_found', 'issues_found', 'uncertain', 'review_unavailable']
  const shown = order.filter((status) => reviews[status])
  if (!shown.length) return null
  return (
    <span className="tally">
      {shown.map((status) => (
        <span key={status} className="tally-item" title={`${reviews[status]} ${reviewStatusLook(status).label.toLowerCase()}`}>
          <Square variant={reviewStatusLook(status).variant} size={11} />{reviews[status]}
        </span>
      ))}
    </span>
  )
}

function HistoryMark({ mode }: { mode: Mode }) {
  if (mode === 'free') return null
  return <span className={`history-mark mode-${mode}`} aria-hidden="true"><Glyph mode={mode} /></span>
}

export function Sidebar({ route }: { route: Route }) {
  const app = useApp()
  const info = MODES[notebook(route.mode)]
  return (
    <aside className="sidebar" aria-label={info.historyLabel}>
      <div className="sidebar-inner">
        <header className="side-head">
          <div className="side-title-row">
            <h1 className="side-title"><HistoryMark mode={info.mode} /><span>{info.historyLabel}</span></h1>
            <button type="button" className="icon-btn side-hide" title="Hide this list" aria-label="Hide this list"
              onClick={() => setPanelHidden('sidebar', true)}>
              <PanelLeftIcon />
            </button>
          </div>
          <a className="btn btn-primary btn-block" href={href(info.mode)}><PlusIcon size={16} /> {info.newLabel}</a>
          <a className="btn btn-quiet btn-block" href={href('style')} aria-current={route.mode === 'style' && !route.id ? 'page' : undefined}>
            <span className="history-mark mode-style" aria-hidden="true"><Glyph mode="style" /></span> {route.mode === 'style' ? MODES.style.newLabel : 'Learn my writing style'}
          </a>
        </header>
        <div className="side-list">
          <ErrorNote>{app.listError}</ErrorNote>
          {info.kind === 'free' && <ProjectList />}
          <SideList route={route} app={app} />
        </div>
        <TaskPanel />
        <WorkspacePanel route={route} />
      </div>
    </aside>
  )
}

/** What is left of the list once it slides away: the notebook's spine, to pull it back. */
export function SideSpine({ route }: { route: Route }) {
  const app = useApp()
  const info = MODES[notebook(route.mode)]
  const running = activeTasks(app.snapshot).length > 0
  return (
    <button type="button" className="spine side-spine" onClick={() => setPanelHidden('sidebar', false)}
      title={`Show the ${info.historyLabel} list`} aria-label={`Show the ${info.historyLabel} list`}>
      <ChevronIcon size={16} />
      <HistoryMark mode={info.mode} />
      <span className="spine-text">{info.historyLabel}</span>
      {running && <Square variant="running" size={12} label="The model is working" />}
    </button>
  )
}

function SideList({ route, app }: { route: Route; app: AppState }) {
  const info = MODES[notebook(route.mode)]
  if (info.kind === 'proof') {
    if (!app.proofs) return <p className="side-empty muted">Loading…</p>
    if (!app.proofs.length) return <p className="side-empty muted">{info.empty}</p>
    return (
      <>
        {app.proofs.map((job) => {
          const look = proofLook(job.status, job.running, job.version)
          return (
            <a key={job.id} href={href('prove', job.id)} className={'side-item' + (route.id === job.id ? ' side-item-active' : '')}>
              <Square variant={look.variant} label={look.label} />
              <span className="side-body">
                <span className="side-name"><Inline limit={110}>{job.title}</Inline></span>
                <span className="side-meta">
                  {job.version === 2
                    ? <span>Attempt {job.rounds.used} of {job.rounds.limit}</span>
                    : <span title="Made by the v0.4 engine; readable, not resumable">v0.4 job</span>}
                  <ReviewTally reviews={job.reviews} />
                  <span className="side-time">{ago(job.updated_at)}</span>
                </span>
              </span>
            </a>
          )
        })}
      </>
    )
  }
  if (info.kind === 'chat' || info.kind === 'free') {
    return <ChatSideList route={route} app={app} />
  }
  if (!app.research) return <p className="side-empty muted">Loading…</p>
  const jobs = app.research.filter((job) => job.kind === info.mode)
  if (!jobs.length) return <p className="side-empty muted">{info.empty}</p>
  return (
    <>
      {jobs.map((job) => {
        const look = researchLook(job.status, job.running)
        return (
          <a key={job.id} href={href(info.mode, job.id)} className={'side-item' + (route.id === job.id ? ' side-item-active' : '')}>
            <Square variant={look.variant} label={look.label} />
            <span className="side-body">
              <span className="side-name"><Inline limit={110}>{job.title}</Inline></span>
              <span className="side-meta">
                {job.variant && <span className="side-variant">{VARIANT_NAMES[job.variant]}</span>}
                <span>{job.verdict ? VERDICTS[job.verdict]?.label || look.label : look.label}</span>
                <span className="side-time">{ago(job.updated_at)}</span>
              </span>
            </span>
          </a>
        )
      })}
    </>
  )
}

function ChatSideList({ route, app }: { route: Route; app: AppState }) {
  const [selected, setSelected] = useState<ChatListItem | null>(null)
  const [actions, setActions] = useState<ChatListItem | null>(null)
  const opener = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    if (selected && app.chats && !app.chats.some((chat) => chat.id === selected.id)) setSelected(null)
  }, [app.chats, selected])
  // Every conversation lives here, including critiques and explorations from earlier versions.
  if (!app.chats) return <p className="side-empty muted">Loading…</p>
  const chats = app.chats.filter((chat) => app.projectFilter === 'all' || chat.project_id === app.projectFilter)
  return (
    <>
      {!chats.length && <p className="side-empty muted">{app.projectFilter === 'all' ? MODES.free.empty : 'No conversations here yet.'}</p>}
      {chats.map((chat) => {
        const work = chatWork(app.snapshot, chat.id)
        return (
          <MathMacrosProvider key={chat.id} definitions={chat.math_macros}>
          <div className={'side-chat-row' + (route.id === chat.id ? ' side-item-active' : '')}>
            <a href={href(chat.kind, chat.id)} className="side-item side-item-chat" aria-current={route.id === chat.id ? 'page' : undefined}>
              {work.active && <Square variant="running" size={12} label="Active conversation" />}
              {work.queued && <Square variant="ready" size={12} label="Queued conversation" />}
              <span className="side-body">
                <span className="side-name"><Inline limit={110}>{chat.title}</Inline></span>
                <span className="side-meta">
                  {chat.kind !== 'free' && <span className="side-kind" title="A conversation from an earlier version">{MODES[chat.kind].label}</span>}
                  {work.queued && <span>Queued</span>}
                  <span className="side-preview">{chat.preview ? <Inline limit={80}>{chat.preview}</Inline> : 'No answer yet'}</span>
                  <span className="side-time">{ago(chat.updated_at)}</span>
                </span>
              </span>
            </a>
            <button type="button" className="icon-btn side-chat-actions" aria-label={`Conversation options: ${chat.title}`} title="Rename, move or delete conversation"
              onClick={(event) => { opener.current = event.currentTarget; setActions(chat) }}>
              <span aria-hidden="true">···</span>
            </button>
          </div>
          </MathMacrosProvider>
        )
      })}
      {actions && <ChatActionsDialog key={actions.id} chat={actions} app={app} onClose={() => setActions(null)} onDelete={() => { setSelected(actions); setActions(null) }} />}
      {selected && <DeleteChatDialog key={selected.id} chat={selected} app={app} opener={opener.current} onClose={() => setSelected(null)} />}
    </>
  )
}

function ChatActionsDialog({ chat, app, onClose, onDelete }: { chat: ChatListItem; app: AppState; onClose: () => void; onDelete: () => void }) {
  const [title, setTitle] = useState(chat.title)
  const [project, setProject] = useState(chat.project_id || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const blocked = chatDeleteBlocked(app.snapshot, chat.id)
  async function save() {
    if (!title.trim() || saving) return
    setSaving(true); setError('')
    try {
      if (title.trim() !== chat.title) await api.chatTitle(chat.id, title.trim())
      if (project !== (chat.project_id || '')) await api.chatProject(chat.id, project || null)
      refreshLists(); onClose()
    } catch (reason) { setError((reason as Error).message); setSaving(false); refreshLists() }
  }
  return <Modal title="Conversation options" onClose={saving ? undefined : onClose}>
    <form onSubmit={(event) => { event.preventDefault(); void save() }}>
      <label className="field"><span className="field-label">Conversation title</span><input className="text-input" maxLength={120} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="field"><span className="field-label">Project</span><select value={project} disabled={blocked || saving} onChange={(event) => setProject(event.target.value)}>
        <option value="">Without a project</option>{app.projects?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <p className="muted small">{blocked ? 'Wait for current work to finish before moving this conversation. You can still rename it.' : 'Moving changes the project notes used by new turns. Resuming paused work keeps its saved context.'}</p>
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions"><button type="button" className="btn" disabled={saving} onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary" disabled={saving || !title.trim()}>{saving ? 'Saving…' : 'Save changes'}</button></div>
      <div className="chat-delete-option"><button type="button" className="btn btn-quiet btn-delete" disabled={blocked || saving} onClick={onDelete}><TrashIcon size={15} /> Delete conversation…</button>
        {blocked && <p className="muted small">Pause this conversation or cancel its queued work before deleting it.</p>}</div>
    </form>
  </Modal>
}

function DeleteChatDialog({ chat, app, opener, onClose }: { chat: ChatListItem; app: AppState; opener: HTMLButtonElement | null; onClose: () => void }) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const body = useRef<HTMLDivElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)
  const blocked = chatDeleteBlocked(app.snapshot, chat.id)
  useEffect(() => {
    if (deleting) body.current?.closest<HTMLElement>('[role="dialog"]')?.focus()
  }, [deleting])
  useEffect(() => {
    const dialog = body.current?.closest<HTMLElement>('[role="dialog"]')
    if (!dialog) return
    dialog.tabIndex = -1
    cancel.current?.focus()
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const buttons = [...dialog.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
      const first = buttons[0]
      const last = buttons[buttons.length - 1]
      if (!first) { event.preventDefault(); dialog.focus() }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
        event.preventDefault(); first.focus()
      }
    }
    dialog.addEventListener('keydown', trap)
    return () => { dialog.removeEventListener('keydown', trap); if (opener?.isConnected) opener.focus() }
  }, [opener])
  async function remove() {
    if (blocked || deleting) return
    setDeleting(true)
    setError('')
    try { await deleteConversation(chat.id); onClose() }
    catch (reason) { setError((reason as Error).message); setDeleting(false) }
  }
  return (
    <Modal title="Delete conversation?" onClose={deleting ? undefined : onClose}>
      <div ref={body}>
        <p className="delete-chat-title"><strong><Inline>{chat.title}</Inline></strong></p>
        <p>The conversation and its messages will be permanently deleted. Saved proofs, reports and files will remain.</p>
        {blocked && <p className="muted">Pause this conversation or cancel its queued work before deleting it.</p>}
        <ErrorNote>{error}</ErrorNote>
        <div className="modal-actions">
          <button ref={cancel} type="button" className="btn" disabled={deleting} onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-delete" disabled={blocked || deleting} onClick={remove}>
            <TrashIcon size={16} /> {deleting ? 'Deleting…' : 'Delete conversation'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return <>{duration(Math.max(0, now / 1000 - since))}</>
}

export function TaskPanel() {
  const app = useApp()
  const tasks = activeTasks(app.snapshot)
  return (
    <>
      {tasks.map(({ task }) => <ActiveTaskPanel key={task.id} task={task} app={app} />)}
      <QueuePanel />
    </>
  )
}

function ActiveTaskPanel({ task, app }: { task: TaskSummary; app: AppState }) {
  const [error, setError] = useState('')
  const pausing = task.state === 'pausing'
  return (
    <div className="task-panel" role="status">
      <Square variant="running" size={16} label="Running" />
      <a className="task-text" href={taskHref(task, app)}>
        <span className="task-label">{task.label}</span>
        <span className="task-title"><Inline limit={70}>{task.title}</Inline></span>
        <span className="task-state">{pausing ? 'Pausing at the next checkpoint…' : <>Active for <Elapsed since={task.started} /></>}</span>
        {error && <span className="task-error">{error}</span>}
      </a>
      <button type="button" className="btn btn-small" disabled={pausing}
        aria-label={`Pause ${task.label}: ${task.title}`}
        onClick={() => api.pause(task.id).catch((reason: Error) => setError(reason.message))}>
        {pausing ? <PlayIcon size={15} /> : <PauseIcon size={15} />} {pausing ? 'Pausing…' : 'Pause'}
      </button>
    </div>
  )
}

function QueuePanel() {
  const app = useApp()
  const [error, setError] = useState('')
  if (!app.snapshot.queue.length) return null
  return (
    <div className="queue-panel">
      <p className="detail-label">Waiting for a chat slot</p>
      {app.snapshot.queue.map((item) => (
        <div key={item.id} className="queue-item">
          <a className="queue-text" href={taskHref(item, app)}><strong>{item.label}</strong> <Inline limit={50}>{item.title}</Inline></a>
          <button type="button" className="icon-btn" aria-label={`Cancel queued ${item.label}: ${item.title}`} title="Cancel"
            onClick={() => api.cancelQueued(item.id).catch((reason: Error) => setError(reason.message))}>
            <CloseIcon size={14} />
          </button>
        </div>
      ))}
      {error && <p className="task-error">{error}</p>}
    </div>
  )
}

/** The folder the harness works in, shown above the text window; click to open another. */
export function WorkspaceSwitch() {
  const { status } = useApp()
  const [browsing, setBrowsing] = useState(false)
  if (!status) return null
  const folder = status.workspace.split('/').filter(Boolean).pop() || status.workspace
  return (
    <div className="workspace-switch">
      <button type="button" className="workspace-open" title={status.workspace + ' (open another folder)'} onClick={() => setBrowsing(true)}>
        <span className="folder-glyph" aria-hidden="true" />{folder}
      </button>
      {!status.online && <span className="pill" title="No external requests; local and cached sources only">Offline</span>}
      {browsing && <FolderBrowser onClose={() => setBrowsing(false)} />}
    </div>
  )
}

function PilotAllowance({ allowance, detailed = false }: { allowance: TesterAllowance; detailed?: boolean }) {
  const outputUnit = 1000 * Number(allowance.output_per_million)
  if (!Number.isFinite(outputUnit) || outputUnit <= 0) return null
  const inputWeight = Number(allowance.input_per_million) / Number(allowance.output_per_million)
  const inputShare = Math.abs(inputWeight - 1 / 3) < 1e-9 ? 'one third' : inputWeight.toLocaleString(undefined, { maximumFractionDigits: 3 })
  const hint = `Across all conversations. Each input token counts as ${inputShare} of an output token; output includes thinking. This conservative pilot allowance uses Standard list prices; actual billing may be lower. The meter includes reservations.`
  const money = (nanos: number) => '$' + (nanos / 1e9).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  return (
    <div className={detailed ? 'account-allowance' : undefined} title={hint}>
      {detailed && <div className="account-budget-title"><strong>Pilot allowance (USD)</strong><span>{money(allowance.limit_nanos)}</span></div>}
      <Meter label={detailed ? 'Output-equivalent tokens' : 'Pilot allowance'}
        used={(allowance.charged_nanos + allowance.reserved_nanos) / outputUnit / 1e6}
        limit={allowance.limit_nanos / outputUnit / 1e6} unit="M" decimals={3} title={hint} />
      {detailed && <dl className="account-usage">
        <div><dt>Used</dt><dd>{money(allowance.charged_nanos)}</dd></div>
        <div><dt>Reserved</dt><dd>{money(allowance.reserved_nanos)}</dd></div>
        <div><dt>Remaining</dt><dd>{money(Math.max(0, allowance.limit_nanos - allowance.charged_nanos - allowance.reserved_nanos))}</dd></div>
      </dl>}
      <p className="muted small">Output-equivalent tokens: {count(allowance.charged_nanos / outputUnit)} used.</p>
      {allowance.reserved_nanos > 0 && <p className="muted small">{count(allowance.reserved_nanos / outputUnit)} reserved for unfinished or unreported calls.</p>}
      {detailed && <>
        <p className="muted small">{count(allowance.input_tokens)} input · {count(allowance.output_tokens)} output tokens, including thinking.</p>
        <p className="muted small">Each input token counts as {inputShare} of an output token. Unused reservations are released when usage is known.</p>
      </>}
    </div>
  )
}

function AccountMenu({ account, allowance }: { account: Account; allowance?: TesterAllowance | null }) {
  const menu = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (menu.current && !menu.current.contains(event.target as Node)) menu.current.open = false
    }
    // A restored document must reauthenticate before showing any saved history.
    const hide = () => { document.documentElement.style.visibility = 'hidden' }
    const restore = (event: PageTransitionEvent) => {
      if (event.persisted) { hide(); window.location.reload() }
    }
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('pagehide', hide)
    window.addEventListener('pageshow', restore)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('pagehide', hide)
      window.removeEventListener('pageshow', restore)
      document.documentElement.style.visibility = ''
    }
  }, [])
  return (
    <details className="account-menu" ref={menu} onKeyDown={(event) => {
      if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus() }
    }}>
      <summary className="account-button" title={account.email} aria-label={`Account for ${account.email}`}>
        <span className="account-avatar" aria-hidden="true">{account.email.slice(0, 1).toUpperCase()}</span>
        <span className="account-email">{account.email}</span>
        <span className="account-more" aria-hidden="true">···</span>
      </summary>
      <div className="account-popover">
        <div className="account-heading"><span className="muted small">Signed in</span><strong>{account.email}</strong></div>
        {allowance ? <PilotAllowance allowance={allowance} detailed />
          : <p className="muted small">This account has no tester spending limit.</p>}
        <button type="button" className="btn btn-block" onClick={() => {
          document.documentElement.style.visibility = 'hidden'
          window.location.replace('/cdn-cgi/access/logout')
        }}>Sign out</button>
      </div>
    </details>
  )
}

function WorkspacePanel({ route }: { route: Route }) {
  const app = useApp()
  const { status } = app
  const limits = concurrencyLimits(app.snapshot, status)
  const [parallel, setParallel] = useState(false)
  const [pairing, setPairing] = useState(false)
  const [access, setAccess] = useState(false)
  const [account, setAccount] = useState<Account | null>(null)
  const accountEmail = useRef<string | null>(null)
  useEffect(() => {
    let current = true
    const load = () => { void api.account().then((value) => {
      if (!current) return
      if (accountEmail.current && (!value || accountEmail.current !== value.email)) {
        document.documentElement.style.visibility = 'hidden'
        window.location.replace('/')
        return
      }
      accountEmail.current = value?.email || null
      setAccount(value)
    }).catch((error: unknown) => {
      if (current && accountEmail.current && error instanceof ApiError && [401, 403].includes(error.status)) {
        document.documentElement.style.visibility = 'hidden'
        window.location.replace('/')
      }
    }) }
    const focus = () => { if (accountEmail.current) load() }
    load()
    window.addEventListener('focus', focus)
    return () => { current = false; window.removeEventListener('focus', focus) }
  }, [status?.instance])
  const hasAllowance = Boolean(status?.tester_allowance)
  useEffect(() => {
    if (!hasAllowance) return
    const timer = window.setInterval(() => { void refreshStatus() }, 5000)
    return () => window.clearInterval(timer)
  }, [hasAllowance])
  if (!status) return null
  const allowance = status.tester_allowance
  const readable = (['tex', 'pdf', 'py', 'text'] as const).filter((key) => status.read[key])
  const readLabel = readable.length === 4 ? 'Reads all file types' : readable.length ? 'Reads ' + readable.join(', ') : 'Reads no files by itself'
  const reachable = status.ollama.reachable
  const installed = status.ollama.models.includes(status.model)
  const ollama = status.backend === 'ollama'
  const modelNote = !reachable ? (ollama ? 'Ollama is not reachable' : 'The model server is not reachable')
    : !installed ? (ollama ? 'Model is not installed' : 'Model is not served') : 'Model ready'
  const modelHint = !reachable ? (ollama ? 'Start ollama serve, then reload.' : 'Start the model server, then reload.')
    : ollama ? `Run ollama pull ${status.model}.` : `Serve ${status.model}, or restart with --model.`
  return (
    <footer className="workspace">
      <div className="workspace-row">
        <span className={'model-dot' + (reachable && installed ? ' model-ok' : ' model-bad')} aria-hidden="true" />
        <span className="workspace-model" title={`${status.model} at ${status.host}: ${modelNote}`}>{status.model}</span>
        <ThemeButton />
        {status.lan && (
          <button type="button" className="icon-btn" title="Connect a phone" aria-label="Connect a phone" onClick={() => setPairing(true)}>
            <PhoneIcon size={16} />
          </button>
        )}
      </div>
      <button type="button" className="workspace-access" onClick={() => setAccess(true)}>
        <FileIcon size={13} /> {readLabel}
      </button>
      <button type="button" className="workspace-access" onClick={() => setParallel(true)}>
        Parallel work: {activeTasks(app.snapshot).length} / {limits.chats} active
      </button>
      <FeedbackButton chatId={['free', 'critic', 'explore'].includes(route.mode) ? route.id : null}
        chatTitle={app.chats?.find((chat) => chat.id === route.id)?.title} />
      {account ? <AccountMenu account={account} allowance={allowance} />
        : allowance && <PilotAllowance allowance={allowance} />}
      {parallel && <ConcurrencySettings onClose={() => setParallel(false)} />}
      {access && <FileAccess onClose={() => setAccess(false)} />}
      {(!reachable || !installed) && <p className="workspace-warning">{modelNote}. {modelHint}</p>}
      {pairing && (
        <Modal title="Connect a phone" onClose={() => setPairing(false)}>
          <p>On a phone connected to the same network, open one of these addresses. It contains this workspace's access token, so share it only with your own devices.</p>
          {(status.pair_urls || []).map((url) => <p key={url}><code className="pair-url">{url}</code></p>)}
          <p className="muted">After the first visit the phone stays paired. On iPhone, use Share, then Add to Home Screen; on Android, use the browser menu's Install or Add to Home screen.</p>
        </Modal>
      )}
    </footer>
  )
}

function ConcurrencySettings({ onClose }: { onClose: () => void }) {
  const app = useApp()
  const limits = concurrencyLimits(app.snapshot, app.status)
  const [chats, setChats] = useState(limits.chats)
  const [requests, setRequests] = useState(limits.requests)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const valid = [chats, requests].every((value) => Number.isInteger(value) && value >= 1 && value <= 8)
  const save = async () => {
    if (!valid || saving) return
    setSaving(true)
    setError('')
    try { await api.concurrency({ chats, requests }); onClose() }
    catch (reason) { setError((reason as Error).message); setSaving(false) }
  }
  return (
    <Modal title="Parallel work" onClose={saving ? undefined : onClose}>
      <p>Each conversation or job keeps its full token budget. Work beyond the active limit waits in the queue.</p>
      <NumberField label="Active conversations and jobs" value={chats} onChange={setChats} min={1} max={8}
        hint="One turn at a time in each conversation." />
      <NumberField label="Simultaneous model requests" value={requests} onChange={setRequests} min={1} max={8}
        hint="Includes requests from all active conversations and their workers." />
      <p className="muted small">Active requests share the model's throughput. Requests can progress together, but their speeds depend on the model server and hardware. Lowering a limit lets current work finish before new work starts.</p>
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        <button type="button" className="btn" disabled={saving} onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={saving || !valid} onClick={save}>{saving ? 'Saving…' : 'Save limits'}</button>
      </div>
    </Modal>
  )
}

export function TopBar({ route, onMenu }: { route: Route; onMenu: () => void }) {
  const app = useApp()
  const info = MODES[notebook(route.mode)]
  const running = activeTasks(app.snapshot).length > 0
  return (
    <header className="topbar">
      <button type="button" className="icon-btn" aria-label="Open navigation" onClick={onMenu}><MenuIcon /></button>
      <span className="topbar-title"><HistoryMark mode={info.mode} /><span>{info.historyLabel}</span></span>
      {running && <Square variant="running" size={16} label="The model is working" />}
    </header>
  )
}
