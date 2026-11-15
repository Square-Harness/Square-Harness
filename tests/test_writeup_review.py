# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The review of a write-up: findings kept by their lines, revisions as small edits guarded one by one, the standard
notation, and the outline of an introduction."""
import json
import shutil
import unittest

try:
    from .test_gui import GuiCase, text
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import GuiCase, text

from mathagent import writeup

PLAN = json.dumps({'kind': 'notes', 'scope': 'document', 'language': 'English', 'title': 'Notes', 'short_title': 'Notes',
                   'instruction': 'A short note.', 'requirements': [], 'code': False, 'storyline': 'A bound, then its use.',
                   'inventory': [], 'problems': [], 'notation': [], 'sections': [], 'questions': [], 'recalled': [],
                   'needs': []})
DRAFT = r"""\title{A note}
\section{Result}
Let $x\in\mathbb{R}$ and $n\in\mathbb N$.
\begin{lemma}\label{lem:one}
We have $x^2\ge 0$.
\end{lemma}
\begin{theorem}\label{thm:two}
We have
\[
x^2+1>0.
\]
\end{theorem}
It follows from Lemma~\ref{lem:one}.
"""
NOTHING = json.dumps({'issues': []})
PASS = json.dumps({'items': []})


@unittest.skipIf(shutil.which('latexmk') is None, 'latexmk is not installed')
class WriteupReviewTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def test_edits_are_applied_one_by_one_and_the_standard_notation_is_used(self):
        reader = json.dumps({'summary': 'A bound.', 'items': [
            # The quote is retyped, not word for word: the lines keep the finding.
            {'rule': 'N7', 'severity': 'major', 'line': 6, 'last_line': 6, 'quote': 'begin theorem (thm two)',
             'problem': 'The theorem follows the lemma with no link.', 'instruction': 'Say what the lemma is for.',
             'fixable': 'rewrite'},
            {'rule': 'N9', 'severity': 'minor', 'line': 0, 'last_line': 0, 'quote': 'It follows from Lemma~\\ref{lem:one}.',
             'problem': 'Vague.', 'instruction': 'Say what follows.', 'fixable': 'rewrite'}]})
        edits = ('@@ issue 1 lines 5-6\n\\end{lemma}\nLemma~\\ref{lem:one} gives the positivity used in the next result.\n'
                 '\\begin{theorem}\\label{thm:two}\n'
                 '@@ issue 2 lines 8-12\n\\[\nx^2+2>0.\n\\]\n\\end{theorem}\nThe bound follows from Lemma~\\ref{lem:one}.\n')
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, reader, PASS, edits, NOTHING, PASS, PASS)]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'rounds': 2,
                                                   'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        state = json.loads((self.root / '.mathagent' / 'research' / started['id'] / 'state.json').read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        document = state['document']
        self.assertIn('Let $x\\in\\R$ and $n\\in\\N$.', document)  # one way to write the reals
        self.assertIn('\\newcommand{\\R}{\\mathbb{R}}', document)
        self.assertIn('gives the positivity used in the next result', document)  # the first edit is in
        self.assertIn('x^2+1>0', document)  # the second changed a formula no finding was about: refused alone
        self.assertNotIn('x^2+2>0', document)
        first = state['reviews'][0]
        self.assertEqual(first['dropped'], 0)
        refused = [i for i in first['issues'] if i.get('revision')]
        self.assertEqual([i['rule'] for i in refused], ['N9'])
        self.assertIn('a formula changed', refused[0]['revision'])


class WriteupPlanTests(unittest.TestCase):
    def test_a_display_set_again_keeps_its_mathematics_and_a_new_relation_does_not(self):
        self.assertTrue(writeup.same_mathematics(r'\[a=b+c\le d.\]', r'\begin{align}a&=b+c\label{eq:x}\\&\le d.\end{align}'))
        self.assertFalse(writeup.same_mathematics(r'\[a=b\]', r'\[a=b+1\]'))

    def test_raw_blackboard_letters_become_the_standard_commands_outside_comments(self):
        self.assertEqual(writeup.standardised(r'$\mathbb{R}^n$, $\mathbb Zx$, $\mathbbm{1}_A$ % \mathbb{R}', {'R', 'Z', 'ind'}),
                         r'$\R^n$, $\Z x$, $\ind_A$ % \mathbb{R}')

    def test_the_sheets_have_rules_for_the_reader_of_the_whole_document(self):
        for kind in ('article', 'notes', 'exam', 'lecture_notes'):
            self.assertTrue([r for r in writeup.load_guidelines(kind) if r['type'] == 'reader'], kind)
        introduction = {r['id'] for r in writeup.load_guidelines('paper_part', {'introduction'})}
        self.assertTrue({'I1', 'I8', 'P11'} <= introduction and 'P13' not in introduction)
        self.assertFalse([r for r in writeup.load_guidelines('exercise_sheet') if r['id'] == 'P11'])

    def test_a_short_write_up_follows_the_core_rules_only(self):
        short = {r['id'] for r in writeup.load_guidelines('paper_part', size='short')}
        section = {r['id'] for r in writeup.load_guidelines('paper_part', size='section')}
        paper = {r['id'] for r in writeup.load_guidelines('article', size='paper')}
        self.assertTrue({'P1', 'P3', 'P16'} <= short)
        self.assertFalse(short & {'P11', 'P13', 'P18', 'P20'})  # nothing about threads, openings, titles
        self.assertFalse([r for r in writeup.load_guidelines('paper_part', size='short') if r['type'] != 'style'])
        self.assertTrue('P11' in section and 'P18' not in section and {'P11', 'P18', 'P20'} <= paper)
        self.assertIn('M2', {r['id'] for r in writeup.load_guidelines('notes', size='short', numerics=True)})
        self.assertIn('A1', {r['id'] for r in writeup.load_guidelines('article', {'abstract'})})

if __name__ == '__main__':
    unittest.main()
