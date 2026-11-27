// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { api, type TranscriptItem } from '../api'

/** The files a finished write-up, summary or literature review can be downloaded as. The first is the primary one. */
export function JobDownloads({ job, outputs, partial = false }: {
  job: string; outputs: NonNullable<TranscriptItem['outputs']>
  /** A review stopped before the end: its download says it is partial. */
  partial?: boolean
}) {
  return <>
    {outputs.pdf && <a className="btn btn-small btn-primary" href={api.researchPdfDownloadUrl(job)} download>Download PDF</a>}
    {outputs.source && <a className={'btn btn-small' + (outputs.pdf ? '' : ' btn-primary')} href={api.researchSourceUrl(job)} download={outputs.source}>Sources (.zip)</a>}
    {outputs.download && <a className={'btn btn-small' + (outputs.pdf || outputs.source ? '' : ' btn-primary')} href={api.researchTexUrl(job)} download={outputs.download} title="Only the main LaTeX file. Images and template files must be beside it to compile; use Sources (.zip) for the complete bundle.">Main .tex only</a>}
    {outputs.download && outputs.body && <a className="btn btn-small" href={api.researchBodyUrl(job)} download={outputs.body} title="The text alone, without preamble, title or bibliography: paste it into your paper or \input it. Definitions it needs are listed at its top.">Body .tex</a>}
    {outputs.review && <a className="btn btn-small btn-primary" href={api.reviewMarkdownUrl(job)} download>{partial ? 'Download partial review (.md)' : 'Download review (.md)'}</a>}
    {outputs.bib && <a className={'btn btn-small' + (outputs.review ? '' : ' btn-primary')} href={outputs.review ? api.reviewBibtexUrl(job) : api.researchBibDownloadUrl(job)} download>BibTeX (.bib)</a>}
  </>
}

/** Whether a card's outputs include a document to download. */
export const hasDocument = (outputs?: TranscriptItem['outputs'] | null) =>
  !!outputs && !!(outputs.pdf || outputs.source || outputs.download || outputs.review || outputs.bib)
