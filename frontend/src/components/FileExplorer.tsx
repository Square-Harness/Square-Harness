// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { api, fileDownloadUrl, type FolderFiles, type WorkspaceFile, type WorkspaceFolder } from '../api'
import { ago, bytes, count, displayPath } from '../format'
import { useApp, useTick } from '../store'
import { openFile, useRoute } from '../router'
import { BackIcon, ChevronIcon, DownloadIcon, ExternalIcon, FileIcon, FolderIcon } from './Icons'
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

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
const MOD = MAC ? '⌘' : 'Ctrl'

type Sort = { key: 'name' | 'size' | 'updated'; descending: boolean }
type Row = { type: 'folder'; folder: WorkspaceFolder } | { type: 'file'; file: WorkspaceFile }

/** One folder of the workspace, refreshed when files change (an upload, another folder opened). */
function useFolder(path: string) {
  const [listing, setListing] = useState<FolderFiles | null>(null)
  const [error, setError] = useState('')
  const changed = useTick('files')
  useEffect(() => {
    let live = true
    setError('')
    api.browse(path).then((result) => { if (live) setListing(result) }, (reason: Error) => { if (live) setError(reason.message) })
    return () => { live = false }
  }, [path, changed])
  return { listing: listing && listing.path === path ? listing : null, error }
}

/** Every listed file below the interface root (paths relative to it), read once a search starts. */
function useAllFiles(enabled: boolean) {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [truncated, setTruncated] = useState(false)
  const changed = useTick('files')
  useEffect(() => { setFiles(null) }, [changed])
  useEffect(() => {
    if (!enabled || files) return
    let live = true
    api.filesEverywhere().then((result) => { if (live) { setFiles(result.files); setTruncated(!!result.truncated) } }, () => { if (live) setFiles([]) })
    return () => { live = false }
  }, [enabled, files])
  return { files, truncated }
}

function compare(sort: Sort) {
  const name = (file: WorkspaceFile) => file.path.slice(file.path.lastIndexOf('/') + 1)
  return (a: WorkspaceFile, b: WorkspaceFile) => {
    const order = sort.key === 'size' ? a.size - b.size
      : sort.key === 'updated' ? Date.parse(a.updated_at || '') - Date.parse(b.updated_at || '')
      : 0
    const result = order || name(a).localeCompare(name(b), undefined, { numeric: true, sensitivity: 'base' })
    return sort.descending ? -result : result
  }
}

/**
 * The folders below the interface root and their files, browsed like a desktop file manager. It opens in the
 * open folder (the workspace), where the user's files most likely are, and can go up to the root and into any
 * other folder. Paths are relative to the interface root.
 *
 * Folders open on a double click (or Enter); the breadcrumb and Up go back. A click selects a file; with
 * `multiple`, Ctrl/⌘-click adds or removes one file and Shift-click selects the range from the last file
 * clicked. Typing in the search box searches every folder. Hovering a file shows "Download" and "View file", which
 * opens it in the viewer beside the conversation (`onView`, by default openFile). A double click (or Enter) on a
 * file calls `onOpen`; both dialogs attach the file with it.
 */
export function FileExplorer({ selected, onSelect, multiple = false, onOpen, onView, unavailable, badge, startFolder, folderNote }: {
  selected: string[]
  onSelect: (paths: string[]) => void
  multiple?: boolean
  onOpen?: (file: WorkspaceFile) => void
  /** View file: the dialogs close first. */
  onView?: (file: WorkspaceFile) => void
  /** Why a file cannot be selected here (for instance an image the model cannot read), or nothing. */
  unavailable?: (file: WorkspaceFile) => string | undefined
  /** A short label shown after a file's name (for instance "Attached"). */
  badge?: (file: WorkspaceFile) => string | undefined
  /** The folder shown first, relative to the interface root; by default the open folder. */
  startFolder?: string
  /** A note about the folder shown (for instance that its files cannot be attached), or nothing. */
  folderNote?: (folder: string) => string | undefined
}) {
  const { status } = useApp()
  const openFolder = status?.workspace_path || ''
  const [folder, setFolder] = useState((startFolder ?? openFolder).replace(/\/+$/, ''))
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<Sort>({ key: 'name', descending: false })
  const [anchor, setAnchor] = useState<string | null>(null)
  const [focus, setFocus] = useState(0)
  const [activeFolder, setActiveFolder] = useState<string | null>(null)
  const { listing, error } = useFolder(folder)
  const searching = query.trim().length > 0
  const all = useAllFiles(searching)
  const rowRefs = useRef<(HTMLDivElement | null)[]>([])
  const root = status?.root.split('/').filter(Boolean).pop() || 'Folders'
  const openName = openFolder.split('/').pop() || root

  // A folder that disappeared (deleted or renamed outside the interface): back to the workspace.
  useEffect(() => { if (error && folder) { setFolder(''); } }, [error, folder])

  const rows: Row[] = useMemo(() => {
    if (searching) {
      const words = query.toLowerCase().split(/\s+/).filter(Boolean)
      return (all.files || []).filter((file) => words.every((word) => file.path.toLowerCase().includes(word)))
        .sort(compare(sort)).map((file) => ({ type: 'file' as const, file }))
    }
    if (!listing) return []
    const folders = [...listing.folders].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }))
    return [...folders.map((item) => ({ type: 'folder' as const, folder: item })),
      ...[...listing.files].sort(compare(sort)).map((file) => ({ type: 'file' as const, file }))]
  }, [searching, query, all.files, listing, sort])
  const files = rows.flatMap((row) => row.type === 'file' && !unavailable?.(row.file) ? [row.file.path] : [])

  const go = (path: string) => { setFolder(path); setQuery(''); setAnchor(null); setActiveFolder(null); setFocus(0) }
  const up = () => { if (folder) go(folder.split('/').slice(0, -1).join('/')) }

  /** Desktop selection rules: plain click selects one file, Ctrl/⌘ toggles one, Shift selects a range. */
  const pick = (path: string, { toggle, range }: { toggle: boolean; range: boolean }) => {
    if (!multiple) { onSelect(toggle && selected.includes(path) ? [] : [path]); setAnchor(path); return }
    const from = anchor ? files.indexOf(anchor) : -1
    const to = files.indexOf(path)
    if (range && from !== -1 && to !== -1) {
      const span = files.slice(Math.min(from, to), Math.max(from, to) + 1)
      onSelect(toggle ? [...new Set([...selected, ...span])] : span)
      return  // the anchor stays, as in a desktop file manager
    }
    if (toggle) onSelect(selected.includes(path) ? selected.filter((item) => item !== path) : [...selected, path])
    else onSelect([path])
    setAnchor(path)
  }
  const click = (event: ReactMouseEvent, row: Row, index: number) => {
    setFocus(index)
    if (row.type === 'folder') { setActiveFolder(row.folder.path); return }
    setActiveFolder(null)
    if (unavailable?.(row.file)) return
    pick(row.file.path, { toggle: event.metaKey || event.ctrlKey, range: event.shiftKey })
  }
  const open = (row: Row) => {
    if (row.type === 'folder') go(row.folder.path)
    else if (onOpen && !unavailable?.(row.file)) onOpen(row.file)
  }
  const move = (index: number) => {
    const next = Math.max(0, Math.min(rows.length - 1, index))
    setFocus(next)
    rowRefs.current[next]?.focus()
    return rows[next]
  }
  const keys = (event: KeyboardEvent) => {
    const row = rows[focus]
    const mod = event.metaKey || event.ctrlKey
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
      if (event.key === 'ArrowUp' && event.altKey) { event.preventDefault(); up(); return }
      event.preventDefault()
      const target = move(event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : focus + (event.key === 'ArrowDown' ? 1 : -1))
      if (target?.type === 'file' && !unavailable?.(target.file) && !mod) pick(target.file.path, { toggle: false, range: event.shiftKey })
      else if (target?.type === 'folder') setActiveFolder(target.folder.path)
    } else if (event.key === 'Enter' && row) {
      event.preventDefault(); open(row)
    } else if (event.key === ' ' && row?.type === 'file') {
      event.preventDefault(); if (!unavailable?.(row.file)) pick(row.file.path, { toggle: true, range: event.shiftKey })
    } else if (event.key === 'Backspace' && !searching) {
      event.preventDefault(); up()
    } else if (mod && event.key.toLowerCase() === 'a' && multiple) {
      event.preventDefault(); onSelect([...new Set([...selected, ...files])])
    }
  }
  const sortBy = (key: Sort['key']) => setSort((current) => ({ key, descending: current.key === key ? !current.descending : key !== 'name' }))
  const arrow = (key: Sort['key']) => sort.key === key ? (sort.descending ? ' ↓' : ' ↑') : ''
  const crumbs = listing?.crumbs || []
  const loading = searching ? !all.files : !listing && !error
  const truncated = searching ? all.truncated : !!listing?.truncated
  const selectedHere = selected.length
  const statusText = selectedHere === 1 ? displayPath(selected[0])
    : selectedHere > 1 ? `${count(selectedHere)} files selected`
    : multiple ? `Double-click a folder to open it${onOpen ? ', a file to attach it' : ''}. ${MOD}-click or Shift-click to select several files.`
    : 'Double-click a folder to open it.'

  return (
    <div className="ws-file-explorer">
      <div className="ws-file-toolbar">
        <button type="button" className="btn btn-quiet btn-small" disabled={!folder || searching} onClick={up} title="Up one folder (Backspace)" aria-label="Up one folder">
          <span className="ws-file-up"><BackIcon size={15} /></span> Up
        </button>
        <nav className="crumbs ws-file-crumbs" aria-label="Folder path">
          <button type="button" className="crumb" onClick={() => go('')} aria-current={!folder && !searching ? 'page' : undefined}>{root}</button>
          {crumbs.map((crumb, index) => (
            <span key={crumb.path}>
              <span className="crumb-sep">/</span>
              <button type="button" className="crumb" onClick={() => go(crumb.path)} aria-current={index === crumbs.length - 1 && !searching ? 'page' : undefined}>{crumb.name}</button>
            </span>
          ))}
        </nav>
        {openFolder !== folder && !searching && <button type="button" className="btn btn-quiet btn-small ws-file-home" onClick={() => go(openFolder)}
          title="Go back to the open folder"><FolderIcon size={14} /> {openName}</button>}
        <input className="text-input ws-file-search" type="search" value={query} onChange={(event) => { setQuery(event.target.value); setFocus(0) }}
          placeholder="Search all folders" aria-label="Search files in all folders" />
      </div>
      <ErrorNote>{error && folder ? '' : error}</ErrorNote>
      {!searching && folderNote?.(folder) && <p className="warning-note ws-file-note">{folderNote(folder)}</p>}
      {truncated && <p className="warning-note" role="status">{searching ? 'Some workspace entries were omitted from this listing.' : 'This folder has more entries than can be listed.'}</p>}
      <div className="ws-file-head" aria-hidden="true">
        <button type="button" onClick={() => sortBy('name')}>Name{arrow('name')}</button>
        <button type="button" className="ws-file-col-size" onClick={() => sortBy('size')}>Size{arrow('size')}</button>
        <button type="button" className="ws-file-col-date" onClick={() => sortBy('updated')}>Modified{arrow('updated')}</button>
      </div>
      <div className="ws-file-list" role="listbox" aria-label={searching ? `Files matching ${query}` : `Folder ${crumbs.at(-1)?.name || root}`}
        aria-multiselectable={multiple || undefined} onKeyDown={keys}>
        {loading && <Loading />}
        {!loading && !rows.length && <p className="muted ws-file-empty">{searching ? `No file matches “${query.trim()}”.`
          : folder ? 'This folder is empty.' : `No files yet. Drop files on a conversation, or attach them from your device, to add them to ${root}.`}</p>}
        {rows.map((row, index) => {
          const tab = index === Math.min(focus, rows.length - 1) ? 0 : -1
          if (row.type === 'folder') {
            const item = row.folder
            return (
              <div key={'d:' + item.path} ref={(element) => { rowRefs.current[index] = element }} role="option" tabIndex={tab}
                aria-selected={activeFolder === item.path} className={'ws-file-row ws-folder-row' + (activeFolder === item.path ? ' ws-folder-active' : '')}
                title={`${item.name} (double-click to open)`} onClick={(event) => click(event, row, index)} onDoubleClick={() => open(row)}
                onMouseDown={(event) => { if (event.shiftKey) event.preventDefault() }}>
                <FolderIcon size={16} />
                <span className="ws-file-name">{item.name}</span>
                {item.project && <span className="pill">Project</span>}
                {openFolder && item.path === openFolder && <span className="pill ws-open-pill">Open folder</span>}
                <span className="ws-file-size">{item.items === 1 ? '1 item' : `${count(item.items)} items`}</span>
                <span className="ws-file-date" />
                <span className="ws-folder-go"><ChevronIcon size={14} /></span>
              </div>
            )
          }
          const file = row.file
          const part = splitName(file.path)
          const reason = unavailable?.(file)
          const on = selected.includes(file.path)
          const label = badge?.(file)
          return (
            <div key={'f:' + file.path} ref={(element) => { rowRefs.current[index] = element }} role="option" tabIndex={tab}
              aria-selected={on} aria-disabled={reason ? true : undefined}
              className={'ws-file-row' + (on ? ' ws-file-row-selected' : '') + (reason ? ' ws-file-row-unavailable' : '')}
              title={reason || displayPath(file.path)} onClick={(event) => click(event, row, index)}
              onDoubleClick={onOpen ? () => open(row) : undefined}
              onMouseDown={(event) => { if (event.shiftKey) event.preventDefault() }}>
              <FileIcon size={15} />
              <span className="ws-file-name">
                {part.name}
                {searching && <button type="button" className="link-btn ws-file-where" title="Open this folder"
                  onClick={(event) => { event.stopPropagation(); go(part.folder) }}>{part.folder ? displayPath(part.folder) : root}</button>}
              </span>
              {label && <span className="pill">{label}</span>}
              <a className="btn btn-quiet btn-small ws-file-view" href={fileDownloadUrl(file.path)} download={part.name}
                title="Download the file" onClick={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}>
                <DownloadIcon size={13} /> Download</a>
              <button type="button" className="btn btn-quiet btn-small ws-file-view" title="Open the file in the viewer, beside the conversation"
                onClick={(event) => { event.stopPropagation(); if (onView) onView(file); else openFile(file.path) }} onDoubleClick={(event) => event.stopPropagation()}>
                View file <ExternalIcon size={13} /></button>
              {part.extension && <span className={'ws-file-ext ws-file-ext-' + file.kind}>.{part.extension}</span>}
              <span className="ws-file-size">{bytes(file.size)}</span>
              <span className="ws-file-date">{ago(file.updated_at)}</span>
              <span className="ws-folder-go" />
            </div>
          )
        })}
      </div>
      <p className="ws-file-status muted small" role="status">{statusText}</p>
    </div>
  )
}

/**
 * Attach files given by their paths relative to the interface root: a file of the open folder as it is, a file of
 * another folder copied into the open folder first (into the project's folder in a project's conversation),
 * as an upload would be: the model reads only the open folder. Returns the attachments' paths, relative to the
 * open folder, and the files that could not be copied.
 */
async function attachable(paths: string[], prefix: string, projectId?: string | null) {
  const inOpenFolder = (path: string) => path.startsWith(prefix) ? path.slice(prefix.length) : null
  const attached = paths.flatMap((path) => inOpenFolder(path) ?? [])
  const failed: { source: string; error: string }[] = []
  const outside = paths.filter((path) => inOpenFolder(path) === null)
  if (outside.length) {
    const result = await api.copyFiles(outside, projectId || undefined)
    for (const item of result.files) {
      if (item.path) attached.push(item.path)
      else failed.push({ source: item.source, error: item.error || 'not copied' })
    }
  }
  return { attached, failed }
}

const failures = (failed: { source: string; error: string }[]) =>
  failed.map((item) => `${item.source.split('/').pop()}: ${item.error}`).join(' · ')

/** "See my files": what the folders hold, from the folder browser. A double click attaches a file to the next
 * message of the conversation open behind it. */
export function FilesDialog({ onClose, onBack }: { onClose: () => void; onBack?: () => void }) {
  const { status, chats } = useApp()
  const route = useRoute()
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState('')
  const prefix = status?.workspace_path ? status.workspace_path + '/' : ''
  const projectId = chats?.find((chat) => chat.id === route.id)?.project_id ?? null
  const attach = async (file: WorkspaceFile) => {
    setError('')
    try {
      const { attached, failed } = await attachable([file.path], prefix, projectId)
      if (attached.length) {
        window.dispatchEvent(new CustomEvent('square-attach-paths', { detail: attached }))
        onClose()
      } else setError(failures(failed))
    } catch (reason) {
      setError((reason as Error).message)
    }
  }
  return (
    <Modal title="My files" icon={<FileIcon />} onClose={onClose} wide>
      <p className="modal-lede">Your folders and their files, starting in the open folder, whose files the model can be given. Double-click a file to attach it to your next message; point at it to view or download it.</p>
      <FileExplorer selected={selected} onSelect={setSelected} multiple onOpen={(file) => void attach(file)}
        onView={(file) => { onClose(); openFile(file.path) }} />
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        {onBack && <button type="button" className="btn action-start" onClick={onBack}><BackIcon size={15} /> Back to folders</button>}
        <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
      </div>
    </Modal>
  )
}

/** The kinds of files an upload accepts: only those can be copied into the open folder. */
const COPYABLE = /\.(tex|sty|cls|bib|md|txt|py|json|ya?ml|toml|csv|jl|rs|c|h|cpp|hpp|r|m|js|sh|ini|cfg|pdf|png|jpe?g|webp|tar\.gz|tgz|tar|gz)$/i

/**
 * Choose files and attach them to the next message. A file of another folder is copied into the open folder
 * first (into the project's folder in a project's conversation), as an upload would be: the model reads only
 * the open folder, and that limit stays. `attached` and `startFolder` are relative to the open folder.
 */
export function AttachFromWorkspace({ attached, onAttach, onClose, startFolder, projectId }: {
  attached: string[]; onAttach: (paths: string[]) => void; onClose: () => void; startFolder?: string; projectId?: string | null
}) {
  const { status, projects } = useApp()
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // The explorer's paths are relative to the interface root; attachments are relative to the open folder.
  const prefix = status?.workspace_path ? status.workspace_path + '/' : ''
  const inOpenFolder = (path: string) => path.startsWith(prefix) ? path.slice(prefix.length) : null
  const fromRoot = attached.map((path) => prefix + path)
  const openName = status?.workspace.split('/').filter(Boolean).pop() || 'the open folder'
  const project = projects?.find((item) => item.id === projectId)
  const destination = project ? `the folder of the project ${project.name}` : openName
  const unavailable = (file: WorkspaceFile) => fromRoot.includes(file.path) ? 'Already attached to your next message'
    : isImagePath(file.path) && !status?.image_support ? 'This model cannot read images'
    : inOpenFolder(file.path) === null && !COPYABLE.test(file.path) ? 'Only the kinds of files you can upload can be copied into the open folder'
    : undefined
  const outside = selected.filter((path) => inOpenFolder(path) === null)
  const attach = async (paths: string[]) => {
    setBusy(true); setError('')
    try {
      const { attached: done, failed } = await attachable(paths, prefix, projectId)
      if (done.length) onAttach(done)
      if (!failed.length) { onClose(); return }
      setSelected(failed.map((item) => item.source))
      setError(failures(failed) + (done.length ? ' The other files were attached.' : ''))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title="Attach files from the workspace" icon={<FileIcon />} onClose={busy ? undefined : onClose} wide>
      <p className="modal-lede">Select the files to attach to your next message.</p>
      <FileExplorer selected={selected} onSelect={setSelected} multiple unavailable={unavailable}
        onOpen={(file) => { if (!busy) void attach([file.path]) }} onView={(file) => { onClose(); openFile(file.path) }}
        startFolder={startFolder ? prefix + startFolder.replace(/\/+$/, '') : undefined}
        badge={(file) => fromRoot.includes(file.path) ? 'Attached' : undefined}
        folderNote={(folder) => inOpenFolder(folder + '/') === null
          ? 'This folder is outside the active workspace. Files you select here will be copied into the active workspace so that the agent can access them.'
          : undefined} />
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        {outside.length > 0 && <span className="muted small action-start">
          {outside.length === 1 ? '1 file' : `${count(outside.length)} files`} from another folder will be copied into {destination}.</span>}
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={!selected.length || busy} onClick={() => void attach(selected)}>
          {busy ? 'Copying…' : selected.length > 1 ? `Attach ${count(selected.length)} files` : 'Attach'}
        </button>
      </div>
    </Modal>
  )
}

/** The composer's "Attach files" button: a menu to attach from this device (an upload) or from the workspace. */
export function AttachMenu({ label, disabled, attached, onAttach, startFolder, projectId }: {
  label: string; disabled?: boolean; attached: string[]; onAttach: (paths: string[]) => void; startFolder?: string
  projectId?: string | null
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
          <FolderIcon size={15} /><span><strong>From the workspace</strong><span className="attach-menu-hint">Files already uploaded; from another folder, a copy is made here</span></span>
        </button>
      </div>}
      {choosing && <AttachFromWorkspace attached={attached} onAttach={onAttach} onClose={() => setChoosing(false)}
        startFolder={startFolder} projectId={projectId} />}
    </div>
  )
}
