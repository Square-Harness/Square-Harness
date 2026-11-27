# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""A rejected Write-Up revision leaves the last compilable document available."""
import json
import shutil
import unittest

from test_gui import GuiCase, text


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

    def test_a_revision_that_does_not_compile_is_undone_and_the_next_round_runs(self):
        reader = json.dumps({'summary': 'A bound.', 'items': [
            {'rule': 'N7', 'severity': 'major', 'line': 6, 'last_line': 6, 'quote': 'begin theorem (thm two)',
             'problem': 'The theorem follows the lemma with no link.', 'instruction': 'Say what the lemma is for.',
             'fixable': 'rewrite'}]})
        broken = ('[[H1]]\nLet [[M1]] and [[M2]].\n[[S1]]\nThe lemma \\pfoo is used below.\n[[S2]]\n'
                  'It follows from Lemma~\\ref{lem:one}.\n')
        ruling = json.dumps({'section': {'verdict': 'better', 'reason': 'Linked.'},
                             'items': [{'n': 1, 'verdict': 'accept', 'reason': 'Fixed.', 'remains': ''}]})
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, reader, broken, ruling, PASS)]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'rounds': 3,
                                                   'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        folder = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((folder / 'state.json').read_text())
        roles = [c['role'] for c in state['calls']]
        self.assertNotIn('repair', roles)  # a revision is undone, not repaired by the model
        self.assertIn('style_check', roles[roles.index('judge_section'):])  # the next round's focus ran
        self.assertTrue(state['compile']['ok'])
        self.assertNotIn("pfoo", state["document"])
        item = state['ledger'][0]
        self.assertEqual(item['status'], 'open')
        self.assertTrue(item['reasons'][0].startswith('its rewrite did not compile'))
        report = (folder / 'report.md').read_text()
        self.assertIn('did not compile', report)
        self.assertNotIn('### Fixed', report)  # nothing the delivered text does not have
