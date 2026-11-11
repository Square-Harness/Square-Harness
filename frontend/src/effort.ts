// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Effort levels: one control instead of budget fields (no React; testable with node).
import type { Defaults } from './api'

export type EffortKind = 'proof' | 'research' | 'writeup' | 'style'
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
  rounds?: number; tokens: number | null; seconds: number; input_tokens?: number | null; requests?: number; chars?: number
  solve_tokens?: number; verify_tokens?: number
}

const count = (value: number) => new Intl.NumberFormat(undefined).format(Math.round(value))

export function isLevel(value: unknown): value is EffortLevel {
  return typeof value === 'string' && (LEVELS as string[]).includes(value)
}

/** A proof needs room for one full solve and its review: fit the per-call ceilings in the budget. */
function proofCeilings(defaults: Defaults, tokens: number | null, preset?: { solve_tokens: number; verify_tokens: number }) {
  const solve = Math.min(defaults.proof_solve_tokens, preset?.solve_tokens ?? defaults.proof_solve_tokens)
  const verify = Math.min(defaults.proof_verify_tokens, preset?.verify_tokens ?? defaults.proof_verify_tokens)
  // DS4 keeps its full role ceilings even when an explicit total cannot fund
  // them. Admission reports that constraint; selecting effort never hides it.
  if (defaults.api_profile === 'deepinfra') return { tokens, solve_tokens: solve, verify_tokens: verify }
  if (tokens === null || tokens >= solve + verify) return { tokens, solve_tokens: solve, verify_tokens: verify }
  // Launch floors for repairs stay valid: the solve ceiling never goes below them.
  const floor = Math.max(128, defaults.proof_repair_tokens ?? 0, defaults.proof_min_solve_tokens ?? 0)
  const fitted = Math.min(solve, Math.max(floor, Math.floor((tokens * solve) / (solve + verify))))
  const review = Math.min(verify, Math.max(128, tokens - fitted))
  return { tokens, solve_tokens: fitted, verify_tokens: review }
}

export function effortLimits(kind: EffortKind, defaults: Defaults, level: EffortLevel): Limits {
  const preset = defaults.effort_presets?.[level]
  const effort = preset || EFFORTS.find((item) => item.id === level) || EFFORTS[1]
  const seconds = effort.minutes * 60
  const cap = (configured: number | null, value: number) => configured === null ? value : Math.min(configured, value)
  if (kind === 'proof') {
    const tokens = preset ? cap(defaults.proof_tokens, preset.tokens) : defaults.proof_tokens
    return { rounds: effort.tries, seconds: preset?.proof_seconds ?? seconds,
      ...proofCeilings(defaults, tokens, preset) }
  }
  const base: Limits = { tokens: preset ? cap(defaults.research_tokens, preset.research_tokens) : defaults.research_tokens,
    input_tokens: preset ? cap(defaults.research_input_tokens, preset.input) : defaults.research_input_tokens, seconds }
  if (kind === 'writeup' || kind === 'style') return base
  return { ...base, rounds: effort.tries, requests: effort.requests, chars: effort.chars }
}

export function minutesLabel(seconds: number) {
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  const hours = seconds / 3600
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`
}

export function describeLimits(kind: EffortKind, limits: Limits) {
  const tries = (n: number, one: string) => `${count(n)} ${n === 1 ? one : one + 's'}`
  const tokenCap = limits.tokens === null ? '' : `; output capped at ${count(limits.tokens)} tokens`
  if (kind === 'proof') return `Up to ${tries(limits.rounds!, 'attempt')} with independent review; ${count(limits.solve_tokens!)} tokens per solve and ${count(limits.verify_tokens!)} per review; time limit ${minutesLabel(limits.seconds)}${tokenCap}`
  if (kind === 'writeup') return `One writing call (in parts if the material is long), then compiled; ${minutesLabel(limits.seconds)} working time${tokenCap}`
  if (kind === 'style') return `Each article read once, then each domain merged; ${minutesLabel(limits.seconds)} working time${tokenCap}`
  const depth = EFFORTS.find((effort) => effort.tries === limits.rounds)?.label || 'Custom'
  return `Checking depth: ${depth}; ${minutesLabel(limits.seconds)} working time${tokenCap}`
}
