# Style guidelines: numerical results and their commentary

- M1 [style] Setting. Before any result, the text gives what was computed: the problem, the method or scheme, and the
  parameters (mesh sizes, time step, tolerances, number of samples, seed), as far as the material gives them, so that
  the experiment can be run again. An algorithm is described before the results that use it.
- M2 [style] Metric first. The quantity that is measured (which error, in which norm, against which reference) is
  defined before the numbers are discussed.
- M3 [style] Reading. Each table or figure is cited by `\ref` near where it stands, and the text says what the reader
  should see in it (the order of convergence, the regime where the method fails, the comparison between two methods),
  without listing its values again.
- M4 [style] Captions. A caption says what is shown and with which parameters, and can be read without the text.
- M5 [style] Evidence is not proof. A numerical observation is written as one ("the errors decrease at the rate",
  "the experiments suggest"), never as an established fact; a rate is given with the data it is fitted on. An agreement
  or a disagreement with the theory is said plainly, and so is anything unexpected.
- M6 [style] Numbers. Every number in the text is in the material, with the same digits; nothing is rounded silently
  and no value is extrapolated.
- M7 [style] Conclusion. Each experiment ends with one sentence saying what it establishes and within which limits
  (the range of parameters tested).
