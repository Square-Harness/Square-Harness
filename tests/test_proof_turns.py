# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.literature import LiteratureTools
from mathagent.proof import ProofRunner, not_a_proof
from mathagent.tools import Workspace
from test_gui import APPROVAL, PROOF
from test_research import FakeClient, response, tool


class ProofTurnTests(unittest.TestCase):
    def test_large_receipts_are_paged_without_disclosing_the_solver_history(self):
        lookup = {'document_id': 'doc1', 'start_line': 1}
        client = FakeClient([response('private solver thought', [tool('read_paper', **lookup)]),
                             response(calls=[tool('read_proof_evidence', id='E1', chars=300)]),
                             response(PROOF), response(calls=[tool('read_proof_evidence', id='E1', chars=300)]),
                             response(calls=[tool('submit_review', **APPROVAL)])])
        with TemporaryDirectory() as root:
            runner = ProofRunner(Agent(client, Workspace(root, literature=LiteratureTools(root)), ctx=8192))
            source = {'document_id': 'doc1', 'text_sha256': 'version', 'citation': 'doc1:L1-L1',
                      'passage': 'Equality is reflexive. ' * 3000}
            with patch.object(runner.turns.desk, 'execute', return_value=json.dumps({**source, 'metadata': 'test'})):
                out = runner.start('Show that a=a for each a.', max_rounds=1, max_tokens=9000,
                                   max_predict=800, verify_tokens=800)
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            self.assertEqual((out['rounds_started'], len(client.requests)), (1, 5))
            for role in ('solver', 'verifier'):
                self.assertTrue(runner.state['pending'][role+'_indexed'])
            for request in client.requests[3:]:
                messages = json.dumps(request['messages'])
                self.assertIn('Show that a=a for each a.', messages)
                self.assertIn(PROOF, messages)
                self.assertNotIn('private solver thought', messages)
                self.assertNotIn(source['passage'], messages)
            self.assertIn('Equality is reflexive.', json.dumps(client.requests[-1]['messages']))
            visible = runner.turns.page('verifier', {'id': 'index'})
            self.assertNotIn('read_paper', json.loads(visible)['content'])  # only primary material is available

    def test_assistance_reserves_role_allowances_and_can_still_help_on_a_tight_job(self):
        def request(role, tokens):
            help_call = response(calls=[tool('request_evidence', question='Which result applies?', kind='check')])
            client = FakeClient([help_call] if role == 'solver' else [response(PROOF), help_call])
            with TemporaryDirectory() as root:
                runner = ProofRunner(Agent(client, Workspace(root), ctx=1048576, predict=384000), evidence_coordinator=True)
                out = runner.start('Show x=x.', max_tokens=tokens, max_predict=4096, verify_tokens=4096)
                self.assertEqual(out['status'], 'waiting_for_evidence')
                self.assertGreater(out['evidence_request']['remaining_seconds'], 0)
                self.assertLessEqual(out['evidence_request']['remaining_seconds'], 900)
                return out['evidence_request']['remaining_tokens'], tokens-out['tokens_charged']
        available, left = request('solver', 24000)
        self.assertEqual(left-available, 8192)
        available, left = request('verifier', 24000)
        self.assertEqual(left-available, 4096)
        available, left = request('solver', 2400)
        self.assertTrue(0 < available < left-256)

    def test_program_evidence_matches_the_observation_and_id(self):
        exp = 'exp-20261127-110000-abcdef'
        review = {**APPROVAL, 'explanation': f'{exp} was checked.'}
        record = {'kind': 'experiment', 'status': 'ok', 'experiment_id': exp, 'code': 'print(3)', 'stdout': '3\n'}
        self.assertTrue(not_a_proof(f'The calculation in {exp} establishes the claim.', review, []))
        for output in (3, 4):
            candidate = f'```python\nprint(3)\n```\nThe output is {output}.'
            self.assertEqual(not_a_proof(candidate, review, [record]) is None, output == 3)
