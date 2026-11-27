# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""A proof asks its coordinator for research on a single shared inference slot."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from mathagent import refcheck
from mathagent.agent import Agent
from mathagent.assistant_budget import BudgetClient
from mathagent.deepinfra import DeepInfraGenerationError
from mathagent.literature import LiteratureError, LiteratureTools
from mathagent.proof import ProofRunner
from mathagent.proof_support import _SupportLibrary, complete_proof_evidence
from mathagent.source_tasks import scoped_literature
from mathagent.tools import Workspace
from tests.test_research import FakeClient, response, tool


class SupportTests(unittest.TestCase):
    def test_reference_handoff_can_quote_the_target_but_private_outbound_searches_cannot(self):
        url = 'https://stacks.math.columbia.edu/tag/09HX'
        goal = ('Show that every finite subgroup of the multiplicative group of a field is cyclic. Ask the '
                'coordinator for a focused reference check of the Stacks Project, Fields, Lemma 9.17.1 '
                f'(Tag 09HX, {url}). Explain whether the lemma applies here.')
        question = (f'Please check the exact statement and proof of the Stacks Project, Fields, Lemma 9.17.1 '
                    f'(Tag 09HX, {url}). In particular, state its hypotheses and conclusion, and whether it '
                    'applies verbatim to every finite subgroup of the multiplicative group of an arbitrary field. '
                    'Report any differences or additional assumptions.')
        secret = 'Our private computation has an unpublished exceptional case on the final branch'
        Path(self.root, 'manuscript.txt').write_text(secret)
        transport = FakeClient([response(calls=[tool('request_evidence', kind='check', question=question)])])
        parent = ProofRunner(Agent(transport, Workspace(self.root, literature=self.library), ctx=16384), evidence_coordinator=True)
        waiting = parent.start(goal, source_files=['manuscript.txt'], max_tokens=12000, max_predict=1024, verify_tokens=1024)
        self.assertEqual(waiting['status'], 'waiting_for_evidence')
        # Capture the real child binding at admission; exercise its actual HTTP path with a fake source transport.
        with patch.object(refcheck.CheckRunner, 'start', autospec=True, side_effect=KeyboardInterrupt) as admit:
            with self.assertRaises(KeyboardInterrupt):
                complete_proof_evidence(parent, waiting)
        child, submitted = admit.call_args.args
        self.assertEqual(submitted, question)
        lib = child.literature
        self.fixture_fetch.stop()  # Exercise the native policy and replace only its final GET transport below.
        def reply(address, headers, size):
            if address.endswith('/robots.txt'):
                return 200, {}, b'User-agent: *\nAllow: /'
            if address == url:
                return 200, {'content-type': 'text/plain'}, b'An explicitly supplied public theorem locator.'
            raise AssertionError(f'Unexpected outbound URL {address}')
        with patch.object(lib, '_request_once', side_effect=reply) as wire:
            opened = lib.open_paper(url)
            self.assertIn('public theorem locator', lib.read_paper(opened['document_id'])['passage'])
            prior = (wire.call_count, lib.stats['requests'])
            for work in (lambda: lib.crossref(query=secret), lambda: lib.arxiv_search(secret),
                         lambda: lib.open_paper('https://example.org/' + secret.replace(' ', '-'))):
                with self.assertRaisesRegex(LiteratureError, 'private source text'):
                    work()
            self.assertEqual((wire.call_count, lib.stats['requests']), prior)
        private_redirect = 'https://example.org/' + secret.replace(' ', '-')
        with patch.object(lib, '_request_once', return_value=(302, {'location': private_redirect}, b'')) as wire:
            with self.assertRaisesRegex(LiteratureError, 'private source text'):
                lib._fetch('https://example.org/start', _robots=False)
            self.assertEqual([call.args[0] for call in wire.call_args_list], ['https://example.org/start'])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = LiteratureTools(self.root, online=True)
        self.fixture_fetch = patch.object(LiteratureTools, '_fetch', side_effect=LiteratureError('Fixture source unavailable'))
        self.fixture_fetch.start()
        self.addCleanup(self.fixture_fetch.stop)
        self.question = 'Which source states reflexivity of equality?'
        self.ask = response(calls=[tool('request_evidence', kind='check', question=self.question)])
        self.approve = response(calls=[tool('submit_review', schema_version=3, explanation='Reflexivity applies.',
            issues=[], notes=[], task_status='proved', critical_dependencies=[
                {'claim': 'x equals itself.', 'provenance': 'standard', 'check': 'Reflexivity holds for each x.'}])])
        self.goal = 'Prove that every real x satisfies x = x.'

    def start_proof(self, replies, *, solve=2048, verify=2048, specialist=None, hosted=False):
        self.transport = FakeClient(replies)
        if hosted:
            self.transport.profile, self.transport.reasoning_effort = 'deepinfra', 'high'
        self.client = BudgetClient(self.transport, concurrency=1, max_tokens=24000, max_seconds=60)
        self.agent = Agent(self.client, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        self.agent.document_output_cap = specialist
        runner = ProofRunner(self.agent, evidence_coordinator=True)
        pending = runner.start(self.goal, max_predict=solve, verify_tokens=verify, max_rounds=1, max_tokens=24000)
        self.assertEqual(pending['status'], 'waiting_for_evidence')
        return runner, pending

    def test_native_check_recovers_after_completion_before_receipt(self):
        recall = response(json.dumps({'statement': 'Reflexivity.', 'keywords': ['equality'], 'candidates': []}))
        with patch.dict(refcheck.EFFORTS, {'high': {**refcheck.EFFORTS['high'], 'discover': False}}):
            runner, pending = self.start_proof([response('By reflexivity every real x equals itself.'),
                                                self.ask, recall, self.approve])
            self.assertEqual(pending['evidence_request']['role'], 'verifier')
            with patch.object(runner, 'resume', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                complete_proof_evidence(runner, pending)
            self.assertEqual(len(self.transport.requests), 3)
            new = ProofRunner(self.agent, evidence_coordinator=True)
            result = complete_proof_evidence(new, new.resume(pending['id']))
        self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
        self.assertEqual(len(self.transport.requests), 4)
        self.assertEqual(len(new.state['candidates']), 1)
        self.assertEqual(len(new.state['reviews']), 1)
        self.assertEqual(result['tokens_charged'], self.client.snapshot()['tokens'])
        from mathagent.reporting import job_output_budget
        totals = job_output_budget(new.state, False)
        self.assertEqual(totals['used'], result['tokens_charged'])
        self.assertEqual(totals['uncertain'], 0)
        states = list((self.root / '.mathagent' / 'checks').glob('*/state.json'))
        self.assertEqual(len(states), 1)
        child = json.loads(states[0].read_text())
        self.assertEqual(child['goal'], self.question)
        self.assertEqual(child['tokens_charged'], 20)
        self.assertLessEqual(child['settings']['max_tokens'], pending['evidence_request']['remaining_tokens'])
        self.assertTrue(child['settings']['online'])
        self.assertIn(child['id'], json.dumps(self.transport.requests[-1]))

    def test_coordinator_supplies_scoped_passage_without_child(self):
        path = self.root / 'source.txt'
        path.write_text('A first line.\nEquality is reflexive.\n')
        doc = self.library.import_local(path.name)
        question = self.question
        runner, pending = self.start_proof([self.ask, response('By reflexivity, x = x for each real x.'), self.approve])
        view = {'entries': [{'id': 'ref-existing.1', 'task': {'text': question}, 'documents': [{
            'document_id': doc['document_id'], 'text_sha256': doc['text_sha256'], 'passages': [{'range': 'L2-L2'}]}]}]}
        result = complete_proof_evidence(runner, pending, view=view)
        self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
        self.assertEqual(len(self.transport.requests), 3)
        self.assertFalse((self.root / '.mathagent' / 'checks').exists())
        self.assertIn('Equality is reflexive.', json.dumps(self.transport.requests[-1]))
        self.assertNotIn(question, json.dumps(self.transport.requests[-1]))

    def test_check_can_verify_a_named_paper_opened_in_the_requesting_turn(self):
        url = 'https://example.org/tag/AB12'
        self.goal += ' Use the public source ' + url
        question = 'Does Lemma 3.4 (Tag AB12) establish reflexivity?'
        batch = response(calls=[tool('open_paper', identifier_or_url=url),
                                tool('request_evidence', kind='check', question=question)])
        source = b'<html><head><title>Lemma 3.4 (AB12)</title></head><body><p>Lemma 3.4. Equality is reflexive.</p></body></html>'
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                def replies():
                    yield batch
                    if interrupted:
                        yield [KeyboardInterrupt()]  # A pre-handoff native Check paused in its old recall call.
                    yield response('I checked the actual statement.')
                    metadata = json.loads(runner.store.read_artifact(runner.state['evidence'][0]['result']))
                    document_id = metadata['document_id']
                    _, lines = self.library._document(document_id)
                    n = next(n for n, line in enumerate(lines, 1) if 'Equality is reflexive.' in line)
                    yield response(json.dumps({'level': 'read', 'locator': 'Lemma 3.4', 'document_id': document_id,
                                               'lines': f'L{n}-L{n}', 'record': url, 'statement_found': 'Equality is reflexive.',
                                               'note': '', 'needs_other_sources': False}))
                    yield response('By reflexivity, every x equals itself.')
                    yield self.approve
                with patch.object(LiteratureTools, '_fetch', return_value=(source, 'text/html', url)):
                    runner, pending = self.start_proof(replies())
                if interrupted:
                    with patch('mathagent.proof_support._named_proof_source', return_value=None):
                        paused = complete_proof_evidence(runner, pending)
                    self.assertEqual(paused['status'], 'paused')
                    runner = ProofRunner(self.agent, evidence_coordinator=True)
                    pending = runner.resume(pending['id'])
                with patch.object(LiteratureTools, '_fetch', side_effect=AssertionError('Fetched supplied URL a second time')):
                    result = complete_proof_evidence(runner, pending)
                self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
                entry = runner.state['evidence'][1]
                receipt = json.loads(runner.store.read_artifact(entry['receipt']))
                native = json.loads((self.root / '.mathagent/checks' / receipt['result']['job_id'] / 'state.json').read_text())
                self.assertEqual(native['goal'], question)
                self.assertEqual([c['role'] for c in native['calls']], (['recall'] if interrupted else []) + ['verify', 'verdict'])
                supplied, read = native['evidence']
                self.assertEqual((supplied['tool'], read['tool']), ('proof_open_paper', 'read_paper'))
                self.assertEqual(supplied['arguments']['evidence_id'], 'E1')
                self.assertEqual(supplied['arguments']['proof_id'], result['id'])
                primary, = receipt['primary_sources']
                self.assertEqual(primary['text_sha256'], supplied['arguments']['text_sha256'])
                self.assertIn('Equality is reflexive.', primary['content'])

    def test_unrelated_ambiguous_or_unsupplied_openings_do_not_seed_checks(self):
        one, two = 'https://example.org/tag/AB12', 'https://example.org/tag/AB13'
        for name, supplied, opened, question in [
                ('unrelated', [one], [one], 'Which source states Lemma 3.5?'),
                ('conflicting tag', [one], [one], 'Verify Lemma 3.4 at Tag AB99.'),
                ('ambiguous', [one, two], [one, two], 'What does Lemma 3.4 say?'),
                ('unsupplied', [], [one], 'What does Lemma 3.4 say?')]:
            with self.subTest(name=name):
                self.goal = 'Prove x = x. ' + ' '.join(supplied)
                batch = response(calls=[*(tool('open_paper', identifier_or_url=url) for url in opened),
                                        tool('request_evidence', kind='check', question=question)])
                def fetch(url):
                    return b'Lemma 3.4. Equality is reflexive.', 'text/plain', url
                recall = response(json.dumps({'statement': 'Equality.', 'keywords': [], 'candidates': []}))
                with patch.object(LiteratureTools, '_fetch', side_effect=fetch), patch.dict(refcheck.EFFORTS,
                        {'high': {**refcheck.EFFORTS['high'], 'discover': False}}):
                    runner, pending = self.start_proof([batch, recall, response('By reflexivity x=x.'), self.approve])
                    completed = complete_proof_evidence(runner, pending)
                self.assertEqual(completed['status'], 'candidate_complete', completed.get('stop_reason'))
                entry = next(e for e in runner.state['evidence'] if e['name'] == 'request_evidence')
                native = json.loads((self.root / '.mathagent/checks' / entry['job_link'][1] / 'state.json').read_text())
                self.assertNotIn('proof_source', native['check'])
                self.assertEqual([c['role'] for c in native['calls']], ['recall'])

    def test_pending_research_respects_inherited_source_assignment_after_restart(self):
        docs = {}
        for name, content in [('assigned.txt', 'An assigned lemma.'), ('unrelated.txt', 'Unrelated private result.')]:
            (self.root / name).write_text(content)
            docs[name] = self.library.import_local(name)
        scoped = scoped_literature(self.library, 'Use only pinned material.',
                                   sources=[{'path': 'assigned.txt', 'document_id': docs['assigned.txt']['document_id']}])
        inherited = _SupportLibrary(scoped, {})
        self.assertIn('An assigned lemma.', inherited.read_paper(docs['assigned.txt']['document_id'])['passage'])
        with self.assertRaisesRegex(LiteratureError, 'assigned sources'):
            inherited.read_paper(docs['unrelated.txt']['document_id'])
        other = docs['unrelated.txt']
        view = {'entries': [{'id': 'unrelated', 'task': {'text': self.question}, 'documents': [{
            'document_id': other['document_id'], 'text_sha256': other['text_sha256'], 'passages': [{'range': 'L1-L1'}]}]}]}
        for already_ran in (False, True):
            with self.subTest(already_ran=already_ran):
                recall = response(json.dumps({'statement': 'Reflexivity.', 'keywords': ['equality'], 'candidates': []}))
                replies = [self.ask, *([recall] if already_ran else []),
                           response('By reflexivity, each real x equals itself.'), self.approve]
                runner, pending = self.start_proof(replies)
                if already_ran:
                    with patch.dict(refcheck.EFFORTS, {'high': {**refcheck.EFFORTS['high'], 'discover': False}}), \
                            patch.object(runner, 'resume', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                        complete_proof_evidence(runner, pending)
                self.agent.workspace.literature = scoped
                runner = ProofRunner(self.agent, evidence_coordinator=True)
                with patch.object(LiteratureTools, '_fetch', side_effect=AssertionError('Contacted an external source')), \
                        patch.object(refcheck.CheckRunner, 'resume', side_effect=AssertionError('Resumed external research')):
                    result = complete_proof_evidence(runner, runner.resume(pending['id']), view=view)
                self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
                entry = next(e for e in runner.state['evidence'] if e['name'] == 'request_evidence')
                receipt = json.loads(runner.store.read_artifact(entry['receipt']))
                self.assertEqual(receipt['result']['status'], 'unavailable')
                self.assertEqual(receipt['tokens'], 20 if already_ran else 0)
                self.assertNotIn('primary_sources', receipt)
                self.assertNotIn('Unrelated private result.', json.dumps(self.transport.requests))
                self.assertEqual(result['tokens_charged'], self.client.snapshot()['tokens'])

    def test_literature_request_runs_native_survey_and_resumes(self):
        question = 'Survey classical references on reflexive relations.'
        ask = response(calls=[tool('request_evidence', kind='literature', question=question)])
        runner, pending = self.start_proof([ask])
        scope = {'topic': 'Reflexive relations', 'title': 'Reflexive relations', 'setting': 'Sets',
                 'terms': ['reflexive relations'], 'keywords': ['logic'], 'categories': [], 'msc': [],
                 'facets': [{'name': 'Equality', 'queries': ['reflexive relations equality']}], 'seeds': []}
        replies = iter([response('Equality is reflexive, so every real x equals itself.'), self.approve])

        def stream(payload):
            self.transport.requests.append(payload)
            names = {s['function']['name'] for s in payload.get('tools', [])}
            if 'request_evidence' in names:
                yield from next(replies)
            else:
                yield from response(json.dumps(scope))

        with patch.object(self.transport, 'stream', side_effect=stream):
            result = complete_proof_evidence(runner, pending)
        self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
        jobs = list((self.root / '.mathagent' / 'research').glob('*/state.json'))
        self.assertEqual(len(jobs), 1)
        survey = json.loads(jobs[0].read_text())
        self.assertEqual(survey['kind'], 'literature')
        self.assertEqual(survey['goal'], question)
        self.assertEqual(survey['status'], 'partial', survey.get('stop_reason'))
        self.assertEqual(survey['phase'], 'done')
        self.assertTrue(survey['companion'])
        self.assertIsNone(survey['settings']['document_output_cap'])
        self.assertLessEqual(survey['settings']['max_tokens'], pending['evidence_request']['remaining_tokens'])
        self.assertTrue(survey['settings']['online'])
        self.assertEqual(survey['literature_snapshot']['stats']['requests'], 0)
        self.assertEqual(result['tokens_charged'], self.client.snapshot()['tokens'])

    def test_native_check_uses_shared_budget_and_any_explicit_specialist_ceiling(self):
        candidate = response('By reflexivity, x=x for each real x.')
        recall = response(json.dumps({'statement': 'Reflexivity.', 'keywords': ['equality'], 'candidates': []}))
        cases = [('solver', 1024, 4096, None), ('verifier', 1024, 4096, None), ('verifier', 8192, 4096, 768)]
        for role, solve, verify, specialist in cases:
            with self.subTest(role=role, specialist=specialist), patch.dict(refcheck.EFFORTS,
                    {'high': {**refcheck.EFFORTS['high'], 'discover': False}}), \
                    patch.object(BudgetClient, 'remaining_seconds', new_callable=PropertyMock, return_value=55):
                replies = [self.ask, recall, candidate, self.approve] if role == 'solver' else [
                    candidate, self.ask, recall, self.approve]
                runner, pending = self.start_proof(replies, solve=solve, verify=verify, specialist=specialist, hosted=True)
                request = pending['evidence_request']
                self.assertNotIn('call_output_cap', request)
                self.assertEqual(request['remaining_seconds'], 55)
                result = complete_proof_evidence(runner, pending)
                self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
                check = next(call for call in self.transport.requests if any('Before searching, recall from memory.'
                             in str(m.get('content', '')) for m in call['messages']))
                cap = check['options']['num_predict']
                self.assertLessEqual(cap, request['remaining_tokens'])
                if specialist is None:
                    self.assertGreater(cap, solve if role == 'solver' else verify)
                else:
                    self.assertEqual(cap, specialist)
                entry = next(e for e in runner.state['evidence'] if e['name'] == 'request_evidence')
                native_id = entry['job_link'][1]
                settings = json.loads((self.root / '.mathagent' / 'checks' / native_id / 'state.json').read_text())['settings']
                self.assertEqual(settings['max_seconds'], 55)
                self.assertEqual(settings['document_output_cap'], specialist)

    def test_native_check_resume_preserves_saved_and_smaller_current_specialist_ceilings(self):
        recall = response(json.dumps({'statement': 'Reflexivity.', 'keywords': ['equality'], 'candidates': []}))
        for original, current in [(3500, 768), (900, 4000)]:
            with self.subTest(original=original, current=current), patch.dict(refcheck.EFFORTS,
                    {'high': {**refcheck.EFFORTS['high'], 'discover': False}}):
                runner, pending = self.start_proof([self.ask, recall, response('By reflexivity x=x.'), self.approve],
                                                   hosted=True, specialist=original)
                with patch.object(refcheck.CheckRunner, '_step', side_effect=KeyboardInterrupt):
                    paused = complete_proof_evidence(runner, pending)
                self.assertEqual(paused['status'], 'paused')
                self.agent.document_output_cap = current
                resumed = ProofRunner(self.agent, evidence_coordinator=True)
                out = complete_proof_evidence(resumed, resumed.resume(pending['id']))
                self.assertEqual(out['status'], 'candidate_complete', out.get('stop_reason'))
                check = next(call for call in self.transport.requests if any('Before searching, recall from memory.'
                             in str(m.get('content', '')) for m in call['messages']))
                self.assertEqual(check['options']['num_predict'], min(original, current))

    def test_paused_provider_failure_can_finish_with_available_evidence(self):
        # The provider failed immediately after recall. Its active time reached the child deadline
        # while the parent retained time for review. Cover live handling and a restarted coordinator.
        source = self.root / 'source.txt'
        source.write_text('Equality is reflexive.\n')
        metadata = self.library.import_local(source.name)
        row = {'kind': 'reference', 'status': 'source read', 'summary': 'Fixture definition of equality.',
               'qualifiers': [], 'evidence': {'quote': 'Equality is reflexive.'},
               'documents': [{'document_id': metadata['document_id'], 'passages': [{'range': 'L1-L1'}]}]}
        original_step = refcheck.CheckRunner._step
        def delayed_failure(child, phase):
            if phase == 'answer':
                child._seconds_before = child.state['settings']['max_seconds']
                raise DeepInfraGenerationError('Interrupted source verification.', category='protocol')
            return original_step(child, phase)

        for recover in (False, True):
            with self.subTest(recover=recover), patch.dict(refcheck.EFFORTS,
                    {'high': {**refcheck.EFFORTS['high'], 'discover': False}}), patch.object(
                    refcheck.CheckRunner, '_step', delayed_failure), patch.object(refcheck, 'export_evidence', return_value=[row]):
                recall = response(json.dumps({'statement': 'Equality.', 'candidates': [], 'keywords': ['equality']}))
                runner, pending = self.start_proof([response('By reflexivity x=x for each real x.'), self.ask,
                                                    recall, self.approve])
                if recover:
                    with patch.object(runner, 'resume', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                        complete_proof_evidence(runner, pending)
                    runner = ProofRunner(self.agent, evidence_coordinator=True)
                    pending = runner.resume(pending['id'])
                    with patch.object(refcheck.CheckRunner, 'resume', side_effect=AssertionError('Reopened spent check')):
                        result = complete_proof_evidence(runner, pending)
                else:
                    result = complete_proof_evidence(runner, pending)
                self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
                self.assertEqual(len(self.transport.requests), 4)
                entry = next(e for e in runner.state['evidence'] if e['name'] == 'request_evidence')
                receipt = json.loads(runner.store.read_artifact(entry['receipt']))
                reply = receipt['result']
                self.assertEqual((reply['status'], reply['native_status']), ('inconclusive', 'paused'))
                self.assertEqual(reply['exhausted_allowances'], ['seconds'])
                self.assertEqual(reply['worker_error']['details']['provider_category'], 'protocol')
                child = json.loads((self.root / '.mathagent' / 'checks' / reply['job_id'] / 'state.json').read_text())
                self.assertEqual(child['status'], 'paused')
                self.assertEqual(reply['stop_reason'], child['stop_reason'])
                self.assertEqual(receipt['seconds'], child['seconds_used'])
                self.assertEqual(receipt['tokens'], 20)
                self.assertEqual(receipt['primary_sources'][0]['content'], 'Equality is reflexive.')
                self.assertEqual(result['tokens_charged'], self.client.snapshot()['tokens'])

    def test_user_pause_and_provider_pause_with_room_leave_proof_waiting(self):
        for user_pause in (False, True):
            with self.subTest(user_pause=user_pause):
                runner, pending = self.start_proof([self.ask])
                def stop(child, phase):
                    if user_pause:
                        child._seconds_before = child.state['settings']['max_seconds']
                        raise KeyboardInterrupt
                    raise DeepInfraGenerationError('Interrupted before deadline.', category='protocol')
                with patch.object(refcheck.CheckRunner, '_step', stop):
                    result = complete_proof_evidence(runner, pending)
                self.assertEqual(result['status'], 'paused')
                self.assertEqual(runner.state['status'], 'waiting_for_evidence')
                self.assertEqual(len(self.transport.requests), 1)
