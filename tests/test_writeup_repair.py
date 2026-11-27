# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""A write-up that does not compile: the harness's own fixes, then a repair of the failing passage alone."""
import json
import shutil
import unittest

try:
    from .test_gui import GuiCase, text
    from .test_writeup_conversation import PLAN
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import GuiCase, text
    from test_writeup_conversation import PLAN

FILLER = '\n\n'.join(f'Paragraph {i} recalls that $a_{{{i}}}\\le b_{{{i}}}$ for every index.' for i in range(40))


@unittest.skipIf(shutil.which('latexmk') is None, 'latexmk is not installed')
class WriteupRepairTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def run_writeup(self, replies):
        (self.root / 'notes.md').write_text('Rough notes on the half-plane.')
        self.fake.replies = [text(PLAN)] + [text(reply) for reply in replies]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        return json.loads((self.root / '.mathagent' / 'research' / started['id'] / 'state.json').read_text())

    def test_a_clashing_macro_and_a_removed_label_need_no_repair_call(self):
        state = self.run_writeup([r"""\newcommand{\H}{\mathbf{H}}
\title{Half-plane}
\section{Setting}
Let $\H$ be the upper half-plane. Then
\begin{equation}
\label{eq:unused}
x\in\H,
\end{equation}
and
\begin{align}
a &= b \\
  &= c.
\label{eq:also-unused}
\end{align}"""])
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertFalse(any(call['role'] == 'repair' for call in state['calls']))
        self.assertIn('\\renewcommand{\\H}', state['document'])  # LaTeX has \H already: the text's meaning replaces it
        self.assertNotRegex(state['document'], r'\\begin\{(equation|align)\*\}\n\s*\n')  # no empty line where a label stood
        self.assertNotRegex(state['document'], r'\n\s*\n\\end\{(equation|align)\*\}')
        self.assertTrue(any('\\H' in warning for warning in state['warnings']))

    def test_standard_notation_that_latex_may_lack_compiles_without_a_repair_call(self):
        # \P exists in LaTeX, \C only with a Cyrillic font encoding: \renewcommand alone stopped the compile on
        # \C ("Command \C undefined"), in the preamble, where no repair call can reach (report 638715b7).
        state = self.run_writeup(['\\title{Spectra}\n\\section{Setting}\nLet $z\\in\\C$ and let $\\P$ be a '
                                  'probability on $\\C$.\n\n' + FILLER])
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertFalse(any(call['role'] == 'repair' for call in state['calls']))
        self.assertIn('\\providecommand{\\C}{}\\renewcommand{\\C}{\\mathbb{C}}', state['document'])
        self.assertIn('\\providecommand{\\P}{}\\renewcommand{\\P}{\\mathbb{P}}', state['document'])

    def test_a_renewcommand_of_a_new_command_in_the_models_preamble_compiles(self):
        state = self.run_writeup(['\\documentclass{article}\n\\renewcommand{\\cK}{\\mathcal{K}}\n\\begin{document}\n'
                                  '\\title{Kernels}\n\\section{Setting}\nLet $\\cK$ be a compact set.\n\n' + FILLER
                                  + '\n\\end{document}'])
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertFalse(any(call['role'] == 'repair' for call in state['calls']))
        self.assertIn('\\providecommand{\\cK}{}\\renewcommand{\\cK}{{\\mathcal{K}}}', state['document'])

    def test_an_error_in_one_passage_is_repaired_there_and_the_rest_is_not_rewritten(self):
        broken = 'The bound is $a_1_2$ here.'
        state = self.run_writeup([f'\\title{{A note}}\n\\section{{Result}}\n{FILLER}\n\n{broken}\n\n{FILLER}',
                                  'The bound is $a_{1,2}$ here.'])
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        repairs = [r['messages'][1]['content'] for r in self.fake.requests if 'does not compile' in r['messages'][1]['content']]
        self.assertEqual(len(repairs), 1)
        self.assertIn('PASSAGE:\n' + broken, repairs[0])
        self.assertNotIn('Paragraph 3 recalls', repairs[0])  # only the failing passage went to the model
        self.assertIn('The bound is $a_{1,2}$ here.', state['document'])
        self.assertEqual(state['document'].count('Paragraph 3 recalls'), 2)

    def test_two_errors_take_two_rounds_and_an_unchanged_passage_is_no_repair(self):
        one, two = 'The bound is $a_1_2$ here.', 'The rate is $b^3^4$ there.'
        state = self.run_writeup([f'\\title{{A note}}\n\\section{{Result}}\n{one}\n\n{FILLER}\n\n{two}',
                                  'The bound is $a_{1,2}$ here.', 'The rate is $b^{12}$ there.'])
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertEqual(sum(call['role'] == 'repair' for call in state['calls']), 2)  # LaTeX stops at its first error
        self.assertEqual(state['repair_rounds'], 2)

    def test_a_text_accent_used_as_notation_gets_a_definition_and_the_text_is_not_rewritten(self):
        state = self.run_writeup(['\\title{Half-plane}\n\\section{Setting}\nLet $\\H = \\{z : \\operatorname{Im} z > 0\\}$ '
                                  'be the upper half-plane.\n\n' + FILLER, '\\newcommand{\\H}{\\mathbb{H}}'])
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertIn('\\renewcommand{\\H}{{\\mathbb{H}}}', state['document'])
        asked = [r['messages'][1]['content'] for r in self.fake.requests if 'text accents' in r['messages'][1]['content']]
        self.assertEqual(len(asked), 1)
        self.assertNotIn('Paragraph 3 recalls', asked[0])  # only the lines that use \H
        self.assertTrue(any('\\H' in warning and 'check' in warning for warning in state['warnings']))

    def test_a_finished_write_up_that_did_not_compile_is_reopened_and_repaired_not_rewritten(self):
        from mathagent.writeup import WriteupRunner
        broken = 'The bound is $a_1_2$ here.'
        # Neither repair reply fixes the error: the job ends with a document that does not compile.
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(PLAN), text(f'\\title{{A note}}\n\\section{{Result}}\n{FILLER}\n\n{broken}\n\n{FILLER}'),
                             text(broken), text('I cannot fix this.')]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        path = self.root / '.mathagent' / 'research' / started['id'] / 'state.json'
        state = json.loads(path.read_text())
        self.assertFalse(state['compile']['ok'])
        written = sum('Output the LaTeX body only' in r['messages'][1]['content'] for r in self.fake.requests)
        self.assertTrue(WriteupRunner.reopen_for_repair(self.root, started['id']))
        self.fake.replies = [text('The bound is $a_{1,2}$ here.')]
        self.ok('POST', f'/api/research/{started["id"]}/resume', {'additional_seconds': 120})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        state = json.loads(path.read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertIn('The bound is $a_{1,2}$ here.', state['document'])
        self.assertEqual(written, sum('Output the LaTeX body only' in r['messages'][1]['content'] for r in self.fake.requests))
        self.assertFalse(WriteupRunner.reopen_for_repair(self.root, started['id']))  # it compiles now: nothing to repair


    def test_broken_references_are_repaired_the_abstract_is_front_matter_and_links_are_clickable(self):
        (self.root / 'refs.bib').write_text('@article{Rannacher1984,\n  author = {Rannacher, R.},\n'
                                            '  title = {Finite element solution of diffusion problems},\n  year = {1984},\n}\n')
        draft = r"""\title{The theta-scheme}
\begin{abstract}
We prove that the theta-scheme converges for every theta between one half and one, with no condition linking the
time step and the mesh size, and we observe the predicted rates and the oscillations of Crank--Nicolson on a
discontinuous datum in numerical experiments that use the same grid and the same norm as the theorem.
\end{abstract}
\section{The $\theta$-scheme}\label{sec:scheme}
The main result is Theorem~\ref{thm:main-result}; the experiments are in Section~\ref{sec:numerical}, and the first
steps are smoothed as in \cite{Rannacher1985}.
\begin{equation}\label{eq:scheme} U^{n+1}=U^n+\Delta t\,D U^{n+1}. \end{equation}
\section{Proof}\label{sec:proof}
\begin{equation}\label{eq:scheme} U^{n+1}-U^n=\Delta t\,D U^{n+1}. \end{equation}
\begin{theorem}\label{thm:convergence} The scheme \eqref{eq:scheme} converges. \end{theorem}
\section{Numerical experiments}\label{sec:numerics}
The rates agree with Theorem~\ref{thm:convergence}."""
        resolve = json.dumps({'items': [{'n': 1, 'label': 'thm:convergence'}]})
        (self.root / 'notes.md').write_text('Rough notes on the theta-scheme.')
        self.fake.replies = [text(PLAN), text(draft), text(resolve)]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'source_files': ['notes.md'],
                                                   'template_files': ['refs.bib']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        job = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((job / 'state.json').read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        document = state['document']
        # amsart sets the abstract in the title block: before \maketitle, outside the body the review numbers.
        self.assertLess(document.index('\\begin{abstract}'), document.index('\\maketitle'))
        self.assertNotIn('\\begin{abstract}', state['body'])
        self.assertLess(document.index('\\usepackage{hyperref}'), document.index('\\newtheorem'))
        self.assertIn('\\texorpdfstring{$\\theta$}{theta}', state['body'])
        self.assertIn('Theorem~\\ref{thm:convergence}; the experiments are in Section~\\ref{sec:numerics}', state['body'])
        self.assertIn('\\cite{Rannacher1984}', state['body'])
        self.assertIn('\\label{eq:scheme-b}', state['body'])
        self.assertIn('scheme \\eqref{eq:scheme-b} converges', state['body'])  # after the second definition: its name
        self.assertEqual([c['role'] for c in state['calls']].count('resolve_refs'), 1)
        self.assertFalse(state['compile']['references']['unresolved'])
        self.assertNotIn('G2', [g['gate'] for g in state['gates']])
        self.assertEqual([w for w in state['log_warnings'] if w['kind'] in ('class', 'reference', 'citation', 'duplicate')], [])
        report = (job / 'report.md').read_text()
        self.assertIn('- References: clickable (hyperref is loaded)', report)
        self.assertIn('## Fixed automatically', report)
        self.assertIn('\\begin{abstract}', (job / 'artifacts' / 'body.tex').read_text())  # the body to paste keeps it


class RenewingTests(unittest.TestCase):
    def test_a_renewcommand_is_preceded_by_an_empty_providecommand_and_other_definitions_are_unchanged(self):
        from mathagent.writeup import renewing
        self.assertEqual(renewing('\\renewcommand{\\C}{\\mathbb{C}}'), '\\providecommand{\\C}{}\\renewcommand{\\C}{\\mathbb{C}}')
        self.assertEqual(renewing('\\renewcommand*\\abs[1]{\\lvert #1\\rvert}'),
                         '\\providecommand{\\abs}{}\\renewcommand*\\abs[1]{\\lvert #1\\rvert}')
        self.assertEqual(renewing('\\newcommand{\\R}{\\mathbb{R}}'), '\\newcommand{\\R}{\\mathbb{R}}')

    def test_reading_definitions_back_takes_the_renewcommand_not_the_empty_placeholder(self):
        # The assembled preamble is read again (an earlier write-up attached, merged or learnt as a style).
        from mathagent.style import macro_definitions
        self.assertEqual(macro_definitions('\\providecommand{\\C}{}\\renewcommand{\\C}{\\mathbb{C}}'),
                         {'C': '\\renewcommand{\\C}{\\mathbb{C}}'})


if __name__ == '__main__':
    unittest.main()
