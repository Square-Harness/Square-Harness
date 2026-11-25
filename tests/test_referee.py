# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The Review v4 without a model: referees' tool loops and judges answered by a script keyed on who is asking."""
import copy
import json
import re
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from mathagent import referee as R
from mathagent import review_policy as P
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

    verdict = 'The paper proves a bound for doubling sequences; its main theorem is checked and the findings are listed below.'

    def __init__(self, rounds, judges=None, batch=None, readers=None):
        self.rounds, self.judges, self.batch, self.readers = rounds, judges or {}, batch or {}, readers or {}
        self.verdict_prompts = []
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
            items = []
            for i in ids:
                verdict = self.batch.get(i, 'confirmed')
                verdict, effect = verdict if isinstance(verdict, tuple) else (verdict, 'misprint')
                items.append({'id': i, 'verdict': verdict, 'effect': effect, 'reason': 'Checked.'})
            text = json.dumps({'items': items})
        elif payload.get('format') == R.VERDICT_SCHEMA:
            self.verdict_prompts.append(messages[-1]['content'])
            text = json.dumps({'paragraph': self.verdict})
        elif payload.get("format") == R.READER_SCHEMA:
            content = messages[-1]['content']
            unit = re.search(r'STATEMENT: ([^\n]+)', content).group(1)
            answer = self.readers.get(unit + (':answer' if R.READER_ANSWER_NOW in content else ''),
                                      {'explanation': 'Read line by line.', 'issues': [], 'verdict': 'no_issue_found',
                                       'external': []})
            if isinstance(answer, tuple):  # (answer text, reasoning): cut at the output limit
                yield {'message': {'content': answer[0], 'thinking': answer[1]}, 'done': True, 'done_reason': 'length',
                       'eval_count': payload['options']['num_predict'], 'prompt_eval_count': 1000}
                return
            text = json.dumps(answer)
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
              [call('finish', summary='Checked the lemma.')],  # time is left: a second look is asked for once
              [call('finish', summary='Checked the lemma again.')]]
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
        self.assertEqual(roles, {'lead', 'co-referee', 'reader', 'judge', 'judge-batch', 'verdict'})
        self.assertTrue(all(c.get('stats') for c in s['calls']))
        # The report: the confirmed error raises the lead's "accept"; the refuted misprint is counted, not printed.
        report = result['report']
        # The recommendation follows the judged findings by rule (the lead said accept).
        self.assertIn('**Recommendation: Major revision.**\n', report)
        self.assertIn('**E1**', report)
        self.assertEqual(s['referees']['C1']['second_look']['on'], 'finish')
        self.assertTrue(any(m['role'] == 'tool' and m['content'].startswith('Not yet. About') and 'second look at the proof of'
                            in m['content'] for p in client.payloads for m in p['messages']))
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
        self.assertIn('**Recommendation: Major revision.** (provisional: a reported error is not settled)', report)
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
        self.assertIn('another report on the same lines stands', lead.execute('withdraw_issue', {'issue_id': 'I2', 'reason': 'Wrong.'}))
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
        self.assertIn('| Theorem (Main theorem) | no error found | C1; reader: no problem found | Checked the induction;', report)
        checklist = report.split('## What was verified by computation')[1].split('##')[0]
        self.assertIn('Checked by computation, no error found:', checklist)
        self.assertIn('- Theorem (Main theorem) (C1): Checked the induction;', checklist)
        self.assertNotIn('Line by line', checklist)


class ReaderTests(RefereeCase):
    """Focused readers (the v3 line-by-line audit) on the main chain's proofs, beside the referees."""

    def test_plan_order_and_slots(self):
        client = ScriptClient({'lead': ['Done.'], 'C1': ['Done.']})
        runner, _ = self.run_review(client, concurrency=1)  # two model slots: the referees hold both at first
        readers = runner.state['readers']
        names = [runner._unit(r['unit'])['name'] for r in readers]
        self.assertEqual(names, ['Theorem (Main theorem)', 'Lemma (lem:step)'])  # the main proof first, then what it uses
        self.assertEqual([r['depth'] for r in readers], [0, 1])
        self.assertEqual({r['status'] for r in readers}, {'done'})
        self.assertLessEqual(readers[0]['started'], readers[1]['started'])
        calls = [c for c in runner.state['calls'] if c['role'] == 'reader']
        self.assertEqual(len(calls), 2)
        prompt = next(p for p in client.payloads if p.get('format') == R.READER_SCHEMA)
        self.assertTrue(prompt['think'])
        self.assertIn('LINE BY LINE', prompt['messages'][-1]['content'])
        with unittest.mock.patch.object(R, 'READERS', 1):
            self.assertEqual(len(runner._reader_plan()), 1)

    def test_reader_issue_in_the_ledger_and_merged_with_a_referee(self):
        base = line_of(BASE_CASE)
        reader = {'explanation': 'The base case is asserted.', 'verdict': 'issues_found', 'external': [], 'issues': [
            {'start_line': base, 'end_line': base, 'quote': BASE_CASE, 'kind': 'invalid_inference', 'effect': 'breaks_proof',
             'evidence': 'Nothing in the paper gives $a_1\\le 2$. The induction cannot start.', 'suggestion': 'Assume it.'}]}

        class Ordered(ScriptClient):  # the judge answers after both reports (either may come first)
            def stream(self, payload):
                if 'THE REPORTED PROBLEM' in json.dumps(payload['messages'][:3]):
                    time.sleep(0.5)
                yield from super().stream(payload)
        lead = [[call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='proof',
                      claim='Why is $a_1\\le 2$?', evidence='Read.')], 'Done.']
        client = Ordered({'lead': lead}, readers={'Theorem (Main theorem)': reader},
                         judges={'I1': {'verdict': 'refuted', 'effect': 'gap', 'reason': 'It is in the definitions.', 'fix': ''}})
        runner, result = self.run_review(client, co_referees=0)
        issue = runner.state['issues'][0]
        uid = runner._resolve_result('thm:main')
        self.assertEqual(sorted(issue['reported_by']), ['R:' + uid, 'lead'])
        self.assertEqual(issue['kind'], 'proof')
        self.assertEqual(issue['status'], 'disputed')  # a reader and a referee: two independent reports
        reading = next(r for r in runner.state['readers'] if r['unit'] == uid)
        self.assertEqual((reading['verdict'], reading['issues']), ('issues_found', ['I1']))
        self.assertIn('the reader of Theorem (Main theorem)', result['report'].split('## Questions to the authors')[1])


class VerdictTests(RefereeCase):
    def test_the_verdict_is_written_after_the_judge_and_checked(self):
        base, doubling = line_of(BASE_CASE), line_of(DOUBLING)
        lead = [[call('report_issue', start_line=base, end_line=base, quote=BASE_CASE, kind='gap',
                      claim='The base case fails for the sequence of the second section.', evidence='Read.')],
                [call('mark_result', result='thm:main', status='issue', how_checked='Read.')],
                [call('finish', summary='The base case fails for the sequence of the second section (I1).',
                      recommendation='major_revision')]]
        judges = {'I1': {'verdict': 'refuted', 'effect': 'gap', 'reason': 'It is in the definitions.', 'fix': '',
                         'fill': '', 'new_ingredient': ''}}
        client = ScriptClient({'lead': lead}, judges=judges)
        client.verdict = 'The main theorem is in doubt: the base case fails for the sequence of the second section.'
        runner, result = self.run_review(client, co_referees=0)
        record = runner.state['verdict_text']
        self.assertEqual(record['by'], 'code')
        self.assertIn('repeats the claim of I1', record['rejected']['why'])
        prompt = client.verdict_prompts[0]
        self.assertNotIn('(I1)', prompt)  # the lead's sentence naming the rejected issue is not passed on
        self.assertIn('NO PROBLEM WAS CONFIRMED', prompt)
        report = result['report']
        self.assertIn('**Recommendation: Accept.**', report)  # by rule: the lead's major revision rested on I1
        self.assertIn('No problem was confirmed.', report)
        self.assertNotIn('second section', report.split('## Raised by a referee')[0])
        c = R.classify(runner)
        self.assertEqual(runner._verdict_problem('I1 is wrong, and the paper is otherwise fine as written.', c), 'names an issue id')
        self.assertIn('E3', runner._verdict_problem('The error E3 decides it; the rest of the paper is fine.', c))
        self.assertIsNone(runner._verdict_problem('The paper proves a bound for doubling sequences; nothing was confirmed.', c))

    def test_a_referee_that_stops_without_finish_is_nudged_once(self):
        client = ScriptClient({'lead': ['I stop here.', 'Still nothing.']})
        runner, _ = self.run_review(client, co_referees=0)
        lead = runner.state['referees']['lead']
        self.assertEqual(lead['status'], 'ended')
        self.assertIn('unit', lead['nudged'])
        rounds = [p for p in client.payloads if p.get('tools') and 'YOUR TASK (lead' in json.dumps(p['messages'][:3])]
        self.assertIn('You stopped without calling finish. About', rounds[-1]['messages'][-1]['content'])
        self.assertEqual(len(rounds), 2)

    def test_batch_judge_reroutes_more_than_a_misprint(self):
        doubling = line_of(DOUBLING)
        co = [[call('report_issue', start_line=doubling, end_line=doubling, quote=DOUBLING, kind='misprint',
                    claim='The "=" should be "\\le": only the inequality holds.', evidence='Read.', fix='\\le')], 'Done.']
        client = ScriptClient({'lead': ['Done.'], 'C1': co}, batch={'I1': ('confirmed', 'imprecision')},
                              judges={'I1': {'verdict': 'confirmed', 'effect': 'imprecision', 'reason': 'Only an inequality holds.',
                                             'fix': '', 'fill': '', 'new_ingredient': ''}})
        runner, result = self.run_review(client)
        issue = runner.state['issues'][0]
        self.assertEqual(issue['rerouted']['effect'], 'imprecision')
        self.assertFalse(issue['judge'].get('batch'))
        self.assertEqual(issue['judge']['effect'], 'imprecision')
        self.assertIn('**G1. ', result['report'])
        judge = next(p for p in client.payloads if 'THE REPORTED PROBLEM (I1' in json.dumps(p['messages']))
        self.assertIn('MORE THAN A MISPRINT (imprecision)', json.dumps(judge['messages']))


CITING = r"""\documentclass{article}
\newtheorem{theorem}{Theorem}
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{remark}[theorem]{Remark}
\begin{document}
\section{Introduction}
\begin{theorem}[Main theorem]\label{thm:a}
For every integer $n\ge 1$ we have $a_n\le 2^n$.
\end{theorem}
\begin{proof}
By Lemma~\ref{lem:b} applied to $n-1$, the bound follows by induction.
\end{proof}
\begin{lemma}\label{lem:b}
For every integer $n\ge 1$ we have $a_{n+1}\le 2a_n$.
\end{lemma}
\begin{proof}
By Theorem~\ref{thm:a} we have $a_n\le 2^n$, and the step follows.
\end{proof}
\begin{remark}
Lemma~\ref{lem:none} gives more, and Theorem~\ref{lem:b} is sharp.
As Theorem 1 shows, the growth is at most exponential; Theorem 8.20 of \cite{GT} is not used.
\end{remark}
\end{document}
"""


class InternalUseTests(RefereeCase):
    def setUp(self):
        super().setUp()
        (self.root / 'paper.tex').write_text(CITING)

    def line(self, text):
        return next(n for n, row in enumerate(CITING.splitlines(), 1) if text in row)

    def test_the_table_of_internal_uses(self):
        runner, _ = self.run_review(ScriptClient({}), co_referees=0)
        rows = {(r['line'], r['label']): r for r in runner.state['internal_uses']}
        in_proof = rows[(self.line('By Lemma~'), 'lem:b')]
        self.assertEqual((in_proof['in'], in_proof['cited_name'], in_proof['forward']), ('proof', 'Lemma 2', True))
        self.assertEqual(in_proof['problem'], 'circular')  # the lemma's proof uses the theorem it serves
        self.assertEqual(rows[(self.line('By Theorem~'), 'thm:a')]['problem'], 'circular')
        self.assertEqual(rows[(self.line('Lemma~\\ref{lem:none}'), 'lem:none')]['problem'], 'unresolved')
        mismatch = rows[(self.line('Lemma~\\ref{lem:none}'), 'lem:b')]
        self.assertEqual((mismatch['in'], mismatch['problem'], mismatch['named']), ('statement', 'kind_mismatch', 'Theorem'))
        written = rows[(self.line('As Theorem 1'), 'Theorem 1')]
        self.assertEqual((written['cited_name'], written['in'], written['problem']), ('Theorem 1', 'statement', None))
        self.assertNotIn((self.line('As Theorem 1'), 'Theorem 8.20'), rows)  # a cited work's theorem is not ours
        # The reader of the theorem's proof gets its row; the lead gets the rest and every flagged row.
        reader_rows = [r['id'] for r in runner.state['internal_uses'] if r.get('reader')]
        self.assertIn(in_proof['id'], reader_rows)
        lead = runner._first_message('lead')
        self.assertIn('INTERNAL CITATIONS FOR YOU', lead)
        self.assertIn('label lem:none resolves to no result', lead)
        self.assertIn('[code: the sentence says Theorem, the label is a lemma]', lead)
        self.assertIn(written['id'] + ',', lead)

    def test_reader_and_lead_checks_reach_the_judge_and_the_report(self):
        step = self.line('By Lemma~')
        reader = {'explanation': 'Read.', 'verdict': 'no_issue_found', 'external': [], 'issues': [],
                  'uses': [{'use': 'X1', 'verdict': 'hypotheses_fail', 'note': 'Lemma 2 needs $n\\ge 1$; it is applied at $n-1=0$.'}]}
        client = ScriptClient({'lead': [[call('check_uses', items=[{'use': 'X3', 'verdict': 'exact', 'note': 'Fine.'}])], 'Done.']},
                              readers={'Theorem 1 (Main theorem)': reader})
        runner, result = self.run_review(client, co_referees=0)
        rows = {r['id']: r for r in runner.state['internal_uses']}
        self.assertEqual(rows['X1']['line'], step)
        self.assertEqual(rows['X1']['check']['verdict'], 'hypotheses_fail')
        self.assertEqual(rows['X3']['check']['by'], 'lead')
        issue = next(i for i in runner.state['issues'] if 'X1' in i.get('internal_uses', []))
        self.assertEqual((issue['kind'], issue['start_line']), ('proof', step))
        judge = next(p for p in client.payloads if f'THE REPORTED PROBLEM ({issue["id"]}' in json.dumps(p['messages']))
        text = json.dumps(judge['messages'])
        self.assertIn('THE CITED RESULT AS STATED (Lemma 2', text)
        self.assertIn('THE CITING LINES (X1', text)
        prompt = next(p for p in client.payloads if p.get('format') == R.READER_SCHEMA)['messages'][-1]['content']
        self.assertIn('USES OF THE PAPER\'S OWN RESULTS IN THESE LINES', prompt)
        self.assertIn('L' + str(self.line('For every integer $n\\ge 1$ we have $a_{n+1}')), prompt)  # the cited statement
        self.assertIn("Uses of the paper's own results: ", result['report'])
        self.assertIn('Lemma 2 (1 of 2)', result['report'])  # the remark's use of Lemma 2 was not checked
        self.assertEqual(runner.state['use_coverage'][runner._resolve_result('lem:b')]['failed'], 1)


GOVERNED = CITING.replace(r"""\end{remark}
\end{document}""", r"""\end{remark}
Unlike in \cite{GT} (see in particular Lemma 2),
the holes here are explicit. We follow the proof of Theorem 1 and Lemma 2 of \cite{GT}.
By Lemma 2 and the estimate in \cite{GT}, the claim holds.
Proposition 2 is used too.
\end{document}""")


class CitationRuleTests(RefereeCase):
    def setUp(self):
        super().setUp()
        (self.root / 'paper.tex').write_text(GOVERNED)

    def line(self, text):
        return next(n for n, row in enumerate(GOVERNED.splitlines(), 1) if text in row)

    def test_written_out_results_of_a_cited_work_are_not_ours(self):
        runner, _ = self.run_review(ScriptClient({}), co_referees=0)
        rows = {(r['line'], r['label']): r for r in runner.state['internal_uses']}
        self.assertNotIn((self.line('see in particular'), 'Lemma 2'), rows)  # "in \cite{GT} (see ... Lemma 2)"
        self.assertNotIn((self.line('We follow'), 'Theorem 1'), rows)  # "Theorem 1 and Lemma 2 of \cite{GT}"
        self.assertNotIn((self.line('We follow'), 'Lemma 2'), rows)
        doubt = rows[(self.line('By Lemma 2 and'), 'Lemma 2')]
        self.assertEqual((doubt['uncertain'], doubt['problem'], doubt['owner']),
                         ('the sentence also cites another work', None, 'lead'))
        other = rows[(self.line('Proposition 2 is used'), 'Proposition 2')]  # this paper's 2 is a lemma
        self.assertIn('is a lemma, not a proposition', other['uncertain'])
        self.assertIsNone(other['problem'])  # uncertain, not a mismatch
        lead = runner._first_message('lead')
        self.assertIn('[code, uncertain: the sentence also cites another work', lead)

    def test_an_issue_at_a_use_checks_it_and_a_not_internal_verdict(self):
        step = self.line('By Lemma~')
        reader = {'explanation': 'Read.', 'verdict': 'issues_found', 'external': [], 'uses': [], 'issues': [
            {'start_line': step, 'end_line': step, 'quote': 'applied to $n-1$', 'kind': 'invalid_inference',
             'effect': 'gap', 'evidence': 'Lemma 2 assumes $n\\ge 1$, and it is applied at $n-1=0$.',
             'suggestion': 'Start at $n=2$.'}]}
        doubt = self.line('By Lemma 2 and')
        client = ScriptClient({'lead': ['Done.']}, readers={'Theorem 1 (Main theorem)': reader})
        runner, result = self.run_review(client, co_referees=0)
        rows = {r['id']: r for r in runner.state['internal_uses']}
        row = next(r for r in rows.values() if r['line'] == step and r['label'] == 'lem:b')
        self.assertEqual(row['check']['verdict'], 'hypotheses_fail')  # the issue is the check
        issue = next(i for i in runner.state['issues'] if i['start_line'] == step)
        self.assertEqual(row['check']['issue'], issue['id'])
        self.assertIn(row['id'], issue['internal_uses'])
        # A pass by someone else does not undo it; not_internal takes a row out of the counts.
        runner._use_check('lead', row['id'], 'exact', 'Fine.')
        self.assertEqual(row['check']['verdict'], 'hypotheses_fail')
        ident = next(r['id'] for r in rows.values() if r['line'] == doubt)
        self.assertIsNone(runner._use_check('lead', ident, 'not_internal', 'Lemma 2 of [GT].'))
        counts = runner._use_counts()[runner._resolve_result('lem:b')]
        self.assertEqual(counts['failed'], 1)
        self.assertEqual(counts['uses'], sum(1 for r in rows.values() if r['cited'] == runner._resolve_result('lem:b')) - 1)

    def test_rows_outside_the_readers_go_to_the_co_referee_of_their_direction(self):
        (self.root / 'paper.tex').write_text(PAPER)
        runner, _ = self.run_review(ScriptClient({}), co_referees=1, reading=False)
        row = next(r for r in runner.state['internal_uses'] if r['label'] == 'lem:step')
        self.assertEqual((row['in'], row['problem'], row['owner']), ('proof', None, 'C1'))
        self.assertIn(row['id'] + ',', runner._first_message('C1'))
        self.assertNotIn(row['id'] + ',', runner._first_message('lead'))
        self.assertIn(row['id'], runner._second_look('C1')[0])


class StatusTests(RefereeCase):
    """The status of the main results is computed through the map's dependencies; the verdict is checked against it."""

    def test_an_error_in_a_used_lemma_reaches_the_main_theorem(self):
        step = line_of(DOUBLING)
        co = [[call('report_issue', start_line=step, end_line=step, quote=DOUBLING, kind='proof',
                    claim='The doubling bound needs $b_n\\le a_n$, which fails for $n=1$.', evidence='Take $b_1=2a_1$.')],
              [call('mark_result', result='lem:step', status='issue', how_checked='Line by line.', issue_ids=['I1'])],
              [call('finish', summary='The lemma fails.')], [call('finish', summary='The lemma fails.')]]
        lead = [[call('mark_result', result='thm:main', status='no_error_found', how_checked='The induction is fine.')],
                [call('finish', summary='The theorem is fine.')]]
        client = ScriptClient({'lead': lead, 'C1': co},
                              judges={'I1': {'verdict': 'confirmed', 'effect': 'breaks_proof', 'reason': 'The bound fails.',
                                             'fix': '', 'fill': '', 'new_ingredient': ''}})
        client.verdict = ('The paper proves a bound for doubling sequences. The main theorem has no error found. '
                          'The lemma it uses has the confirmed error E1.')
        runner, result = self.run_review(client)
        c = R.classify(runner)
        main = c['mains'][0]
        self.assertEqual(main['status'], 'relies_on_error')
        self.assertIn('relies on a result with a confirmed error: Lemma (lem:step) (E1)', main['text'])
        self.assertEqual(c['recommendation'], 'major_revision')
        record = runner.state['verdict_text']
        self.assertEqual(record['by'], 'code')  # the model called the theorem free of error
        self.assertIn('calls Theorem (Main theorem) free of error', record['rejected']['why'])
        self.assertIn('relies on a result with a confirmed error', client.verdict_prompts[0])
        report = result['report']
        self.assertIn('| Theorem (Main theorem) | relies on a result with a confirmed error: Lemma (lem:step) (E1) |', report)
        self.assertIn('Main results: Theorem (Main theorem): relies on', report)
        # The checks on a written verdict.
        self.assertIn('omits the confirmed error(s) E1',
                      runner._verdict_problem('The paper proves a bound for doubling sequences; see the findings.', c))
        flagged = [m for m in c['mains']]
        self.assertEqual(R.clean_claim('The three main results carry the status no error found.', flagged), 'Theorem (Main theorem)')
        self.assertIsNone(R.clean_claim('The main theorem relies on the lemma, which has an error.', flagged))
        self.assertIsNone(R.clean_claim('The main theorem has no error of its own but E1 breaks it.', flagged))

    def test_no_error_found_needs_every_used_proof_checked(self):
        client = ScriptClient({'lead': ['Done.'], 'C1': ['Done.']})
        runner, _ = self.run_review(client, reading=False, judging=False)
        main = R.classify(runner)['mains'][0]
        self.assertEqual(main['status'], 'not_checked')
        self.assertIn('Lemma (lem:step)', main['text'])
        for name in ('thm:main', 'lem:step'):
            runner.state['coverage'][runner._resolve_result(name)] = {'status': 'no_error_found', 'by': 'C1', 'how_checked': 'Read.'}
        self.assertEqual(R.classify(runner)['mains'][0]['status'], 'no_error_found')


class ReaderCutTests(RefereeCase):
    """A focused reading cut at its output cap keeps what it can; one that keeps nothing is named."""
    ISSUE = {'start_line': 0, 'end_line': 0, 'quote': BASE_CASE, 'kind': 'invalid_inference', 'effect': 'breaks_proof',
             'evidence': 'Nothing gives $a_1\\le 2$.', 'suggestion': 'Assume it.'}

    def reading(self, runner):
        return next(r for r in runner.state['readers'] if r['unit'] == runner._resolve_result('thm:main'))

    def test_salvage_of_a_cut_answer(self):
        issue = dict(self.ISSUE, start_line=line_of(BASE_CASE), end_line=line_of(BASE_CASE))
        cut = '{"issues": [' + json.dumps(issue) + ', {"start_line": 3, "quote": "The ba'
        self.assertEqual(R.salvage_answer(cut)['issues'], [issue])
        self.assertIsNone(R.salvage_answer('{"explanation": "I read the first li'))
        client = ScriptClient({'lead': ['Done.']}, readers={'Theorem (Main theorem)': (cut, 'reasoning ' * 50)})
        runner, result = self.run_review(client, co_referees=0)
        reading = self.reading(runner)
        self.assertEqual((reading['status'], reading['recovered'], reading['verdict']), ('done', 'partial', 'issues_found'))
        self.assertEqual(len(reading['issues']), 1)
        self.assertEqual(sum(1 for p in client.payloads if p.get('format') == R.READER_SCHEMA), 2)  # no follow-up call
        self.assertIn('Theorem (Main theorem) (the problems written before the cut)', result['report'])
        self.assertNotEqual(R.classify(runner)['mains'][0]['status'], 'no_error_found')

    def test_answer_from_the_notes_and_nothing_at_all(self):
        notes = 'The base case needs $a_1\\le 2$. ' * 20
        client = ScriptClient({'lead': ['Done.']}, readers={
            'Theorem (Main theorem)': ('', notes),
            'Theorem (Main theorem):answer': {'issues': [], 'uses': [], 'verdict': 'no_issue_found', 'explanation': 'Fine.',
                                              'external': []},
            'Lemma (lem:step)': ('{"issues": [', 'short')})
        runner, result = self.run_review(client, co_referees=0)
        reading = self.reading(runner)
        self.assertEqual((reading['status'], reading['recovered'], reading['verdict']), ('done', 'answer_now', 'uncertain'))
        answer = next(p for p in client.payloads if R.READER_ANSWER_NOW in p['messages'][-1]['content'])
        self.assertFalse(answer['think'])
        self.assertIn(notes[:60], answer['messages'][-1]['content'])
        self.assertEqual(answer['options']['num_predict'], R.READER_ANSWER_CAP)
        lemma = next(r for r in runner.state['readers'] if r['unit'] == runner._resolve_result('lem:step'))
        self.assertEqual(lemma['status'], 'unusable')
        self.assertIn('Focused readings not done: Lemma (lem:step) (no usable answer, cut at the output limit)', result['report'])
        self.assertIn('an answer written from its notes', result['report'])
        main = R.classify(runner)['mains'][0]
        self.assertEqual(main['status'], 'not_checked')
        self.assertIn('Lemma (lem:step)', main['text'])
        first = next(p for p in client.payloads if p.get('format') == R.READER_SCHEMA)
        self.assertEqual(first['options']['num_predict'], R.READER_CAP)
        self.assertIn(R.READER_BOUND, first['messages'][-1]['content'])


class BudgetTests(RefereeCase):
    def test_the_pool_charges_the_providers_input_count(self):
        """About 110 calls of about 90k real input tokens, each reserved at a byte count 3.4 times larger."""
        from mathagent.assistant_budget import BudgetClient

        class Provider:
            timeout = 60
            in_flight = []

            def count_input_tokens(self, payload):
                return 306_000

            def stream(self, payload):
                self.in_flight.append(tree.pool.state['input_tokens'])
                yield {'message': {'content': 'ok'}, 'done': True, 'done_reason': 'stop', 'eval_count': 2000,
                       'prompt_eval_count': 90_000}
        limit = 33_554_432
        tree = BudgetClient(Provider(), max_tokens=12_000_000, max_input_tokens=limit, max_seconds=10_000)
        usage = {'tokens': 0, 'input_tokens': 0}
        review = tree.fork(Provider(), allowance=None, usage=usage, input_allowance=limit)
        payload = {'model': 'm', 'messages': [{'role': 'user', 'content': 'x'}],
                   'options': {'num_predict': 32_000, 'num_ctx': 1_000_000}}
        for n in range(110):
            list(review.stream(copy.deepcopy(payload)))
            if n == 0:
                self.assertEqual(Provider.in_flight[0], 306_000)  # the reservation holds only while the call runs
        self.assertEqual(tree.pool.state['input_tokens'], 110 * 90_000)
        self.assertEqual(usage['input_tokens'], 110 * 90_000)
        self.assertLess(tree.pool.state['input_tokens'], limit / 3)
        self.assertEqual(review.remaining_input_tokens, limit - 110 * 90_000)

    def test_referees_and_readers_stop_before_the_judges(self):
        runner, _ = self.run_review(ScriptClient({'lead': ['Done.']}), co_referees=0)

        class Short:
            remaining_tokens = 10_000_000
            remaining_input_tokens = 350_000
        runner.agent.client = Short()
        with self.assertRaises(R.RefereeStop):
            runner._admit_call('reader', 32_000, 150_000)
        with self.assertRaises(R.RefereeStop):
            runner._admit_call('co-referee', 8_000, 150_000)
        runner._admit_call('judge', 40_000, 150_000)  # the judges and the verdict keep the rest
        runner._admit_call('verdict', 2_500, 150_000)
        runner._admit_call('reader', 32_000, 100_000)  # 350k - 100k still leaves the share of two judgments


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
