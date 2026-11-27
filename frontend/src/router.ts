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
export type Route = { mode: Mode | 'about' | 'file'; id: string | null; tab: string | null; from: string | null; panel?: WorkSelection
  /** The file the viewer page shows (#/file?path=…), relative to the interface root. */
  path?: string | null
  /** The file the viewer panel shows beside a conversation (?file=…), in the notebook's place. */
  file?: string }

const CHAT_MODES = ['free', 'critic', 'explore']
const validFile = (value: string | null) => value && value.length <= 1000 ? value : undefined

export function parse(hash: string): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const [first, second, third] = path.split('/')
  if (first === 'about') return { mode: 'about', id: null, tab: null, from: null }
  if (first === 'file') {
    const params = new URLSearchParams(search)
    const origin = params.get('from')
    return { mode: 'file', id: null, tab: null, from: origin && UUID.test(origin) ? origin : null, path: params.get('path') }
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
  const file = CHAT_MODES.includes(mode) ? validFile(params.get('file')) : undefined
  return { mode, id, tab: id && third ? third : null, from, ...(panel && { panel }), ...(file && { file }) }
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
  file?: string) {
  const path = '#/' + [mode, id, id ? tab : null].filter(Boolean).join('/')
  const params = new URLSearchParams()
  if (id && from && UUID.test(from)) params.set('from', from)
  if (id && panel && CHAT_MODES.includes(mode)) {
    params.set('panel', panel.tab)
    if (panel.selected) params.set('selected', panel.selected)
  }
  if (file && CHAT_MODES.includes(mode)) params.set('file', file)
  return path + (params.size ? '?' + params.toString() : '')
}

/** Open the notebook. Asked for by the reader, it replaces an open file viewer (which asks first about unsaved
 * changes); opened automatically (parallel work starting), it waits behind the viewer. */
export function openWorkPanel(tab: WorkTab, selected: string | null = null, automatic = false) {
  const route = parse(window.location.hash)
  if (!route.id || !CHAT_MODES.includes(route.mode)) return
  if (!automatic && route.file && !leaveFile()) return
  if (!automatic) rememberWorkPanel({ open: true, tab })
  const url = href(route.mode, route.id, route.tab, route.from, { tab, selected }, automatic ? route.file : undefined)
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
  window.location.hash = href(route.mode, route.id, route.tab, route.from, undefined, route.file)
}

/** `file` keeps the viewer panel open on the new page (a new conversation created from the start page). */
export function go(mode: Mode, id?: string | null, tab?: string | null, from?: string | null, file?: string) {
  const current = parse(window.location.hash)
  const origin = from === undefined && current.mode === mode && current.id === id ? current.from : from
  window.location.hash = href(mode, id, tab, origin, undefined, file)
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

/** Where a file opens from `route`: the viewer panel beside the conversation, or the viewer page elsewhere. */
export function viewHref(route: Route, path: string) {
  return CHAT_MODES.includes(route.mode) ? href(route.mode, route.id, route.tab, route.from, route.panel, path) : fileHref(path)
}

/** Open a file in the viewer, in this window: beside the conversation, in the notebook's place. */
export function openFile(path: string) {
  const route = parse(window.location.hash)
  if (route.file === path || route.path === path) return
  if ((route.file || route.mode === 'file') && !leaveFile()) return
  window.location.hash = viewHref(route, path)
}

/** Close the viewer panel: the notebook, if it was open, comes back. */
export function closeFile() {
  const route = parse(window.location.hash)
  if (!route.file || !leaveFile()) return
  window.location.hash = href(route.mode, route.id, route.tab, route.from, route.panel)
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
