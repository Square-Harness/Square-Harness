# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Hosted model streams settle reported usage and retain unknown-usage reservations."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from mathagent.agent import Agent, AgentError
from mathagent.assistant_budget import BudgetClient
from mathagent.backends import MeasuredStreamCancellation
from mathagent.deepinfra import DeepInfra, DeepInfraGenerationError, DeepInfraRateLimit, MODEL, TesterAllowanceInsufficient, rejected_before_inference
from mathagent.gui import store
from mathagent.hosted_quota import HostedQuota, quota_status
from mathagent.proof import ProofRunner
from mathagent.reporting import job_output_budget, output_summary
from mathagent.tools import Workspace
from tests.test_gui import APPROVAL, PROOF, FakeDeepInfra
from tests.test_research import FakeClient, response


class HostedQuotaTests(unittest.TestCase):
    def test_proof_resume_preserves_measured_usage_from_an_incomplete_stream(self):
        with tempfile.TemporaryDirectory() as workspace:
            failed = DeepInfraGenerationError('Stream interrupted.', category='interrupted',
                                              stats={'eval_count': 9, 'prompt_eval_count': 45})
            client = FakeClient([[failed], response(PROOF, count=37), response(json.dumps(APPROVAL), count=23)])
            runner = ProofRunner(Agent(client, Workspace(workspace)))
            result = runner.start('Prove x=x.', max_tokens=6153, max_predict=4096, verify_tokens=2048)
            self.assertEqual((result['status'], runner.state['tokens_charged']), ('paused', 9))
            resumed = runner.resume(result['id'])
            self.assertEqual((resumed['status'], runner.state['tokens_charged']), ('candidate_complete', 69))
            self.assertEqual([call['charged_tokens'] for call in runner.state['calls']], [9, 37, 23])

    def test_legacy_budget_counts_only_reported_output_and_separates_orphan_reservations(self):
        calls = [{'status': 'complete', 'stats': {'eval_count': 4}},
                 {'status': 'interrupted', 'reserved_tokens': 100}, {'status': 'running', 'reserved_tokens': 70}]
        job = {'tokens_charged': 174, 'settings': {'max_tokens': 200}, 'calls': calls}
        self.assertEqual(job_output_budget(job, True), {'used': 4, 'limit': 200, 'uncertain': 100, 'reserved': 70})
        self.assertEqual(job_output_budget(job, False), {'used': 4, 'limit': 200, 'uncertain': 170, 'reserved': 0})
        self.assertIn('Provider-reported output was 4 tokens. Usage is unknown for up to 170', output_summary(job))
        parent = {'status': 'complete', 'query': 'Q?', 'main_calls': 3, 'last_call': {'stats': {'eval_count': 20}},
                  'proof_routing': {'stats': {'eval_count': 2}}, 'budget': {'tokens': 135, 'max_tokens': 200},
                  'rejected_tool_calls': [{'main_call': 2, 'stats': {'eval_count': 8}}]}
        transcript = [{'role': 'user', 'content': 'Q?'},
                      {'role': 'assistant', 'mode': 'free', 'stats': {'eval_count': 5}},
                      {'role': 'assistant', 'mode': 'free', 'stats': {'eval_count': 20}}]
        tokens = store.assistant_view({'assistant_state': parent, 'transcript': transcript})['assistant_budget']['tokens']
        self.assertEqual(tokens, {'used': 35, 'limit': 200, 'uncertain': 100, 'reserved': 0})

    def test_accepted_stream_failure_reports_progress_and_only_final_usage(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key, 'DEEPINFRA_API_KEY_FILE': '',
            'SH_DEEPINFRA_SERVICE_TIER': 'standard', 'SH_DEEPINFRA_QUOTA_USD': '4',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory).resolve() / 'quota.sqlite'),
        }):
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}],
                       'options': {'num_predict': 128, 'num_ctx': 16384}, 'think': True}
            client = BudgetClient(DeepInfra(fake.host), max_tokens=1024)
            partial = {'choices': [{'delta': {'reasoning_content': 'think', 'content': 'abc', 'tool_calls': [
                {'index': 0, 'id': 'tool', 'function': {'name': 'run', 'arguments': '{'}}]}}]}
            usage = {'choices': [], 'usage': {'prompt_tokens': 45, 'completion_tokens': 9}}
            failure = {'error': {'code': 500, 'message': 'secret ' + fake.key}}
            fake.replies = [{'events': [partial, usage, failure]},
                            {'events': [partial, {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}, usage, failure]},
                            ('Recovered', '', 6)]
            with self.assertRaises(DeepInfraGenerationError) as unknown:
                list(client.stream(payload))
            error = unknown.exception
            self.assertEqual((error.category, error.retryable, error.stats), ('server_error', True, {}))
            self.assertEqual(error.stream_progress, {'content_chars': 3, 'thinking_chars': 5, 'tool_chars': 4})
            self.assertNotIn(fake.key, str(error))
            self.assertFalse(rejected_before_inference(error))
            self.assertEqual(len(fake.completions), 1)
            self.assertEqual((client.snapshot()['tokens'], client.snapshot()['measured_tokens']), (128, 0))
            with self.assertRaises(DeepInfraGenerationError) as measured:
                list(client.stream(payload))
            self.assertEqual(measured.exception.stats, {'eval_count': 9, 'prompt_eval_count': 45})
            self.assertTrue(list(client.stream(payload))[-1]['done'])
            state = client.snapshot()
            self.assertEqual((state['tokens'], state['measured_tokens'], state['reserved_tokens']), (143, 15, 0))
            self.assertEqual(store.assistant_view({'assistant_state': {'budget': state, 'status': 'paused'}})
                             ['assistant_budget']['tokens'], {'used': 15, 'uncertain': 128, 'reserved': 0, 'limit': 1024})
            self.assertEqual(len(fake.completions), 3)
            with sqlite3.connect(os.environ['SH_DEEPINFRA_QUOTA_FILE']) as db:
                self.assertEqual(db.execute('SELECT input_tokens, output_tokens FROM calls ORDER BY rowid').fetchall(),
                                 [(None, None), (45, 9), (200, 6)])

            # An error can include a terminal usage record. A cancellation after
            # receipt of that same authoritative usage preserves it without delivering tools.
            for terminal in ({**usage, **failure}, {'choices': [{'delta': {}, 'finish_reason': 'error'}], 'usage': usage['usage']}):
                fake.replies = [{'events': [partial, terminal]}]
                with self.assertRaises(DeepInfraGenerationError) as ended:
                    list(client.stream(payload))
                self.assertEqual(ended.exception.stats['eval_count'], 9)
            fake.replies = [('Cancelled', '', 8)]
            original_sse = client.client._sse
            def cancel_before_done(response, deadline):
                for raw in original_sse(response, deadline):
                    if raw == '[DONE]':
                        raise KeyboardInterrupt
                    yield raw
            with patch.object(client.client, '_sse', side_effect=cancel_before_done):
                with self.assertRaises(MeasuredStreamCancellation):
                    list(client.stream(payload))
            self.assertEqual(client.snapshot()['measured_tokens'], 41)
            self.assertEqual(quota_status()['output_tokens'], 41)

    def test_pre_stream_rejection_retries_and_settles_only_one_reservation(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key, 'DEEPINFRA_API_KEY_FILE': '',
            'SH_DEEPINFRA_SERVICE_TIER': 'standard', 'SH_DEEPINFRA_QUOTA_USD': '4',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory).resolve() / 'quota.sqlite'),
        }):
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}],
                       'options': {'num_predict': 128}, 'think': False}
            client = DeepInfra(fake.host)
            busy = {'http_status': 429, 'body': {'detail': 'Model busy'}, 'headers': {'Retry-After': '0'}}
            fake.replies = [busy, ('Ready', '', 5)]
            self.assertTrue(list(client.stream(payload))[-1]['done'])
            self.assertEqual(len(fake.completions), 2)
            # Static reasons only. Neither an ambiguous 429 nor an account limit may be replayed,
            # even with Retry-After or an otherwise transient code.
            for body, header, reason in [
                ({'error': {'type': 'rate_limit_error', 'message': 'Insufficient quota ' + fake.key}}, '0', 'credit'),
                ({'detail': fake.key}, '0', 'rate, capacity or spending'),
                ({'detail': 'Rate limited'}, '1800', 'temporarily'),
                ({'detail': 'Rate limited'}, 'invalid', 'temporarily'),
            ]:
                fake.replies = [{'http_status': 429, 'body': body, 'headers': {'Retry-After': header}}]
                with self.assertRaises(DeepInfraRateLimit) as stopped:
                    list(client.stream(payload))
                self.assertTrue(rejected_before_inference(stopped.exception))
                self.assertIn(reason, str(stopped.exception))
                self.assertNotIn(fake.key, str(stopped.exception))
            fake.replies = [busy, busy]
            with self.assertRaises(DeepInfraRateLimit):
                list(client.stream(payload))
            client.timeout = .5
            fake.replies = [{**busy, 'headers': {'Retry-After': '1'}}]
            with self.assertRaises(DeepInfraRateLimit):
                list(client.stream(payload))
            client.timeout = 5
            client.cancel = threading.Event()
            client.cancel.set()
            fake.replies = [busy]
            with self.assertRaises(KeyboardInterrupt) as stopped:
                list(client.stream(payload))
            self.assertTrue(rejected_before_inference(stopped.exception))
            self.assertEqual(len(fake.completions), 10)
            with sqlite3.connect(os.environ['SH_DEEPINFRA_QUOTA_FILE']) as db:
                rows = db.execute('SELECT id, input_tokens, output_tokens FROM calls ORDER BY rowid').fetchall()
            self.assertEqual([(i, o) for _, i, o in rows], [(200, 5)] + [(0, 0)] * 7)
            quota = HostedQuota.from_environment('standard')
            quota.settle(rows[-1][0], 0, 0)  # repeated settlement is harmless
            self.assertEqual(quota_status()['reserved_nanos'], 0)

            # A failure in an accepted stream is ambiguous; it retains the reservation.
            client.cancel.clear()
            fake.replies = [busy, {'events': [{'choices': [{'index': 0, 'delta': {'content': 'Partial'}, 'finish_reason': None}]},
                                        {'error': {'code': 429, 'message': fake.key}}]}]
            with self.assertRaises(DeepInfraGenerationError) as stopped:
                list(client.stream(payload))
            self.assertFalse(rejected_before_inference(stopped.exception))
            self.assertNotIn(fake.key, str(stopped.exception))
            self.assertEqual(len(fake.completions), 12)
            self.assertGreater(quota_status()['reserved_nanos'], 0)

    def test_real_http_stream_settles_reported_usage_and_holds_unknown_usage(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key,
            'DEEPINFRA_API_KEY_FILE': '', 'SH_DEEPINFRA_SERVICE_TIER': 'standard',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory).resolve() / 'quota.sqlite'),
            'SH_DEEPINFRA_QUOTA_USD': '0.210',
            'SH_DEEPINFRA_QUOTA_INPUT_PER_MILLION': '0.20',
            'SH_DEEPINFRA_QUOTA_OUTPUT_PER_MILLION': '0.60',
        }):
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}],
                       'options': {'num_predict': 128}, 'think': False}
            client = DeepInfra(fake.host)
            # Tokenization is free and must not consume a generation reservation.
            self.assertGreater(client.count_input_tokens(payload), 0)
            fake.replies = [('Ready', '', 5), ('Ready again', '', 5), {'events': [
                {'choices': [{'index': 0, 'delta': {'content': 'Incomplete'}, 'finish_reason': 'stop'}]},
            ]}]
            for _ in range(2):
                self.assertTrue(list(client.stream(payload))[-1]['done'])
            with self.assertRaisesRegex(AgentError, 'without finish reason and token usage'):
                list(client.stream(payload))
            parent = BudgetClient(DeepInfra(fake.host), max_tokens=1000)
            with self.assertRaises(TesterAllowanceInsufficient) as refused:
                list(parent.stream({**payload, 'options': {**payload['options'], 'num_ctx': 16384}}))
            self.assertTrue(rejected_before_inference(refused.exception))
            self.assertEqual((parent.snapshot()['tokens'], parent.snapshot()['input_tokens']), (0, 0))
            with sqlite3.connect(os.environ['SH_DEEPINFRA_QUOTA_FILE']) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 3)
            status = quota_status()
            self.assertEqual((status['input_tokens'], status['output_tokens']), (400, 10))
            self.assertGreater(status['reserved_nanos'], 200000000)
            self.assertEqual(len(fake.completions), 3)
            os.environ['SH_DEEPINFRA_SERVICE_TIER'] = 'priority'
            with self.assertRaisesRegex(AgentError, 'Standard service tier'):
                list(DeepInfra(fake.host).stream(payload))
