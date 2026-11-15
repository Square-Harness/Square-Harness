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



if __name__ == '__main__':
    unittest.main()
