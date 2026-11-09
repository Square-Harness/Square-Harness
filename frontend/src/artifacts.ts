// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

// Artifacts of sandbox runs: files the interface shows from model or tool output.
// The server validates the run id and the file name again, and serves each type with
// headers that keep it inert (images; HTML only in a sandboxed frame; text for data).
import type { TranscriptItem } from './api.ts'

export type Artifact = {
  artifact_id: string
  filename: string
  mime_type: string
  url: string
  width?: number | null
  height?: number | null
  bytes?: number | null
}

const NAME = '[A-Za-z0-9][A-Za-z0-9._-]{0,63}'
const RUN = 'exp-\\d{8}-\\d{6}-[0-9a-f]{6}'
const WORKSPACE_IMAGE = new RegExp(`^(?:\\./)?\\.mathagent/experiments/(${RUN})/(${NAME}\\.(?:png|svg))$`)
const SERVED_IMAGE = new RegExp(`^/api/artifacts/${RUN}/${NAME}\\.(?:png|svg)$`)

/** The served URL of a file saved by a sandbox run. */
export const artifactUrl = (run: string, name: string) => `/api/artifacts/${encodeURIComponent(run)}/${encodeURIComponent(name)}`

/** A workspace image path such as .mathagent/experiments/<id>/figure-1.png, as a served URL; '' otherwise. */
export const experimentImage = (url: string) => {
  const match = WORKSPACE_IMAGE.exec(url)
  return match ? artifactUrl(match[1], match[2]) : SERVED_IMAGE.test(url) ? url : ''
}

/** How an artifact is shown. */
export const displayOf = (mime: string): 'image' | 'page' | 'document' | 'data' =>
  mime === 'image/png' || mime === 'image/svg+xml' ? 'image' : mime === 'text/html' ? 'page'
    : mime === 'application/pdf' ? 'document' : 'data'

export const artifactsOf = (item: TranscriptItem): Artifact[] => item.artifacts || []
