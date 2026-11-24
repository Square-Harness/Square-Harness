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
from mathagent.agent import Agent, AgentError
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
        # The refuted misprint is not a finding, but it is listed, one line, so that a wrong rejection can be caught.
        rejected = report.split('## Raised by a referee and rejected by the judge')[1]
        self.assertIn('Doubling should read halving. *Judge:* Checked.', rejected)
        self.assertNotIn('halving', report.split('## Raised by a referee')[0])
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
        # The lead did not finish: the recommendation is provisional and the report says what was not checked.
        self.assertIn('**Recommendation: Minor revision.** (provisional: the review is incomplete)', report)
        self.assertIn('*The review is incomplete: the lead referee did not finish', report)
        self.assertEqual(result['status'], 'partial')

    def test_first_rounds_are_short(self):
        lead = [[call('list_results')], [call('read_lines', start_line=1, end_line=5)], [call('list_results')], 'Done.']
        client = ScriptClient({'lead': lead})
        self.run_review(client, co_referees=0)
        caps = [p['options']['num_predict'] for p in client.payloads if p.get('tools') and 'YOUR TASK (lead' in json.dumps(p['messages'][:3])]
        self.assertEqual(caps[:3], [R.FIRST_ROUND_CAP, R.FIRST_ROUND_CAP, R.ROUND_CAP])

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


class JudgeTests(RefereeCase):
    """The judge keeps false alarms out; it never makes a real finding smaller or invisible."""

    def review_with(self, judges, lead=None, co=None, **options):
        base = line_of(BASE_CASE)
        lead = lead or [[call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='proof',
                              claim='The base case is not proved: nothing gives $a_1\\le 2$.', evidence='Read the definitions.')],
                        'Done.']
        client = ScriptClient({'lead': lead, 'C1': co or ['Nothing.']}, judges=judges)
        runner, result = self.run_review(client, **options)
        return runner, result, client

    def test_a_reported_error_rated_lower_without_a_repair_stays_an_error(self):
        runner, result, client = self.review_with({'I1': {'verdict': 'confirmed', 'effect': 'gap', 'reason': 'A step is missing.',
                                                          'fix': 'Add it.', 'fill': '', 'new_ingredient': ''}}, co_referees=0)
        issue = runner.state['issues'][0]
        self.assertEqual((issue['judge']['effect'], issue['judge']['judge_effect'], issue['judge']['kept_error']),
                         ('breaks_proof', 'gap', 'no_fill'))
        self.assertTrue(issue['judge']['tools'])  # an error on the main chain is judged with the tool loop
        report = result['report']
        self.assertIn('**E1. ', report)
        self.assertIn('*The judge rated it gap, but it gave no complete argument that repairs it: it stays an error.', report)
        self.assertIn('**Recommendation: Major revision.**', report)
        judge_prompt = next(p for p in client.payloads if 'THE REPORTED PROBLEM (I1' in json.dumps(p['messages']))
        text = json.dumps(judge_prompt['messages'])
        self.assertIn('a density, a regularity up to the boundary or the initial', text)
        self.assertIn('new_ingredient', text)

    def test_a_repair_with_a_new_ingredient_stays_an_error(self):
        fill = ('Write $a_1=a_0+b_0$ and use $b_0\\le a_0\\le 1$; then $a_1\\le 2$ follows, and the induction starts. '
                'This uses the bound $a_0\\le 1$.')
        runner, result, _ = self.review_with({'I1': {'verdict': 'confirmed', 'effect': 'imprecision', 'reason': 'Minor.',
                                                     'fix': '', 'fill': fill, 'new_ingredient': 'the bound $a_0\\le 1$'}},
                                             co_referees=0)
        self.assertEqual(runner.state['issues'][0]['judge']['kept_error'], 'new_ingredient')
        self.assertIn('its repair needs the bound $a_0\\le 1$, which the manuscript neither proves nor cites', result['report'])

    def test_a_complete_repair_files_it_as_a_gap_and_shows_the_disagreement(self):
        fill = ('By definition $a_1=a_0+b_0$ with $b_0\\le a_0$, and the standing assumption $a_0=1$ of the introduction '
                'gives $a_1\\le 2$, so the base case holds as stated.')
        runner, result, _ = self.review_with({'I1': {'verdict': 'confirmed', 'effect': 'gap', 'reason': 'Only a step is missing.',
                                                     'fix': '', 'fill': fill, 'new_ingredient': 'none'}}, co_referees=0)
        self.assertTrue(runner.state['issues'][0]['judge']['lowered'])
        report = result['report']
        self.assertIn('**G1. ', report)
        self.assertIn("*Reported as error in a proof by lead; the judge's argument that it is a gap:* By definition", report)
        self.assertIn('## Errors\n\nNone confirmed.', report)

    def test_refutation_against_two_referees_is_disputed(self):
        base = line_of(BASE_CASE)
        report = dict(start_line=base, end_line=base, quote=BASE_CASE, kind='gap', evidence='Read.')
        lead = [[call('report_issue', claim='Why is $a_1\\le 2$?', **report)], 'Done.']
        co = [[call('report_issue', claim='The base case $a_1\\le 2$ is not justified.', **report)], 'Done.']
        gate = threading.Event()

        class Ordered(ScriptClient):  # the judge answers once both referees have reported
            def stream(self, payload):
                if 'THE REPORTED PROBLEM' in json.dumps(payload['messages'][:3]):
                    gate.wait(5)
                elif sum(1 for m in payload['messages'] if m['role'] == 'tool' and 'I1' in m['content']) and \
                        'C1' in json.dumps(payload['messages'][:3]):
                    gate.set()
                yield from super().stream(payload)
        client = Ordered({'lead': lead, 'C1': co},
                         judges={'I1': {'verdict': 'refuted', 'effect': 'gap', 'reason': 'Stated in the definitions.', 'fix': ''}})
        runner, result = self.run_review(client)
        issue = runner.state['issues'][0]
        self.assertEqual((issue['status'], issue['disputed'], sorted(issue['reported_by'])), ('disputed', 'two_referees', ['C1', 'lead']))
        self.assertIn('*Disputed:* the judge rejected it (Stated in the definitions.), but two referees reported it independently.',
                      result['report'])
        self.assertNotIn('## Raised by a referee and rejected', result['report'])

    def test_an_undecided_error_on_the_main_chain_makes_the_recommendation_provisional(self):
        doubling = line_of(DOUBLING)
        lead = [[call('report_issue', start_line=doubling, end_line=doubling, quote=DOUBLING, kind='proof',
                      claim='The doubling bound needs $b_n\\le a_n$ for all $n$, which is not shown.', evidence='Read.')],
                [call('mark_result', result='thm:main', status='no_error_found', how_checked='Read it.')],
                [call('finish', summary='Fine.', recommendation='accept')]]
        runner, result, _ = self.review_with({'I1': {'verdict': 'undecided', 'effect': 'breaks_proof', 'reason': 'Cannot tell.',
                                                     'fix': '', 'fill': '', 'new_ingredient': ''}}, lead=lead, co_referees=0)
        report = result['report']
        self.assertIn('**Recommendation: Major revision.** (raised', report)
        self.assertIn('(provisional: a reported error is not settled)', report)
        self.assertIn('reported as error in a proof and not settled', report)

    def test_the_judge_sees_the_other_side(self):
        runner, _, _ = self.review_with({}, lead=['Done.'], co_referees=0)
        base = line_of(BASE_CASE)
        uid = runner._resolve_result('thm:main')
        runner.state['coverage'][uid] = {'status': 'no_error_found', 'how_checked': 'The base case is in the definitions.',
                                         'counterexample': '', 'issues': [], 'by': 'C4', 'at': 1}
        desk = R.RefereeDesk(runner, 'C1', 'co-referee')
        desk.execute('report_issue', dict(start_line=base, end_line=base, quote=BASE_CASE, kind='gap', claim='Unproved base case.',
                                          evidence='Read.'))
        issue = runner.state['issues'][-1]
        text = runner._issue_text(issue)
        self.assertIn('C4 checked it and found no error: The base case is in the definitions.', text)
        issue.update(status='judged', judge={'verdict': 'confirmed', 'effect': 'gap', 'reason': 'Yes.', 'fix': '', 'tools': False})
        self.assertIn('*C4 checked Theorem (Main theorem) and found no error:*', '\n'.join(R.render(runner)))


class WithdrawTests(RefereeCase):
    def test_withdraw_and_non_findings(self):
        runner, _ = self.run_review(ScriptClient({}), co_referees=0)
        base, doubling = line_of(BASE_CASE), line_of(DOUBLING)
        lead, co = R.RefereeDesk(runner, 'lead', 'lead'), R.RefereeDesk(runner, 'C1', 'co-referee')
        report = dict(start_line=base, end_line=base, quote=BASE_CASE, kind='gap', evidence='Read.')
        # A non-finding is refused at once: checks go to mark_result.
        self.assertIn('Nothing was recorded', lead.execute('report_issue', {**report, 'claim': 'Not a misprint after all — '
                                                                            'verified consistent (no issue).'}))
        lead.execute('report_issue', {**report, 'claim': 'The base case is unproved.'})
        self.assertIn('Refused', co.execute('withdraw_issue', {'issue_id': 'I1', 'reason': 'It is fine.'}))
        self.assertIn('is withdrawn', lead.execute('withdraw_issue', {'issue_id': 'I1', 'reason': 'The definitions give it.'}))
        issue = runner.state['issues'][0]
        self.assertEqual((issue['status'], issue['withdrawn']['by']), ('withdrawn', 'lead'))
        # A report merged from two referees: one withdrawal leaves the other's report as the issue.
        lead.execute('report_issue', dict(start_line=doubling, end_line=doubling, quote=DOUBLING, kind='gap',
                                          claim='First claim.', evidence='Read.'))
        co.execute('report_issue', dict(start_line=doubling, end_line=doubling, quote=DOUBLING, kind='gap',
                                        claim='Second claim.', evidence='Computed.'))
        self.assertIn('still report', lead.execute('withdraw_issue', {'issue_id': 'I2', 'reason': 'Wrong.'}))
        second = runner.state['issues'][1]
        self.assertEqual((second['claim'], second['reported_by'], second['status']), ('Second claim.', ['C1'], 'pending'))
        # Saved before this check: a non-finding in the ledger is never printed.
        runner.state['issues'].append(dict(second, id='I3', claim='Not a misprint after all — verified consistent (no issue).',
                                           status='unjudged', reported_by=['C1']))
        report_text = '\n'.join(R.render(runner))
        self.assertNotIn('The base case is unproved', report_text)
        self.assertNotIn('after all', report_text)
        self.assertIn('2 report(s) taken back by their own referees', report_text)


class FailureTests(RefereeCase):
    def test_a_refused_credential_stops_the_review_at_once(self):
        base = line_of(BASE_CASE)

        class Refusing(ScriptClient):
            def stream(self, payload):
                first_round = payload.get('tools') and not any(m['role'] == 'assistant' for m in payload['messages'])
                if not (first_round and 'YOUR TASK (lead' in json.dumps(payload['messages'][:3])):
                    with self.lock:
                        self.payloads.append(copy.deepcopy(payload))
                    raise AgentError('DeepInfra HTTP 403: Check the credential scopes and allowed models.')
                yield from super().stream(payload)
        lead = [[call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='gap',
                      claim='The base case is unproved.', evidence='Read.')], 'Done.']
        runner, result = self.run_review(Refusing({'lead': lead}), co_referees=0)
        s = runner.state
        self.assertEqual((s['referees']['lead']['status'], s['referees']['lead']['restarts']), ('stopped', 0))
        self.assertIn('refused the calls', s['referees']['lead']['stop'])
        self.assertEqual(sum('refused' in w for w in s['warnings']), 1)
        self.assertFalse(any('started again' in w for w in s['warnings']))
        refused = [c for c in s['calls'] if c['status'] == 'rejected']
        self.assertTrue(refused)
        self.assertTrue(all(c['charged_tokens'] == 0 for c in refused))
        self.assertEqual(s['tokens_charged'], sum(c['stats']['eval_count'] for c in s['calls'] if c.get('stats')))
        issue = s['issues'][0]
        self.assertEqual((issue['status'], issue['unjudged_reason']), ('unjudged', 'provider'))
        report = result['report']
        self.assertIn('(provisional: the review is incomplete)', report)
        self.assertIn("*Not judged (by the provider's refusal).*", report)
        self.assertIn('*Reported by lead as gap in a proof.*', report)
        self.assertIn('The model provider refused the calls from', report)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('refused the calls', s['stop_reason'])


class ReportTests(RefereeCase):
    def test_main_results_from_coverage_and_computation_checklist(self):
        doubling = line_of(DOUBLING)
        co = [[call('mark_result', result='thm:main', status='no_error_found',
                    how_checked='Checked the induction; recomputed $a_n$ for $n\\le 20$ with sympy.')],
              [call('mark_result', result='lem:step', status='no_error_found', how_checked='Line by line.')],
              [call('report_issue', start_line=doubling, end_line=doubling, quote=DOUBLING, kind='misprint',
                    claim='Doubling should read halving.', evidence='Read.', fix='halving')],
              [call('finish', summary='Checked.')]]
        runner, result = self.run_review(ScriptClient({'lead': ['I stop.'], 'C1': co}))
        report = result['report']
        self.assertIn('| Theorem (Main theorem) | no error found | C1 | Checked the induction;', report)
        checklist = report.split('## What was verified by computation')[1].split('##')[0]
        self.assertIn('Checked by computation, no error found:', checklist)
        self.assertIn('- Theorem (Main theorem) (C1): Checked the induction;', checklist)
        self.assertNotIn('Line by line', checklist)


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
