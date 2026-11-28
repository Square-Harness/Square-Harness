// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { Chat } from '../api'
import { ago } from '../format'
import { closeFile } from '../router'
import { ViewerPanel, ViewerHead } from '../views/FileView'
import { MathMacrosProvider } from './Markdown'
import { ResultDocument, useChatOutputs } from './OutputsTab'

/**
 * A document an agent produced (a write-up's PDF or LaTeX, a review, a BibTeX file, a figure), opened in the file
 * viewer beside the conversation like any other file: its file name on top, then the document alone.
 */
export function ResultPanel({ chat, id }: { chat: Chat; id: string }) {
  const { outputs, loaded, error } = useChatOutputs(chat)
  const output = outputs.find(item => item.id === id || item.aliases.includes(id))
  const name = output?.fileName || output?.name || 'Document'
  const meta = output ? [output.producer, output.time ? 'saved ' + ago(output.time) : ''].filter(Boolean).join(' · ') : ''
  return <ViewerPanel label={name}>
    <ViewerHead name={name} meta={meta} download={output?.download} downloadName={output?.fileName} onClose={closeFile} />
    <div className="file-view">
      <MathMacrosProvider definitions={chat.math_macros}>
        {output ? <ResultDocument key={output.id} output={output} chat={chat.id} />
          : <p role="status" className="muted">{error || (loaded ? 'This document is unavailable in this conversation.' : 'Loading the document…')}</p>}
      </MathMacrosProvider>
    </div>
  </ViewerPanel>
}
