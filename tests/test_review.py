# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The paper review's rules, without a model: every call is answered by a script keyed on the call's role."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.research import _read_state
from mathagent.review import PaperReviewRunner, TypoCheckRunner, runner_for_state, variant_for_state
from mathagent.ledger import _atomic_write
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


class ScriptedTypos(Scripted, TypoCheckRunner):
    pass


TYPO = {'start_line': 20, 'end_line': 20, 'quote': BASE_CASE, 'check': 'Read.', 'is_problem': True, 'kind': 'clarity',
        'importance': 'suggestion', 'comment': 'Say why the base case holds.', 'suggestion': 'Since $a_1 = 1$.'}


class ModesTests(ReviewCase):
    def test_review_does_not_read_for_typos_and_names_the_typo_check(self):
        runner, result = self.run_review({})
        self.assertEqual(self.roles('read') + self.roles('typo-check'), [])
        self.assertEqual((runner.state['level'], runner.state['pipeline'], result['status']), ('high', 'review-v2', 'reviewed'))
        shown = result['report'].rstrip().splitlines()[-1]
        self.assertIn('Typos and presentation were not checked', shown)
        self.assertIn('`@typo_check`', shown)
        self.assertNotIn('## Typos and presentation', result['report'])
        # The second pass on the main result reasons; the first pass and the defence do not (the High of old).
        think = {call['key']: call['think'] for call in self.roles('check')}
        main = self.unit('thm:main')
        self.assertEqual((think[main + '#0'], think[main + '#1']), (False, True))

    def test_typo_check_reads_and_checks_its_remarks_only(self):
        runner = self.runner({'read': {'summary': 'The paper.', 'comments': [TYPO]},
                              'typo-check': {'items': [{'id': 'R1', 'check': 'Read.', 'keep': True}]}}, ScriptedTypos)
        result = runner.start('Check the typos.', source_files=['paper.tex'], max_rounds=5)
        self.assertEqual({call['role'] for call in runner.asked}, {'read', 'typo-check'})
        self.assertEqual((runner.state['level'], runner.state['pipeline'], runner.state['settings']['max_rounds']),
                         ('low', 'typo-check-v1', 1))
        self.assertEqual(result['status'], 'reviewed')
        self.assertTrue(result['report'].startswith('# Typos and presentation'))
        self.assertIn('Say why the base case holds.', result['report'])
        self.assertIn('The proofs were not checked', result['report'])

    def saved(self, cls, script, **fields):
        """A job interrupted at its first call, then rewritten as a review saved before the modes changed."""
        def stop(key, prompt):
            raise KeyboardInterrupt
        first = 'read' if cls is ScriptedTypos else 'scope'
        runner = self.runner({first: stop}, cls)
        paused = runner.start('Review this paper.', source_files=['paper.tex'])
        self.assertEqual(paused['status'], 'paused')
        state = _read_state(runner.directory, paused['id'])
        state.update(pipeline='review-v2', variant='review', **fields)
        _atomic_write(runner.directory / 'state.json', __import__('json').dumps(state).encode())
        return paused['id'], state

    def resumed(self, job_id, state, script, cls=None):
        base = cls or runner_for_state(state)
        runner = self.runner(script, type('Resumed', (Scripted, base), {}))
        return runner, runner.resume(job_id)

    def test_saved_low_review_resumes_as_a_typo_check(self):
        script = {'read': {'summary': 'The paper.', 'comments': [TYPO]},
                  'typo-check': {'items': [{'id': 'R1', 'check': 'Read.', 'keep': True}]}}
        for cls in (None, PaperReviewRunner):  # by its saved state, and as the saved child of a chat's review card
            job_id, state = self.saved(ScriptedTypos, {}, level='low')
            self.assertEqual(variant_for_state(state), 'typos')
            self.assertIs(runner_for_state(state), TypoCheckRunner)
            runner, result = self.resumed(job_id, state, script, cls)
            self.assertEqual({call['role'] for call in runner.asked}, {'read', 'typo-check'})
            self.assertEqual(result['status'], 'reviewed')
            self.assertTrue(result['report'].startswith('# Typos and presentation'))

    def test_saved_medium_and_high_reviews_resume_as_the_review(self):
        for level in ('medium', 'high'):
            # Such a review had planned to read the paper for typos; the resumed review does not.
            job_id, state = self.saved(Scripted, {}, level=level, read_parts=[[1, 22]])
            self.assertIs(runner_for_state(state), PaperReviewRunner)
            runner, result = self.resumed(job_id, state, {})
            roles = {call['role'] for call in runner.asked}
            self.assertEqual(roles & {'read', 'typo-check'}, set())
            self.assertLessEqual({'scope', 'check', 'citations', 'write'}, roles)
            self.assertTrue(any(call['think'] for call in self.roles('check')))
            self.assertEqual(result['status'], 'reviewed')
            self.assertTrue(result['report'].startswith('# Referee report'))


if __name__ == '__main__':
    unittest.main()
