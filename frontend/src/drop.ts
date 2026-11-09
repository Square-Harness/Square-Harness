// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 SquareHarness Team

// One drop target at a time: the form or composer on screen decides what a dropped
// file is for (a pinned source, a template, an attachment). Files are always saved
// in the workspace first.
import { useEffect, useRef, useSyncExternalStore } from 'react'

type Handler = (paths: string[]) => void
type Target = { handler: Handler; label: string; project_id?: string | null; pending: number }
let current: Target | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((listener) => listener())
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
export const useUploadsPending = () => useSyncExternalStore(subscribe, () => current?.pending || 0)

/** Track the original target, even if navigation replaces the active composer. */
export function beginUpload(target: Target | null) {
  if (target) { target.pending++; notify() }
  return () => { if (target) { target.pending--; notify() } }
}

export function useDropTarget(label: string, handler: Handler, project_id?: string | null) {
  const latest = useRef(handler)
  latest.current = handler
  useEffect(() => {
    const entry = { get handler() { return latest.current }, label, project_id, pending: 0 }
    current = entry
    notify()
    return () => {
      if (current === entry) { current = null; notify() }
    }
  }, [label, project_id])
  return useUploadsPending()
}

export function dropTarget() {
  return current
}
