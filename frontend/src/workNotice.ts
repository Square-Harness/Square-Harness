// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { taskReference } from './outputs'
import { PHASES } from './workProgress'

const TOOLS: Record<string, string> = {
  read_file: 'Reading the source', search_text: 'Finding a passage', list_files: 'Finding source files',
  list_index: 'Looking through earlier work', read_index: 'Reading earlier findings',
  check_reference: 'Checking a reference', read_paper: 'Reading the paper',
  run_python: 'Running a calculation', sanity_check: 'Testing examples', run_experiment: 'Testing examples',
  replace_text: 'Revising the document', write_file: 'Writing the document',
}

// Only for operational messages from the harness. Never filter a mathematical result or source passage.
export function workNotice(text: string): string {
  if (/^Proof [0-9a-f-]{36}:/i.test(text)) return 'Started work on the proof'
  if (/^Research [0-9a-f-]{36}:/i.test(text)) return 'Started the research task'
  if (/^Research phase: /.test(text)) return PHASES[text.slice(16)] || 'Continuing the research'
  if (/^Resuming with calibrated Proof policy/.test(text)) return 'Resuming the saved proof'
  if (/^The verifier found no issue in the response/.test(text)) return 'The reviewer found no issue. The answer may establish the claim or give a counterexample.'
  if (/^Worker finished\.?$/.test(text)) return ''
  if (/^The worker returned an empty answer after a tool call/.test(text)) return 'Requesting a conclusion'
  if (/^Skipped \d+ repeated tool call/.test(text)) return 'Continuing with the evidence already collected'
  if (/^Tool-round limit reached|^Output limit reached while reasoning/.test(text)) return 'Preparing the answer with the work completed so far'
  if (/^Compiling.*latexmk/.test(text)) return 'Typesetting the document'
  if (/^Wrote (?:\/|[a-z]:\\|.*\.mathagent\/)/i.test(text)) return 'Saved the document'
  const mapped = text.match(/^Mapped (.+): (\d+ statements?.*)$/)
  if (mapped) return 'Mapped the manuscript · ' + mapped[2]
  return text.trim()
    .replace(/^Model decision:\s*/, '')
    .replace(/(?:[\w./-]*\/)?\.mathagent\/[^\s,;]+/gi, 'the saved work')
    .replace(/(?:^|\s)(?:\/(?:Users|home|private|tmp|var|workspace)\/|[a-z]:\\)[^\n]*?([^/\\\s]+\.[a-z0-9]{1,7})(?=[\s,:;)]|$)/gi, ' $1')
    .replace(/\b(?:[PCLREAU]-)?exp-\d{8}-\d{6}-[a-f0-9]+\b/gi, 'the experiment')
    .replace(/\b(?:[PCLREAU]-)?[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}\b/gi, 'the saved result')
    .replace(/\b(?:child_|batch_|turn_|investigation_)[a-f0-9]{16,}\b/gi, 'the task')
    .replace(/\b(?:SHA-?256[ :=]*)?[a-f\d]{64}\b/gi, 'the recorded statement')
}

export function activityCaption(item: { kind: string; text: string }): string {
  if (item.kind === 'result') return ''
  if (item.kind !== 'tool') return workNotice(item.text)
  const tool = item.text.replace(/^Using /, '').match(/^[a-z][a-z0-9_]*/i)?.[0] || ''
  return TOOLS[tool] || (/search|lookup/i.test(tool) ? 'Searching the literature' : 'Consulting a research tool')
}

export function taskNotice(text: string, tasks: { id: string; mode: string }[]): string {
  return workNotice(text).replace(/\bt[1-8]\b/g, id => taskReference(tasks, id))
}
