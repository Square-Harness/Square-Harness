// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useState } from 'react'
import { api, ApiError, type Project } from '../api'
import { refreshLists, setProjectFilter, useApp } from '../store'
import { go } from '../router'
import { ErrorNote, Modal } from './common'
import { Markdown } from './Markdown'
import { BackIcon, FolderIcon, MoreIcon, NotesIcon, PlusIcon } from './Icons'
import { plural } from '../format'

const SHOWN = 6

/** Projects are folders of conversations. At the top level they are listed above every
    conversation; opening one shows only its conversations, under a card with its notes. */
export function ProjectNav() {
  const app = useApp()
  const [editing, setEditing] = useState<Project | 'new' | null>(null)
  const [all, setAll] = useState(false)
  const selectProject = (id: string) => {
    // The open composer can ask before leaving an unsent message behind.
    if (!window.dispatchEvent(new Event('square-new-project-chat', { cancelable: true }))) return
    setProjectFilter(id); go('free')
  }
  const editor = editing && <ProjectEditor project={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={(project) => {
    refreshLists(); if (editing === 'new') selectProject(project.id); setEditing(null)
  }} />
  const count = (id: string) => app.chats?.filter((chat) => chat.project_id === id).length ?? 0
  const current = app.projects?.find((project) => project.id === app.projectFilter)
  if (current) {
    return <section className="project-current" aria-label={`Project: ${current.name}`}>
      <button type="button" className="side-back" onClick={() => setProjectFilter('all')}><BackIcon size={15} /> All conversations</button>
      <div className="project-card">
        <span className="project-card-mark" aria-hidden="true"><FolderIcon size={17} /></span>
        <div className="project-card-text">
          <strong>{current.name}</strong>
          <span>{plural(count(current.id), 'conversation')}{current.notes.trim() ? '' : ' · no notes yet'}</span>
        </div>
        <button type="button" className="btn btn-small btn-block" onClick={() => setEditing(current)}><NotesIcon size={15} /> Project notes</button>
      </div>
      {editor}
    </section>
  }
  const projects = app.projects || []
  const shown = all ? projects : projects.slice(0, SHOWN)
  return <section className="side-section" aria-label="Projects">
    <div className="side-label"><span>Projects</span>
      <button type="button" className="icon-btn side-label-action" title="New project" aria-label="New project" onClick={() => setEditing('new')}><PlusIcon size={15} /></button>
    </div>
    {app.projects && !projects.length && <button type="button" className="project-empty" onClick={() => setEditing('new')}>
      <FolderIcon size={16} /><span>Group conversations around one problem, with shared notes.</span>
    </button>}
    {shown.map((project) => <div key={project.id} className="project-row">
      <button type="button" className="project-open" title={`Open ${project.name}`} onClick={() => selectProject(project.id)}>
        <FolderIcon size={16} /><span className="project-name">{project.name}</span><span className="project-count">{count(project.id)}</span>
      </button>
      <button type="button" className="icon-btn row-more" title="Project notes" aria-label={`Edit project notes: ${project.name}`} onClick={() => setEditing(project)}><MoreIcon size={17} /></button>
    </div>)}
    {projects.length > SHOWN && <button type="button" className="side-more" aria-expanded={all} onClick={() => setAll(!all)}>
      {all ? 'Show fewer' : `Show ${projects.length - SHOWN} more`}
    </button>}
    {editor}
  </section>
}

export function ProjectSelector({ value, onChange, disabled = false }: { value: string | null; onChange: (id: string | null) => void; disabled?: boolean }) {
  const { projects } = useApp()
  const [editing, setEditing] = useState(false)
  const project = projects?.find((item) => item.id === value)
  return <div className="project-selector">
    <label className="composer-chip" title={disabled ? 'Wait for current work to finish before moving this conversation.' : 'The project this conversation belongs to'}>
      <FolderIcon size={15} /><span className="composer-chip-label">Project</span>
      <select aria-label="Conversation project" value={value || ''} disabled={disabled} onChange={(event) => onChange(event.target.value || null)}>
        <option value="">No project</option>
        {projects?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </label>
    {project && <button type="button" className="btn btn-quiet btn-small" onClick={() => setEditing(true)} aria-label="Project notes"><NotesIcon size={14} /><span className="composer-long-label"> Project notes</span></button>}
    {editing && project && <ProjectEditor project={project} onClose={() => setEditing(false)} onSaved={() => { refreshLists(); setEditing(false) }} />}
  </div>
}

type Draft = { name: string; notes: string }
const draftOf = (project: Project | null): Draft => ({ name: project?.name || '', notes: project?.notes || '' })

/** Notes remain a normal text draft; stale saves never replace what the person typed. */
function ProjectEditor({ project, onClose, onSaved }: { project: Project | null; onClose: () => void; onSaved: (project: Project) => void }) {
  const [baseline, setBaseline] = useState(project)
  const [draft, setDraft] = useState<Draft>(() => draftOf(project))
  const [backup, setBackup] = useState<Draft | null>(null)
  const [latest, setLatest] = useState<Project | null>(null)
  const [stale, setStale] = useState(false)
  const [saving, setSaving] = useState(false)
  const [discard, setDiscard] = useState(false)
  const [error, setError] = useState('')
  const original = draftOf(baseline)
  const dirty = draft.name !== original.name || draft.notes !== original.notes
  useEffect(() => {
    if (!dirty && !backup) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, backup])
  const close = () => { if (dirty || backup) setDiscard(true); else onClose() }
  async function save() {
    if (saving || !draft.name.trim()) return
    setSaving(true); setError('')
    try {
      const result = baseline ? await api.saveProject(baseline.id, { ...draft, name: draft.name.trim(), revision: baseline.revision })
        : await api.createProject(draft.name.trim(), draft.notes)
      onSaved(result.project)
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409 && baseline) {
        setStale(true)
        try { setLatest((await api.project(baseline.id)).project) } catch { /* The draft still stays on screen. */ }
        setError('This project changed elsewhere. Your draft has been kept. Load the latest version before saving again.')
      } else setError((reason as Error).message)
      setSaving(false)
    }
  }
  async function loadLatest() {
    if (!baseline || saving) return
    setSaving(true)
    try {
      const updated = latest || (await api.project(baseline.id)).project
      setBackup(draft); setBaseline(updated); setDraft(draftOf(updated)); setLatest(null); setStale(false); setError('')
    } catch (reason) { setError((reason as Error).message) }
    finally { setSaving(false) }
  }
  return <Modal title={baseline ? 'Project notes' : 'New project'} icon={baseline ? <NotesIcon /> : <FolderIcon />} onClose={saving ? undefined : close} wide>
    <form className="project-editor" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <label className="field"><span className="field-label">Project name</span>
        <input className="text-input" autoComplete="off" maxLength={100} value={draft.name} placeholder="For example, Heat equation observability" onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
      </label>
      <div className="project-notes-columns">
        <label className="project-notes-pane"><span className="project-notes-label"><span className="field-label">Notes</span><span className="muted small">{draft.notes.length.toLocaleString()} / 8,000 characters{dirty ? ' · unsaved' : ''}</span></span><textarea className="project-notes" aria-label="Project notes" rows={18} maxLength={8000} value={draft.notes}
          placeholder={'# Goal\nWhat are you trying to prove or understand?\n\n## Definitions and notation\n\n## Established facts\n\n## Open steps and possible approaches\nLabel assumptions and conjectures clearly.\n\n## References and files'}
          onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></label>
        <div className="project-notes-pane"><span className="project-notes-label"><span className="field-label">Preview</span></span><div className="project-notes-preview" aria-label="Project notes preview">{draft.notes ? <Markdown>{draft.notes}</Markdown> : <p className="muted">Your project notes will appear here as you write.</p>}</div></div>
      </div>
      <p className="muted small">Keep the goal, notation, established facts and open steps here, in Markdown. New turns in this project read these notes; work already running is not changed.</p>
      <ErrorNote>{error}</ErrorNote>
      {stale && <div className="warning-note"><p>Reloading the latest version keeps a recoverable copy of your current draft.</p>
        <button type="button" className="btn btn-small" disabled={saving} onClick={() => void loadLatest()}>Load latest version</button>
      </div>}
      {backup && <p className="draft-backup">Your earlier draft is kept. <button type="button" className="btn btn-quiet btn-small" onClick={() => { setDraft(backup); setBackup(null) }}>Restore my draft</button></p>}
      {discard ? <div className="warning-note" role="alert"><p>Discard your unsaved changes{backup ? ' and the earlier draft' : ''}?</p><div className="modal-actions">
        <button type="button" className="btn" onClick={() => setDiscard(false)}>Keep editing</button>
        <button type="button" className="btn btn-delete" onClick={onClose}>Discard changes</button>
      </div></div> : <div className="modal-actions">
        <button type="button" className="btn" disabled={saving} onClick={close}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={saving || !draft.name.trim() || stale}>{saving ? 'Saving…' : 'Save project'}</button>
      </div>}
    </form>
  </Modal>
}
