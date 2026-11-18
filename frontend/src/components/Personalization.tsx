// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useState, type FormEvent } from 'react'
import { api, type Personalization } from '../api'
import { ErrorNote, Loading, Modal, useLoad } from './common'
import { SlidersIcon } from './Icons'

export function PersonalizationSettings({ onClose }: { onClose: () => void }) {
  const { data, error } = useLoad(api.personalization, [])
  const [draft, setDraft] = useState<Personalization | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const value = draft || data
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!value || saving) return
    setSaving(true)
    setSaveError('')
    try {
      await api.savePersonalization(value)
      onClose()
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : String(reason))
      setSaving(false)
    }
  }
  return (
    <Modal title="Personalization" icon={<SlidersIcon />} onClose={saving ? undefined : onClose}>
      <p className="modal-lede">Preferences that every new chat message follows, in all your folders. They add to the request and to any project notes.</p>
      <ErrorNote>{error || saveError}</ErrorNote>
      {!value && !error && <Loading />}
      {value && (
        <form onSubmit={save}>
          <label className="field">
            <span className="field-label">Standing instructions</span>
            <span className="field-hint">How you like answers: language, level of detail, conventions, what to avoid.</span>
            <textarea rows={6} maxLength={4000} disabled={saving} value={value.instructions}
              placeholder={'For example: answer in French; state every hypothesis; prefer short proofs with the key idea first.'}
              onChange={(event) => setDraft({ ...value, instructions: event.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">LaTeX macros</span>
            <span className="field-hint">Declarations such as <code>{String.raw`\newcommand{\RR}{\mathbb{R}}`}</code>, one per line. The model may use them and the chat displays them.</span>
            <textarea className="mono-input" rows={6} maxLength={4000} disabled={saving} spellCheck={false} value={value.macros}
              placeholder={String.raw`\newcommand{\RR}{\mathbb{R}}` + '\n' + String.raw`\DeclareMathOperator{\supp}{supp}`}
              onChange={(event) => setDraft({ ...value, macros: event.target.value })} />
          </label>
          <div className="modal-actions">
            <button type="button" className="btn" disabled={saving} onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving || !draft}>{saving ? 'Saving…' : 'Save preferences'}</button>
          </div>
        </form>
      )}
    </Modal>
  )
}
