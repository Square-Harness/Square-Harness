// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { api, fileDownloadUrl, fileRawUrl, PDF_FIT, type ViewedFile } from '../api'
import { CodeEditor } from '../components/CodeEditor'
import { ErrorNote, Loading } from '../components/common'
import { CloseIcon, DownloadIcon, FileIcon } from '../components/Icons'
import { Markdown } from '../components/Markdown'
import { PanelGrip, usePanelWidth, usePublishedWidth, useWide } from '../components/PanelResize'
import { ago, bytes, displayPath } from '../format'
import { filePanelWidth, rememberFilePanelWidth } from '../panels'
import { closeFile, leaveFile, setUnsavedFile } from '../router'

// Unsaved edits of a file the viewer left without a decision (the browser's Back button): reopening the file
// brings them back while the file on disk is unchanged.
const drafts = new Map<string, { sha256: string; text: string }>()

/** The viewer page (#/file?path=…), for a file opened where no conversation is open; the bar above leads back. */
export function FileView({ path }: { path: string | null }) {
  if (!path) return <div className="file-view"><ErrorNote>No file was given.</ErrorNote></div>
  return <FileViewer path={path} />
}

/** A workspace file in the viewer beside a conversation. */
export function FilePanel({ path }: { path: string }) {
  return <ViewerPanel label={'File ' + (path.split('/').pop() || path)}><FileViewer path={path} onClose={closeFile} /></ViewerPanel>
}

/**
 * The viewer beside a conversation, in the notebook's place (the notebook comes back when it closes): a workspace
 * file, or a document an agent produced (ResultPanel). It is resized like the notebook, up to 75% of the window,
 * so that the reader can write a message while reading.
 */
export function ViewerPanel({ label, children }: { label: string; children: ReactNode }) {
  const wide = useWide()
  const [width, setWidth] = usePanelWidth(() => filePanelWidth() ?? Math.round(window.innerWidth * 0.45))
  const [dragging, setDragging] = useState(false)
  const pane = useRef<HTMLElement>(null)
  usePublishedWidth(pane, width)
  // Covering the page on a narrow screen, it closes with Escape like the notebook.
  useEffect(() => {
    if (wide) return
    const keys = (event: KeyboardEvent) => { if (event.key === 'Escape' && !document.querySelector('.modal-scrim')) closeFile() }
    document.addEventListener('keydown', keys)
    return () => document.removeEventListener('keydown', keys)
  }, [wide])
  return <>
    {!wide && <div className="work-panel-scrim" onClick={closeFile} aria-hidden="true" />}
    <aside ref={pane} className={'work-panel file-panel' + (dragging ? ' file-panel-dragging' : '')}
      role={wide ? 'complementary' : 'dialog'} aria-modal={!wide || undefined} aria-label={label}
      style={{ '--work-panel-width': width + 'px' } as CSSProperties}>
      {wide && <PanelGrip label="File viewer width" width={width} onWidth={setWidth} onCommit={rememberFilePanelWidth} onDragging={setDragging} />}
      {children}
    </aside>
  </>
}

/** The viewer panel's header, for a file or a document: its name and facts, its tools (Edit, Save), then Download
 * and Close on the same line, so that the document below keeps the height. */
export function ViewerHead({ name, title, meta, tools, download, downloadName, downloadTitle, onClose }: {
  name: string; title?: string; meta: ReactNode; tools?: ReactNode
  download?: string; downloadName?: string; downloadTitle?: string; onClose: () => void
}) {
  return <header className="work-panel-head file-panel-head">
    <span className="notebook-header-mark" aria-hidden="true"><FileIcon size={17} /></span>
    <div className="notebook-heading">
      <h2 title={title || name}>{name}</h2>
      <span className="notebook-head-status">{meta}</span>
    </div>
    {tools}
    {download && <a className="icon-btn" href={download} download={downloadName || true} aria-label={'Download ' + name}
      title={downloadTitle || 'Download'}><DownloadIcon size={18} /></a>}
    <button type="button" className="icon-btn" aria-label="Close the viewer" title="Close the viewer" onClick={onClose}><CloseIcon size={18} /></button>
  </header>
}

/**
 * A file of the workspace: a PDF in the browser's viewer, fitted to the width; Markdown rendered; other text as
 * text, coloured for TeX and code; an image as it is. Markdown and text files are edited in place: click in the
 * text, write, save (Ctrl/Cmd+S). A save is refused if the file changed since it was opened. While changes are
 * unsaved, whatever replaces the viewer (another file, the notebook, a link) asks first (router.leaveFile).
 * `onClose` makes it the panel's content, with the notebook's header; without it, the page's.
 */
export function FileViewer({ path, onClose }: { path: string; onClose?: () => void }) {
  const [file, setFile] = useState<ViewedFile | null>(null)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState('')
  const panel = !!onClose

  useEffect(() => {
    api.viewFile(path).then((value) => {
      const kept = drafts.get(path)
      setFile(value)
      setDraft(kept && kept.sha256 === value.sha256 ? kept.text : value.text ?? '')
      if (!panel) document.title = value.name + ' · Square Harness'
    }, (reason: Error) => setError(reason.message))
  }, [path, panel])

  const dirty = !!file && file.text !== undefined && draft !== file.text
  const save = useCallback(async () => {
    if (!file || !file.sha256 || !dirty || saving) return
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
  // Unsaved changes: kept for a reopening, announced to whatever would replace the viewer.
  useEffect(() => {
    if (!file?.sha256) return
    if (dirty) drafts.set(path, { sha256: file.sha256, text: draft })
    else drafts.delete(path)
  }, [path, file?.sha256, draft, dirty])
  useEffect(() => {
    if (!dirty) return
    setUnsavedFile({ path, discard: () => { drafts.delete(path) } })
    return () => setUnsavedFile(null)
  }, [dirty, path])
  // Links (Back to conversation, the sidebar, a job) replace the viewer in this window: unsaved changes ask first.
  useEffect(() => {
    if (!dirty) return
    const guard = (event: MouseEvent) => {
      const link = event.target instanceof Element ? event.target.closest('a[href^="#"]') : null
      if (link && !leaveFile()) { event.preventDefault(); event.stopPropagation() }
    }
    document.addEventListener('click', guard, true)
    return () => document.removeEventListener('click', guard, true)
  }, [dirty])

  const reload = () => {
    setError('')
    drafts.delete(path)
    api.viewFile(path).then((value) => { setFile(value); setDraft(value.text ?? ''); setSaved('') }, (reason: Error) => setError(reason.message))
  }
  const name = file?.name || path.split('/').pop() || 'File'
  const folder = path.includes('/') ? displayPath(path.slice(0, path.lastIndexOf('/'))) : ''
  const meta = (folder ? folder + ' · ' : '') + (file ? `${bytes(file.size)} · modified ${ago(file.updated_at)}` : '')
    + (file?.encoding && file.encoding !== 'utf-8' ? ` · ${file.encoding}` : '')
  const conflict = error.includes('changed since it was opened')
  const state = saving ? 'Saving…' : dirty ? 'Unsaved changes' : saved
  const actions = <>
    {file?.kind === 'markdown' && <button type="button" className="btn btn-quiet btn-small file-view-mode" onClick={() => setEditing((on) => !on)}>
      {editing ? 'Preview' : 'Edit'}</button>}
    {file && <a className="btn btn-quiet btn-small" href={fileDownloadUrl(path)} download={file.name}
      title={dirty ? 'Download the file as last saved; save first to include your changes' : 'Download this file'}>
      <DownloadIcon size={14} /> Download</a>}
    {file?.editable && <>
      <span className="muted small file-view-state" role="status">{state}</span>
      <button type="button" className="btn btn-primary btn-small" disabled={!dirty || saving} onClick={() => void save()} title="Save (Ctrl+S)">Save</button>
    </>}
  </>
  return (
    <>
      {panel && <ViewerHead name={name} title={displayPath(path)} onClose={onClose!}
        meta={<>{meta}{state && <> · <span className="file-view-state-inline">{state}</span></>}<span className="sr-only" role="status">{state}</span></>}
        tools={<>
          {file?.kind === 'markdown' && <button type="button" className="btn btn-quiet btn-small" onClick={() => setEditing((on) => !on)}>{editing ? 'Preview' : 'Edit'}</button>}
          {file?.editable && <button type="button" className="btn btn-primary btn-small" disabled={!dirty || saving} onClick={() => void save()} title="Save (Ctrl+S)">Save</button>}
        </>}
        download={file ? fileDownloadUrl(path) : undefined} downloadName={file?.name}
        downloadTitle={dirty ? 'Download the file as last saved; save first to include your changes' : 'Download this file'} />}
      <div className={'file-view' + (file?.kind === 'pdf' ? ' file-view-pdf' : '')}>
        {!panel && (
          <header className="file-view-head">
            <FileIcon size={18} />
            <div className="file-view-title"><h1>{name}</h1><p className="muted small">{meta}</p></div>
            {actions}
          </header>
        )}
        {!conflict && <ErrorNote>{error}</ErrorNote>}
        {conflict && <p className="warning-note" role="alert">This file changed on disk after you opened it, so your changes were not saved. <button type="button" className="link-btn" onClick={reload}>Reload it</button> (this discards them), or copy your text before reloading.</p>}
        {!file && !error && <Loading />}
        {file?.kind === 'pdf' && <iframe className="file-view-frame" title={file.name} src={fileRawUrl(path) + PDF_FIT} />}
        {file?.kind === 'image' && <div className="file-view-image"><img src={fileRawUrl(path)} alt={file.name} /></div>}
        {file?.kind === 'markdown' && (editing
          ? <CodeEditor name={file.name} value={draft} onChange={setDraft} autoFocus />
          : <div className="file-view-markdown" title="Click to edit" role="button" tabIndex={0}
              onClick={() => setEditing(true)} onKeyDown={(event) => { if (event.key === 'Enter') setEditing(true) }}>
              {draft.trim() ? <Markdown>{draft}</Markdown> : <p className="muted">This file is empty. Click to write.</p>}
            </div>)}
        {file?.kind === 'text' && <CodeEditor name={file.name} value={draft} onChange={setDraft} />}
        {file?.kind === 'too_large' && <p className="muted file-view-note">{file.note || 'This file is too large to open here.'}</p>}
        {file?.kind === 'unsupported' && <p className="muted file-view-note">{file.note || 'This kind of file cannot be shown here.'} Only PDF, Markdown, text and image files open in the viewer.</p>}
      </div>
    </>
  )
}
