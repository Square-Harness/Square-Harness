# Style guidelines: notes (working notes, rough notes, cleaned-up notes)

Notes are short and direct. They are written for the author and close colleagues: no introduction, no motivation,
no announcement of what comes next.

- N1 [style] Brevity. Direct sentences; a routine computation is given in one display with its result, not line by
  line. No sentence announces the text ("We begin with", "In this section", "We now turn to").
- N2 [style] Statements. Each claim is stated with its hypotheses; it is in a theorem-like environment when it is
  used later, and a plain sentence otherwise.
- N3 [style] Notation. Every symbol is defined before or where it is first used, and one object has one symbol.
- N4 [style] References. A formula the text comes back to is numbered and cited with `\eqref`; a result is cited by
  number with `\ref`; never "the inequality above".
- N5 [style] Voice. The text states mathematics; it does not talk about the notes, the source or the draft. An open
  point is a line starting with "Open:" or a Remark, stated as mathematics.
- N6 [style] Displays. A long computation is an `align` with one relation per line; nothing runs into the margin.
- N7 [reader, section] Thread. The notes open with two or three sentences: the setting and the result they reach. Between two
  results, a few words say how the second uses or extends the first; the rule fails only where two results follow each
  other with nothing between them.
- N8 [reader, section] Order. Conventions and notation come once, at the start; a result comes after what its proof uses.
- N9 [reader, section] Coherence. No two passages contradict each other, and no paragraph is repeated in another place.
- N10 [reader] Length. The notes are not longer than the material they clean up, except for steps the request asks
  to fill in.
