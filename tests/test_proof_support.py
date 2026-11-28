# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""A proof asks its coordinator for research on a single shared inference slot."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathagent import refcheck
from mathagent.agent import Agent
from mathagent.assistant_budget import BudgetClient
from mathagent.literature import LiteratureError, LiteratureTools
from mathagent.proof import ProofRunner
from mathagent.proof_support import complete_proof_evidence
from mathagent.tools import Workspace
from test_research import FakeClient, response, tool


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

    def test_native_check_recovers_after_completion_before_receipt(self):
        recall = response(json.dumps({'statement': 'Reflexivity.', 'keywords': ['equality'], 'candidates': []}))
        with patch.dict(refcheck.EFFORTS, {'high': {**refcheck.EFFORTS['high'], 'discover': False}}):
            transport = FakeClient([response('By reflexivity every real x equals itself.'), self.ask, recall, self.approve])
            client = BudgetClient(transport, concurrency=1, max_tokens=24000, max_seconds=60)
            agent = Agent(client, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
            runner = ProofRunner(agent, evidence_coordinator=True)
            pending = runner.start(self.goal, max_predict=2048, verify_tokens=2048, max_rounds=1, max_tokens=24000)
            self.assertEqual(pending['status'], 'waiting_for_evidence')
            self.assertEqual(pending['evidence_request']['role'], 'verifier')
            with patch.object(runner, 'resume', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                complete_proof_evidence(runner, pending)
            self.assertEqual(len(transport.requests), 3)
            new = ProofRunner(agent, evidence_coordinator=True)
            result = complete_proof_evidence(new, new.resume(pending['id']))
        self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(len(new.state['candidates']), 1)
        self.assertEqual(len(new.state['reviews']), 1)
        self.assertEqual(result['tokens_charged'], client.snapshot()['tokens'])
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
        self.assertIn(child['id'], json.dumps(transport.requests[-1]))
