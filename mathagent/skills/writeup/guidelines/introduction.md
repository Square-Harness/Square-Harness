# Style guidelines: introduction of a paper

An introduction is one section. Its parts are paragraphs, in this order; the main results may be set apart with
`\subsection*`. It is written from the paper's results and from the bibliography records, and an article's
introduction usually takes one and a half to three pages.

- I1 [required] Opening. The first two sentences name the mathematical object and the precise question, and the first
  paragraph displays the inequality or equation at stake. It starts with the mathematics: no sentence about the
  importance of the field, no announcement ("We begin with").
- I2 [required] Literature. What is already known is described with citations, organised by what was proved (the
  result, its hypotheses, the method), not as a list of names: the work that leads to the question before the main
  result, the comparison with the closest work after it. Every statement about earlier work has a citation from
  the bibliography records; nothing is attributed from memory, and nothing is said about a work beyond what the
  material says of it (a title alone supports no claim). Authors are named as the record gives them: all of them, or
  the first "et al." for more than three. A work that does not bear on the question is not cited.
- I3 [required] Positioning. The text says what is new compared with the closest cited work: which gap the paper fills,
  and what was out of reach of the earlier methods.
- I4 [required] Main result. The main result is stated in the introduction as a numbered theorem with all its
  hypotheses (or informally with a reference to the precise statement), before the method. Next to it, one or two
  sentences say it in words. It is followed by comments: what each hypothesis is for, whether the constant or the
  exponent is sharp, immediate consequences. Every term of the statement is defined, explained or standard.
- I5 [required] Method. One or two paragraphs give the strategy of the proof, step by step; they name the main
  obstacle and the idea that overcomes it, and say which step is new.
- I6 [style] Organisation. When the material gives the sections of the paper, the introduction ends with its plan,
  section by section, with `\ref`. When it does not, or when the paper is short, there is no plan: none is made up.
- I7 [style] Restraint. No proof in the introduction, only the notation needed to state the results, and at most about
  four displayed statements.
- I8 [reader] One section. The introduction is a single `\section`; its parts are paragraphs, never sections of their own.
- I9 [reader] Substance. Each part says something specific to this paper. A part made of one or two general sentences
  ("stability estimates of this type are known in the literature") is missing its content.
- I10 [reader] Something concrete. When the material has an example, a special case or a figure that shows what the
  result says, the introduction gives it.
- I11 [style] Contributions are written as prose, not as a bulleted list.
