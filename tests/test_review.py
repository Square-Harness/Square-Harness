# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The paper review's rules, without a model: every call is answered by a script keyed on the call's role."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.review import PaperReviewRunner
from mathagent.tools import Workspace
from tests.test_research import FakeClient

PAPER = r"""\documentclass{article}
\begin{document}
\title{On doubling sequences}
\begin{abstract}
We prove a bound for doubling sequences.
\end{abstract}
\section{Introduction}
\begin{theorem}[Main theorem]\label{thm:main}
For every integer $n\ge 1$ we have $a_n \le 2^n$.
\end{theorem}
\begin{lemma}\label{lem:step}
For every $n$ we have $a_{n+1}\le 2a_n$.
\end{lemma}
\begin{proof}
By definition $a_{n+1}=a_n+b_n$ and $b_n\le a_n$.
Hence the doubling bound follows at once.
\end{proof}
\begin{proof}[Proof of Theorem \ref{thm:main}]
We argue by induction using Lemma \ref{lem:step}.
The base case is clear since $a_1\le 2$.
The inductive step multiplies by two.
\end{proof}
\end{document}
"""
BASE_CASE = 'The base case is clear'
DOUBLING = 'Hence the doubling bound follows'


def issue(quote, effect, kind='invalid_inference'):
    line = next(n for n, text in enumerate(PAPER.splitlines(), 1) if quote in text) if quote in PAPER else 1
    return {'start_line': line, 'end_line': line, 'quote': quote, 'kind': kind, 'effect': effect,
            'evidence': f'Objection to "{quote}".', 'suggestion': ''}


class Scripted(PaperReviewRunner):
    """A review whose model calls are answered by `script`: {role: value or function(key, prompt)}."""
    script = {}

    def _whole_fits(self):
        return True  # as on a hosted model: every call sees the whole manuscript

    def _ask(self, role, prompt, *, cap, schema=None, key='', think=False, **kwargs):
        self.asked.append({'role': role, 'key': key, 'think': think, 'prompt': prompt})
        answer = self.script.get(role, DEFAULTS.get(role))
        value = answer(key, prompt) if callable(answer) else answer
        return {'text': '', 'complete': True, 'value': value, 'output_limit': cap}


DEFAULTS = {
    'check': {'explanation': '', 'issues': [], 'verdict': 'no_issue_found', 'external': []},
    'citations': {'citations': []},
    'consistency': {'explanation': '', 'issues': []},
    'completeness': {'explanation': '', 'issues': []},
    'read': {'summary': 'A part of the paper.', 'comments': []},
    'merge': {'groups': []},
    'write': {'summary': 'The paper bounds doubling sequences.', 'significance': 'Modest.', 'correctness': 'See the comments.',
              'presentation': 'Clear.', 'recommendation': 'accept', 'reasons': 'As found.', 'confidential': ''},
}


class ReviewCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'paper.tex').write_text(PAPER)

    def runner(self, script, cls=Scripted):
        agent = Agent(FakeClient([]), Workspace(self.root), ctx=400_000, think=True)
        runner = cls(agent)
        runner.asked = []
        runner.script = {'scope': self.scope, **script}
        self.review = runner
        return runner

    def scope(self, key, prompt):
        units = {u['label']: u['id'] for u in self.review.state['map']['units']}
        return {'overview': 'The paper bounds doubling sequences.', 'field': 'sequences', 'contribution': 'A bound.',
                'main': [units['thm:main']], 'notation': [], 'closest': [], 'queries': [], 'proofs': [], 'prose_results': []}

    def run_review(self, script, **budgets):
        runner = self.runner(script)
        result = runner.start('Review this paper.', source_files=['paper.tex'], max_tokens=2_000_000, **budgets)
        return runner, result

    def unit(self, label):
        return next(u['id'] for u in self.review.state['map']['units'] if u['label'] == label)

    def roles(self, role):
        return [call for call in self.review.asked if call['role'] == role]


class FewerFalseAlarmsTests(ReviewCase):
    def checks(self, by_label):
        """A check script: the issues of the first pass on each labelled result."""
        def answer(key, prompt):
            found = next((issues for label, issues in by_label.items() if key == self.unit(label) + '#0'), [])
            return {'explanation': '', 'issues': found, 'verdict': 'issues_found' if found else 'no_issue_found', 'external': []}
        return answer

    def test_alleged_major_is_judged_even_when_the_defence_concedes(self):
        concede = {'position': 'conceded', 'argument': 'No defence.', 'effect': 'breaks_proof', 'repair': ''}
        script = {'check': self.checks({'thm:main': [issue(BASE_CASE, 'breaks_proof')]}), 'defend': concede}
        # The judge rejects the allegation: the conceded major leaves the comments.
        runner, result = self.run_review({**script, 'arbiter': {'assessment': 'rejected', 'effect': 'breaks_proof',
                                                                 'argument': 'The base case holds: $a_1\\le 2$.', 'repair': ''}})
        self.assertEqual(len(self.roles('arbiter')), 1)
        self.assertTrue(self.roles('arbiter')[0]['think'])
        self.assertIn('The defence did not hold the manuscript right', self.roles('arbiter')[0]['prompt'])
        self.assertEqual(runner.state['ledger'], [])
        self.assertIn('rejected by the second opinion', result['report'])
        # The judge cannot decide: an alleged major stays a major comment.
        runner, _ = self.run_review({**script, 'arbiter': {'assessment': 'undecided', 'effect': 'breaks_proof',
                                                           'argument': 'Unclear.', 'repair': ''}})
        self.assertEqual([(row['label'], row['status']) for row in runner.state['ledger']], [('Major 1', 'undecided')])
        # The judge makes it a gap with a repair: minor only when the separate repair check finds the repair to work.
        gap = {'assessment': 'valid', 'effect': 'gap', 'argument': 'A gap.', 'repair': 'Use $a_1=1$.'}
        for works, label in (('no', 'Major 1'), ('yes', 'Minor 1')):
            runner, _ = self.run_review({**script, 'arbiter': gap, 'repair-check': {'works': works, 'argument': 'Checked.'}})
            self.assertEqual([row['label'] for row in runner.state['ledger']], [label])
            self.assertEqual(len(self.roles('repair-check')), 1)

    def test_minor_findings_are_checked_and_unlocated_ones_dropped(self):
        found = {'lem:step': [issue(DOUBLING, 'imprecision'), issue('a phrase that the paper does not contain', 'gap')],
                 'thm:main': [issue(BASE_CASE, 'misprint')]}

        def minor(key, prompt):
            rows = [line for line in prompt.split('FINDINGS:\n')[1].splitlines() if line.startswith('F')]
            return {'items': [{'id': row.split()[0], 'check': 'Read the lines.', 'keep': DOUBLING not in row} for row in rows]}
        runner, result = self.run_review({'check': self.checks(found), 'minor-check': minor})
        calls = self.roles('minor-check')
        self.assertEqual(len(calls), 1)  # one batch, without hidden reasoning
        self.assertFalse(calls[0]['think'])
        self.assertNotIn('does not contain', calls[0]['prompt'])  # an unlocated finding is not worth a check
        report = result['report']
        # Kept: the supported finding. Left out: the unsupported one and the one whose quote is not in the source.
        self.assertEqual([row['quote'] for row in runner.state['ledger']], [BASE_CASE])
        self.assertIn('checked against the paper', report)
        self.assertNotIn(DOUBLING, report)
        self.assertNotIn('does not contain', report)
        self.assertNotIn('location unsure', report)
        self.assertIn('1 not supported by the paper and left out', report)
        self.assertIn('1 objection(s) whose quote was not found', report)
        # Both stay in the saved job.
        decided = runner.state['minor_checks']
        self.assertEqual(sorted(entry['keep'] for entry in decided.values()), [False, True])
        saved = [i['quote'] for check in runner.state['checks'].values() for i in check['issues']]
        self.assertIn(DOUBLING, saved)
        self.assertIn('a phrase that the paper does not contain', saved)
        # A batch whose answer misses a finding leaves it unchecked and in the report; a resumed review asks again.
        runner, result = self.run_review({'check': self.checks(found), 'minor-check': {'items': []}})
        self.assertEqual({tuple(entry) for entry in runner.state['minor_checks'].values()}, {('failed',)})
        self.assertEqual(len(runner.state['ledger']), 2)


if __name__ == '__main__':
    unittest.main()
