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


NO_ATTACK = {'verdict': 'none', 'counterexample': 'Tried $n = 1$ and constant sequences.', 'hypothesis_check': '',
             'quote': ''}
DEFAULTS = {
    'attack': NO_ATTACK,
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
        # The judge's verdict is final: a gap it finds makes the finding minor, with no further check.
        gap = {'assessment': 'valid', 'effect': 'gap', 'argument': 'A gap.', 'repair': 'Use $a_1=1$.'}
        runner, _ = self.run_review({**script, 'arbiter': gap})
        self.assertEqual([row['label'] for row in runner.state['ledger']], ['Minor 1'])
        self.assertEqual(self.roles('repair-check'), [])

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
# The same remark as the typo check's lanes see it: a formula remark (tests/test_typocheck.py tests the lanes).
REMARK = {'line': 20, 'quote': BASE_CASE, 'problem': 'Say why the base case holds.', 'correction': 'Since $a_1 = 1$.',
          'evidence_line': 0, 'evidence_quote': ''}


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
        runner = self.runner({'read': {'summary': 'The paper.', 'comments': [TYPO], 'remarks': [REMARK], 'lines': []},
                              'typo-check': {'items': [{'id': 'R1', 'check': 'Read.', 'keep': True}],
                                             'check': 'Read.', 'verdict': 'correction'}}, ScriptedTypos)
        result = runner.start('Check the typos.', source_files=['paper.tex'], max_rounds=5)
        self.assertEqual({call['role'] for call in runner.asked}, {'read', 'typo-check'})
        self.assertEqual((runner.state['level'], runner.state['pipeline'], runner.state['settings']['max_rounds']),
                         ('high', 'typo-check-v1', 5))
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
        script = {'read': {'summary': 'The paper.', 'comments': [TYPO], 'remarks': [REMARK], 'lines': []},
                  'typo-check': {'items': [{'id': 'R1', 'check': 'Read.', 'keep': True}], 'check': 'Read.', 'verdict': 'correction'}}
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


class AttackTests(ReviewCase):
    def line(self, text):
        return next(n for n, row in enumerate(PAPER.splitlines(), 1) if text in row)

    def test_counterexample_becomes_a_judged_finding(self):
        attack = {'verdict': 'major', 'counterexample': 'Take $a_n = 3^n$: then $a_1 = 3 > 2$.',
                  'hypothesis_check': 'The statement assumes only $n \\ge 1$.', 'quote': 'we have $a_n \\le 2^n$'}
        judge = {'assessment': 'valid', 'effect': 'statement_false', 'argument': 'The example satisfies the hypotheses.', 'repair': ''}
        runner, result = self.run_review({'attack': attack, 'arbiter': judge, 'defend': {
            'position': 'conceded', 'argument': 'No defence.', 'effect': 'statement_false', 'repair': ''}})
        main, lemma = self.unit('thm:main'), self.unit('lem:step')
        # One reasoning call per main result, on the whole paper, with the manuscript before the task.
        calls = self.roles('attack')
        self.assertEqual([(call['key'], call['think']) for call in calls], [(f'attack:{main}', True)])
        prompt = calls[0]['prompt']
        self.assertTrue(prompt.startswith('SOURCE M1'))
        self.assertLess(prompt.index('END OF THE MANUSCRIPT'), prompt.index('Attack the STATEMENT'))
        first = next(i for i, call in enumerate(runner.asked) if call['role'] != 'scope')
        self.assertEqual(runner.asked[first]['role'], 'attack')  # the first call started once the overview is known
        # The state the benchmark reads.
        saved = runner.state['attacks'][main]
        self.assertEqual(saved['verdict'], 'major')
        # The counterexample is a finding that the statement is false, judged like any alleged major.
        self.assertEqual([(row['label'], row['effect'], row['unit'], row['status']) for row in runner.state['ledger']],
                         [('Major 1', 'statement_false', main, 'confirmed')])
        self.assertEqual(len(self.roles('arbiter')), 1)
        self.assertIn('Take $a_n = 3^n$', self.roles('defend')[0]['prompt'])
        # No extra pass follows an attack (the Review no longer names a weakest step).
        self.assertEqual([call['key'] for call in self.roles('check') if call['key'] == lemma + '#weak'], [])
        # The report shows the outcome next to the main result.
        report = result['report']
        self.assertIn('| proposed: Major 1 |', report)
        self.assertIn('raised by the search for a counterexample to the statement', report)
        self.assertEqual(result['status'], 'reviewed')

    def test_no_hit_and_no_weak_step_keep_the_standard_depth(self):
        runner, result = self.run_review({})
        main, lemma = self.unit('thm:main'), self.unit('lem:step')
        self.assertEqual(runner.state['attacks'][main]['verdict'], 'none')
        self.assertEqual(sorted(call['key'] for call in self.roles('check')),
                         sorted([main + '#0', main + '#1', lemma + '#0', lemma + '#1']))
        self.assertEqual(runner.state['ledger'], [])
        self.assertIn('| none found |', result['report'])

    def test_rejected_edge_and_unchecked_counterexamples(self):
        hit = {'verdict': 'minor', 'counterexample': 'Take $a_1 = 3$.', 'hypothesis_check': 'Only $n \\ge 1$ is assumed.',
               'quote': 'not words of the statement'}
        rejected = {'assessment': 'rejected', 'effect': 'statement_false', 'argument': 'The sequence is doubling.', 'repair': ''}
        runner, result = self.run_review({'attack': hit, 'arbiter': rejected, 'defend': {
            'position': 'defended', 'argument': 'Doubling is assumed.', 'effect': 'statement_false', 'repair': ''}})
        main = self.unit('thm:main')
        self.assertEqual(runner.state['ledger'], [])  # a counterexample the judge rejects is no comment
        self.assertIn('| proposed, rejected by the second opinion |', result['report'])
        # An edge case is shown under the table and is not a finding.
        runner, result = self.run_review({'attack': {**NO_ATTACK, 'verdict': 'edge', 'counterexample': 'For $n = 0$ the sum is empty.'}})
        self.assertEqual(runner.state['checks'].get(main + '#attack'), None)
        self.assertIn('edge case: For $n = 0$ the sum is empty.', result['report'])
        # A counterexample announced without the check of the hypotheses is not kept.
        runner, result = self.run_review({'attack': {**hit, 'hypothesis_check': ''}})
        self.assertEqual((runner.state['attacks'][main]['verdict'], runner.state['ledger']), ('none', []))
        self.assertEqual(self.roles('defend'), [])


# -- the Assistant's review, with a literature comparison on request ---------------------------------

import json
import threading
from types import SimpleNamespace

from mathagent.mode_calls import ModeOrchestrator
from mathagent.review_position import literature_goal, own_marks, render_position
from tests.test_research import response

MANUSCRIPT = PAPER.replace(r'\title{On doubling sequences}', r'\title{On doubling sequences}' '\n' r'\author{Ada Martin \and Bo Chen}')
BRIEF = {'question': 'Growth bounds for sequences whose consecutive terms at most double.',
         'results': ['A sequence that at most doubles at each step is bounded by a power of two.'],
         'closest': ['Okabe 1998: exponential bounds for subadditive recursions.']}
POSITION = {'results': [{'result': 'Theorem (Main theorem)', 'closest': 'The exponential bound of [1]; [2] is the same statement; see also [9].',
                         'difference': 'The bound of [1] needs monotone sequences.'}],
            'recommendation': 'It changes nothing.'}
REFERENCES = [
    {'k': 1, 'title': 'Exponential bounds for subadditive recursions', 'authors': ['Okabe, Ken'], 'year': 1998, 'doi': '10.1000/x'},
    {'k': 2, 'title': 'On doubling sequences', 'authors': ['Martin, Ada', 'Chen, Bo'], 'year': 2026, 'arxiv': '2601.00001'},
    {'k': 3, 'title': 'Halving sequences', 'authors': ['Bo Chen', 'Ada Martin'], 'year': 2024},
]
COMPARISON = 'Review that paper and compare it with the literature.'
REPORT = '# Referee report\n\n## Summary\n\nA bound.\n\n## Major comments\n\n1. A comment.\n\n## Recommendation\n\n**Minor revision**'


class FanOutCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'paper.tex').write_text(MANUSCRIPT)
        self.saved = {'job-review': {'sources': [{'content': MANUSCRIPT}]},
                      'job-lit': {'references': REFERENCES, 'draft': 'Okabe proved an exponential bound [1].\n\n## References\n\n1. Okabe.'}}
        for name, value in (('_read_state', lambda directory, job_id: self.saved[job_id]),
                            ('_job_directory', lambda root, job_id: Path(job_id))):
            patcher = patch('mathagent.mode_calls.' + name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = []
        self.literature = {'status': 'complete', 'answer': 'Okabe proved an exponential bound [1].', 'job_id': 'job-lit'}

    def assistant(self, replies, *, online=True, concurrency=2, required_mode='referee'):
        workspace = Workspace(self.root)
        workspace.literature = SimpleNamespace(online=online)
        self.client = FakeClient([response(json.dumps(reply) if isinstance(reply, dict) else reply) for reply in replies])
        agent = SimpleNamespace(client=self.client, model='model', ctx=200_000, workspace=workspace, top_p=.95, seed=None,
                                think=False, history=[], last_stats={})
        self.parent = ModeOrchestrator(agent, self.delegate, required_mode=required_mode, concurrency=concurrency)
        return self.parent

    def delegate(self, action, child, save):
        self.events.append(('start', action['mode'], self.parent.state['force_final'], len(self.client.requests)))
        if action['mode'] == 'literature':
            return dict(self.literature)
        return {'status': 'reviewed', 'answer': REPORT, 'job_id': 'job-review'}

    def test_review_and_literature_review_start_together_and_the_reply_joins_them(self):
        for concurrency in (1, 2):
            self.events = []
            result = self.assistant([BRIEF, POSITION], concurrency=concurrency).run(COMPARISON, files=['paper.tex'])
            review, literature = result['children']
            self.assertEqual((review['action']['mode'], literature['action']['mode']), ('referee', 'literature'))
            self.assertEqual(review['batch_id'], literature['batch_id'])  # one batch, two cards and jobs
            # The literature review: lowest breadth, no manuscript, its own question in public words.
            action = literature['action']
            self.assertEqual((action['effort'], action['files'], literature['context']['query']), ('high', [], action['request']))
            self.assertIn(BRIEF['question'], action['request'])
            self.assertNotIn('For every integer', action['request'])
            # The answer was not final when the review returned: both workers ran, and the position was written last.
            self.assertEqual(sorted(event[1] for event in self.events), ['literature', 'referee'])
            if concurrency == 1:  # in turn: the review has returned when the literature review starts
                self.assertEqual([event[1] for event in self.events], ['referee', 'literature'])
            self.assertFalse(any(event[2] for event in self.events))
            self.assertEqual({event[3] for event in self.events}, {1})  # one model call before them: the question
            self.assertEqual(len(self.client.requests), 2)
            self.assertTrue(self.parent.state['force_final'])
            # The reply: the report as it is, then the position from the verified records.
            answer = result['answer']
            self.assertTrue(answer.startswith(REPORT))
            position = answer[len(REPORT):]
            self.assertIn('## Position in the literature', position)
            self.assertIn('The exponential bound of [1]; [2] is the same statement; see also.', position)  # [9] is no record
            self.assertIn("*On doubling sequences* (2026). [arXiv:2601.00001](https://arxiv.org/abs/2601.00001) — **the manuscript itself", position)
            self.assertIn("Bo Chen, Ada Martin. *Halving sequences* (2024). — **a work of the manuscript's authors", position)
            self.assertNotIn('Okabe proved an exponential bound [1].\n\n## References', answer)  # the review is on its card
            self.assertEqual(sum(m.get('role') == 'assistant' and bool(m.get('content')) for m in self.parent.state['messages']), 1)
            prompt = self.client.requests[1]['messages'][1]['content']
            self.assertIn('*On doubling sequences* (2026). [arXiv:2601.00001](https://arxiv.org/abs/2601.00001) OWN', prompt)

    def test_no_position_without_online_search(self):
        for request in ('Review this paper.', 'Review it without a literature review.',
                        'Review this paper. The text says “compare it with the literature”.',
                        'Review the proof and check its citations.',
                        'Relis cet article et ne compare pas ses résultats aux travaux antérieurs.'):
            result = self.assistant([], online=True).run(request, files=['paper.tex'])
            self.assertEqual(result['answer'], REPORT)
            self.assertEqual(self.client.requests, [])
        parent = self.assistant([BRIEF, POSITION])
        result = parent.run('Relis cet article et compare ses résultats aux travaux antérieurs.', files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['referee', 'literature'])
        result = self.assistant([], online=False).run(COMPARISON, files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['referee'])
        self.assertIn('## Position in the literature\n\nMissing: online search is off', result['answer'])
        self.assertEqual(self.client.requests, [])
        result = self.assistant([]).run(COMPARISON + ' Without searching online.', files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['referee'])
        self.assertIn('Missing: the request excludes online search', result['answer'])

    def test_unfinished_literature_review_leaves_the_position_missing(self):
        self.literature = {'status': 'budget_exhausted', 'answer': '', 'job_id': 'job-lit'}
        result = self.assistant([BRIEF]).run(COMPARISON, files=['paper.tex'])
        self.assertEqual(len(result['children']), 2)
        self.assertTrue(result['answer'].startswith(REPORT))
        self.assertIn('Missing: the literature review started beside the report did not finish', result['answer'])
        self.assertIn('no position was written from memory', result['answer'])
        self.assertEqual(len(self.client.requests), 1)  # no position call without verified records

    def test_question_copied_from_the_manuscript_starts_no_literature_review(self):
        copied = {**BRIEF, 'question': 'For every integer $n\\ge 1$ we have $a_n \\le 2^n$ and more words.'}
        result = self.assistant([copied, copied]).run(COMPARISON, files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['referee'])
        self.assertIn('could not be written in public words', result['answer'])
        # The second try was told which phrase it copied.
        self.assertIn('COPIED THESE PHRASES', self.client.requests[1]['messages'][1]['content'])
        self.assertIn('- for every integer n 1 we have a n 2 n\n', self.client.requests[1]['messages'][1]['content'] + '\n')

    def test_a_copied_brief_is_written_again_once(self):
        copied = {**BRIEF, 'question': 'For every integer $n\\ge 1$ we have $a_n \\le 2^n$ and more words.'}
        result = self.assistant([copied, BRIEF, POSITION]).run(COMPARISON, files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['referee', 'literature'])
        self.assertIn(BRIEF['question'], result['children'][1]['action']['request'])

    def test_review_in_chat_returns_the_saved_report_and_compares_on_request(self):
        for selected in (None, 'referee'):
            parent = self.assistant([], required_mode=selected)
            self.client.profile = 'deepinfra'
            result = parent.run('review that paper', files=['paper.tex'])
            self.assertEqual([c['action']['mode'] for c in result['children']], ['referee'])
            self.assertEqual(result['children'][0]['action']['request'], 'review that paper')
            self.assertEqual(result['answer'], REPORT)
            self.assertFalse(self.client.requests)
        query = 'Review the attached manuscript, focusing on its argument in Section 3.'
        route = {'route': 'workers', 'effort': 'high', 'reason': 'Manuscript review.',
                 'tasks': [{'mode': 'referee', 'files': [], 'request': 'Check the paper.'}]}
        parent = self.assistant([route], required_mode=None)
        self.client.profile = 'deepinfra'
        result = parent.run(query, files=['paper.tex'])
        self.assertEqual(len(result['children']), 1)
        self.assertEqual(result['children'][0]['action']['files'], ['paper.tex'])
        self.assertEqual(result['children'][0]['action']['request'], query)
        self.assertEqual(result['answer'], REPORT)
        self.assertEqual(len(self.client.requests), 1)  # routing only
        parent = self.assistant([BRIEF, POSITION], required_mode=None)
        self.client.profile = 'deepinfra'
        result = parent.run(COMPARISON, files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['referee', 'literature'])
        self.assertEqual(result['children'][0]['action']['effort'], 'high')  # no effort is chosen for a review
        self.assertTrue(result['answer'].startswith(REPORT))
        self.assertIn('## Position in the literature\n\nWritten from the literature review', result['answer'])

    def test_typo_check_and_follow_up_start_no_literature_review(self):
        result = self.assistant([], required_mode='typo_check').run('Check the typos.', files=['paper.tex'])
        self.assertEqual([child['action']['mode'] for child in result['children']], ['typo_check'])
        self.assertEqual(result['answer'], REPORT)

    def test_own_records_and_citations(self):
        self.assertEqual(own_marks(REFERENCES, MANUSCRIPT), {2: 'manuscript', 3: 'authors'})
        self.assertIsNone(render_position({'results': [], 'recommendation': 'None.'}, REFERENCES, {}))
        self.assertIsNone(literature_goal({'question': '', 'results': ['x'], 'closest': []}, MANUSCRIPT))
