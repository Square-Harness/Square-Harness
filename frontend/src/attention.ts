// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// While the tab is in the background, its title announces work that ended, until the tab is looked at again.
import type { TaskSummary } from './api'

type Ended = Pick<TaskSummary, 'id' | 'label' | 'state'>

/** The tab title for the work that ended out of sight: ∎ for finished work, □ when something stopped short. */
export function attentionTitle(base: string, ended: readonly Ended[]): string {
  if (!ended.length) return base
  const finished = ended.every((task) => task.state === 'done')
  const what = ended.length > 1 ? `${ended.length} jobs ${finished ? 'finished' : 'ended'}`
    : `${ended[0].label} ${ended[0].state === 'done' ? 'finished' : ended[0].state === 'paused' ? 'paused' : 'stopped'}`
  return `${finished ? '∎' : '□'} ${what} · ${base}`
}

let base = ''
let ended: Ended[] = []
let listening = false

/** Called for every task that reaches an end state (done, paused, error). */
export function announceEnd(task: TaskSummary) {
  if (typeof document === 'undefined' || !document.hidden || task.killed || !['proof', 'research', 'chat'].includes(task.kind)) return
  if (!ended.length) base = document.title
  ended = [...ended.filter((item) => item.id !== task.id), task]
  document.title = attentionTitle(base, ended)
  if (listening) return
  listening = true
  document.addEventListener('visibilitychange', () => {
    if (document.hidden || !ended.length) return
    ended = []
    document.title = base
  })
}
