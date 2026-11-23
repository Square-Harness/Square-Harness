// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Application state fed by one Server-Sent Events connection.
import { useSyncExternalStore } from 'react'
import {
  api, ApiError, onUnauthorized,
  type ChatListItem, type Concurrency, type ProofListItem, type ResearchListItem,
  type Status, type StreamChunk, type TaskSnapshot, type TaskSummary, type Approval, type Activity, type TranscriptItem, type Project,
} from './api'
import { reduceActivity, reduceChat, reduceTask, taskById } from './concurrency'
import { chatDeleteBlocked, deletedChatDestination, withoutDeletedChat } from './chatDeletion'

export type Connection = 'connecting' | 'open' | 'reconnecting'

export type AppState = {
  auth: 'checking' | 'ok' | 'needed'
  status: Status | null
  connection: Connection
  snapshot: TaskSnapshot
  proofs: ProofListItem[] | null
  research: ResearchListItem[] | null
  chats: ChatListItem[] | null
  projects: Project[] | null
  projectFilter: string
  listError?: string
  ticks: Record<string, number>
  /** Bumped when the connection comes back: every open view reloads, including one that never loaded. */
  resync: number
  /** The folder another tab opened, shown once after this tab followed it. */
  folderNotice?: string
}

type ServerEvent =
  | { type: 'hello'; seq: number; instance: string; build?: string; resync: boolean }
  | { type: 'task'; seq: number; task: TaskSummary }
  | ({ type: 'activity'; seq: number; task: string; job: string; id: string | null } & Activity)
  | { type: 'approval'; seq: number; state: 'pending' | 'approved' | 'denied'; approval: Approval | { id: string; task: string } }
  | { type: 'chat'; seq: number; chat: string; task: string; kind: 'start' | 'delta' | 'end' | 'tool' | 'result' | 'notice' | 'experiment' | 'progress'; thinking?: string; text?: string; item?: TranscriptItem }
  | { type: 'chat_saved'; seq: number; chat: string }
  | { type: 'chat_deleted'; seq: number; chat: string }
  | { type: 'job'; seq: number; job: 'proof' | 'research'; id: string; status: string; phase: string | null }
  | ({ type: 'stream'; seq: number; job: 'proof' | 'research'; id: string; role: string } & StreamChunk)
  | { type: 'queue'; seq: number; queue: TaskSummary[] }
  | { type: 'concurrency'; seq: number; concurrency: Concurrency }
  | { type: 'routing'; seq: number; chat: string; state: 'running' | 'done' }
  | { type: 'workspace'; seq: number; path: string }
  | { type: 'files'; seq: number; path: string }
  | { type: 'projects'; seq: number }
  | { type: 'personalization'; seq: number }

const FOLDER_NOTICE = 'square-folder-notice'
const OWN_SWITCH = 'square-own-folder-switch'
const EMPTY: TaskSnapshot = { seq: 0, task: null, activity: [], live: null, approvals: [], queue: [], routing: [], tasks: [] }

let state: AppState = {
  auth: 'checking', status: null, connection: 'connecting', snapshot: EMPTY,
  proofs: null, research: null, chats: null, projects: null, projectFilter: 'all', ticks: {}, resync: 0,
  folderNotice: takeFolderNotice(),
}
const listeners = new Set<() => void>()

function set(patch: Partial<AppState> | ((s: AppState) => Partial<AppState>)) {
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useApp(): AppState {
  return useSyncExternalStore(subscribe, () => state)
}

export function getState() {
  return state
}

/** Sidebar grouping only; never switches the server's workspace or running jobs. */
export function setProjectFilter(projectFilter: string) {
  set({ projectFilter })
}

/** Changes whenever the server reports a change to this job or chat, or the connection returns. */
export function useTick(id: string | null | undefined): number {
  return useSyncExternalStore(subscribe, () => (id ? state.ticks[id] || 0 : 0) + state.resync * 1_000_000)
}

function bump(id: string | null | undefined) {
  if (!id) return
  set((s) => ({ ticks: { ...s.ticks, [id]: (s.ticks[id] || 0) + 1 } }))
}

// -- live model output ------------------------------------------------------

type StreamListener = (chunk: StreamChunk & { role: string }) => void
const streamListeners = new Map<string, Set<StreamListener>>()

export function onStream(job: string, id: string, file: string, listener: StreamListener) {
  const key = `${job}:${id}:${file}`
  if (!streamListeners.has(key)) streamListeners.set(key, new Set())
  streamListeners.get(key)!.add(listener)
  return () => {
    streamListeners.get(key)?.delete(listener)
  }
}

// -- lists ---------------------------------------------------------------------

let listTimer: number | undefined
let listGeneration = 0
let loadingLists = false
let pendingLists: 'all' | 'chats' | null = null
const pendingChatTicks = new Set<string>()
const deletedChats = new Set<string>()
function resetChatDeletionCache() {
  deletedChats.clear()
  ++listGeneration
}
export function refreshLists(delay = 0, scope: 'all' | 'chats' = 'all') {
  if (pendingLists !== 'all') pendingLists = scope
  if (listTimer !== undefined || loadingLists) return
  listTimer = window.setTimeout(async () => {
    listTimer = undefined
    loadingLists = true
    const all = pendingLists === 'all'
    pendingLists = null
    const generation = listGeneration
    try {
      const results = await Promise.allSettled([all ? api.proofs() : null, all ? api.researches() : null,
        api.chats(), all ? api.projects() : null] as const)
      if (generation !== listGeneration) return
      const [proofs, research, chats, projects] = results
      const errors = results.flatMap(result => result.status === 'rejected' ? [String(result.reason?.message || 'Could not load saved work.')] : [])
      set({
        ...(proofs.status === 'fulfilled' && proofs.value ? { proofs: proofs.value.jobs } : {}),
        ...(research.status === 'fulfilled' && research.value ? { research: research.value.jobs } : {}),
        ...(chats.status === 'fulfilled' ? { chats: chats.value.chats.filter(chat => !deletedChats.has(chat.id)) } : {}),
        ...(projects.status === 'fulfilled' && projects.value ? { projects: projects.value.projects } : {}),
        listError: errors.join(' '),
      })
    } finally {
      loadingLists = false
      if (pendingLists) refreshLists(200, pendingLists)
    }
  }, delay)
}

function forgetChat(id: string, seq?: number) {
  deletedChats.add(id)
  // The open composer keeps its unsent message before the view leaves the deleted conversation.
  window.dispatchEvent(new CustomEvent('square-chat-deleted', { detail: id }))
  set((s) => {
    const ticks = { ...s.ticks }
    delete ticks[id]
    return {
      chats: s.chats?.filter((chat) => chat.id !== id) ?? null,
      ticks,
      snapshot: { ...withoutDeletedChat(s.snapshot, id), seq: Math.max(s.snapshot.seq, seq || 0) },
    }
  })
  const destination = deletedChatDestination(window.location.hash, id)
  if (destination) window.location.hash = destination
  refreshLists()
}

export async function deleteConversation(id: string) {
  if (chatDeleteBlocked(state.snapshot, id)) {
    throw new Error('Pause this conversation or cancel its queued work before deleting it.')
  }
  const result = await api.deleteChat(id)
  forgetChat(result.deleted)
}

// Folder changes invalidate every loaded chat, job, pending HTTP request and live stream.
// A tab that did not open the folder itself says so after reloading.
function takeFolderNotice(): string | undefined {
  try {
    const notice = sessionStorage.getItem(FOLDER_NOTICE) || undefined
    sessionStorage.removeItem(FOLDER_NOTICE)
    return notice
  } catch { return undefined }
}
/** Called by the tab that opens another folder, so that only the other tabs show a notice. */
export function markOwnFolderSwitch() {
  try { sessionStorage.setItem(OWN_SWITCH, '1') } catch { /* private mode */ }
}
export function dismissFolderNotice() { set({ folderNotice: undefined }) }
let leaving = false
function leaveWorkspace(path?: string) {
  // The folder event and a status check can both arrive before the reload: act once.
  if (leaving) return
  leaving = true
  try {
    if (sessionStorage.getItem(OWN_SWITCH)) sessionStorage.removeItem(OWN_SWITCH)
    else sessionStorage.setItem(FOLDER_NOTICE, path || 'another folder')
  } catch { /* private mode: reload without a notice */ }
  const notebook = window.location.hash.split('?')[0].split('/').slice(0, 2).join('/') || '#/free'
  window.history.replaceState(null, '', notebook)
  window.location.reload()
}

let statusGeneration = 0
export async function refreshStatus() {
  const generation = ++statusGeneration
  try {
    const status = await api.status()
    if (generation !== statusGeneration) return
    if (state.status && state.status.workspace !== status.workspace) {
      leaveWorkspace(status.workspace)
      return
    }
    set({ status })
  } catch {
    /* connection state already tells the user */
  }
}

// -- task snapshot and events ------------------------------------------------

let buffered: ServerEvent[] | null = null

async function refreshSnapshot() {
  if (buffered) return
  buffered = []
  try {
    const snapshot = await api.task()
    const pending = buffered || []
    buffered = null
    set({ snapshot: [...deletedChats].reduce(withoutDeletedChat, snapshot) })
    pending.filter((event) => event.seq > snapshot.seq).forEach(handle)
  } catch {
    const pending = buffered || []
    buffered = null
    pending.forEach(handle)
    if (state.auth === 'ok') window.setTimeout(refreshSnapshot, 1000)
  }
}

let instance = ''
// The bundle this page runs (assets/index-<hash>.js); empty in development.
const ownBuild = typeof document === 'undefined' ? ''
  : (document.querySelector('script[src*="/assets/index-"]') as HTMLScriptElement | null)?.src.split('/').pop() || ''

function handle(event: ServerEvent) {
  // A task event (such as a new job's ID) may be newer than the snapshot being fetched.
  if (buffered && ['chat', 'chat_deleted', 'activity', 'approval', 'task', 'queue', 'concurrency', 'routing'].includes(event.type)) {
    buffered.push(event)
    return
  }
  switch (event.type) {
    case 'hello':
      // A server started with another interface bundle: load it, or new features stay invisible.
      if (event.build && ownBuild && event.build !== ownBuild) { window.location.reload(); return }
      if (event.resync || (instance && instance !== event.instance)) {
        if (instance && instance !== event.instance) resetChatDeletionCache()
        refreshAll()
      }
      instance = event.instance
      break
    case 'task': {
      const fresh = !taskById(state.snapshot, event.task.id)
      set((s) => ({ snapshot: reduceTask(s.snapshot, event.task, event.seq) }))
      if (fresh && ['starting', 'running'].includes(event.task.state)) refreshSnapshot()
      if (['done', 'paused', 'error'].includes(event.task.state)) {
        refreshLists(150)
        bump(event.task.target)
        if (event.task.state === 'error') refreshStatus()
      } else if (event.task.target) {
        bump(event.task.target)
        refreshLists(300)
      }
      break
    }
    case 'activity':
      set((s) => ({ snapshot: reduceActivity(s.snapshot, event.task, {
        kind: event.kind, text: event.text, time: event.time, child_id: event.child_id, card_index: event.card_index,
        turn_id: event.turn_id, batch_id: event.batch_id, job: event.job, id: event.id,
      }, event.seq) }))
      break
    case 'approval':
      set((s) => ({
        snapshot: {
          ...s.snapshot,
          approvals: event.state === 'pending'
            ? [...s.snapshot.approvals.filter((a) => a.id !== event.approval.id), event.approval as Approval]
            : s.snapshot.approvals.filter((a) => a.id !== event.approval.id),
        },
      }))
      break
    case 'chat':
      set((s) => ({ snapshot: reduceChat(s.snapshot, event) }))
      break
    case 'chat_saved':
      refreshLists(450, 'chats')
      if (!pendingChatTicks.has(event.chat)) {
        pendingChatTicks.add(event.chat)
        window.setTimeout(() => {
          pendingChatTicks.delete(event.chat)
          if (!deletedChats.has(event.chat)) bump(event.chat)
        }, 250)
      }
      break
    case 'personalization':
      refreshLists(100)
      bump('personalization')
      break
    case 'chat_deleted':
      forgetChat(event.chat, event.seq)
      break
    case 'job':
      refreshLists(400)
      bump(event.id)
      break
    case 'stream':
      streamListeners.get(`${event.job}:${event.id}:${event.file}`)?.forEach((listener) => listener(event))
      break
    case 'concurrency':
      set((s) => ({ snapshot: { ...s.snapshot, seq: Math.max(s.snapshot.seq, event.seq), concurrency: event.concurrency }, status: s.status ? { ...s.status, concurrency: event.concurrency } : null }))
      break
    case 'queue':
      set((s) => ({ snapshot: { ...s.snapshot, seq: Math.max(s.snapshot.seq, event.seq), queue: event.queue } }))
      break
    case 'routing':
      set((s) => ({
        snapshot: {
          ...s.snapshot,
          routing: event.state === 'running' ? [...new Set([...s.snapshot.routing, event.chat])] : s.snapshot.routing.filter((c) => c !== event.chat),
        },
      }))
      bump(event.chat)
      break
    case 'workspace':
      leaveWorkspace(event.path)
      break
    case 'files':
      bump('files')
      break
    case 'projects':
      refreshLists(50)
      bump('projects')
      break
  }
}

// -- connection ------------------------------------------------------------------

let source: EventSource | null = null
let retry: number | undefined

function connect() {
  source?.close()
  window.clearTimeout(retry)
  source = new EventSource('/api/events')
  source.onopen = () => {
    // A view whose request failed while the harness was away loads again.
    const returning = state.connection === 'reconnecting'
    set({ connection: 'open' })
    if (returning) refreshAll()
  }
  source.onmessage = (message) => {
    try {
      handle(JSON.parse(message.data))
    } catch (error) {
      console.warn('Ignored malformed event', error)
    }
  }
  source.onerror = () => {
    set({ connection: 'reconnecting' })
    if (source && source.readyState === EventSource.CLOSED) {
      // The browser gave up (server stopped, or the token was refused).
      retry = window.setTimeout(async () => {
        try {
          await api.status()
          connect()
          refreshAll()
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) return
          retry = window.setTimeout(() => connect(), 3000)
        }
      }, 2000)
    }
  }
}

export function refreshAll() {
  refreshStatus()
  refreshLists()
  refreshSnapshot()
  set((s) => ({ resync: s.resync + 1 }))
}

let unauthorizedListener: (() => void) | null = null
export async function start() {
  unauthorizedListener?.()
  unauthorizedListener = onUnauthorized(() => {
    source?.close()
    set({ auth: 'needed' })
  })
  try {
    const status = await api.status()
    if (state.status && state.status.workspace !== status.workspace) {
      leaveWorkspace(status.workspace)
      return
    }
    set({ status, auth: 'ok' })
    connect()
    refreshLists()
    refreshSnapshot()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) set({ auth: 'needed' })
    else {
      set({ auth: 'ok', connection: 'reconnecting' })
      retry = window.setTimeout(start, 3000)
    }
  }
}

export async function login(token: string) {
  await api.login(token)
  await start()
}

// -- theme ---------------------------------------------------------------------------

export type Theme = 'system' | 'light' | 'dark'

export function readTheme(): Theme {
  try {
    const value = localStorage.getItem('square-theme')
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function applyTheme(theme: Theme) {
  if (theme === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem('square-theme', theme)
  } catch {
    /* private mode: the choice lasts for this page */
  }
}
