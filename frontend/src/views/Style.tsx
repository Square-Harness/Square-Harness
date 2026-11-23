// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { api, type DomainStyle, type ResearchDetail, type StyleChoice, type StyleDescription, type StyleEdit, type StyleProfile } from '../api'
import { Collapse, ErrorNote, FilePicker, Loading, Modal, NumberField, OptionalNumberField, Tabs, UsageMeters, WorkProgressNote, useLoad } from '../components/common'
import { effortLimits } from '../components/Effort'
import { CloseIcon, FileIcon, PauseIcon, PencilIcon, PlayIcon, PlusIcon, TrashIcon } from '../components/Icons'
import { Markdown, MathMacrosProvider } from '../components/Markdown'
import { IMAGE_SUFFIXES, isImagePath } from '../components/ImageAttachments'
import { researchLook, researchResumable, Square, type Variant } from '../components/Square'
import { capacityFull, taskForJob } from '../concurrency'
import { useDropTarget } from '../drop'
import { ago } from '../format'
import { go, href } from '../router'
import { useApp, useTick } from '../store'
import { Files } from './ProofView'
import { ActivityLog, ArtifactViewer, BusyNote, JobAside, StreamBody, queuedMessage, useJobClass, useLiveStream } from './shared'

const NAME = /^[\w][\w .-]{0,59}$/u
const DEFAULT_NAME = 'My papers'
const TEMPLATE = ['.tex', '.sty', '.cls', '.bib']
const ARTICLE_FILES = ['.tex', '.pdf', '.tar.gz', '.tgz', '.tar', '.gz', ...IMAGE_SUFFIXES]
const MAX_FILES = 20  // Hub._capture_images refuses more
const isArticleFile = (path: string) => ARTICLE_FILES.some((suffix) => path.toLowerCase().endsWith(suffix))
const STEPS = ['scan', 'classify', 'observe', 'condense', 'macros', 'save'] as const
const STEP_NAMES: Record<string, string> = { scan: 'Read', classify: 'Domains', observe: 'Articles', condense: 'Merge', macros: 'Macros', save: 'Save', done: 'Done' }
/** The questions a style answers about how the author explains (mathagent/style.py QUESTIONS). */
const QUESTIONS = [
  ['definitions', 'Definitions'], ['motivation', 'Motivation and the results put forward'],
  ['proof_motivation', 'Motivating the proof strategy'], ['proof_organisation', 'Organising proofs'],
  ['omissions', 'What is left out'], ['contribution', 'Pointing to the contribution'], ['tone', 'Tone and voice'],
] as const

/** A name no saved style has, so that learning a style never replaces one unasked. */
function freeName(saved: StyleProfile[]) {
  const taken = new Set(saved.map((item) => item.name))
  for (let n = 1; ; n++) {
    const name = n === 1 ? DEFAULT_NAME : `${DEFAULT_NAME} ${n}`
    if (!taken.has(name)) return name
  }
}

/** Learn from earlier articles or screenshots; exact macros require LaTeX sources. Two things are asked, the articles
 * and a name; a style is always learnt with the High limits, which More options can change. */
export function StyleNew() {
  const { status, snapshot } = useApp()
  const d = status?.defaults
  const images = status?.image_support === true
  const [selected, setSelected] = useState<string[]>([])
  const [otherFiles, setOtherFiles] = useState<string[]>([])
  const [templateFiles, setTemplateFiles] = useState<string[]>([])
  const [saved, setSaved] = useState<StyleProfile[]>([])
  const [choice, setChoice] = useState<StyleChoice>({ mode: 'auto' })
  const [typedName, setTypedName] = useState<string | null>(null)
  const [goal, setGoal] = useState('')
  const [tokens, setTokens] = useState<number | null>(null)
  const [minutes, setMinutes] = useState(30)
  const [inputTokens, setInputTokens] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuedNote, setQueuedNote] = useState('')

  useEffect(() => { api.styles().then((result) => { setSaved(result.styles); setChoice(result.choice) }, () => undefined) }, [])
  useEffect(() => {
    if (!d) return
    const limits = effortLimits('style', d, 'high')
    setTokens(limits.tokens)
    setMinutes(Math.max(1, Math.round(limits.seconds / 60)))
    setInputTokens(limits.input_tokens!)
  }, [d])
  const uploading = useDropTarget('Use PDFs, TeX sources or screenshots as style examples', (paths) => {
    setSelected((items) => [...new Set([...items, ...paths.filter(isArticleFile)])])
    setOtherFiles(paths.filter((path) => !isArticleFile(path)))
  })
  const browse = () => window.dispatchEvent(new CustomEvent('square-attach-files', {
    detail: ARTICLE_FILES.filter((suffix) => images || !isImagePath(suffix)).join(','),
  }))

  const running = capacityFull(snapshot, status)
  const name = (typedName ?? freeName(saved)).trim()
  const validName = NAME.test(name)
  const replaces = saved.some((item) => item.name === name)
  const tooMany = selected.length > MAX_FILES
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (uploading) return
    setBusy(true)
    setError('')
    setQueuedNote('')
    try {
      const result = await api.startStyle({ name, goal: goal.trim(), source_files: selected, template_files: templateFiles,
        tokens, seconds: minutes * 60, input_tokens: inputTokens, queue: true })
      if (result.task.state === 'queued') {
        setQueuedNote(queuedMessage(result.task.title))
        setSelected([])
      } else if (result.id) go('style', result.id)
      else setError(result.task.error || 'The style job did not start.')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sheet paper">
      <form className="sheet-inner compose" onSubmit={submit} noValidate>
        <div className="entry">
          <span className="in-margin margin-glyph" aria-hidden="true">≅</span>
          <div>
            <h1 className="page-title">Learn your writing style</h1>
            <p className="lede">
              Give a few of your own articles. Square Harness reads how you write, and your write-ups then follow your style.
            </p>
            <Collapse summary="What it learns">
              <ul className="style-learns">
                <li>Who your papers are written for and how you explain: how you introduce definitions, motivate the work
                  and your proof strategies, organise proofs, what you leave out and how you point to the contribution.</li>
                <li>Your names for results, your typesetting habits and your phrasings, kept only when they occur word
                  for word in your articles.</li>
                <li>From TeX sources, your macros, what you use them for and the packages they need.</li>
                <li>One style guide per field and readership: a numerics paper is not written like a geometry paper.</li>
              </ul>
            </Collapse>
          </div>
        </div>

        {saved.length > 0 && (
          <div className="entry">
            <span className="in-margin" />
            <SavedStyles styles={saved} choice={choice} onChoice={setChoice}
              onSaved={(next) => setSaved((items) => items.map((item) => item.name === next.name ? next : item))}
              onChanged={() => api.styles().then((result) => { setSaved(result.styles); setChoice(result.choice) }, () => undefined)} />
          </div>
        )}

        {saved.length > 0 && (
          <div className="entry">
            <span className="in-margin" />
            <h2 className="style-heading">Learn a new style</h2>
          </div>
        )}

        <div className="entry">
          <span className="in-margin round-number" aria-hidden="true">1</span>
          <div className="field">
            <span className="field-label">Add your articles</span>
            <button type="button" className="style-drop" onClick={browse} disabled={!!uploading}>
              <FileIcon size={24} />
              <span className="style-drop-title">{uploading ? 'Uploading…' : 'Drop your articles here, or click to browse'}</span>
              <span className="field-hint">PDFs with selectable text or TeX sources{images && '; you can also paste screenshots'}</span>
            </button>
            <FilePicker selected={selected} onChange={setSelected} allowPdf allowImages={images} accept={ARTICLE_FILES} label="Choose from workspace" />
            {tooMany ? <ErrorNote>Choose at most {MAX_FILES} files: {selected.length} are selected.</ErrorNote>
              : <span className="field-hint">One article is enough to start; two or more per field work better. TeX sources also teach your
                macros: add the files they \input as well.</span>}
            {otherFiles.length > 0 && <p className="field-hint" role="status">Saved to the workspace only: {otherFiles.join(', ')}. Choose a PDF, TeX source or screenshot for the style.</p>}
          </div>
        </div>

        <div className="entry">
          <span className="in-margin round-number" aria-hidden="true">2</span>
          <label className="field">
            <span className="field-label">Name this style</span>
            <input type="text" className="text-input style-name" value={typedName ?? freeName(saved)} onChange={(event) => setTypedName(event.target.value)}
              placeholder={DEFAULT_NAME} maxLength={60} />
            {!validName ? <span className="field-hint">{name ? 'Use letters, digits, spaces, dots and dashes.' : 'Name the style.'}</span>
              : replaces && <span className="field-hint">A saved style has this name: it will be replaced.</span>}
          </label>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <Collapse summary="More options">
            <div className="style-options-more">
              <div className="field">
                <span className="field-label">LaTeX template</span>
                <FilePicker selected={templateFiles} onChange={setTemplateFiles} accept={TEMPLATE} label="Choose template files" />
                <span className="field-hint">
                  Write-ups in this style start from its preamble (packages, macros, theorem environments). Give a .tex
                  (one of your articles works: only its preamble is used) and/or .sty, .cls or .bib files. A write-up
                  given a template of its own uses that one instead.
                </span>
              </div>
              <label className="field">
                <span className="field-label">About these articles</span>
                <textarea rows={2} value={goal} onChange={(event) => setGoal(event.target.value)} />
                <span className="field-hint">Helps the sorting by field; never copied into the style.</span>
              </label>
              <div className="field">
                <span className="field-label">Limits</span>
                <div className="field-grid">
                  <NumberField label="Time" value={minutes} onChange={setMinutes} min={1} suffix="min" />
                  <OptionalNumberField label="Total output token limit" value={tokens} onChange={setTokens} hint="Optional. Leave blank for no cumulative limit." />
                  <OptionalNumberField label="Total input token limit" value={inputTokens} onChange={setInputTokens} step={10000} hint="Each article is read once, then each field is merged and the macros are described." />
                </div>
              </div>
            </div>
          </Collapse>
        </div>

        <div className="entry">
          <span className="in-margin" />
          <div className="form-actions">
            <ErrorNote>{error}</ErrorNote>
            {queuedNote && <p className="queued-note" role="status">{queuedNote}</p>}
            {running && <BusyNote queue />}
            <button type="submit" className="btn btn-primary btn-large" disabled={busy || !!uploading || !selected.length || tooMany || !validName}>
              {uploading ? 'Uploading…' : busy ? 'Starting…' : running ? 'Add to the queue' : 'Learn my style'}
            </button>
            <p className="field-hint">
              {selected.length ? 'You get a style guide to read and correct; write-ups use it once it is saved.' : 'Add at least one article.'}
            </p>
          </div>
        </div>
      </form>
    </div>
  )
}

const sameChoice = (a: StyleChoice, b: StyleChoice) => a.mode === b.mode && (a.style || '') === (b.style || '') && (a.domain || '') === (b.domain || '')

/** The saved styles and which one write-ups follow (@writeup, @summarize and the Write-up form), saved as soon as it
 * changes: the learned style that fits the material (Automatic), a learned style or one of its domains, or the default
 * style (no rules of its own: the write-up guidelines as they are), always there in case a learned style does not suit. */
function SavedStyles({ styles, choice, onChoice, onSaved, onChanged }: {
  styles: StyleProfile[]; choice: StyleChoice; onChoice: (choice: StyleChoice) => void; onSaved: (profile: StyleProfile) => void
  onChanged: () => void
}) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [renaming, setRenaming] = useState<StyleProfile | null>(null)
  const [deleting, setDeleting] = useState<StyleProfile | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const choose = async (next: StyleChoice) => {
    if (sameChoice(next, choice)) return
    setBusy(true)
    setError('')
    try {
      onChoice((await api.setStyleChoice(next)).choice)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const learned = choice.mode === 'style' || choice.mode === 'domain'
  return (
    <section className="style-saved" aria-labelledby="style-saved-title">
      <h2 className="style-heading" id="style-saved-title">Your styles</h2>
      <p className="field-hint">
        Choose the style that @writeup, @summarize and the Write-up form follow, in every folder you open here. What you
        ask for in a request always comes first (for example “list the contributions as bullet points”).
      </p>
      <ul className="style-options" role="radiogroup" aria-labelledby="style-saved-title">
        <StyleOption on={choice.mode === 'auto'} busy={busy} onPick={() => choose({ mode: 'auto' })} title="Automatic"
          note="Each write-up follows the saved style that fits its material, or the default style when none fits." />
        {styles.map((profile) => {
          const on = learned && choice.style === profile.name
          const domains = profile.domains.filter((domain) => !on || choice.mode !== 'domain' || domain.name === choice.domain)
          const departures = on ? domains.flatMap((domain) => (domain.departures || []).map((departure) => ({ ...departure, domain: domain.name }))) : []
          const sources = profile.articles.some((article) => article.source_archive) ? 'text sources' : `article${profile.articles.length === 1 ? '' : 's'}`
          return (
            <StyleOption key={profile.name} on={on} busy={busy} onPick={() => choose({ mode: 'style', style: profile.name })} title={profile.name}
              note={`${profile.domains.map((item) => item.name).join(', ')} · ${profile.articles.length} ${sources} · ${profile.macros.length} macro${profile.macros.length === 1 ? '' : 's'} · ${ago(profile.updated_at)}`}>
              {on && profile.domains.length > 1 && (
                <label className="field style-option-domain">
                  <span className="field-hint">Kind of paper</span>
                  <select value={choice.mode === 'domain' ? choice.domain : ''} disabled={busy}
                    onChange={(event) => choose(event.target.value ? { mode: 'domain', style: profile.name, domain: event.target.value } : { mode: 'style', style: profile.name })}>
                    <option value="">The one that fits each write-up</option>
                    {profile.domains.map((domain) => <option key={domain.name} value={domain.name}>{domain.name}</option>)}
                  </select>
                </label>
              )}
              {departures.length > 0 && (
                <Collapse summary={`Sets ${departures.length} write-up guideline${departures.length === 1 ? '' : 's'} aside`}>
                  <ul className="style-departures small">
                    {departures.map((departure) => (
                      <li key={departure.domain + departure.rule}><strong>{departure.rule}</strong> {departure.habit}
                        {domains.length > 1 && <span className="muted"> ({departure.domain})</span>}</li>
                    ))}
                  </ul>
                </Collapse>
              )}
              <Collapse summary="Read the style guide">
                <div className="style-actions">
                  <button type="button" className="btn btn-small" disabled={editing === profile.name} onClick={() => setEditing(profile.name)}>
                    <PencilIcon size={14} /> Edit the guide</button>
                  <button type="button" className="btn btn-small btn-quiet" onClick={() => setRenaming(profile)}>Rename…</button>
                  <button type="button" className="btn btn-small btn-quiet btn-delete" onClick={() => setDeleting(profile)}><TrashIcon size={14} /> Delete…</button>
                </div>
                <StyleTemplate profile={profile} onSaved={onSaved} />
                {editing === profile.name
                  ? <StyleEditor profile={profile} onClose={() => setEditing(null)} onSaved={(next) => { onSaved(next); setEditing(null) }} />
                  : <Profile profile={profile} />}
              </Collapse>
            </StyleOption>
          )
        })}
        <StyleOption on={choice.mode === 'none'} busy={busy} onPick={() => choose({ mode: 'none' })} title="Default style"
          note="No style of your own: write-ups follow the write-up guidelines as they are." />
      </ul>
      {learned && !styles.some((profile) => profile.name === choice.style) &&
        <p className="field-hint" role="status">The style chosen earlier is no longer saved: write-ups choose automatically.</p>}
      <ErrorNote>{error}</ErrorNote>
      {renaming && <RenameStyleDialog profile={renaming} taken={styles.map((item) => item.name)} onClose={() => setRenaming(null)}
        onDone={() => { setRenaming(null); onChanged() }} />}
      {deleting && <DeleteStyleDialog profile={deleting} onClose={() => setDeleting(null)} onDone={() => { setDeleting(null); onChanged() }} />}
    </section>
  )
}

function RenameStyleDialog({ profile, taken, onClose, onDone }: { profile: StyleProfile; taken: string[]; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(profile.name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const next = name.trim()
  const clash = next !== profile.name && taken.includes(next)
  const valid = NAME.test(next) && !clash
  const save = async () => {
    if (!valid || saving) return
    if (next === profile.name) { onClose(); return }
    setSaving(true); setError('')
    try { await api.renameStyle(profile.name, next); onDone() } catch (reason) { setError((reason as Error).message); setSaving(false) }
  }
  return (
    <Modal title="Rename the style" icon={<PencilIcon />} onClose={saving ? undefined : onClose}>
      <form onSubmit={(event) => { event.preventDefault(); void save() }}>
        <label className="field"><span className="field-label">Name</span>
          <input className="text-input" maxLength={60} value={name} onChange={(event) => setName(event.target.value)} />
          <span className="field-hint">{clash ? 'Another saved style has this name.' : !NAME.test(next) ? 'Use letters, digits, spaces, dots and dashes.'
            : 'If write-ups follow this style, they keep following it under its new name.'}</span>
        </label>
        <ErrorNote>{error}</ErrorNote>
        <div className="modal-actions">
          <button type="button" className="btn" disabled={saving} onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving || !valid}>{saving ? 'Saving…' : 'Rename'}</button>
        </div>
      </form>
    </Modal>
  )
}

function DeleteStyleDialog({ profile, onClose, onDone }: { profile: StyleProfile; onClose: () => void; onDone: () => void }) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const remove = async () => {
    setDeleting(true); setError('')
    try { await api.deleteStyle(profile.name); onDone() } catch (reason) { setError((reason as Error).message); setDeleting(false) }
  }
  return (
    <Modal title="Delete the style?" icon={<TrashIcon />} tone="danger" onClose={deleting ? undefined : onClose}>
      <p><strong>{profile.name}</strong></p>
      <p>The style guide and its macros will be permanently deleted. Write-ups already made keep their text; new write-ups
        choose a style automatically if they followed this one.</p>
      <ErrorNote>{error}</ErrorNote>
      <div className="modal-actions">
        <button type="button" className="btn" disabled={deleting} onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-delete" disabled={deleting} onClick={remove}><TrashIcon size={16} /> {deleting ? 'Deleting…' : 'Delete the style'}</button>
      </div>
    </Modal>
  )
}

/** The limits of style.py LIMITS: items per list and characters per item. */
const ITEM = 300
const COUNTS: Record<string, number> = { structure: 12, phrases: 10, names: 12, typesetting: 12 }
const listCount = (key: string) => COUNTS[key] ?? 5

/** One list of a style guide, as text items the author can change, remove or add to. */
function ListEditor({ label, items, max, onChange }: { label: string; items: string[]; max: number; onChange: (items: string[]) => void }) {
  return (
    <div className="field style-edit-list">
      <span className="field-label">{label}</span>
      {items.map((item, index) => (
        <div key={index} className="style-edit-row">
          <textarea className="text-input" rows={2} maxLength={ITEM} value={item} aria-label={`${label}, item ${index + 1}`}
            onChange={(event) => onChange(items.map((value, i) => i === index ? event.target.value : value))} />
          <button type="button" className="icon-btn" aria-label={`Remove item ${index + 1} of ${label}`}
            onClick={() => onChange(items.filter((_, i) => i !== index))}><CloseIcon size={15} /></button>
        </div>
      ))}
      {items.length < max && <button type="button" className="btn btn-small btn-quiet style-edit-add" onClick={() => onChange([...items, ''])}>
        <PlusIcon size={14} /> Add</button>}
    </div>
  )
}

/** One kind of paper of a saved style, as fields the author corrects: what does not sound like them is changed or
 * removed; departures and macros can only be removed (they come from the articles). */
function StyleEditor({ profile, onClose, onSaved }: { profile: StyleProfile; onClose: () => void; onSaved: (profile: StyleProfile) => void }) {
  const [domainName, setDomainName] = useState(profile.domains[0]?.name || '')
  const domain = profile.domains.find((item) => item.name === domainName) || profile.domains[0]
  const initial = (item: DomainStyle): StyleEdit => ({
    audience: item.audience || '', aim: item.aim || '', structure: [...item.structure], phrases: [...item.phrases],
    ...Object.fromEntries(QUESTIONS.map(([key]) => [key, [...(item[key] || [])]])),
    names: (item.names || []).map((entry) => ({ ...entry })), typesetting: (item.typesetting || []).map((entry) => ({ ...entry })),
    departures: (item.departures || []).map((entry) => entry.rule), macros: profile.macros.map((macro) => macro.name),
  })
  const [draft, setDraft] = useState<StyleEdit>(() => domain ? initial(domain) : {})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (domain) setDraft(initial(domain)) }, [domainName])
  if (!domain) return null
  const set = (patch: StyleEdit) => setDraft((value) => ({ ...value, ...patch }))
  const save = async () => {
    setSaving(true); setError('')
    const clean = (items?: string[]) => (items || []).map((item) => item.trim()).filter(Boolean)
    const items: StyleEdit = {
      ...draft, domain: domain.name, structure: clean(draft.structure), phrases: clean(draft.phrases),
      ...Object.fromEntries(QUESTIONS.map(([key]) => [key, clean(draft[key])])),
      names: (draft.names || []).filter((entry) => entry.term.trim() && entry.note.trim()),
      typesetting: (draft.typesetting || []).filter((entry) => entry.habit.trim() && entry.example.trim()),
    }
    try { onSaved((await api.editStyle(profile.name, items)).style) } catch (reason) { setError((reason as Error).message); setSaving(false) }
  }
  const pairs = <K extends 'names' | 'typesetting'>(key: K, fields: [string, string], labels: [string, string], title: string) => {
    const rows = (draft[key] || []) as Record<string, string>[]
    const update = (next: Record<string, string>[]) => set({ [key]: next } as StyleEdit)
    return (
      <div className="field style-edit-list">
        <span className="field-label">{title}</span>
        {rows.map((row, index) => (
          <div key={index} className="style-edit-row style-edit-pair">
            {fields.map((field, f) => <input key={field} className="text-input" maxLength={f ? 200 : ITEM} value={row[field]} placeholder={labels[f]}
              aria-label={`${title}, ${labels[f]} ${index + 1}`}
              onChange={(event) => update(rows.map((value, i) => i === index ? { ...value, [field]: event.target.value } : value))} />)}
            <button type="button" className="icon-btn" aria-label={`Remove entry ${index + 1} of ${title}`}
              onClick={() => update(rows.filter((_, i) => i !== index))}><CloseIcon size={15} /></button>
          </div>
        ))}
        {rows.length < listCount(key) && <button type="button" className="btn btn-small btn-quiet style-edit-add"
          onClick={() => update([...rows, { [fields[0]]: '', [fields[1]]: '' }])}><PlusIcon size={14} /> Add</button>}
      </div>
    )
  }
  return (
    <form className="style-editor" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <p className="field-hint">Correct what does not sound like you. Mathematics is written as in LaTeX, between $ signs.</p>
      {profile.domains.length > 1 && (
        <label className="field style-option-domain"><span className="field-label">Kind of paper</span>
          <select value={domain.name} disabled={saving} onChange={(event) => setDomainName(event.target.value)}>
            {profile.domains.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
          <span className="field-hint">Save before switching: each kind of paper is saved on its own.</span>
        </label>
      )}
      <label className="field"><span className="field-label">Written for</span>
        <textarea rows={2} maxLength={400} value={draft.audience} onChange={(event) => set({ audience: event.target.value })} /></label>
      <label className="field"><span className="field-label">Aim</span>
        <textarea rows={2} maxLength={400} value={draft.aim} onChange={(event) => set({ aim: event.target.value })} /></label>
      <ListEditor label="Typical structure" items={draft.structure || []} max={listCount('structure')} onChange={(structure) => set({ structure })} />
      {QUESTIONS.map(([key, label]) => (
        <ListEditor key={key} label={label} items={draft[key] || []} max={listCount(key)} onChange={(items) => set({ [key]: items } as StyleEdit)} />
      ))}
      {pairs('names', ['term', 'note'], ['name', 'what you use it for'], 'Names you use')}
      {pairs('typesetting', ['habit', 'example'], ['habit', 'example from your articles'], 'Typesetting habits')}
      <ListEditor label="Your phrasings" items={draft.phrases || []} max={listCount('phrases')} onChange={(phrases) => set({ phrases })} />
      {(domain.departures || []).length > 0 && (
        <fieldset className="field style-edit-list">
          <legend className="field-label">Write-up guidelines this style sets aside (untick to follow the guideline again)</legend>
          {(domain.departures || []).map((departure) => (
            <label key={departure.rule} className="style-edit-check">
              <input type="checkbox" checked={draft.departures?.includes(departure.rule)}
                onChange={(event) => set({ departures: event.target.checked ? [...(draft.departures || []), departure.rule]
                  : (draft.departures || []).filter((rule) => rule !== departure.rule) })} />
              <span><strong>{departure.rule}</strong> <Text>{departure.habit}</Text></span>
            </label>
          ))}
        </fieldset>
      )}
      {profile.macros.length > 0 && (
        <fieldset className="field style-edit-list">
          <legend className="field-label">Macros (untick those write-ups should not use; shared by every kind of paper)</legend>
          {profile.macros.map((macro) => (
            <label key={macro.name} className="style-edit-check">
              <input type="checkbox" checked={draft.macros?.includes(macro.name)}
                onChange={(event) => set({ macros: event.target.checked ? [...(draft.macros || []), macro.name]
                  : (draft.macros || []).filter((name) => name !== macro.name) })} />
              <span><code>{macro.definition}</code>{macro.usage && <span className="muted"> · {macro.usage}</span>}</span>
            </label>
          ))}
        </fieldset>
      )}
      <ErrorNote>{error}</ErrorNote>
      <div className="style-template-actions">
        <button type="submit" className="btn btn-small btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save the guide'}</button>
        <button type="button" className="btn btn-small" disabled={saving} onClick={onClose}>Cancel</button>
      </div>
    </form>
  )
}

/** One choice of the style write-ups follow; a saved style brings its settings and its guide. */
function StyleOption({ on, busy, onPick, title, note, children }: {
  on: boolean; busy: boolean; onPick: () => void; title: string; note: string; children?: ReactNode
}) {
  return (
    <li className={'style-option' + (on ? ' style-option-on' : '')}>
      <label className="style-option-head">
        <input type="radio" name="active-style" checked={on} disabled={busy} onChange={onPick} />
        <span className="style-option-text"><span className="style-option-name">{title}</span><span className="muted small">{note}</span></span>
      </label>
      {children && <div className="style-option-more">{children}</div>}
    </li>
  )
}

/** The LaTeX template of a saved style: write-ups in the style build on it unless given their own. */
function StyleTemplate({ profile, onSaved }: { profile: StyleProfile; onSaved: (profile: StyleProfile) => void }) {
  const [files, setFiles] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const current = profile.template || []
  const save = async (paths: string[]) => {
    setBusy(true)
    setError('')
    try {
      onSaved((await api.setStyleTemplate(profile.name, paths)).style)
      setFiles([])
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="style-template">
      <p className="detail-label">LaTeX template</p>
      <p className="small">{current.length
        ? <>Write-ups in this style build on {current.map((file, index) => <span key={file.name}>{index ? ', ' : ''}<code>{file.name}</code></span>)} when you give them no template.</>
        : 'None: write-ups in this style use a standard amsart preamble unless you give them a template.'}</p>
      <FilePicker selected={files} onChange={setFiles} accept={TEMPLATE} label={current.length ? 'Choose other template files' : 'Choose template files'} />
      <div className="style-template-actions">
        {files.length > 0 && <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => save(files)}>Use as this style's template</button>}
        {current.length > 0 && <button type="button" className="btn btn-small btn-quiet" disabled={busy} onClick={() => save([])}>Remove the template</button>}
      </div>
      <ErrorNote>{error}</ErrorNote>
    </section>
  )
}

/** A sentence of a style guide with its mathematics typeset, in the style's own macros (MathMacrosProvider). */
function Text({ children }: { children: string }) {
  return <Markdown inline>{children}</Markdown>
}

/** A style's macros as the math renderer takes them, so that "$\\Z$" in its guide shows as the author's symbol. */
const styleMacros = (profile?: StyleProfile | null) => Object.fromEntries((profile?.macros || []).map((macro) => [macro.name, macro.definition]))

/** One kind of paper: who it is for, how the author explains, names, typesetting and phrasings. */
function Description({ style }: { style: StyleDescription }) {
  const list = (label: string, items: string[] | undefined) => items && items.length > 0 &&
    <><p className="detail-label">{label}</p><ul>{items.map((item, i) => <li key={i}><Text>{item}</Text></li>)}</ul></>
  return (
    <>
      {style.audience && <><p className="detail-label">Written for</p><p><Text>{style.audience}</Text></p></>}
      {style.aim && <><p className="detail-label">Aim</p><p><Text>{style.aim}</Text></p></>}
      {style.structure.length > 0 && <><p className="detail-label">Typical structure</p><ol>{style.structure.map((item, i) => <li key={i}><Text>{item}</Text></li>)}</ol></>}
      {QUESTIONS.map(([key, label]) => <div key={key}>{list(label, style[key])}</div>)}
      {list('How you explain', style.habits)}
      {list('Names you use', style.names?.map((item) => `${item.term}: ${item.note}`))}
      {style.typesetting && style.typesetting.length > 0 && <><p className="detail-label">Typesetting habits</p>
        <ul>{style.typesetting.map((item, i) => <li key={i}><Text>{item.habit}</Text> <code>{item.example}</code></li>)}</ul></>}
      {style.phrases.length > 0 && <><p className="detail-label">Your phrasings</p><ul className="style-phrases">{style.phrases.map((item, i) => <li key={i}>“<Text>{item}</Text>”</li>)}</ul></>}
      {style.departures && style.departures.length > 0 && <><p className="detail-label">Where you depart from the write-up guidelines</p>
        <ul className="style-departures">{style.departures.map((item) => {
          const example = item.examples?.[0] ?? item.example
          return <li key={item.rule}><strong title={item.rule_text}>{item.rule}</strong> <Text>{item.habit}</Text>
            {item.of && item.of > 1 && <span className="muted"> (in {item.articles} of {item.of} articles)</span>}
            {example && <> <code>{example}</code></>}</li>
        })}</ul></>}
    </>
  )
}

/** A saved style as a write-up will read it: per domain, then the macros. */
export function Profile({ profile }: { profile: StyleProfile }) {
  return (
    <MathMacrosProvider definitions={styleMacros(profile)}>
    <div className="style-profile">
      {profile.domains.map((domain) => (
        <section key={domain.name} className="style-domain">
          <h3>{domain.name}</h3>
          {domain.description && <p className="muted small">{domain.description}</p>}
          <p className="muted small">From {domain.articles.join(', ')}.</p>
          <Description style={domain} />
        </section>
      ))}
      {profile.packages && profile.packages.length > 0 && <p className="muted small">Packages your articles load: {profile.packages.map((item) => item.name).join(', ')}.</p>}
      {profile.template && profile.template.length > 0 && <p className="muted small">LaTeX template: {profile.template.map((file) => file.name).join(', ')}.</p>}
      <Collapse summary={`${profile.macros.length} macro${profile.macros.length === 1 ? '' : 's'} you use${profile.environments.length ? `, ${profile.environments.length} theorem environments` : ''}`}>
        <table className="style-macros">
          <tbody>
            {profile.macros.map((macro) => (
              <tr key={macro.name}>
                <td><code>{macro.definition}</code>{macro.usage && <span className="muted"> · {macro.usage}</span>}
                  {macro.packages && macro.packages.length > 0 && <span className="muted"> · needs {macro.packages.join(', ')}</span>}</td>
                <td className="muted">{macro.uses}×</td>
                <td className="muted">{macro.articles.length} article{macro.articles.length === 1 ? '' : 's'}</td>
              </tr>
            ))}
            {profile.environments.map((env) => (
              <tr key={'env-' + env.name}><td><code>{env.definition}</code></td><td className="muted">{env.uses}×</td><td /></tr>
            ))}
          </tbody>
        </table>
      </Collapse>
    </div>
    </MathMacrosProvider>
  )
}

/** A quotation the model gave that is not in the article is left out (style.py, _observe): the filter at work, not a
 * problem of the job. The wording is the runner's; any other warning stays a warning. */
const DROPPED = /^(\d+) name\(s\), typesetting example\(s\), phrasing\(s\) or departure\(s\) quoted for .+ were not found in it and were dropped\.$/

/** The goal a style job gets when the form's note about the articles is empty (style.py, StyleRunner.start). */
const defaultGoal = (name: string) => `Condense my writing style "${name}" from these articles.`

/** What the job is doing, in the author's words rather than the runner's phases. */
function stepText(data: ResearchDetail) {
  const articles = data.articles || []
  const read = Object.keys(data.observations || {}).length
  const next = articles.find((article) => !data.observations?.[article.id])
  const domain = (data.domains || []).find((item) => !item.complete)
  switch (data.phase) {
    case 'scan': return 'Reading your files'
    case 'classify': return 'Sorting your articles by field'
    case 'observe': return next ? `Reading article ${read + 1} of ${articles.length}: ${next.title || next.path}` : 'Reading your articles'
    case 'condense': return domain ? `Merging what your ${domain.name} articles share` : 'Merging what your articles share'
    case 'macros': return 'Noting what you use your macros for'
    default: return 'Saving the style'
  }
}

export function StyleView({ id, tab }: { id: string; tab: string | null }) {
  const tick = useTick(id)
  const app = useApp()
  const { data, error } = useLoad(() => api.research(id), [id, tick])
  const [artifact, setArtifact] = useState<string | null>(null)
  const active = tab || 'style'
  const jobClass = useJobClass()
  const task = taskForJob(app.snapshot, 'research', id)?.task
  const ours = !!task && task.kind === 'research' && task.target === id && ['starting', 'running', 'pausing'].includes(task.state)
  if (!data) {
    return <div className="sheet paper"><div className="sheet-inner">{error ? <ErrorNote>{error}</ErrorNote> : <Loading />}</div></div>
  }
  const dropped = data.warnings.reduce((sum, warning) => sum + Number(DROPPED.exec(warning)?.[1] || 0), 0)
  const warnings = data.warnings.filter((warning) => !DROPPED.test(warning))
  const aside = (
    <div className="ledger">
      <section className="aside-section">
        <h3 className="aside-title">Notes</h3>
        {warnings.length > 0 && <ul className="issue-list">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        {dropped > 0 && <p className="muted small style-note">{dropped} quotation{dropped === 1 ? ' was' : 's were'} left out of the style:
          the model gave {dropped === 1 ? 'it' : 'them'}, but {dropped === 1 ? 'it does' : 'they do'} not occur word for word in your articles.</p>}
        {!warnings.length && !dropped && <p className="muted small">Nothing to report.</p>}
      </section>
      <section className="aside-section">
        <Collapse summary="Usage">
          <UsageMeters budget={data.budget} />
        </Collapse>
      </section>
    </div>
  )
  return (
    <div className={jobClass}>
      <div className="job-main sheet paper">
        <div className="sheet-inner">
          <Header data={data} ours={ours} pausing={task?.state === 'pausing'} />
          <Tabs active={active} onChange={(next) => go('style', id, next === 'style' ? null : next)} tabs={[
            { id: 'style', label: 'Style' },
            { id: 'articles', label: 'Articles', badge: data.articles?.length || undefined },
            { id: 'checks', label: 'Notes', badge: warnings.length || undefined, narrowOnly: true },
            { id: 'files', label: 'Files', badge: data.artifacts.length || undefined },
          ]} />
          {active === 'style' && (
            <div className="document">
              {data.running && <LiveStyle data={data} ours={ours} />}
              {data.profile ? (
                <>
                  <Saved data={data} />
                  <Profile profile={data.profile} />
                </>
              ) : !data.running && <Markdown>{data.report}</Markdown>}
            </div>
          )}
          {active === 'articles' && <Articles data={data} />}
          {active === 'checks' && <div className="ledger-tab">{aside}</div>}
          {active === 'files' && <Files names={data.artifacts} onOpen={setArtifact} />}
        </div>
      </div>
      <JobAside label="Notes">{aside}</JobAside>
      {artifact && <ArtifactViewer job="research" id={id} name={artifact} onClose={() => setArtifact(null)} />}
    </div>
  )
}

function Header({ data, ours, pausing }: { data: ResearchDetail; ours: boolean; pausing: boolean }) {
  const app = useApp()
  const look = researchLook(data.status, data.running)
  const [error, setError] = useState('')
  const busy = capacityFull(app.snapshot, app.status)
  const ownTask = taskForJob(app.snapshot, 'research', data.id)?.task
  const queued = app.snapshot.queue.some((item) => item.target === data.id)
  const resumable = data.progress?.resumable ?? researchResumable(data.status)
  const act = (action: Promise<unknown>) => { setError(''); action.catch((reason: Error) => setError(reason.message)) }
  const current = STEPS.indexOf(data.phase as typeof STEPS[number])
  const name = data.style_name || ''
  return (
    <header className="job-head">
      <div className="job-status">
        <Square variant={look.variant} size={18} label={look.label} />
        <span className="job-status-label">{look.label}</span>
      </div>
      <h1 className="style-job-title">{data.profile ? 'Your style' : data.running ? 'Learning the style' : 'Style'} “{name}”</h1>
      {data.goal !== defaultGoal(name) && <div className="style-job-about muted"><Markdown>{data.goal}</Markdown></div>}
      <ol className="phases phases-wide" aria-label="Workflow">
        {STEPS.map((step, index) => {
          const variant: Variant = index < current || data.phase === 'done' ? 'complete' : index === current ? (data.running ? 'running' : 'paused') : 'ready'
          return <li key={step} className={'phase phase-' + variant}><Square variant={variant} size={10} />{STEP_NAMES[step]}</li>
        })}
      </ol>
      <WorkProgressNote progress={data.progress} status={data.status} running={data.running} taskId={ownTask?.id} />
      <div className="job-facts">
        {data.sources.map((source) => <span key={source.id} className="fact-file">{source.path}</span>)}
        <span>Started {ago(data.created_at)}</span>
      </div>
      <div className="job-actions">
        {ours ? (
          <button type="button" className="btn" disabled={pausing} onClick={() => act(api.pause(ownTask!.id))}><PauseIcon size={16} /> {pausing ? 'Pausing…' : 'Pause'}</button>
        ) : resumable ? (
          <button type="button" className="btn btn-primary" disabled={queued} onClick={() => act(api.resumeResearch(data.id, true))}>
            <PlayIcon size={16} /> {queued ? 'Waiting in the queue' : busy ? 'Queue resume' : 'Resume saved work'}
          </button>
        ) : null}
      </div>
      {!ours && busy && resumable && !queued && <BusyNote queue />}
      <ErrorNote>{error}</ErrorNote>
    </header>
  )
}

/** The running job: what it is doing in plain words, with the model's own output one click away. */
function LiveStyle({ data, ours }: { data: ResearchDetail; ours: boolean }) {
  const app = useApp()
  return (
    <div className="live" id="live">
      <p className="live-head"><Square variant="running" size={14} /> {stepText(data)}</p>
      <p className="muted small style-note">You can leave this page: the style is saved when the work ends, and it stays in the list on the left.</p>
      {ours && <ActivityLog items={taskForJob(app.snapshot, 'research', data.id)?.activity || []} />}
      {data.live && (
        <Collapse summary="Show the model's output">
          <LiveOutput id={data.id} role={data.live.role} file={data.live.file} />
        </Collapse>
      )}
    </div>
  )
}

function LiveOutput({ id, role, file }: { id: string; role: string; file: string }) {
  const stream = useLiveStream('research', id, file)
  return <StreamBody stream={stream} role={role} live />
}

/** The end of a job: the style is saved, and whether write-ups follow it. */
function Saved({ data }: { data: ResearchDetail }) {
  const [choice, setChoice] = useState<StyleChoice | null>(null)
  useEffect(() => { api.styles().then((result) => setChoice(result.choice), () => undefined) }, [data.saved_to])
  const name = data.style_name
  const followed = !choice ? ''
    : choice.mode === 'auto' ? 'Write-ups follow it whenever it fits their material.'
    : choice.mode === 'none' ? 'Your write-ups follow the default style for now.'
    : choice.style === name ? 'Your write-ups follow it.'
    : `Your write-ups follow “${choice.style}” for now.`
  return (
    <div className="style-done">
      <p><strong>Saved as “{name}”.</strong> {followed}</p>
      <p>The guide below is a model description of how you write: read it, and correct what does not sound like you
        with <strong>Edit the guide</strong> on the Writing Styles page, where you can also rename or delete the style.</p>
      <p><a className="btn btn-small" href={href('style')}>Open the Writing Styles page</a></p>
    </div>
  )
}

function Articles({ data }: { data: ResearchDetail }) {
  if (!data.articles?.length) return <p className="empty-note">The articles are read first.</p>
  const domainOf = new Map((data.domains || []).flatMap((domain) => domain.articles.map((id) => [id, domain.name] as const)))
  return (
    <MathMacrosProvider definitions={styleMacros(data.profile)}>
    <ol className="outline-list">
      {data.articles.map((article) => {
        const seen = data.observations?.[article.id]
        return (
          <li key={article.id}>
            <strong>{article.title || article.path}</strong>{domainOf.has(article.id) && <span className="muted"> · {domainOf.get(article.id)}</span>}
            <p className="muted small">{article.path}, {article.lines} lines{article.source_kind === 'pdf_text'
              ? ' of extracted PDF text.' : `, ${article.headings.filter((h) => !h.startsWith(' ')).length} sections.`}
              {article.source_archive && ' Archive member; this may be an article fragment, and includes/macros were not assembled.'}
              {article.inputs.length > 0 && ` Includes ${article.inputs.join(', ')}, which was not read.`}</p>
            {seen ? (
              <Collapse summary="What the model noticed">
                <Description style={seen} />
              </Collapse>
            ) : <p className="muted small">Not read yet.</p>}
          </li>
        )
      })}
    </ol>
    </MathMacrosProvider>
  )
}
