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
import { CloseIcon, NotebookIcon, NotesIcon, StackIcon, InvestigationIcon, FileIcon } from './Icons'
import { Glyph } from './Glyph'
import { Square } from './Square'
import './WorkPanel.css'

const WIDE = '(min-width: 1200px)'
const DEFAULT_WIDTH = 380
const MIN_WIDTH = 304
const MAX_WIDTH = 600
const LABELS = { agents: 'Activity', outputs: 'Results', record: 'Evidence', plan: 'Plan' }
const TAB_ICONS = { agents: StackIcon, outputs: FileIcon, record: NotesIcon, plan: InvestigationIcon }
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
  const running = activeLanes.filter(lane => lane.status === 'running').length
  const waiting = activeLanes.length - running
  const activity = [running ? running + ' working' : '', waiting ? waiting + ' waiting' : ''].filter(Boolean).join(' · ')
  const investigation = chat.investigation
  const lastTab = workPreferences().tab
  const defaultTab = lastTab && tabs.includes(lastTab) ? lastTab
    : activeLanes.length ? 'agents' : investigation ? 'plan' : (chat.agents || []).length ? 'outputs' : 'record'
  const announcement = activeLanes.map(agent => (agent.mode === 'prove' ? 'Proof' : agent.mode.replaceAll('_', ' ')) + ' ' + agent.status).join('. ')
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
  if (!selection) return <nav className="notebook-rail" aria-label="Research notebook">
    <button className="notebook-pull" type="button" data-work-trigger aria-label="Open research notebook" aria-expanded={false}
      title="Open research notebook" onClick={() => openWorkPanel(defaultTab)}>
      <span className="notebook-pull-cover"><NotebookIcon size={21} /></span><span>Notebook</span>
    </button>
    <div className="notebook-index">{tabs.map(key => {
      const Icon = TAB_ICONS[key]
      return <button key={key} type="button" onClick={() => openWorkPanel(key)} aria-label={'Open ' + LABELS[key].toLowerCase()}>
        <span className="notebook-index-icon"><Icon size={18} />{key === 'agents' && !!running && <i />}</span>
        {LABELS[key]}
      </button>
    })}</div>
    <span aria-live="polite" className="sr-only">{announcement}</span>
  </nav>
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
      <header className={'work-panel-head' + (investigation ? ' work-panel-investigation' : '')}>
        <span className="notebook-header-mark" aria-hidden="true">{investigation ? <Glyph mode="investigate" /> : <NotebookIcon size={22} />}</span>
        <div className="notebook-heading"><h2 id="work-panel-title">Research notebook</h2>
          {activity && <span className="notebook-head-status"><Square variant={running ? 'running' : 'waiting'} size={13} /> {activity}</span>}
        </div>
        <span aria-live="polite" className="sr-only">{announcement}</span>
        <button type="button" className="icon-btn" aria-label="Close research notebook" title="Close research notebook" onClick={closeWorkPanel}><CloseIcon size={18} /></button>
      </header>
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
