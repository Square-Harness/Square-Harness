# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The typo check in three lanes (rules, language, mathematics), without a model: every call is answered by a script."""
import tempfile
import unittest
from pathlib import Path

from mathagent import typocheck as T
from mathagent import typolint
from mathagent.agent import Agent
from mathagent.review import TypoCheckRunner
from mathagent.tools import Workspace
from tests.test_research import FakeClient


def masked(line):
    return typolint._Source(['\\begin{document}', line, '\\end{document}']).prose.split('\n')[1]


def edits(old, new):
    return T.line_edits(7, old, new, masked(old))


class Helpers(unittest.TestCase):
    def test_units_end_at_a_sentence_and_skip_comments(self):
        lines = ['Some words here'] * 8 + ['End of a sentence.'] + ['% a comment'] * 5 + ['Last words.']
        self.assertEqual(T.units(lines, 6, 1, len(lines)), [(1, 9), (10, 15)])
        self.assertEqual(T.units(['% only', '% comments'], 6, 1, 2), [])
        text = T.numbered(['a', 'b', 'c', 'd'], 2, 3)
        self.assertIn('--- BEGIN (proofread from here) ---\n2| b\n3| c\n--- END ---', text)

    def test_neighbouring_edits_are_one_edit(self):
        found = edits('This paper aims at proving the claim.', 'This paper aims to prove the claim.')
        self.assertEqual([(e['old'], e['new']) for e in found], [('at proving', 'to prove')])
        self.assertEqual(found[0]['start_line'], 7)
        self.assertIn('aims to prove', found[0]['suggestion'])
        # Far apart: two edits.
        found = edits('The sequences is bounded and the map are smooth.', 'The sequence is bounded and the map is smooth.')
        self.assertEqual([(e['old'], e['new']) for e in found], [('sequences', 'sequence'), ('are', 'is')])

    def test_masked_math_arguments_and_comments_are_left_alone(self):
        self.assertEqual(edits('Let $x_1$ be a point.', 'Let $x_2$ be a point.'), [])
        self.assertEqual(edits('As in \\cite{smiht} we argue.', 'As in \\cite{smith} we argue.'), [])
        self.assertEqual(edits('We argue. % teh comment', 'We argue. % the comment'), [])
        self.assertEqual(edits('We argue here.', 'We argue\nhere.'), [])

    def test_a_rewrite_is_not_a_correction(self):
        self.assertIsNone(edits('one two three four five six seven eight nine ten eleven twelve thirteen fourteen',
                                'uno two tres four cinq six sept eight nueve ten once twelve trece fourteen'))

    def test_british_and_american_spelling_is_not_an_edit(self):
        for old, new in (('colour', 'color'), ('normalise', 'normalize'), ('modelling', 'modeling'),
                         ('the centre of mass', 'the center of mass'), ('we analyse', 'we analyze')):
            self.assertTrue(T.spelling_swap(old, new), (old, new))
        for old, new in (('there', 'their'), ('Colour', 'colour'), ('colour,', 'color.'), ('a colour', 'the color')):
            self.assertFalse(T.spelling_swap(old, new), (old, new))
        self.assertEqual(edits('We normalise the colour of a set.', 'We normalize the color of a set.'), [])

    def test_votes_and_second_look(self):
        a = {'start_line': 3, 'old': 'At  Proving', 'new': 'to prove'}
        b = {'start_line': 3, 'old': 'at proving', 'new': 'To Prove'}
        self.assertEqual(T.edit_id(a), T.edit_id(b))
        self.assertNotEqual(T.edit_id(a), T.edit_id({**b, 'start_line': 4}))
        table = {('error', False): 'typo', ('error', True): 'typo', ('style', False): 'suggestion',
                 ('wrong', False): None, ('wrong', True): 'suggestion', (None, True): 'typo', (None, False): 'suggestion'}
        for (verdict, unanimous), expected in table.items():
            self.assertEqual(T.importance_of(verdict, unanimous), expected, (verdict, unanimous))

    def test_loose_location(self):
        lines = ['Text.'] * 20 + ['We have \\left( a_{n+1} \\right) = 2 a_n for every integer n.'] + ['Text.'] * 5
        self.assertEqual(T.near(lines, 25, '(a_{n+1}) = 2a_n'), 21)  # spaces, braces, \left, 4 lines off
        self.assertIsNone(T.near(lines, 5, 'a_{n+1}'))  # too far
        self.assertIsNone(T.near(lines, 21, 'b_n'))
        self.assertEqual(T.near(lines, 21, 'a_{n+1} \\right) = 2 a_n for every integer m, as the model wrote'), 21)  # by its first characters


PAPER = r"""\documentclass{article}
\begin{document}
\section{Introduction}
This result allows to conclude the proof of the bound.
The sequences is bounded by a constant for this class.
We study the map $f(x) = x^2$ on the interval and the sequence.
Let $a_n = 2^n$ for every $n \ge 1$.
Then $a_{n+1} = 3 a_n$ for every $n$.
Moreover $b_n \le a_n$ by definition of the sequence.
Finally $c_n = a_n + b_n$ holds for this sequence.
\end{document}
"""


def remark(line, quote, correction, problem):
    return {'line': line, 'quote': quote, 'problem': problem, 'correction': correction, 'evidence_line': 7,
            'evidence_quote': 'a_n = 2^n'}


FORMULAS = [remark(8, 'a_{n+1} = 3 a_n', 'a_{n+1} = 2 a_n', 'The factor contradicts line 7.'),
            remark(9, 'b_n \\le a_n', 'b_n \\ge a_n', 'The inequality is reversed.'),
            remark(10, 'c_n = a_n + b_n', 'c_n = a_n - b_n', 'The sign looks wrong.'),
            remark(30, 'not in the paper', 'x', 'Nowhere.')]


def read(key, prompt):
    if key.startswith('L'):
        return {'summary': 'Defines a doubling sequence.', 'remarks': FORMULAS}
    if key == 'N':  # the same slip seen as notation: one remark
        return {'remarks': [remark(8, 'a_{n+1}=3a_n', 'a_{n+1} = 2 a_n', 'Two factors for one sequence.')]}
    lines = [{'n': 5, 'corrected': '5| The sequence is bounded by a constant for this class.'},
             {'n': 4, 'corrected': 'This result allows us to conclude the proof of the bound.'}]
    if key.startswith('W0'):
        lines.append({'n': 6, 'corrected': 'We study the map $f(x) = x^2$ in the interval and the sequence.'})
    return {'lines': lines}


def check(key, prompt):
    if key.startswith('V'):
        blocks = prompt.split('PROPOSALS:')[1].split('PROPOSAL ')[1:]
        return {'items': [{'id': int(b.split()[0]), 'verdict': 'error' if 'sequence is' in b else 'wrong'} for b in blocks]}
    row = prompt.split('REMARK (line ')[1]
    if row.startswith('8'):
        return {'check': 'Line 7 gives the factor 2.', 'verdict': 'correction'}
    if row.startswith('9'):
        # The first look (keys T…) lets it through; the reasoning check (keys C…) drops it.
        return {'check': 'Line 9 is right by definition.', 'verdict': 'look' if key.startswith('T') else 'drop'}
    return {'check': 'Nothing defines $c_n$.', 'verdict': 'look'}


class ScriptedTypos(TypoCheckRunner):
    script = {}

    def _whole_fits(self):
        return True

    def _ask(self, role, prompt, *, cap, schema=None, key='', think=False, **kwargs):
        self.asked.append({'role': role, 'key': key, 'think': think, 'prompt': prompt, **kwargs})
        answer = self.script[role]
        return {'text': '', 'complete': True, 'value': answer(key, prompt), 'output_limit': cap}


def section(report, title):
    return report.split(f'### {title}\n')[1].split('\n### ')[0].split('\n## ')[0]


class Lanes(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'paper.tex').write_text(PAPER)

    def runner(self, script):
        runner = ScriptedTypos(Agent(FakeClient([]), Workspace(self.root), ctx=400_000, think=True))
        runner.asked, runner.script = [], script
        return runner

    def test_each_lane_lands_in_its_block(self):
        runner = self.runner({'read': read, 'typo-check': check})
        result = runner.start('Check the typos.', source_files=['paper.tex'])
        self.assertEqual(result['status'], 'reviewed')
        report = result['report']
        keys = [call['key'] for call in runner.asked]
        self.assertEqual(sorted(keys), sorted(['L2', 'N', 'W0:2', 'W1:2', 'V0', 'T0', 'T1', 'T2', 'C0', 'C1', 'C2']))
        # The language calls see no paper and do not reason; a formula remark gets a first look, then a check that reasons,
        # both with the lines around it in front.
        for call in runner.asked:
            self.assertEqual(call['think'], call['key'].startswith('C'))
            self.assertEqual('THE MANUSCRIPT' in call['prompt'], call['key'][0] in 'LNCT', call['key'])
        self.assertTrue(all(call.get('reference') is False for call in runner.asked if call['key'][0] in 'WV'))
        # An edit a rule already reports is not judged again; the edit one reading made and the check called wrong is gone.
        verdict = next(call['prompt'] for call in runner.asked if call['key'] == 'V0')
        self.assertNotIn('(line 4)', verdict)
        self.assertNotIn('in the interval', report)
        language = section(report, 'Typos and language')
        self.assertIn('allows us to conclude', language)  # the rule
        self.assertIn('Read "sequence" for "sequences".', language)  # the language lane, both readings
        corrections = section(report, 'Corrections in formulas and notation')
        self.assertIn('a_{n+1} = 2 a_n', corrections)
        self.assertEqual(corrections.count('line 8'), 1)  # formula reading and notation call merged
        self.assertNotIn('c_n', corrections)
        to_check = section(report, 'Formulas to check')
        self.assertIn('Nothing defines $c_n$.', to_check)
        self.assertNotIn('Suggested', to_check)  # a place to check is never a correction
        self.assertNotIn('b_n \\ge a_n', report)  # dropped by its check
        self.assertIn('1 wrong or unnecessary correction(s) removed', report)
        self.assertIn('- Lines 2–11: Defines a doubling sequence.', report)
        self.assertEqual(runner.state['typo_lanes']['unlocated'], 1)

    def test_a_resumed_check_asks_nothing_again(self):
        def stop(key, prompt):
            if key == 'C2':
                raise KeyboardInterrupt
            return check(key, prompt)
        runner = self.runner({'read': read, 'typo-check': stop})
        paused = runner.start('Check the typos.', source_files=['paper.tex'])
        self.assertEqual(paused['status'], 'paused')
        answered = {call['key'] for call in runner.asked} - {'C2'}
        resumed = self.runner({'read': read, 'typo-check': check})
        result = resumed.resume(paused['id'])
        again = {call['key'] for call in resumed.asked}
        self.assertIn('C2', again)
        self.assertEqual(again & answered, set())
        self.assertEqual(result['status'], 'reviewed')
        self.assertIn('Nothing defines $c_n$.', section(result['report'], 'Formulas to check'))
        self.assertEqual(result['report'].count('Read "sequence" for "sequences".'), 1)

    def test_a_small_context_reads_part_by_part_plus_the_rules(self):
        runner = self.runner({'read': lambda key, prompt: {'summary': 'A part.', 'comments': []}})
        runner._whole_fits = lambda: False
        result = runner.start('Check the typos.', source_files=['paper.tex'])
        self.assertEqual({call['key'][0] for call in runner.asked}, {'L'})  # no language, notation or check call
        self.assertEqual(result['status'], 'reviewed')
        self.assertIn('allows us to conclude', result['report'])
        self.assertNotIn('checked against the whole paper', result['report'])


if __name__ == '__main__':
    unittest.main()
