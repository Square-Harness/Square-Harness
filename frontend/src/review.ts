// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { Budget, ReviewVariant, RouteMode } from './api'
import type { EffortLevel } from './effort'
import type { Variant } from './components/Square'

/** The kinds of review behind the Review notebook; the engine kind stays 'referee'. */
// 'journal' is the former detailed review: saved jobs stay readable and resume as a review.
export const VARIANTS: ReviewVariant[] = ['quick', 'review', 'typos', 'explain']

export const VARIANT_NAMES: Record<ReviewVariant, string> = {
  quick: 'Quick Check', review: 'Review', journal: 'Review', typos: 'Typo Check', explain: 'Explanation',
}

/** A quick check ends with a verdict on the proof, shown with the notebook's squares. */
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
/** Steps of reviews saved before the review and the detailed review became one. */
export const LEGACY_REVIEW_STEPS: Record<string, string[]> = {
  'review-v1': ['plan', 'read', 'scope', 'literature', 'write', 'done'],
  'journal-v1': VARIANT_STEPS.journal,
}

export const VARIANT_STEP_NAMES: Record<string, string> = {
  plan: 'Map', extract: 'Find', verify: 'Check', confirm: 'Re-check', read: 'Read', scope: 'Overview', check: 'Proofs',
  audit: 'Read and check',
  literature: 'Literature', presentation: 'Presentation', write: 'Write', explain: 'Explain', fill: 'Fill', fix: 'Fix', done: 'Done',
}

/** The effort each kind starts from. A review and a typo check have one depth: no effort is chosen for them, and
 *  the level only names their budget (mathagent/router.py FIXED_EFFORT). */
export const VARIANT_EFFORT: Record<ReviewVariant, EffortLevel> = { quick: 'medium', review: 'high', journal: 'high', typos: 'low', explain: 'medium' }
export const FIXED_VARIANTS: ReviewVariant[] = ['review', 'journal', 'typos']
export const FIXED_EFFORT: Partial<Record<RouteMode, EffortLevel>> = { referee: 'high', detailed_review: 'high', typo_check: 'low' }

/** A review checks every proof: twice the working time on a local model, four times the input on DS4 (gui/effort.py). */
export const VARIANT_SCALE: Record<ReviewVariant, number> = { quick: 1, review: 2, journal: 2, typos: 2, explain: 1 }
export const VARIANT_INPUT_SCALE: Record<ReviewVariant, number> = { quick: 1, review: 4, journal: 4, typos: 4, explain: 1 }

export const VARIANT_CAVEATS: Record<ReviewVariant, string> = {
  quick: 'Model check, not a certificate. Each objection links to the proof lines it concerns; the proof was not rewritten.',
  review: 'Model draft of a referee report. Every proof is checked and every objection gets a second opinion; the draft is rewritten as checks finish. Typos are not checked here: that is the typo check. Links open the manuscript lines.',
  typos: 'Model proofreading, not a referee report: typos and presentation only, each correction checked against the paper. No proof was checked. Links open the manuscript lines.',
  journal: 'Model draft of a referee report (an earlier detailed review). The comments on proofs come from checked findings; links open the manuscript lines they cite.',
  explain: 'Model explanation. Links open the source lines each step explains; an accuracy pass checks it against the source when the effort allows.',
}

/** Resuming near a time cap requires a visibly labelled, explicit extension. */
export function resumeAdditionalSeconds(budget: Budget) {
  return budget.limit !== null && budget.limit > 0 && budget.used >= 0.8 * budget.limit ? budget.limit : 0
}
