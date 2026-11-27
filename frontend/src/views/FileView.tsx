// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fileRawUrl, type ViewedFile } from '../api'
import { ErrorNote, Loading } from '../components/common'
import { FileIcon } from '../components/Icons'
import { Markdown } from '../components/Markdown'
import { ago, bytes, displayPath } from '../format'

/**
 * A file of the workspace, opened from the file explorer or an attached file: a PDF in the browser's viewer,
 * Markdown rendered, other text as text, an image as it is. Markdown and text files can be edited in place:
 * click in the text, write, save (Ctrl/Cmd+S). A save is refused if the file changed since it was opened.
 */
export function FileView({ path }: { path: string | null }) {
  const [file, setFile] = useState<ViewedFile | null>(null)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState('')
  const editor = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!path) { setError('No file was given.'); return }
    api.viewFile(path).then((value) => { setFile(value); setDraft(value.text ?? ''); document.title = value.name + ' · Square Harness' },
      (reason: Error) => setError(reason.message))
  }, [path])

  const dirty = !!file && file.text !== undefined && draft !== file.text
  const save = useCallback(async () => {
    if (!file || !path || !file.sha256 || !dirty || saving) return
    setSaving(true); setError('')
    try {
      const result = await api.saveFile(path, draft, file.sha256)
      setFile({ ...file, text: draft, sha256: result.sha256, size: result.size, updated_at: new Date().toISOString() })
      setSaved('Saved')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }, [file, path, draft, dirty, saving])

  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && file?.editable) { event.preventDefault(); void save() }
    }
    const leave = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault() }
    window.addEventListener('keydown', keys)
    window.addEventListener('beforeunload', leave)
    return () => { window.removeEventListener('keydown', keys); window.removeEventListener('beforeunload', leave) }
  }, [save, dirty, file?.editable])
  useEffect(() => { if (dirty) setSaved('') }, [dirty])
  useEffect(() => { if (editing) editor.current?.focus() }, [editing])

  const reload = () => {
    if (!path) return
    setError('')
    api.viewFile(path).then((value) => { setFile(value); setDraft(value.text ?? ''); setSaved('') }, (reason: Error) => setError(reason.message))
  }
  const folder = path && path.includes('/') ? displayPath(path.slice(0, path.lastIndexOf('/'))) : ''
  const conflict = error.includes('changed since it was opened')
  return (
    <div className={'file-view' + (file?.kind === 'pdf' ? ' file-view-pdf' : '')}>
      <header className="file-view-head">
        <FileIcon size={18} />
        <div className="file-view-title">
          <h1>{file?.name || (path ? path.split('/').pop() : 'File')}</h1>
          <p className="muted small">{folder ? folder + ' · ' : ''}{file ? `${bytes(file.size)} · modified ${ago(file.updated_at)}` : ''}
            {file?.encoding && file.encoding !== 'utf-8' ? ` · ${file.encoding}` : ''}</p>
        </div>
        {file?.kind === 'markdown' && <button type="button" className="btn btn-quiet btn-small" onClick={() => setEditing((on) => !on)}>
          {editing ? 'Preview' : 'Edit'}</button>}
        {file?.editable && <>
          <span className="muted small file-view-state" role="status">{saving ? 'Saving…' : dirty ? 'Unsaved changes' : saved}</span>
          <button type="button" className="btn btn-primary btn-small" disabled={!dirty || saving} onClick={() => void save()} title="Save (Ctrl+S)">Save</button>
        </>}
      </header>
      {!conflict && <ErrorNote>{error}</ErrorNote>}
      {conflict && <p className="warning-note" role="alert">This file changed on disk after you opened it, so your changes were not saved. <button type="button" className="link-btn" onClick={reload}>Reload it</button> (this discards them), or copy your text before reloading.</p>}
      {!file && !error && <Loading />}
      {file?.kind === 'pdf' && path && <iframe className="file-view-frame" title={file.name} src={fileRawUrl(path)} />}
      {file?.kind === 'image' && path && <div className="file-view-image"><img src={fileRawUrl(path)} alt={file.name} /></div>}
      {file?.kind === 'markdown' && (editing
        ? <textarea ref={editor} className="file-view-editor" value={draft} spellCheck={false} aria-label={`Edit ${file.name}`}
            onChange={(event) => setDraft(event.target.value)} />
        : <div className="file-view-markdown" title="Click to edit" role="button" tabIndex={0}
            onClick={() => setEditing(true)} onKeyDown={(event) => { if (event.key === 'Enter') setEditing(true) }}>
            {draft.trim() ? <Markdown>{draft}</Markdown> : <p className="muted">This file is empty. Click to write.</p>}
          </div>)}
      {file?.kind === 'text' && <textarea className="file-view-editor" value={draft} spellCheck={false} aria-label={`Edit ${file.name}`}
        onChange={(event) => setDraft(event.target.value)} />}
      {file?.kind === 'too_large' && <p className="muted file-view-note">This file is too large to show here (more than 4 MB of text).</p>}
      {file?.kind === 'unsupported' && <p className="muted file-view-note">{file.note || 'This kind of file cannot be shown here.'} Only PDF, Markdown, text and image files open in the viewer.</p>}
    </div>
  )
}
