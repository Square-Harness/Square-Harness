// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useSyncExternalStore } from 'react'

export type Mode = 'free' | 'prove' | 'critic' | 'explore' | 'literature' | 'referee' | 'writeup' | 'style'
export const MODE_ORDER: Mode[] = ['free', 'prove', 'critic', 'explore', 'literature', 'referee', 'writeup', 'style']
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/

export type Route = { mode: Mode | 'about'; id: string | null; tab: string | null; from: string | null }

export function parse(hash: string): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const [first, second, third] = path.split('/')
  if (first === 'about') return { mode: 'about', id: null, tab: null, from: null }
  const requestedMode = (MODE_ORDER as string[]).includes(first) ? (first as Mode) : 'free'
  const id = second && UUID.test(second) ? second : null
  // Other notebooks open from conversations, cards and links; the Style page is opened directly.
  const mode = id || ['prove', 'literature', 'referee', 'writeup', 'style'].includes(requestedMode) ? requestedMode : 'free'
  const origin = new URLSearchParams(search).get('from')
  const from = id && origin && UUID.test(origin) ? origin : null
  return { mode, id, tab: id && third ? third : null, from }
}

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parse(hash)
}

export function href(mode: Route['mode'], id?: string | null, tab?: string | null, from?: string | null) {
  const path = '#/' + [mode, id, id ? tab : null].filter(Boolean).join('/')
  return path + (id && from && UUID.test(from) ? '?from=' + from : '')
}

export function go(mode: Mode, id?: string | null, tab?: string | null, from?: string | null) {
  const current = parse(window.location.hash)
  const origin = from === undefined && current.mode === mode && current.id === id ? current.from : from
  window.location.hash = href(mode, id, tab, origin)
}

/** Older chats and About share the conversation sidebar. */
export function notebook(mode: Route['mode']): Mode {
  return mode === 'critic' || mode === 'explore' || mode === 'about' ? 'free' : mode
}
