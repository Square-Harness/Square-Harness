// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import type { Chat } from '../api'
import { ago } from '../format'
import { closeFile } from '../router'
import { ViewerPanel } from '../views/FileView'
import { ErrorNote } from './common'
import { CloseIcon, FileIcon } from './Icons'
import { MathMacrosProvider } from './Markdown'
import { OutputPreview, useChatOutputs } from './OutputsTab'

/**
 * A document an agent produced (a write-up's PDF or LaTeX, a literature review, a BibTeX file, a figure), in the
 * file viewer beside the conversation rather than in the notebook: the same reading as the notebook's Results
 * (citations, sources, downloads), with the room of the viewer.
 */
export function ResultPanel({ chat, id }: { chat: Chat; id: string }) {
  const { outputs, loaded, error } = useChatOutputs(chat)
  const output = outputs.find(item => item.id === id || item.aliases.includes(id))
  return <ViewerPanel label={output ? output.producer + ': ' + output.name : 'Result'}>
    <header className="work-panel-head file-panel-head">
      <span className="notebook-header-mark" aria-hidden="true"><FileIcon size={17} /></span>
      <div className="notebook-heading">
        <h2>{output?.name || 'Result'}</h2>
        <span className="notebook-head-status">{output ? [output.producer !== output.name ? output.producer : '', output.time ? 'saved ' + ago(output.time) : '']
          .filter(Boolean).join(' · ') : ''}</span>
      </div>
      <button type="button" className="icon-btn" aria-label="Close the document" title="Close the document" onClick={closeFile}><CloseIcon size={18} /></button>
    </header>
    <div className="file-view result-view">
      <MathMacrosProvider definitions={chat.math_macros}>
        {output ? <OutputPreview key={output.id} output={output} chat={chat.id} fit />
          : <><ErrorNote>{error}</ErrorNote><p role="status" className="muted">{loaded ? 'This result is unavailable in this conversation.' : 'Loading the result…'}</p></>}
      </MathMacrosProvider>
    </div>
  </ViewerPanel>
}
