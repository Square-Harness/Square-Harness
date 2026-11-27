// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Pure text preparation for typeset mathematics (no React; testable with node).

const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]+`)/g
const DISPLAY_ENV = /(^|\n)([ \t]*)(\\begin\{(equation|align|gather|multline|flalign|alignat)(\*?)\}[\s\S]*?\\end\{\4\5\})/g
const THEOREM = 'theorem|lemma|proposition|corollary|definition|remark|example|claim|conjecture|assumption|hypothesis'
// Cross-references become placeholders while equations are numbered, then resolve:
// \uE000 in prose, \uE002 inside mathematics, both closed by \uE001.
const PROSE_REF = '\uE000', MATH_REF = '\uE002', REF_END = '\uE001'

const capitalize = (word: string) => word.charAt(0).toUpperCase() + word.slice(1)
const references = (text: string, open: string) =>
  text.replace(/\\(eq)?ref\{([^{}\n]*)\}/g, (_m, eq: string | undefined, label: string) => `${open}${eq ? 'eq' : 'ref'}:${label}${REF_END}`)

/** Text-mode LaTeX that models often emit in prose, outside math. */
function prose(text: string): string {
  return references(text
    .replace(/\\textbf\{([^{}\n]*)\}/g, '**$1**')
    .replace(/\\(?:emph|textit)\{([^{}\n]*)\}/g, '*$1*')
    .replace(new RegExp(`\\\\begin\\{(${THEOREM})\\}(?:\\[([^\\]\\n]*)\\])?`, 'g'),
      (_m, env: string, title?: string) => `**${capitalize(env)}${title ? ` (${title})` : ''}.** `)
    .replace(new RegExp(`\\\\end\\{(${THEOREM})\\}`, 'g'), '')
    .replace(/\\begin\{proof\}(?:\[([^\]\n]*)\])?/g, (_m, title?: string) => `*${title || 'Proof'}.* `)
    .replace(/\\end\{proof\}/g, ' ∎')
    .replace(/\\label\{[^{}\n]*\}/g, ''), PROSE_REF)
}

// Some saved planner fields mix prose with undelimited notation such as λ_{n+1} or C_{p^2}.
// Recognize individual indexed symbols and operators only. Keep identifiers,
// destinations, literal code and existing mathematical spans as supplied.
const SCRIPT_CHARS = 'a-zA-Z0-9α-ωΑ-Ω+−=<>≤≥ ,-'
const SCRIPTED_SYMBOL = new RegExp(`(?<![\\p{L}\\p{N}_/\\\\])((?:inf|sup|lim|max|min|[a-zA-Zα-ωΑ-Ω∫∑∏])(?:[_^](?:\\{(?:[${SCRIPT_CHARS}]|[_^](?:[a-zA-Z0-9]|\\{[${SCRIPT_CHARS}]{1,20}\\})){1,35}\\}|[a-zA-Z0-9α-ωΑ-Ω])){1,2})(?![\\p{L}\\p{N}_/]|\\.[a-zA-Z])`, 'gu')
const OPERATORS: Record<string, string> = { '∫': '\\int', '∑': '\\sum', '∏': '\\prod' }
function typesetSymbols(text: string): string {
  return text.split(/(https?:\/\/\S+|\]\([^\n)]*\))/g).map((part, index) => index % 2 ? part :
    part.replace(SCRIPTED_SYMBOL, (_, token: string) => {
      const tex = token.replace(/^(inf|sup|lim|max|min)/, '\\$1').replace(/^[∫∑∏]/, (symbol) => OPERATORS[symbol] + ' ')
        .replaceAll('−', '-').replaceAll('≥', '\\ge ').replaceAll('≤', '\\le ')
      return '$' + tex + '$'
    })).join('')
}

// -- equation numbers ----------------------------------------------------------------

type Numbering = { count: number; labels: Map<string, string> }
const LABEL = /\\label\{([^{}\n]*)\}/g
const TAG = /\\tag\*?\{([^{}\n]*)\}/
// KaTeX numbers rows with a page-wide CSS counter and has no multline or flalign:
// number each message's equations here, in starred environments with explicit tags.
const STARRED: Record<string, string> = { equation: 'equation*', align: 'align*', gather: 'gather*', alignat: 'alignat*', multline: 'gather*', flalign: 'align*' }
const NUMBERED_ENV = /\\begin\{(equation|align|gather|multline|flalign|alignat)(\*?)\}(\{\d+\})?([\s\S]*?)\\end\{\1\2\}/g

/** Split an environment body at its top-level row breaks (\\), not inside braces or inner environments. */
function rows(body: string): string[] {
  const out: string[] = []
  let depth = 0, start = 0
  for (let i = 0; i < body.length; i++) {
    const c = body[i]
    if (c === '\\') {
      if (body.startsWith('\\begin{', i)) depth++
      else if (body.startsWith('\\end{', i)) depth--
      else if (body[i + 1] === '\\' && depth === 0) { out.push(body.slice(start, i)); start = i + 2 }
      i++
    } else if (c === '{') depth++
    else if (c === '}') depth--
  }
  out.push(body.slice(start))
  return out
}

/** One numbered unit (a row, or a whole equation): its own tag, the next number, or none. */
function tagUnit(unit: string, numbered: boolean, state: Numbering): string {
  const labels = [...unit.matchAll(LABEL)].map((match) => match[1])
  let text = unit.replace(LABEL, '')
  const explicit = text.match(TAG)
  const off = /\\(?:nonumber|notag)\b/.test(text)
  text = text.replace(/\\(?:nonumber|notag)\b/g, '')
  let tag: string | null = explicit ? explicit[1] : null
  if (!explicit && numbered && !off && text.trim()) {
    tag = String(++state.count)
    text = text.replace(/\s*$/, (space) => ` \\tag{${tag}}${space}`)
  }
  if (tag !== null) for (const label of labels) state.labels.set(label, tag)
  return text
}

function numberDisplay(body: string, state: Numbering): string {
  let found = false
  body = body.replace(NUMBERED_ENV, (_m, env: string, star: string, columns: string | undefined, inner: string) => {
    found = true
    const numbered = !star
    const perRow = env !== 'equation' && env !== 'multline'
    const content = perRow ? rows(inner).map((row) => tagUnit(row, numbered, state)).join('\\\\')
      : env === 'multline' ? (() => { const parts = rows(inner); const last = parts.length - 1
        return parts.map((row, index) => tagUnit(row, numbered && index === last, state)).join('\\\\') })()
      : tagUnit(inner, numbered, state)
    return `\\begin{${STARRED[env]}}${columns || ''}${content}\\end{${STARRED[env]}}`
  })
  // A labelled display outside any numbered environment is numbered so that \eqref can point to it.
  return !found && /\\label\{/.test(body) ? tagUnit(body, true, state) : body.replace(LABEL, '')
}

/** Inline mathematics cannot carry \tag or \label: move a tag after the formula as text. */
function inlineMath(body: string, display: boolean): string {
  const tag = body.match(TAG)
  const tex = body.replace(LABEL, '').replace(new RegExp(TAG.source, 'g'), '').trim()
  return `$${display ? '\\displaystyle ' : ''}${references(tex, MATH_REF)}$${tag ? ` (${tag[1]})` : ''}`
}

// -- dollar delimiters ------------------------------------------------------------------

type Span = { index: number; end: number; body: string; display: boolean }

/** Mathematics delimited by $…$ or $$…$$. A single $ is only a delimiter when it pairs on the
    same line and does not read as an amount ("$5 and $10"): the closing $ is not followed by a
    digit, and a body that ends with a space also starts with one ("$ x $"). */
function mathSpans(text: string): Span[] {
  const spans: Span[] = []
  let i = 0
  while (i < text.length) {
    const at = text.indexOf('$', i)
    if (at < 0) break
    if (at > 0 && text[at - 1] === '\\') { i = at + 1; continue }
    if (text[at + 1] === '$') {
      const end = text.indexOf('$$', at + 2)
      if (end > at + 2 && text.slice(at + 2, end).trim()) {
        spans.push({ index: at, end: end + 2, body: text.slice(at + 2, end), display: true })
        i = end + 2
      } else i = at + 2
      continue
    }
    const close = text.indexOf('$', at + 1)
    const body = close < 0 ? '' : text.slice(at + 1, close)
    const amount = close >= 0 && (/\d/.test(text[close + 1] || '') || (/\s$/.test(body) && !/^\s/.test(body)))
    if (close < 0 || !body.trim() || body.includes('\n') || amount || text[close - 1] === '\\') { i = at + 1; continue }
    spans.push({ index: at, end: close + 1, body, display: false })
    i = close + 1
  }
  return spans
}

/** A $ that delimits nothing is a literal dollar sign; escape it so Markdown math leaves it alone. */
const literalDollars = (text: string) => text.replace(/(?<!\\)\$/g, '\\$')

function normalizeText(text: string, mathProse: boolean, state: Numbering): string {
  const prepareProse = (part: string) => prose(mathProse ? typesetSymbols(literalDollars(part)) : literalDollars(part))
  text = text
    .replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => `$$${body}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$${body.trim()}$`)
  // A bare display environment on its own lines becomes display math.
  text = text.replace(DISPLAY_ENV, (match, start: string, indent: string, env: string, _n: string, _s: string, offset: number, whole: string) => {
    const before = whole.slice(0, offset + start.length).trimEnd()
    return before.endsWith('$$') ? match : `${start}${indent}$$\n${env}\n${indent}$$`
  })
  let out = ''
  let last = 0
  for (const span of mathSpans(text)) {
    out += prepareProse(text.slice(last, span.index))
    if (span.display) {
      const lineStart = text.lastIndexOf('\n', span.index - 1) + 1
      const lineEnd = text.indexOf('\n', span.end)
      const before = text.slice(lineStart, span.index)
      const after = text.slice(span.end, lineEnd === -1 ? text.length : lineEnd)
      const body = span.body.trim()
      if (!before.trim() && !after.trim()) {
        // Alone on its line(s): a display block, keeping list indentation.
        const numbered = references(numberDisplay(body, state), MATH_REF)
        out += `$$\n${before}${numbered.split('\n').map((line) => line.trim()).join('\n' + before)}\n${before}$$`
      } else {
        // Inside a sentence: keep the paragraph intact, typeset in display style.
        out += inlineMath(body.replace(/\s*\n\s*/g, ' '), true)
      }
    } else {
      out += inlineMath(span.body, false)
    }
    last = span.end
  }
  return out + prepareProse(text.slice(last))
}

function resolveReferences(text: string, labels: Map<string, string>): string {
  return text.replace(/([\uE000\uE002])(eq|ref):([^\uE001]*)\uE001/g, (_m, open: string, kind: string, label: string) => {
    const number = labels.get(label)
    const shown = number === undefined ? (kind === 'eq' ? `(${label})` : label) : kind === 'eq' ? `(${number})` : number
    return open === MATH_REF ? `\\text{${shown.replace(/[{}\\]/g, '')}}` : shown
  })
}

export function normalizeMath(source: string, mathProse = false): string {
  const state: Numbering = { count: 0, labels: new Map() }
  const parts = source.split(CODE).map((part, index) => (index % 2 ? part : normalizeText(part, mathProse, state)))
  return parts.map((part, index) => (index % 2 ? part : resolveReferences(part, state.labels))).join('')
}

// -- citations such as [M1:L3-L6] or [doc-0123abcd:L12-L28] -----------------------

const CITE = /\[((?:doc-[A-Za-z0-9_-]+|S[\w-]+|[MPT]\d+)(?::L\d+(?:-L?\d+)?)?)\]/g
type MdNode = { type: string; value?: string; children?: MdNode[]; data?: Record<string, unknown> }

function splitCitations(node: MdNode): MdNode[] {
  const value = node.value || ''
  const out: MdNode[] = []
  let last = 0
  for (const match of value.matchAll(CITE)) {
    const at = match.index ?? 0
    if (at > last) out.push({ type: 'text', value: value.slice(last, at) })
    out.push({ type: 'citation', data: { hName: 'cite', hProperties: { dataCite: match[1] } },
      children: [{ type: 'text', value: match[1] }] })
    last = at + match[0].length
  }
  if (!out.length) return [node]
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
  return out
}

function walk(node: MdNode, split: (node: MdNode) => MdNode[], includeInlineCode = false) {
  if (!node.children) return
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' || (includeInlineCode && child.type === 'inlineCode')) return split(child)
    if (!['code', 'inlineCode', 'math', 'inlineMath', 'link', 'linkReference', 'citation'].includes(child.type)) walk(child, split, includeInlineCode)
    return [child]
  })
}

export const remarkCitations = () => (tree: MdNode) => walk(tree, splitCitations)

// Saved answers can cite a paper in prose, in brackets, or as a single inline code span.
// The id is the literature store's document id; its line numbers belong to that specific text.
const DOCUMENT_LINES = /(?<![\w/-])(doc-[0-9a-f]{24}):L([1-9]\d*)(?:[-–]L?([1-9]\d*))?(?![\w/–-])/g

export const remarkDocumentLines = () => (tree: MdNode) => walk(tree, node => {
  const value = node.value || ''
  const children: MdNode[] = []
  let last = 0
  for (const match of value.matchAll(DOCUMENT_LINES)) {
    const start = Number(match[2]), end = Number(match[3] ?? match[2])
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || end < start) continue
    let from = match.index!, to = from + match[0].length
    if (value[from - 1] === '[' && value[to] === ']') { from--; to++ }
    if (node.type === 'inlineCode' && (from !== 0 || to !== value.length)) return [node]
    if (from > last) children.push({ type: 'text', value: value.slice(last, from) })
    children.push({ type: 'citation', data: { hName: 'cite', hProperties: {
      dataDocument: match[1], dataStart: start, dataEnd: end, dataCite: match[0],
    } }, children: [{ type: 'text', value: match[0] }] })
    last = to
  }
  if (!children.length) return [node]
  if (last < value.length) children.push({ type: 'text', value: value.slice(last) })
  return children
}, true)

export type PaperLinks = Map<number, { url: string; title: string }>
/** Only link numbers that the saved review assigns to an actual paper. */
export const remarkPaperLinks = (papers: PaperLinks) => (tree: MdNode) => walk(tree, node => {
  const text = node.value || ''
  const children: MdNode[] = []
  let start = 0
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    const paper = papers.get(Number(match[1]))
    if (!paper) continue
    children.push({ type: 'text', value: text.slice(start, match.index) },
      { type: 'link', ...paper, children: [{ type: 'text', value: match[0] }] })
    start = match.index + match[0].length
  }
  return start ? [...children, { type: 'text', value: text.slice(start) }] : [node]
})

/** Split streaming text into stable blocks so only the growing tail re-renders. */
/** A user's message as typed: underscores outside mathematics and code are letters (a_n, b_{n+1}), never Markdown emphasis. */
export function literalUnderscores(text: string): string {
  return text.split(/(```[\s\S]*?```|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\])/)
    .map((part, index) => (index % 2 ? part : part.replace(/(?<!\\)_/g, '\\_'))).join('')
}

export function splitBlocks(text: string): string[] {
  const blocks: string[] = []
  let current: string[] = []
  let fence = false
  let math = false
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (/^(```|~~~)/.test(trimmed)) fence = !fence
    else if (!fence && trimmed === '$$') math = !math
    if (!trimmed && !fence && !math && current.length) {
      blocks.push(current.join('\n'))
      current = []
    } else {
      current.push(line)
    }
  }
  if (current.length) blocks.push(current.join('\n'))
  return blocks
}

/** Shorten text for a one-line snippet without cutting inside inline math. */
export function snippet(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= limit) return flat
  let cut = flat.lastIndexOf(' ', limit)
  if (cut < limit * 0.6) cut = limit
  let head = flat.slice(0, cut)
  if ((head.match(/\$\$/g) || []).length % 2 === 1) head = head.slice(0, head.lastIndexOf('$$'))
  if ((head.replace(/\$\$/g, '').match(/(?<!\\)\$/g) || []).length % 2 === 1) head = head.slice(0, head.lastIndexOf('$'))
  return head.trimEnd() + '…'
}
