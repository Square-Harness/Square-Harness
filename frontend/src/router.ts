// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useSyncExternalStore } from 'react'
import { rememberWorkPanel } from './panels'

export type Mode = 'free' | 'prove' | 'critic' | 'explore' | 'literature' | 'referee' | 'writeup' | 'style'
export const MODE_ORDER: Mode[] = ['free', 'prove', 'critic', 'explore', 'literature', 'referee', 'writeup', 'style']
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/

export const WORK_TABS = ['agents', 'outputs', 'record', 'plan'] as const
export type WorkTab = typeof WORK_TABS[number]
export type WorkSelection = { tab: WorkTab; selected: string | null }
export type LineRange = { start: number; end: number }
export type ViewerTarget = { kind: 'file' | 'document'; path: string; lines?: LineRange }
export type Route = { mode: Mode | 'about' | 'file'; id: string | null; tab: string | null; from: string | null; panel?: WorkSelection; viewer?: ViewerTarget; result?: string }

const CHAT_MODES = ['free', 'critic', 'explore']

function readViewer(params: URLSearchParams, fileKey: string): ViewerTarget | undefined {
  const document = params.get('document')
  const file = params.get(fileKey)
  const target: ViewerTarget | undefined = document && /^doc-[a-f0-9]{24}$/.test(document)
    ? { kind: 'document', path: document } : file ? { kind: 'file', path: file } : undefined
  if (!target) return
  const match = /^(\d+)(?:-(\d+))?$/.exec(params.get('lines') || '')
  if (match) {
    const start = Number(match[1]), end = Number(match[2] || match[1])
    if (Number.isSafeInteger(start) && start > 0 && Number.isSafeInteger(end) && end >= start) target.lines = { start, end }
  }
  return target
}

function setViewer(params: URLSearchParams, target: ViewerTarget, fileKey: string) {
  params.set(target.kind === 'document' ? 'document' : fileKey, target.path)
  if (target.lines) {
    const { start, end } = target.lines
    params.set('lines', start === end ? String(start) : start + '-' + end)
  }
}

export function parse(hash: string): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const [first, second, third] = path.split('/')
  if (first === 'about') return { mode: 'about', id: null, tab: null, from: null }
  if (first === 'file') {
    const params = new URLSearchParams(search)
    const origin = params.get('from')
    return { mode: 'file', id: null, tab: null, from: origin && UUID.test(origin) ? origin : null, viewer: readViewer(params, 'path') }
  }
  const requestedMode = (MODE_ORDER as string[]).includes(first) ? (first as Mode) : 'free'
  const id = second && UUID.test(second) ? second : null
  // Other notebooks open from conversations, cards and links; the Style page is opened directly.
  const mode = id || ['prove', 'literature', 'referee', 'writeup', 'style'].includes(requestedMode) ? requestedMode : 'free'
  const params = new URLSearchParams(search)
  const origin = params.get('from')
  const from = id && origin && UUID.test(origin) ? origin : null
  const panelTab = WORK_TABS.find((item) => item === params.get('panel'))
  const selected = params.get('selected')
  const panel = id && CHAT_MODES.includes(mode) && panelTab
    ? { tab: panelTab, selected: selected && selected.length <= 400 ? selected : null } : undefined
  const viewer = readViewer(params, 'file')
  const result = id && CHAT_MODES.includes(mode) && (!viewer || viewer.lines) ? params.get('result') || undefined : undefined
  return { mode, id, tab: id && third ? third : null, from, ...(panel && { panel }), ...(viewer && { viewer }), ...(result && { result }) }
}

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parse(hash)
}

export function href(mode: Route['mode'], id?: string | null, tab?: string | null, from?: string | null, panel?: WorkSelection,
  viewer?: ViewerTarget, result?: string) {
  const path = '#/' + [mode, id, id ? tab : null].filter(Boolean).join('/')
  const params = new URLSearchParams()
  if (id && from && UUID.test(from)) params.set('from', from)
  if (id && panel && CHAT_MODES.includes(mode)) {
    params.set('panel', panel.tab)
    if (panel.selected) params.set('selected', panel.selected)
  }
  if (viewer) setViewer(params, viewer, 'file')
  if (id && result && CHAT_MODES.includes(mode)) params.set('result', result)
  return path + (params.size ? '?' + params.toString() : '')
}

/** Open the notebook. Asked for by the reader, it replaces an open file viewer (which asks first about unsaved
 * changes); opened automatically (parallel work starting), it waits behind the viewer. */
export function openWorkPanel(tab: WorkTab, selected: string | null = null, automatic = false) {
  const route = parse(window.location.hash)
  if (!route.id || !CHAT_MODES.includes(route.mode)) return
  if (!automatic && route.viewer && !leaveFile()) return
  if (!automatic) rememberWorkPanel({ open: true, tab })
  const url = automatic ? href(route.mode, route.id, route.tab, route.from, { tab, selected }, route.viewer, route.result)
    : href(route.mode, route.id, route.tab, route.from, { tab, selected })
  if (automatic) {
    window.history.replaceState(null, '', url)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  } else {
    window.location.hash = url
  }
}

export function closeWorkPanel() {
  const route = parse(window.location.hash)
  rememberWorkPanel({ open: false })
  window.location.hash = href(route.mode, route.id, route.tab, route.from, undefined, route.viewer, route.result)
}

/** `file` keeps the viewer panel open on the new page (a new conversation created from the start page). */
export function go(mode: Mode, id?: string | null, tab?: string | null, from?: string | null, viewer?: ViewerTarget) {
  const current = parse(window.location.hash)
  const origin = from === undefined && current.mode === mode && current.id === id ? current.from : from
  window.location.hash = href(mode, id, tab, origin, undefined, viewer)
}

/** Older chats and About share the conversation sidebar. */
export function notebook(mode: Route['mode']): Mode {
  return mode === 'critic' || mode === 'explore' || mode === 'about' || mode === 'file' ? 'free' : mode
}

/** The viewer page of a file, by its path relative to the interface root; `from`, the conversation it was opened
 * from, gives the page its way back. Used where no conversation is open (a job page, About). */
export function fileHref(path: string, from?: string | null) {
  return '#/file?' + new URLSearchParams({ path, ...(from && UUID.test(from) && { from }) }).toString()
}

/** Citations keep the current conversation or job visible beside the source. */
export function viewerHref(route: Route, target: ViewerTarget) {
  if (CHAT_MODES.includes(route.mode) || (route.id && (target.lines || target.kind === 'document'))) {
    return href(route.mode, route.id, route.tab, route.from, route.panel, target, target.lines ? route.result : undefined)
  }
  const params = new URLSearchParams()
  if (route.mode === 'file' && route.from) params.set('from', route.from)
  setViewer(params, target, 'path')
  return '#/file?' + params.toString()
}

export function viewHref(route: Route, path: string) {
  return viewerHref(route, { kind: 'file', path })
}

export const REVEAL_LINES = 'square-reveal-lines'

export function openViewer(target: ViewerTarget) {
  const route = parse(window.location.hash)
  const url = viewerHref(route, target)
  if (url === window.location.hash) {
    if (target.lines) window.dispatchEvent(new Event(REVEAL_LINES))
    return
  }
  if (route.viewer && (route.viewer.kind !== target.kind || route.viewer.path !== target.path) && !leaveFile()) return
  window.location.hash = url
}

export function openFile(path: string) {
  openViewer({ kind: 'file', path })
}

/** Open a document an agent produced (a result of the notebook's Results) in the viewer, in the notebook's place. */
export function openResult(result: string) {
  const route = parse(window.location.hash)
  if (!route.id || !CHAT_MODES.includes(route.mode) || (route.result === result && !route.viewer)) return
  if (route.viewer && !leaveFile()) return
  window.location.hash = href(route.mode, route.id, route.tab, route.from, route.panel, undefined, result)
}

/** The address of a result in the viewer beside its conversation (for links). */
export function resultHref(mode: Mode, chat: string, result: string) {
  return href(mode, chat, null, null, undefined, undefined, result)
}

/** Close the viewer panel (a file or a result): the notebook, if it was open, comes back. */
export function closeFile() {
  const route = parse(window.location.hash)
  if (!route.viewer && !route.result) return
  if (route.viewer && !leaveFile()) return
  window.location.hash = href(route.mode, route.id, route.tab, route.from, route.panel, undefined, route.viewer ? route.result : undefined)
}

// Unsaved changes in the viewer: whatever replaces it asks first.
let unsaved: { path: string; discard: () => void } | null = null

/** The viewer reports its unsaved changes (or null); `discard` forgets them once the reader agrees to leave. */
export function setUnsavedFile(value: typeof unsaved) { unsaved = value }

/** True when the viewer may be replaced: nothing unsaved, or the reader agreed to drop the changes. */
export function leaveFile() {
  if (!unsaved) return true
  if (!window.confirm(`Leave ${unsaved.path.split('/').pop()} without saving your changes?`)) return false
  unsaved.discard()
  unsaved = null
  return true
}
