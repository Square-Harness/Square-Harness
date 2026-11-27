// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

/**
 * Syntax colours for the file viewer's editor, after VS Code's Light+ and Dark+ themes (with LaTeX Workshop's
 * scopes for TeX). Each language is a list of rules joined into one regular expression; the text between
 * matches stays plain. The output is HTML: every piece of the text is escaped, and the only markup is
 * `<span class="tok-…">` with a fixed class name.
 */

export type Language = 'latex' | 'bibtex' | 'python' | 'julia' | 'rust' | 'javascript' | 'c' | 'shell' | 'json' | 'toml' | 'yaml'

const EXTENSIONS: Record<string, Language> = {
  tex: 'latex', sty: 'latex', cls: 'latex', ltx: 'latex', bib: 'bibtex', py: 'python', jl: 'julia', rs: 'rust',
  js: 'javascript', mjs: 'javascript', ts: 'javascript', c: 'c', h: 'c', cpp: 'c', hpp: 'c', sh: 'shell',
  json: 'json', toml: 'toml', ini: 'toml', cfg: 'toml', yaml: 'yaml', yml: 'yaml',
}

/** The language of a file name, or null when it is shown as plain text. */
export function languageOf(name: string): Language | null {
  const match = /\.([A-Za-z0-9]+)$/.exec(name)
  return match ? EXTENSIONS[match[1].toLowerCase()] ?? null : null
}

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const span = (kind: string, text: string) => text ? `<span class="tok-${kind}">${escape(text)}</span>` : ''

type Context = { text: string; end: number; previous: string }
type Render = (match: RegExpExecArray, context: Context, previous: string) => string
type Rule = { re: string; kind?: string; render?: Render }
type Spec = { rules: Rule[]; compiled?: RegExp }

/** True when `sticky` matches right after the current token. */
const next = (context: Context, sticky: RegExp) => { sticky.lastIndex = context.end; return sticky.test(context.text) }

function run(text: string, spec: Spec): string {
  const re = spec.compiled ??= new RegExp(spec.rules.map((rule, index) => `(?<r${index}>${rule.re})`).join('|'), 'g')
  const context: Context = { text, end: 0, previous: '' }
  let out = ''
  let last = 0
  re.lastIndex = 0
  for (let match = re.exec(text); match; match = re.exec(text)) {
    if (!match[0]) { re.lastIndex++; continue }
    const found = match
    const rule = spec.rules.find((_, index) => found.groups?.[`r${index}`] !== undefined)!
    out += escape(text.slice(last, match.index))
    context.end = match.index + match[0].length
    const previous = context.previous
    context.previous = ''
    out += rule.render ? rule.render(match, context, previous) : span(rule.kind!, match[0])
    last = context.end
  }
  return out + escape(text.slice(last))
}

const set = (words: string) => new Set(words.split(/\s+/).filter(Boolean))
const CALL = /[ \t]*\(/y

/** Identifiers: keywords, declarations, constants and types by name, a call by the parenthesis after it. */
function words(re: string, language: {
  keyword: string; storage: string; constant: string; type?: string; builtin?: string
  /** The kind of the name a keyword declares (`def` → function). */
  declares?: Record<string, string>; capitalTypes?: boolean; call?: RegExp
}): Rule {
  const keyword = set(language.keyword), storage = set(language.storage), constant = set(language.constant)
  const type = set(language.type || ''), builtin = set(language.builtin || '')
  return {
    re, render: (match, context, previous) => {
      const word = match[0]
      const kind = previous && language.declares?.[previous] ? language.declares[previous]
        : keyword.has(word) ? 'keyword' : storage.has(word) ? 'storage' : constant.has(word) ? 'constant'
        : type.has(word) ? 'type' : builtin.has(word) || next(context, language.call || CALL) ? 'function'
        : language.capitalTypes && /^[A-Z]\w*[a-z]/.test(word) ? 'type' : ''
      if (language.declares?.[word]) context.previous = word
      return kind ? span(kind, word) : escape(word)
    },
  }
}

const NUMBER = String.raw`\b(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?)`
const LINE_START = String.raw`(?<=(?:^|\n)[ \t]*)`
const C_COMMENTS: Rule[] = [{ re: String.raw`//.*`, kind: 'comment' }, { re: String.raw`/\*[\s\S]*?(?:\*/|$)`, kind: 'comment' }]

// -- TeX (LaTeX Workshop's scopes) --------------------------------------------------------------------------

const MATH_ENVIRONMENTS = 'equation|align|alignat|gather|multline|flalign|eqnarray|displaymath|math|dmath|IEEEeqnarray'
const VERBATIM_ENVIRONMENTS = 'verbatim|Verbatim|lstlisting|minted|comment'
const TEX_KEYWORDS = set(String.raw`documentclass usepackage RequirePackage begin end newcommand renewcommand providecommand
  DeclareMathOperator DeclareRobustCommand newenvironment renewenvironment newtheorem theoremstyle def edef gdef xdef let
  input include includeonly bibliography bibliographystyle addbibresource printbibliography makeatletter makeatother
  if ifx ifnum ifdim else fi newif expandafter`)

/** Mathematics: the content in one colour, its commands in another. */
function math(text: string) {
  let out = ''
  for (const piece of text.split(/(\\(?:[A-Za-z@]+|[^A-Za-z@\n])|%.*)/)) {
    if (!piece) continue
    out += piece.startsWith('\\') ? span('math-command', piece) : piece.startsWith('%') ? span('comment', piece) : span('math', piece)
  }
  return out
}

const environment = (command: string, name: string) => span('keyword', command) + '{' + span('variable', name) + '}'

const LATEX: Spec = {
  rules: [
    { re: String.raw`%.*`, kind: 'comment' },
    { // \begin{verbatim} … \end{verbatim}: the content as it is
      re: String.raw`\\begin\{(?<venv>(?:${VERBATIM_ENVIRONMENTS})\*?)\}[\s\S]*?(?:\\end\{\k<venv>\}|$)`,
      render: (match) => {
        const name = match.groups!.venv, close = `\\end{${name}}`, closed = match[0].endsWith(close)
        const body = match[0].slice(`\\begin{${name}}`.length, closed ? -close.length : undefined)
        return environment('\\begin', name) + span(name === 'comment' ? 'comment' : 'string', body) + (closed ? environment('\\end', name) : '')
      },
    },
    { // \begin{equation} … \end{equation}
      re: String.raw`\\begin\{(?<menv>(?:${MATH_ENVIRONMENTS})\*?)\}[\s\S]*?(?:\\end\{\k<menv>\}|$)`,
      render: (match) => {
        const name = match.groups!.menv, close = `\\end{${name}}`, closed = match[0].endsWith(close)
        const body = match[0].slice(`\\begin{${name}}`.length, closed ? -close.length : undefined)
        return environment('\\begin', name) + math(body) + (closed ? environment('\\end', name) : '')
      },
    },
    { // $$ … $$, \[ … \], \( … \), $ … $ (inline mathematics stops at a blank line)
      re: String.raw`\$\$[\s\S]*?(?:\$\$|$)|\\\[[\s\S]*?(?:\\\]|$)|\\\([\s\S]*?(?:\\\)|$)|\$(?:\\[\s\S]|[^$\\\n]|\n(?![ \t]*\n))+?\$`,
      render: (match) => {
        const text = match[0], open = text.startsWith('$$') || text.startsWith('\\') ? 2 : 1
        const closer = open === 1 ? '$' : text.startsWith('$$') ? '$$' : text.startsWith('\\[') ? '\\]' : '\\)'
        const closed = text.length >= open + closer.length && text.endsWith(closer)
        return span('math-delimiter', text.slice(0, open)) + math(text.slice(open, closed ? -closer.length : undefined))
          + (closed ? span('math-delimiter', closer) : '')
      },
    },
    { // \section{Title}: the title stands out
      re: String.raw`\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?(?:\[[^\]\n]*\])?\{(?:[^{}\n]|\{[^{}\n]*\})*\}`,
      render: (match) => {
        const [, command, option, title] = /^(\\[A-Za-z]+\*?)(\[[^\]\n]*\])?\{([\s\S]*)\}$/.exec(match[0])!
        return span('function', command) + escape(option || '') + '{' + span('heading', title) + '}'
      },
    },
    { // labels, references, citations, packages and files: the key or name
      re: String.raw`\\(?:label|ref|eqref|cref|Cref|autoref|pageref|nameref|cite[A-Za-z]*|nocite|input|include|includegraphics|bibliography|bibliographystyle|addbibresource|usepackage|RequirePackage|documentclass|url|href)\*?(?:\[[^\]\n]*\])*\{[^{}\n]*\}`,
      render: (match) => {
        const [, command, options, key] = /^(\\[A-Za-z]+\*?)((?:\[[^\]\n]*\])*)\{([^{}\n]*)\}$/.exec(match[0])!
        return span(TEX_KEYWORDS.has(command.slice(1)) ? 'keyword' : 'function', command) + escape(options) + '{' + span('reference', key) + '}'
      },
    },
    {
      re: String.raw`\\(?:begin|end)\{[^{}\n]*\}`,
      render: (match) => { const [, command, name] = /^(\\[a-z]+)\{(.*)\}$/.exec(match[0])!; return environment(command, name) },
    },
    {
      re: String.raw`\\(?:[A-Za-z@]+\*?|[^A-Za-z@\n])`,
      render: (match) => span(TEX_KEYWORDS.has(match[0].slice(1).replace(/\*$/, '')) ? 'keyword' : 'function', match[0]),
    },
    { re: String.raw`#\d`, kind: 'variable' },
    { re: '&', kind: 'keyword' },
  ],
}

const BIBTEX: Spec = {
  rules: [
    { re: String.raw`%.*`, kind: 'comment' },
    { re: String.raw`@[A-Za-z]+(?=\s*[{(])`, kind: 'keyword' },
    { re: String.raw`(?<=@[A-Za-z]+\s*[{(]\s*)[^,\s{}()]+`, kind: 'reference' },
    { re: String.raw`[A-Za-z][\w-]*(?=\s*=)`, kind: 'variable' },
    { re: String.raw`(?<==\s*)\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|"(?:\\.|[^"\\])*"`, kind: 'string' },
    { re: String.raw`\b\d+\b`, kind: 'number' },
  ],
}

// -- programming languages ----------------------------------------------------------------------------------

const PYTHON: Spec = {
  rules: [
    { re: String.raw`#.*`, kind: 'comment' },
    { re: String.raw`(?:[rRbBuUfF]{1,2})?(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)`, kind: 'string' },
    { re: LINE_START + String.raw`@[A-Za-z_][\w.]*`, kind: 'macro' },
    { re: NUMBER + '[jJ]?', kind: 'number' },
    words(String.raw`[A-Za-z_]\w*`, {
      keyword: 'if elif else for while try except finally with return yield break continue pass raise import from as await assert match case',
      storage: 'def class lambda async global nonlocal and or not in is del',
      constant: 'True False None self cls',
      type: 'int float str bool list dict set tuple bytes bytearray object type complex frozenset',
      declares: { def: 'function', class: 'type' }, capitalTypes: true,
    }),
  ],
}

const JULIA: Spec = {
  rules: [
    { re: String.raw`#=[\s\S]*?(?:=#|$)`, kind: 'comment' },
    { re: String.raw`#.*`, kind: 'comment' },
    { re: String.raw`"""[\s\S]*?(?:"""|$)|(?:\b(?:r|raw|b))?"(?:\\[\s\S]|[^"\\])*"?|` + '`[^`]*`?', kind: 'string' },
    { re: String.raw`(?<![\w)\]}'.])'(?:\\.|[^'\\\n])'`, kind: 'string' },
    { re: String.raw`@[A-Za-z_][\w!]*`, kind: 'macro' },
    { re: String.raw`(?<=[\s(\[{,=]):[A-Za-z_]\w*`, kind: 'constant' },
    { re: NUMBER + '(?:im)?', kind: 'number' },
    words(String.raw`[A-Za-z_]\w*!?`, {
      keyword: 'if elseif else for while begin end return break continue try catch finally do using import export where in isa',
      storage: 'function macro struct mutable abstract primitive type module baremodule const local global let quote',
      constant: 'true false nothing missing NaN Inf pi im',
      declares: { function: 'function', macro: 'function', struct: 'type', type: 'type', module: 'type' },
      capitalTypes: true, call: /\.?\(/y,
    }),
  ],
}

const RUST: Spec = {
  rules: [
    ...C_COMMENTS,
    { re: String.raw`b?r(?<hashes>#*)"[\s\S]*?(?:"\k<hashes>|$)`, kind: 'string' },
    { re: String.raw`b?"(?:\\[\s\S]|[^"\\])*"?`, kind: 'string' },
    { re: String.raw`b?'(?:\\(?:[nrt\\'"0]|x[\da-fA-F]{2}|u\{[\da-fA-F]{1,6}\})|[^'\\\n])'`, kind: 'string' },
    { re: String.raw`'[A-Za-z_]\w*`, kind: 'storage' },
    { re: String.raw`#!?\[[^\]\n]*\]`, kind: 'macro' },
    { re: String.raw`[A-Za-z_]\w*!(?=\s*[(\[{])`, kind: 'macro' },
    { re: NUMBER + String.raw`(?:_?(?:[iu](?:8|16|32|64|128|size)|f32|f64))?`, kind: 'number' },
    words(String.raw`[A-Za-z_]\w*`, {
      keyword: 'if else match loop while for in return break continue await',
      storage: 'fn let mut const static struct enum trait impl type mod pub crate super extern unsafe async move ref dyn where as use',
      constant: 'true false self Self',
      type: 'i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char str',
      declares: { fn: 'function', struct: 'type', enum: 'type', trait: 'type', type: 'type' }, capitalTypes: true,
    }),
  ],
}

const JAVASCRIPT: Spec = {
  rules: [
    ...C_COMMENTS,
    { re: String.raw`"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?|` + '`(?:\\\\[\\s\\S]|[^`\\\\])*`?', kind: 'string' },
    { re: NUMBER + 'n?', kind: 'number' },
    words(String.raw`[A-Za-z_$][\w$]*`, {
      keyword: 'if else for while do switch case default break continue return throw try catch finally await yield import export from as',
      storage: 'function class const let var async extends static get set interface type enum implements declare readonly public private protected abstract namespace new delete typeof instanceof in of void keyof',
      constant: 'true false null undefined this super NaN Infinity',
      type: 'string number boolean any unknown never object symbol bigint',
      declares: { function: 'function', class: 'type', interface: 'type', type: 'type', enum: 'type' }, capitalTypes: true,
    }),
  ],
}

const C: Spec = {
  rules: [
    ...C_COMMENTS,
    { re: LINE_START + String.raw`#[ \t]*[A-Za-z]+`, kind: 'keyword' },
    { re: String.raw`(?<=#[ \t]*include[ \t]*)<[^>\n]*>`, kind: 'string' },
    { re: String.raw`"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])+'`, kind: 'string' },
    { re: NUMBER + '[uUlLfF]*', kind: 'number' },
    words(String.raw`[A-Za-z_]\w*`, {
      keyword: 'if else for while do switch case default break continue return goto sizeof try catch throw',
      storage: 'int char float double void long short signed unsigned const static extern struct union enum typedef auto register volatile inline bool class namespace template typename public private protected virtual using new delete operator constexpr noexcept explicit friend mutable override final',
      constant: 'true false NULL nullptr this',
      type: 'size_t ptrdiff_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t',
      declares: { struct: 'type', class: 'type', enum: 'type', union: 'type' }, capitalTypes: true,
    }),
  ],
}

const SHELL: Spec = {
  rules: [
    { re: String.raw`(?<![^\s;])#.*`, kind: 'comment' },
    { re: String.raw`'[^']*'?|"(?:\\[\s\S]|[^"\\])*"?`, kind: 'string' },
    { re: String.raw`\$\{[^}\n]*\}|\$[A-Za-z_]\w*|\$[0-9#@?$!*-]`, kind: 'variable' },
    { re: String.raw`\b\d+\b`, kind: 'number' },
    words(String.raw`[A-Za-z_]\w*`, {
      keyword: 'if then else elif fi for while until do done case esac in select return break continue',
      storage: 'function local export readonly declare',
      constant: 'true false',
      builtin: 'echo cd printf read set unset source exit test eval exec shift trap',
    }),
  ],
}

const JSON_SPEC: Spec = {
  rules: [
    { re: String.raw`"(?:\\.|[^"\\\n])*"(?=\s*:)`, kind: 'variable' },
    { re: String.raw`"(?:\\.|[^"\\\n])*"?`, kind: 'string' },
    { re: String.raw`-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b`, kind: 'number' },
    { re: String.raw`\b(?:true|false|null)\b`, kind: 'constant' },
  ],
}

const TOML: Spec = {
  rules: [
    { re: String.raw`(?<![^\s])[#;].*`, kind: 'comment' },
    { re: LINE_START + String.raw`\[\[?[^\]\n]*\]\]?`, kind: 'type' },
    { re: LINE_START + String.raw`[A-Za-z0-9_.\-"' ]*[A-Za-z0-9_"'](?=[ \t]*=)`, kind: 'variable' },
    { re: String.raw`"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'[^'\n]*'?`, kind: 'string' },
    { re: String.raw`\b(?:true|false)\b`, kind: 'constant' },
    { re: String.raw`[+-]?\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b`, kind: 'number' },
  ],
}

const YAML: Spec = {
  rules: [
    { re: String.raw`(?<![^\s])#.*`, kind: 'comment' },
    { re: String.raw`(?<=^|\n)(?:---|\.\.\.)(?=\s|$)`, kind: 'keyword' },
    { re: String.raw`(?<=(?:^|\n)[ \t]*(?:-[ \t]+)?)[^\s#:'"{}\[\],-][^:#\n]*?(?=:(?:[ \t]|\n|$))`, kind: 'variable' },
    { re: String.raw`"(?:\\.|[^"\\\n])*"?|'(?:''|[^'\n])*'?`, kind: 'string' },
    { re: String.raw`[&*][A-Za-z0-9_-]+`, kind: 'constant' },
    { re: String.raw`\b(?:true|false|null|yes|no)\b`, kind: 'constant' },
    { re: String.raw`[+-]?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b`, kind: 'number' },
  ],
}

const SPECS: Record<Language, Spec> = {
  latex: LATEX, bibtex: BIBTEX, python: PYTHON, julia: JULIA, rust: RUST, javascript: JAVASCRIPT, c: C,
  shell: SHELL, json: JSON_SPEC, toml: TOML, yaml: YAML,
}

/** The text as HTML, coloured for its language. */
export function highlight(text: string, language: Language): string {
  return run(text, SPECS[language])
}

/** The text as HTML, coloured when its language is known and only escaped otherwise. */
export function colour(text: string, language: Language | null): string {
  return language ? highlight(text, language) : escape(text)
}

/**
 * Coloured HTML cut into one block per line of the text (`<span class="code-line">`), which the viewer numbers.
 * A token spanning several lines (a block comment, a verbatim environment) is closed at each line end and
 * reopened on the next line; tokens never nest. The newlines themselves become the blocks' ends, so the copy
 * keeps exactly the textarea's characters, and an empty line keeps its height with a zero-width space.
 */
export function numberedLines(html: string, start?: number, end?: number): { html: string; count: number } {
  const lines: string[] = []
  let line = ''
  let open = ''  // the token span open across the current position, if any
  for (const part of html.split(/(<span class="tok-[a-z-]+">|<\/span>|\n)/)) {
    if (!part) continue
    if (part === '\n') {
      lines.push(line + (open ? '</span>' : ''))
      line = open
    } else {
      if (part.startsWith('<span')) open = part
      else if (part === '</span>') open = ''
      line += part
    }
  }
  lines.push(line)
  return {
    html: lines.map((text, index) => {
      const line = index + 1
      const cited = start && end && line >= start && line <= end
      const content = text.replace(/<span class="tok-[a-z-]+"><\/span>/g, '') || '\u200b'
      return `<span class="code-line${cited ? ' cited-source-selected' : ''}" data-line="${line}">${content}</span>`
    }).join(''),
    count: lines.length,
  }
}
