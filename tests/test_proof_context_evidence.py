# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import json
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.proof import ProofRunner
from mathagent.tools import Workspace
from test_gui import APPROVAL, PROOF
from test_research import FakeClient, response, tool


class ProofContextEvidenceTests(unittest.TestCase):
    @staticmethod
    def source():
        return json.dumps({'document_id': 'doc1', 'citation': 'doc1:L1-L2', 'text_sha256': 'version',
                           'passage': 'First observed line. ' + 'Source text. ' * 6000 + 'Last observed line.'})

    def test_closed_verifier_sees_a_marked_prefix_with_the_full_remaining_review_output(self):
        read = tool('read_paper', document_id='doc1')
        client = FakeClient([response(PROOF, count=600), response(calls=[read], count=1000),
                             response(json.dumps(APPROVAL))])
        with TemporaryDirectory() as root:
            runner = ProofRunner(Agent(client, Workspace(root), ctx=8192))
            record = self.source()
            with patch.object(runner.turns.desk, 'execute', return_value=record):
                out = runner.start('Show a=a.', max_rounds=1, max_tokens=2500, max_predict=900, verify_tokens=1000)
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            final = client.requests[-1]
            self.assertNotIn('tools', final)
            self.assertIn('task_status', final['format']['properties'])
            self.assertEqual(final['options']['num_predict'], 900)
            self.assertIn(PROOF, json.dumps(final['messages']))
            note, excerpt = final['messages'][-1]['content'].rsplit(' characters:\n', 1)
            size, length = map(int, re.search(r'First (\d+) of (\d+)$', note).groups())
            self.assertEqual((size, length, excerpt), (len(excerpt), len(record), record[:size]))
            self.assertIn('First observed line.', excerpt)
            self.assertNotIn('Last observed line.', excerpt)
            self.assertIn('Further evidence reads are unavailable', note)
            self.assertIn('uncertainty', note)
            self.assertNotIn('Use read_', note)

    def test_compact_verifier_can_reopen_a_document_and_submit_its_review(self):
        read = tool('read_paper', document_id='doc1')
        client = FakeClient([response(PROOF), response(calls=[read]), response(calls=[read]),
                             response(calls=[tool('submit_review', **APPROVAL)])])
        with TemporaryDirectory() as root:
            runner = ProofRunner(Agent(client, Workspace(root), ctx=8192))
            schemas = runner.turns.schemas

            def sizable_catalog(role):
                available = schemas(role)
                if runner.store and (runner.state.get('pending') or {}).get(role + '_indexed'):
                    return [{**s, 'function': {**s['function'], 'description': 'catalog ' * 8000}}
                            if s['function']['name'] == 'search_papers' else s for s in available]
                return available

            passage = json.dumps({'document_id': 'doc1', 'citation': 'doc1:L2-L2', 'text_sha256': 'version',
                                  'passage': 'The second source page is now open.'})
            with patch.object(runner.turns, 'schemas', side_effect=sizable_catalog), \
                 patch.object(runner.turns.desk, 'execute', side_effect=[self.source(), passage]) as execute:
                out = runner.start('Show a=a.', max_rounds=1, max_tokens=6000, max_predict=900, verify_tokens=1000)
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            self.assertEqual(execute.call_count, 2)
            reopened, final = client.requests[-2:]
            self.assertEqual({s['function']['name'] for s in reopened['tools']},
                             {'read_proof_evidence', 'read_paper', 'submit_review'})
            self.assertIn('Use read_paper', reopened['messages'][-1]['content'])
            self.assertIn(PROOF, json.dumps(final['messages']))
            self.assertIn('Earlier records are omitted', final['messages'][-1]['content'])
            self.assertIn('Complete latest record:\n' + passage, final['messages'][-1]['content'])
