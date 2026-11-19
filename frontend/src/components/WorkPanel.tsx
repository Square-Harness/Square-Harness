// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import type { Chat } from '../api'
import { rememberWorkPanel, workPreferences } from '../panels'
import { closeWorkPanel, openWorkPanel, useRoute, WORK_TABS, type WorkTab } from '../router'
import { AgentsTab } from './AgentGroup'
import { OutputsTab } from './OutputsTab'
import { PlanTab } from './PlanTab'
import { RecordTab } from './RecordTab'
import { CloseIcon } from './Icons'
import './WorkPanel.css'

const WIDE = '(min-width: 1320px)'
const DEFAULT_WIDTH = 390
const MIN_WIDTH = 304
const MAX_WIDTH = 600
const LABELS = { agents: 'Agents', outputs: 'Outputs', record: 'Record', plan: 'Plan' }
const clampWidth = (value: number) => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(value)))

export function WorkPanel({ chat }: { chat: Chat }) {
  const route = useRoute()
  const selection = route.panel
  const tabs = WORK_TABS.filter((tab) => tab !== 'plan' || !!chat.investigation)
  const tab = selection && tabs.includes(selection.tab) ? selection.tab : 'agents'
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches)
  const [width, setWidth] = useState(() => clampWidth(workPreferences().width ?? DEFAULT_WIDTH))
  const pane = useRef<HTMLElement>(null)
  const tablist = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)
  const autoTurn = useRef('')
  const drag = useRef<{ x: number; width: number } | null>(null)
  const activeLanes = (chat.agents || []).filter((agent) => agent.status === 'running' || agent.status === 'waiting')
  const currentTurn = activeLanes.length >= 2 ? activeLanes[0].turn_id : ''
  useEffect(() => {
    const query = window.matchMedia(WIDE)
    const changed = () => setWide(query.matches)
    query.addEventListener('change', changed)
    return () => query.removeEventListener('change', changed)
  }, [])
  useEffect(() => {
    const first = !initialized.current
    initialized.current = true
    if (selection || route.id !== chat.id) return
    const pref = workPreferences()
    const firstParallel = wide && currentTurn && autoTurn.current !== currentTurn && pref.open === undefined
    if (firstParallel) autoTurn.current = currentTurn
    if (first && pref.open || firstParallel) {
      const tab = firstParallel ? 'agents' : pref.tab || 'agents'
      openWorkPanel(tab === 'plan' && !chat.investigation ? 'agents' : tab, null, true)
    }
  }, [selection, route.id, chat.id, chat.investigation, currentTurn, wide])
  useLayoutEffect(() => {
    const main = pane.current?.closest('main')
    if (!main) return
    main.style.setProperty('--work-panel-width', width + 'px')
    return () => { main.style.removeProperty('--work-panel-width') }
  }, [width, !!selection])
  const returnFocus = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!selection) return
    returnFocus.current = document.activeElement as HTMLElement | null
    if (!wide) tablist.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus()
    const release = () => {
      const old = returnFocus.current
      if (old?.isConnected && old !== document.body) old.focus()
      else document.querySelector<HTMLElement>('[data-work-trigger]')?.focus()
    }
    return release
  }, [!!selection, wide])
  useEffect(() => {
    if (!selection) return
    const keys = (event: globalThis.KeyboardEvent) => {
      const el = pane.current
      if (!el || document.querySelector('.modal-scrim')) return
      if (event.key === 'Escape' && (!wide || el.contains(document.activeElement))) {
        event.preventDefault(); closeWorkPanel(); return
      }
      if (wide || event.key !== 'Tab') return
      const controls = Array.from(el.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input:not([disabled]),select,[tabindex="0"]'))
        .filter((item) => item.tabIndex >= 0 && item.getClientRects().length)
      const first = controls[0], last = controls.at(-1)
      if (event.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) {
        event.preventDefault(); last?.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !el.contains(document.activeElement))) {
        event.preventDefault(); first?.focus()
      }
    }
    document.addEventListener('keydown', keys)
    return () => document.removeEventListener('keydown', keys)
  }, [!!selection, wide])
  if (!selection) return null
  const choose = (key: WorkTab) => openWorkPanel(key)
  const keys = (event: KeyboardEvent, index: number) => {
    const target = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft'
      ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1
    if (target < 0) return
    event.preventDefault()
    choose(tabs[target])
    tablist.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[target]?.focus()
  }
  return <>
    {!wide && <div className="work-panel-scrim" onClick={closeWorkPanel} aria-hidden="true" />}
    <aside className="work-panel" ref={pane} role={wide ? 'complementary' : 'dialog'} aria-modal={!wide || undefined}
      aria-labelledby="work-panel-title" style={{ '--work-panel-width': width + 'px' } as CSSProperties}>
      {wide && <div role="separator" aria-label="Research panel width" aria-orientation="vertical" tabIndex={0}
        aria-valuemin={MIN_WIDTH} aria-valuemax={MAX_WIDTH} aria-valuenow={width} className="work-panel-grip"
        onPointerDown={(e) => { if (e.button !== 0) return; drag.current = { x: e.clientX, width }; e.currentTarget.setPointerCapture(e.pointerId) }}
        onPointerMove={(e) => { if (drag.current) setWidth(clampWidth(drag.current.width + drag.current.x - e.clientX)) }}
        onPointerUp={(e) => { if (drag.current) { const value = clampWidth(drag.current.width + drag.current.x - e.clientX); setWidth(value); rememberWorkPanel({ width: value }) } drag.current = null }}
        onPointerCancel={() => { drag.current = null }}
        onKeyDown={(e) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
          e.preventDefault()
          const value = e.key === 'Home' ? MIN_WIDTH : e.key === 'End' ? MAX_WIDTH : clampWidth(width + (e.key === 'ArrowLeft' ? 1 : -1) * (e.shiftKey ? 64 : 16))
          setWidth(value); rememberWorkPanel({ width: value })
        }} />}
      <div className="work-panel-head">
        <h2 id="work-panel-title">Research notebook</h2>
        <button type="button" className="icon-btn" aria-label="Close work panel" onClick={closeWorkPanel}><CloseIcon size={16} /></button>
      </div>
      <div ref={tablist} role="tablist" aria-label="Research notebook" className="work-panel-tabs">
        {tabs.map((key, i) => <button type="button" key={key} id={'work-tab-' + key}
          role="tab" tabIndex={key === tab ? 0 : -1} aria-selected={key === tab} aria-controls="work-panel-content"
          onKeyDown={(event) => keys(event, i)} onClick={() => choose(key)}>{LABELS[key]}</button>)}
      </div>
      <div role="tabpanel" id="work-panel-content" aria-labelledby={'work-tab-' + tab} tabIndex={0} className="work-panel-scroll" key={tab}>
        {tab === 'agents' && <AgentsTab chat={chat} selected={selection.selected} onSelect={(id) => openWorkPanel('agents', id)} />}
        {tab === 'outputs' && <OutputsTab chat={chat} selected={selection.selected} onSelect={(id) => openWorkPanel('outputs', id)} />}
        {tab === 'record' && <RecordTab chat={chat} onOutput={(id) => openWorkPanel('outputs', id)} />}
        {tab === 'plan' && <PlanTab chat={chat} />}
      </div>
    </aside>
  </>
}
