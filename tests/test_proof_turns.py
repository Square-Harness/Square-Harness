# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.proof import ProofRunner
from mathagent.tools import Workspace
from test_gui import APPROVAL, PROOF
from test_research import FakeClient, response, tool


class ProofTurnTests(unittest.TestCase):
    def test_truncated_surplus_turn_finishes_the_same_role_and_attempt(self):
        read = tool('search_papers', query='example')
        limited_solver = response(complete=False, count=450)
        limited_solver[0]['message']['thinking'] = 'solver-only unfinished calculation'
        limited_review = response('{"schema_version":', complete=False, count=400)
        client = FakeClient([response(calls=[read], count=750), limited_solver, response(PROOF, count=300),
                             response(calls=[read], count=500), limited_review, response(json.dumps(APPROVAL))])
        with TemporaryDirectory() as root:
            agent = Agent(client, Workspace(root), ctx=16384)
            runner = ProofRunner(agent)
            def pause_after_journal(kind, value):
                if kind == 'end' and len(runner.state['calls']) == 2:
                    raise KeyboardInterrupt
            runner.emit = pause_after_journal
            with patch.object(runner.turns.desk, 'execute', return_value='{"results": []}'):
                paused = runner.start('Show x=x.', max_predict=1200, verify_tokens=1200, max_tokens=3600, max_rounds=1)
            self.assertEqual(paused['status'], 'paused')
            self.assertIsNone(paused['initial_candidate'])
            runner = ProofRunner(agent)
            with patch.object(runner.turns.desk, 'execute', return_value='{"results": []}'):
                out = runner.resume(paused['id'])
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            self.assertEqual((out['rounds_started'], len(runner.state['candidates']), len(runner.state['reviews'])), (1, 1, 1))
            self.assertEqual([c['reserved_tokens'] for c in runner.state['calls']], [1200, 450, 1200, 1200, 400, 1200])
            for limited, final in [(1, 2), (4, 5)]:
                self.assertTrue(runner.state['calls'][limited]['evidence_surplus_limited'])
                self.assertNotIn('tools', client.requests[final])
                self.assertIn('-final', runner.state['calls'][final]['key'])
            self.assertIn('solver-only unfinished calculation', json.dumps(client.requests[2]))
            self.assertNotIn('solver-only unfinished calculation', json.dumps(client.requests[3:]))
            self.assertIn('task_status', client.requests[5]['format']['properties'])
