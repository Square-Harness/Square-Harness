// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useState, type FormEvent } from 'react'
import { api, type FolderListing, type ReadSettings } from '../api'
import { workspaceBusy } from '../concurrency'
import { markOwnFolderSwitch, refreshStatus, useApp } from '../store'
import { ErrorNote, Loading, Modal, Toggle } from './common'
import { FilesDialog } from './FileExplorer'
import { ChevronIcon, FileIcon, FolderIcon } from './Icons'

function counts(files: { tex: number; pdf: number; py: number }) {
  const parts = [files.tex && `${files.tex} TeX`, files.pdf && `${files.pdf} PDF`, files.py && `${files.py} Python`].filter(Boolean)
  return parts.length ? parts.join(', ') : 'No TeX, PDF or Python files'
}

export function FolderBrowser({ onClose }: { onClose: () => void }) {
  const app = useApp()
  const [path, setPath] = useState(app.status?.workspace_path || '')
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [seeing, setSeeing] = useState(false)
  const busy = workspaceBusy(app.snapshot)

  useEffect(() => {
    setError('')
    api.folders(path).then(setListing, (reason: Error) => setError(reason.message))
  }, [path])

  const open = async (target: string) => {
    setError('')
    try {
      markOwnFolderSwitch()
      await api.openFolder(target)
      await refreshStatus()
      onClose()
    } catch (reason) {
      setError((reason as Error).message)
    }
  }
  const create = async (event: FormEvent) => {
    event.preventDefault()
    try {
      const created = await api.createFolder(path, name)
      setName('')
      setPath(created.path)
    } catch (reason) {
      setError((reason as Error).message)
    }
  }
  const rootName = (listing?.root || app.status?.root || '').split('/').filter(Boolean).pop() || 'root'
  const crumbs = path ? path.split('/') : []
  const isCurrent = listing && listing.path === (app.status?.workspace_path || '')
  const recent = listing ? (listing.recent.includes('') ? listing.recent : [...listing.recent, '']) : []
  if (seeing) return <FilesDialog onClose={onClose} onBack={() => setSeeing(false)} />
  return (
    <Modal title="Open a folder" icon={<FolderIcon />} onClose={onClose} wide>
      <nav className="crumbs" aria-label="Folder path">
        <button type="button" className="crumb" onClick={() => setPath('')}>{rootName}</button>
        {crumbs.map((part, index) => (
          <span key={index}>
            <span className="crumb-sep">/</span>
            <button type="button" className="crumb" onClick={() => setPath(crumbs.slice(0, index + 1).join('/'))}>{listing?.project && listing.path === path && index === crumbs.length - 1 ? listing.project.name : part}</button>
          </span>
        ))}
      </nav>
      <ErrorNote>{error}</ErrorNote>
      {!listing && !error && <Loading />}
      {listing && (
        <>
          <div className="folder-here">
            <span className="muted">{counts(listing.files)} in this folder</span>
            {listing.holds_projects ? <span className="muted small">The file folders of your projects</span>
              : listing.project ? <span className="muted small">Files of the project <strong>{listing.project.name}</strong>{listing.project.deleted ? ' (deleted)' : ''}</span>
              : <button type="button" className="btn btn-primary" disabled={!!isCurrent || busy} onClick={() => open(listing.path)}>
                {isCurrent ? 'This is the open folder' : 'Open this folder'}
              </button>}
          </div>
          {listing.project && <p className="muted small">A project keeps its files here. {listing.project.deleted ? 'The project was deleted; its files were kept.' : 'Open the project from the conversation list to work with them; this folder cannot be opened as a workspace.'}</p>}
          {busy && <p className="busy-note">Pause all active work and cancel queued turns before opening another folder. Chats and jobs belong to the folder they started in.</p>}
          <ul className="folder-list">
            {listing.folders.map((folder) => (
              <li key={folder.path}>
                <button type="button" className="folder-row" onClick={() => setPath(folder.path)} title={folder.project ? folder.path : undefined}>
                  <FolderIcon size={17} />
                  <span className="folder-name">{folder.name}</span>
                  {folder.project && <span className="pill">{folder.project.deleted ? 'Deleted project' : 'Project'}</span>}
                  {folder.jobs && <span className="pill">Saved work</span>}
                  <span className="folder-count">{counts(folder.files)}</span>
                  <ChevronIcon size={15} />
                </button>
              </li>
            ))}
            {!listing.folders.length && <li className="muted small folder-empty">No subfolders here.</li>}
          </ul>
          <form className="folder-new" onSubmit={create}>
            <input className="text-input" value={name} onChange={(event) => setName(event.target.value)} placeholder="New folder name" aria-label="New folder name" />
            <button type="submit" className="btn" disabled={!name.trim()}>Create folder here</button>
          </form>
          {recent.length > 0 && (
            <div className="recent">
              <p className="detail-label">Recently opened</p>
              {recent.map((item) => (
                <button key={item || '.'} type="button" className="link-btn recent-item" onClick={() => setPath(item)}>{item || rootName}</button>
              ))}
            </div>
          )}
          <p className="muted small">Only folders inside {listing.root} can be opened. Hidden folders are not shown.</p>
        </>
      )}
      <div className="modal-actions folder-actions">
        <button type="button" className="btn" onClick={() => setSeeing(true)}><FileIcon size={15} /> See my files</button>
      </div>
    </Modal>
  )
}

const GROUPS: { key: keyof ReadSettings; label: string; hint: string }[] = [
  { key: 'tex', label: 'LaTeX', hint: '.tex .bib .sty .cls and TeX source archives' },
  { key: 'pdf', label: 'PDF', hint: 'text extracted locally' },
  { key: 'py', label: 'Python', hint: '.py' },
  { key: 'text', label: 'Other text', hint: '.md .txt .json .yaml .csv' },
]

export function FileAccess({ onClose }: { onClose: () => void }) {
  const { status } = useApp()
  const [error, setError] = useState('')
  if (!status) return null
  const toggle = async (key: keyof ReadSettings, value: boolean) => {
    setError('')
    try {
      await api.saveSettings({ [key]: value })
      await refreshStatus()
    } catch (reason) {
      setError((reason as Error).message)
    }
  }
  return (
    <Modal title="File access" icon={<FileIcon />} onClose={onClose}>
      <p className="modal-lede">The kinds of files the model may list, search and read by itself in this folder. Files you attach or drop into a job are always available to that job.</p>
      <div className="option-list">
        {GROUPS.map((group) => (
          <Toggle key={group.key} label={group.label} hint={group.hint} checked={status.read[group.key]} onChange={(value) => { void toggle(group.key, value) }} />
        ))}
      </div>
      <ErrorNote>{error}</ErrorNote>
      <p className="muted small">Saved for this folder as you change it, and applied to new work. Nothing leaves your computer when the model reads a file.</p>
      <div className="modal-actions"><button type="button" className="btn btn-primary" onClick={onClose}>Done</button></div>
    </Modal>
  )
}
