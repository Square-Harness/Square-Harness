import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { MODE_CALLS, coverOf, modeOptions, modeQuery, updateCall, validCall, type ModeCall, type ModeQuery } from '../modeCalls'
import { CloseIcon } from './Icons'
import { Glyph } from './Glyph'
import { EFFORTS, type EffortLevel } from './Effort'
import './ModeMessage.css'

export function ModeMessage({ value, onChange, call, onCall, effort, onEffort, hasEffort, onSend, disabled }: {
  value: string; onChange: (value: string) => void
  call: ModeCall | null; onCall: (call: ModeCall | null) => void
  effort: EffortLevel | null; onEffort: (effort: EffortLevel | null) => void; hasEffort: boolean
  onSend: () => void; disabled: boolean
}) {
  const input = useRef<HTMLTextAreaElement>(null)
  const selectedCaret = useRef<number | null>(null)
  const listId = useId()
  const [query, setQuery] = useState<ModeQuery | null>(null)
  const queryRef = useRef<ModeQuery | null>(null)
  const dismissed = useRef<{ text: string; start: number } | null>(null)
  const [active, setActive] = useState(0)
  // Hovering or focusing the selected mode opens its menu; a click keeps it open (touch screens).
  const [pinned, setPinned] = useState(false)
  const chip = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!pinned) return
    const close = (event: MouseEvent) => { if (!chip.current?.contains(event.target as Node)) setPinned(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [pinned])
  const options = query ? modeOptions(query.query) : []
  const open = !disabled && options.length > 0
  useLayoutEffect(() => {
    const caret = selectedCaret.current
    if (caret === null) return
    selectedCaret.current = null
    input.current?.focus()
    input.current?.setSelectionRange(caret, caret)
  }, [value, call])
  useEffect(() => {
    if (open) document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: 'nearest' })
  }, [active, open, listId])
  const refresh = (text: string, start: number, end: number) => {
    const next = modeQuery(text, start, end)
    const shown = next && !(validCall(text, call) && next.start === call.start)
      && !(dismissed.current?.text === text && dismissed.current.start === next.start) ? next : null
    const previous = queryRef.current
    if (shown?.start === previous?.start && shown?.end === previous?.end && shown?.query === previous?.query) return
    queryRef.current = shown
    setQuery(shown)
    setActive(0)
  }
  const select = (index: number) => {
    if (!query || !options[index]) return
    const mode = options[index].mode
    const token = '@' + mode
    const suffix = value.slice(query.end)
    const space = suffix.length === 0 ? ' ' : ''
    const next = value.slice(0, query.start) + token + space + suffix
    selectedCaret.current = query.start + token.length + space.length
    onChange(next)
    onCall({ mode, start: query.start, end: query.start + token.length })
    setQuery(null)
    queryRef.current = null
  }
  const keys = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return
    if (open && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((index) => (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
        return
      }
      if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); select(active); return }
      if (event.key === 'Escape') {
        event.preventDefault()
        dismissed.current = { text: value, start: query!.start }
        queryRef.current = null
        setQuery(null)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); onSend() }
  }
  return (
    <div className="mode-message">
      {validCall(value, call) && (() => {
        const info = MODE_CALLS.find((item) => item.mode === call.mode)
        const level = EFFORTS.find((item) => item.id === effort)
        const choose = (next: EffortLevel | null) => { onEffort(next); input.current?.focus() }
        return <div className={'selected-mode-wrap' + (pinned ? ' selected-mode-open' : '')} ref={chip}>
          <div className="selected-mode-menu" role="group" aria-label={`@${call.mode} options`}>
            <p className="selected-mode-title"><strong>@{call.mode}</strong>{info && info.label !== call.mode && <span> · {info.label}</span>}</p>
            {info && <p className="selected-mode-about">{info.summary}</p>}
            {hasEffort ? <>
              <p className="selected-mode-label">Effort</p>
              <div role="radiogroup" aria-label="Effort for this message" className="effort-menu">
                {[{ id: null, label: 'Auto', hint: 'Not set: the harness picks the effort for this mode' }, ...EFFORTS].map((item) => (
                  <button key={item.id ?? 'auto'} type="button" role="radio" aria-checked={item.id === effort}
                    className={'effort-menu-item' + (item.id === effort ? ' effort-menu-current' : '')}
                    disabled={disabled} onClick={() => choose(item.id)}>
                    <span className="effort-menu-name">{item.label}</span><span className="effort-menu-hint">{item.hint}</span>
                  </button>
                ))}
              </div>
            </> : <p className="selected-mode-label">No effort setting for this mode.</p>}
          </div>
          <div className="selected-mode" role="status" tabIndex={0} onClick={() => setPinned((on) => !on)}
            onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setPinned((on) => !on) } else if (event.key === 'Escape') setPinned(false) }}
            aria-haspopup="true" aria-expanded={pinned}>
            <span className={`route-cover mode-${coverOf(call.mode)}`}><Glyph mode={coverOf(call.mode)} /></span>
            <strong>@{call.mode}</strong><span>{hasEffort ? (level ? `${level.label} effort` : 'Auto effort') : 'Selected for this message'}</span>
            <button type="button" className="chip-remove" aria-label="Clear selected mode" onClick={(event) => { event.stopPropagation(); onCall(null); setPinned(false); setQuery(null); input.current?.focus() }}><CloseIcon size={13} /></button>
          </div>
        </div>
      })()}
      {open && <div className="mode-picker">
        <div className="mode-picker-title">Modes <span>↑ ↓ to browse · Enter or Tab to select · Esc to dismiss</span></div>
        <div id={listId} role="listbox" aria-label="Choose a mode">
          {options.map((item, index) => <div key={item.mode} id={`${listId}-${index}`} role="option"
            aria-selected={index === active} className={'mode-option' + (index === active ? ' mode-option-active' : '')}
            onMouseEnter={() => setActive(index)} onMouseDown={(event) => event.preventDefault()} onClick={() => select(index)}>
            <span className={`route-cover mode-${coverOf(item.mode)}`}><Glyph mode={coverOf(item.mode)} /></span>
            <span><strong>@{item.mode}</strong><span className="mode-option-summary">{item.summary}</span></span>
          </div>)}
        </div>
      </div>}
      <textarea ref={input} value={value} onChange={(event) => {
        const next = event.target.value
        onCall(updateCall(value, next, call))
        onChange(next)
        refresh(next, event.target.selectionStart, event.target.selectionEnd)
      }} onSelect={(event) => refresh(value, event.currentTarget.selectionStart, event.currentTarget.selectionEnd)}
        onBlur={() => { queryRef.current = null; setQuery(null) }} onKeyDown={keys}
        rows={Math.min(8, Math.max(2, value.split('\n').length))}
        placeholder="Describe what you want to do; type @ to select a mode…" aria-label="Message"
        role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined} />
    </div>
  )
}
