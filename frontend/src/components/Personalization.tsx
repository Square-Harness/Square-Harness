// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useState, type FormEvent } from 'react'
import { api, type Personalization } from '../api'
import { ErrorNote, Loading, Modal, useLoad } from './common'

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
    <Modal title="Personalization" onClose={saving ? undefined : onClose} wide>
      <ErrorNote>{error || saveError}</ErrorNote>
      {!value && !error && <Loading />}
      {value && (
        <form className="personalization-form" onSubmit={save}>
          <label>
            <span className="field-label">Standing instructions</span>
            <span className="muted">Preferences for future chat messages across your folders.</span>
            <textarea rows={5} maxLength={4000} disabled={saving} value={value.instructions}
              onChange={(event) => setDraft({ ...value, instructions: event.target.value })} />
          </label>
          <label>
            <span className="field-label">LaTeX macros</span>
            <span className="muted">Use declarations like <code>{String.raw`\newcommand{\RR}{\mathbb{R}}`}</code> for chat and its math display.</span>
            <textarea rows={5} maxLength={4000} disabled={saving} spellCheck={false} value={value.macros}
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
