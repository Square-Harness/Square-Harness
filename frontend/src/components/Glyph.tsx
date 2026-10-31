import { MODES } from '../modes'
import type { Mode } from '../router'
import { FlaskIcon, SearchIcon } from './Icons'

// A literature check's mark in an Assistant conversation: a citation.
export const CHECK_GLYPH = '[1]'
// A numerical sanity check looks for a counterexample: it tests whether "for all" fails.
export const SANITY_GLYPH = '¬∀'

/** Marks of the Assistant's @ calls that are not notebooks. */
export type Cover = Mode | 'check' | 'python_lab' | 'sanity_check'

/** A mark. Literature and the Python Lab are drawn (sized by the font); the others are notation. */
export function Glyph({ mode }: { mode: Cover }) {
  if (mode === 'literature') return <SearchIcon size="1em" />
  if (mode === 'python_lab') return <FlaskIcon size="1em" />
  if (mode === 'sanity_check') return <>{SANITY_GLYPH}</>
  return <>{mode === 'check' ? CHECK_GLYPH : MODES[mode].glyph}</>
}
