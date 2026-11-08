import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { beginUpload, dropTarget, useUploadsPending } from '../drop'
import { useApp } from '../store'
import { isImagePath } from './ImageAttachments'

type Toast = { id: number; text: string; error?: boolean }

/** Drag files anywhere over the work pane: they are saved in the workspace. */
export function DropZone() {
  const { status, projects } = useApp()
  const [over, setOver] = useState(false)
  const [toasts, setToasts] = useState<Toast[]>([])
  const depth = useRef(0)
  const counter = useRef(0)
  const input = useRef<HTMLInputElement>(null)
  const imageSupport = useRef(false)
  imageSupport.current = status?.image_support === true
  const uploading = useUploadsPending()

  useEffect(() => {
    const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types || []).includes('Files')
    // Screenshots in the feedback dialog must never become workspace uploads.
    const feedbackOpen = (event: DragEvent) => {
      if (!document.querySelector('[data-feedback-dialog]')) return false
      event.preventDefault(); depth.current = 0; setOver(false); return true
    }
    const toast = (text: string, error = false) => {
      const id = ++counter.current
      setToasts((items) => [...items, { id, text, error }])
      window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), error ? 7000 : 4000)
    }
    const enter = (event: DragEvent) => {
      if (!hasFiles(event)) return
      if (feedbackOpen(event)) return
      event.preventDefault()
      depth.current += 1
      setOver(true)
    }
    const leave = (event: DragEvent) => {
      if (!hasFiles(event)) return
      depth.current = Math.max(0, depth.current - 1)
      if (!depth.current) setOver(false)
    }
    const overHandler = (event: DragEvent) => {
      if (!hasFiles(event)) return
      if (feedbackOpen(event)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const upload = async (files: File[], target = dropTarget()) => {
      if (!files.length) return
      const finish = beginUpload(target)
      const attach = target?.handler
      const saved: string[] = []
      try {
        for (const file of files) {
          try {
            if ((isImagePath(file.name) || file.type.startsWith('image/')) && !imageSupport.current) throw new Error('This model cannot read images. Use a vision-capable model or attach text instead.')
            const result = await api.upload(file, target?.project_id)
            saved.push(result.path)
            const original = target?.project_id ? `projects/${target.project_id}/${file.name}` : file.name
            toast(result.path === original ? `Added ${result.path}` : `Added ${file.name} as ${result.path} (a file with that name exists)`)
          } catch (reason) {
            toast(`${file.name}: ${(reason as Error).message}`, true)
          }
        }
        // Navigation during an upload must not attach old files to the new composer.
        if (saved.length && target === dropTarget()) attach?.(saved)
      } finally { finish() }
    }
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return
      if (feedbackOpen(event)) return
      event.preventDefault(); depth.current = 0; setOver(false)
      void upload(Array.from(event.dataTransfer?.files || []))
    }
    const paste = (event: ClipboardEvent) => {
      if (event.defaultPrevented || !dropTarget() || (event.target instanceof Element && event.target.closest('[role="dialog"]'))) return
      const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith('image/'))
      if (!files.length) return
      event.preventDefault(); void upload(files)
    }
    let pickerTarget: ReturnType<typeof dropTarget> | undefined
    const choose = () => { pickerTarget = dropTarget(); input.current?.click() }
    const selected = () => {
      const files = Array.from(input.current?.files || [])
      if (input.current) input.current.value = ''
      void upload(files, pickerTarget)
      pickerTarget = undefined
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragleave', leave)
    window.addEventListener('dragover', overHandler)
    window.addEventListener('drop', drop)
    window.addEventListener('paste', paste)
    window.addEventListener('square-attach-files', choose)
    const picker = input.current
    picker?.addEventListener('change', selected)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('dragover', overHandler)
      window.removeEventListener('drop', drop)
      window.removeEventListener('paste', paste)
      window.removeEventListener('square-attach-files', choose)
      picker?.removeEventListener('change', selected)
    }
  }, [])

  const folder = status?.workspace.split('/').filter(Boolean).pop() || 'the workspace'
  const target = dropTarget()
  const project = projects?.find((item) => item.id === target?.project_id)
  return (
    <>
      <input ref={input} type="file" multiple hidden accept={'.tex,.sty,.cls,.bib,.md,.txt,.pdf,.py' + (status?.image_support ? ',.png,.jpg,.jpeg,.webp' : '')} aria-label="Attach files or images" />
      {over && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-card">
            <p className="drop-title">Drop to add to {folder}</p>
            <p className="drop-hint">{target ? target.label : 'Files are saved in the folder; nothing is replaced.'}{project ? ` · ${project.name}` : ''}</p>
            <p className="drop-types">.tex .sty .cls .bib .md .txt .pdf .py{status?.image_support ? ' · PNG JPEG WebP' : ''}</p>
          </div>
        </div>
      )}
      <div className="toasts" role="status">
        {uploading > 0 && <p className="toast">Uploading attachments…</p>}
        {toasts.map((item) => <p key={item.id} className={'toast' + (item.error ? ' toast-error' : '')}>{item.text}</p>)}
      </div>
    </>
  )
}
