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
  /** The file the viewer shows (#/file?path=…), relative to the interface root. */
  path?: string | null }

export function parse(hash: string): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const [first, second, third] = path.split('/')
  if (first === 'about') return { mode: 'about', id: null, tab: null, from: null }
  if (first === 'file') return { mode: 'file', id: null, tab: null, from: null, path: new URLSearchParams(search).get('path') }
  const requestedMode = (MODE_ORDER as string[]).includes(first) ? (first as Mode) : 'free'
  const id = second && UUID.test(second) ? second : null
  // Other notebooks open from conversations, cards and links; the Style page is opened directly.
  const mode = id || ['prove', 'literature', 'referee', 'writeup', 'style'].includes(requestedMode) ? requestedMode : 'free'
  const params = new URLSearchParams(search)
  const origin = params.get('from')
  const from = id && origin && UUID.test(origin) ? origin : null
  const panelTab = WORK_TABS.find((item) => item === params.get('panel'))
  const selected = params.get('selected')
  const panel = id && ['free', 'critic', 'explore'].includes(mode) && panelTab
    ? { tab: panelTab, selected: selected && selected.length <= 400 ? selected : null } : undefined
  return { mode, id, tab: id && third ? third : null, from, ...(panel && { panel }) }
}

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parse(hash)
}

export function href(mode: Route['mode'], id?: string | null, tab?: string | null, from?: string | null, panel?: WorkSelection) {
  const path = '#/' + [mode, id, id ? tab : null].filter(Boolean).join('/')
  const params = new URLSearchParams()
  if (id && from && UUID.test(from)) params.set('from', from)
  if (id && panel && ['free', 'critic', 'explore'].includes(mode)) {
    params.set('panel', panel.tab)
    if (panel.selected) params.set('selected', panel.selected)
  }
  return path + (params.size ? '?' + params.toString() : '')
}

export function openWorkPanel(tab: WorkTab, selected: string | null = null, automatic = false) {
  const route = parse(window.location.hash)
  if (!route.id || !['free', 'critic', 'explore'].includes(route.mode)) return
  if (!automatic) rememberWorkPanel({ open: true, tab })
  const url = href(route.mode, route.id, route.tab, route.from, { tab, selected })
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
  window.location.hash = href(route.mode, route.id, route.tab, route.from)
}

export function go(mode: Mode, id?: string | null, tab?: string | null, from?: string | null) {
  const current = parse(window.location.hash)
  const origin = from === undefined && current.mode === mode && current.id === id ? current.from : from
  window.location.hash = href(mode, id, tab, origin)
}

/** Older chats and About share the conversation sidebar. */
export function notebook(mode: Route['mode']): Mode {
  return mode === 'critic' || mode === 'explore' || mode === 'about' || mode === 'file' ? 'free' : mode
}

/** The viewer page of a file, by its path relative to the interface root. */
export function fileHref(path: string) {
  return '#/file?' + new URLSearchParams({ path }).toString()
}

/** Open a file in the viewer, in a new tab: the conversation stays where it was. */
export function openFile(path: string) {
  window.open(window.location.pathname + window.location.search + fileHref(path), '_blank', 'noopener')
}
