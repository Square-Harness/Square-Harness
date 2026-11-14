// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Markdown with typeset mathematics. Model and source text is untrusted:
// no raw HTML, no external images, KaTeX with trust disabled, and only http(s)/mailto links.
// The one image source allowed is an image saved by a sandbox run in this folder, which the
// authenticated /api/artifacts endpoint serves after validating its id and name.
import { createContext, memo, useContext, useMemo, useState, type ReactNode } from 'react'
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

type Hast = { type: string; value?: string; tagName?: string; properties?: { className?: unknown }; children?: Hast[] }
const textOf = (node?: Hast): string => (node?.type === 'text' ? node.value || '' : (node?.children || []).map(textOf).join(''))

/** Copy text; outside a secure context (a phone on the local network) the clipboard API is missing. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    try { return document.execCommand('copy') } catch { return false } finally { area.remove() }
  }
}

/** A fenced code block with its language and a copy button: a LaTeX fragment is pasted into the user's own document. */
function CodeBlock({ node, children }: { node?: Hast; children?: ReactNode }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const code = node?.children?.find((child) => child.tagName === 'code')
  const classes = Array.isArray(code?.properties?.className) ? code.properties.className.map(String) : []
  const language = classes.find((name) => name.startsWith('language-'))?.slice('language-'.length) || ''
  const copy = async () => {
    setState(await copyText(textOf(node).replace(/\n$/, '')) ? 'copied' : 'failed')
    window.setTimeout(() => setState('idle'), 1600)
  }
  return (
    <div className="code-window">
      <div className="code-window-head">
        <span>{/^(la)?tex$/i.test(language) ? 'LaTeX' : language}</span>
        <button type="button" className="code-copy" onClick={copy}>
          {state === 'copied' ? 'Copied' : state === 'failed' ? 'Select the code to copy it' : 'Copy'}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  )
}

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
      h4: inlineText, h5: inlineText, h6: inlineText }
      : { pre: ({ node, children: code }) => <CodeBlock node={node as Hast | undefined}>{code}</CodeBlock> }),
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
