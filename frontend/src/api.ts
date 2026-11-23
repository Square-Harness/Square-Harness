// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Typed client for the harness's local /api. Shapes mirror mathagent/gui/store.py.
import type { EffortLevel } from './effort'

export type Budget = { used: number; limit: number | null; reserved?: number; uncertain?: number }

export type WorkProgress = {
  phase: string
  completed: number
  total: number
  unit: string
  scope: string
  partial: boolean
  resumable?: boolean
}

export type AssistantBudget = {
  tokens: Budget
  input_tokens?: { used: number; limit: number | null }
  seconds: Budget
  context?: Budget
}

export type Defaults = {
  api_profile?: string
  literature_presets?: Partial<Record<EffortLevel, { seconds: number; requests: number }>>
  budget_profile?: string
  effort_presets?: Partial<Record<EffortLevel, {
    tries: number; minutes: number; tokens: number; research_tokens: number; input: number
    requests: number; chars: number; solve_tokens: number; verify_tokens: number
    repair_tokens?: number; proof_seconds?: number; proof_call_seconds?: number
  }>> | null
  ctx: number
  predict: number
  think: boolean
  proof_rounds: number
  proof_tokens: number | null
  proof_seconds: number
  proof_call_seconds?: number | null
  proof_solve_tokens: number
  proof_verify_tokens: number
  proof_repair_tokens: number | null
  proof_min_solve_tokens: number | null
  research_rounds: number
  research_tokens: number | null
  research_input_tokens: number | null
  research_seconds: number
  research_requests: number
  research_chars: number
}

export type TesterAllowance = {
  limit_nanos: number
  charged_nanos: number
  reserved_nanos: number
  input_tokens: number
  output_tokens: number
  input_per_million: string
  output_per_million: string
}

export type Account = {
  email: string
  logout_url: string
}

export type Personalization = {
  instructions: string
  macros: string
  revision: string
}

export type Project = {
  id: string
  name: string
  notes: string
  revision: string
  folder: string
  created_at: string
  updated_at: string
}

export type FeedbackCategory = 'bug' | 'suggestion' | 'math'
export type FeedbackImage = { name: string; mime: string; data: string }
export type FeedbackConfig = { enabled: boolean; delivery_configured: boolean; delivery_mode?: 'slack_files' | 'webhook' | 'saved'; max_images: number; max_image_bytes: number }
export type FeedbackPreview = { files: { name: string; size: number }[]; size: number; omissions?: { name: string; reason: string }[]; scope?: string; redacted?: boolean }
export type FeedbackResult = { id: string; status: 'delivered' | 'saved'; delivery_error?: string; archive_url: string }
export type ImageRef = { sha256: string; mime: string; name: string; width: number; height: number; bytes: number }

export type Status = {
  version: string
  instance: string
  seq: number
  workspace: string
  model: string
  /** The model is the built-in default: the user configured none. */
  model_default?: boolean
  host: string
  backend: 'ollama' | 'openai' | 'llamacpp'
  image_support?: boolean
  online: boolean
  online_locked: boolean
  allow_python: boolean
  lan: boolean
  defaults: Defaults
  concurrency?: Concurrency
  tester_allowance?: TesterAllowance | null
  ollama: { reachable: boolean; models: string[]; error?: string }
  pair_urls?: string[]
  root: string
  workspace_path: string
  read: ReadSettings
  latex: boolean
  recent: string[]
}

export type ReadSettings = { tex: boolean; pdf: boolean; py: boolean; text: boolean }

export type FolderProject = { id: string; name: string; deleted?: boolean }
export type FolderListing = {
  root: string
  path: string
  current: string
  recent: string[]
  files: { tex: number; pdf: number; py: number }
  folders: { name: string; path: string; files: { tex: number; pdf: number; py: number }; jobs: boolean
    /** A project's own file folder: shown by its name, never opened as a workspace. */
    project?: FolderProject | null }[]
  /** Set when the listed folder itself is a project's file folder. */
  project?: FolderProject | null
  /** The listed folder is a workspace's projects/ folder. */
  holds_projects?: boolean
}

export type Template = { name: string; files: string[] }

/** Which saved style write-ups use: chosen automatically, none, one style (its best domain), or one domain. */
export type StyleChoice = { mode: 'auto' | 'none' | 'style' | 'domain'; style?: string; domain?: string }

/** A saved writing style (mathagent/style.py): one JSON file per name, shared by every folder. */
/** How the author writes one kind of paper. Version 1 styles have `habits` instead of the explanation fields. */
export type StyleDescription = {
  audience?: string
  aim: string
  structure: string[]
  definitions?: string[]
  motivation?: string[]
  proof_motivation?: string[]
  proof_organisation?: string[]
  omissions?: string[]
  contribution?: string[]
  tone?: string[]
  names?: { term: string; note: string }[]
  typesetting?: { habit: string; example: string }[]
  habits?: string[]
  phrases: string[]
  /** Write-up rules the author does not follow: one article's (with its example), or a domain's (counted over its articles). */
  departures?: StyleDeparture[]
}
export type StyleDeparture = { rule: string; habit: string; example?: string; examples?: string[]; sheet?: string; rule_text?: string; articles?: number; of?: number }
export type DomainStyle = StyleDescription & { name: string; description: string; articles: string[] }
/** Corrections of one domain of a saved style (POST /api/styles/<name> with items): each field given replaces it;
 * departures and macros list what to keep. */
export type StyleEdit = Partial<Pick<StyleDescription, 'audience' | 'aim' | 'structure' | 'definitions' | 'motivation' | 'proof_motivation'
  | 'proof_organisation' | 'omissions' | 'contribution' | 'tone' | 'names' | 'typesetting' | 'phrases'>> & {
  domain?: string; description?: string; departures?: string[]; macros?: string[]
}
export type StyleProfile = {
  format: 'square-style'
  version: 1 | 2
  name: string
  created_at: string
  updated_at: string
  articles: { path: string; sha256: string; title: string; domain: string; source_archive?: string }[]
  packages?: { name: string; options: string; articles: string[] }[]
  macros: { name: string; definition: string; uses: number; articles: string[]; usage?: string; packages?: string[] }[]
  environments: { name: string; definition: string; uses: number }[]
  domains: DomainStyle[]
  /** The LaTeX template write-ups in this style build on: names and sizes when listed, contents in a job. */
  template?: { name: string; size?: number; content?: string }[]
}

export type TaskState = 'queued' | 'starting' | 'running' | 'pausing' | 'paused' | 'done' | 'error' | 'cancelled'

export type TaskSummary = {
  /** A message queued to run after its conversation's current turn. */
  followup?: boolean
  waiting_model_calls?: number
  position?: number
  queued_at?: number | null
  slot?: boolean
  id: string
  kind: 'proof' | 'research' | 'chat'
  target: string | null
  origin_chat?: string | null
  label: string
  title: string
  state: TaskState
  error: string | null
  result_status: string | null
  started: number
  finished: number | null
}

export type Approval = {
  id: string
  task: string
  task_kind: string
  target: string | null
  kind: 'write' | 'python' | 'confirm'
  preview: string
}

export type LiveStep =
  | { type: 'call'; thinking: string; text: string }
  | { type: 'tool' | 'result' | 'notice'; text: string; progress?: boolean }
  | { type: 'experiment'; item: TranscriptItem }  // a sandbox run's files, shown as soon as they exist

export type LiveTurn = { chat: string; kind: 'message' | 'review'; user: string; steps: LiveStep[]; mode?: 'explore' | 'free' | 'check' }

export type AgentOrigin = {
  child_id?: string; card_index?: number; turn_id?: string; batch_id?: string
}
export type Activity = AgentOrigin & {
  kind: 'notice' | 'tool' | 'result'; text: string; time: number; job?: string; id?: string | null
}
export type AgentStatus = 'waiting' | 'running' | 'finished' | 'failed' | 'stopped'
export type Agent = {
  id: string; turn_id: string; batch_id: string; mode: string; objective: string; effort: string
  status: AgentStatus; card_index: number | null; job_id: string | null; answer_id: string | null
  started_at: string | null; ended_at: string | null; progress: WorkProgress | null
  allowance: number | null; usage: { input_tokens?: number; output_tokens?: number; seconds?: number; requests?: number }
  error: { code: string; scope?: string; retryable?: boolean; message?: string } | null
  outcome: string; timeline: Activity[]
}

export type Concurrency = { chats: number; requests: number; active: number }
export type TaskEntry = { task: TaskSummary; activity: Activity[]; live: LiveTurn | null }

export type TaskSnapshot = {
  seq: number
  task: TaskSummary | null
  activity: Activity[]
  live: LiveTurn | null
  approvals: Approval[]
  queue: TaskSummary[]
  routing: string[]
  tasks?: TaskEntry[]
  concurrency?: Concurrency
}

// Proof jobs (v0.5 engine): immutable candidates, whole-proof reviews, recorded calls.
export type ReviewStatus = 'unreviewed' | 'no_issue_found' | 'issues_found' | 'uncertain' | 'review_unavailable'

export type Candidate = {
  id: string
  attempt: number
  call: string
  artifact: string
  sha256: string
  parent: string | null
  kind: 'initial' | 'repair' | 'continue' | 'retry'
  transport_complete: boolean
  review_status: ReviewStatus
}

export type ReviewIssue = { kind: 'invalid_inference' | 'missing_justification' | 'uncertainty'; location: string; evidence: string }

export type ReviewResponse = {
  verdict: 'no_issue_found' | 'issues_found' | 'uncertain'
  explanation: string
  issues: ReviewIssue[]
  schema_version?: 2 | 3
  verdict_source?: 'controller_task_status_v3'
  task_status?: 'proved' | 'refuted' | 'incomplete' | 'uncertain'
  critical_dependencies?: { claim: string; provenance: 'given' | 'proved' | 'standard' | 'unverified'; check: string }[]
  notes?: string[]
}

export type Review = {
  id: string
  candidate: string
  candidate_sha256: string
  call: string
  response: ReviewResponse | null
  protocol_error: string | null
}

export type Pending = {
  phase: 'solve' | 'review' | 'continue' | 'repair'
  kind: Candidate['kind']
  parent: string | null
  review: string | null
  index: number
  review_attempt: number
  candidate?: string
  solver_cap?: number
  continuation_fallback?: string
  notes_chars_kept?: number
  notes_chars_total?: number
}

export type Selection = { previous: string | null; selected: string; reason: string; after_call_count: number }

export type Call = {
  key?: string
  role: string
  round: number
  status: string
  reserved_tokens: number
  charged_tokens?: number
  stream: string
  request: string
  seconds?: number
  stats?: { eval_count?: number; prompt_eval_count?: number; done_reason?: string }
  context?: { input_tokens: number; method: string }
}

export type Live = { file: string; role: string; round?: number } | null

export type ProofListItem = {
  id: string
  kind: 'proof'
  version: number
  status: string
  title: string
  updated_at: string
  created_at: string
  running: boolean
  sources: string[]
  rounds: Budget
  tokens: Budget
  reviews: Partial<Record<ReviewStatus, number>>
}

export type ProofDetail = {
  owner_task?: TaskSummary | null
  id: string
  kind: 'proof'
  version: number
  // Made by the v0.4 engine: readable, never resumed.
  legacy: boolean
  goal: string
  status: string
  stop_reason: string | null
  recovery_notice: string | null
  created_at: string
  updated_at: string
  revision: number
  settings: Record<string, unknown> & {
    max_rounds: number; model: string; ctx: number; max_predict?: number; verify_tokens?: number
    specialist_output_cap?: number | null
    repair_tokens?: number; verify_temperature?: number; harness_version?: string
  }
  budget: { rounds: Budget; tokens: Budget; seconds: Budget; context?: Budget }
  progress?: WorkProgress | null
  sources: { path: string; sha256?: string; lines: number }[]
  candidates: Candidate[]
  reviews: Review[]
  calls: Call[]
  pending: Pending | null
  selected_candidate: string | null
  initial_candidate: string | null
  selection_history: Selection[]
  // The selected candidate's exact text (for a legacy job, its audited proof.md).
  answer: string | null
  running: boolean
  live: Live
  artifacts: { name: string; size: number }[]
  /** The saved work can continue with an explicit extra allowance (unfinished, or stopped on a budget). */
  can_add_allowance?: boolean
  allowance_additions?: { time: string; tokens?: number; seconds?: number; rounds?: number }[]
}

export type Evidence = {
  id: string
  tool: string
  arguments: Record<string, unknown>
  artifact: string
  result: string
  round: number
}

export type ResearchListItem = {
  id: string
  kind: 'literature' | 'referee' | 'writeup' | 'style'
  status: string
  phase: string
  title: string
  updated_at: string
  created_at: string
  running: boolean
  sources: string[]
  tokens: Budget
  variant?: ReviewVariant | null
  verdict?: string | null
}

/** The four kinds of review behind the Review notebook (engine kind 'referee'). */
export type ReviewVariant = 'quick' | 'review' | 'journal' | 'explain'

export type WriteupSection = { title: string; sources: string[]; goal: string; latex: string; complete: boolean; truncated: boolean }
// The outline call's plan, as the controller settled it (writeup.WriteupRunner._settle_outline).
export type WriteupPlan = {
  kind: string; scope: 'document' | 'part'; language: string; title: string; short_title: string; instruction: string
  requirements: string[]; inventory: { id: string; type: string; text: string; status: string }[]
  problems: { inventory_id: string; what: string }[]; notation: { object: string; symbol: string; also_written: string }[]
  sections: { heading: string; role: string; source: 'material' | 'composed'; covers: string[]; parts: string[]; requested_by: string; purpose: string }[]
  needs: { type: string; what: string; concerns: string[]; blocking: boolean }[]
  dropped_sections?: string[]; unassigned_parts?: string[]
}

export type CompileResult = {
  available: boolean
  ok: boolean
  errors: { file: string | null; line: number | null; message: string }[]
  log: string | null
  failing_sections: number[]
  pdf: boolean
}

/** Literature jobs by pipeline: reviews, and the reading lists that older jobs built. */
export const REVIEW_PIPELINE = 'literature-review-v2'
export const LIST_PIPELINE = 'reading-list-v1'

/** Reference [k] of a literature review: pool record n, and how far the review read it. */
export type ReviewReference = {
  k: number
  n: number
  title: string
  authors: string[]
  year?: number | string | null
  venue?: string | null
  doi?: string | null
  arxiv?: string | null
  zbl?: string | null
  url?: string | null
  preprint: boolean
  read: 'full_text' | 'abstract' | 'metadata'
}

export type ReviewEvidence = Pick<ReviewReference, 'n' | 'title' | 'doi' | 'arxiv' | 'zbl'> & {
  k: number | null
  s2?: string | null
  abstract: string
  review?: string
  passages: { lines: string; locator: string; text: string }[]
}

/** What a literature review searched (queries asked and answered per source) and kept. */
export type ReviewCoverage = {
  sources: Record<string, { asked: number; answered: number; errors: string[] }>
  candidates: number
  screened: number
  selected: number
  cited: number
  full_text: number
}

export type ResearchDetail = {
  owner_task?: TaskSummary | null
  id: string
  kind: 'literature' | 'referee' | 'writeup' | 'style'
  goal: string
  status: string
  phase: string
  stop_reason: string | null
  created_at: string
  updated_at: string
  settings: Record<string, unknown> & { online?: boolean; model: string }
  budget: { rounds: Budget; tokens: Budget; input_tokens: Budget; seconds: Budget; requests: Budget; chars: Budget; context?: Budget }
  progress?: WorkProgress | null
  sources: { id: string; path: string; sha256: string; lines: number }[]
  plan: string
  notes: { round: number; title?: string; text: string; complete: boolean }[]
  pipeline?: string | null
  variant?: ReviewVariant | null
  verdict?: string | null
  level?: string | null
  unit?: string | null
  evidence: Evidence[]
  draft: string
  review: string
  draft_complete?: boolean
  review_complete?: boolean
  warnings: string[]
  citation_issues: string[]
  review_context_issues: string[]
  manuscript_ranges: Record<string, [number, number][]>
  calls: Call[]
  report: string
  running: boolean
  live: Live
  artifacts: { name: string; size: number }[]
  outline?: { title: string; sections: { title: string; sources: string[]; goal: string }[] } | WriteupPlan | null
  sections?: WriteupSection[]
  document?: string
  conversion_source?: string | null
  section_lines?: [number, number][]
  compile?: CompileResult | null
  outputs?: { tex?: string; pdf?: string | boolean; download?: string; source?: string; body?: string; review?: string; bib?: string }
  // Write-ups: the controller's gate, the outline's needs and the review rounds.
  readiness?: 'ready' | 'draft' | null
  gates?: { gate: string; message: string }[]
  needs?: { type: string; what: string; concerns: string[]; blocking: boolean }[]
  reviews?: { round: number; issues: { severity: string }[]; dropped: number; complete: boolean }[]
  review_round?: number
  candidates?: { round: number; score: number[]; readiness: string | null; compile_ok: boolean; selected: boolean }[]
  // Literature reviews (pipeline literature-review-v2) only.
  references?: ReviewReference[]
  review_evidence_count?: number
  coverage?: ReviewCoverage | null
  macros?: string[]
  theorems?: string[]
  output_name?: string
  style_name?: string
  title?: string
  style_checked?: boolean
  parts?: { complete: boolean; characters: number }[]
  style?: { label: string; profile: string; domain: string } | null
  articles?: { id: string; path: string; title: string; abstract: string; headings: string[]; environments: Record<string, number>; inputs: string[]; lines: number; source_archive?: string; source_kind?: 'pdf_text' }[]
  domains?: { name: string; description: string; articles: string[]; complete: boolean }[]
  observations?: Record<string, StyleDescription & { complete: boolean }>
  profile?: StyleProfile | null
  saved_to?: string | null
}

export type ChatListItem = {
  agent_statuses?: Pick<Agent, 'id' | 'mode' | 'status'>[]
  project_id?: string | null
  id: string
  kind: 'critic' | 'explore' | 'free'
  title: string
  updated_at: string
  created_at: string
  messages: number
  preview: string
  math_macros?: Record<string, string>
  /** With a search (`GET /api/chats?q=`): a short excerpt around the first match. */
  match?: string
}

export type ToolCall = { id?: string; function: { name: string; arguments: Record<string, unknown> | string } }

export type RouteMode = 'investigate' | 'prove' | 'explore' | 'check' | 'literature' | 'referee' | 'detailed_review' | 'quick_review' | 'explain' | 'writeup' | 'summarize'
  | 'python_lab' | 'sanity_check'

export type TranscriptItem = AgentOrigin & {
  investigation_id?: string
  synthesis?: string
  synthesis_steps?: string[]
  status_time?: string
  started_at?: string
  ended_at?: string
  error_code?: string
  role: 'user' | 'assistant' | 'tool' | 'review' | 'notice' | 'route' | 'experiment'
  content: string
  time: string
  thinking?: string
  stats?: { eval_count?: number; prompt_eval_count?: number; done_reason?: string }
  tool_calls?: ToolCall[]
  tool_name?: string
  discarded?: boolean
  files?: string[]
  attached_files?: string[]
  images?: ImageRef[]
  mode?: RouteMode | 'critic' | 'clarify' | 'free'
  request?: string
  reason?: string
  question?: string
  status?: 'proposed' | 'starting' | 'queued' | 'started' | 'done' | 'failed' | 'cancelled' | 'stopped' | 'dismissed'
  job_id?: string
  answer_id?: string
  outputs?: { download?: string; pdf?: boolean; tex?: string; source?: string; body?: string; review?: string; bib?: string }
  task?: string
  error?: string | null
  missing?: string[]
  effort?: string
  orchestrated?: boolean
  outcome?: string
  // A sandbox run's files (role 'experiment'), served by /api/artifacts/<id>/<name>.
  experiment?: string
  run_status?: string
  purpose?: string
  artifacts?: import('./artifacts.ts').Artifact[]
}

export type RecordEntry = {
  id: string; job_id: string; chat_id: string | null; scope: string; folder: string
  mode: string; kind: string; status: string; direction: string | null; qualifiers: string[]
  task: { text: string; text_truncated?: boolean }
  summary: string; created_at: string; evidence: Record<string, unknown>
  documents?: { document_id: string; title?: string; passages?: { range: string; label?: string }[] }[]
}
export type ResearchIndex = { revision?: string; scope: string; entries: RecordEntry[]; scopes: { key: string; count: number }[] }
export type Chat = {
  agents?: Agent[]
  waiting_for_results?: { mode: string; objective: string }[]
  agent_concurrency?: number | null
  images?: ImageRef[]
  title_source?: string | null
  project_id?: string | null
  id: string
  mode: 'critic' | 'explore' | 'free'
  title: string
  settings: { think: boolean; model: string; online?: boolean }
  created_at: string
  updated_at: string
  transcript: TranscriptItem[]
  context_messages: number
  math_macros?: Record<string, string>
  assistant_status?: 'queued' | 'running' | 'paused' | 'complete' | 'incomplete' | 'budget_exhausted' | 'failed'
  investigation?: { id: string; phase: string; statement: string; reading?: string; approved: boolean; can_stop: boolean; current: boolean; usage?: AssistantBudget | null
    synthesis?: string; synthesis_steps?: string[]; target_reviews?: string[]
    ceiling: { tokens?: number; input_tokens?: number; seconds?: number; requests?: number; chars?: number } | null
    tasks: { id: string; mode: string; objective: string; after: string[]; status: string
      effort?: string; claim?: string; child_id?: string | null; job_id?: string | null; reason?: string | null }[]
  } | null
  assistant_resumable?: boolean
  assistant_budget?: AssistantBudget
  assistant_progress?: WorkProgress | null
  /** A message waiting to run after the current turn (`POST /route` with `queue: true`). */
  queued_followup?: { task: string; content: string } | null
  /** Usage over every turn of the conversation, provider-reported where available. */
  usage_total?: { input_tokens: number; output_tokens: number; seconds: number } | null
}

export type StreamChunk = {
  file: string
  start: number
  end: number
  reset: boolean
  text: string
  thinking: string
  tool_calls: ToolCall[]
  done: boolean
  stats: Record<string, unknown>
}

export type Artifact = {
  name: string
  size: number
  text: string
  json?: unknown
  stream?: { text: string; thinking: string; tool_calls: ToolCall[]; done: boolean; stats: Record<string, unknown> }
}

export type WorkspaceFile = { path: string; size: number; kind: 'text' | 'pdf' | 'image' }
export type ProjectFile = { path: string; name: string; size: number; updated_at: string }
/** An explicit extra allowance for resuming a job: recorded in its state and report. */
export type AllowanceAdditions = { add_tokens?: number; add_seconds?: number; add_rounds?: number }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'The harness is not reachable. Check that square-harness --gui is still running.')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401) authListeners.forEach((listener) => listener())
    throw new ApiError(response.status, (data && data.error) || response.statusText)
  }
  return data as T
}

/** The findings record is large and mostly unchanged between two events: an unchanged revision answers 304. */
const indexPages = new Map<string, ResearchIndex>()
async function indexPage(path: string): Promise<ResearchIndex> {
  const known = indexPages.get(path)
  let response: Response
  try {
    response = await fetch(path, { credentials: 'same-origin', headers: known?.revision ? { 'If-None-Match': '"' + known.revision + '"' } : {} })
  } catch {
    throw new ApiError(0, 'The harness is not reachable. Check that square-harness --gui is still running.')
  }
  if (response.status === 304 && known) return known
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401) authListeners.forEach((listener) => listener())
    throw new ApiError(response.status, (data && data.error) || response.statusText)
  }
  indexPages.set(path, data as ResearchIndex)
  if (indexPages.size > 12) indexPages.delete(indexPages.keys().next().value!)
  return data as ResearchIndex
}

const authListeners = new Set<() => void>()
export function onUnauthorized(listener: () => void) {
  authListeners.add(listener)
  return () => authListeners.delete(listener)
}

const get = <T,>(path: string) => request<T>('GET', path)
const post = <T,>(path: string, body: unknown = {}) => request<T>('POST', path, body)

async function zip(path: string, body: unknown): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(path, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  } catch { throw new ApiError(0, 'The harness is not reachable.') }
  if (!response.ok) {
    if (response.status === 401) authListeners.forEach((listener) => listener())
    const data = await response.json().catch(() => ({}))
    throw new ApiError(response.status, data.error || 'Could not download the archive.')
  }
  return response.blob()
}

export type StartResult = { task: TaskSummary; id: string | null }

export const api = {
  status: () => get<Status>('/api/status'),
  account: async (): Promise<Account | null> => {
    try {
      const account = await get<Account>('/api/account')
      return typeof account.email === 'string' && typeof account.logout_url === 'string' ? account : null
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null
      throw error
    }
  },
  login: (token: string) => post<{ ok: boolean }>('/api/login', { token }),
  task: () => get<TaskSnapshot>('/api/task'),
  pause: (task: string, investigationId?: string, chatId?: string) => post<{ task: TaskSummary }>('/api/task/pause', { task, investigation_id: investigationId, chat_id: chatId }),
  concurrency: (limits: { chats?: number; requests?: number }) => post<{ concurrency: Concurrency }>('/api/concurrency', limits),
  decide: (id: string, approve: boolean) => post<{ ok: boolean }>(`/api/approvals/${id}`, { approve }),
  files: () => get<{ files: WorkspaceFile[]; truncated?: boolean }>('/api/files'),
  previewImages: (files: string[]) => post<{ images: ImageRef[] }>('/api/images/preview', { files }),
  imageUrl: (sha256: string) => `/api/images/${encodeURIComponent(sha256)}`,

  proofs: () => get<{ jobs: ProofListItem[] }>('/api/proofs'),
  proof: (id: string) => get<ProofDetail>(`/api/proofs/${id}`),
  proofReport: (id: string) => get<{ report: string; ledger: string }>(`/api/proofs/${id}/report`),
  proofSources: (id: string) => get<{ sources: { path: string; sha256?: string; content: string }[] }>(`/api/proofs/${id}/sources`),
  proofArtifact: (id: string, name: string) => get<Artifact>(`/api/proofs/${id}/artifacts/${name}`),
  proofStream: (id: string, name: string, from = 0) => get<StreamChunk>(`/api/proofs/${id}/stream/${name}?from=${from}`),
  startProof: (body: Record<string, unknown>) => post<StartResult>('/api/proofs', body),
  resumeProof: (id: string, queue = false, additions: AllowanceAdditions = {}) => post<StartResult>(`/api/proofs/${id}/resume`, { queue, ...additions }),
  deleteProof: (id: string) => request<{ ok: boolean }>('DELETE', `/api/proofs/${id}`),

  researches: () => get<{ jobs: ResearchListItem[] }>('/api/research'),
  research: (id: string) => get<ResearchDetail>(`/api/research/${id}`),
  reviewEvidence: (id: string) => get<{ items: ReviewEvidence[] }>(`/api/research/${id}/review-evidence`),
  researchSources: (id: string) => get<{ sources: { id: string; path: string; sha256: string; content: string }[] }>(`/api/research/${id}/sources`),
  researchArtifact: (id: string, name: string) => get<Artifact>(`/api/research/${id}/artifacts/${name}`),
  researchStream: (id: string, name: string, from = 0) => get<StreamChunk>(`/api/research/${id}/stream/${name}?from=${from}`),
  startResearch: (body: Record<string, unknown>) => post<StartResult>('/api/research', body),
  resumeResearch: (id: string, queue = false, additionalSeconds = 0, additions: AllowanceAdditions = {}) => post<StartResult>(`/api/research/${id}/resume`, { queue, ...(additionalSeconds > 0 ? { additional_seconds: additionalSeconds } : {}), ...additions }),

  folders: (path = '') => get<FolderListing>('/api/folders?path=' + encodeURIComponent(path)),
  createFolder: (path: string, name: string) => post<{ path: string }>('/api/folders', { path, name }),
  openFolder: (path: string) => post<{ workspace: string }>('/api/workspace', { path }),
  saveSettings: (read: Partial<ReadSettings>) => post<{ read: ReadSettings }>('/api/settings', { read }),
  personalization: () => get<Personalization>('/api/personalization'),
  savePersonalization: (value: Personalization) => post<Personalization>('/api/personalization', value),
  upload: async (file: File, project_id?: string | null) => {
    let response: Response
    try {
      response = await fetch('/api/files/upload?name=' + encodeURIComponent(file.name) + (project_id ? '&project_id=' + encodeURIComponent(project_id) : ''), {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
      })
    } catch {
      throw new ApiError(0, 'The harness is not reachable.')
    }
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401) authListeners.forEach((listener) => listener())
      throw new ApiError(response.status, data.error || response.statusText)
    }
    return data as { path: string; size?: number; image?: ImageRef; note?: string }
  },
  templates: () => get<{ templates: Template[] }>('/api/templates'),
  startWriteup: (body: Record<string, unknown>) => post<StartResult>('/api/writeup', body),
  styles: () => get<{ styles: StyleProfile[]; choice: StyleChoice }>('/api/styles'),
  setStyleChoice: (choice: StyleChoice) => post<{ choice: StyleChoice }>('/api/styles/choice', choice),
  startStyle: (body: Record<string, unknown>) => post<StartResult>('/api/style', body),
  setStyleTemplate: (name: string, files: string[]) => post<{ style: StyleProfile }>('/api/styles/template', { name, template_files: files }),
  renameStyle: (name: string, rename: string) => post<{ style: StyleProfile }>(`/api/styles/${encodeURIComponent(name)}`, { rename }),
  editStyle: (name: string, items: StyleEdit) => post<{ style: StyleProfile }>(`/api/styles/${encodeURIComponent(name)}`, { items }),
  deleteStyle: (name: string) => request<{ ok: true }>('DELETE', `/api/styles/${encodeURIComponent(name)}`),
  researchPdfUrl: (id: string) => `/api/research/${id}/pdf`,
  researchPdfDownloadUrl: (id: string) => `/api/research/${id}/pdf?download=1`,
  researchTexUrl: (id: string) => `/api/research/${id}/tex`,
  researchSourceUrl: (id: string) => `/api/research/${id}/source`,
  researchBodyUrl: (id: string) => `/api/research/${id}/body`,
  writeupNeeds: (id: string, action: 'waive' | 'stop') => post<{ task: TaskSummary | null; id: string }>(`/api/research/${id}/needs`, { action }),
  researchReviewDownloadUrl: (id: string) => `/api/research/${id}/review?download=1`,
  researchBibDownloadUrl: (id: string) => `/api/research/${id}/bib?download=1`,
  // Clean standalone files for use outside the app: Markdown with a numbered reference list, BibTeX of the cited works.
  reviewMarkdownUrl: (id: string) => `/api/research/${id}/download.md`,
  reviewBibtexUrl: (id: string) => `/api/research/${id}/download.bib`,
  cancelQueued: (task: string) => post<{ task: TaskSummary }>(`/api/queue/${task}/cancel`),
  route: (id: string, content: string, files: string[], mode_call?: { mode: RouteMode; start: number; effort?: EffortLevel }, queue = false) =>
    post<{ ok: boolean; task?: TaskSummary }>(`/api/chats/${id}/route`, { content, files, mode_call, ...(queue ? { queue: true } : {}) }),
  index: (scope: { chat?: string; project?: string }) => indexPage('/api/index?' + new URLSearchParams(scope).toString()),
  chatExperiment: (chat: string, job: string) => get<{ answer: string; status: string; run_status?: string; error?: unknown; stop_reason?: string; limits?: unknown; stdout_tail?: string; seconds?: number }>(`/api/chats/${chat}/experiments/${job}`),
  chatCheck: (chat: string, job: string) => get<{ answer: string; status: string }>(`/api/chats/${chat}/checks/${job}`),
  savedChatAnswer: (chat: string, answer: string) => get<{ answer: string; status: string }>(`/api/chats/${chat}/answers/${answer}`),
  stopInvestigation: (id: string, expectedId?: string) => post<{ status: string; answer: string }>(
    `/api/chats/${id}/investigation/stop`, expectedId ? { investigation_id: expectedId } : {}),
  resumeAssistant: (id: string) => post<{ task: TaskSummary }>(`/api/chats/${id}/assistant/resume`),
  startRoute: (id: string, index: number, body: { mode: string; request: string; files: string[]; limits?: Record<string, number | null> }) =>
    post<{ task: TaskSummary }>(`/api/chats/${id}/routes/${index}/start`, body),
  cancelRoute: (id: string, index: number) => post<{ task: TaskSummary }>(`/api/chats/${id}/routes/${index}/cancel`),
  dismissRoute: (id: string, index: number) => post<{ item: TranscriptItem }>(`/api/chats/${id}/routes/${index}/dismiss`),
  chats: (query = '') => get<{ chats: ChatListItem[]; truncated?: boolean }>('/api/chats' + (query ? '?q=' + encodeURIComponent(query) : '')),
  projects: () => get<{ projects: Project[] }>('/api/projects'),
  project: (id: string) => get<{ project: Project }>(`/api/projects/${id}`),
  createProject: (name: string, notes = '') => post<{ project: Project }>('/api/projects', { name, notes }),
  saveProject: (id: string, value: { name?: string; notes?: string; revision: string }) => post<{ project: Project }>(`/api/projects/${id}`, value),
  projectFiles: (id: string) => get<{ files: ProjectFile[]; truncated?: boolean }>(`/api/projects/${id}/files`),
  deleteProject: (id: string) => request<{ ok: boolean; moved: number }>('DELETE', `/api/projects/${id}`),
  chat: (id: string) => get<Chat>(`/api/chats/${id}`),
  chatTitle: (id: string, title: string) => post<Chat>(`/api/chats/${id}/title`, { title }),
  chatProject: (id: string, project_id: string | null) => post<Chat>(`/api/chats/${id}/project`, { project_id }),
  deleteChat: (id: string) => request<{ deleted: string }>('DELETE', `/api/chats/${id}`),
  createChat: (mode: string, think?: boolean, online?: boolean, project_id: string | null = null) => post<Chat>('/api/chats', { mode, think, online, project_id }),
  chatSettings: (id: string, settings: { think?: boolean; online?: boolean }) => post<Chat>(`/api/chats/${id}/settings`, settings),
  send: (id: string, content: string, files: string[] = []) => post<{ task: TaskSummary }>(`/api/chats/${id}/messages`, { content, files, queue: true }),
  review: (id: string) => post<{ task: TaskSummary }>(`/api/chats/${id}/review`, { queue: true }),
  feedbackConfig: () => get<FeedbackConfig>('/api/feedback/config'),
  previewFeedbackTrace: (chat_id: string) => post<FeedbackPreview>('/api/feedback/preview', { chat_id }),
  exportFeedbackTrace: (chat_id: string) => zip('/api/feedback/export', { chat_id }),
  sendFeedback: (value: { category: FeedbackCategory; description: string; chat_id: string | null; include_trace: boolean; images: FeedbackImage[] }) =>
    post<FeedbackResult>('/api/feedback', { ...value, chat_id: value.include_trace ? value.chat_id : null }),
  feedbackArchiveUrl: (id: string) => `/api/feedback/${encodeURIComponent(id)}/archive`,
}
