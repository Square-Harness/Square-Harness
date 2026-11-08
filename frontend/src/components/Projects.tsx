import { useEffect, useState } from 'react'
import { api, ApiError, type Project } from '../api'
import { refreshLists, setProjectFilter, useApp } from '../store'
import { go } from '../router'
import { ErrorNote, Modal } from './common'
import { Markdown } from './Markdown'
import { PlusIcon } from './Icons'

export function ProjectList() {
  const app = useApp()
  const [editing, setEditing] = useState<Project | 'new' | null>(null)
  const selectProject = (id: string) => {
    // The open composer can ask before leaving an unsent message behind.
    if (!window.dispatchEvent(new Event('square-new-project-chat', { cancelable: true }))) return
    setProjectFilter(id); go('free')
  }
  return <section className="projects" aria-label="Projects">
    <div className="project-list-heading"><strong>Projects</strong>
      <button type="button" className="icon-btn" title="New project" aria-label="New project" onClick={() => setEditing('new')}><PlusIcon size={15} /></button>
    </div>
    <button type="button" className={'project-filter' + (app.projectFilter === 'all' ? ' project-selected' : '')}
      aria-pressed={app.projectFilter === 'all'} onClick={() => setProjectFilter('all')}>All conversations</button>
    {app.projects?.map((project) => <div key={project.id} className={'project-row' + (app.projectFilter === project.id ? ' project-selected' : '')}>
      <button type="button" className="project-filter" aria-pressed={app.projectFilter === project.id} onClick={() => selectProject(project.id)}>
        <span className="folder-glyph" aria-hidden="true" /><span>{project.name}</span>
      </button>
      <button type="button" className="icon-btn project-edit" title={`Edit ${project.name}`} aria-label={`Edit project notes: ${project.name}`} onClick={() => setEditing(project)}>···</button>
    </div>)}
    {editing && <ProjectEditor project={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={(project) => {
      refreshLists(); if (editing === 'new') selectProject(project.id); setEditing(null)
    }} />}
  </section>
}

export function ProjectSelector({ value, onChange, disabled = false }: { value: string | null; onChange: (id: string | null) => void; disabled?: boolean }) {
  const { projects } = useApp()
  const [editing, setEditing] = useState(false)
  const project = projects?.find((item) => item.id === value)
  return <div className="project-selector">
    <label><span>Project</span><select aria-label="Conversation project" value={value || ''} disabled={disabled} title={disabled ? 'Wait for current work to finish before moving this conversation.' : undefined} onChange={(event) => onChange(event.target.value || null)}>
      <option value="">Without a project</option>
      {projects?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    {project && <button type="button" className="btn btn-quiet btn-small" onClick={() => setEditing(true)}>Project notes</button>}
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
  return <Modal title={baseline ? 'Project notes' : 'New project'} onClose={saving ? undefined : close} wide>
    <form className="project-editor" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <label className="field"><span className="field-label">Project name</span>
        <input className="text-input" autoComplete="off" maxLength={100} value={draft.name} placeholder="For example, Heat equation observability" onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
      </label>
      <p className="muted small">Keep the goal, notation, established facts and open steps here. These notes give context to new turns; they do not change work already running.</p>
      <div className="project-editor-toolbar"><strong className="field-label">Project notes</strong><span className="muted small">{draft.notes.length.toLocaleString()} / 8,000 characters{dirty ? ' · Unsaved changes' : ''}</span></div>
      <div className="project-notes-columns">
        <label className="project-notes-pane"><span className="field-label">Instructions and context</span><textarea className="project-notes" aria-label="Project notes" rows={18} maxLength={8000} value={draft.notes}
          placeholder={'# Goal\nWhat are you trying to prove or understand?\n\n## Definitions and notation\n\n## Established facts\n\n## Open steps and possible approaches\nLabel assumptions and conjectures clearly.\n\n## References and files'}
          onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></label>
        <div className="project-notes-pane"><span className="field-label">Live preview</span><div className="project-notes-preview" aria-label="Project notes preview">{draft.notes ? <Markdown>{draft.notes}</Markdown> : <p className="muted">Your project notes will appear here as you write.</p>}</div></div>
      </div>
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
