// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Terminal-style recall for the message box: ↑ in an empty box brings back the last message sent,
// then ↑ and ↓ walk the earlier ones. A recalled message that was edited is a draft like any other.
import { useRef, type KeyboardEvent } from 'react'

const MAX_RECENT = 50
const recent: string[] = []

/** Messages sent from this tab, oldest first: the history of a conversation that has none yet. */
export const recentMessages = (): readonly string[] => recent
export function rememberMessage(text: string) {
  if (!text.trim() || recent.at(-1) === text) return
  recent.push(text)
  if (recent.length > MAX_RECENT) recent.shift()
}

/** One step from `index` in `history` (oldest first; `history.length` is the empty draft), or null to leave the key alone. */
export function recallStep(history: readonly string[], index: number, up: boolean): number | null {
  const next = index + (up ? -1 : 1)
  return next < 0 || next > history.length ? null : next
}

/**
 * Returns a key handler: true when it handled an arrow key. It acts only in an empty box or on an unedited
 * recalled message, with the caret on the first line (↑) or the last (↓), so arrows still move inside a draft.
 */
export function useRecall(history: readonly string[], value: string, set: (text: string) => void) {
  const shown = useRef<{ index: number; text: string } | null>(null)
  return (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if ((event.key !== 'ArrowUp' && event.key !== 'ArrowDown') || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey
        || event.nativeEvent.isComposing) return false
    const up = event.key === 'ArrowUp'
    const browsing = shown.current !== null && value === shown.current.text && history[shown.current.index] === value
    if (browsing ? false : value !== '' || !up) return false
    const box = event.currentTarget
    if (box.selectionStart !== box.selectionEnd) return false
    if (up ? value.lastIndexOf('\n', box.selectionStart - 1) !== -1 : value.indexOf('\n', box.selectionStart) !== -1) return false
    const next = recallStep(history, browsing ? shown.current!.index : history.length, up)
    if (next === null) return false
    event.preventDefault()
    shown.current = next === history.length ? null : { index: next, text: history[next] }
    set(shown.current?.text ?? '')
    return true
  }
}
