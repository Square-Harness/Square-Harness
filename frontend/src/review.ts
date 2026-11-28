// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { Budget, ReviewVariant, RouteMode } from './api'
import type { EffortLevel } from './effort'
import type { Cover } from './components/Glyph'
import type { Variant } from './components/Square'

/** The kinds of review behind the Review notebook; the engine kind stays 'referee'. */
// 'journal' is the former detailed review: saved jobs stay readable and resume as a review.
export const VARIANTS: ReviewVariant[] = ['quick', 'review', 'typos', 'explain']

export const VARIANT_NAMES: Record<ReviewVariant, string> = {
  quick: 'Proof Check', review: 'Review', journal: 'Review', typos: 'Typo', explain: 'Explanation',
}

/** The mark of each kind (components/Glyph.tsx): a ticked square, the pilcrow, a letter underlined, a bulb. */
export const VARIANT_COVERS: Record<ReviewVariant, Cover> = {
  quick: 'proof_check', review: 'referee', journal: 'referee', typos: 'typo', explain: 'explain',
}

/** A proof check ends with a verdict on the proof, shown with the notebook's squares. */
export const VERDICTS: Record<string, { variant: Variant; label: string }> = {
  no_issue_found: { variant: 'reviewed', label: 'No issue found' },
  issues_found: { variant: 'refuted', label: 'Issues found' },
  issues_alleged: { variant: 'gap', label: 'Issues alleged, not re-checked' },
  uncertain: { variant: 'uncertain', label: 'Uncertain' },
  unavailable: { variant: 'ready', label: 'No usable check' },
}

/** Workflow steps of each kind, as the engine names its phases. */
export const VARIANT_STEPS: Record<ReviewVariant, string[]> = {
  quick: ['plan', 'extract', 'verify', 'confirm', 'done'],
  review: ['plan', 'audit', 'write', 'done'],
  journal: ['plan', 'read', 'scope', 'check', 'confirm', 'literature', 'write', 'done'],
  typos: ['plan', 'audit', 'done'],
  explain: ['plan', 'extract', 'explain', 'fill', 'check', 'fix', 'done'],
}
export const GRAPH_REVIEW_PIPELINE = 'review-graph-v1'
export const AUTHOR_REVIEW_PIPELINE = 'review-explore-v2'
const LEGACY_EXPLORE_REVIEW = 'review-explore-v1'

export function isManuscriptReview(pipeline: string | undefined | null) {
  return pipeline === GRAPH_REVIEW_PIPELINE || pipeline === AUTHOR_REVIEW_PIPELINE || pipeline === LEGACY_EXPLORE_REVIEW
}

/** Saved pipelines keep their own phase sequence. */
export const PIPELINE_STEPS: Record<string, string[]> = {
  [GRAPH_REVIEW_PIPELINE]: ['plan', 'review', 'done'],
  [AUTHOR_REVIEW_PIPELINE]: ['plan', 'review', 'done'],
  [LEGACY_EXPLORE_REVIEW]: ['plan', 'review', 'done'],
  'review-v4': ['plan', 'referees', 'write', 'done'],
  'review-v1': ['plan', 'read', 'scope', 'literature', 'write', 'done'],
  'journal-v1': VARIANT_STEPS.journal,
}

export const VARIANT_STEP_NAMES: Record<string, string> = {
  plan: 'Map', extract: 'Find', verify: 'Check', confirm: 'Re-check', read: 'Read', scope: 'Overview', check: 'Proofs',
  review: 'Review', referees: 'Referees and judge', audit: 'Read and check',
  literature: 'Literature', presentation: 'Presentation', write: 'Write', explain: 'Explain', fill: 'Fill', fix: 'Fix', done: 'Done',
}

export const FIXED_VARIANTS: ReviewVariant[] = ['review', 'journal', 'typos']
export const FIXED_EFFORT: Partial<Record<RouteMode, EffortLevel>> = { referee: 'high', detailed_review: 'high', typo_check: 'high' }

export const VARIANT_CAVEATS: Record<ReviewVariant, string> = {
  quick: 'Model check, not a certificate. Each objection links to the proof lines it concerns; the proof was not rewritten.',
  review: 'Model draft of a referee report. The report identifies the results examined, the objections raised and the checks still open. Links open the manuscript lines.',
  typos: 'Model proofreading, not a referee report: typos and presentation only, each correction checked against the paper. No proof was checked. Links open the manuscript lines.',
  journal: 'Model draft of a referee report (an earlier detailed review). The comments on proofs come from checked findings; links open the manuscript lines they cite.',
  explain: 'Model explanation. Links open the source lines each step explains; an accuracy pass checks it against the source when the effort allows.',
}

/** Resuming near a time cap requires a visibly labelled, explicit extension. */
export function resumeAdditionalSeconds(budget: Budget) {
  return budget.limit !== null && budget.limit > 0 && budget.used >= 0.8 * budget.limit ? budget.limit : 0
}
