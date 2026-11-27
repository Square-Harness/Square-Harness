// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useMemo, useRef, type CSSProperties } from 'react'
import { colour, languageOf, numberedLines } from '../highlight'
import './CodeEditor.css'

/** Beyond this many characters the text is shown plain, without colours or line numbers, so that typing stays immediate. */
const MAX_COLOURED = 400_000

/** The coloured, numbered copy of a text; null when it is too long. The gutter fits the largest line number. */
function useNumbered(name: string, text: string) {
  const language = languageOf(name)
  return useMemo(() => {
    if (text.length > MAX_COLOURED) return null
    const { html, count } = numberedLines(colour(text, language))
    return { html, style: { '--gutter': Math.max(2, String(count).length) + 'ch' } as CSSProperties }
  }, [language, text])
}

/** A text to read only (a write-up's LaTeX, a BibTeX file), coloured for its language and numbered like the editor. */
export function CodeView({ name, text }: { name: string; text: string }) {
  const numbered = useNumbered(name, text)
  return <div className="code-editor code-view" style={numbered?.style}>
    {numbered ? <pre className="code-editor-highlight code-numbered" dangerouslySetInnerHTML={{ __html: numbered.html }} />
      : <pre className="code-editor-highlight">{text}</pre>}
  </div>
}

/**
 * A text file to edit, numbered by line and coloured for its language (TeX, BibTeX, Python, Julia, Rust and other
 * code; other text stays plain). The coloured copy lies under a transparent textarea with the same metrics: the
 * textarea grows with the text and the box around both scrolls, so the two always line up and typing, selection
 * and undo stay the browser's own. The line numbers sit in the copy's left margin, outside the text.
 */
export function CodeEditor({ name, value, onChange, autoFocus = false }: {
  name: string; value: string; onChange: (value: string) => void; autoFocus?: boolean
}) {
  const numbered = useNumbered(name, value)
  const input = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { if (autoFocus) input.current?.focus() }, [autoFocus])
  const textarea = <textarea ref={input} className={numbered ? 'code-editor-input' : 'file-view-editor'} value={value} spellCheck={false}
    autoCapitalize="off" autoCorrect="off" aria-label={`Edit ${name}`} onChange={(event) => onChange(event.target.value)} />
  if (!numbered) return textarea
  return (
    <div className="code-editor" style={numbered.style}>
      <div className="code-editor-layer">
        <pre className="code-editor-highlight code-numbered" aria-hidden="true" dangerouslySetInnerHTML={{ __html: numbered.html }} />
        {textarea}
      </div>
    </div>
  )
}
