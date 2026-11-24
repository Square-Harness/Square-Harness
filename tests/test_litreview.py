# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Offline literature-review regressions using synthetic primary passages."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.assistant_budget import BudgetClient
from mathagent.backends import MeasuredStreamCancellation
from mathagent.deepinfra import DeepInfraGenerationError, DeepInfraRateLimit, TesterAllowanceInsufficient
from mathagent.literature import LiteratureTools
from mathagent.litreview import ReviewRunner, _cutoff_supported
from mathagent.proof import count_input
from mathagent.tools import Workspace
from tests.test_research import FakeClient, response


class LiteratureReviewTests(unittest.TestCase):
    def test_parallel_screen_failure_defers_only_affected_candidates_and_splits_the_retry(self):
        client = FakeClient([])
        runner = ReviewRunner(Agent(client, Workspace(self.root)), concurrency=2)
        groups = []
        def stream(payload):
            candidates = payload['messages'][-1]['content'].split('CANDIDATES (', 1)[1]
            ids = [int(n) for n in re.findall(r'^#?(\d+)\s*\|', candidates, re.M)]
            groups.append(ids)
            if ids == list(range(1, 33)):
                raise DeepInfraGenerationError('Model generation failed.', category='server_error', confirmed_terminal=True)
            yield from response(json.dumps({'items': [
                {'n': n, 'kind': 'result', 'relevance': 3, 'facet': 0} for n in ids]}))
        def step(phase):
            if phase == 'plan':
                for n in range(1, 46):
                    runner._record({'title': f'Example result {n}', 'doi': f'10.5555/screen.{n}'}, 'crossref', '')
                runner.state.update(phase='screen', scope={'topic': 'test', 'facets': [], 'setting': ''})
            else:
                runner._screen()
        pending = lambda: [n for n in range(1, 46) if str(n) not in runner.state['screened']
                           and str(n) not in runner.state['screen_failed']]
        with patch.object(client, 'stream', side_effect=stream), patch.object(runner, '_step', side_effect=step), \
                patch.object(runner, '_pending_screen', side_effect=pending), patch.object(runner, '_after_screen', return_value='done'):
            result = runner.start('Review the example results.', max_tokens=40000)
        self.assertEqual(runner.state['phase'], 'done', result.get('stop_reason'))
        self.assertFalse(runner.state['screen_failed'])
        self.assertEqual(len(runner.state['screened']), 45)
        self.assertEqual(sorted(groups), sorted([list(range(1, 33)), list(range(33, 46)), list(range(1, 11)),
                                                 list(range(11, 21)), list(range(21, 31)), [31, 32]]))
        failed, = [row for row in runner.state['calls'] if row['status'] == 'generation_failed']
        self.assertEqual((failed['units'], failed['stream_progress']), (list(range(1, 33)), {}))
        self.assertEqual(runner.state['tokens_charged'], failed['reserved_tokens'] + 5 * 20)

    def test_outline_failure_and_interrupted_check_still_deliver_only_source_checked_prose(self):
        fact = 'The observation time is strictly positive for an exterior strip at positive distance from the singular line.'
        unsupported = 'All bounded domains have the same numerical threshold.'
        located = {'verdicts': [
            {'unit': 1, 'reading': 'Positive time for an exterior strip.', 'verdict': 'supported',
             'support': [{'source': 1, 'condition': 'E1', 'conclusion': 'E1'}]},
            {'unit': 2, 'reading': 'nothing', 'verdict': 'unsupported', 'support': [],
             'problem': 'No numerical statement in the paper.'}]}
        compared = {'checks': [{'unit': 1, 'faithful': True}]}
        measured = DeepInfraGenerationError('Stream interrupted.', category='interrupted',
                                            stats={'eval_count': 12, 'prompt_eval_count': 130})
        empty = DeepInfraGenerationError('Model generation failed.', category='server_error', confirmed_terminal=True,
                                        stats={'eval_count': 0, 'prompt_eval_count': 130})
        transport = FakeClient([[measured], response(fact[:-1] + ' [@1].\n\n' + unsupported[:-1] + ' [@1].'),
                                [empty], response(json.dumps(located)), response(json.dumps(compared))])
        runner = ReviewRunner(Agent(transport, Workspace(self.root)))
        default_step = runner._step
        def step(phase):
            if phase == 'plan':
                runner._record({'title': 'Time for exterior strips', 'authors': ['A. Author'],
                                'year': 2020, 'doi': '10.5555/strip'}, 'crossref', 'fixture')
                runner.state.update(phase='outline', selected=[1], outline_read_done=True,
                                    scope={'topic': 'Observation timing', 'title': 'Observation timing',
                                           'facets': [], 'terms': [], 'keywords': []},
                                    cards={'1': {'mode': 'primary_passages', 'passages': {'L4-L4': fact}}},
                                    screened={'1': {'relevance': 3, 'kind': 'core', 'facet': -1}})
            else:
                default_step(phase)
        with patch.object(runner, '_step', side_effect=step):
            result = runner.start('Survey observation timing.', max_tokens=40000)
        self.assertEqual(result['status'], 'partial', result.get('stop_reason'))
        self.assertTrue(runner.state['outline_fallback'])
        self.assertTrue(runner.state['checks']['0']['ran'])
        markdown = (runner.directory / 'review.md').read_text()
        self.assertIn(fact[:-1] + ' [1].', markdown)
        self.assertNotIn(unsupported[:-1], markdown)
        self.assertEqual([(row['role'], row['status']) for row in runner.state['calls']],
                         [('outline', 'generation_failed'), ('write', 'complete'), ('check', 'generation_failed'),
                          ('check', 'complete'), ('verify', 'complete')])
        self.assertEqual((runner.state['tokens_charged'], runner.state['input_tokens_charged']), (72, 560))

    def test_outage_stops_before_a_fourth_unknown_charge_and_resume_keeps_the_failed_units(self):
        outage = DeepInfraGenerationError('Provider ended the stream.', category='server_error', confirmed_terminal=True)
        transport = FakeClient([[outage]] * 3 + [response(str(n)) for n in range(4)])
        runner = ReviewRunner(Agent(transport, Workspace(self.root)))
        def step(phase):
            if phase == 'plan':
                runner.state.update(phase='read', scope={'topic': 'Observation', 'keywords': []})
                return
            pending = [n for n in range(4) if str(n) not in runner.state['cards']]
            pending.sort(key=lambda n: runner._failure_count('card', n))
            if not pending:
                runner.state['phase'] = 'done'
                return
            n = pending[0]
            def read():
                result = runner._ask('card', 'Read work ' + str(n), cap=1000, units=[n])
                runner.state['cards'][str(n)] = {'summary': result['text']}
            runner._wave([(n, read)], lambda _, reason: self.fail(reason))
        with patch.object(runner, '_step', side_effect=step):
            paused = runner.start('Review the records.', max_tokens=6000)
            self.assertEqual(paused['status'], 'paused')
            self.assertEqual(paused['worker_error']['code'], 'provider_generation_failed')
            self.assertEqual(runner.state['tokens_charged'], 3000)
            self.assertFalse(runner.state['card_failed'])
            self.assertEqual([call['units'] for call in runner.state['calls']], [[0], [1], [2]])
            resumed = runner.resume(paused['id'])
        self.assertEqual(resumed['id'], paused['id'])
        self.assertEqual(runner.state['phase'], 'done')
        self.assertEqual([call['units'] for call in runner.state['calls'][3:]], [[3], [0], [1], [2]])
        self.assertEqual(runner.state['tokens_charged'], 3080)

    def test_critical_scope_is_retried_only_once_and_an_account_error_stops_immediately(self):
        for category, tries in [('server_error', 2), ('quota', 1)]:
            with self.subTest(category=category):
                error = DeepInfraGenerationError('The provider stopped generation.', category=category, confirmed_terminal=True)
                runner = ReviewRunner(Agent(FakeClient([[error]] * 2), Workspace(self.root)))
                result = runner.start('Review observation times.', max_tokens=20000)
                self.assertEqual(result['status'], 'paused')
                self.assertEqual(result['worker_error']['details'], {'phase': 'scope', 'provider_category': category})
                self.assertEqual(len(runner.state['calls']), tries)
                self.assertIsNone(runner.state['scope'])
        # The phase still has ample working time, but the original provider
        # request window has elapsed when the first terminal error arrives.
        client = FakeClient([])
        client.profile, client.reasoning_effort, client.timeout = 'deepinfra', 'none', 5
        runner = ReviewRunner(Agent(client, Workspace(self.root)))
        with patch('mathagent.litreview.time.monotonic', return_value=100) as clock:
            def late_failure(payload):
                clock.return_value = 106
                raise DeepInfraGenerationError('Provider ended the stream.', category='server_error', confirmed_terminal=True)
            with patch.object(client, 'stream', side_effect=late_failure) as requests:
                stopped = runner.start('Review observation times.', max_tokens=20000)
        self.assertEqual((stopped['status'], requests.call_count), ('paused', 1))

    def test_local_allowance_refusal_and_measured_cancellation_pause_without_an_automatic_retry(self):
        cases = [(TesterAllowanceInsufficient(), 0, 0), (MeasuredStreamCancellation({'eval_count': 18, 'prompt_eval_count': 70}), 18, 70)]
        for error, output, incoming in cases:
            with self.subTest(error=type(error).__name__):
                client = FakeClient([[error]])
                budget = BudgetClient(client, max_tokens=20000, max_input_tokens=20000)
                runner = ReviewRunner(Agent(budget, Workspace(self.root)))
                result = runner.start('Survey controllability.', max_tokens=20000)
                self.assertEqual(result['status'], 'paused')
                self.assertEqual(len(client.requests), 1)
                self.assertEqual((runner.state['tokens_charged'], runner.state['input_tokens_charged']), (output, incoming))
                self.assertFalse(runner.state.get('generation_failures'))
                if isinstance(error, TesterAllowanceInsufficient):
                    self.assertEqual(result['worker_error']['scope'], 'account')
                    self.assertEqual(runner.state['calls'][0]['rejection_reason'], 'tester_allowance_insufficient')
                    self.assertEqual((budget.snapshot()['tokens'], budget.snapshot()['input_tokens']), (0, 0))

    def test_provider_rejection_pauses_the_wave_and_resume_keeps_completed_work(self):
        rejection = DeepInfraRateLimit('The model is temporarily at capacity.', completion_rejected=True)
        transport = FakeClient([response('first'), [rejection], response('second'), response('third')])
        budget = BudgetClient(transport, max_tokens=5000, max_input_tokens=50000)
        runner = ReviewRunner(Agent(budget, Workspace(self.root)), concurrency=1)

        def step(phase):
            state = runner.state
            if phase == 'plan':
                state.update(phase='read', scope={'keywords': ['observation']})
                return
            def read(n):
                item = runner._ask('card', str(n), cap=1800)
                state['notes'].append({'round': n, 'text': item['text'], 'complete': True})
            pending = [n for n in range(1, 4) if not any(note['round'] == n for note in state['notes'])]
            runner._wave([(n, lambda n=n: read(n)) for n in pending], lambda n, reason: self.fail(reason))
            state['phase'] = 'done'

        with patch.object(runner, '_step', side_effect=step):
            paused = runner.start('Review observation.', max_tokens=5000)
            self.assertEqual(paused['status'], 'paused')
            self.assertEqual(paused['worker_error']['code'], 'provider_rate_limit')
            self.assertEqual([note['text'] for note in runner.state['notes']], ['first'])
            self.assertEqual([call['status'] for call in runner.state['calls']], ['complete', 'rejected'])
            rejected = runner.state['calls'][-1]
            self.assertEqual((rejected['charged_tokens'], rejected['charged_input_tokens']), (0, 0))
            self.assertEqual((runner.state['tokens_charged'], runner.state['input_tokens_charged']), (20, 100))
            self.assertEqual(budget.snapshot()['tokens'], 20)
            # The pool charges the provider's input count once a call ends, as the job does (not the reservation).
            self.assertLess(100, count_input(transport, transport.requests[0])[0])
            self.assertEqual(budget.snapshot()['input_tokens'], 100)
            self.assertTrue((runner.directory / 'review.md').is_file())
            resumed = runner.resume(paused['id'])
        self.assertEqual(resumed['id'], paused['id'])
        self.assertEqual([note['text'] for note in runner.state['notes']], ['first', 'second', 'third'])
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(budget.snapshot()['tokens'], 60)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runner = ReviewRunner(Agent(FakeClient([]), Workspace(self.root)))
        self.runner.directory = self.root
        self.runner.state = {**self.runner._extra_state(), 'goal': 'What is the observation time?',
                             'warnings': [], 'selected': [1, 2]}
        for index, title in enumerate(('Observation from a strip', 'Observation on a cylinder'), 1):
            self.runner._record({'title': title, 'authors': ['A. Author'], 'year': 2020,
                                 'doi': f'10.5555/fixture.{index}'}, 'crossref', 'fixture')

    def primary(self, n, passages):
        self.runner.state['cards'][str(n)] = {'mode': 'primary_passages', 'passages': passages}

    def check(self, i, verdicts):
        """Check a section as the check phase does: locate (and ask once more for a sentence left without
        an answer), compare, settle. `verdicts` scripts the locating referee by sentence number; the
        comparison confirms every sentence it is given."""
        state = self.runner.state
        state.setdefault('settings', {'max_rounds': 1})
        state.setdefault('tokens_charged', 0)

        def ask(role, prompt, *, units=(), **options):
            numbers = [int(unit.split(':')[1]) for unit in units]
            if role == 'verify':
                return {'complete': True, 'value': {'checks': [{'unit': j, 'faithful': True} for j in numbers]}}
            return {'complete': True, 'value': {'verdicts': [dict(verdicts[j], unit=j) for j in numbers if j in verdicts]}}
        frame = self.runner._check_frame(i)
        with patch.object(self.runner, '_ask', side_effect=ask):
            for _ in range(2):
                for _, job in self.runner._locating_jobs({i: frame}):
                    job()
            for _, job in self.runner._verifying_jobs({i: frame}):
                job()
        self.runner._settle_check(i, frame)

    def test_assigned_cached_primary_survives_exhausted_online_budget(self):
        state = self.runner.state
        lit = self.runner.agent.workspace.literature = LiteratureTools(self.root, online=True, max_requests=0)
        state['pool'][0]['arxiv'] = '2301.00001'
        state['pool'][1]['arxiv'] = '2301.00002'
        body = (b'<html><title>Observation from a strip</title><div class="ltx_theorem"><p>Theorem 2.1.</p>'
                b'<p>An exterior observation strip requires a strictly positive time.</p>'
                b'<p>Proof.</p></div></html>')
        with patch.object(lit, '_fetch', return_value=(body, 'text/html', 'https://arxiv.org/html/2301.00001')):
            lit.open_paper('arxiv:2301.00001')
        state.update(settings={'max_rounds': 1, 'max_seconds': 300}, scope={'keywords': ['observation']},
                     outline={'sections': [{'works': [1, 2]}]}, outline_read_attempts=[2])
        state['cards'] = {str(n): self.runner._record_card(n) for n in (1, 2)}
        with patch.object(lit, '_fetch', side_effect=AssertionError('Unexpected network')), \
                patch.object(self.runner, '_save'), patch.object(self.runner, '_ask', side_effect=AssertionError):
            self.runner._read_outline_sources()
        self.assertEqual(state['cards']['1']['mode'], 'primary_passages')
        self.assertIn('Theorem 2.1', state['cards']['1']['primary_locators'].values())
        self.assertFalse(state['cards']['2']['passages'])
        self.assertEqual(state['outline_read_attempts'], [2])
        self.assertEqual(lit.stats['requests'], 0)

    def test_evidence_ids_are_original_and_cannot_cross_papers_or_theorems(self):
        condition = 'Suppose the strip meets the singular line.'
        conclusion = 'Observability holds in every positive time.'
        other = 'There is a strictly positive minimal observation time.'
        self.primary(1, {'L4-L6': condition + ' ' + conclusion, 'L20-L21': other})
        self.primary(2, {'L3-L3': conclusion})  # Intentionally identical words in two papers.
        self.runner.state['cards']['1']['summary'] = 'Unsupported paraphrase.'
        spans = {}
        packet = self.runner._pack([1, 2], sources_only=True, source_spans=spans)
        self.assertNotIn('Unsupported paraphrase', packet)
        ids = {value: key for key, value in spans.items()}
        valid = {'units': [1], 'source': 1, 'condition': ids[1, 'L4-L6', condition],
                 'conclusion': ids[1, 'L4-L6', conclusion]}
        support = self.runner._support([valid], [conclusion], [1, 2], source_spans=spans)
        self.assertEqual(support[1][1], {'condition': condition, 'conclusion': conclusion})
        for address in [(2, 'L3-L3', conclusion), (1, 'L20-L21', other), (1, 'L4-L6', condition)]:
            with self.subTest(address=address):
                wrong = {**valid, 'conclusion': ids[address]}
                self.assertFalse(self.runner._support([wrong], [conclusion], [1, 2], source_spans=spans))

    def test_specialized_cutoff_uses_the_exact_affine_bound(self):
        theorem = r'For six points the criterion is $m\geq\frac54 r-\frac{5}{12}$.'
        source = [r'The criterion is \(m\geq\frac{5}{4}r-\frac{5}{12}\).']
        for cutoff, valid in [('2', False), ('25/12', True), ('3', False)]:
            with self.subTest(cutoff=cutoff):
                claim = f'At $r=2$ this requires $m ≥ {cutoff}$.'
                self.assertEqual(_cutoff_supported(claim, theorem, source), valid)
        integer = 'For integer $m$, ' + theorem
        self.assertTrue(_cutoff_supported('At $r=2$ this requires $m>=3$.', integer, source))
        self.assertFalse(_cutoff_supported('At $r=2$ this requires $m>3$.', integer, source))
        self.assertFalse(_cutoff_supported('At $r=2$ this requires $m>=3$.', '', source))
        ambiguous = '$m>=5/4r-5/12$'
        self.assertFalse(_cutoff_supported('At $r=2$ this requires $m>=25/12$.', ambiguous, [ambiguous]))
        for product, fused in [(r'2\cdot3', '23'), (r'2\times3', '23'), (r'\frac{1}{2}3', '1/23')]:
            with self.subTest(product=product):
                malformed = f'$m>={product}r$'
                self.assertFalse(_cutoff_supported(f'At $r=1$ this gives $m>={fused}$.', malformed, [malformed]))
                self.assertFalse(_cutoff_supported(f'At $r=1$ this gives $m>={product}$.', '', []))
        explicit = ['For $r=2$, the result gives $m>=3$.']
        self.assertTrue(_cutoff_supported('At $r=2$ this requires $m>=3$.', '', explicit))
        self.assertFalse(_cutoff_supported('At $r=2$ this requires $m>=2$.', theorem, explicit))

    def test_displaced_pdf_fraction_cannot_certify_a_numerical_claim(self):
        condition = 'For an exterior strip'
        conclusion = 'there is a strictly positive minimal observation time.'
        passage = condition + ', ' + conclusion
        self.primary(1, {'L9-L12': passage + ' The minimal 2 time is given below.'})
        self.runner.state['documents']['1'] = {'extractor': 'pdftotext -layout'}
        units = [passage, r'The minimal time is $2/a^2$ [@1].']
        verdict = {'source': 1, 'units': [1, 2], 'condition': condition, 'conclusion': conclusion}
        self.assertEqual(set(self.runner._support([verdict], units, [1])), {1})
        # A legible, independently anchored formula in the same PDF can still be used.
        clean = 'the minimal time is $a^2/2$.'
        self.runner.state['cards']['1']['passages']['L25-L26'] = condition + ', ' + clean
        self.assertIn(1, self.runner._support([{**verdict, 'units': [1], 'conclusion': clean}],
                                              [condition + ', ' + clean], [1]))

    def test_abstract_completion_inherits_the_scope_of_the_prior_result(self):
        completion = 'We establish the conjecture for all remaining cases.'
        anchor = {'condition': completion, 'conclusion': completion}
        claim = 'The inequality holds in every dimension [@1].'
        for qualified, restated in [('stable', 'in its stable form'), ('eventual', 'for all sufficiently large indices'),
                                    ('asymptotic', 'in the limit'), ('almost-sure', 'with probability one'),
                                    ('conditional', 'under the hypothesis of independence')]:
            with self.subTest(qualification=qualified):
                self.runner.state['pool'][0]['abstract'] = f'Earlier work proved the {qualified} inequality. ' + completion
                self.assertFalse(self.runner._scope_preserved(1, anchor, claim, []))
                self.assertTrue(self.runner._scope_preserved(1, anchor, f'The inequality holds {restated}.', []))
                self.assertTrue(self.runner._scope_preserved(1, anchor, claim, [f'We work {restated}.']))
        independent = 'The spectrum is real in every dimension.'
        self.runner.state['pool'][0]['abstract'] = 'Earlier work proved the stable inequality. ' + independent
        self.assertTrue(self.runner._scope_preserved(1, {'condition': independent, 'conclusion': independent},
                                                    independent, []))
        unconditional = 'We prove the remaining cases unconditionally.'
        self.runner.state['pool'][0]['abstract'] = 'The previous proof was conditional. ' + unconditional
        self.assertTrue(self.runner._scope_preserved(1, {'condition': unconditional, 'conclusion': unconditional},
                                                    claim, []))

    def test_complete_response_can_leave_most_assertions_unjudged(self):
        fact = 'The observation time is positive for an exterior strip.'
        self.primary(1, {'L3-L3': fact})
        state = self.runner.state
        state['settings'] = {'max_rounds': 1}
        state['outline'] = {'title': 'Observation time', 'sections': [
            {'heading': 'Threshold', 'kind': 'results', 'works': [1]}]}
        claims = [f'The estimate holds with constant {n} [@1].' for n in range(1, 24)]
        # Plain notation and table headings need no verdict. The numerical data row does.
        draft = '\n\n'.join(['Let $C$ denote the constant.', fact[:-1] + ' [@1].', *claims,
                            '| Estimate | Value |\n| --- | --- |\n| Bound | $C=24$ [@1] |'])
        units = [p for p in self.runner._assertion_units(draft) if p.strip()]
        good = {2: {'verdict': 'supported', 'support': [{'source': 1, 'condition': 'E1', 'conclusion': 'E1'}]}}
        rejected = {'verdict': 'unsupported', 'support': [], 'problem': 'No numerical constant in the paper.'}
        every = {**good, **{j: rejected for j in [*range(3, 26), len(units)]}}
        # A referee can return fewer verdicts than sentences, twice: the others stay unjudged.
        for verdicts, judged in [(good, 1), (every, 25)]:
            with self.subTest(judged=judged):
                state['sections'] = {'0': {'text': draft}}
                self.check(0, verdicts)
                check = state['checks']['0']
                self.assertEqual((check['judged_units'], check['substantive_units']), (judged, 25))
                self.assertEqual(check['ran'], judged == 25)
                self.assertEqual(check['withheld'], 24)
                self.assertTrue(check['supported_only'])
                self.runner._assemble()
                self.runner._finish()
                self.assertIn(fact[:-1] + ' [1].', state['draft'])
                self.assertNotIn('$C=24$', state['draft'])
                self.assertEqual(state['status'], 'partial')
                self.assertEqual('some section checks were incomplete' in state['citation_issues'], judged < 25)

    def test_recovery_checks_a_matching_writer_citation_before_publication(self):
        fact = 'For an exterior observation strip, the minimal observation time is strictly positive'
        state = self.runner.state
        state['settings'] = {'max_seconds': None, 'max_tokens': None}
        state['scope'] = {'topic': 'Observation time'}
        state['outline'] = {'title': 'Threshold', 'answer_works': [1], 'sections': [
            {'heading': 'Strip', 'kind': 'results', 'works': [1]}]}
        tag = 'L6-L6'
        self.primary(1, {tag: 'Theorem 2. ' + fact + '.'})
        state['cards']['1']['primary_locators'] = {tag: 'Theorem 2'}
        for citation, accepted in [('@1, Theorem 2', True), ('@1', True),
                                   ('@1, Theorem 4', False), ('@2', False)]:
            with self.subTest(citation=citation):
                state['sections'] = {'0': {'text': '', 'previous': 'A claim [@1, Theorem 2].'}}
                state['checks'] = {'0': {'withheld': 1}}
                state.pop('core_recovery', None)
                row = {'source': 1, 'condition': tag, 'conclusion': tag, 'text': f'{fact} [{citation}].'}
                decisions = [{'source': 1, 'supported': True, 'reason': 'Theorem 2 gives this strict inequality.'}]
                replies = [{'complete': True, 'value': {'statements': [row]}},
                           {'complete': True, 'value': {'decisions': decisions}}]
                with patch.object(self.runner, '_save'), patch.object(self.runner, '_late', return_value=False), \
                        patch.object(self.runner, '_tokens_left', return_value=20000), \
                        patch.object(self.runner, '_ask', side_effect=replies) as ask:
                    self.runner._recover_core_answer()
                self.assertEqual(ask.call_count, 2 if accepted else 1)
                self.assertEqual(state['sections']['0']['text'], fact + ' [@1, Theorem 2].' if accepted else '')

    def test_recovery_normalizes_only_unambiguous_subranges_and_matching_citations(self):
        state = self.runner.state
        state.update(settings={'max_seconds': None, 'max_tokens': None}, scope={'topic': 'Observation time'},
                     outline={'answer_works': [1], 'sections': [{'heading': 'Threshold', 'works': [1]}]})
        theorem, setup = 'L7-L14', 'L11-L16'
        fact = 'the estimate for a positive weight holds in every positive time.'
        self.primary(1, {theorem: 'Theorem 2.1. For a strictly positive weight, ' + fact,
                         setup: 'The weight is strictly positive on the given interval.'})
        state['cards']['1'].update(primary_locators={theorem: 'Theorem 2.1'}, primary_setup=setup)
        prefix = "In {@1}'s Theorem 2.1 [@1, L7-L14], "
        cases = [
            ('L7-L8', 'L9-L10', prefix, True),
            ('L7-L8', 'L12-L13', prefix, False),  # Ambiguous between result and setup.
            ('L7-L8', 'L7-L16', prefix, False),   # Crosses their bounds.
            ('L7-L8', 'L15-L16', prefix, False), # Only a setup block contains the conclusion.
            ('L8-L7', 'L9-L10', prefix, False),
            ('L1-L2', 'L9-L10', prefix, False),
            ('L7-L8', 'L9-L10', prefix.replace('{@1}', '{@2}'), False),
            ('L7-L8', 'L9-L10', prefix.replace('[@1,', '[@2,'), False),
            ('L7-L8', 'L9-L10', prefix.replace('Theorem 2.1', 'Theorem 2.2'), False),
            ('L7-L8', 'L9-L10', prefix.replace('L7-L14', 'L7-L20'), False),
        ]
        for condition, conclusion, intro, expected in cases:
            with self.subTest(condition=condition, conclusion=conclusion, intro=intro):
                state.pop('core_recovery', None)
                state['sections'] = {'0': {'text': '', 'previous': 'The estimate [@1, Theorem 2.1].'}}
                state['checks'] = {'0': {'withheld': 1}}
                row = {'source': 1, 'condition': condition, 'conclusion': conclusion, 'text': intro + fact}
                replies = [{'complete': True, 'value': {'statements': [row]}}, {'complete': True, 'value': {
                    'decisions': [{'source': 1, 'supported': True, 'reason': 'The primary theorem gives the estimate.'}]}}]
                with patch.object(self.runner, '_save'), patch.object(self.runner, '_late', return_value=False), \
                        patch.object(self.runner, '_tokens_left', return_value=10000), \
                        patch.object(self.runner, '_ask', side_effect=replies) as ask:
                    self.runner._recover_core_answer()
                self.assertEqual(ask.call_count, 2 if expected else 1)
                if expected:
                    accepted = state['core_recovery']['accepted'][0]
                    self.assertEqual((accepted['condition'], accepted['conclusion']), (theorem, theorem))
                    self.assertEqual(accepted['passages'], {theorem: state['cards']['1']['passages'][theorem]})
                    self.assertIn("Author's Theorem 2.1, " + fact, ask.call_args.args[1])
                else:
                    self.assertFalse(state['sections']['0']['text'])

    def test_editorially_removed_named_result_requires_its_own_setup_and_new_review(self):
        state = self.runner.state
        state.update(settings={'max_seconds': None, 'max_tokens': None}, scope={'topic': 'Observation time'},
                     outline={'answer_works': [], 'sections': [{'heading': 'The criterion', 'works': [1, 2]}]})
        setup, theorem, neighbor = 'L1-L2', 'L7-L9', 'L13-L15'
        statement = 'For a bounded interval and a positive weight, the weighted estimate holds in positive time.'
        self.primary(1, {setup: 'Fix a bounded interval with a positive weight.',
                         theorem: 'Theorem 2.1. The weighted estimate holds in positive time.',
                         neighbor: 'Proposition 2.2. A lower bound also holds.'})
        state['cards']['1'].update(primary_setup=setup, primary_locators={theorem: 'Theorem 2.1', neighbor: 'Proposition 2.2'})
        retained = 'A lower bound also holds [@1, Proposition 2.2].'
        removed = 'The estimate holds for the undefined operator [@1, Theorem 2.1].'
        state['sections'] = {'0': {'text': retained, 'before_editorial_check': removed + '\n\n' + retained}}
        state['checks'] = {'0': {'ran': True, 'revised': True}}
        omission = {'paragraph': 1, 'section': 0, 'reason': 'undefined', 'evidence': 'The weight is not defined.'}
        state['editorial_check'] = {'complete': True, 'omitted': [omission]}
        self.assertEqual(self.runner._recovery_sources()[1]['results'], [theorem])
        for reason in ('duplicate', 'contradiction', 'lost_scope'):
            omission['reason'] = reason
            self.assertFalse(self.runner._recovery_sources())
        omission['reason'] = 'undefined'
        state['sections']['0']['text'] += ' A retained statement [@1, Theorem 2.1].'
        self.assertFalse(self.runner._recovery_sources())
        state['sections']['0']['text'] = retained
        replies = [{'complete': True, 'value': {'statements': [
            {'source': 1, 'condition': setup, 'conclusion': theorem, 'text': statement}]}},
            {'complete': True, 'value': {'decisions': [
                {'source': 1, 'supported': True, 'reason': 'The condition and conclusion match the source.'}]}}]
        with patch.object(self.runner, '_save'), patch.object(self.runner, '_late', return_value=False), \
                patch.object(self.runner, '_tokens_left', return_value=10000), \
                patch.object(self.runner, '_ask', side_effect=replies) as ask:
            self.runner._recover_core_answer()
            self.runner._recover_core_answer()  # A resumed job cannot publish twice.
        self.assertEqual(ask.call_count, 2)
        for call in ask.call_args_list:
            self.assertIn('Fix a bounded interval with a positive weight.', call.args[1])
            self.assertNotIn(removed, call.args[1])
        self.assertIn(retained, ask.call_args.args[1])
        self.assertEqual(state['sections']['0']['text'], statement[:-1] + ' [@1, Theorem 2.1].\n\n' + retained)

    def test_recovery_and_normal_check_preserve_strict_threshold_logic(self):
        # Separate open sides leave the endpoint undetermined. Reverse one comparison
        # and vary the TeX spelling of the subscript, as independently authored prose does.
        passage = (r'Theorem 2. For $s_{c}<s$, the estimate holds; '
                   r'for $s<s_{c}$, the estimate fails.')
        target = r'For this family on the bounded interval, the estimate holds exactly for $s>s_c$.'
        tag = 'L4-L8'
        self.primary(1, {tag: passage})
        state = self.runner.state
        state['settings'] = {'max_seconds': None, 'max_tokens': None}
        state['scope'] = {'topic': 'Observation time'}
        state['outline'] = {'title': 'Threshold', 'answer_works': [1], 'sections': [
            {'heading': 'Threshold', 'kind': 'results', 'works': [1]}]}
        state['cards']['1']['primary_locators'] = {tag: 'Theorem 2'}
        state['sections'] = {'0': {'text': '', 'previous': 'A claim [@1, Theorem 2].'}}
        state['checks'] = {'0': {'withheld': 1}}
        row = {'source': 1, 'condition': tag, 'conclusion': tag, 'text': target}
        replies = [{'complete': True, 'value': {'statements': [row]}},
                   {'complete': True, 'value': {'decisions': [
                       {'source': 1, 'supported': True, 'reason': 'This is the sufficient direction.'}]}}]
        with patch.object(self.runner, '_save'), patch.object(self.runner, '_late', return_value=False), \
                patch.object(self.runner, '_tokens_left', return_value=20000), \
                patch.object(self.runner, '_ask', side_effect=replies) as ask:
            self.runner._recover_core_answer()
        weaker = target.replace('exactly for', 'for')
        self.assertIn('Candidate: ' + weaker, ask.call_args_list[1].args[1])
        self.assertEqual(state['sections']['0']['text'], weaker[:-1] + ' [@1, Theorem 2].')
        verdict = {'units': [1], 'source': 1, 'condition': 'the estimate holds', 'conclusion': 'the estimate fails.'}
        for words in ('exactly for', 'if and only if', 'iff'):
            with self.subTest(words=words):
                self.assertFalse(self.runner._support([verdict], [target.replace('exactly for', words)], [1]))
        self.assertTrue(self.runner._support([verdict], [weaker], [1]))
        # An actual equivalence or failure including equality does decide this boundary.
        for endpoint in [r'The estimate holds if and only if $s>s_c$.',
                         r'The estimate fails for $s\leq s_{c}$.', 'At equality the estimate fails.']:
            self.primary(1, {tag: passage + ' ' + endpoint})
            self.assertTrue(self.runner._support([verdict], [target], [1]))
        self.primary(1, {tag: passage + r' Whether the estimate holds at $s=s_c$ remains open.'})
        self.assertFalse(self.runner._support([verdict], [target], [1]))
        self.primary(1, {tag: passage + r' The estimate holds if and only if $q>q_c$ in the other model.'})
        self.assertFalse(self.runner._support([verdict], [target], [1]))

    def test_checked_export_keeps_the_hypothesis_and_lists_an_undiscussed_planned_work_apart(self):
        condition = 'Under assumption (2.1), let $d>1$.'
        result = 'The system is observable in positive time.'
        self.primary(2, {'L5-L6': condition + ' ' + result})
        state = self.runner.state
        state['outline'] = {'title': 'Observation time', 'answer_works': [2], 'sections': [
            {'heading': 'Observation', 'kind': 'results', 'works': [2]},
            {'heading': 'Other controls', 'kind': 'other', 'works': [1]}]}
        state['sections'] = {'0': {'text': 'For $d>1$, the system is observable in positive time [@2].\n\n'
                                           'In every dimension the system is observable [@2].'},
                             '1': {'text': 'Every measurable set gives observation [@1].'}}
        spans = {}
        self.runner._pack([2], sources_only=True, source_spans=spans)
        ids = {sentence: key for key, (_, _, sentence) in spans.items()}
        # Even a referee endorsing both sentences with real source spans cannot drop d>1.
        verdict = {'verdict': 'supported',
                   'support': [{'source': 2, 'condition': ids[condition], 'conclusion': ids[result]}]}
        self.check(0, {1: verdict, 2: verdict})
        self.runner._assemble()
        markdown = (self.root / 'review.md').read_text()
        self.assertIn('For $d>1$, the system is observable in positive time [1].', markdown)
        self.assertNotIn('In every dimension', markdown)
        self.assertNotIn('Every measurable set', markdown)
        # The unchecked section says nothing; its planned work is listed apart, without a claim.
        self.assertEqual(state['bibliography'], [2, 1])
        self.assertEqual([(row['read'], row['cited']) for row in state['references']],
                         [('full_text', True), ('metadata', False)])
        self.assertLess(markdown.index('**Further reading.**'), markdown.index('2. A. Author. *Observation from a strip*'))
        bib = (self.root / 'references.bib').read_text()
        self.assertIn('10.5555/fixture.2', bib)
        self.assertIn('10.5555/fixture.1', bib)


    def test_export_drops_only_a_contradicted_relative_publication_date(self):
        state = self.runner.state
        state['outline'] = {'title': 'Estimates', 'sections': [{'heading': 'Estimates', 'kind': 'results', 'works': [1, 2]}]}
        state['checks'] = {'0': {'ran': True}}
        first, second = state['pool']
        for record, year in [(first, 2001), (second, 2002)]:
            record.update(type='journal article', venue=f'J. Math. 7 ({year}), 3–7', year=year)

        def exported(adjective, prior='The estimate holds [@1].'):
            state['sections'] = {'0': {'text': prior + ' The ' + adjective + ' result of {@2} proves the bound [@2].'}}
            return self.runner._compose().split('## References')[0]

        self.assertIn('The result of Author proves the bound [2].', exported('earlier'))
        self.assertIn('The later result', exported('later'))
        self.assertIn('The earlier result', exported('earlier', 'Several proofs are available [@1; @2].'))
        self.assertIn('The earlier result', exported('earlier', 'The estimate holds [@1]. We turn to a different question.'))
        second['venue'] = 'J. Math. 6 (2000), 3–7'
        self.assertIn('The result of Author proves the bound [2].', exported('later'))
        self.assertIn('The earlier result', exported('earlier'))
        second['venue'] = 'J. Math. 7 (2002), 3–7'
        second['preprint_year'] = 1999
        self.assertIn('The earlier result', exported('earlier'))
        first.update(venue=None, year_from='arxiv')
        self.assertIn('The earlier result', exported('earlier'))
        second['abstract'] = f'We extend the main theorem of {first["doi"]} to a new class.'
        self.assertIn('The result of Author proves the bound [2].', exported('earlier'))
        self.assertIn('The later result', exported('later'))
        second['abstract'] = f'We extend the main theorem of {first["doi"]}.9 to a new class.'
        self.assertIn('The earlier result', exported('earlier'))


if __name__ == '__main__':
    unittest.main()
