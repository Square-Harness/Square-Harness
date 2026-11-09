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
import { normalizeMath, remarkCitations, snippet, splitBlocks } from '../mathtext'
import { experimentImage } from '../artifacts'

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

type Props = {
  children: string
  className?: string
  onCite?: (ref: string) => void
  flagged?: Set<string>
  inline?: boolean
}

export const Markdown = memo(function Markdown({ children, className, onCite, flagged, inline }: Props) {
  const macros = useContext(MathMacros)
  const katex = useMemo(() => ({ ...KATEX, macros: { ...macros } }), [macros])
  const components = useMemo<Components>(() => ({
    a: ({ href, children: label }) => (
      <a href={href} target="_blank" rel="noopener noreferrer nofollow">{label}</a>
    ),
    img: ({ src, alt }) => (typeof src === 'string' && experimentImage(src)
      ? <img className="experiment-inline" src={src} alt={alt || 'Sandbox figure'} loading="lazy" />
      : <span className="img-alt">(image{alt ? `: ${alt}` : ''} not loaded)</span>),
    // Compact previews keep heading text and inline math without heading size.
    ...(inline ? { p: inlineText, h1: inlineText, h2: inlineText, h3: inlineText,
      h4: inlineText, h5: inlineText, h6: inlineText } : {}),
    cite: (props) => {
      const ref = String((props as Record<string, unknown>)['data-cite'] || '')
      const bad = flagged?.has(ref)
      return (
        <button type="button" className={'cite' + (bad ? ' cite-flagged' : '')}
          title={bad ? 'The controller flagged this citation' : 'Show the cited passage'}
          onClick={() => onCite?.(ref)}>
          {ref}
        </button>
      )
    },
  }), [onCite, flagged, inline])
  const source = useMemo(() => normalizeMath(children || ''), [children])
  const Wrapper = inline ? 'span' : 'div'
  return (
    <Wrapper className={(inline ? 'prose-inline' : 'prose') + (className ? ' ' + className : '')}>
      <ReactMarkdown
        remarkPlugins={onCite ? [remarkGfm, remarkMath, remarkCitations] : [remarkGfm, remarkMath]}
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
export function Inline({ children, limit, className }: { children: string; limit?: number; className?: string }) {
  const text = limit ? snippet(children, limit) : children.replace(/\s+/g, ' ').trim()
  return <Markdown inline className={className}>{text}</Markdown>
}

export function StreamingMarkdown({ text, className }: { text: string; className?: string }) {
  const blocks = splitBlocks(text)
  return (
    <div className={'streaming' + (className ? ' ' + className : '')}>
      {blocks.map((block, index) => <Markdown key={index}>{block}</Markdown>)}
    </div>
  )
}
