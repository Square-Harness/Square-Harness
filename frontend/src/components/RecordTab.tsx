// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useState } from 'react'
import { api, type Chat, type RecordEntry } from '../api'
import { modeLabel } from '../outputs'
import { href } from '../router'
import { useApp, useTick } from '../store'
import { ErrorNote, useLoad } from './common'
import { Markdown } from './Markdown'

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
    if (id === chat.id) return <button className="btn btn-small btn-quiet" type="button"
      onClick={() => onOutput(job)}>Open output</button>
    const source = chats?.find(item => item.id === id)
    return <a className="btn btn-small btn-quiet" href={href(source?.kind || 'free', id, null, null, { tab: 'outputs', selected: job })}>
      Open in {source?.title || 'source conversation'}</a>
  }
  return <section className="work-tab-content">
    <p className="work-tab-context">{project?.name || (chat.project_id ? 'Project record' : 'Conversation record')}</p>
    <div className="work-record-filter">
      <label>Find an entry<input type="search" value={query} onChange={e => setQuery(e.target.value)} /></label>
      {modes.length > 1 && <label>Mode<select value={mode} onChange={e => setMode(e.target.value)}>
        <option value="">All modes</option>{modes.map(m => <option key={m} value={m}>{modeLabel(m)}</option>)}
      </select></label>}
    </div>
    <ErrorNote>{error}</ErrorNote>{error && <button type="button" className="btn btn-small" onClick={reload}>Retry</button>}
    {!data && !error && <p role="status">Loading research record…</p>}
    {data && !visible.length && <p className="empty-note">{entries.length ? 'No entries match this filter.' : 'No evidence has been recorded yet.'}</p>}
    {[...groups].map(([label, items]) => <section key={label} className="work-record-group">
    <h3>{label}</h3><ul className="work-record-list">{items.map(entry => <li key={entry.id}><details>
      <summary><strong>{entry.task.text || entry.summary || modeLabel(entry.mode)}</strong><span>{modeLabel(entry.mode)}</span></summary>
      {entry.direction && <p>{entry.direction.replace(/_/g, ' ')}</p>}
      {entry.qualifiers.length > 0 && <p className="muted small">{entry.qualifiers.join(' · ')}</p>}
      {entry.summary && <Markdown>{entry.summary}</Markdown>}
      <code className="work-record-id">{entry.id}</code>{link(entry)}
    </details></li>)}</ul></section>)}
  </section>
}
