// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { ErrorNote, Modal } from './common'
import './screenshot.css'

type Area = { x: number; y: number; width: number; height: number }

/** A single local frame; every exit, including a late permission response, stops capture. */
export async function captureScreenshot(signal: AbortSignal): Promise<HTMLCanvasElement> {
  const options: DisplayMediaStreamOptions & { selfBrowserSurface: string; preferCurrentTab: boolean } = {
    video: true, audio: false, selfBrowserSurface: 'include', preferCurrentTab: true,
  }
  const request = navigator.mediaDevices.getDisplayMedia(options)
  let stream: MediaStream | null = null
  const stop = () => stream?.getTracks().forEach((track) => track.stop())
  // The browser chooser cannot be cancelled programmatically. Stop any late stream.
  void request.then((value) => { stream = value; if (signal.aborted) stop() }, () => {})
  signal.addEventListener('abort', stop, { once: true })
  const video = document.createElement('video')
  video.muted = true
  video.playsInline = true
  try {
    stream = await request
    if (signal.aborted) throw new DOMException('Screenshot cancelled.', 'AbortError')
    video.srcObject = stream
    await new Promise<void>((resolve, reject) => {
      let frame: number | undefined
      let finished = false
      const done = (error?: Error) => {
        if (finished) return
        finished = true
        window.clearTimeout(timer)
        signal.removeEventListener('abort', abort)
        if (frame !== undefined) video.cancelVideoFrameCallback(frame)
        error ? reject(error) : resolve()
      }
      const abort = () => done(new DOMException('Screenshot cancelled.', 'AbortError'))
      const timer = window.setTimeout(() => done(new Error('No screenshot frame was received. Try again or paste a screenshot.')), 10000)
      signal.addEventListener('abort', abort, { once: true })
      void video.play().then(() => {
        if (finished) return
        if (signal.aborted) { abort(); return }
        if (typeof video.requestVideoFrameCallback === 'function') frame = video.requestVideoFrameCallback(() => done())
        else done()
      }, (error: Error) => done(error))
    })
    if (!video.videoWidth || !video.videoHeight) throw new Error('The selected screen could not be captured.')
    const scale = Math.min(1, 4096 / Math.max(video.videoWidth, video.videoHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Screenshots are unavailable in this browser. Paste or attach an image instead.')
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    return canvas
  } finally {
    stop()
    signal.removeEventListener('abort', stop)
    video.pause()
    video.srcObject = null
  }
}

const blobOf = (canvas: HTMLCanvasElement, mime: string, quality?: number) => new Promise<Blob>((resolve, reject) => {
  canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('The screenshot could not be encoded.')), mime, quality)
})

/** Crop locally, then fit the existing upload limit without sending the full capture. */
async function screenshotFile(source: HTMLCanvasElement, area: Area, maxBytes: number) {
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  if (!context) throw new Error('The screenshot could not be prepared.')
  canvas.width = Math.max(1, Math.round(area.width))
  canvas.height = Math.max(1, Math.round(area.height))
  context.drawImage(source, area.x, area.y, area.width, area.height, 0, 0, canvas.width, canvas.height)
  let blob = await blobOf(canvas, 'image/png')
  if (blob.size <= maxBytes) return new File([blob], 'screenshot.png', { type: blob.type })
  for (let attempt = 0; attempt < 4; attempt++) {
    for (const quality of [0.9, 0.75, 0.6]) {
      blob = await blobOf(canvas, 'image/jpeg', quality)
      if (blob.size <= maxBytes) return new File([blob], 'screenshot.jpg', { type: blob.type })
    }
    canvas.width = Math.max(1, Math.floor(canvas.width * 0.75))
    canvas.height = Math.max(1, Math.floor(canvas.height * 0.75))
    context.drawImage(source, area.x, area.y, area.width, area.height, 0, 0, canvas.width, canvas.height)
  }
  throw new Error('This screenshot is too large. Select a smaller area or paste a smaller image.')
}

export function ScreenshotCrop({ source, maxBytes, onDone, onCancel }: {
  source: HTMLCanvasElement; maxBytes: number; onDone: (file: File) => Promise<void>; onCancel: () => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const drag = useRef<{ x: number; y: number; pointer: number } | null>(null)
  const [area, setArea] = useState<Area | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const full = { x: 0, y: 0, width: source.width, height: source.height }
  useEffect(() => { canvas.current?.getContext('2d')?.drawImage(source, 0, 0) }, [source])
  function point(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    return { x: Math.max(0, Math.min(source.width, (event.clientX - bounds.left) / bounds.width * source.width)),
      y: Math.max(0, Math.min(source.height, (event.clientY - bounds.top) / bounds.height * source.height)) }
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const start = drag.current
    if (!start || start.pointer !== event.pointerId) return
    const end = point(event)
    setArea({ x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) })
  }
  async function attach(selected: Area) {
    if (busy) return
    setBusy(true); setError('')
    try { await onDone(await screenshotFile(source, selected, maxBytes)) }
    catch (reason) { setError((reason as Error).message); setBusy(false) }
  }
  const valid = !!area && area.width >= 2 && area.height >= 2
  return <Modal title="Select screenshot area" onClose={busy ? undefined : onCancel} wide>
    <p className="muted small">Drag across the image to choose an area, then attach it. You can also use the whole screenshot. Nothing is sent until you submit your feedback.</p>
    <div className="screenshot-crop-frame">
      <div className="screenshot-crop-area" style={{ width: `min(100%, ${52 * source.width / source.height}dvh)` }}
        onPointerDown={(event) => {
          if (busy || event.button !== 0 || drag.current) return
          event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = { ...point(event), pointer: event.pointerId }; setArea(null)
        }} onPointerMove={move} onPointerUp={(event) => {
          if (drag.current?.pointer === event.pointerId) { move(event); drag.current = null }
        }}
        onPointerCancel={() => { drag.current = null; setArea(null) }}>
        <canvas ref={canvas} width={source.width} height={source.height} role="img" aria-label="Captured screen. Drag to select an area, or use the whole screenshot button." />
        {valid && <span className="screenshot-crop-selection" aria-hidden="true" style={{ left: `${area.x / source.width * 100}%`, top: `${area.y / source.height * 100}%`, width: `${area.width / source.width * 100}%`, height: `${area.height / source.height * 100}%` }} />}
      </div>
    </div>
    <p className="muted small" aria-live="polite">{valid ? `Selected area: ${Math.round(area.width)} × ${Math.round(area.height)} pixels.` : 'No area selected.'}</p>
    <ErrorNote>{error}</ErrorNote>
    <div className="modal-actions">
      <button type="button" className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
      <button type="button" className="btn" disabled={busy} onClick={() => void attach(full)}>Use whole screenshot</button>
      <button type="button" className="btn btn-primary" disabled={busy || !valid} onClick={() => area && void attach(area)}>{busy ? 'Preparing…' : 'Attach selected area'}</button>
    </div>
  </Modal>
}
