"""Regression coverage for the November main-branch audit."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import Agent, AgentError
from mathagent.assistant_budget import BudgetClient
from mathagent.jsonutil import loads
from mathagent.ledger import ProofStore
from mathagent.proof import ProofRunner, validate_review
from mathagent.reporting import usage_footer
from mathagent.tools import Workspace
from mathagent.web import _HTMLPage
from tests.test_proof_v05 import Client, event, approval
from tests import test_orchestrator as controller
from tests.test_orchestrator import calls, call, action, final


class AuditRegressions(unittest.TestCase):
    def test_model_json_fences_prefix_and_thinking(self):
        payload = approval()
        self.assertEqual(validate_review('<think>ignore {broken}</think>\n```json\n' + json.dumps(payload) + '\n```'), payload)
        self.assertEqual(loads('Here is the result:\n{"proof":"$\\ell^2$"}'), {'proof': '$\\ell^2$'})
        self.assertEqual(loads('2026 summary: {"note":"value"}'), {'note': 'value'})
        self.assertEqual(loads(r'{"note":"$\beta + \nabla_x$"}'), {'note': r'$\beta + \nabla_x$'})
        with self.assertRaises(ValueError):
            loads('```json\n{"verdict":')
        with self.assertRaises(ValueError):
            loads('{"wrapper": ' + json.dumps(payload))

    def test_footer_handles_unlimited_and_rounds_seconds(self):
        rendered = usage_footer({'settings': {'max_tokens': None, 'max_input_tokens': None, 'max_seconds': 899.930387},
                                 'tokens_charged': 11512, 'input_tokens_charged': 17629, 'seconds_used': 113})
        self.assertIn('11512 (no cumulative limit)', rendered)
        self.assertIn('113.0/899.9', rendered)
        self.assertNotIn('None', rendered)

    def test_omitted_html_end_tags_are_not_nested_without_bound(self):
        parser = _HTMLPage()
        parser.feed('<body>' + ''.join('<P>Paragraph %s' % n for n in range(300)) + '<ul>' + '<li>entry' * 300)
        self.assertLess(len(parser.stack), 10)
        self.assertIn('Paragraph 299', parser.root.text())

    def test_thinking_policy_setter_reaches_wrapped_transport(self):
        client = Client([])
        client.thinking_budget_policy = {}
        budget = BudgetClient(client)
        fork = budget.fork(client, allowance=None, usage={})
        fork.thinking_budget_policy = {'ordinary': .6}
        self.assertEqual(client.thinking_budget_policy, {'ordinary': .6})

    def test_empty_interrupted_proof_reuses_its_only_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client([KeyboardInterrupt(), [event('Proof. Reflexivity.')], [event(approval())]])
            runner = ProofRunner(Agent(client, Workspace(directory), ctx=40960))
            result = runner.start('Prove x=x.', max_rounds=1, max_predict=512, verify_tokens=512)
            self.assertEqual(result['status'], 'paused')
            result = runner.resume(result['id'])
            self.assertEqual(result['status'], 'candidate_complete')
            self.assertEqual(runner.state['rounds_started'], 1)
            self.assertEqual(result['tokens_charged'], 20)

    def test_all_proof_phases_keep_original_conversation_hypotheses(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client([[event('Working fragment.', reason='length')], [event('Complete proof.')], [event(approval())]])
            runner = ProofRunner(Agent(client, Workspace(directory), ctx=40960))
            result = runner.start('Prove the requested estimate.', max_predict=512, verify_tokens=512,
                reference_context={'query': 'Prove it.', 'history': [{'role': 'user', 'content': 'EXACT_HYPOTHESIS_H'}]})
            self.assertEqual(result['status'], 'candidate_complete')
            self.assertEqual(len(client.requests), 3)
            self.assertTrue(all('EXACT_HYPOTHESIS_H' in json.dumps(request) for request in client.requests))

    def test_unexpected_proof_exception_stops_active_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client([OSError('disk full')])
            runner = ProofRunner(Agent(client, Workspace(directory), ctx=40960))
            with self.assertRaisesRegex(OSError, 'disk full'):
                runner.start('Prove x=x.', max_predict=512, verify_tokens=512)
            state = ProofStore.load(directory, runner.state['id']).state
            self.assertEqual(state['status'], 'paused')
            self.assertIsNone(state['active_checkpoint_wall'])

    def test_ollama_sized_journal_can_exceed_old_four_megabyte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProofStore.create(directory, 'Prove x=x.', {'max_tokens': None, 'max_rounds': 1, 'max_seconds': 60})
            journal = store.start_stream('solver')
            chunk = {'message': {'thinking': 'a token'}, 'done': False, 'model': 'qwen3.8-27b', 'created_at': '2026-11-01T00:00:00.000000Z'}
            with patch('mathagent.ledger.os.fsync'):
                for _ in range(40000):
                    store.append_stream(journal, chunk)
            self.assertGreater(len(store.read_artifact(journal)), 4 * 1024 * 1024)


class DelegateAuditRegressions(unittest.TestCase):
    setUp = controller.OrchestratorTests.setUp
    worker = controller.OrchestratorTests.worker
    runner = controller.OrchestratorTests.runner

    def test_nonexistent_file_is_correctable_without_launching_worker(self):
        runner, client = self.runner([calls(call(args=action('referee', files=['missing.tex']))), final('The file is missing.')])
        result = runner.run('Review missing.tex.')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.children, [])
        self.assertIn('not a file', str(client.requests[-1]))

    def test_discovered_file_is_not_authorized_for_review(self):
        (self.root / 'private.tex').write_text('Private source')
        runner, _ = self.runner([calls(call(args=action('referee', files=['private.tex']))), final('Please name the manuscript.')])
        runner.run('Review the manuscript.')
        self.assertEqual(self.children, [])

    def test_prove_attachment_filter_does_not_reintroduce_pdf(self):
        (self.root / 'article.pdf').write_bytes(b'%PDF-1.4')
        runner, _ = self.runner([calls(call(args=action('prove', files=[]))), final('A proof was checked.')])
        runner.run('Prove x=x.', files=['article.pdf'])
        self.assertEqual(self.children[0]['action']['files'], [])

    def test_closing_calculation_is_not_a_pending_work_plan(self):
        runner, _ = self.runner([final('Let me verify with n=3: 1+3+5 = 9 = 3^2.')])
        self.assertEqual(runner.run('Check the case n=3.')['status'], 'complete')
