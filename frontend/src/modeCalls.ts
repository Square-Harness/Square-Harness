// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { RouteMode } from './api'
import type { Cover } from './components/Glyph'

/** The cover a mode call is drawn with: the Proof Check, the Review, the Typo check and Explain each have their own mark. */
export const coverOf = (mode: RouteMode): Cover =>
  mode === 'quick_review' ? 'proof_check' : mode === 'typo_check' ? 'typo' : mode === 'detailed_review' ? 'referee'
    : mode === 'summarize' ? 'writeup' : mode
export const MODE_CALLS: { mode: RouteMode; label: string; summary: string }[] = [
  { mode: 'prove', label: 'Prove', summary: 'Prove a statement, review the proof, and repair objections.' },
  { mode: 'check', label: 'Literature Check', summary: 'Find or verify a reference for a known result.' },
  { mode: 'literature', label: 'Literature', summary: 'Survey a mathematical question through its main results, methods and sources.' },
  { mode: 'quick_review', label: 'Proof Check', summary: 'Check whether a given proof is correct: verdict and located issues.' },
  { mode: 'referee', label: 'Review', summary: 'Review the statements and proofs of an attached manuscript.' },
  { mode: 'typo_check', label: 'Typo', summary: 'List the typos and presentation problems of a manuscript, each correction checked.' },
  { mode: 'explain', label: 'Explain', summary: 'Explain a result and its proof precisely, step by step.' },
  { mode: 'writeup', label: 'Write-Up', summary: 'Write up results from this conversation, or an attached draft, as a .tex file in your style. Alone, it summarizes.' },
  { mode: 'summarize', label: 'Summarize', summary: 'Summarize the important results obtained so far as a .tex file.' },
  { mode: 'investigate', label: 'Investigate', summary: 'Make a research plan, follow several lines of work, and bring the findings together.' },
  { mode: 'explore', label: 'Explore', summary: 'Explore approaches, with heuristics labelled.' },
  // User-only: the Assistant cannot start these itself.
  { mode: 'python_lab', label: 'Python Lab', summary: 'Run Python in an isolated sandbox: computations, simulations and plots.' },
  { mode: 'sanity_check', label: 'Sanity Check', summary: 'Try to falsify a claim with quick numerical experiments.' },
]
/** Exact names and id prefixes take precedence over words inside a label. */
export const modeOptions = (query: string) => {
  const score = (item: typeof MODE_CALLS[number]) => item.mode === query || item.label.toLowerCase() === query ? 0
    : item.mode.startsWith(query) ? 1 : item.label.toLowerCase().split(/\s+/).some((word) => word.startsWith(query)) ? 2 : 3
  return MODE_CALLS.filter((item) => score(item) < 3).sort((a, b) => score(a) - score(b))
}
export type ModeCall = { mode: RouteMode; start: number; end: number }
export type ModeQuery = { start: number; end: number; query: string }
const word = (char: string) => /[\p{L}\p{N}_@.+/\-]/u.test(char)

export function validCall(text: string, call: ModeCall | null): call is ModeCall {
  return !!call && text.slice(call.start, call.end) === '@' + call.mode
    && (!call.start || !word(text[call.start - 1]))
    && (call.end === text.length || !word(text[call.end]))
}

/** A mode name typed out in full, as if picked from the menu: followed by a space, or ending the text when `atEnd`. */
export function typedCall(text: string, atEnd = false): ModeCall | null {
  for (const match of text.matchAll(/@([a-z_]+)/g)) {
    const start = match.index!, end = start + match[0].length
    if (!MODE_CALLS.some((item) => item.mode === match[1]) || (start && word(text[start - 1]))) continue
    if (end < text.length ? !/\s/.test(text[end]) : !atEnd) continue
    return { mode: match[1] as RouteMode, start, end }
  }
  return null
}

export function modeQuery(text: string, caret: number, end = caret): ModeQuery | null {
  if (caret !== end) return null
  const before = text.slice(0, caret)
  const match = /@([a-z]*)$/i.exec(before)
  if (!match || (match.index && word(text[match.index - 1]))) return null
  const tail = /^[a-z]*/i.exec(text.slice(caret))![0]
  const tokenEnd = caret + tail.length
  if (tokenEnd < text.length && word(text[tokenEnd])) return null
  return { start: match.index, end: tokenEnd, query: match[1].toLowerCase() }
}

export function updateCall(before: string, after: string, call: ModeCall | null): ModeCall | null {
  if (!call) return null
  let start = 0
  while (start < before.length && start < after.length && before[start] === after[start]) start++
  let end = before.length, nextEnd = after.length
  while (end > start && nextEnd > start && before[end - 1] === after[nextEnd - 1]) { end--; nextEnd-- }
  const delta = after.length - before.length
  const next = end <= call.start ? { ...call, start: call.start + delta, end: call.end + delta }
    : start >= call.end ? call : null
  return validCall(after, next) ? next : null
}

export function callPayload(text: string, call: ModeCall | null) {
  if (!validCall(text, call)) return undefined
  // DOM selections use UTF-16 offsets; the server indexes Unicode code points.
  return { mode: call.mode, start: Array.from(text.slice(0, call.start)).length, effort: 'high' as const }
}
