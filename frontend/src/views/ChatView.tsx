// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 ENPC / SquareHarness

import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactElement } from 'react'
import { api, type LiveTurn, type RouteMode, type TranscriptItem } from '../api'
import { capacityFull, chatWork, taskById } from '../concurrency'
import { assistantResumable, routeActivity } from '../assistant'
import { assistantBudgetDisplay } from '../assistantBudget'
import { Collapse, ErrorNote, Loading, Toggle, WorkProgressNote, useLoad } from '../components/common'
import { CloseIcon, FileIcon, PauseIcon, PlayIcon, SendIcon } from '../components/Icons'
import { EFFORTS, effortLimits, isLevel, type EffortKind, type EffortLevel } from '../components/Effort'
import { Glyph, type Cover } from '../components/Glyph'
import { ModeMessage } from '../components/ModeMessage'
import { ProjectSelector } from '../components/Projects'
import { ImageGallery, isImagePath, PinnedImages } from '../components/ImageAttachments'
import { callPayload, coverOf, MODE_CALLS, typedCall, validCall, type ModeCall } from '../modeCalls'
import { Inline, Markdown, MathMacrosProvider, StreamingMarkdown } from '../components/Markdown'
import { proofLook, researchLook, Square, type Variant } from '../components/Square'
import { useDropTarget } from '../drop'
import { artifactsOf, displayOf, type Artifact } from '../artifacts'
import { ago, count } from '../format'
import { MODES } from '../modes'
import { WorkspaceSwitch } from '../components/Shell'
import { go, href } from '../router'
import { refreshLists, setProjectFilter, useApp, useTick } from '../store'
import { BusyNote, Thinking, toolLine } from './shared'

type ChatMode = 'critic' | 'explore' | 'free'

const INTRO: Record<ChatMode, { title: string; text: string; examples: string[] }> = {
  free: {
    title: 'What are you working on?',
    text: 'Discuss a question, share a proof or attach your notes. The main agent chooses the appropriate skills and brings their results back into the conversation.',
    examples: [],
  },
  critic: {
    title: 'Critique an argument',
    text: 'Paste a statement with its proof, or name a file in the workspace. The critic looks for the first false or unjustified step and tries counterexamples before suggesting repairs.',
    examples: [
      'Is every bounded sequence in $L^2(0,1)$ strongly precompact? Justify your answer.',
      'Read manuscript.tex and identify the first unsupported implication.',
    ],
  },
  explore: {
    title: 'Explore a question',
    text: 'Ask about approaches, reformulations or connections. The model labels heuristic steps and conjectures, so you can tell them from arguments.',
    examples: [
      'Which compactness arguments show that a bounded sequence in $H^1(0,1)$ has a subsequence converging in $L^2(0,1)$?',
      'How could one approach an observability inequality for the heat equation on a bounded domain?',
    ],
  },
}

// How a card names what the model started. Critic and explore are both an answer here;
// a literature check also answers here, with its evidence.
type Kind = 'answer' | 'check' | 'prove' | 'literature' | 'referee' | 'detailed_review' | 'quick_review' | 'explain' | 'writeup' | 'summarize'
  | 'python_lab' | 'sanity_check'
// `cover` names the mark (components/Glyph.tsx) and its colour class.
const KINDS: Record<Kind, { noun: string; label: string; cover: Cover }> = {
  answer: { noun: 'an answer', label: 'Answer', cover: 'free' },
  check: { noun: 'a literature check', label: 'Literature check', cover: 'check' },
  prove: { noun: 'a proof', label: 'Proof', cover: 'prove' },
  literature: { noun: 'a literature report', label: 'Literature report', cover: 'literature' },
  referee: { noun: 'a review', label: 'Review', cover: 'referee' },
  detailed_review: { noun: 'a detailed review', label: 'Detailed review', cover: 'referee' },
  quick_review: { noun: 'a quick review', label: 'Quick review', cover: 'referee' },
  explain: { noun: 'an explanation', label: 'Explanation', cover: 'referee' },
  writeup: { noun: 'a write-up', label: 'Write-up', cover: 'writeup' },
  summarize: { noun: 'a summary', label: 'Summary', cover: 'writeup' },
  python_lab: { noun: 'a Python Lab run', label: 'Python Lab', cover: 'python_lab' },
  sanity_check: { noun: 'a numerical sanity check', label: 'Numerical sanity check', cover: 'sanity_check' },
}
const kindOf = (mode: RouteMode | 'free'): Kind => (mode === 'critic' || mode === 'explore' || mode === 'free' ? 'answer' : mode)
const effortKind = (mode: RouteMode): EffortKind | null =>
  mode === 'prove' ? 'proof' : mode === 'writeup' || mode === 'summarize' ? 'writeup' : ['literature', 'referee', 'detailed_review', 'quick_review', 'explain'].includes(mode) ? 'research' : null
// Quick reviews and explanations are pages of the Review notebook; summaries are write-ups.
// Python Lab and sanity checks answer in the conversation: they have no notebook page.
type Paged = Exclude<RouteMode, 'check' | 'python_lab' | 'sanity_check'>
const paged = (mode: RouteMode): mode is Paged => mode !== 'check' && mode !== 'python_lab' && mode !== 'sanity_check'
const pageOf = (mode: Paged) => (mode === 'quick_review' || mode === 'explain' || mode === 'detailed_review' ? 'referee' : mode === 'summarize' ? 'writeup' : mode)
const effortLabel = (effort?: string) => EFFORTS.find((item) => item.id === (isLevel(effort) ? effort : 'medium'))!.label

export function ChatView({ mode, id }: { mode: ChatMode; id: string | null }) {
  const app = useApp()
  const tick = useTick(id)
  // Reloaded when a turn ends too: its last save may have arrived before the turn was marked done.
  const [ended, setEnded] = useState(0)
  const { data, error } = useLoad(() => (id ? api.chat(id) : Promise.resolve(null)), [id, tick, ended])
  const work = chatWork(app.snapshot, id)
  const task = work.active?.task
  const ours = !!task
  const current = work.active?.live?.chat === id ? work.active.live : null
  // Keep a finished turn on screen until the saved transcript replaces it.
  const [lingering, setLingering] = useState<LiveTurn | null>(null)
  const previous = useRef<LiveTurn | null>(null)
  useEffect(() => {
    if (!current && previous.current) { setLingering(previous.current); setEnded((n) => n + 1) }
    previous.current = current
  }, [current])
  useEffect(() => { setLingering(null) }, [data])
  const live = current || lingering
  const routing = work.routing
  const [draft, setDraft] = useState('')
  const [selectedCall, setSelectedCall] = useState<ModeCall | null>(null)
  // null: no effort named, so the harness picks its default for the mode.
  const [selectedEffort, setSelectedEffort] = useState<EffortLevel | null>(null)
  const selectCall = (next: ModeCall | null) => {
    if (next?.mode !== selectedCall?.mode) setSelectedEffort(null)
    setSelectedCall(next)
  }
  const [attachments, setAttachments] = useState<string[]>([])
  const defaultProject = app.projects?.some((project) => project.id === app.projectFilter) ? app.projectFilter : null
  const [newProject, setNewProject] = useState<string | null>(defaultProject)
  const [projectOverride, setProjectOverride] = useState<string | null | undefined>(undefined)
  const [projectSaving, setProjectSaving] = useState(false)
  useEffect(() => { setProjectOverride(undefined) }, [data?.project_id])
  const savedProject = data ? data.project_id || null : app.chats?.find((chat) => chat.id === id)?.project_id || null
  const currentProject = id ? (projectOverride !== undefined ? projectOverride : savedProject) : newProject
  useEffect(() => { if (!id && !createdChat.current) setNewProject(defaultProject) }, [defaultProject, id])
  const [sendError, setSendError] = useState('')
  const [sending, setSending] = useState(false)
  const createdChat = useRef<string | null>(null)
  useEffect(() => {
    const prepare = (event: Event) => {
      if (sending) { event.preventDefault(); setSendError('Wait for the message to be sent before changing projects.'); return }
      if ((draft.trim() || attachments.length) && !window.confirm('Start a new conversation in this project? Your unsent message and attachments will be cleared.')) {
        event.preventDefault(); return
      }
      setDraft(''); setAttachments([]); setSelectedCall(null); setSelectedEffort(null); setSendError(''); createdChat.current = null
    }
    window.addEventListener('square-new-project-chat', prepare)
    return () => window.removeEventListener('square-new-project-chat', prepare)
  }, [draft, attachments, sending])
  const [resuming, setResuming] = useState(false)
  const [think, setThink] = useState<boolean>(app.status?.defaults.think ?? true)
  // Online search is always on, unless the interface was launched --offline.
  const locked = !!app.status?.online_locked
  const scroller = useRef<HTMLDivElement>(null)
  const composer = useRef<HTMLFormElement>(null)
  const pinned = useRef(true)

  const uploading = useDropTarget('Attached to your next message', (paths) => setAttachments((items) => [...new Set([...items, ...paths])]),
    currentProject)
  useEffect(() => { if (data) setThink(data.settings.think) }, [data?.id])
  // Conversations saved with search off (earlier versions) get it back.
  useEffect(() => {
    if (id && data && !locked && !data.settings.online) api.chatSettings(id, { online: true }).catch(() => undefined)
  }, [id, data?.settings.online, locked])

  // Follow new output while the reader is at the bottom; never yank them back up.
  const liveSize = live ? live.steps.reduce((n, step) => n + (step.type === 'call' ? step.text.length + step.thinking.length : 1), 0) : 0
  useLayoutEffect(() => {
    const element = scroller.current
    if (element && pinned.current) element.scrollTop = element.scrollHeight
  }, [data, liveSize, routing])

  const free = mode === 'free'
  const hasImages = attachments.some(isImagePath)
  const budgetDisplay = data?.assistant_budget ? assistantBudgetDisplay(data.assistant_budget) : null
  const paused = free && assistantResumable(data)
  const send = async (text?: string) => {
    let raw = text ?? draft
    let imageOnlyCall: ModeCall | null = null
    if (hasImages) {
      const selected = validCall(raw, selectedCall) ? selectedCall : typedCall(raw, true)
      const body = selected ? (raw.slice(0, selected.start) + raw.slice(selected.end)).trim() : raw.trim()
      if (!body) {
        imageOnlyCall = selected
        raw = (raw.trimEnd() ? raw.trimEnd() + '\n\n' : '') + 'Please examine the attached images.'
      }
    }
    // A message that is only a mode name ("@summarize") is that call, picked from the menu or not.
    const call = validCall(raw, selectedCall) ? selectedCall : imageOnlyCall || (raw.trim().match(/^@[a-z_]+$/) ? typedCall(raw, true) : null)
    const modeCall = free && text === undefined ? callPayload(raw, call, call === selectedCall ? selectedEffort ?? undefined : undefined) : undefined
    const content = modeCall ? raw : raw.trim()
    // A new message while the Assistant is paused sets that turn aside (the server says so in the conversation).
    if (!content.trim() || sending || resuming || projectSaving || uploading || work.blocked || (hasImages && !app.status?.image_support)) return
    setSending(true)
    setSendError('')
    try {
      let chatId = id || createdChat.current
      if (!chatId) { chatId = (await api.createChat(mode, think, locked ? undefined : true, newProject)).id; createdChat.current = chatId }
      if (free) await api.route(chatId, content, attachments, modeCall)
      else await api.send(chatId, content, attachments)
      setDraft('')
      setSelectedCall(null)
      setAttachments([])
      pinned.current = true
      if (!id) go(mode, chatId)
    } catch (reason) {
      setSendError((reason as Error).message)
    } finally {
      setSending(false)
    }
  }
  const submit = (event: FormEvent) => { event.preventDefault(); send() }
  const pickSkill = (next: RouteMode) => {
    const currentCall = validCall(draft, selectedCall) ? selectedCall : typedCall(draft, true)
    const message = currentCall ? (draft.slice(0, currentCall.start) + draft.slice(currentCall.end)).trimStart() : draft
    const token = '@' + next
    setDraft(token + ' ' + message)
    selectCall({ mode: next, start: 0, end: token.length })
    window.requestAnimationFrame(() => {
      const input = composer.current?.querySelector('textarea')
      input?.focus(); input?.setSelectionRange(token.length + 1, token.length + 1)
    })
  }
  const changeProject = async (project: string | null) => {
    if (sending || resuming || projectSaving || work.blocked) return
    const chatId = id || createdChat.current
    if (chatId) {
      setProjectSaving(true); setSendError('')
      try {
        await api.chatProject(chatId, project)
        if (id) setProjectOverride(project)
        refreshLists()
      } catch (reason) { setSendError((reason as Error).message); return }
      finally { setProjectSaving(false) }
    }
    if (!id) setNewProject(project)
    setProjectFilter(project || 'all')
  }
  const keys = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send() }
  }
  const changeThink = (value: boolean) => {
    setThink(value)
    if (id) api.chatSettings(id, { think: value }).catch((reason: Error) => setSendError(reason.message))
  }
  const resume = async () => {
    if (!id || resuming) return
    setResuming(true)
    setSendError('')
    try { await api.resumeAssistant(id) } catch (reason) { setSendError((reason as Error).message) } finally { setResuming(false) }
  }
  const taskError = app.snapshot.task?.target === id && app.snapshot.task.state === 'error' ? app.snapshot.task.error : ''
  const transcript = data?.transcript || []
  // Another conversation's work can be queued; this conversation has one active turn.
  const blocked = work.blocked

  return (
    <MathMacrosProvider definitions={data?.math_macros}>
    <div className="chat">
      <div className="chat-scroll sheet paper" ref={scroller}
        onScroll={(event) => { const el = event.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }}>
        <div className="sheet-inner chat-inner">
          {!id && <Intro mode={mode} onPick={(text) => { setDraft(text); setSelectedCall(null) }} onSkill={pickSkill} />}
          {id && error && <ErrorNote>{error}</ErrorNote>}
          {id && !data && !error && <Loading />}
          {data && !transcript.length && !live && !routing && <Intro mode={mode} onPick={(text) => { setDraft(text); setSelectedCall(null) }} onSkill={pickSkill} />}
          {id && <Transcript items={transcript} mode={mode} chatId={id} />}
          {routing && !live && (
            <div className="entry message message-model">
              <span className="in-margin speaker speaker-model mode-free">∀</span>
              <p className="muted routing"><Square variant="running" size={12} /> Reading your request…</p>
            </div>
          )}
          {live && <LiveTurnView live={live} mode={mode} />}
          {free && data?.assistant_progress && <div className="entry">
            <span className="in-margin" />
            <WorkProgressNote progress={data.assistant_progress} status={data.assistant_status || ''} running={ours} />
          </div>}
          {free && assistantResumable(data) && !ours && (
            <div className="entry message message-model">
              <span className="in-margin speaker speaker-model mode-free">{MODES.free.glyph}</span>
              <p className="notice">The assistant is paused. Its work is saved: resume from where it stopped, or send a new message to set this turn aside.</p>
            </div>
          )}
        </div>
      </div>
      <form className="composer" ref={composer} onSubmit={submit}>
        <div className="composer-top">
          <WorkspaceSwitch />
          <ProjectSelector value={currentProject} onChange={(project) => { void changeProject(project) }} disabled={sending || resuming || projectSaving || work.blocked || (!!id && !data)} />
          <button type="button" className="btn btn-quiet btn-small" disabled={sending || projectSaving || work.blocked} onClick={() => window.dispatchEvent(new Event('square-attach-files'))}><FileIcon size={14} /> Attach files{app.status?.image_support ? ' or images' : ''}</button>
          {attachments.filter((path) => !isImagePath(path)).map((path) => (
            <span key={path} className="chip">
              <FileIcon size={14} /><span>{path}</span>
              <button type="button" className="chip-remove" aria-label={`Remove ${path}`}
                onClick={() => setAttachments((items) => items.filter((item) => item !== path))}><CloseIcon size={13} /></button>
            </span>
          ))}
        </div>
        <PinnedImages paths={attachments} onRemove={(path) => setAttachments((items) => items.filter((item) => item !== path))} />
        {hasImages && app.status?.image_support === false && <p className="error-note">This model cannot read images. Remove the images or use a vision-capable model.</p>}
        <div className="composer-box">
          {free ? <ModeMessage value={draft} onChange={setDraft} call={selectedCall} onCall={selectCall}
            effort={selectedEffort} onEffort={setSelectedEffort} hasEffort={!!selectedCall && !!effortKind(selectedCall.mode)}
            onSend={() => { send() }} disabled={blocked || sending || resuming || projectSaving} /> : <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={keys}
            rows={Math.min(8, Math.max(2, draft.split('\n').length))}
            placeholder={free ? 'Describe what you want to do; drop files here to attach them…'
              : mode === 'critic' ? 'Ask the critic to check a statement, a proof, or a file…' : 'Ask about an approach, a reformulation or a connection…'}
            aria-label="Message" />}
          {ours ? (
            <button type="button" className="btn composer-send" disabled={task?.state === 'pausing'} onClick={() => api.pause(task!.id).catch((reason: Error) => setSendError(reason.message))}>
              {task?.state === 'pausing' ? <PlayIcon size={16} /> : <PauseIcon size={16} />} {task?.state === 'pausing' ? 'Pausing…' : free ? 'Pause' : 'Stop'}
            </button>
          ) : paused && !draft.trim() && !hasImages ? (
            <button type="button" className="btn btn-primary composer-send" disabled={work.blocked || resuming || sending || !!uploading} onClick={resume}>
              <PlayIcon size={16} /> {resuming ? 'Resuming…' : 'Resume'}
            </button>
          ) : work.queued ? (
            <button type="button" className="btn composer-send" onClick={() => api.cancelQueued(work.queued!.id).catch((reason: Error) => setSendError(reason.message))}>
              <CloseIcon size={16} /> Cancel queued turn
            </button>
          ) : (
            <button type="submit" className="btn btn-primary composer-send" disabled={blocked || sending || resuming || projectSaving || !!uploading || (!draft.trim() && !hasImages) || (hasImages && !app.status?.image_support)} aria-label="Send">
              <SendIcon size={16} /> {uploading ? 'Uploading…' : 'Send'}
            </button>
          )}
        </div>
        <div className="composer-bar">
          {!free && <Toggle label="Thinking" checked={think} onChange={changeThink} />}
          {free && budgetDisplay && (
            <span className="muted small composer-budget" title={budgetDisplay.title}>
              {budgetDisplay.output}
              {budgetDisplay.reserved && <> {' · '}{budgetDisplay.reserved}</>}
              {budgetDisplay.context && <> {' · '}{budgetDisplay.context}</>}
              {' · '}{budgetDisplay.time}
            </span>
          )}
          {free && <span className="composer-hint">@ selects a mode</span>}
        </div>
        {work.queued && <p className="queued-note" role="status">This turn is queued and will start when a chat slot opens. You can cancel it above.</p>}
        {!blocked && capacityFull(app.snapshot, app.status) && <BusyNote queue />}
        <ErrorNote>{sendError || taskError}</ErrorNote>
      </form>
    </div>
    </MathMacrosProvider>
  )
}

function Intro({ mode, onPick, onSkill }: { mode: ChatMode; onPick: (text: string) => void; onSkill: (mode: RouteMode) => void }) {
  const intro = INTRO[mode]
  return (
    <div className="entry intro">
      <span className={`in-margin speaker speaker-model mode-${mode}`}>{MODES[mode].glyph}</span>
      <div>
        <h1 className="page-title">{intro.title}</h1>
        <p className="lede">{intro.text}</p>
        {mode === 'free' && <>
          <p className="muted intro-skill-hint">Choose a skill below, or type <code>@</code> in your message, to request one explicitly.</p>
          <div className="intro-skills" aria-label="Available skills">{MODE_CALLS.map((skill) => <button key={skill.mode} type="button" className="intro-skill" title={skill.summary} onClick={() => onSkill(skill.mode)}>
            <span className={`route-cover mode-${coverOf(skill.mode)}`} aria-hidden="true"><Glyph mode={coverOf(skill.mode)} /></span>{skill.label}
          </button>)}</div>
          <p className="muted small intro-feedback">Found an issue or have an idea? <button type="button" className="link-btn" onClick={() => window.dispatchEvent(new Event('square-feedback'))}>Send feedback</button>.</p>
        </>}
        {intro.examples.length > 0 && <div className="examples">
          {intro.examples.map((example) => (
            <button key={example} type="button" className="example" onClick={() => onPick(example)}>
              <Markdown>{example}</Markdown>
            </button>
          ))}
        </div>}
      </div>
    </div>
  )
}

type Step = { item: TranscriptItem; index: number }
type Turn = { user?: TranscriptItem; steps: Step[] }

function group(items: TranscriptItem[]): Turn[] {
  const turns: Turn[] = []
  items.forEach((item, index) => {
    if (item.role === 'user' || !turns.length) turns.push({ user: item.role === 'user' ? item : undefined, steps: item.role === 'user' ? [] : [{ item, index }] })
    else turns[turns.length - 1].steps.push({ item, index })
  })
  return turns
}

function Transcript({ items, mode, chatId }: { items: TranscriptItem[]; mode: ChatMode; chatId: string }) {
  return (
    <>
      {group(items).map((turn, index) => (
        <div key={index} className="turn">
          {turn.user && <UserMessage item={turn.user} />}
          <ModelSteps steps={turn.steps} mode={mode} chatId={chatId} />
        </div>
      ))}
    </>
  )
}

function UserMessage({ item }: { item: TranscriptItem }) {
  return (
    <div className={'entry message message-user' + (item.discarded ? ' message-discarded' : '')}>
      <span className="in-margin speaker speaker-user">You</span>
      <div className="message-body">
        <Markdown>{item.content}</Markdown>
        {item.files && item.files.length > 0 && (
          <div className="attachments">{item.files.filter((path) => !item.images?.length || !isImagePath(path)).map((path) => <span key={path} className="chip chip-static"><FileIcon size={13} /><span>{path}</span></span>)}</div>
        )}
        {!!item.images?.length && <ImageGallery images={item.images} />}
        {item.time && <span className="message-time">{ago(item.time)}</span>}
      </div>
    </div>
  )
}

function ModelSteps({ steps, mode, chatId }: { steps: Step[]; mode: ChatMode; chatId: string }) {
  const out: ReactElement[] = []
  let blocks: ReactElement[] = []
  // An Assistant conversation answers with one voice, whichever engine mode wrote it, except
  // a literature check, which keeps its citation mark; older critique and exploration chats
  // keep their own mark.
  let mark: ChatMode | 'check' = mode
  const flush = (key: number) => {
    if (!blocks.length) return
    out.push(
      <div key={'answer' + key} className="entry message message-model">
        <span className={`in-margin speaker speaker-model mode-${mark}`} title={mark === 'check' ? 'Literature check' : MODES[mark].label}><Glyph mode={mark} /></span>
        <div className="message-body">{blocks}</div>
      </div>,
    )
    blocks = []
  }
  const markFor = (item: TranscriptItem): ChatMode | 'check' => (item.mode === 'check' ? 'check' : mode)
  for (let i = 0; i < steps.length; i++) {
    const { item, index } = steps[i]
    if (item.role === 'route') {
      flush(index)
      out.push(<RouteCard key={'route' + index} chatId={chatId} index={index} item={item} />)
    } else if (item.role === 'assistant') {
      if (markFor(item) !== mark) { flush(index); mark = markFor(item) }
      const calls = item.tool_calls || []
      const results: TranscriptItem[] = []
      while (i + 1 < steps.length && steps[i + 1].item.role === 'tool' && results.length < calls.length) results.push(steps[++i].item)
      const final = !calls.length
      blocks.push(
        <div key={index} className="step">
          <Thinking text={item.thinking || ''} />
          {calls.map((call, n) => (
            <Collapse key={n} className="tool-step" summary={<span className="tool-line">{toolLine(call)}</span>}>
              <pre className="plain">{results[n]?.content ?? 'No result was recorded.'}</pre>
            </Collapse>
          ))}
          {item.content && <Markdown className={final ? 'answer' : 'interim'}>{item.content}</Markdown>}
          {final && item.stats?.eval_count !== undefined && (
            <span className="message-time">{count(item.stats.eval_count)} tokens{item.stats.done_reason === 'length' ? ', stopped at the output limit' : ''}</span>
          )}
        </div>,
      )
    } else if (item.role === 'review') {
      blocks.push(
        <div key={index} className="review-note">
          <p className="review-label">Fresh-context review</p>
          <p className="muted small">A separate critic context audited the previous answer. It is another model opinion, not verification.</p>
          <Thinking text={item.thinking || ''} />
          <Markdown>{item.content}</Markdown>
        </div>,
      )
    } else if (item.role === 'notice') {
      blocks.push(<p key={index} className={'notice' + (item.discarded ? ' notice-discarded' : '')}>{item.content}</p>)
    } else if (item.role === 'tool') {
      blocks.push(<Collapse key={index} className="tool-step" summary={<span className="tool-line">{item.tool_name || 'tool'} result</span>}><pre className="plain">{item.content}</pre></Collapse>)
    } else if (item.role === 'experiment') {
      blocks.push(<ExperimentFigures key={index} item={item} />)
    }
  }
  flush(-1)
  return <>{out}</>
}

/** What the model started for a message, at which effort, with a way to cancel it. */
function RouteCard({ chatId, index, item }: { chatId: string; index: number; item: TranscriptItem }) {
  const app = useApp()
  const [error, setError] = useState('')
  const [acting, setActing] = useState(false)
  useEffect(() => { setError(item.error || '') }, [item.error])
  if (item.status === 'dismissed') {
    return <div className="entry route-dismissed"><span className="in-margin" /><span className="muted small">Suggestion dismissed.</span></div>
  }
  if (item.mode === 'clarify' || !item.mode) {
    return (
      <div className="entry route route-clarify">
        <span className="in-margin speaker speaker-model mode-free">{MODES.free.glyph}</span>
        <div className="route-card">
          <p className="route-title">One detail first</p>
          <Markdown className="route-question">{item.question || ''}</Markdown>
          <p className="muted small">Answer in the message box; the work starts as soon as the request is clear.</p>
        </div>
      </div>
    )
  }
  const mode = item.mode as RouteMode
  const kind = kindOf(mode)
  const info = item.orchestrated && mode === 'critic'
    ? { noun: 'a critique', label: 'Critique', cover: 'critic' as const }
    : item.orchestrated && mode === 'explore'
      ? { noun: 'an exploration', label: 'Exploration', cover: 'explore' as const }
      : KINDS[kind]
  const effort = kind === 'answer' ? '' : `${effortLabel(item.effort)} effort`
  const act = async (action: () => Promise<unknown>) => {
    setActing(true)
    setError('')
    try { await action() } catch (reason) { setError((reason as Error).message) } finally { setActing(false) }
  }
  // Suggestions saved before jobs started on their own, and jobs that failed or were
  // cancelled before running, can be started (again) as the model chose them.
  const start = () => act(() => {
    const limits = effortKind(mode) && app.status ? effortLimits(effortKind(mode)!, app.status.defaults, isLevel(item.effort) ? item.effort : 'medium') : undefined
    return api.startRoute(chatId, index, { mode, request: item.request || '', files: item.files || [], limits: limits as Record<string, number> | undefined })
  })

  const task = taskById(app.snapshot, item.task)?.task
  const { queued, running } = routeActivity(item, app.snapshot)
  let status = ''
  let variant: Variant = 'ready'
  if (item.job_id && mode === 'prove') {
    const job = app.proofs?.find((proof) => proof.id === item.job_id)
    if (job) ({ variant, label: status } = proofLook(job.status, job.running && (!item.orchestrated || running)))
  } else if (item.job_id) {
    const job = app.research?.find((report) => report.id === item.job_id)
    if (job) ({ variant, label: status } = researchLook(job.status, job.running && (!item.orchestrated || running)))
  }
  let headline: string
  if (item.status === 'proposed') { headline = `${info.label} suggested`; status = 'Not started'; variant = 'ready' }
  else if (item.status === 'failed') { headline = item.orchestrated ? `${info.label} did not finish` : `Could not start ${info.noun}`; variant = 'error' }
  else if (item.status === 'cancelled') { headline = `${info.label} cancelled`; status = 'Removed from the queue'; variant = 'spent' }
  else if (queued) { headline = `${info.label} queued`; status = 'Starts when a chat slot opens'; variant = 'ready' }
  else if (running && task?.state === 'pausing') { headline = `Stopping ${info.noun}`; status = 'At the next checkpoint'; variant = 'running' }
  else if (running) { headline = kind === 'answer' && !item.orchestrated ? 'Answering here' : kind === 'check' && !item.orchestrated ? 'Checking references' : `${item.job_id || item.orchestrated ? 'Running' : 'Starting'} ${info.noun}`; status = ''; variant = 'running' }
  else if (item.status === 'stopped') { headline = kind === 'answer' && !item.orchestrated ? 'The answer was stopped' : `${info.label} stopped`; status = status || 'Paused'; variant = 'paused' }
  else if (item.orchestrated && item.status === 'done') { headline = `${info.label} returned`; if (!item.job_id) { status = ''; variant = 'ready' } }
  else if (item.orchestrated) { headline = `${info.label} awaiting resume`; status = 'Paused'; variant = 'paused' }
  else if (kind === 'answer') { headline = 'Answered below'; status = ''; variant = 'complete' }
  else if (kind === 'check') { headline = 'References checked below'; status = ''; variant = 'complete' }
  else { headline = info.label }
  return (
    <div className={'entry route route-' + (item.status || 'proposed')}>
      <span className={`in-margin speaker speaker-model mode-${info.cover}`} title={info.label}><Glyph mode={info.cover} /></span>
      <div className="route-card route-auto">
        <div className="route-line">
          <Square variant={variant} size={14} />
          <p className="route-title">{headline}</p>
          {effort && <span className="route-effort">{effort}</span>}
          {status && <span className="route-status">{status}</span>}
        </div>
        <div className="route-actions">
          {(queued || running) && (
            <button type="button" className="btn btn-small" disabled={acting || task?.state === 'pausing'}
              title={queued ? 'Take it out of the queue' : item.orchestrated ? 'Pause the assistant and its current task; their work is saved' : 'Stop at the next checkpoint; the job is kept and can be resumed from its page'}
              onClick={() => act(() => queued ? api.cancelQueued(item.task!) : item.orchestrated ? api.pause(item.task!) : api.cancelRoute(chatId, index))}>
              {item.orchestrated && running ? <PauseIcon size={14} /> : <CloseIcon size={14} />} {item.orchestrated && running ? 'Pause assistant' : 'Cancel'}
            </button>
          )}
          {item.job_id && <>
            {item.outputs?.pdf && <a className="btn btn-small btn-primary" href={api.researchPdfDownloadUrl(item.job_id)} download>Download PDF</a>}
            {item.outputs?.source && <a className={'btn btn-small' + (item.outputs.pdf ? '' : ' btn-primary')} href={api.researchSourceUrl(item.job_id)} download={item.outputs.source}>Sources (.zip)</a>}
            {item.outputs?.download && <a className={'btn btn-small' + (item.outputs.pdf || item.outputs.source ? '' : ' btn-primary')} href={api.researchTexUrl(item.job_id)} download={item.outputs.download} title="Only the main LaTeX file. Images and template files must be beside it to compile; use Sources (.zip) for the complete bundle.">Main .tex only</a>}
          </>}
          {item.job_id && paged(mode) && <a className="btn btn-small btn-quiet" href={href(pageOf(mode), item.job_id, null, chatId)}>Open the {info.label.toLowerCase()}</a>}
          {!item.orchestrated && ['proposed', 'failed', 'cancelled'].includes(item.status || '') && (
            <button type="button" className="btn btn-small" disabled={acting} onClick={start}>
              {item.status === 'proposed' ? 'Start' : 'Start again'}
            </button>
          )}
          <Collapse className="route-details" summary={(kind === 'answer' || kind === 'check') && !item.orchestrated ? 'Question as the model sees it' : `Request given to the ${info.label.toLowerCase()}`}>
            <div className="route-summary"><Inline limit={1200}>{item.request || ''}</Inline></div>
            {item.files && item.files.length > 0 && (
              <div className="attachments">{item.files.map((path) => <span key={path} className="chip chip-static"><FileIcon size={13} /><span>{path}</span></span>)}</div>
            )}
            {item.missing && item.missing.length > 0 && <p className="muted small">Not found in this folder: {item.missing.join(', ')}.</p>}
            {item.reason && <p className="muted small">Why: {item.reason}</p>}
          </Collapse>
        </div>
        {item.outcome && item.outcome !== error && <p className="muted small">{item.outcome}</p>}
        <ErrorNote>{error}</ErrorNote>
      </div>
    </div>
  )
}

/** The files of one sandbox run, shown where the run happened, each by its MIME type. */
function ExperimentFigures({ item }: { item: TranscriptItem }) {
  const artifacts = artifactsOf(item)
  if (!artifacts.length) return null
  return (
    <div className="experiment">
      {artifacts.map((artifact) => <ArtifactView key={artifact.artifact_id} artifact={artifact} purpose={item.purpose} />)}
    </div>
  )
}

function ArtifactView({ artifact, purpose }: { artifact: Artifact; purpose?: string }) {
  const display = displayOf(artifact.mime_type)
  const caption = (
    <figcaption>
      {purpose ? `${purpose} · ` : ''}
      <a href={artifact.url} target="_blank" rel="noopener noreferrer">{artifact.filename}</a>
      {' · '}<span className="experiment-id">{artifact.artifact_id.split('/')[0]}</span>
    </figcaption>
  )
  if (display === 'image') {
    return (
      <figure className="experiment-figure">
        <a href={artifact.url} target="_blank" rel="noopener noreferrer" title="Open at full size">
          <img src={artifact.url} alt={`${artifact.filename} from a sandbox run`}
            width={artifact.width ?? undefined} height={artifact.height ?? undefined} />
        </a>
        {caption}
      </figure>
    )
  }
  if (display === 'page') {
    // Untrusted HTML: its own scripts only, in an opaque origin (no allow-same-origin).
    return (
      <figure className="experiment-figure experiment-page">
        <iframe src={artifact.url} sandbox="allow-scripts" title={artifact.filename} referrerPolicy="no-referrer" />
        {caption}
      </figure>
    )
  }
  return (
    <p className="experiment-file">
      <FileIcon size={14} /> <a href={artifact.url} target="_blank" rel="noopener noreferrer">{artifact.filename}</a>
      <span className="muted small"> · {display === 'document' ? 'PDF' : artifact.mime_type}
        {artifact.bytes ? ` · ${Math.max(1, Math.round(artifact.bytes / 1024))} KB` : ''}</span>
    </p>
  )
}

function LiveTurnView({ live, mode }: { live: LiveTurn; mode: ChatMode }) {
  const glyph: ChatMode | 'check' = live.mode === 'check' ? 'check' : mode
  return (
    <div className="turn turn-live">
      {live.kind === 'message' && live.user && <UserMessage item={{ role: 'user', content: live.user, time: '' }} />}
      <div className="entry message message-model">
        <span className={`in-margin speaker speaker-model mode-${glyph}`}><Glyph mode={glyph} /></span>
        <div className="message-body">
          {live.kind === 'review' && <p className="review-label">Fresh-context review in progress</p>}
          {live.steps.map((step, index) => step.type === 'call' ? (
            <div key={index} className="step">
              <Thinking text={step.thinking} live={!step.text} />
              {step.text && <StreamingMarkdown text={step.text} className="answer" />}
              {!step.text && !step.thinking && index === live.steps.length - 1 && (
                <p className="muted small">Waiting for the first tokens. The model may still be loading into memory.</p>
              )}
            </div>
          ) : step.type === 'tool' ? (
            <p key={index} className="tool-line tool-live">{step.text}</p>
          ) : step.type === 'result' ? (
            <p key={index} className="tool-result-live">{step.text}</p>
          ) : step.type === 'experiment' ? (
            <ExperimentFigures key={index} item={step.item} />
          ) : (
            <p key={index} className="notice">{step.text}</p>
          ))}
          {!live.steps.length && <p className="muted small">Waiting for the model…</p>}
        </div>
      </div>
    </div>
  )
}
