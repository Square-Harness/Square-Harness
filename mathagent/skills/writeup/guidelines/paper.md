# Style guidelines: mathematical text (article, part of a paper, a cleaned-up result)

Each rule has an identifier. A rule fails only when a passage of the document breaks it, and applies only when the
document contains what it is about. Rules marked [reader] are about the document as a whole and are checked by a
reader of the whole text. A rule tagged `section` applies from the size of a section of a paper; a rule tagged
`paper` applies to a whole paper only. A short write-up follows the untagged rules alone.

- P1 [style] References to formulas. A formula the text comes back to is numbered and cited with `\eqref`; only such
  formulas are numbered. The text never describes which formula it means ("the inequality above", "the previous
  estimate", "the bound on x_n we found") when a `\eqref` would say it.
- P2 [style] References to results. Theorems, lemmas, definitions, sections and figures are cited by name and number
  (`Theorem~\ref{...}`), never "the theorem above", and the name matches what the label points to.
- P3 [style] Notation. Every symbol is defined before or where it is first used, never borrowed from a later passage,
  and one object has one symbol.
- P4 [style] Statements. A theorem, lemma or definition contains all its hypotheses and says what its constants depend
  on; it can be read alone. It holds the statement only, with no commentary inside.
- P5 [style] Proofs. A proof longer than ten lines says at its start how it goes; each step of a chain of equalities
  or inequalities says what it uses (a hypothesis, a numbered formula, an earlier result); what is left out is said to
  be left out, and "clearly" is kept for a check of one line. The proof ends where the claim is reached.
- P6 [style] Sentences. Complete sentences in the first person plural; a displayed formula is part of its sentence and
  carries its punctuation (a sentence that ends with a colon or with "we get" before a display is complete); no
  sentence starts with a symbol, and words separate two formulas.
- P7 [style] Voice. The text states mathematics. It does not talk about the notes, the source, the draft, or the
  author's doubts. The status of each claim is explicit (proved, known and cited, conjectured, observed numerically).
  An open point is a Remark or an "Open questions" paragraph, stated as mathematics. Saying what the text proves or
  how it compares with earlier work is mathematics, not talk about the draft.
- P8 [style] Displays. A long computation is an `align` with one relation per line; nothing runs into the margin.
- P9 [style] No empty opener. No sentence only announces the text ("We begin with the setting", "We now recall some
  results", "In this section we state the theorem"): it starts with the mathematics, or says what the next result
  is for.
- P10 [style] Economy. Nothing is repeated word for word, and no filler ("it is worth noting that", "it is easy to see
  that" without the reason). Saying a statement or a definition again in another form, in words or on an example, is
  not repetition.
- P16 [style] Definitions. Most terms are defined in the running text where they are first needed ("we say that",
  "is called"). A Definition environment is kept for the few central notions and for those cited later by number.
- P11 [reader, section] Thread. Where one result uses another, the text says so at the place of use, with the
  reference; a result used later is said to be, once. The rule fails where such a link is missing, where a section
  starts with a theorem-like environment, or where a result arrives with nothing that tells where it fits. It does not
  ask for a sentence before every statement.
- P12 [reader, section] Hierarchy. The reader can tell the main result from the auxiliary ones: the main result is
  named as such, an auxiliary lemma is introduced by what it serves, and a consequence is presented as one.
- P13 [reader, section] Opening. The document (or the section, for a part of a paper) starts with a few sentences that
  say what is proved in it and under which assumptions, before the first definition or statement.
- P14 [reader, section] Comment. A main result is followed by what the material says about it: which hypothesis is
  needed for what, whether the constant or the rate is sharp, an example. Nothing is invented for this.
- P15 [reader, section] Coherence. No two passages contradict each other, and no paragraph is repeated in another place.
- P17 [reader, section] Central notions. A notion the whole text rests on is not dropped on the reader: a sentence
  before its definition says why it is needed, and the definition is followed by its meaning in words or by an example
  or a non-example, when the material has one. A definition used more than a section after it was given is recalled.
- P18 [reader, paper] Model case. When the main proof is long or technical and the material treats a simpler case, that
  case comes first, and the text says what it shows and what the general case adds.
- P19 [reader, paper] Ending. A paper whose content is its theorems ends with its last result, with no summary. A paper
  about a method or about numerical evidence ends with a short discussion that states its limits.
- P20 [style, paper] Title. At most about twelve words and no symbols; it names the object, the method or the claim.
- P21 [reader, section] Earned sentences. Every sentence gives the reader a fact, a reason or a reference. A sentence
  that only restates the statement that follows, announces what comes next or says that a result is important is
  removed, not reworded.
- P22 [reader, paper] Sections. A section is a part of the paper with its own purpose and runs two to five pages; a
  step of an argument is a subsection or a paragraph.
