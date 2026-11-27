// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Markdown with typeset mathematics. Model and source text is untrusted:
// no raw HTML, no external images, KaTeX with trust disabled, and only http(s)/mailto links.
// The one image source allowed is an image saved by a sandbox run in this folder, which the
// authenticated /api/artifacts endpoint serves after validating its id and name.
import { createContext, memo, useContext, useMemo, type ReactNode } from 'react'
import { renderToString } from 'katex'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
// Copying a selection that contains typeset mathematics puts its LaTeX source, between $ or $$, in plain text.
import 'katex/contrib/copy-tex'
import { normalizeMath, remarkCitations, remarkDocumentLines, remarkPaperLinks, snippet, splitBlocks, type PaperLinks } from '../mathtext'
import type { PluggableList } from 'unified'
import { experimentImage } from '../artifacts'
import { api } from '../api'
import { openViewer, useRoute, viewerHref, type ViewerTarget } from '../router'
import { CopyButton, useLoad } from './common'
import { FileIcon } from './Icons'

const KATEX = { throwOnError: false, strict: 'ignore', trust: false, maxExpand: 500, maxSize: 20 } as const
const MathMacros = createContext<Record<string, string | object>>({})

export function MathMacrosProvider({ definitions, children }: { definitions?: Record<string, string>; children: ReactNode }) {
  const macros = useMemo(() => {
    const macros: Record<string, string | object> = {}
    for (const [name, definition] of Object.entries(definitions || {})) {
      // The server selected each definition with request notation ahead of templates.
      const source = definition.replace(/^\\(?:newcommand|providecommand)\b/, '\\renewcommand')
      const replacement = source.startsWith('\\renewcommand')
      if (replacement) macros['\\' + name] = ''
      try {
        renderToString(source, { ...KATEX, throwOnError: true, macros, globalGroup: true })
      } catch {
        if (replacement) delete macros['\\' + name]
      }
    }
    return macros
  }, [definitions])
  return <MathMacros.Provider value={macros}>{children}</MathMacros.Provider>
}

// -- rendering ---------------------------------------------------------------------

const safeUrl = (url: string) => (/^(https?:|mailto:)/i.test(url) ? url : experimentImage(url))
const inlineText = ({ children }: { children?: ReactNode }) => <>{children}</>
type HastNode = { type: string; value?: string; children?: HastNode[] }
const hastText = (node?: HastNode): string => node ? (node.value ?? (node.children || []).map(hastText).join('')) : ''

// Several passages often cite the same paper. Only its title is needed until a link is opened.
const documentTitles = new Map<string, Promise<string>>()
function documentTitle(id: string) {
  const known = documentTitles.get(id)
  if (known) return known
  const title = api.document(id).then(document => document.title).catch(error => {
    documentTitles.delete(id)
    throw error
  })
  documentTitles.set(id, title)
  return title
}

function DocumentLink({ id, start, end, flagged, onPassage }: {
  id: string; start: number; end: number; flagged?: boolean; onPassage?: () => void
}) {
  const route = useRoute()
  const { data, error } = useLoad(() => documentTitle(id), [id])
  const title = data || (error ? 'Unavailable document' : 'Document')
  const target: ViewerTarget = { kind: 'document', path: id, lines: { start, end } }
  const lines = start === end ? String(start) : `${start}–${end}`
  if (error && onPassage) return <button type="button" className={'cite' + (flagged ? ' cite-flagged' : '')}
    onClick={onPassage} title="Show the saved passage">Saved passage:{lines}</button>
  return <a className={'document-cite' + (flagged ? ' cite-flagged' : '')} href={viewerHref(route, target)}
    title={error || (flagged ? 'Flagged citation. ' : '') + `Open ${title} at ${start === end ? 'line' : 'lines'} ${lines}`}
    onClick={event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      event.stopPropagation()
      openViewer(target)
    }}><FileIcon size={13} />{title}<span className="document-cite-lines">:{lines}</span></a>
}

type Props = {
  children: string
  className?: string
  onCite?: (ref: string) => void
  flagged?: Set<string>
  inline?: boolean
  mathProse?: boolean
  papers?: PaperLinks
  /** Text the person typed: mathematics KaTeX cannot read keeps the text colour instead of error red. */
  quiet?: boolean
}

export const Markdown = memo(function Markdown({ children, className, onCite, flagged, inline, mathProse, papers, quiet }: Props) {
  const macros = useContext(MathMacros)
  const katex = useMemo(() => ({ ...KATEX, macros: { ...macros }, ...(quiet ? { errorColor: 'currentColor' } : {}) }), [macros, quiet])
  const components = useMemo<Components>(() => ({
    a: ({ href, title, children: label }) => (
      <a href={href} title={title} target="_blank" rel="noopener noreferrer nofollow">{label}</a>
    ),
    img: ({ src, alt }) => (typeof src === 'string' && experimentImage(src)
      ? <img className="experiment-inline" src={src} alt={alt || 'Sandbox figure'} loading="lazy" />
      : <span className="img-alt">(image{alt ? `: ${alt}` : ''} not loaded)</span>),
    // A code block keeps its exact text one click away.
    ...(inline ? {} : { pre: ({ node, children: code }) => (
      <div className="md-code">
        <pre>{code}</pre>
        <CopyButton className="code-copy" label="Copy" title="Copy this code" text={() => hastText(node as HastNode | undefined)} />
      </div>
    ) }),
    // Compact previews keep heading text and inline math without heading size.
    ...(inline ? { p: inlineText, h1: inlineText, h2: inlineText, h3: inlineText,
      h4: inlineText, h5: inlineText, h6: inlineText } : {}),
    cite: (props) => {
      const fields = props as Record<string, unknown>
      const ref = String(fields['data-cite'] || '')
      const bad = flagged?.has(ref)
      if (fields['data-document']) {
        const id = String(fields['data-document'])
        return <DocumentLink key={id} id={id} start={Number(fields['data-start'])} end={Number(fields['data-end'])}
          flagged={bad} onPassage={onCite && (() => onCite(ref))} />
      }
      return (
        <button type="button" className={'cite' + (bad ? ' cite-flagged' : '')}
          title={bad ? 'The controller flagged this citation' : 'Show the cited passage'}
          onClick={() => onCite?.(ref)}>
          {ref}
        </button>
      )
    },
  }), [onCite, flagged, inline])
  const source = useMemo(() => normalizeMath(children || '', mathProse), [children, mathProse])
  const plugins = useMemo<PluggableList>(() => {
    const list: PluggableList = [remarkGfm, remarkMath]
    if (!inline && !quiet) list.push(remarkDocumentLines)
    if (onCite) list.push(remarkCitations)
    if (papers?.size) list.push([remarkPaperLinks, papers])
    return list
  }, [onCite, papers, inline, quiet])
  const Wrapper = inline ? 'span' : 'div'
  return (
    <Wrapper className={(inline ? 'prose-inline' : 'prose') + (className ? ' ' + className : '')}>
      <ReactMarkdown
        remarkPlugins={plugins}
        rehypePlugins={[[rehypeKatex, katex]]}
        components={components}
        urlTransform={safeUrl}
      >
        {source}
      </ReactMarkdown>
    </Wrapper>
  )
})

/** One line of text with its mathematics typeset, shortened safely. */
export function Inline({ children, limit, className, mathProse }: { children: string; limit?: number; className?: string; mathProse?: boolean }) {
  const text = limit ? snippet(children, limit) : children.replace(/\s+/g, ' ').trim()
  return <Markdown inline mathProse={mathProse} className={className}>{text}</Markdown>
}

export function StreamingMarkdown({ text, className }: { text: string; className?: string }) {
  const blocks = splitBlocks(text)
  return (
    <div className={'streaming' + (className ? ' ' + className : '')}>
      {blocks.map((block, index) => <Markdown key={index}>{block}</Markdown>)}
    </div>
  )
}
