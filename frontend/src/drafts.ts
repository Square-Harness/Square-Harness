// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Unsent messages, kept per conversation in this browser tab (sessionStorage): a draft survives a reload and a
// visit to another conversation, and goes when the tab closes or the account signs out. Only the text is kept;
// attachments are chosen again. Nothing is sent to the server.
const PREFIX = 'square-draft:'
const key = (chat: string | null) => PREFIX + (chat || 'new')

export function loadDraft(chat: string | null): string {
  try { return sessionStorage.getItem(key(chat)) || '' } catch { return '' }
}

export function saveDraft(chat: string | null, text: string) {
  try {
    if (text.trim()) sessionStorage.setItem(key(chat), text)
    else sessionStorage.removeItem(key(chat))
  } catch { /* private mode or a full store: the draft lives only on the page */ }
}

/** Another person may use this tab next (sign-out, account change). */
export function clearDrafts() {
  try {
    for (let index = sessionStorage.length - 1; index >= 0; index--) {
      const name = sessionStorage.key(index)
      if (name?.startsWith(PREFIX)) sessionStorage.removeItem(name)
    }
  } catch { /* nothing was stored */ }
}
