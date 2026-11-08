import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api, ApiError, type FeedbackCategory, type FeedbackConfig, type FeedbackImage, type FeedbackPreview, type FeedbackResult } from '../api'
import { bytes } from '../format'
import { ErrorNote, Modal } from './common'
import { CloseIcon, PlusIcon } from './Icons'

type Image = FeedbackImage & { preview: string }

const readImage = (file: File) => new Promise<Image>((resolve, reject) => {
  const reader = new FileReader()
  reader.onerror = () => reject(new Error(`Could not read ${file.name}.`))
  reader.onload = () => {
    const preview = String(reader.result)
    resolve({ name: file.name, mime: file.type, data: preview.slice(preview.indexOf(',') + 1), preview })
  }
  reader.readAsDataURL(file)
})

export function FeedbackButton({ chatId, chatTitle }: { chatId: string | null; chatTitle?: string }) {
  const [context, setContext] = useState<{ id: string | null; title?: string } | null>(null)
  useEffect(() => {
    const open = () => setContext({ id: chatId, title: chatTitle })
    window.addEventListener('square-feedback', open)
    return () => window.removeEventListener('square-feedback', open)
  }, [chatId, chatTitle])
  return <>
    <button type="button" className="workspace-access feedback-open" onClick={() => setContext({ id: chatId, title: chatTitle })}>
      <span aria-hidden="true">◇</span> Feedback
    </button>
    {context && <FeedbackDialog chatId={context.id} chatTitle={context.title} onClose={() => setContext(null)} />}
  </>
}

function FeedbackDialog({ chatId, chatTitle, onClose }: { chatId: string | null; chatTitle?: string; onClose: () => void }) {
  const [config, setConfig] = useState<FeedbackConfig | null>(null)
  const [category, setCategory] = useState<FeedbackCategory>('bug')
  const [description, setDescription] = useState('')
  const [images, setImages] = useState<Image[]>([])
  const [includeTrace, setIncludeTrace] = useState(false)
  const [preview, setPreview] = useState<FeedbackPreview | null>(null)
  const [busy, setBusy] = useState<'images' | 'preview' | 'download' | 'send' | null>(null)
  const [result, setResult] = useState<FeedbackResult | null>(null)
  const [error, setError] = useState('')
  const [discard, setDiscard] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    let current = true
    api.feedbackConfig().then((value) => { if (current) setConfig(value) }, (reason: Error) => {
      if (current) setError(reason instanceof ApiError && reason.status === 404 ? 'Feedback is unavailable on this server.' : reason.message)
    })
    return () => { current = false }
  }, [])
  const close = () => {
    if (!result && (description.trim() || images.length)) setDiscard(true)
    else onClose()
  }
  async function add(files: File[]) {
    if (!files.length || busy || result) return
    const maxImages = Math.min(3, config?.max_images ?? 3)
    const maxBytes = Math.min(3 * 1024 * 1024, config?.max_image_bytes ?? 3 * 1024 * 1024)
    if (files.length + images.length > maxImages) { setError(`Attach up to ${maxImages} screenshots.`); return }
    if (files.some((file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > maxBytes)) {
      setError(`Use PNG, JPEG or WebP screenshots, no larger than ${bytes(maxBytes)} each.`); return
    }
    setBusy('images'); setError('')
    try { const added = await Promise.all(files.map(readImage)); setImages((existing) => [...existing, ...added]) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(null) }
  }
  const stopDrop = (event: DragEvent) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); event.stopPropagation() } }
  async function inspect() {
    if (!chatId || busy) return
    setBusy('preview'); setError('')
    try { setPreview(await api.previewFeedbackTrace(chatId)) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(null) }
  }
  async function download() {
    if (!chatId || busy) return
    setBusy('download'); setError('')
    try {
      const blob = await api.exportFeedbackTrace(chatId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = 'square-harness-conversation.zip'; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) { setError((reason as Error).message) }
    finally { setBusy(null) }
  }
  async function send() {
    if (!description.trim() || busy || !config?.enabled || (includeTrace && !chatId)) return
    setBusy('send'); setError('')
    try { setResult(await api.sendFeedback({ category, description: description.trim(), chat_id: includeTrace ? chatId : null,
      include_trace: includeTrace, images: images.map(({ name, mime, data }) => ({ name, mime, data })) })) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(null) }
  }
  return <Modal title="Feedback" onClose={busy ? undefined : close} wide>
    <div className="feedback-dialog" data-feedback-dialog onDragEnter={stopDrop} onDragLeave={stopDrop} onDragOver={stopDrop}
      onDrop={(event) => { stopDrop(event); void add([...event.dataTransfer.files]) }}
      onPaste={(event) => {
        const files = [...event.clipboardData.items].filter((item) => item.kind === 'file').map((item) => item.getAsFile()).filter((file): file is File => !!file)
        if (files.length) { event.preventDefault(); event.stopPropagation(); void add(files) }
      }}>
      {result ? <div className="feedback-result" role="status">
        <h3>{result.status === 'delivered' ? 'Feedback sent' : 'Feedback saved'}</h3>
        <p>{result.status === 'delivered' ? 'Thank you. Your feedback was delivered.' : 'Your feedback is saved on the server. Delivery has not been confirmed.'}</p>
        {result.delivery_error && <p className="muted small">{result.delivery_error}</p>}
        <a className="btn" href={api.feedbackArchiveUrl(result.id)} download>Download submitted feedback</a>
        <div className="modal-actions"><button type="button" className="btn btn-primary" onClick={onClose}>Done</button></div>
      </div> : <form onSubmit={(event) => { event.preventDefault(); void send() }}>
        <p className="muted">Report a bug, suggest an improvement, or flag a mathematical issue. Screenshots can be pasted, dropped or attached here.</p>
        {config?.delivery_mode === 'slack_files' && <p className="muted small">Your feedback, screenshots and any selected conversation trace are sent to the private Square Harness Slack channel. Screenshots appear as attachments; the complete report is attached as a ZIP.</p>}
        <label className="field"><span className="field-label">Category</span><select value={category} onChange={(event) => setCategory(event.target.value as FeedbackCategory)}>
          <option value="bug">Bug</option><option value="suggestion">Suggestion</option><option value="math">Mathematical issue</option>
        </select></label>
        <label className="field"><span className="field-label">What happened?</span><textarea aria-label="Feedback description" rows={5} maxLength={8000} value={description}
          placeholder="What did you expect, and what did you observe?" onChange={(event) => setDescription(event.target.value)} /></label>
        <div className="feedback-images">
          {images.map((image, index) => <figure key={index}><img src={image.preview} alt={`Attached screenshot: ${image.name}`} /><figcaption>{image.name}</figcaption>
            <button type="button" className="icon-btn" disabled={!!busy} aria-label={`Remove screenshot ${image.name}`} onClick={() => setImages((items) => items.filter((_, i) => i !== index))}><CloseIcon size={15} /></button>
          </figure>)}
        </div>
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={(event) => { void add([...(event.target.files || [])]); event.target.value = '' }} />
        <button type="button" className="btn btn-small" disabled={!!busy || images.length >= Math.min(3, config?.max_images ?? 3)} onClick={() => input.current?.click()}><PlusIcon size={15} /> Add screenshots</button>
        <p className="muted small">Up to three PNG, JPEG or WebP images, 3 MB each.</p>
        <div className="feedback-trace">
          <label className="feedback-checkbox"><input type="checkbox" checked={includeTrace} disabled={!chatId || !!busy} onChange={(event) => { setIncludeTrace(event.target.checked); setPreview(null) }} />
            <span>Include this conversation’s trace</span></label>
          {chatId ? <p className="muted small">{chatTitle || 'Current conversation'}. This can include messages, model thinking and tool activity. You can optionally inspect or download the trace before sending.</p>
            : <p className="muted small">Open a conversation to include its trace. Other conversations are never attached.</p>}
          {includeTrace && <>
            <div className="feedback-trace-actions"><button type="button" className="btn btn-small" disabled={!!busy} onClick={() => void inspect()}>{busy === 'preview' ? 'Reading…' : 'Preview trace files'}</button>
              <button type="button" className="btn btn-small" disabled={!!busy} onClick={() => void download()}>{busy === 'download' ? 'Preparing ZIP…' : 'Download trace ZIP'}</button></div>
            {preview && <div className="feedback-manifest"><p className="small">{preview.files.length} files · {bytes(preview.size)}</p><ul>{preview.files.map((file) => <li key={file.name}><span>{file.name}</span><span>{bytes(file.size)}</span></li>)}</ul></div>}
            {preview?.scope && <p className="muted small">{preview.scope}{preview.redacted ? '. Recognized credentials are redacted.' : ''}</p>}
            {!!preview?.omissions?.length && <div className="feedback-omissions"><p className="muted small">Not included:</p><ul>{preview.omissions.map((item, index) => <li key={index}>{item.name}: {item.reason}</li>)}</ul></div>}
            {!preview && <p className="muted small">Preview and ZIP download are optional. You can send feedback now with the selected trace.</p>}
            {preview && <p className="muted small">A running conversation can still change. The ZIP captures its trace when downloaded or sent.</p>}
          </>}
        </div>
        {config && !config.enabled && <p className="warning-note">Feedback is not enabled for this installation.</p>}
        {config?.enabled && !config.delivery_configured && <p className="muted small">Delivery is not configured. Your feedback will be saved on this server.</p>}
        <ErrorNote>{error}</ErrorNote>
        {discard ? <div className="warning-note" role="alert"><p>Discard this feedback draft?</p><div className="modal-actions">
          <button type="button" className="btn" onClick={() => setDiscard(false)}>Keep editing</button><button type="button" className="btn btn-delete" onClick={onClose}>Discard draft</button>
        </div></div> : <div className="modal-actions"><button type="button" className="btn" disabled={!!busy} onClick={close}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={!!busy || !config?.enabled || !description.trim() || (includeTrace && !chatId)}>{busy === 'send' ? 'Sending…' : 'Send feedback'}</button></div>}
      </form>}
    </div>
  </Modal>
}
