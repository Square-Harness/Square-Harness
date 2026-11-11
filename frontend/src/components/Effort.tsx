// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Effort: one control instead of budget fields. The levels and their budgets are in effort.ts.
import { useEffect, useRef, type KeyboardEvent } from 'react'
import type { Defaults } from '../api'
import { describeLimits, EFFORTS, effortLimits, type EffortKind, type EffortLevel } from '../effort'

export { describeLimits, EFFORTS, effortLimits, isLevel } from '../effort'
export type { EffortKind, EffortLevel, Limits } from '../effort'

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

export function EffortSlider({ kind, defaults, level, onLevel, custom, compact, suggested }: {
  kind: EffortKind; defaults: Defaults; level: EffortLevel; onLevel: (level: EffortLevel) => void
  custom?: boolean; compact?: boolean; suggested?: boolean
}) {
  const index = EFFORTS.findIndex((effort) => effort.id === level)
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const focusNext = useRef(false)
  useEffect(() => {
    if (focusNext.current) { refs.current[index]?.focus(); focusNext.current = false }
  }, [index])
  const keys = (event: KeyboardEvent) => {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowUp' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 0
    if (!step) return
    event.preventDefault()
    focusNext.current = true
    onLevel(EFFORTS[clamp(index + step, 0, EFFORTS.length - 1)].id)
  }
  const limits = effortLimits(kind, defaults, level)
  return (
    <div className={'effort' + (compact ? ' effort-compact' : '')}>
      <div className="effort-head">
        <span className="field-label">Effort{suggested && <span className="effort-suggested"> suggested by the model</span>}</span>
        <span className="effort-now">
          {custom ? 'Custom limits' : EFFORTS[index].label}
        </span>
      </div>
      <div className="effort-track" role="radiogroup" aria-label="Effort" onKeyDown={keys}>
        <span className="effort-line" aria-hidden="true"><span className="effort-fill" style={{ width: `${(index / (EFFORTS.length - 1)) * 100}%` }} /></span>
        {EFFORTS.map((effort, i) => (
          <button key={effort.id} ref={(node) => { refs.current[i] = node }} type="button" role="radio"
            aria-checked={effort.id === level} tabIndex={effort.id === level ? 0 : -1} title={effort.hint}
            className={'effort-stop' + (i <= index ? ' effort-stop-on' : '') + (effort.id === level ? ' effort-stop-current' : '')}
            onClick={() => onLevel(effort.id)}>
            <span className="effort-square" aria-hidden="true" />
            <span className="effort-label">{effort.label}</span>
          </button>
        ))}
      </div>
      <p className={'field-hint effort-summary' + (compact ? ' effort-summary-compact' : '')}>
        {describeLimits(kind, limits)}{custom ? '; changed in Advanced limits below' : ''}.
      </p>
    </div>
  )
}
