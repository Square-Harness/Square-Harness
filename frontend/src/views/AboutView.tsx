// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import { Glyph } from '../components/Glyph'
import { coverOf, MODE_CALLS } from '../modeCalls'
import { href } from '../router'

export function AboutView() {
  return (
    <div className="sheet paper">
      <div className="sheet-inner">
        <h1 className="page-title">About Square Harness</h1>
        <p className="lede">
          Ask the Assistant a mathematical question in your own words. It can answer directly or start a focused job,
          such as a proof or a literature check, and bring the result back to the conversation.
        </p>
        <section aria-labelledby="about-skills">
          <h2 className="section-title" id="about-skills">Skills</h2>
          <p className="lede">
            Each skill is built to do one specific task well. The Assistant calls them itself: it divides a large task
            into jobs that the skills can handle, then puts their results together. When the sandbox is available, it
            can also run a short automatic sanity check.
          </p>
          <p className="warning-note" role="note">
            You can call a skill yourself in a conversation by typing <code>@</code> followed by its name, for
            example <code>@writeup</code>. Python Lab and Sanity Check start a dedicated run. The Assistant is designed
            to handle complex tasks. A skill forced onto a task beyond its specific goal can fail in unexpected ways.
          </p>
          <ul className="about-skills" role="list">
            {MODE_CALLS.map(({ mode, label, summary }) => {
              const cover = coverOf(mode)
              return (
                <li key={mode}>
                  <span className={`route-cover mode-${cover}`} aria-hidden="true"><Glyph mode={cover} /></span>
                  <div>
                    <strong>{label}</strong> <code>@{mode}</code>
                    <p>{summary}</p>
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
        <p className="about-style">
          <a href={href('style')}>Learn my writing style</a> uses your own articles to guide future write-ups.
        </p>
      </div>
    </div>
  )
}
