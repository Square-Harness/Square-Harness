// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useState, type FormEvent } from 'react'
import { api, type ReviewVariant } from '../api'
import { WorkspaceSwitch } from '../components/Shell'
import { Collapse, ErrorNote, FilePicker, NumberField, OptionalNumberField, Toggle } from '../components/common'
import { EffortSlider, effortLimits, type EffortLevel } from '../components/Effort'
import { useDropTarget } from '../drop'
import { go } from '../router'
import { Glyph } from '../components/Glyph'
import { useApp } from '../store'
import { capacityFull } from '../concurrency'
import { FIXED_VARIANTS, VARIANT_EFFORT, VARIANT_INPUT_SCALE, VARIANT_NAMES, VARIANT_SCALE, VARIANTS } from '../review'
import { BusyNote, queuedMessage } from './shared'

type Kind = 'literature' | 'referee'
type Copy = { title: string; lede: string; placeholder: string; pinHint: string; field: string; files: string; noun: string }

const LITERATURE: Copy = {
  title: 'Write a literature review',
  lede: 'Trace a mathematical question through its main results, proofs and remaining questions. The review links each cited work and indicates where it could consult the paper itself.',
  placeholder: 'Observability and null controllability of the heat equation: the main results, from the classical Carleman estimates to recent work.',
  pinHint: 'Optional. A pinned manuscript helps define the topic. Public searches use mathematical terms.',
  field: 'Scope and request', files: 'Pinned sources', noun: 'literature review',
}

// The Review notebook holds four kinds of review; the engine kind stays 'referee'.
const REVIEW: Record<ReviewVariant, Copy> = {
  quick: {
    title: 'Check a proof',
    lede: 'Paste a statement and its proof, or pin a file and say which result. Independent passes read the proof line by line, adversarially and against the results it uses; each alleged error is re-checked, and every objection points to the proof lines. The proof is never rewritten.',
    placeholder: 'Lemma. Let (u_n) be a bounded sequence in H^1(0,1). Then a subsequence converges strongly in L^2(0,1).\nProof. …',
    pinHint: 'Optional: instead of pasting, pin a TeX, Markdown, text or PDF file and name the result below.',
    field: 'Statement and proof', files: 'Or pin a file', noun: 'proof check',
  },
  review: {
    title: 'Review a manuscript',
    lede: 'Pin the manuscript. The harness reads the whole paper and checks every proof, appendices included, the main results first; it reads across the paper for inconsistencies and unproved claims, and gives every objection a second opinion (a defence of the paper, then a judge). The report is rewritten as checks finish: a first draft is readable within minutes. There is one depth, so no effort to choose. Typos are a separate, faster job: the Typo Check.',
    placeholder: 'Optional focus, e.g. concentrate on the proof of Theorem 1.4 and the lemmas it uses.',
    pinHint: 'Pin the manuscript (a TeX source gives the best results; PDF, Markdown and text work too).',
    field: 'Focus of the review (optional)', files: 'Manuscript', noun: 'review',
  },
  typos: {
    title: 'Check the typos of a manuscript',
    lede: 'Pin the manuscript. The harness proofreads the whole paper part by part for typos, misprinted formulas, undefined symbols, wrong cross-references and unclear sentences, then checks every suggested correction against the paper. No proof is checked: ask for a Review for that.',
    placeholder: 'Optional focus, e.g. only Sections 3 and 4.',
    pinHint: 'Pin the manuscript (a TeX source gives the best typo check; PDF, Markdown and text work too).',
    field: 'Focus of the typo check (optional)', files: 'Manuscript', noun: 'typo check',
  },
  journal: {
    title: 'Review a manuscript',
    lede: 'The former detailed review, now the review.',
    placeholder: '', pinHint: '', field: 'Focus of the review (optional)', files: 'Manuscript', noun: 'review',
  },
  explain: {
    title: 'Explain a result',
    lede: 'Paste a lemma or theorem with its proof, or pin a file and say which result. You get what it says, the idea, a line-by-line walkthrough for short proofs, the standard facts used and where each hypothesis matters: precise, for a mathematician from another field.',
    placeholder: 'Lemma. …\nProof. …',
    pinHint: 'Optional: instead of pasting, pin a file and name the result below.',
    field: 'Result and proof', files: 'Or pin a file', noun: 'explanation',
  },
}

const VARIANT_KEY = 'square.review.variant'

function savedVariant(): ReviewVariant {
  try {
    const value = window.localStorage.getItem(VARIANT_KEY)
    return VARIANTS.includes(value as ReviewVariant) ? (value as ReviewVariant) : 'quick'
  } catch {
    return 'quick'
  }
}

export function ResearchNew({ kind }: { kind: Kind }) {
  const { status, snapshot } = useApp()
  const d = status?.defaults
  const [variant, setVariant] = useState<ReviewVariant>(savedVariant)
  const review = kind === 'referee'
  const copy = review ? REVIEW[variant] : LITERATURE
  const manuscript = review && FIXED_VARIANTS.includes(variant)
  const pasted = review && !manuscript
  const [goal, setGoal] = useState('')
  const [target, setTarget] = useState('')
  const [files, setFiles] = useState<string[]>([])
  const [rounds, setRounds] = useState(3)
  const [tokens, setTokens] = useState<number | null>(null)
  const [minutes, setMinutes] = useState(15)
  const [inputTokens, setInputTokens] = useState<number | null>(null)
  const [requests, setRequests] = useState(12)
  const [chars, setChars] = useState(30000)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuedNote, setQueuedNote] = useState('')
  const [online, setOnline] = useState(true)
  const [level, setLevel] = useState<EffortLevel>(review ? VARIANT_EFFORT[savedVariant()] : 'medium')

  const uploading = useDropTarget(!review ? 'Pinned as sources for the report' : manuscript ? 'Pinned as the manuscript' : 'Pinned as the source',
    (paths) => setFiles((items) => [...new Set([...items, ...paths])]))

  const scaled = (next: EffortLevel, kind: ReviewVariant = variant) => {
    const limits = effortLimits(review ? 'research' : 'literature', d!, next)
    const ds4 = d?.api_profile === 'deepinfra'
    const scale = review && !ds4 ? VARIANT_SCALE[kind] : 1
    const input = review && ds4 && limits.input_tokens ? VARIANT_INPUT_SCALE[kind] : 1
    return { ...limits, seconds: limits.seconds * scale, input_tokens: limits.input_tokens && limits.input_tokens * input }
  }
  const applyLevel = (next: EffortLevel, kind: ReviewVariant = variant) => {
    if (!d) return
    const limits = scaled(next, kind)
    setRounds(limits.rounds!)
    setTokens(limits.tokens)
    setMinutes(limits.seconds / 60)
    setInputTokens(limits.input_tokens!)
    setRequests(limits.requests!)
    setChars(limits.chars!)
  }
  useEffect(() => {
    if (!d) return
    applyLevel(level)
    // Only when the launch defaults arrive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d])
  // Online search is on unless the interface was launched with --offline.
  useEffect(() => { setOnline(!!status?.online) }, [status?.online])

  const preset = d ? scaled(level) : null
  const custom = !!preset && (rounds !== preset.rounds || tokens !== preset.tokens || minutes !== preset.seconds / 60
    || inputTokens !== preset.input_tokens || requests !== preset.requests || chars !== preset.chars)
  const chooseLevel = (next: EffortLevel) => {
    setLevel(next)
    applyLevel(next)
  }
  const chooseVariant = (next: ReviewVariant) => {
    setVariant(next)
    setLevel(VARIANT_EFFORT[next])
    applyLevel(VARIANT_EFFORT[next], next)
    setError('')
    try { window.localStorage.setItem(VARIANT_KEY, next) } catch { /* a convenience only */ }
  }
  // Quick checks and explanations never search: they read the proof and what it cites.
  // No review searches online: the paper review suggests a literature review instead.
  const offline = review
  const searching = online && !status?.online_locked && !offline

  const running = capacityFull(snapshot, status)
  const noun = copy.noun
  const fileNamed = /\.(tex|md|txt|pdf)\b/.test(goal)
  const ready = !review ? !!goal.trim()
    : manuscript ? files.length > 0 || fileNamed
      : files.length ? !!(goal.trim() || target.trim()) : !!goal.trim()
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (uploading) return
    setBusy(true)
    setError('')
    setQueuedNote('')
    const request = goal.trim() || (variant === 'typos' && review ? 'Check the typos and the presentation of this manuscript.'
      : manuscript ? 'Referee this manuscript for a mathematics journal.'
      : `${variant === 'quick' ? 'Check the proof of' : 'Explain'} ${target.trim()}.`)
    try {
      const result = await api.startResearch({
        kind, goal: request, source_files: files, rounds, tokens, seconds: minutes * 60,
        input_tokens: inputTokens, requests, chars, online: status?.online_locked ? undefined : searching, queue: true,
        ...(review ? { variant, target: files.length ? target.trim() : '' } : {}),
      })
      if (result.task.state === 'queued') {
        setQueuedNote(queuedMessage(result.task.title))
        setGoal('')
        setTarget('')
        setFiles([])
      } else if (result.id) go(kind, result.id)
      else setError(result.task.error || `The ${noun} did not start.`)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sheet paper">
      <form className="sheet-inner compose" onSubmit={submit} noValidate>
        <div className="entry"><span className="in-margin" /><WorkspaceSwitch /></div>
        <div className="entry">
          <span className="in-margin margin-glyph" aria-hidden="true"><Glyph mode={review && variant === 'explain' ? 'explain' : kind} /></span>
          <div>
            {review && (
              <div className="segmented" role="group" aria-label="Kind of review">
                {VARIANTS.map((item) => (
                  <button key={item} type="button" aria-pressed={variant === item} onClick={() => chooseVariant(item)}>
                    {VARIANT_NAMES[item]}
                  </button>
                ))}
              </div>
            )}
            <h1 className="page-title">{copy.title}</h1>
            <p className="lede">{copy.lede}</p>
            {pasted ? (
              <p className="network-note">Works on this computer only: the {noun} reads the text and the statements it cites, and searches nothing online.</p>
            ) : offline ? (
              <p className="network-note">The {noun} searches nothing online.{variant === 'typos' ? '' : ' To place the paper\'s results in the literature, ask the Assistant for a review (@referee) in a conversation: it starts a literature review beside it.'}</p>
            ) : (
              <p className={'network-note' + (searching ? ' network-online' : '')}>
                {searching
                  ? `Online search is on for this ${noun}: search queries and paper downloads leave this computer. Search uses short public topic queries; manuscript text stays local. Untick Search online below to keep it offline.`
                  : `Online search is off: the ${noun} uses pinned files and papers already cached in this workspace, and will say that its coverage is limited.`}
              </p>
            )}
          </div>
        </div>
        <div className="entry">
          <span className="in-margin" />
          <label className="field">
            <span className="field-label">{copy.field}</span>
            <textarea className="goal-input" rows={pasted ? 9 : 5} value={goal} onChange={(event) => setGoal(event.target.value)}
              placeholder={copy.placeholder} required={!manuscript} />
          </label>
        </div>
        <div className="entry">
          <span className="in-margin" />
          <div className="field">
            <span className="field-label">{copy.files}</span>
            <FilePicker selected={files} onChange={setFiles} allowPdf allowImages />
            <span className="field-hint">{copy.pinHint}</span>
          </div>
        </div>
        {pasted && files.length > 0 && (
          <div className="entry">
            <span className="in-margin" />
            <label className="field">
              <span className="field-label">Which result</span>
              <span className="field-input"><input type="text" value={target} onChange={(event) => setTarget(event.target.value)}
                placeholder="Lemma 3.2, Theorem 1.4, or a label such as lem:compact" /></span>
              <span className="field-hint">The harness finds the statement, its proof and the results it cites in the pinned file.</span>
            </label>
          </div>
        )}
        <div className="entry">
          <span className="in-margin" />
          <div>
            {d && !manuscript && <EffortSlider kind={review ? 'research' : 'literature'} defaults={d} level={level} onLevel={chooseLevel} custom={custom} />}
            {manuscript && <p className="field-hint">{variant === 'typos' ? 'A typo check' : 'A review'} has one depth: there is no effort to choose. It reads the whole paper in every call and allows {d?.api_profile === 'deepinfra' ? 'four times the input' : 'twice the working time'} of the other modes.</p>}
            {!offline && (
              <Toggle label="Search online" checked={searching} onChange={setOnline} disabled={status?.online_locked}
                hint={status?.online_locked ? 'Launched with --offline: online search stays off.' : (kind === 'literature' ? 'zbMATH, arXiv, Semantic Scholar and Crossref, and open full texts of key papers when available.' : 'Checks cited results and compares the contribution with arXiv, Semantic Scholar and zbMATH.')} />
            )}
            <Collapse summary="Advanced limits">
              <div className="field-grid">
                {!manuscript && <NumberField label={review ? 'Tries (sets passes and depth)' : 'Investigation rounds'} value={rounds} onChange={setRounds} min={1} max={100} />}
                <OptionalNumberField label="Total output token limit" value={tokens} onChange={setTokens} hint="Optional. Leave blank for no cumulative limit." />
                <NumberField label="Time" value={minutes} onChange={setMinutes} min={0.001} step={0.001} suffix="min" />
                <OptionalNumberField label="Total input token limit" value={inputTokens} onChange={setInputTokens} step={10000} hint="Optional. Repeated input counts again; this is not the context size." />
                {!offline && <NumberField label="Web requests" value={requests} onChange={setRequests} min={0} max={1000} hint={searching ? 'External retrievals' : 'Unused while offline'} />}
                {review && !pasted && <NumberField label="Evidence characters" value={chars} onChange={setChars} min={1000} max={1000000} step={1000} hint="Returned by literature tools" />}
              </div>
            </Collapse>
          </div>
        </div>
        <div className="entry">
          <span className="in-margin" />
          <div className="form-actions">
            <ErrorNote>{error}</ErrorNote>
            {queuedNote && <p className="queued-note" role="status">{queuedNote}</p>}
            {running && <BusyNote queue />}
            <button type="submit" className="btn btn-primary btn-large" disabled={busy || !!uploading || !ready}>
              {uploading ? 'Uploading…' : busy ? 'Starting…' : running ? `Add ${noun} to the queue` : `Start ${noun}`}
            </button>
            {manuscript && !files.length && <p className="field-hint">Pin the manuscript, or name its file in the request.</p>}
            {pasted && files.length > 0 && !goal.trim() && !target.trim() && <p className="field-hint">Name the result to {variant === 'quick' ? 'check' : 'explain'}.</p>}
          </div>
        </div>
      </form>
    </div>
  )
}
