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
from mathagent.literature import LiteratureTools
from mathagent.proof import ProofRunner
from mathagent.proof_support import complete_proof_evidence
from mathagent.tools import Workspace
from tests.test_research import FakeClient, response, tool


class SupportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = LiteratureTools(self.root, online=False)
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
        self.assertFalse(child['settings']['online'])
        self.assertIn(child['id'], json.dumps(self.transport.requests[-1]))

    def test_coordinator_supplies_scoped_passage_offline_without_child(self):
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

    def test_offline_literature_request_runs_native_survey_and_resumes(self):
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
        self.assertEqual(survey['settings']['document_output_cap'], 2048)
        self.assertTrue(all(call['reserved_tokens'] <= 2048 for call in survey['calls']))
        self.assertFalse(survey['settings']['online'])
        self.assertEqual(survey['literature_snapshot']['stats']['requests'], 0)
        self.assertEqual(result['tokens_charged'], self.client.snapshot()['tokens'])

    def test_native_check_inherits_requesting_role_per_call_ceiling(self):
        candidate = response('By reflexivity, x=x for each real x.')
        recall = response(json.dumps({'statement': 'Reflexivity.', 'keywords': ['equality'], 'candidates': []}))
        cases = [('solver', 1024, 4096, None, 1024), ('verifier', 1024, 4096, None, 4096),
                 ('verifier', 8192, 4096, 768, 768)]
        for role, solve, verify, specialist, expected in cases:
            with self.subTest(role=role, expected=expected), patch.dict(refcheck.EFFORTS,
                    {'high': {**refcheck.EFFORTS['high'], 'discover': False}}):
                replies = [self.ask, recall, candidate, self.approve] if role == 'solver' else [
                    candidate, self.ask, recall, self.approve]
                runner, pending = self.start_proof(replies, solve=solve, verify=verify, specialist=specialist, hosted=True)
                result = complete_proof_evidence(runner, pending)
                self.assertEqual(result['status'], 'candidate_complete', result.get('stop_reason'))
                check = next(call for call in self.transport.requests if any('Before searching, recall from memory.'
                             in str(m.get('content', '')) for m in call['messages']))
                self.assertEqual(check['options']['num_predict'], expected)
