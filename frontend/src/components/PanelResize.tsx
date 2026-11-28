// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

/** The side panels beside a conversation (the notebook, the file viewer) sit beside it from this width; below, they
 * cover the page. */
export const WIDE = '(min-width: 1200px)'
export const PANEL_MIN = 304
// A panel may cover up to 75% of the window; the conversation column narrows to make room.
const MAX_SHARE = 0.75
export const panelMax = () => Math.max(PANEL_MIN, Math.round(window.innerWidth * MAX_SHARE))
export const clampPanel = (value: number) => Math.max(PANEL_MIN, Math.min(panelMax(), Math.round(value)))

export function useWide() {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches)
  useEffect(() => {
    const query = window.matchMedia(WIDE)
    const changed = () => setWide(query.matches)
    query.addEventListener('change', changed)
    return () => query.removeEventListener('change', changed)
  }, [])
  return wide
}

/** A panel's width, from the stored preference. A narrower window lowers it; the preference is kept for later. */
export function usePanelWidth(preferred: () => number) {
  const [width, setWidth] = useState(() => clampPanel(preferred()))
  const latest = useRef(preferred)
  latest.current = preferred
  useEffect(() => {
    const fit = () => setWidth((current) => clampPanel(Math.max(current, latest.current())))
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])
  return [width, setWidth] as const
}

/** Gives the page the panel's width (the Feedback button keeps clear of it). */
export function usePublishedWidth(pane: RefObject<HTMLElement | null>, width: number) {
  useLayoutEffect(() => {
    const main = pane.current?.closest('main')
    if (!main) return
    main.style.setProperty('--work-panel-width', width + 'px')
    return () => { main.style.removeProperty('--work-panel-width') }
  }, [pane, width])
}

/** The panel's left edge: drag it, or focus it and use the arrow keys (Home and End for the narrowest and widest). */
export function PanelGrip({ label, width, onWidth, onCommit, onDragging }: {
  label: string; width: number; onWidth: (value: number) => void; onCommit: (value: number) => void
  /** While dragging, frames (a PDF) must not swallow the pointer. */
  onDragging?: (dragging: boolean) => void
}) {
  const drag = useRef<{ x: number; width: number } | null>(null)
  const end = () => { drag.current = null; onDragging?.(false) }
  return <div role="separator" aria-label={label} aria-orientation="vertical" tabIndex={0}
    aria-valuemin={PANEL_MIN} aria-valuemax={panelMax()} aria-valuenow={width} className="work-panel-grip"
    onPointerDown={(e) => { if (e.button !== 0) return; drag.current = { x: e.clientX, width }; e.currentTarget.setPointerCapture(e.pointerId); onDragging?.(true) }}
    onPointerMove={(e) => { if (drag.current) onWidth(clampPanel(drag.current.width + drag.current.x - e.clientX)) }}
    onPointerUp={(e) => { if (drag.current) { const value = clampPanel(drag.current.width + drag.current.x - e.clientX); onWidth(value); onCommit(value) } end() }}
    onPointerCancel={end}
    onKeyDown={(e) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
      e.preventDefault()
      const value = e.key === 'Home' ? PANEL_MIN : e.key === 'End' ? panelMax() : clampPanel(width + (e.key === 'ArrowLeft' ? 1 : -1) * (e.shiftKey ? 64 : 16))
      onWidth(value); onCommit(value)
    }} />
}
