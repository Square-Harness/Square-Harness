// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Fresh work uses High. Earlier levels can still be displayed on saved jobs.
import type { Defaults } from './api'

export type EffortKind = 'proof' | 'literature' | 'research' | 'writeup' | 'style'
export type EffortLevel = 'low' | 'medium' | 'high'

// Effort sets checking depth, attempts, and time. Token estimates remain in this
// table for older integrations; they are not cumulative limits for new work.
export const EFFORTS: {
  id: EffortLevel; label: string; hint: string
  tries: number; minutes: number; tokens: number; input: number; requests: number; chars: number
}[] = [
  { id: 'low', label: 'Low', hint: 'One quick try', tries: 1, minutes: 1, tokens: 30_000, input: 120_000, requests: 20, chars: 15_000 },
  { id: 'medium', label: 'Medium', hint: 'Several passes with independent checks', tries: 3, minutes: 15, tokens: 60_000, input: 240_000, requests: 50, chars: 30_000 },
  { id: 'high', label: 'High', hint: 'Deeper checking and more approaches', tries: 5, minutes: 30, tokens: 100_000, input: 400_000, requests: 80, chars: 60_000 },
]

export const LEVELS = EFFORTS.map((effort) => effort.id)

export type Limits = {
  rounds?: number; tokens: number | null; seconds: number; input_tokens?: number | null; requests?: number | null; chars?: number | null
  solve_tokens?: number; verify_tokens?: number
}

const count = (value: number) => new Intl.NumberFormat('en-US').format(Math.round(value))

export function isLevel(value: unknown): value is EffortLevel {
  return typeof value === 'string' && (LEVELS as string[]).includes(value)
}

/** Whether the server runs a hosted DS4 profile (deepinfra, openrouter): it then sends its DS4 budget profile. */
export function isDS4(defaults: Defaults): boolean {
  return !!defaults.budget_profile && defaults.budget_profile !== 'default'
}

export function effortLimits(kind: EffortKind, defaults: Defaults, wholeManuscript = false): Limits {
  const level = 'high'
  const preset = defaults.effort_presets?.[level]
  const effort = preset || EFFORTS.find((item) => item.id === level)!
  // Whole-manuscript reviews and typo checks read the full paper each time (gui/effort.py).
  const ds4 = isDS4(defaults)
  const seconds = effort.minutes * 60 * (wholeManuscript && !ds4 ? 2 : 1)
  const cap = (configured: number | null, value: number) => configured === null ? value : Math.min(configured, value)
  if (kind === 'proof') {
    const tokens = preset ? cap(defaults.proof_tokens, preset.tokens) : defaults.proof_tokens
    return { rounds: defaults.proof_rounds, seconds: Math.min(preset?.proof_seconds ?? seconds, defaults.proof_seconds), tokens,
      solve_tokens: preset?.solve_tokens ?? defaults.proof_solve_tokens, verify_tokens: preset?.verify_tokens ?? defaults.proof_verify_tokens }
  }
  const base: Limits = { tokens: preset ? cap(defaults.research_tokens, preset.research_tokens) : defaults.research_tokens,
    input_tokens: preset ? cap(defaults.research_input_tokens, preset.input * (wholeManuscript && ds4 ? 4 : 1)) : defaults.research_input_tokens, seconds }
  if (kind === 'writeup' || kind === 'style') return base
  const survey = kind === 'literature' ? defaults.literature_presets?.[level] : undefined
  return { ...base, rounds: defaults.research_rounds, seconds: survey?.seconds ?? seconds, requests: sourceLimit(defaults.research_requests), chars: sourceLimit(defaults.research_chars) }
}

function sourceLimit(value: number | null | undefined) {
  return value == null || value === Number.MAX_SAFE_INTEGER ? null : value
}

export function minutesLabel(seconds: number) {
  if (seconds < 60) return `${Number(seconds.toFixed(2))} s`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  const hours = seconds / 3600
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`
}

export function describeLimits(kind: EffortKind, limits: Limits) {
  const tries = (n: number, one: string) => `${count(n)} ${n === 1 ? one : one + 's'}`
  const tokenCap = limits.tokens === null ? '' : `; output capped at ${count(limits.tokens)} tokens`
  if (kind === 'proof') return `Up to ${tries(limits.rounds!, 'attempt')} with independent review; ${count(limits.solve_tokens!)} tokens per solve and ${count(limits.verify_tokens!)} per review; time limit ${minutesLabel(limits.seconds)}${tokenCap}`
  if (kind === 'writeup') return `One writing call (in parts if the material is long), then compiled; ${minutesLabel(limits.seconds)} working time${tokenCap}`
  if (kind === 'literature') return `Up to ${minutesLabel(limits.seconds)} working time${limits.requests == null ? '' : ` and ${count(limits.requests)} source requests`}${tokenCap}`
  const depth = EFFORTS.find((effort) => effort.tries === limits.rounds)?.label || 'Custom'
  return `Checking depth: ${depth}; ${minutesLabel(limits.seconds)} working time${tokenCap}`
}
