// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useState } from 'react'
import { api, type Chat, type RecordEntry } from '../api'
import { researchModeName } from '../outputs'
import { plural } from '../format'
import { SearchIcon } from './Icons'
import { Square, type Variant } from './Square'
import { href } from '../router'
import { useApp, useTick } from '../store'
import { ErrorNote, useLoad } from './common'
import { Inline } from './Markdown'

const evidenceLabels: Record<string, [string, Variant]> = {
  'model-reviewed proof': ['Model-reviewed proofs', 'reviewed'],
  'model-reviewed refutation': ['Model-reviewed refutations', 'refuted'],
  'lead (model-written)': ['Exploratory leads', 'ready'],
  'source read': ['Sources read', 'complete'],
  'ran': ['Numerical observations', 'complete'],
  'not reviewed': ['Awaiting review', 'waiting'],
  'review uncertain': ['Uncertain reviews', 'uncertain'],
  'objections found': ['Open objections', 'gap'],
  'located, place unconfirmed': ['Source located, passage unconfirmed', 'uncertain'],
}

const qualifierLabels: Record<string, string> = {
  'worded by a model brief': 'Question formulated by the assistant',
  'relative to pinned context': 'Uses the supplied assumptions',
  'bounded record': 'Partial experiment record',
  'legacy format': 'Earlier record format',
  'kept copy': 'Archived copy',
}

export function RecordTab({ chat, onOutput }: { chat: Chat; onOutput: (id: string) => void }) {
  const { projects, chats } = useApp()
  const tick = useTick(chat.id)
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('')
  const { data, error, reload } = useLoad(() => api.index({ chat: chat.id }), [chat.id, chat.project_id, tick])
  const project = projects?.find(item => item.id === chat.project_id)
  const entries = data?.entries || []
  const needle = query.toLocaleLowerCase().trim()
  const visible = entries.filter(entry => (!mode || mode === entry.mode) && (!needle ||
    [entry.id, entry.task.text, entry.summary, entry.status, ...entry.qualifiers].join(' ').toLocaleLowerCase().includes(needle)))
  const modes = [...new Set(entries.map(entry => entry.mode))].sort()
  const groups = new Map<string, RecordEntry[]>()
  for (const entry of visible) {
    const group = groups.get(entry.status)
    if (group) group.push(entry)
    else groups.set(entry.status, [entry])
  }
  function link(entry: RecordEntry) {
    const job = entry.job_id
    const id = entry.chat_id
    if (!id || !job) return null
    if (id === chat.id) return <button className="work-text-link" type="button"
      onClick={() => onOutput(job)}>Read the result <span aria-hidden="true">→</span></button>
    const source = chats?.find(item => item.id === id)
    return <a className="work-text-link" href={href(source?.kind || 'free', id, null, null, { tab: 'outputs', selected: job })}>
      Open in {source?.title || 'source conversation'}</a>
  }
  return <section className="work-tab-content work-evidence">
    <div className="work-section-head"><h3>{chat.project_id ? 'Project evidence' : 'Conversation evidence'}</h3>
      {data && <small>{plural(entries.length, 'entry', 'entries')}</small>}</div>
    {project && <p className="work-project-name">{project.name}</p>}
    {(entries.length > 1 || query || mode) && <div className="work-record-filter">
      <label className="work-search"><SearchIcon size={16} />
        <input className="text-input" aria-label="Search evidence" placeholder="Search evidence…" type="search"
          value={query} onChange={e => setQuery(e.target.value)} /></label>
      {modes.length > 1 && <select aria-label="Filter by method" value={mode} onChange={e => setMode(e.target.value)}>
        <option value="">All methods</option>{modes.map(m => <option key={m} value={m}>{researchModeName(m)}</option>)}
      </select>}
    </div>}
    <ErrorNote>{error}</ErrorNote>{error && <button type="button" className="btn btn-small" onClick={reload}>Retry</button>}
    {!data && !error && <p role="status">Loading evidence…</p>}
    {data && !visible.length && <p className="empty-note">{entries.length ? 'No entries match this filter.' : 'Findings and sources will appear here as work is saved.'}</p>}
    {[...groups].map(([label, items]) => {
      const [group, mark] = evidenceLabels[label] || [label.charAt(0).toUpperCase() + label.slice(1), 'partial' as const]
      return <section key={label} className="work-record-group">
        <h4><Square variant={mark} size={14} />{group}</h4>
        <ul className="work-record-list">{items.map(entry => {
          const title = entry.task.text || entry.summary || researchModeName(entry.mode)
          return <li key={entry.id}><details className="work-fold">
            <summary><span className="work-entry-kind">{researchModeName(entry.mode)}</span><Inline>{title}</Inline></summary>
            <div className="work-entry-content">
              {entry.summary && entry.summary !== title && <blockquote className="work-excerpt">
                <Inline>{entry.kind === 'lead' ? entry.summary.replace(/^#{1,6}\s+Findings\s+/i, '') : entry.summary}</Inline>
              </blockquote>}
              {entry.direction && !label.includes('proof') && !label.includes('refutation') &&
                <p className="work-entry-note">Assessment · {entry.direction.replace(/_/g, ' ')}</p>}
              {entry.qualifiers.length > 0 && <p className="work-entry-note">{entry.qualifiers.map(item => qualifierLabels[item] || item).join(' · ')}</p>}
              {link(entry)}
            </div>
          </details></li>
        })}</ul>
      </section>
    })}
  </section>
}
