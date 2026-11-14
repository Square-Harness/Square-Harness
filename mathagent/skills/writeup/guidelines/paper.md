# Style guidelines: mathematical paper text (article, notes, part of a paper)

Each rule has an identifier. A rule fails only when a passage of the document breaks it.

- P1 [style] References to formulas. A formula the text comes back to is numbered and cited with `\eqref`. The text never
  describes which formula it means ("the inequality above", "the previous estimate", "the bound on x_n we found",
  "letting m tend to infinity above") when a `\eqref` would say it.
- P2 [style] References to results. Theorems, lemmas, definitions, sections and figures are cited by name and number
  (`Theorem~\ref{...}`), never "the theorem above" or "the previous lemma".
- P3 [style] Notation. Every symbol is defined before or where it is first used, and one object has one symbol.
- P4 [style] Statements. A theorem, lemma or definition contains all its hypotheses; it can be read alone.
- P5 [style] Proofs. A proof longer than ten lines says at its start how it goes; each step says what it uses
  (a hypothesis, a numbered formula, an earlier result); the proof ends where the claim is reached.
- P6 [style] Sentences. Complete sentences in the first person plural; a displayed formula is part of its sentence and
  carries its punctuation; no sentence starts with a symbol.
- P7 [style] Voice. The text states mathematics. It does not talk about the notes, the source, the draft, or the
  author's doubts. An open point is a Remark or an "Open questions" paragraph, stated as mathematics.
- P8 [style] Displays. A long computation is an `align` with one relation per line; nothing runs into the margin.
- P9 [style] Signposting. Each section opens by saying what it does; a result is announced before it is stated.
- P10 [style] Economy. Nothing is said twice; no filler ("it is worth noting that", "it is easy to see that" without the reason).
