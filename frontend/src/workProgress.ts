// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { REVIEW_PIPELINE, type WorkProgress } from './api.ts'
import { count } from './format.ts'

export const PHASES: Record<string, string> = {
  verify: 'Verifying', fix: 'Correcting', literature: 'Checking references', sweep: 'Searching scholarly records',
  graph: 'Following citations', screen: 'Screening records', organise: 'Organising the reading list', annotate: 'Annotating entries',
  recall: 'Recalling candidate references', verdict: 'Assessing evidence', discover: 'Discovering references',
  standard_discover: 'Discovering references', standard_select: 'Selecting references', reference_select: 'Selecting references',
  plan: 'Planning', read: 'Reading sources', reading: 'Reading sources', investigate: 'Investigating',
  scope: 'Preparing the overview', extract: 'Finding the result and proof', pick: 'Finding the result',
  check: 'Checking the argument', confirm: 'Rechecking an objection', explain: 'Writing the explanation',
  audit: 'Reading and checking the paper',
  fill: 'Completing the explanation', 'explain-check': 'Checking the explanation',
  'explain-fix': 'Correcting the explanation', write: 'Writing the report', draft: 'Writing the report',
  review: 'Reviewing the draft', revise: 'Revising the draft', section: 'Writing sections',
  assemble: 'Assembling the document', compile: 'Compiling', repair: 'Repairing',
  export: 'Saving the document', done: 'Work saved',
  search: 'Searching zbMATH, arXiv, Semantic Scholar and Crossref', expand: 'Following citations',
  rank: 'Selecting the works', outline: 'Planning the review',
}

// A literature review shares some phase names with reviews and write-ups (scope, read, check…).
const REVIEW_PHASES: Record<string, string> = {
  scope: 'Defining the scope', read: 'Reading the key papers', write: 'Writing the review',
  check: 'Checking the review', assemble: 'Assembling the bibliography',
}

const phaseLabel = (phase: string, pipeline?: string | null) =>
  (pipeline === REVIEW_PIPELINE ? REVIEW_PHASES[phase] : undefined) || PHASES[phase] || 'Working'

/** Operational coverage is not mathematical verification. */
export function workProgressText(progress: WorkProgress, status: string, running = false, pipeline?: string | null) {
  if (progress.total <= 0) {
    if (running) return `${phaseLabel(progress.phase, pipeline)}…`
    if (status === 'failed' || status === 'error') return 'Work stopped. Any available results are saved.'
    if (status === 'paused' || status === 'running') return 'Paused. Work is saved.'
    return progress.partial ? 'Partial result. Unresolved points are listed in the result.' : 'Work saved.'
  }
  const completed = Math.max(0, Math.min(progress.completed, progress.total))
  const pending = Math.max(0, progress.total - completed)
  const coverage = `${count(completed)} of ${count(progress.total)} ${progress.unit} finished`
  if (running && !completed) return phaseLabel(progress.phase, pipeline)
  if (running) return `${phaseLabel(progress.phase, pipeline)} · ${coverage}.`
  if (status === 'failed' || status === 'error') return `Work stopped. ${coverage}${pending ? `; ${count(pending)} pending` : ''}.`
  if (status === 'paused' || status === 'running') return `Paused. ${coverage}${pending ? `; ${count(pending)} pending` : ''}.`
  if (pending) return `Partial result. ${coverage}; ${count(pending)} pending.`
  if (progress.partial) return `Partial result. ${coverage}. Unresolved points are listed in the result.`
  return `${coverage}.`
}
