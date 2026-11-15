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
        # One round: the review (fidelity, reader, style), the writer's edits, then the judge's ruling on the edit
        # that passed the guard. Nothing is reviewed again from scratch after the revision.
        ruling = json.dumps({'items': [{'n': 1, 'verdict': 'accept', 'reason': 'The lemma is now introduced.'}]})
        again = '@@ issue 1 lines 9-12\n\\[\nx^2+3>0.\n\\]\n\\end{theorem}\n'  # the second attempt changes the formula too
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, reader, PASS, edits, ruling, again)]
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
        self.assertNotIn('x^2+3>0', document)
        self.assertEqual(state['reviews'][0]['dropped'], 0)
        ledger = {item['rule']: item for item in state['ledger']}
        self.assertEqual(ledger['N7']['status'], 'fixed')
        self.assertEqual(ledger['N9']['status'], 'open')
        self.assertEqual(ledger['N9']['attempts'], 2)  # refused by the guard (a formula changed), then tried once more
        self.assertEqual([i['rule'] for i in state['reviews'][-1]['issues']], ['N9'])  # what still stands
        self.assertEqual(sum(call['role'] == 'fidelity' for call in state['calls']), 1)  # reviewed once, not twice
        self.assertEqual(sum(call['role'] == 'revise_section' for call in state['calls']), 2)

    def test_a_rebuttal_the_judge_upholds_closes_the_finding_and_a_rejected_one_goes_to_the_author(self):
        reader = json.dumps({'summary': 'A bound.', 'items': [
            {'rule': 'N7', 'severity': 'major', 'line': 0, 'last_line': 0, 'quote': 'We have $x^2\\ge 0$.',
             'problem': 'The lemma is not motivated.', 'instruction': 'Say what it is for.', 'fixable': 'rewrite'},
            {'rule': 'N9', 'severity': 'major', 'line': 0, 'last_line': 0, 'quote': 'It follows from Lemma~\\ref{lem:one}.',
             'problem': 'Vague.', 'instruction': 'Say what follows.', 'fixable': 'rewrite'}]})
        answers = ('@@ issue 1 rebut\nThe sentence after the theorem says what the lemma is for.\n'
                   '@@ issue 2 rebut\nThe sentence is clear.\n')
        ruling = json.dumps({'items': [{'n': 1, 'verdict': 'accept', 'reason': 'The link exists.'},
                                       {'n': 2, 'verdict': 'reject', 'reason': 'It does not say what follows.'}]})
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, reader, PASS, answers, ruling)]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'rounds': 2,
                                                   'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        folder = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((folder / 'state.json').read_text())
        ledger = {item['rule']: item for item in state['ledger']}
        self.assertEqual(ledger['N7']['status'], 'refuted')  # the judge agreed with the writer
        self.assertEqual(ledger['N9']['status'], 'disputed')  # neither overrules the other: it goes to the author
        self.assertEqual(state['readiness'], 'draft')
        self.assertEqual([i['rule'] for i in state['reviews'][-1]['issues']], ['N9'])
        self.assertIn('The sentence is clear.', (folder / 'report.md').read_text())
        self.assertIn('x^2+1>0', state['document'])  # no edit: the text is unchanged


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

    def test_every_warning_of_the_log_is_classified(self):
        from mathagent import texcheck
        log = ("Class amsart Warning: Abstract should precede \\maketitle in AMS document classes; reported on input line 31.\n"
               "LaTeX Warning: Reference `sec:numerics' on page 2 undefined on input line 85.\n"
               "LaTeX Warning: Reference `sec:numerics' on page 2 undefined on input line 87.\n"
               "Package hyperref Warning: Token not allowed in a PDF string (Unicode):\n"
               "(hyperref)                removing `math shift' on input line 40.\n"
               "LaTeX Warning: `h' float specifier changed to `ht'.\n"
               "Overfull \\hbox (3.1pt too wide) in paragraph at lines 50--52\n"
               "Overfull \\hbox (12.0pt too wide) in paragraph at lines 60--61\n"
               "LaTeX Warning: There were undefined references.\n")
        found = {(w['kind'], w['key']): w for w in texcheck.log_warnings(log, body_line=40)}
        self.assertEqual(found[('class', 'amsart Abstract should precede \\maketitle in AMS document classes')]['action'], 'hold')
        self.assertEqual((found[('reference', 'sec:numerics')]['count'], found[('reference', 'sec:numerics')]['action']), (2, 'fix'))
        self.assertEqual(found[('pdfstring', 'Token not allowed in a PDF string')]['line'], 40)  # read on its second line
        self.assertEqual(found[('float', 'h')]['action'], 'fix')
        self.assertEqual(found[('overfull', '60')]['action'], 'fix')
        self.assertEqual([w['kind'] for w in found.values()].count('layout'), 1)  # 3pt is only listed
        self.assertNotIn('summary', [w['kind'] for w in found.values()])

    def test_labels_references_and_titles_are_repaired_by_code(self):
        body = (r"\section{Bounds in $L^{2}$}\label{sec:bounds}" "\n" r"See Section~\ref{sec:bound} and \eqref{eq:a}." "\n"
                r"\begin{equation}\label{eq:a} x=1 \end{equation}" "\n" r"\begin{equation}\label{eq:a} y=2 \end{equation}" "\n"
                r"By \eqref{eq:a} and \cite{Rannacher1985}. % \label{eq:a}")
        renamed, pairs = writeup.rename_duplicates(body)
        self.assertEqual(pairs, [('eq:a', 'eq:a-b')])
        self.assertIn(r'and \eqref{eq:a}.', renamed)  # before the second definition: the first
        self.assertIn(r'By \eqref{eq:a-b}', renamed)  # after it: the second
        self.assertIn(r'% \label{eq:a}', renamed)  # comments are left alone
        self.assertEqual(writeup.repoint(body, ['sec:bound'], ['sec:bounds', 'eq:a'])[1], {'sec:bound': 'sec:bounds'})
        self.assertEqual(writeup.repoint(body, ['sec:x'], ['sec:bounds'])[1], {})
        self.assertIn(r'\texorpdfstring{$L^{2}$}{L2}', writeup.pdf_titles(body))
        self.assertIn(r'\cite{Rannacher1984}', writeup.recite(body, {'Rannacher1985': 'Rannacher1984'}))
        self.assertEqual(writeup.cut_abstract('\\begin{abstract}\nWe prove it.\n\\end{abstract}\n\n\\section{A}x'),
                         ('We prove it.', '\\section{A}x'))
        figure = '\\begin{figure}[h]\n\\includegraphics[width=3cm]{f}\n\\end{figure}'
        self.assertIn('[htbp]', writeup.float_placements(figure))
        self.assertIn('[width=3cm,height=0.8\\textheight,keepaspectratio]', writeup.fit_figure(figure, 3))
        self.assertEqual(writeup.label_targets(body)['sec:bounds'][0], 'section')

if __name__ == '__main__':
    unittest.main()
