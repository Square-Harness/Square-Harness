// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactElement } from 'react'
import { api, ApiError, type Chat, type LiveTurn, type RouteMode, type TranscriptItem } from '../api'
import { capacityFull, chatWork, taskById } from '../concurrency'
import { assistantResumable, routeActivity } from '../assistant'
import { assistantBudgetDisplay } from '../assistantBudget'
import { Collapse, CopyButton, ErrorNote, Loading, ModelWaitNote, Toggle, WorkProgressNote, useLoad } from '../components/common'
import { CloseIcon, FileIcon, FolderIcon, PauseIcon, PlayIcon, SendIcon, StopIcon } from '../components/Icons'
import { EFFORTS, effortLimits, isLevel, type EffortKind } from '../effort'
import { Glyph, type Cover } from '../components/Glyph'
import { ModeMessage } from '../components/ModeMessage'
import { ProjectSelector } from '../components/Projects'
import { ImageGallery, isImagePath, PinnedImages } from '../components/ImageAttachments'
import { callPayload, typedCall, validCall, type ModeCall } from '../modeCalls'
import { Inline, Markdown, MathMacrosProvider, StreamingMarkdown } from '../components/Markdown'
import { proofLook, researchLook, Square, type Variant } from '../components/Square'
import { useDropTarget } from '../drop'
import { recentMessages, rememberMessage, useRecall } from '../recall'
import { loadDraft, saveDraft } from '../drafts'
import { artifactsOf, displayOf, type Artifact } from '../artifacts'
import { literalUnderscores } from '../mathtext'
import { ago, count, displayPath, duration } from '../format'
import { MODES } from '../modes'
import { FIXED_EFFORT } from '../review'
import { WorkspaceSwitch } from '../components/Shell'
import { WorkPanel } from '../components/WorkPanel'
import { ConversationPlan, InvestigationReport, WorkingAnswer } from '../components/InvestigationReading'
import { go, href } from '../router'
import { getState, refreshLists, setProjectFilter, useApp, useTick } from '../store'
import { BusyNote, QUIET_TOOLS, Thinking, toolLine } from './shared'

import { activeAgent, deriveAgents, groupsOf, type AgentLane } from '../agents'
import { AgentGroup, WaitingForResults } from '../components/AgentGroup'
import { AttachMenu } from '../components/FileExplorer'

type ChatMode = 'critic' | 'explore' | 'free'

/** An unsent message kept when its conversation is deleted (here or in another tab). */
let rescued: { draft: string; attachments: string[] } | null = null

const INTRO: Record<ChatMode, { title: string; text: string; examples: string[] }> = {
  free: {
    title: 'What are you working on?',
    text: 'Discuss a question, share a proof or attach your notes. The main agent chooses the appropriate skills and brings their results back into the conversation.',
    examples: [],
  },
  critic: {
    title: 'Continue this conversation',
    text: 'This conversation started in the former Critique mode.',
    examples: [],
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

// How a card names what the model started. Explore and saved Critique are both answers here;
// a literature check also answers here, with its evidence.
type Kind = 'answer' | 'check' | 'prove' | 'literature' | 'referee' | 'detailed_review' | 'typo_check' | 'quick_review' | 'explain' | 'writeup' | 'summarize'
  | 'python_lab' | 'sanity_check'
// `cover` names the mark (components/Glyph.tsx) and its colour class.
const KINDS: Record<Kind, { noun: string; label: string; cover: Cover }> = {
  answer: { noun: 'an answer', label: 'Answer', cover: 'free' },
  check: { noun: 'a literature check', label: 'Literature Check', cover: 'check' },
  prove: { noun: 'a proof', label: 'Proof', cover: 'prove' },
  literature: { noun: 'a literature review', label: 'Literature Review', cover: 'literature' },
  referee: { noun: 'a review', label: 'Review', cover: 'referee' },
  detailed_review: { noun: 'a review', label: 'Review', cover: 'referee' },
  typo_check: { noun: 'a typo check', label: 'Typo', cover: 'typo' },
  quick_review: { noun: 'a proof check', label: 'Proof Check', cover: 'proof_check' },
  explain: { noun: 'an explanation', label: 'Explanation', cover: 'explain' },
  writeup: { noun: 'a write-up', label: 'Write-Up', cover: 'writeup' },
  summarize: { noun: 'a summary', label: 'Summary', cover: 'writeup' },
  python_lab: { noun: 'a Python Lab run', label: 'Python Lab', cover: 'python_lab' },
  sanity_check: { noun: 'a numerical sanity check', label: 'Numerical Sanity Check', cover: 'sanity_check' },
}
const kindOf = (mode: RouteMode | 'critic' | 'free'): Kind => (mode === 'critic' || mode === 'explore' || mode === 'free' || mode === 'investigate' ? 'answer' : mode)
const effortKind = (mode: RouteMode): EffortKind | null =>
  mode === 'prove' ? 'proof' : mode === 'writeup' || mode === 'summarize' ? 'writeup' : mode === 'literature' ? 'literature' : ['referee', 'detailed_review', 'typo_check', 'quick_review', 'explain'].includes(mode) ? 'research' : null
// Quick reviews and explanations are pages of the Review notebook; summaries are write-ups.
// Python Lab and sanity checks answer in the conversation: they have no notebook page.
type Paged = Exclude<RouteMode, 'investigate' | 'check' | 'python_lab' | 'sanity_check'>
const paged = (mode: RouteMode | 'critic' | 'free'): mode is Paged =>
  mode !== 'critic' && mode !== 'free' && mode !== 'investigate' && mode !== 'check' && mode !== 'python_lab' && mode !== 'sanity_check'
const pageOf = (mode: Paged) => (mode === 'quick_review' || mode === 'explain' || mode === 'detailed_review' || mode === 'typo_check' ? 'referee' : mode === 'summarize' ? 'writeup' : mode)
const effortLabel = (effort?: string) => EFFORTS.find((item) => item.id === (isLevel(effort) ? effort : 'high'))!.label

export function ChatView({ mode, id }: { mode: ChatMode; id: string | null }) {
  const app = useApp()
  const tick = useTick(id)
  const personalizationTick = useTick('personalization')
  // Reloaded when a turn ends too: its last save may have arrived before the turn was marked done.
  const [ended, setEnded] = useState(0)
  const { data, error } = useLoad(() => (id ? api.chat(id) : Promise.resolve(null)), [id], [tick, ended, personalizationTick].join('/'))
  const agentLanes = useMemo(() => data && data.id === id ? deriveAgents(data, app.proofs || [], app.research || []) : [],
    [data, id, app.proofs, app.research])
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
  const [kept] = useState(() => id ? null : rescued)
  useEffect(() => { if (kept) rescued = null }, [kept])
  // An unsent message is kept for this conversation while the tab stays open (drafts.ts).
  const [draft, setDraft] = useState(() => kept?.draft || loadDraft(id))
  useEffect(() => { saveDraft(id, draft) }, [id, draft])
  const [selectedCall, setSelectedCall] = useState<ModeCall | null>(() => mode === 'free' ? typedCall(draft, true) : null)
  const [attachments, setAttachments] = useState<string[]>(kept?.attachments || [])
  const [deletedNotice, setDeletedNotice] = useState(!!kept)
  const defaultProject = app.projects?.some((project) => project.id === app.projectFilter) ? app.projectFilter : null
  const [newProject, setNewProject] = useState<string | null>(defaultProject)
  const [projectOverride, setProjectOverride] = useState<string | null | undefined>(undefined)
  const [projectSaving, setProjectSaving] = useState(false)
  useEffect(() => { setProjectOverride(undefined) }, [data?.project_id])
  const savedProject = data ? data.project_id || null : app.chats?.find((chat) => chat.id === id)?.project_id || null
  const currentProject = id ? (projectOverride !== undefined ? projectOverride : savedProject) : newProject
  useEffect(() => { if (!id && !createdChat.current) setNewProject(defaultProject) }, [defaultProject, id])
  const [sendError, setSendError] = useState('')
  // A refusal because this conversation is busy (another tab sent first) clears once its turn ends.
  const [busyError, setBusyError] = useState(false)
  const [sending, setSending] = useState(false)
  const createdChat = useRef<string | null>(null)
  useEffect(() => {
    const deleted = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== id) return
      rescued = { draft, attachments }
      saveDraft(id, '')
      setProjectFilter(currentProject || 'all')
    }
    window.addEventListener('square-chat-deleted', deleted)
    return () => window.removeEventListener('square-chat-deleted', deleted)
  })
  useEffect(() => {
    const prepare = (event: Event) => {
      if (sending) { event.preventDefault(); setSendError('Wait for the message to be sent before changing projects.'); return }
      if ((draft.trim() || attachments.length) && !window.confirm('Start a new conversation in this project? Your unsent message and attachments will be cleared.')) {
        event.preventDefault(); return
      }
      setDraft(''); setAttachments([]); setSelectedCall(null); setSendError(''); createdChat.current = null
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

  // The sidebar follows the open conversation: a project view that does not contain it returns to all conversations.
  useEffect(() => {
    if (!id || !data || data.id !== id) return
    const filter = getState().projectFilter
    if (filter !== 'all' && filter !== (data.project_id || 'all')) setProjectFilter(data.project_id || 'all')
  }, [id, data?.id, data?.project_id])
  // A new or newly opened conversation is ready for typing (not on touch screens, where it would open the keyboard).
  useEffect(() => {
    if (!window.matchMedia?.('(pointer: fine)').matches) return
    const active = document.activeElement
    if (active && active !== document.body && !active.closest('.sidebar, .composer')) return
    composer.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus({ preventScroll: true })
  }, [id])
  const sawBusy = useRef(false)
  useEffect(() => {
    if (!busyError) { sawBusy.current = false; return }
    if (work.blocked) sawBusy.current = true
    else if (sawBusy.current) { setSendError(''); setBusyError(false) }
  }, [work.blocked, busyError])

  // A project's file list attaches a file to the next message.
  useEffect(() => {
    const attach = (event: Event) => {
      const paths = (event as CustomEvent<string[]>).detail
      if (Array.isArray(paths)) setAttachments((items) => [...new Set([...items, ...paths])])
    }
    window.addEventListener('square-attach-paths', attach)
    return () => window.removeEventListener('square-attach-paths', attach)
  }, [])

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
  const pausedInvestigation = paused && !!data?.investigation?.current
  // While this conversation's own turn runs, a new Assistant message waits in the queue and starts after it.
  const canQueue = free && !!id && (!!work.active || work.routing) && !work.queued
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
    const modeCall = free && text === undefined ? callPayload(raw, call) : undefined
    const content = modeCall ? raw : raw.trim()
    // A new message while the Assistant is paused sets that turn aside (the server says so in the conversation).
    const queue = canQueue
    if (!content.trim() || pausedInvestigation || sending || resuming || projectSaving || uploading || (work.blocked && !queue) || (hasImages && !app.status?.image_support)) return
    setSending(true)
    setSendError('')
    setBusyError(false)
    try {
      let chatId = id || createdChat.current
      if (!chatId) { chatId = (await api.createChat(mode, think, locked ? undefined : true, newProject)).id; createdChat.current = chatId }
      if (free) await api.route(chatId, content, attachments, modeCall, queue)
      else await api.send(chatId, content, attachments)
      if (text === undefined) rememberMessage(content)
      saveDraft(id, '')  // at once: a first message leaves this view before the draft effect runs
      setDraft('')
      setSelectedCall(null)
      setAttachments([])
      pinned.current = true
      if (!id) go(mode, chatId)
    } catch (reason) {
      setSendError((reason as Error).message)
      setBusyError(reason instanceof ApiError && reason.status === 409)
    } finally {
      setSending(false)
    }
  }
  const submit = (event: FormEvent) => { event.preventDefault(); send() }
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
    if (boxKeys(event)) return
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
  const stopInvestigation = async () => {
    if (!id || resuming) return
    setResuming(true)
    setSendError('')
    try { await api.stopInvestigation(id, data?.investigation?.id) } catch (reason) { setSendError((reason as Error).message) } finally { setResuming(false) }
  }
  // Stop for good: a running turn pauses and is then closed; a paused one is closed at once. Saved work is kept.
  const stop = async () => {
    if (!id || resuming) return
    setSendError('')
    try {
      if (task) await api.kill(task.id)
      else { setResuming(true); await api.stopAssistant(id) }
    } catch (reason) { setSendError((reason as Error).message) } finally { setResuming(false) }
  }
  const transcript = data?.transcript || []
  // ↑ in an empty message box recalls what was sent: this conversation's messages, or this tab's in a new one.
  const sentHere = useMemo(() => sentMessages(data?.transcript || []), [data?.transcript])
  const recall = useRecall(sentHere.length ? sentHere : recentMessages(), draft, (text) => {
    setDraft(text)
    setSelectedCall(free ? typedCall(text, true) : null)
  })
  // Esc in the message box pauses the running turn, as the Pause button does.
  const boxKeys = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (recall(event)) return true
    if (event.key !== 'Escape' || !task || task.state === 'pausing' || event.nativeEvent.isComposing) return false
    event.preventDefault()
    api.pause(task.id).catch((reason: Error) => setSendError(reason.message))
    return true
  }
  const lastTask = app.snapshot.task
  const taskError = lastTask?.target === id && lastTask.state === 'error' ? lastTask.error : ''
  const last = transcript.at(-1)
  const errorInChat = taskError && (last?.role === 'notice' && last.content.includes(taskError) ||
    transcript.some(item => item.role === 'route' && item.task === lastTask?.id && item.error === taskError))
  // Another conversation's work can be queued; this conversation has one active turn.
  const blocked = work.blocked
  // Only an Assistant turn is closed by Stop; a job started from a card pauses and resumes from its own page.
  const stoppable = free && task?.kind === 'chat'
  // With Queue beside them, Pause and Stop keep only their icons on a narrow screen, so the text box keeps its room.
  const crowded = ours && canQueue && !!(draft.trim() || hasImages)

  return (
    <MathMacrosProvider definitions={data?.math_macros}>
    <div className="chat-workspace">
    <div className="chat">
      <div className="chat-scroll sheet paper" ref={scroller}
        onScroll={(event) => { const el = event.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }}>
        <div className="sheet-inner chat-inner">
          {!id && <Intro mode={mode} project={app.projects?.find((item) => item.id === currentProject)?.name} onPick={(text) => { setDraft(text); setSelectedCall(null) }} />}
          {id && error && <ErrorNote>{error}</ErrorNote>}
          {id && !data && !error && <Loading />}
          {data && !transcript.length && !live && !routing && <Intro mode={mode} onPick={(text) => { setDraft(text); setSelectedCall(null) }} />}
          {id && <Transcript items={transcript} mode={mode} chatId={id} lanes={agentLanes} plan={data?.investigation} waiting={data?.waiting_for_results} paused={paused && !ours} />}
          {routing && !live && (
            <div className="entry message message-model">
              <span className="in-margin speaker speaker-model mode-free">∀</span>
              <p className="muted routing"><Square variant="running" size={12} /> Reading your request…</p>
            </div>
          )}
          {live && <LiveTurnView live={live} mode={mode} investigating={!!data?.investigation?.current} agentsWorking={agentLanes.some(activeAgent)} waitingForOutput={!!task?.waiting_model_calls} />}
          {free && data?.assistant_progress && !agentLanes.some(activeAgent) && <div className="entry">
            <span className="in-margin" />
            <WorkProgressNote progress={data.assistant_progress} status={data.assistant_status || ''} running={ours} />
          </div>}
          {paused && !ours && (
            <div className="entry message message-model">
              <span className={'in-margin speaker speaker-model mode-' + (pausedInvestigation ? 'investigate' : 'free')}><Glyph mode={pausedInvestigation ? 'investigate' : 'free'} /></span>
              <div>
                <p className="notice">{pausedInvestigation
                  ? 'The investigation is paused. Resume the plan or finish with a report on the evidence collected so far.'
                  : 'The assistant is paused. Resume its saved work, stop it for good, or send a new message to set this turn aside.'}</p>
                {pausedInvestigation && <button type="button" className="btn btn-quiet btn-small" disabled={resuming || work.blocked}
                  onClick={stopInvestigation}>Stop and report</button>}
              </div>
            </div>
          )}
        </div>
      </div>
      <form className="composer" ref={composer} onSubmit={submit}>
        {deletedNotice && !id && <p className="warning-note composer-notice" role="status">
          The conversation you had open was deleted{draft.trim() || attachments.length ? '. Your unsent message is kept here, in a new conversation.' : '.'}
          <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => setDeletedNotice(false)}><CloseIcon size={13} /></button>
        </p>}
        <div className="composer-top">
          <WorkspaceSwitch />
          <ProjectSelector value={currentProject} onChange={(project) => { void changeProject(project) }} disabled={sending || resuming || projectSaving || work.blocked || (!!id && !data)} />
          <AttachMenu label={'Attach files' + (app.status?.image_support ? ' or images' : '')} disabled={sending || projectSaving || (work.blocked && !canQueue)}
            attached={attachments} onAttach={(paths) => setAttachments((items) => [...new Set([...items, ...paths])])}
            startFolder={app.projects?.find((item) => item.id === currentProject)?.folder} projectId={currentProject} />
          {attachments.filter((path) => !isImagePath(path)).map((path) => (
            <span key={path} className="chip" title={path}>
              <FileIcon size={14} /><span>{displayPath(path)}</span>
              <button type="button" className="chip-remove" aria-label={`Remove ${displayPath(path)}`}
                onClick={() => setAttachments((items) => items.filter((item) => item !== path))}><CloseIcon size={13} /></button>
            </span>
          ))}
        </div>
        <PinnedImages paths={attachments} onRemove={(path) => setAttachments((items) => items.filter((item) => item !== path))} />
        {hasImages && app.status?.image_support === false && <p className="error-note">This model cannot read images. Remove the images or use a vision-capable model.</p>}
        <div className="composer-box">
          {free ? <ModeMessage value={draft} onChange={setDraft} call={selectedCall} onCall={setSelectedCall}
            onSend={() => { send() }} onKey={boxKeys} disabled={pausedInvestigation || (blocked && !canQueue) || sending || resuming || projectSaving} /> : <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={keys}
            rows={Math.min(8, Math.max(2, draft.split('\n').length))}
            placeholder={free ? 'Describe what you want to do; drop files here to attach them…'
              : mode === 'critic' ? 'Continue the conversation…' : 'Ask about an approach, a reformulation or a connection…'}
            aria-label="Message" />}
          {ours && canQueue && (draft.trim() || hasImages) && (
            <button type="submit" className="btn btn-primary composer-send" disabled={sending || !!uploading || (hasImages && !app.status?.image_support)}
              title="Send this message after the current turn finishes">
              <SendIcon size={16} /> {sending ? 'Queueing…' : 'Queue'}
            </button>
          )}
          {ours ? <>
            <button type="button" className={'btn composer-send' + (crowded ? ' btn-quiet' : '')} disabled={task?.state === 'pausing'} onClick={() => api.pause(task!.id).catch((reason: Error) => setSendError(reason.message))}
              aria-label={free ? 'Pause' : 'Stop'} title={free ? 'Pause at the next checkpoint: the work is saved and can be resumed (Esc in the message box)' : 'Esc in the message box'}>
              {task?.state === 'pausing' ? <PlayIcon size={16} /> : <PauseIcon size={16} />} <span className={crowded ? 'composer-long-label' : undefined}>{task?.state === 'pausing' ? (task.killed ? 'Stopping…' : 'Pausing…') : free ? 'Pause' : 'Stop'}</span>
            </button>
            {stoppable && <StopButton disabled={!!task?.killed} investigation={!!data?.investigation?.current} compact={crowded} onClick={stop} />}
          </> : paused && (pausedInvestigation || (!draft.trim() && !hasImages)) ? <>
            <button type="button" className="btn btn-primary composer-send" disabled={work.blocked || resuming || sending || !!uploading} onClick={resume}>
              <PlayIcon size={16} /> {resuming ? 'Resuming…' : 'Resume'}
            </button>
            <StopButton disabled={work.blocked || resuming || sending} investigation={pausedInvestigation} onClick={stop} />
          </> : work.queued && !work.active && !work.queued.followup ? (
            <button type="button" className="btn composer-send" onClick={() => api.cancelQueued(work.queued!.id).catch((reason: Error) => setSendError(reason.message))}>
              <CloseIcon size={16} /> Cancel queued turn
            </button>
          ) : (
            <button type="submit" className="btn btn-primary composer-send" disabled={(blocked && !canQueue) || sending || resuming || projectSaving || !!uploading || (!draft.trim() && !hasImages) || (hasImages && !app.status?.image_support)} aria-label={canQueue ? 'Queue' : 'Send'}>
              <SendIcon size={16} /> {uploading ? 'Uploading…' : canQueue ? 'Queue' : 'Send'}
            </button>
          )}
        </div>
        <div className="composer-bar">
          {!free && <Toggle label="Thinking" checked={think} onChange={changeThink} />}
          {free && (budgetDisplay || data?.usage_total) && (
            <details className="composer-usage"><summary>Usage details</summary>
              {data?.usage_total && <p title="Summed over every turn of this conversation, as reported by the model provider where available.">
                This conversation: {count(data.usage_total.input_tokens)} input · {count(data.usage_total.output_tokens)} output tokens · {duration(data.usage_total.seconds)} working time</p>}
              {budgetDisplay && <p title={budgetDisplay.title}>{data?.usage_total ? 'Latest turn: ' : ''}{[budgetDisplay.output, budgetDisplay.reserved, budgetDisplay.uncertain,
                budgetDisplay.context, budgetDisplay.time].filter(Boolean).join(' · ')}</p>}
            </details>
          )}
          {free && <span className="composer-hint">@ selects a skill<span className="composer-long-label">{ours ? ' · Esc pauses' : ' · ↑ recalls your last message'}</span></span>}
        </div>
        <ModelWaitNote taskId={task?.id} />
        {work.queued && (work.active || work.routing || work.queued.followup ? (
          <div className="queued-note" role="status">
            <span><Square variant="ready" size={12} /> Queued: {(data?.queued_followup?.content || work.queued.title)
              ? <Inline limit={90}>{data?.queued_followup?.content || work.queued.title}</Inline> : 'your next message'}.
              {work.active || work.routing ? ' It starts when the current turn finishes.' : paused ? ' It starts after you resume the paused turn.' : ' It starts in a moment.'}</span>
            <button type="button" className="btn btn-quiet btn-small" onClick={() => api.cancelQueued(work.queued!.id).catch((reason: Error) => setSendError(reason.message))}>
              <CloseIcon size={14} /> Cancel
            </button>
          </div>
        ) : <p className="queued-note" role="status">{work.queued.position && work.queued.position > 1 ? `Waiting for a free place: ${work.queued.position - 1} ahead of this one.` : 'Next to start, as soon as a place frees up.'} You can cancel it above.</p>)}
        {!blocked && capacityFull(app.snapshot, app.status) && <BusyNote queue />}
        <ErrorNote>{sendError || (!errorInChat && taskError)}</ErrorNote>
      </form>
    </div>
    {data && <WorkPanel key={data.id} chat={data} />}
    </div>
    </MathMacrosProvider>
  )
}

/** Ends the turn for good, beside Pause or Resume. The square is the notebook's end mark. */
function StopButton({ disabled, investigation, compact, onClick }: { disabled: boolean; investigation: boolean; compact?: boolean; onClick: () => void }) {
  return <button type="button" className="btn btn-delete composer-send" disabled={disabled} onClick={onClick} aria-label="Stop for good"
    title={investigation ? 'Stop the investigation for good and report on the evidence collected so far'
      : 'Stop this turn for good: it cannot be resumed. Jobs it started keep their saved work.'}>
    <StopIcon size={16} /> <span className={compact ? 'composer-long-label' : undefined}>Stop</span>
  </button>
}

function Intro({ mode, project, onPick }: { mode: ChatMode; project?: string; onPick: (text: string) => void }) {
  const intro = INTRO[mode]
  return (
    <div className="entry intro">
      <span className={`in-margin speaker speaker-model mode-${mode}`}>{MODES[mode].glyph}</span>
      <div>
        <h1 className="page-title">{intro.title}</h1>
        <p className="lede">{intro.text}</p>
        {project && <p className="intro-project"><FolderIcon size={15} /><span>This conversation will belong to <strong>{project}</strong> and read its notes.</span></p>}
        {mode === 'free' && <>
          <p className="muted intro-about">See the <a href={href('about')}>About</a> page for the available skills.</p>
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

function Transcript({ items, mode, chatId, lanes, plan, waiting, paused }: { items: TranscriptItem[]; mode: ChatMode; chatId: string; lanes: AgentLane[]; plan?: Chat['investigation']; waiting?: Chat['waiting_for_results']; paused: boolean }) {
  const turns = group(items)
  const groups = groupsOf(lanes)
  const byCard = new Map<number, AgentLane[]>()
  const cardTurns = new Map<number, number>()
  turns.forEach((turn, index) => turn.steps.forEach(step => cardTurns.set(step.index, index)))
  const owners = new Map<string, number>()
  for (const lane of lanes) {
    const owner = lane.card_index == null ? undefined : cardTurns.get(lane.card_index)
    if (owner !== undefined) owners.set(lane.turn_id, owner)
  }
  const unstarted = new Map<number, AgentLane[][]>()
  const finalTurn = plan ? turns.findIndex(turn => turn.steps.some(({ item }) => item.investigation_id === plan.id)) : -1
  const planLane = plan && lanes.find(lane => owners.has(lane.turn_id) &&
    plan.tasks.some(task => task.child_id === lane.id || (!!task.job_id && task.job_id === (lane.job_id || lane.answer_id))))
  const planTurn = finalTurn >= 0 ? finalTurn : plan?.current ? (planLane && owners.get(planLane.turn_id)) ?? turns.length - 1 : -1
  for (const batch of groups) {
    const cards = batch.flatMap(lane => lane.card_index == null ? [] : [lane.card_index])
    if (cards.length) {
      for (const index of cards) byCard.set(index, index === Math.min(...cards) ? batch : [])
    } else {
      // An unstarted historical slot may have no transcript position. Always
      // retain it in Agents; only display it inside a turn of known ownership.
      const owner = owners.get(batch[0].turn_id) ?? (batch.some(activeAgent) ? turns.length - 1 : undefined)
      if (owner !== undefined) {
        const inReport = plan && turns[owner].steps.some(({ item }) => item.investigation_id === plan.id) &&
          batch.every(lane => !activeAgent(lane) && plan.tasks.some(task => task.child_id === lane.id))
        if (inReport) continue
        const previous = unstarted.get(owner)
        if (previous) previous.push(batch)
        else unstarted.set(owner, [batch])
      }
    }
  }
  return (
    <>
      {turns.map((turn, index) => (
        <div key={index} className="turn">
          {turn.user && <UserMessage item={turn.user} call={turn.steps.find(({ item }) => item.role === 'route' && !!item.reason?.startsWith(EXPLICIT_CALL))?.item} />}
          {plan && index === planTurn && <ConversationPlan key={plan.id} plan={plan} finished={finalTurn >= 0} />}
          <ModelSteps steps={turn.steps} mode={mode} chatId={chatId} groups={byCard} prompt={turn.user?.content} paused={paused && index === turns.length - 1} />
          {unstarted.get(index)?.map(batch => <AgentGroup key={batch[0].id} lanes={batch} prompt={turn.user?.content} chatId={chatId} />)}
          {index === turns.length - 1 && <WaitingForResults tasks={waiting} />}
          {plan && index === planTurn && finalTurn < 0 && <WorkingAnswer plan={plan} chatId={chatId} />}
        </div>
      ))}
    </>
  )
}

// The route card the harness saves for a skill the person picked with @ (mode_calls.py).
const EXPLICIT_CALL = 'User explicitly selected @'

/** The messages sent in a conversation, oldest first, as they were typed: a skill picked with @ leads its message again. */
function sentMessages(transcript: TranscriptItem[]) {
  const sent: string[] = []
  transcript.forEach((item, index) => {
    if (item.role !== 'user' || !item.content.trim()) return
    let call: TranscriptItem | undefined
    for (let next = index + 1; next < transcript.length && transcript[next].role !== 'user' && !call; next++) {
      if (transcript[next].role === 'route' && transcript[next].reason?.startsWith(EXPLICIT_CALL)) call = transcript[next]
    }
    const text = call?.mode && call.mode !== 'clarify' ? `@${call.mode} ${item.content}` : item.content
    if (sent.at(-1) !== text) sent.push(text)
  })
  return sent
}

function UserMessage({ item, call }: { item: TranscriptItem; call?: TranscriptItem }) {
  const files = (item.files || []).filter(path => !item.images?.length || !isImagePath(path))
  const attached = files.filter(path => item.attached_files?.includes(path))
  const available = files.filter(path => !item.attached_files?.includes(path))
  const picked = call?.mode && call.mode !== 'clarify' ? call.mode : null
  const cover = picked && picked !== 'critic' && picked !== 'free' ? KINDS[kindOf(picked)].cover : null
  return (
    <div className={'entry message message-user' + (item.discarded ? ' message-discarded' : '')}>
      <span className="in-margin speaker speaker-user">You</span>
      <div className="message-body">
        {picked && <p className="message-call" title="You selected this skill for the message">
          {cover && <span className={`route-cover mode-${cover}`} aria-hidden="true"><Glyph mode={cover} /></span>}
          <strong>@{picked}</strong>{call?.effort && effortKind(picked as RouteMode) ? <span> · {effortLabel(call.effort)} effort</span> : null}
        </p>}
        <Markdown quiet>{literalUnderscores(item.content)}</Markdown>
        {attached.length > 0 && (
          <div className="attachments" role="group" aria-label="Attached to this message">{attached.map(path =>
            <span key={path} className="chip chip-static" title={path}><FileIcon size={13} /><span>{displayPath(path)}</span></span>
          )}</div>
        )}
        {available.length > 0 && (
          <p className="message-sources"><span>{item.attached_files ? 'Available for this reply' : available.length === 1 ? 'Source' : 'Sources'}</span>
            {' · '}{available.map(displayPath).join(', ')}</p>
        )}
        {!!item.images?.length && <ImageGallery images={item.images} />}
        {item.time && <span className="message-time">{ago(item.time)}</span>}
      </div>
    </div>
  )
}

const PAUSE_NOTICE = 'Assistant paused.'
const clock = (time: string) => { const date = new Date(time); return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) }

function ModelSteps({ steps, mode, chatId, groups, prompt, paused }: { steps: Step[]; mode: ChatMode; chatId: string; groups: Map<number, AgentLane[]>; prompt?: string; paused: boolean }) {
  const out: ReactElement[] = []
  let blocks: ReactElement[] = []
  // An Assistant conversation answers with one voice, whichever engine mode wrote it, except
  // a literature check, which keeps its citation mark; older critique and exploration chats
  // keep their own mark.
  let mark: ChatMode | 'check' | 'investigate' = mode
  const flush = (key: number) => {
    if (!blocks.length) return
    out.push(
      <div key={'answer' + key} className="entry message message-model">
        <span className={`in-margin speaker speaker-model mode-${mark}`} title={mark === 'investigate' ? 'Investigation' : mark === 'check' ? 'Literature Check' : MODES[mark].label}><Glyph mode={mark} /></span>
        <div className="message-body">{blocks}</div>
      </div>,
    )
    blocks = []
  }
  const markFor = (item: TranscriptItem): ChatMode | 'check' | 'investigate' =>
    item.investigation_id ? 'investigate' : item.mode === 'check' ? 'check' : item.mode === 'free' ? 'free' : mode
  for (let i = 0; i < steps.length; i++) {
    const { item, index } = steps[i]
    if (item.role === 'route') {
      flush(index)
      const batch = mode === 'free' && item.orchestrated ? groups.get(index) : undefined
      if (!batch) out.push(<RouteCard key={'route' + index} chatId={chatId} index={index} item={item} />)
      else if (batch.length) out.push(<AgentGroup key={batch[0].id} lanes={batch} prompt={prompt} chatId={chatId} />)
    } else if (item.role === 'assistant') {
      if (markFor(item) !== mark) { flush(index); mark = markFor(item) }
      const calls = item.tool_calls || []
      const results: TranscriptItem[] = []
      while (i + 1 < steps.length && steps[i + 1].item.role === 'tool' && results.length < calls.length) results.push(steps[++i].item)
      const final = !calls.length
      blocks.push(
        <div key={index} className="step">
          <Thinking text={item.thinking || ''} />
          {calls.map((call, n) => groups.size > 0 && call.function?.name === 'delegate' ? null :
            QUIET_TOOLS.has(call.function?.name || '') ? <p key={n} className="tool-step tool-line">{toolLine(call)}</p> : (
            <Collapse key={n} className="tool-step" summary={<span className="tool-line">{toolLine(call)}</span>}>
              <pre className="plain">{results[n]?.content ?? 'No result was recorded.'}</pre>
            </Collapse>
          ))}
          {item.content && (item.investigation_id ? <InvestigationReport content={item.content} chatId={chatId} synthesis={item.synthesis} covered={item.synthesis_steps} />
            : <Markdown className={final ? 'answer' : 'interim'}>{item.content}</Markdown>)}
          {final && item.content && <div className="message-actions">
            <CopyButton label="Copy" title="Copy this answer as Markdown, with its LaTeX source" text={item.content} />
          </div>}
          {final && item.stats?.done_reason === 'length' && (
            <span className="message-time">The answer reached its length limit.</span>
          )}
        </div>,
      )
    } else if (item.role === 'review') {
      blocks.push(
        <div key={index} className="review-note">
          <p className="review-label">Fresh-context review</p>
          <p className="muted small">A separate model context audited the previous answer. The review is a model judgment.</p>
          <Thinking text={item.thinking || ''} />
          <Markdown>{item.content}</Markdown>
        </div>,
      )
    } else if (item.role === 'notice') {
      // Older saved turns repeat a paused worker's card as a discarded message.
      if (item.discarded && steps.some(({ item: card }) => card.role === 'route' && card.orchestrated &&
          card.status === 'stopped' && card.error && item.content === 'Not added to the conversation: ' + card.error)) continue
      if (item.content.startsWith(PAUSE_NOTICE)) {
        // While paused, the paused note under the conversation says it once; afterwards this is history.
        const later = steps.slice(i + 1).some(({ item: next }) => next.role === 'notice' && next.content.startsWith(PAUSE_NOTICE))
        if (paused && !later) continue
        blocks.push(<p key={index} className="notice notice-history">Paused{item.time && clock(item.time) ? ` at ${clock(item.time)}` : ''}.</p>)
        continue
      }
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
  const mode = item.mode
  const kind = kindOf(mode)
  const info = mode === 'critic'
    ? { noun: 'a critique', label: 'Critique', cover: 'critic' as const }
    : item.orchestrated && mode === 'explore'
      ? { noun: 'an exploration', label: 'Exploration', cover: 'explore' as const }
      : KINDS[kind]
  const effort = kind === 'answer' || FIXED_EFFORT[mode as RouteMode] ? '' : `${effortLabel(item.effort)} effort`
  const act = async (action: () => Promise<unknown>) => {
    setActing(true)
    setError('')
    try { await action() } catch (reason) { setError((reason as Error).message) } finally { setActing(false) }
  }
  // Suggestions saved before jobs started on their own, and jobs that failed or were
  // cancelled before running, can be started (again) as the model chose them.
  const start = (activeMode: RouteMode) => act(() => {
    const effort = effortKind(activeMode)
    const limits = effort && app.status ? effortLimits(effort, app.status.defaults, !!FIXED_EFFORT[activeMode]) : undefined
    return api.startRoute(chatId, index, { mode: activeMode, request: item.request || '', files: item.files || [], limits: limits as Record<string, number> | undefined })
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
    if (job) ({ variant, label: status } = researchLook(job.status, job.running && (!item.orchestrated || running), job.kind))
  }
  let headline: string
  if (mode === 'critic' && item.status !== 'done' && !queued && !running) { headline = 'Critique retired'; status = ''; variant = 'spent' }
  else if (item.status === 'proposed') { headline = `${info.label} suggested`; status = 'Not started'; variant = 'ready' }
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
            {item.outputs?.download && item.outputs.body && <a className="btn btn-small" href={api.researchBodyUrl(item.job_id)} download={item.outputs.body} title="The text alone, without preamble, title or bibliography: paste it into your paper or \input it. Definitions it needs are listed at its top.">Body .tex</a>}
            {item.outputs?.review && <a className="btn btn-small btn-primary" href={api.reviewMarkdownUrl(item.job_id)} download>{item.status === 'stopped' ? 'Download partial review (.md)' : 'Download review (.md)'}</a>}
            {item.outputs?.bib && <a className={'btn btn-small' + (item.outputs.review ? '' : ' btn-primary')} href={item.outputs.review ? api.reviewBibtexUrl(item.job_id) : api.researchBibDownloadUrl(item.job_id)} download>BibTeX (.bib)</a>}
          </>}
          {item.job_id && paged(mode) && <a className="btn btn-small btn-quiet" href={mode === 'literature'
            ? href('free', chatId, null, null, { tab: 'outputs', selected: item.job_id })
            : href(pageOf(mode), item.job_id, null, chatId)}>{mode === 'literature' ? 'Read literature review' : 'Open the ' + info.label.toLowerCase()}</a>}
          {mode !== 'critic' && mode !== 'free' && !item.orchestrated && ['proposed', 'failed', 'cancelled'].includes(item.status || '') && (
            <button type="button" className="btn btn-small" disabled={acting} onClick={() => start(mode)}>
              {item.status === 'proposed' ? 'Start' : 'Start again'}
            </button>
          )}
          <Collapse className="route-details" summary={(kind === 'answer' || kind === 'check') && !item.orchestrated ? 'Question as the model sees it' : `Request given to the ${info.label.toLowerCase()}`}>
            <div className="route-summary"><Inline mathProse limit={1200}>{item.request || ''}</Inline></div>
            {item.files && item.files.length > 0 && (
              <div className="attachments">{item.files.map((path) => <span key={path} className="chip chip-static" title={path}><FileIcon size={13} /><span>{displayPath(path)}</span></span>)}</div>
            )}
            {item.missing && item.missing.length > 0 && <p className="muted small">Not found in this folder: {item.missing.join(', ')}.</p>}
            {item.reason && <p className="muted small">Why: {item.reason}</p>}
          </Collapse>
        </div>
        {item.answer_id && <SavedWorkerAnswer key={item.answer_id} chat={chatId} id={item.answer_id} />}
        {item.outcome && item.outcome !== error && !(item.status === 'stopped' && error && item.outcome.includes(error)) &&
          <p className="muted small">{item.outcome}</p>}
        <ErrorNote>{error}</ErrorNote>
      </div>
    </div>
  )
}

function SavedWorkerAnswer({ chat, id }: { chat: string; id: string }) {
  const [result, setResult] = useState<{ answer: string; status: string } | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toggle = async () => {
    if (open || result) { setOpen(!open); return }
    setLoading(true)
    setError('')
    try { setResult(await api.savedChatAnswer(chat, id)); setOpen(true) }
    catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }
  return <div>
    <button type="button" className="btn btn-small btn-quiet" aria-expanded={open} disabled={loading} onClick={toggle}>
      {loading ? 'Loading…' : open ? 'Hide worker answer' : 'Read worker answer'}
    </button>
    {open && result && (result.answer ? <Markdown>{result.answer}</Markdown> :
      <p className="muted small">This worker saved no answer. Its final status was {result.status}.</p>)}
    <ErrorNote>{error}</ErrorNote>
  </div>
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

export function ArtifactView({ artifact, purpose }: { artifact: Artifact; purpose?: string }) {
  const display = displayOf(artifact.mime_type)
  const caption = (
    <figcaption>
      {purpose ? `${purpose} · ` : ''}
      <a href={artifact.url} target="_blank" rel="noopener noreferrer">{artifact.filename}</a>
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

function LiveTurnView({ live, mode, investigating, agentsWorking, waitingForOutput }: { live: LiveTurn; mode: ChatMode; investigating: boolean; agentsWorking: boolean; waitingForOutput: boolean }) {
  const glyph = investigating ? 'investigate' : live.mode ?? mode
  const steps = agentsWorking ? live.steps.filter(step => !('progress' in step && step.progress)) : live.steps
  if (agentsWorking && !steps.length && !live.user && live.kind !== 'review') return null
  return (
    <div className="turn turn-live">
      {live.kind === 'message' && live.user && <UserMessage item={{ role: 'user', content: live.user, time: '' }} />}
      <div className="entry message message-model">
        <span className={`in-margin speaker speaker-model mode-${glyph}`}><Glyph mode={glyph} /></span>
        <div className="message-body">
          {live.kind === 'review' && <p className="review-label">Fresh-context review in progress</p>}
          {steps.map((step, index) => step.type === 'call' ? (
            <div key={index} className="step">
              <Thinking text={step.thinking} live={!step.text} />
              {step.text && <StreamingMarkdown text={step.text} className="answer" />}
              {!step.text && !step.thinking && !waitingForOutput && index === steps.length - 1 && (
                <p className="muted small">Preparing a response…</p>
              )}
            </div>
          ) : step.type === 'tool' ? (
            // The agents block above already shows each delegated task with its own state.
            step.text.startsWith('Delegating a mathematical task') ? null :
            <p key={index} className="tool-line tool-live">{step.text}</p>
          ) : step.type === 'result' ? (
            <p key={index} className="tool-result-live">{step.text}</p>
          ) : step.type === 'experiment' ? (
            <ExperimentFigures key={index} item={step.item} />
          ) : (
            <p key={index} className="notice">{step.text}</p>
          ))}
          {!steps.length && !agentsWorking && !waitingForOutput && <p className="muted small">Waiting for the model…</p>}
        </div>
      </div>
    </div>
  )
}
