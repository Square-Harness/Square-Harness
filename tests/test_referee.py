# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The Review v4 without a model: referees' tool loops and judges answered by a script keyed on who is asking."""
import copy
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

from mathagent import referee as R
from mathagent.agent import Agent
from mathagent.literature import LiteratureTools
from mathagent.numerics.policy import PolicyError, REVIEW_RUNS, resolve, review_policy
from mathagent.numerics.session import NumericsSession, PythonSandbox
from mathagent.review import PaperReviewRunner, runner_class, runner_for_state, variant_for_state
from mathagent.review_position import position_prompt
from mathagent.tools import Workspace
from tests.test_review import BASE_CASE, DOUBLING, PAPER


def call(name, **args):
    return {'function': {'name': name, 'arguments': args}}


def line_of(text):
    return next(n for n, row in enumerate(PAPER.splitlines(), 1) if text in row)


class ScriptClient:
    """Answers referees round by round from `rounds[who]`, judges from `judges[issue id]`, batches from `batch`."""
    host = 'http://fake.invalid'
    timeout = 60

    def __init__(self, rounds, judges=None, batch=None):
        self.rounds, self.judges, self.batch = rounds, judges or {}, batch or {}
        self.payloads = []
        self.lock = threading.Lock()

    def stream(self, payload):
        with self.lock:
            self.payloads.append(copy.deepcopy(payload))  # the agent reuses the dict of its previous round
        messages = payload['messages']
        first = next(m['content'] for m in messages if m['role'] == 'user')
        text, calls = '', []
        if payload.get('tools') and 'YOUR TASK' in first:
            who = 'lead' if 'YOUR TASK (lead referee)' in first else re.search(r'co-referee (C\d)', first).group(1)
            done = sum(1 for m in messages if m['role'] == 'assistant')
            script = self.rounds.get(who, [])
            step = script[done] if done < len(script) else 'Done.'
            if isinstance(step, str):
                text = step
            else:
                calls = step
        elif payload.get('format') == R.BATCH_SCHEMA:
            ids = re.findall(r'^(I\d+) \(', messages[-1]['content'], re.M)
            text = json.dumps({'items': [{'id': i, 'verdict': self.batch.get(i, 'confirmed'), 'reason': 'Checked.'} for i in ids]})
        else:
            ident = re.search(r'THE REPORTED PROBLEM \((I\d+),', first).group(1)
            text = json.dumps(self.judges.get(ident, {'verdict': 'confirmed', 'effect': 'gap', 'reason': 'Checked.', 'fix': ''}))
        yield {'message': {'content': text, 'tool_calls': calls}, 'done': True, 'done_reason': 'stop',
               'eval_count': 30, 'prompt_eval_count': 1000}


class Runner(R.RefereeRunner):
    def _whole_fits(self):
        return True  # as on a hosted model


class RefereeCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'paper.tex').write_text(PAPER)

    def run_review(self, client, ctx=400_000, **options):
        agent = Agent(client, Workspace(self.root, literature=LiteratureTools(self.root, online=False)), ctx=ctx, think=True)
        runner = Runner(agent, concurrency=4)
        for key, value in options.items():
            setattr(runner, key, value)
        result = runner.start('Review this paper.', source_files=['paper.tex'], max_tokens=2_000_000, max_seconds=900)
        return runner, result

    def results(self, client, who):
        """The tool results a referee received, in order."""
        last = [p for p in client.payloads if p.get('tools') and f'({who}' in json.dumps(p['messages'][1:2])]
        return [m['content'] for m in last[-1]['messages'] if m['role'] == 'tool'] if last else []


class LedgerTests(RefereeCase):
    def test_issues_quotes_finish_and_judge(self):
        base, doubling = line_of(BASE_CASE), line_of(DOUBLING)
        lead = [
            [call('report_issue', start_line=base, end_line=base, quote='words that are not there', kind='proof',
                  claim='The base case is wrong.', evidence='Computed.')],
            [call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='proof',
                  claim='The base case needs $a_1 \\le 2$, never assumed.', evidence='Take $a_1 = 3$.', fix='Assume $a_1 \\le 2$.')],
            [call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='gap',
                  claim='Same place.', evidence='Again.')],
            [call('mark_result', result='thm:main', status='issue', how_checked='Read the induction.', issue_ids=['I1'],
                  counterexample_tried='$a_n = 3^n$.')],
            [call('finish', summary='A short paper on doubling sequences. I1 matters.', recommendation='accept')],
        ]
        co = [[call('report_issue', start_line=doubling, end_line=doubling, quote=DOUBLING, kind='misprint',
                    claim='Doubling should read halving.', evidence='Read.', fix='halving')],
              [call('mark_result', result='lem:step', status='no_error_found', how_checked='Line by line.')],
              [call('mark_result', result='thm:main', status='issue', how_checked='The induction.', issue_ids=['I1'])],
              [call('finish', summary='Checked the lemma.')]]
        client = ScriptClient({'lead': lead, 'C1': co},
                              judges={'I1': {'verdict': 'confirmed', 'effect': 'breaks_proof', 'reason': 'The base case fails.',
                                             'fix': 'Assume $a_1\\le 2$.'}},
                              batch={'I1': 'refuted', 'I2': 'refuted'})
        client.judges['I2'] = client.judges['I1']  # the referees run in parallel: either may report first
        runner, result = self.run_review(client)
        s = runner.state
        self.assertEqual(s['pipeline'], 'review-v4')
        # The ledgers and their stable keys.
        self.assertEqual(sorted(i['id'] for i in s['issues']), ['I1', 'I2'])
        first = next(i for i in s['issues'] if i['reported_by'] == ['lead'])
        self.assertEqual((first['kind'], first['status'], first['judge']['verdict'], first['judge']['effect']),
                         ('proof', 'judged', 'confirmed', 'breaks_proof'))
        self.assertEqual(len(first['other_reports']), 1)  # the second report on the same lines was merged
        self.assertEqual(next(i for i in s['issues'] if i is not first)['judge']['verdict'], 'refuted')
        self.assertEqual(s['referees']['lead']['status'], 'finished')
        self.assertEqual(s['referees']['C1']['status'], 'finished')
        self.assertEqual(s['coverage'][runner._resolve_result('thm:main')]['status'], 'issue')
        results = self.results(client, 'lead')
        self.assertIn('Nothing was recorded', results[0])
        self.assertIn('Recorded as I', results[1])
        self.assertIn('Merged with I', results[2])
        self.assertIn('min left', results[0])
        roles = {c['role'] for c in s['calls']}
        self.assertEqual(roles, {'lead', 'co-referee', 'judge', 'judge-batch'})
        self.assertTrue(all(c.get('stats') for c in s['calls']))
        # The report: the confirmed error raises the lead's "accept"; the refuted misprint is counted, not printed.
        report = result['report']
        self.assertIn('**Recommendation: Major revision.** (raised', report)
        self.assertIn('**E1**', report)
        self.assertIn('## Errors', report)
        self.assertIn('Found by lead; verified by the judge', report)
        self.assertNotIn('halving', report)
        self.assertIn('1 reported issue(s) were refuted', report)
        self.assertIn('`@typo_check`', report)
        self.assertLess(len(report.splitlines()), 300)
        self.assertEqual(result['status'], 'reviewed')
        # The Assistant's merge reads the start of the report and its recommendation.
        prompt = position_prompt(report, 'A review.', [], {})
        self.assertIn('RECOMMENDATION OF THE REPORT: Major revision', prompt)
        self.assertNotIn('## Errors', prompt)

    def test_desk_tools(self):
        runner, _ = self.run_review(ScriptClient({}), co_referees=0)
        desk = R.RefereeDesk(runner, 'lead', 'lead')
        base = line_of(BASE_CASE)
        names = [item['function']['name'] for item in desk.schemas()]
        self.assertNotIn('python', names)  # no sandbox here
        self.assertIn('search_papers', names)
        report = dict(start_line=base, end_line=base, kind='proof', claim='Fails.', evidence='Checked.')
        self.assertIn('Nothing was recorded', desk.execute('report_issue', {**report, 'quote': 'not in the paper at all'}))
        self.assertIn('Nothing was recorded', desk.execute('report_issue', {**report, 'quote': BASE_CASE, 'experiment_ids': ['x1']}))
        # A miscounted line is moved to the line that holds the quote.
        self.assertIn('Recorded as I1', desk.execute('report_issue', {**report, 'start_line': base + 3, 'end_line': base + 3,
                                                                       'quote': BASE_CASE}))
        self.assertEqual(runner.state['issues'][0]['start_line'], base)
        self.assertIn('Merged with I1', R.RefereeDesk(runner, 'C1', 'co-referee').execute('report_issue', {**report, 'quote': BASE_CASE}))
        self.assertEqual(runner.state['issues'][0]['reported_by'], ['lead', 'C1'])
        self.assertIn('Refused: give these results', desk.execute('finish', {'summary': 'Done.', 'recommendation': 'accept'}))
        self.assertIn('No result named', desk.execute('mark_result', {'result': 'Theorem 9', 'status': 'issue', 'how_checked': '-'}))
        desk.execute('mark_result', {'result': 'thm:main', 'status': 'no_error_found', 'how_checked': 'Read.'})
        self.assertIn('Refused: give the recommendation', desk.execute('finish', {'summary': 'Done.'}))
        with self.assertRaises(R.RefereeFinished):
            desk.execute('finish', {'summary': 'Done.', 'recommendation': 'minor_revision'})
        self.assertEqual(runner.state['referees']['lead']['recommendation'], 'minor_revision')
        self.assertIn('L2:', desk.execute('read_lines', {'start_line': 2, 'end_line': 3}))
        self.assertIn(f'L{base}:', desk.execute('search_manuscript', {'text': 'base case'}))

    def test_unfinished_referee_and_unjudged_issue(self):
        base = line_of(BASE_CASE)
        lead = [[call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='gap',
                      claim='Why is $a_1\\le 2$?', evidence='Not stated.', question=True)], 'I stop here.']
        client = ScriptClient({'lead': lead, 'C1': ['Nothing to say.']},
                              judges={'I1': {'verdict': 'undecided', 'effect': 'gap', 'reason': 'Depends on $a_1$.', 'fix': ''}})
        runner, result = self.run_review(client)
        s = runner.state
        self.assertEqual(s['referees']['lead']['status'], 'ended')
        report = result['report']
        self.assertIn('## Questions to the authors', report)
        self.assertIn('Not checked (no status given)', report)
        self.assertIn('**Recommendation: Minor revision.**', report)

    def test_round_at_its_cap_continues_with_tools(self):
        base = line_of(BASE_CASE)

        class Capped(ScriptClient):
            def stream(self, payload):
                first = next(m['content'] for m in payload['messages'] if m['role'] == 'user')
                if payload.get('tools') and 'YOUR TASK (lead' in first and not any(m['role'] == 'assistant' for m in payload['messages']):
                    self.payloads.append(copy.deepcopy(payload))
                    yield {'message': {'thinking': 'The base case needs a_1 <= 2.'}, 'done': False}
                    yield {'message': {'content': ''}, 'done': True, 'done_reason': 'length', 'eval_count': 100,
                           'prompt_eval_count': 1000}
                    return
                yield from super().stream(payload)
        lead = [None, [call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='gap',
                            claim='Why $a_1\\le 2$?', evidence='Not stated.')], 'Done.']
        client = Capped({'lead': lead})
        runner, _ = self.run_review(client, co_referees=0)
        rounds = [p for p in client.payloads if p.get('tools') and 'YOUR TASK (lead' in json.dumps(p['messages'][:3])]
        self.assertTrue(rounds[1].get('tools'))  # the loop went on with its tools
        self.assertIn('THE END OF YOUR REASONING SO FAR', rounds[1]['messages'][-1]['content'])
        self.assertIn('a_1 <= 2', rounds[1]['messages'][-1]['content'])
        self.assertEqual(len(runner.state['issues']), 1)

    def test_query_filter(self):
        words = ' ' + ' '.join(R._public_words(PAPER)) + ' '
        self.assertTrue(R.copies_manuscript('By definition $a_{n+1}=a_n+b_n$ and $b_n\\le a_n$ hence', words))
        self.assertFalse(R.copies_manuscript('doubling sequences bound induction', words))
        lead = [[call('search_papers', query='We argue by induction using Lemma lem:step. The base case is clear')], 'Done.']
        runner, _ = self.run_review(ScriptClient({'lead': lead}), co_referees=0)
        tool = next(m for p in runner.agent.client.payloads for m in p['messages'] if m['role'] == 'tool')
        self.assertIn('repeats 8 consecutive words', tool['content'])

    def test_route_keeps_v3_when_the_paper_does_not_fit(self):
        runner, _ = self.run_review(ScriptClient({}), ctx=150_000)
        self.assertEqual(runner.state['pipeline'], 'review-v2')
        self.assertEqual(runner.state['issues'], [])
        self.assertIs(runner_class('review'), R.RefereeRunner)
        self.assertIs(runner_for_state({'pipeline': 'review-v2'}), PaperReviewRunner)
        self.assertIs(runner_for_state({'pipeline': 'review-v4'}), R.RefereeRunner)
        self.assertEqual(variant_for_state({'pipeline': 'review-v4'}), 'review')


class WorkerSlotsTests(unittest.TestCase):
    def test_a_review_worker_has_its_own_slots(self):
        from mathagent.assistant_budget import BudgetClient
        tree = BudgetClient(ScriptClient({}), max_seconds=100, concurrency=2)
        own = threading.BoundedSemaphore(16)
        review = tree.fork(ScriptClient({}), allowance=None, usage={'tokens': 0, 'input_tokens': 0}, slots=own)
        other = tree.fork(ScriptClient({}), allowance=None, usage={'tokens': 0, 'input_tokens': 0})
        self.assertIs(review.slots, own)
        self.assertIs(other.slots, tree.pool.slots)
        self.assertIs(review.pool, tree.pool)  # one shared budget


class SandboxPolicyTests(unittest.TestCase):
    def test_review_policy_limits(self):
        self.assertEqual(REVIEW_RUNS, {'lead': 20, 'co-referee': 8, 'judge': 3})
        policy = review_policy('co-referee')
        self.assertEqual((policy.name, policy.max_runs, policy.defaults.timeout_seconds, policy.escalation),
                         ('review', 8, 30, False))
        with self.assertRaises(PolicyError):
            resolve(policy, {'timeout_seconds': 60})
        with self.assertRaises(ValueError):
            review_policy('assistant')

    def test_runs_stop_at_the_role_limit(self):
        class Executor:
            def probe(self, refresh=False):
                raise AssertionError('no run once the budget is used')
        session = NumericsSession(Executor(), None, review_policy('judge'), None, explicit=False)
        session.runs = 3
        value = json.loads(session.execute('execute_python', {'code': 'print(1)'}))
        self.assertEqual(value['status'], 'budget_exhausted')
        sandbox = PythonSandbox(enabled=True, automatic=False, executor=Executor())
        self.assertIsNone(sandbox.review_session('.', 'lead'))


if __name__ == '__main__':
    unittest.main()
