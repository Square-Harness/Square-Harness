// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useLayoutEffect, useMemo, useRef, type CSSProperties } from 'react'
import { colour, languageOf, numberedLines, type Language } from '../highlight'
import { REVEAL_LINES, type LineRange } from '../router'
import { ErrorNote } from './common'
import './CodeEditor.css'

/** Large uncited files fall back to plain text so that typing stays immediate. */
const MAX_COLOURED = 400_000

function useNumbered(name: string, text: string, lines?: LineRange, syntax?: Language, editable = false) {
  const language = syntax ?? languageOf(name)
  const start = lines?.start, end = lines?.end
  const enabled = !!lines || !!syntax || text.length <= MAX_COLOURED
  // The editable overlay is rebuilt on each keystroke. For a large source, tokenize only the cited passage.
  const rangeOnly = editable && text.length > MAX_COLOURED && language
  const from = rangeOnly && start, to = rangeOnly && end
  const coloured = useMemo(() => {
    if (!enabled) return null
    if (!from || !to) return colour(text, language)
    const rows = text.split('\n')
    return [rows.slice(0, from - 1), rows.slice(from - 1, to), rows.slice(to)]
      .map((part, index) => part.length ? colour(part.join('\n'), index === 1 ? language : null) : null)
      .filter((part) => part !== null).join('\n')
  }, [enabled, text, language, from, to])
  return useMemo(() => {
    if (coloured === null) return null
    const { html, count } = numberedLines(coloured, start, end)
    return { html, count, style: { '--gutter': Math.max(2, String(count).length) + 'ch' } as CSSProperties }
  }, [coloured, start, end])
}

function useRevealLines(lines?: LineRange) {
  const box = useRef<HTMLDivElement>(null)
  const start = lines?.start, end = lines?.end
  useLayoutEffect(() => {
    if (!start || !end) return
    const reveal = () => {
      const scroller = box.current
      const row = scroller?.querySelector('.cited-source-selected')
      if (!row || !scroller) return
      const editor = scroller.querySelector('textarea')
      if (editor) {
        const source = editor.value.split('\n')
        const from = source.slice(0, start - 1).reduce((offset, line) => offset + line.length + 1, 0)
        const to = from + source.slice(start - 1, end).join('\n').length
        editor.focus({ preventScroll: true })
        editor.setSelectionRange(from, to, 'backward')
      }
      scroller.scrollTop += row.getBoundingClientRect().top - scroller.getBoundingClientRect().top - scroller.clientHeight / 4
    }
    reveal()
    window.addEventListener(REVEAL_LINES, reveal)
    return () => window.removeEventListener(REVEAL_LINES, reveal)
  }, [start, end])
  return box
}

/** A saved source or output, coloured and numbered like the editor. */
export function CodeView({ name, text, lines, syntax }: { name: string; text: string; lines?: LineRange; syntax?: Language }) {
  const numbered = useNumbered(name, text, lines, syntax)
  const box = useRevealLines(lines)
  return <>
    {lines && numbered && lines.end > numbered.count && <ErrorNote>The citation extends past the end of this document.</ErrorNote>}
    <div ref={box} className="code-editor code-view" style={numbered?.style} tabIndex={lines ? 0 : undefined}
      aria-label={lines ? name : undefined} role={lines ? 'region' : undefined}>
      <div className="code-editor-layer">
        {numbered ? <pre className="code-editor-highlight code-numbered" dangerouslySetInnerHTML={{ __html: numbered.html }} />
          : <pre className="code-editor-highlight">{text}</pre>}
      </div>
    </div>
  </>
}

/**
 * A text file to edit, numbered by line and coloured for its language (TeX, BibTeX, Python, Julia, Rust and other
 * code; other text stays plain). The coloured copy lies under a transparent textarea with the same metrics: the
 * textarea grows with the text and the box around both scrolls, so the two always line up and typing, selection
 * and undo stay the browser's own. The line numbers sit in the copy's left margin, outside the text.
 */
export function CodeEditor({ name, value, onChange, autoFocus = false, lines }: {
  name: string; value: string; onChange: (value: string) => void; autoFocus?: boolean; lines?: LineRange
}) {
  const numbered = useNumbered(name, value, lines, undefined, true)
  const box = useRevealLines(lines)
  const input = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { if (autoFocus) input.current?.focus() }, [autoFocus])
  const textarea = <textarea ref={input} className={numbered ? 'code-editor-input' : 'file-view-editor'} value={value} spellCheck={false}
    autoCapitalize="off" autoCorrect="off" aria-label={`Edit ${name}`} onChange={(event) => onChange(event.target.value)} />
  if (!numbered) return textarea
  return <>
    {lines && lines.end > numbered.count && <ErrorNote>The citation extends past the end of this file.</ErrorNote>}
    <div ref={box} className="code-editor" style={numbered.style}>
      <div className="code-editor-layer">
        <pre className="code-editor-highlight code-numbered" aria-hidden="true" dangerouslySetInnerHTML={{ __html: numbered.html }} />
        {textarea}
      </div>
    </div>
  </>
}
