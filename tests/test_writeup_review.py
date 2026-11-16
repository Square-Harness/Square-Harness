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
        style = json.dumps({'items': [
            # The quote is retyped, not word for word: the lines keep the finding.
            {'rule': 'N2', 'verdict': 'fail', 'severity': 'major', 'line': 6, 'last_line': 6, 'quote': 'begin theorem (thm two)',
             'problem': 'The theorem follows the lemma with no link.', 'instruction': 'Say what the lemma is for.',
             'fixable': 'rewrite'},
            {'rule': 'N4', 'verdict': 'fail', 'severity': 'minor', 'line': 0, 'last_line': 0,
             'quote': 'It follows from Lemma~\\ref{lem:one}.', 'problem': 'Vague.', 'instruction': 'Say what follows.',
             'fixable': 'rewrite'}]})
        reader = json.dumps({'summary': 'A bound.', 'items': []})
        edits = ('@@ issue 1 lines 5-6\n\\end{lemma}\nLemma~\\ref{lem:one} gives the positivity used in the next result.\n'
                 '\\begin{theorem}\\label{thm:two}\n'
                 '@@ issue 2 lines 8-12\n\\[\nx^2+2>0.\n\\]\n\\end{theorem}\nThe bound follows from Lemma~\\ref{lem:one}.\n')
        (self.root / 'notes.md').write_text('Rough notes.')
        # One round: the review (fidelity, reader, style), the writer's edits, then the judge's ruling on the edit
        # that passed the guard. Nothing is reviewed again from scratch after the revision.
        ruling = json.dumps({'items': [{'n': 1, 'verdict': 'accept', 'reason': 'The lemma is now introduced.'}]})
        # The second attempt is a rewrite with the mathematics frozen: an answer that writes a formula is refused.
        again = '@@ issue 1 lines 9-12\n\\[\nx^2+3>0.\n\\]\n\\end{theorem}\n'
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, reader, style, edits, ruling, again)]
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
        self.assertEqual(ledger['N2']['status'], 'fixed')
        self.assertEqual(ledger['N4']['status'], 'open')
        self.assertEqual(ledger['N4']['attempts'], 2)  # refused by the guard (a formula changed), then tried once more
        self.assertIn('the rewrite was refused', ledger['N4']['reasons'][-1])
        self.assertEqual([i['rule'] for i in state['reviews'][-1]['issues']], ['N4'])  # what still stands
        self.assertEqual(sum(call['role'] == 'fidelity' for call in state['calls']), 1)  # reviewed once, not twice
        self.assertEqual([call['role'] for call in state['calls'] if call['role'] in ('revise_section', 'rewrite_section')],
                         ['revise_section', 'rewrite_section'])

    def test_a_rebuttal_the_judge_upholds_closes_the_finding_and_a_rejected_one_goes_to_the_author(self):
        style = json.dumps({'items': [
            {'rule': 'N2', 'verdict': 'fail', 'severity': 'major', 'line': 0, 'last_line': 0, 'quote': 'We have $x^2\\ge 0$.',
             'problem': 'The lemma is not motivated.', 'instruction': 'Say what it is for.', 'fixable': 'rewrite'},
            {'rule': 'N4', 'verdict': 'fail', 'severity': 'major', 'line': 0, 'last_line': 0,
             'quote': 'It follows from Lemma~\\ref{lem:one}.', 'problem': 'Vague.', 'instruction': 'Say what follows.',
             'fixable': 'rewrite'},
            {'rule': 'N1', 'verdict': 'fail', 'severity': 'minor', 'line': 0, 'last_line': 0, 'quote': 'Let $x\\in\\R$',
             'problem': 'Wordy.', 'instruction': 'Shorten.', 'fixable': 'rewrite'}]})
        answers = ('@@ issue 1 rebut\nThe sentence after the theorem says what the lemma is for.\n'
                   '@@ issue 2 rebut\nThe sentence is clear.\n')  # nothing on the third: it is asked again, alone
        ruling = json.dumps({'items': [{'n': 1, 'verdict': 'accept', 'reason': 'The link exists.'},
                                       {'n': 2, 'verdict': 'reject', 'reason': 'It does not say what follows.'}]})
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, json.dumps({'summary': '', 'items': []}), style,
                                                      answers, ruling, 'Nothing to change.')]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'rounds': 2,
                                                   'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        folder = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((folder / 'state.json').read_text())
        ledger = {item['rule']: item for item in state['ledger']}
        self.assertEqual(ledger['N2']['status'], 'refuted')  # the judge agreed with the writer
        self.assertEqual(ledger['N4']['status'], 'disputed')  # neither overrules the other: it goes to the author
        self.assertEqual(ledger['N1']['reasons'][0], 'unanswered')  # asked twice, answered by nothing
        asked = [r['messages'][1]['content'] for r in self.fake.requests if 'got no answer the first time' in r['messages'][1]['content']]
        self.assertEqual(len(asked), 1)
        self.assertIn('[N1]', asked[0])
        self.assertNotIn('[N4]', asked[0])
        self.assertEqual(state['readiness'], 'draft')
        self.assertEqual(sorted(i['rule'] for i in state['reviews'][-1]['issues']), ['N1', 'N4'])
        report = (folder / 'report.md').read_text()
        self.assertIn('The sentence is clear.', report)
        self.assertIn('### For you to settle (1)', report)
        self.assertIn('| N2 | style | style reviewer | followed', report)  # set aside: nothing stands
        self.assertIn('| N4 | style | style reviewer | 1 open (author) |', report)
        self.assertIn('x^2+1>0', state['document'])  # no edit: the text is unchanged

    def test_a_finding_about_the_flow_is_fixed_by_a_rewrite_with_the_mathematics_frozen(self):
        reader = json.dumps({'summary': 'A bound.', 'items': [
            {'rule': 'N7', 'severity': 'major', 'line': 6, 'last_line': 6, 'quote': 'begin theorem (thm two)',
             'problem': 'The theorem follows the lemma with no link.', 'instruction': 'Say what the lemma is for.',
             'fixable': 'rewrite'}]})
        rewrite = ('[[H1]]\nLet [[M1]] and [[M2]].\n[[S1]]\nThe lemma gives the positivity used below.\n[[S2]]\n'
                   'It follows from Lemma~\\ref{lem:one}.\n')
        ruling = json.dumps({'section': {'verdict': 'better', 'reason': 'Linked.'},
                             'items': [{'n': 1, 'verdict': 'partial', 'reason': 'Better.', 'remains': 'Say which bound is used.'}]})
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(reply) for reply in (PLAN, DRAFT, NOTHING, reader, PASS, rewrite, ruling)]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'rounds': 2,
                                                   'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        folder = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((folder / 'state.json').read_text())
        asked = next(r['messages'][1]['content'] for r in self.fake.requests if 'SECTION TO REWRITE' in r['messages'][1]['content'])
        self.assertIn('[[S2]] statement: \\begin{theorem}\\label{thm:two} We have', asked)  # the legend
        self.assertNotIn('x^2+1>0.\n', asked.split('SECTION TO REWRITE')[1])  # the mathematics is frozen
        self.assertIn('The lemma gives the positivity used below.', state['body'])
        self.assertIn('\\begin{theorem}\\label{thm:two}\nWe have\n\\[\nx^2+1>0.\n\\]\n\\end{theorem}', state['body'])
        item = state['ledger'][0]
        # Partly fixed: the text was kept, the finding stays open with what remains; the second rewrite (no answer) is refused.
        self.assertEqual((item['status'], item['attempts'], item['problem']), ('open', 2, 'Say which bound is used.'))
        self.assertEqual(state['rewrites'], {'proposed': 2, 'guard': 1, 'judge': 0, 'applied': 1})
        report = (folder / 'report.md').read_text()
        self.assertIn('### Not fixed after two attempts (1)', report)
        self.assertIn('| N7 | reader | reader | 1 open (reviser) |', report)

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

    def test_a_rewrite_that_loses_an_environment_or_doubles_a_label_is_refused(self):
        frozen = '[[H1]]\nIntro.\n\\begin{proof}\nBy [[M1]].\n\\end{proof}\n'
        legend = {'[[H1]]': {'kind': 'H', 'text': '\\section{A}'}, '[[M1]]': {'kind': 'M', 'text': '$x$'}}
        self.assertEqual(writeup.frozen_guard(frozen, frozen.replace('Intro.', 'Intro, then.'), legend), '')
        self.assertIn('environment', writeup.frozen_guard(frozen, frozen.replace('\\end{proof}\n', ''), legend))
        self.assertEqual(writeup.doubled_labels('\\begin{equation}\\label{a} x \\end{equation}'), 0)
        self.assertEqual(writeup.doubled_labels('\\begin{equation}\\label{a}\\label{b} x \\end{equation}'), 1)
        self.assertEqual(writeup.doubled_labels('\\begin{align}x\\label{a}\\\\ y\\label{b}\\end{align}'), 0)
        self.assertEqual(writeup.doubled_labels('\\begin{lemma}\\label{l} \\begin{equation}\\label{e} x\\end{equation}\\end{lemma}'), 0)

    def test_the_skeleton_fixes_the_labels_every_call_reads(self):
        plan = json.loads(PLAN)
        plan.update(kind='article', size='section', sections=[
            {'heading': 'Stability', 'role': 'body', 'source': 'material', 'covers': ['S1'], 'parts': [], 'requested_by': '',
             'purpose': 'The bound.', 'thread': []},
            {'heading': 'Convergence', 'role': 'body', 'source': 'material', 'covers': ['S2'], 'parts': [], 'requested_by': '',
             'purpose': 'The theorem.', 'thread': []}])
        skeleton = json.dumps({'sections': [{'heading': 'Stability', 'label': 'sec:stability', 'opens': 'the scheme', 'closes': 'the bound'},
                                            {'heading': 'Convergence', 'label': 'sec:convergence', 'opens': 'the bound', 'closes': ''}],
                               'items': [{'id': 'S1', 'environment': 'lemma', 'label': 'lem:stability', 'name': 'stability of the step',
                                          'section': 'sec:stability', 'role': 'auxiliary', 'uses': []},
                                         {'id': 'S2', 'environment': 'theorem', 'label': 'thm:convergence', 'name': 'convergence',
                                          'section': 'sec:convergence', 'role': 'main', 'uses': ['S1']}],
                               'equations': [], 'floats': [], 'aliases': []})
        draft = r"""\title{A note}
\section{Stability}\label{sec:stab}
\begin{lemma} The step is stable. \end{lemma}
\section{Convergence}
\begin{theorem}\label{thm:convergence} The scheme converges. \end{theorem}
\begin{proof} Sum the errors. \end{proof}
Section~\ref{sec:stab} gives the bound."""
        (self.root / 'notes.md').write_text('Rough notes.')
        self.fake.replies = [text(reply) for reply in (json.dumps(plan), skeleton, draft)]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'rounds': 1, 'source_files': ['notes.md']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        job = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((job / 'state.json').read_text())
        self.assertEqual([c['role'] for c in state['calls']][:3], ['plan', 'skeleton', 'writing'])
        writing = next(r['messages'][1]['content'] for r in self.fake.requests if 'Output the LaTeX body only' in r['messages'][1]['content'])
        self.assertIn('SKELETON OF THE WHOLE DOCUMENT', writing)
        self.assertIn('Theorem "convergence" [thm:convergence], main; uses lem:stability', writing)
        self.assertNotIn('Before each result, say why it comes', writing)
        body = state['body']
        self.assertIn('\\section{Stability}\\label{sec:stability}', body)  # the writer's label takes the skeleton's
        self.assertIn('Section~\\ref{sec:stability} gives', body)  # and its references follow
        self.assertIn('\\begin{lemma}\\label{lem:stability}', body)  # the only lemma of its section
        self.assertIn('\\section{Convergence}\\label{sec:convergence}', body)
        rules = {f['rule'] for f in state['checks']}
        self.assertIn('skeleton-link', rules)  # the proof of the theorem does not cite the lemma it uses
        self.assertIn('G6', [g['gate'] for g in state['gates']])
        self.assertIn('## Skeleton', (job / 'report.md').read_text())
        self.assertTrue(any((job / 'artifacts').glob('*skeleton*')))


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

    def test_frozen_text_keeps_the_mathematics_of_a_rewrite(self):
        text = ('\\section{Stability}\\label{sec:stab}\nLet $x>0$ and $y$. Then\n\\[\na=b\n\\]\n'
                '\\begin{lemma}\\label{lem:a} For $x>0$, it holds. \\end{lemma}\n% TODO: check\nBy Lemma~\\ref{lem:a} and $x>0$ we conclude.\n')
        frozen, legend = writeup.freeze(text)
        self.assertEqual(frozen, '[[H1]]\nLet [[M1]] and [[M2]]. Then\n[[D1]]\n[[S1]]\n[[C1]]\nBy Lemma~\\ref{lem:a} and [[M1]] we conclude.\n')
        self.assertEqual(writeup.thaw(frozen, legend), text)
        guard = lambda after, rules=(): writeup.frozen_guard(frozen, after, legend, rules, {'lem:a', 'sec:stab'})
        better = frozen.replace('Then', 'Hence, by the definition,')
        self.assertEqual(guard(better), '')
        self.assertIn('moved, lost or repeated', guard(better.replace('[[D1]]\n[[S1]]', '[[S1]]\n[[D1]]')))
        self.assertIn('outside the frozen', guard(better + ' Since $z>0$.'))
        self.assertIn('inline formula', guard(better.replace(' and [[M2]]', '')))
        self.assertEqual(guard(better.replace(' and [[M2]]', ''), ['P21']), '')  # a sentence deleted on purpose
        self.assertIn('does not exist', guard(better + ' See Theorem~\\ref{thm:x}.'))
        self.assertIn('does not exist', guard(better + ' [[D7]]'))
        self.assertIn('TODO', guard(better + '% TODO: more\n'))
        numbered, _ = writeup.numbered_frozen(text)
        self.assertIn('6| [[S1]]', numbered)  # the line numbers stay those of the text
        self.assertIn('8| By Lemma', numbered)

    def test_texts_are_spliced_by_code_and_a_change_of_notation_is_guarded(self):
        bodies, definitions, entries, changes = writeup.splice_texts([
            ('a.tex', '\\section{A}\\label{sec:a}\n\\begin{equation}\\label{eq:x} x=1 \\end{equation}\nBy \\eqref{eq:x}.\n'
                      '@article{K1, title = {On schemes}, doi = {10.1/abc}}\n\\cite{K1}', '\\newcommand{\\T}{\\mathcal{T}}'),
            ('b.tex', '\\section{B}\\label{sec:b}\n\\begin{equation}\\label{eq:x} x=1 \\end{equation}\nBy \\eqref{eq:x} and $\\T$.\n'
                      '@article{K2, title = {On Schemes}, doi = {10.1/ABC}}\n\\cite{K2}', '\\newcommand{\\T}{\\mathbb{T}}')])
        self.assertIn('\\label{eq:x-b}', bodies[1])
        self.assertIn('By \\eqref{eq:x-b} and $\\TB$', bodies[1])  # its own references follow; the other \\T is \\TB
        self.assertIn('By \\eqref{eq:x}.', bodies[0])
        self.assertEqual(definitions, ['\\newcommand{\\T}{\\mathcal{T}}', '\\newcommand{\\TB}{\\mathbb{T}}'])
        self.assertIn('\\cite{K1}', bodies[1])  # one work under two keys takes the first
        self.assertEqual([key for key, _ in entries], ['K1'])
        self.assertEqual(len(changes['b.tex']), 3)
        table = [('h', '\\dx')]
        self.assertEqual(writeup.notation_guard('Let $h>0$ and $u_h$.', 'Let $\\dx>0$ and $u_\\dx$.', table), '')
        self.assertIn('beyond', writeup.notation_guard('Let $h>0$.', 'Let $\\dx>1$.', table))
        self.assertIn('around', writeup.notation_guard('Let $h>0$.', 'Take $\\dx>0$.', table))
        pairs = writeup.duplicate_blocks(['\\begin{theorem} The scheme converges for every step. \\end{theorem}',
                                          'Text.\n\\begin{theorem}\\label{thm:b} The scheme converges for every step. \\end{theorem}'])
        self.assertEqual(pairs, [(0, '[[S1]]', 1, '[[S1]]')])

    def test_a_generated_passage_is_frozen_like_any_text_between_its_markers(self):
        text = ('\\section{Discussion}\n% BEGIN COMPOSED\nThe theorem needs $\\theta\\ge 1/2$.\n\n'
                'Its constant is explicit:\n\\[ C=C_1. \\]\n% TODO: check the constant\n% END COMPOSED\n')
        frozen, legend = writeup.freeze(text)
        self.assertEqual(frozen, '[[H1]]\n[[C1]]\nThe theorem needs [[M1]].\n\nIts constant is explicit:\n[[D1]]\n[[C2]]\n[[C3]]\n')
        self.assertEqual(writeup.thaw(frozen, legend), text)
        guard = lambda after: writeup.frozen_guard(frozen, after, legend)
        self.assertEqual(guard(frozen.replace('needs', 'requires')), '')  # its sentences can be rewritten
        self.assertIn('moved', guard(frozen.replace('[[C1]]\n', '').replace('[[C3]]', '[[C3]]\n[[C1]]')))  # the markers stay paired

    def test_the_skeleton_is_settled_by_the_controller(self):
        outline = {'sections': [{'heading': 'Introduction', 'role': 'introduction', 'covers': []},
                                {'heading': 'Stability', 'role': 'body', 'covers': ['S1']},
                                {'heading': 'Convergence', 'role': 'body', 'covers': ['S2']}]}
        raw = {'sections': [{'heading': 'Introduction', 'label': 'sec:intro', 'opens': '', 'closes': ''},
                            {'heading': 'Stability', 'label': 'Stabilité, uniforme!', 'opens': 'the scheme', 'closes': ''},
                            {'heading': 'Convergence', 'label': 'sec:conv', 'opens': '', 'closes': ''}],
               'items': [{'id': 'S1', 'environment': 'lemma', 'label': 'lem:stab', 'name': 'stability', 'section': 'sec:intro',
                          'role': 'auxiliary', 'uses': ['S2', 'S1', 'X9']},
                         {'id': 'S2', 'environment': 'theorem', 'label': 'thm:main', 'name': 'convergence', 'section': 'sec:conv',
                          'role': 'main', 'uses': ['S1']}],
               'equations': [], 'floats': [],
               'aliases': [{'from': 'sec:numerical', 'to': 'sec:conv'}, {'from': 'sec:other', 'to': 'sec:missing'}]}
        skeleton = writeup.settle_skeleton(raw, outline, {'thm:main'}, ['sec:numerical', 'sec:other'])
        self.assertEqual([s['label'] for s in skeleton['sections']], ['sec:intro', 'sec:stabilite-uniforme', 'sec:conv'])
        items = {i['id']: i for i in skeleton['items']}
        self.assertEqual(items['S1']['section'], 'sec:stabilite-uniforme')  # the outline's covers win
        self.assertEqual(items['S1']['uses'], [])  # no self-reference, no unknown id, and the cycle's forward edge is dropped
        self.assertEqual((items['S2']['uses'], items['S1']['used_by']), (['S1'], ['S2']))
        self.assertEqual(skeleton['aliases'], [{'from': 'sec:numerical', 'to': 'sec:conv'}])
        fallback = writeup.settle_skeleton(None, outline)
        self.assertEqual([s['label'] for s in fallback['sections']], ['sec:introduction', 'sec:stability', 'sec:convergence'])
        self.assertTrue(fallback['warning'] and not fallback['items'])

    def test_the_text_is_checked_against_the_skeleton(self):
        skeleton = {'sections': [{'heading': 'Introduction', 'label': 'sec:intro', 'role': 'introduction'},
                                 {'heading': 'Stability', 'label': 'sec:stab', 'role': 'body'},
                                 {'heading': 'Convergence', 'label': 'sec:conv', 'role': 'body'}],
                    'items': [{'id': 'S1', 'environment': 'lemma', 'label': 'lem:stab', 'name': 'stability', 'section': 'sec:stab',
                               'role': 'auxiliary', 'uses': [], 'used_by': ['S2']},
                              {'id': 'S2', 'environment': 'theorem', 'label': 'thm:main', 'name': 'convergence',
                               'section': 'sec:conv', 'role': 'main', 'uses': ['S1'], 'used_by': []}],
                    'equations': [{'label': 'eq:scheme', 'what': 'the scheme', 'section': 'sec:stab'}], 'floats': []}
        body = (r"\section{Introduction}\label{sec:intro}" "\nOur main result is Theorem~\\ref{thm:main}.\n"
                r"\section{Stability}\label{sec:stab}" "\nBy Theorem~\\ref{thm:main} the step is stable.\n"
                r"\begin{lemma}\label{lem:stab} Stable. \end{lemma}" "\n" r"\begin{equation}\label{eq:scheme} U=V \end{equation}" "\n"
                r"\section{Convergence}\label{sec:conv}" "\n" r"\begin{theorem}\label{thm:main} It converges. \end{theorem}" "\n"
                r"\begin{proof} Sum the errors. \end{proof}")
        found = {rule: line for rule, _, _, _, line in writeup.skeleton_findings(body, skeleton, ['sec:intro'])}
        self.assertEqual(found['skeleton-link'], 8)  # the theorem's proof does not cite the lemma
        self.assertEqual(found['skeleton-order'], 4)  # cited in Stability before Convergence states it; the introduction may
        self.assertIn('skeleton-orphan', found)
        self.assertIn('skeleton-equation', found)
        self.assertNotIn('skeleton-label', found)
        fixed = body.replace('Sum the errors.', 'By Lemma~\\ref{lem:stab}, sum the errors \\eqref{eq:scheme}.')
        fixed = fixed.replace('By Theorem~\\ref{thm:main} the', 'The')
        self.assertEqual(writeup.skeleton_findings(fixed, skeleton, ['sec:intro']), [])
        missing = writeup.skeleton_findings(body.replace('\\label{lem:stab}', ''), skeleton)
        # The frame: the introduction cites the main result and, from three body sections on, every section.
        skeleton['sections'].append({'heading': 'Numerics', 'label': 'sec:num', 'role': 'body'})
        self.assertEqual(len(writeup.frame_findings('We prove Theorem~\\ref{thm:main}. Experiments follow.', skeleton)), 2)
        self.assertEqual(writeup.frame_findings('We prove Theorem~\\ref{thm:main}; Sections~\\ref{sec:stab}, '
                                                '\\ref{sec:conv} and \\ref{sec:num} give the proof and the experiments.',
                                                skeleton), [])
        outline = writeup.text_outline('\\section{A}\\label{sec:a}\nFirst.\n\nMiddle.\n\n\\begin{lemma}\\label{lem:x} L. '
                                       '\\end{lemma}\n\nMore.\n\nLast.')
        self.assertEqual(outline, '\\section{A}\\label{sec:a}\nFirst.\n\n\\begin{lemma}\\label{lem:x} L. \\end{lemma}'
                                  '\n\n[...]\n\nLast.')
        self.assertIn(('skeleton-label', 'blocker'), [(f[0], f[1]) for f in missing])

if __name__ == '__main__':
    unittest.main()
