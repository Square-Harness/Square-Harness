// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import { refreshStatus } from '../store'
import { ErrorNote, Loading, Modal, Toggle, useLoad } from './common'
import { SlidersIcon } from './Icons'

export function ProviderSettings({ onClose }: { onClose: () => void }) {
  const { data, error } = useLoad(api.providerSettings, [])
  const [selected, setSelected] = useState('')
  const [host, setHost] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [remember, setRemember] = useState(false)
  const [clearApiKey, setClearApiKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  useEffect(() => {
    if (!data) return
    const option = data.providers.find((item) => item.id === data.selected)
    if (!option) return
    setSelected(option.id)
    setHost(option.host)
    setModel(option.model)
    setRemember(option.remembered)
  }, [data])

  const active = data?.providers.find((item) => item.id === selected)
  const rememberLabel = data?.mode === 'local'
    ? 'Remember API key on this computer'
    : data?.remember_store ? `Remember API key in ${data.remember_store}` : 'Remember API key'
  const rememberHint = data?.mode === 'local'
    ? 'Off by default. When enabled, the app stores the key in a permission-restricted file outside workspaces.'
    : data?.remember_available
      ? `Off by default. When enabled, the app stores the key in your account's ${data.remember_store}.`
      : 'Server keys are session-only. Persistent server storage requires an operator-managed secret service.'
  const apiKeyPlaceholder = active?.has_api_key
    ? active.remembered
      ? `Remembered in ${data?.mode === 'local' ? 'the local credential file' : data?.remember_store}; leave blank to keep it`
      : 'Available in this session; leave blank to keep it'
    : 'Enter provider API key'
  function chooseProvider(id: string) {
    const option = data?.providers.find((item) => item.id === id)
    if (!option) return
    setSelected(id)
    setHost(option.host)
    setModel(option.model)
    setApiKey('')
    setRemember(option.remembered)
    setClearApiKey(false)
    setSaveError('')
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!data || !active || saving) return
    setSaving(true)
    setSaveError('')
    try {
      await api.updateProviderSettings({
        provider_id: selected,
        host,
        model,
        ...(apiKey ? { api_key: apiKey } : {}),
        remember,
        ...(clearApiKey ? { clear_api_key: true } : {}),
      })
      setApiKey('')
      setClearApiKey(false)
      await refreshStatus()
      onClose()
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : String(reason))
      setSaving(false)
    }
  }

  return (
    <Modal title="Model provider" icon={<SlidersIcon />} onClose={saving ? undefined : onClose}>
      <p className="modal-lede">Choose the model service used for new work. Provider credentials are sent to this backend only when you save; they are never returned to the browser or written into conversations.</p>
      <ErrorNote>{error || saveError}</ErrorNote>
      {!data && !error && <Loading />}
      {data && (
        <form onSubmit={save}>
          <label className="field">
            <span className="field-label">Provider</span>
            <select value={active?.id || data.selected} disabled={saving} onChange={(event) => chooseProvider(event.target.value)}>
              {data.providers.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Endpoint URL</span>
            <input className="text-input" type="url" required maxLength={2048} value={host} disabled={saving}
              list="provider-hosts" autoComplete="url" onChange={(event) => setHost(event.target.value)} />
            {data.mode === 'server' && (
              <>
                <datalist id="provider-hosts">
                  {data.allowed_hosts.map((allowed) => <option key={allowed} value={allowed} />)}
                </datalist>
                <span className="field-hint">Server mode accepts only origins configured by the operator. Allowed origins appear as suggestions.</span>
              </>
            )}
          </label>
          <label className="field">
            <span className="field-label">Model name</span>
            <input className="text-input" type="text" required maxLength={256} value={model} disabled={saving}
              autoComplete="off" onChange={(event) => setModel(event.target.value)} />
          </label>
          {active?.supports_api_key && (
            <>
              <label className="field">
                <span className="field-label">API key</span>
                <input className="text-input" type="password" maxLength={16384} value={apiKey} disabled={saving}
                  autoComplete="new-password" placeholder={apiKeyPlaceholder}
                  onChange={(event) => setApiKey(event.target.value)} />
                <span className="field-hint">{active.requires_api_key ? 'Required by this provider.' : 'Optional for this endpoint.'} The key is not displayed again after saving.</span>
              </label>
              {active.has_api_key && (
                <label className="toggle">
                  <input type="checkbox" checked={clearApiKey} disabled={saving}
                    onChange={(event) => {
                      const clear = event.target.checked
                      setClearApiKey(clear)
                      if (clear) { setApiKey(''); setRemember(false) }
                    }} />
                  <span className="toggle-track" aria-hidden="true"><span className="toggle-thumb" /></span>
                  <span className="toggle-text"><span>Remove the current API key</span></span>
                </label>
              )}
              <Toggle label={rememberLabel} checked={remember}
                onChange={setRemember} disabled={saving || !data.remember_available}
                hint={rememberHint} />
              {active.has_api_key && <p className="muted small" role="status">
                {active.remembered
                  ? `This key is remembered in ${data.mode === 'local' ? 'the local private credential file' : data.remember_store}.`
                  : 'A key is available in this session.'}
              </p>}
            </>
          )}
          <div className="modal-actions">
            <button type="button" className="btn" disabled={saving} onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving || !active}>{saving ? 'Saving…' : 'Save provider'}</button>
          </div>
        </form>
      )}
    </Modal>
  )
}
