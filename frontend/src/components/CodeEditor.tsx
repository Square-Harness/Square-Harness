// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useMemo } from 'react'
import { highlight, languageOf } from '../highlight'
import './CodeEditor.css'

/** Beyond this many characters the text is left uncoloured, so that typing stays immediate. */
const MAX_COLOURED = 400_000

/**
 * A text file to edit, coloured for its language (TeX, BibTeX, Python, Julia, Rust and other code). The
 * coloured copy lies under a transparent textarea with the same metrics: the textarea grows with the text and the
 * box around both scrolls, so the two always line up and typing, selection and undo stay the browser's own.
 */
export function CodeEditor({ name, value, onChange }: { name: string; value: string; onChange: (value: string) => void }) {
  const language = languageOf(name)
  const html = useMemo(() => language && value.length <= MAX_COLOURED ? highlight(value, language) : null, [language, value])
  const textarea = <textarea className={html === null ? 'file-view-editor' : 'code-editor-input'} value={value} spellCheck={false}
    autoCapitalize="off" autoCorrect="off" aria-label={`Edit ${name}`} onChange={(event) => onChange(event.target.value)} />
  if (html === null) return textarea
  return (
    <div className="code-editor">
      <div className="code-editor-layer">
        {/* The final newline gives the copy the empty last line the textarea shows after a trailing newline. */}
        <pre className="code-editor-highlight" aria-hidden="true" dangerouslySetInnerHTML={{ __html: html + '\n' }} />
        {textarea}
      </div>
    </div>
  )
}
