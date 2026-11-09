// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 SquareHarness Team

import { useEffect, useState } from 'react'
import { api, type ImageRef } from '../api'
import { useApp, useTick } from '../store'
import { CloseIcon, FileIcon } from './Icons'

export const IMAGE_SUFFIXES = ['.png', '.jpg', '.jpeg', '.webp']
export const isImagePath = (path: string) => /\.(png|jpe?g|webp)$/i.test(path)

export function ImageGallery({ images, onRemove }: { images: ImageRef[]; onRemove?: (name: string) => void }) {
  return <div className="image-attachments">
    {images.map((image, index) => <figure key={image.sha256 + ':' + index} className="image-attachment">
      <a href={api.imageUrl(image.sha256)} target="_blank" rel="noopener noreferrer" title={`Open ${image.name}`}>
        <img src={api.imageUrl(image.sha256)} alt={image.name} loading="lazy" decoding="async" />
      </a>
      <figcaption title={image.name}>{image.name}</figcaption>
      {onRemove && <button type="button" className="icon-btn" aria-label={`Remove ${image.name}`} onClick={() => onRemove(image.name)}><CloseIcon size={14} /></button>}
    </figure>)}
  </div>
}

/** Pin immutable thumbnails from workspace paths; the backend validates the full batch. */
export function PinnedImages({ paths, onRemove }: { paths: string[]; onRemove?: (path: string) => void }) {
  const { status } = useApp()
  const changed = useTick('files')
  const selected = paths.filter(isImagePath)
  const key = JSON.stringify([status?.workspace, changed, selected])
  const [snapshot, setSnapshot] = useState<{ key: string; images: ImageRef[]; error: string } | null>(null)
  useEffect(() => {
    let current = true
    if (selected.length) {
      api.previewImages(selected).then((result) => {
        if (current) setSnapshot({ key, images: result.images.map((image, index) => ({ ...image, name: selected[index] })), error: '' })
      }, (reason: Error) => { if (current) setSnapshot({ key, images: [], error: reason.message }) })
    }
    return () => { current = false }
    // The key includes file changes and workspace, so reused names never show an old preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  if (!selected.length) return null
  const ready = snapshot?.key === key ? snapshot : null
  return <div className="pinned-images">
    {ready?.images.length ? <ImageGallery images={ready.images} onRemove={onRemove} /> : <>
      <div className="attachments">{selected.map((path) => <span key={path} className="chip"><FileIcon size={14} /><span>{path}</span>{onRemove && <button type="button" className="chip-remove" aria-label={`Remove ${path}`} onClick={() => onRemove(path)}><CloseIcon size={13} /></button>}</span>)}</div>
      <p className="muted small">{ready?.error ? 'Image preview unavailable.' : 'Preparing image previews…'}</p>
    </>}
    {ready?.error && <p className="error-note" role="alert">{ready.error}</p>}
    <p className="muted small">Up to 8 PNG, JPEG or WebP images · 8 MiB each · 24 MiB combined.</p>
  </div>
}
