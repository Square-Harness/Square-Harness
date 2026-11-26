// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { api, type WorkspaceFile } from '../api'
import { bytes, displayPath } from '../format'
import { useApp, useTick } from '../store'
import { BackIcon, ChevronIcon, FileIcon, FolderIcon } from './Icons'
import { ErrorNote, Loading, Modal } from './common'
import { isImagePath } from './ImageAttachments'

/** A file's name and extension, the extension kept whole for archives (.tar.gz). */
export function splitName(path: string): { folder: string; name: string; extension: string } {
  const at = path.lastIndexOf('/')
  const folder = at === -1 ? '' : path.slice(0, at)
  const base = path.slice(at + 1)
  const archive = /\.tar\.gz$/i.exec(base)
  const dot = archive ? archive.index : base.lastIndexOf('.')
  return dot > 0 ? { folder, name: base.slice(0, dot), extension: base.slice(dot + 1).toLowerCase() }
    : { folder, name: base, extension: '' }
}

/** The workspace's files (the ones the model can be given: text, PDF and images), refreshed when files change. */
function useWorkspaceFiles() {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [error, setError] = useState('')
  const changed = useTick('files')  // an upload, or another folder opened
  useEffect(() => {
    let live = true
    setError('')
    api.files().then((result) => { if (live) { setFiles(result.files); setTruncated(!!result.truncated) } },
      (reason: Error) => { if (live) setError(reason.message) })
    return () => { live = false }
  }, [changed])
  return { files, truncated, error }
}

/**
 * The files of the open workspace, grouped by folder: name, extension and size.
 *
 * A click selects a file (or toggles it when `multiple`). Opening a file is not decided yet: pass `onOpen`
 * and a double click (or Enter) on a file calls it, for instance to download or preview that file.
 */
export function FileExplorer({ selected, onSelect, multiple = false, onOpen, unavailable, badge }: {
  selected: string[]
  onSelect: (paths: string[]) => void
  multiple?: boolean
  onOpen?: (file: WorkspaceFile) => void
  /** Why a file cannot be selected here (for instance an image the model cannot read), or nothing. */
  unavailable?: (file: WorkspaceFile) => string | undefined
  /** A short label shown after a file's size (for instance "Attached"). */
  badge?: (file: WorkspaceFile) => string | undefined
}) {
  const { status } = useApp()
  const { files, truncated, error } = useWorkspaceFiles()
  const [filter, setFilter] = useState('')
  const groups = useMemo(() => {
    const words = filter.toLowerCase().split(/\s+/).filter(Boolean)
    const byFolder = new Map<string, WorkspaceFile[]>()
    for (const file of files || []) {
      const path = file.path.toLowerCase()
      if (words.some((word) => !path.includes(word))) continue
      const folder = splitName(file.path).folder
      byFolder.set(folder, [...(byFolder.get(folder) || []), file])
    }
    return [...byFolder.entries()].sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
  }, [files, filter])
  const shown = groups.reduce((total, [, items]) => total + items.length, 0)
  const root = status?.workspace.split('/').filter(Boolean).pop() || 'Workspace'

  const choose = (file: WorkspaceFile) => {
    if (unavailable?.(file)) return
    const on = selected.includes(file.path)
    onSelect(multiple ? (on ? selected.filter((path) => path !== file.path) : [...selected, file.path]) : (on ? [] : [file.path]))
  }
  const keys = (event: KeyboardEvent, file: WorkspaceFile) => {
    if (event.key === 'Enter' && onOpen) { event.preventDefault(); onOpen(file) }
    else if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); choose(file) }
  }
  return (
    <div className="ws-file-explorer">
      <input className="text-input ws-file-filter" type="search" value={filter} onChange={(event) => setFilter(event.target.value)}
        placeholder="Filter by name, folder or extension" aria-label="Filter files" />
      <ErrorNote>{error}</ErrorNote>
      {!files && !error && <Loading />}
      {truncated && <p className="warning-note" role="status">This folder has more files than can be listed. Open a smaller folder to see the rest.</p>}
      {files && !files.length && <p className="muted ws-file-empty">No files yet. Drop files on a conversation, or attach them from your device, to add them to {root}.</p>}
      {files && files.length > 0 && !shown && <p className="muted ws-file-empty">No file matches “{filter}”.</p>}
      {shown > 0 && <div className="ws-file-list" role="listbox" aria-label={`Files in ${root}`} aria-multiselectable={multiple || undefined}>
        {groups.map(([folder, items]) => (
          <div key={folder || '.'} className="ws-file-group" role="group" aria-label={folder ? displayPath(folder) : root}>
            <div className="ws-file-group-head" aria-hidden="true"><FolderIcon size={15} /><span>{folder ? displayPath(folder) : root}</span></div>
            {items.map((file) => {
              const part = splitName(file.path)
              const reason = unavailable?.(file)
              const on = selected.includes(file.path)
              const label = badge?.(file)
              return (
                <div key={file.path} role="option" tabIndex={reason ? -1 : 0} aria-selected={on} aria-disabled={reason ? true : undefined}
                  className={'ws-file-row' + (on ? ' ws-file-row-selected' : '') + (reason ? ' ws-file-row-unavailable' : '')}
                  title={reason || file.path} onClick={() => choose(file)} onKeyDown={(event) => keys(event, file)}
                  onDoubleClick={onOpen ? () => onOpen(file) : undefined}>
                  <FileIcon size={15} />
                  <span className="ws-file-name">{part.name}</span>
                  {part.extension && <span className={'ws-file-ext ws-file-ext-' + file.kind}>.{part.extension}</span>}
                  {label && <span className="pill">{label}</span>}
                  <span className="ws-file-size">{bytes(file.size)}</span>
                </div>
              )
            })}
          </div>
        ))}
      </div>}
    </div>
  )
}

/** "See my files": what the open workspace holds, from the folder browser. */
export function FilesDialog({ onClose, onBack }: { onClose: () => void; onBack?: () => void }) {
  const { status } = useApp()
  const [selected, setSelected] = useState<string[]>([])
  const folder = status?.workspace.split('/').filter(Boolean).pop() || 'the workspace'
  return (
    <Modal title={`Files in ${folder}`} icon={<FileIcon />} onClose={onClose} wide>
      <p className="modal-lede">The files saved in the open folder that the model can be given: text, PDF and images. Files you attach or drop are saved here.</p>
      <FileExplorer selected={selected} onSelect={setSelected} />
      {selected.length === 1 && <p className="muted small ws-file-selected-path">{displayPath(selected[0])}</p>}
      <div className="modal-actions">
        {onBack && <button type="button" className="btn" onClick={onBack}><BackIcon size={15} /> Back to folders</button>}
        <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
      </div>
    </Modal>
  )
}

/** Choose files already in the workspace and attach them to the next message. */
export function AttachFromWorkspace({ attached, onAttach, onClose }: {
  attached: string[]; onAttach: (paths: string[]) => void; onClose: () => void
}) {
  const { status } = useApp()
  const [selected, setSelected] = useState<string[]>([])
  const unavailable = (file: WorkspaceFile) => attached.includes(file.path) ? 'Already attached to your next message'
    : isImagePath(file.path) && !status?.image_support ? 'This model cannot read images' : undefined
  return (
    <Modal title="Attach files from the workspace" icon={<FileIcon />} onClose={onClose} wide>
      <p className="modal-lede">Click the files to attach to your next message. They stay where they are in the folder.</p>
      <FileExplorer selected={selected} onSelect={setSelected} multiple unavailable={unavailable}
        badge={(file) => attached.includes(file.path) ? 'Attached' : undefined} />
      <div className="modal-actions">
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={!selected.length} onClick={() => { onAttach(selected); onClose() }}>
          {selected.length > 1 ? `Attach ${selected.length} files` : 'Attach'}
        </button>
      </div>
    </Modal>
  )
}

/** The composer's "Attach files" button: a menu to attach from this device (an upload) or from the workspace. */
export function AttachMenu({ label, disabled, attached, onAttach }: {
  label: string; disabled?: boolean; attached: string[]; onAttach: (paths: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const menuId = useId()
  useEffect(() => {
    if (!open) return
    const outside = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false) }
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', outside)
    document.addEventListener('keydown', escape)
    box.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape) }
  }, [open])
  const pick = (from: 'device' | 'workspace') => {
    setOpen(false)
    if (from === 'device') window.dispatchEvent(new Event('square-attach-files'))
    else setChoosing(true)
  }
  const arrows = (event: KeyboardEvent) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const items = Array.from(box.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') || [])
    const at = items.indexOf(document.activeElement as HTMLButtonElement)
    items[(at + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus()
  }
  return (
    <div className="attach-menu" ref={box}>
      <button type="button" className="btn btn-quiet btn-small" disabled={disabled} aria-haspopup="menu" aria-expanded={open}
        aria-controls={open ? menuId : undefined} aria-label={label} onClick={() => setOpen((on) => !on)}>
        <FileIcon size={14} /><span className="composer-long-label"> {label}</span><span className="attach-chevron"><ChevronIcon size={13} /></span>
      </button>
      {open && <div className="attach-menu-list" id={menuId} role="menu" aria-label={label} onKeyDown={arrows}>
        <button type="button" role="menuitem" className="attach-menu-item" onClick={() => pick('device')}>
          <FileIcon size={15} /><span><strong>From my device</strong><span className="attach-menu-hint">Upload files; they are saved in the workspace</span></span>
        </button>
        <button type="button" role="menuitem" className="attach-menu-item" onClick={() => pick('workspace')}>
          <FolderIcon size={15} /><span><strong>From the workspace</strong><span className="attach-menu-hint">Files already uploaded to this folder</span></span>
        </button>
      </div>}
      {choosing && <AttachFromWorkspace attached={attached} onAttach={onAttach} onClose={() => setChoosing(false)} />}
    </div>
  )
}
