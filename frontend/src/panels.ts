// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Side panels the reader can slide away on wide screens: the notebook's list on the
// left and a job's overview (budget, candidates, checks) on the right. Remembered
// per browser; phones and narrow windows ignore it.
import { useSyncExternalStore } from 'react'
import type { WorkTab } from './router'

export type Panel = 'sidebar' | 'aside'

const KEYS: Record<Panel, string> = { sidebar: 'square-hide-sidebar', aside: 'square-hide-aside' }
const listeners = new Set<() => void>()

function read(panel: Panel) {
  try {
    return localStorage.getItem(KEYS[panel]) === '1'
  } catch {
    return false
  }
}

const hidden: Record<Panel, boolean> = { sidebar: read('sidebar'), aside: read('aside') }

export function setPanelHidden(panel: Panel, value: boolean) {
  hidden[panel] = value
  try {
    if (value) localStorage.setItem(KEYS[panel], '1')
    else localStorage.removeItem(KEYS[panel])
  } catch {
    /* private mode: the choice lasts for this page */
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function usePanelHidden(panel: Panel): boolean {
  return useSyncExternalStore(subscribe, () => hidden[panel])
}

/** For event handlers: whether the panel is hidden right now. */
export function isPanelHidden(panel: Panel): boolean {
  return hidden[panel]
}

type WorkPreference = { open?: boolean; width?: number; tab?: WorkTab }

export function workPreferences(): WorkPreference {
  try {
    const value = JSON.parse(localStorage.getItem('square-work-panel') || '{}')
    return {
      open: typeof value?.open === 'boolean' ? value.open : undefined,
      width: typeof value?.width === 'number' && Number.isFinite(value.width) ? value.width : undefined,
      tab: ['agents', 'outputs', 'record', 'plan'].includes(value?.tab) ? value.tab : undefined,
    }
  } catch { return {} }
}

export function rememberWorkPanel(change: WorkPreference) {
  try { localStorage.setItem('square-work-panel', JSON.stringify({ ...workPreferences(), ...change })) }
  catch { /* Navigation and resizing still work without browser storage. */ }
}
