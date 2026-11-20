// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Saved chats carry an immutable, controller-written report. Read its structural markers
// while preserving the recorded mathematical qualifications.
import type { RouteMode } from './api'

const digestLine = /^SHA-256 [0-9a-f]{64}$/m
const uuid = '[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}'
const recordId = new RegExp('^([A-Z])-(' + uuid + '|exp-\\d{8}-\\d{6}-[a-z0-9]+)(?:\\.\\d+)? \\| (.+?) \\| Task: (.*)$')
const jobEnd = new RegExp(' · job (' + uuid + ')$')
const reportTask = /^- \*\*(t[1-8]) · (explore|check|literature|prove|quick_review) · ([a-z_]+)\*\* — (.*)$/
export type Finding = { kind: string; job: string; label: string; qualifiers: string[]; summary: string }
export type ReportStep = { id: string; mode: RouteMode; state: string; objective: string; claim: string; job: string; evidence: Finding[]; notes: string[]; excerpt: string }

function finding(line: string): Finding | null {
  const parts = recordId.exec(line)
  if (!parts) return null
  const [, kind, job, qualification, content] = parts
  const [label, ...qualifiers] = qualification.split(' · ')
  const separator = content.lastIndexOf(' | ')
  return { kind, job, label, qualifiers, summary: separator < 0 ? '' : content.slice(separator + 3) }
}

// Only the logged conclusion can attribute a proof or refutation to the approved statement.
export function readInvestigationReport(content: string) {
  const head = '## Investigation report'
  if (!content.startsWith(head + '\n')) return null
  const sections = new Map<string, string>()
  const names = /\n### (Research answer|Original request|Confirmed statement|Proposed statement|Related statements|Model-declared variants|Work and evidence|Status of the (?:confirmed|proposed) target)\n/g
  let start = head.length, section = 'reason'
  for (const m of content.matchAll(names)) {
    sections.set(section, content.slice(start, m.index).trim())
    section = m[1]; start = m.index + m[0].length
  }
  sections.set(section, content.slice(start).trim())
  const work: ReportStep[] = []
  for (const line of (sections.get('Work and evidence') || '').split('\n')) {
    const match = reportTask.exec(line)
    if (match) {
      const state = match[3], mode = match[2] as RouteMode
      let objective = match[4]
      const native = / · (?:Worker status: [\w -]+ \(supporting finding unavailable\)|native status [\w-]+ \(index evidence unavailable\))$/.exec(objective)
      if (native) objective = objective.slice(0, native.index)
      const job = jobEnd.exec(objective)
      if (job) objective = objective.slice(0, job.index)
      const claim = / · claim (target|variant|v\d+)$/.exec(objective)
      if (claim) objective = objective.slice(0, claim.index)
      work.push({ id: match[1], mode, state, objective, job: job?.[1] || '', claim: claim?.[1] || '', evidence: [],
        notes: native ? ['The supporting record is unavailable.'] : [], excerpt: '' })
    } else if (line.trim()) {
      const last = work.at(-1)
      if (!last) return null
      const text = line.trim()
      const entry = finding(text)
      // A report saved before objectives were written on one line: the job and claim markers end a
      // later line, and the lines in between belong to the objective.
      const tail = !last.job && !last.evidence.length && !last.excerpt && !entry && jobEnd.exec(text)
      if (tail) {
        let rest = text.slice(0, tail.index)
        const claim = / · claim (target|variant|v\d+)$/.exec(rest)
        if (claim) { rest = rest.slice(0, claim.index); last.claim = claim[1] }
        last.objective = [last.objective, ...last.notes, rest].join(' ')
        last.notes = []; last.job = tail[1]
      }
      else if (entry) last.evidence.push(entry)
      else if (text.startsWith('Model-written lead (excerpt): ')) last.excerpt = text.slice('Model-written lead (excerpt): '.length)
      else last.notes.push(text)
    }
  }
  const statement = (sections.get('Proposed statement') || sections.get('Confirmed statement') || '').replace(digestLine, '').trim()
  const verdict = sections.get('Status of the proposed target') || sections.get('Status of the confirmed target') || ''
  const reviewed = verdict.split('\n').filter(line => line.startsWith('• ')).map(line => finding(line.slice(2))).filter(entry => entry !== null)
  const variants = (sections.get('Related statements') || sections.get('Model-declared variants') || '').replace(/^\*\*v(\d+)\*\* \((.*?)\) · [0-9a-f]{64}$/gm, '**Related statement $1** ($2)\n')
  return { statement, synthesis: sections.get('Research answer') || '', reason: sections.get('reason') || '', work, reviewed, variants,
    missingReview: /^No (?:indexed )?model-reviewed proof/.test(verdict), original: sections.get('Original request') || '' }
}
