# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Hosted model streams settle reported usage and retain unknown-usage reservations."""
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from mathagent.agent import AgentError
from mathagent.deepinfra import DeepInfra, DeepInfraGenerationError, DeepInfraRateLimit, MODEL, rejected_before_inference
from mathagent.hosted_quota import HostedQuota, quota_status
from tests.test_gui import FakeDeepInfra


class HostedQuotaTests(unittest.TestCase):
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
            with self.assertRaisesRegex(AgentError, 'allowance is insufficient'):
                list(DeepInfra(fake.host).stream(payload))
            status = quota_status()
            self.assertEqual((status['input_tokens'], status['output_tokens']), (400, 10))
            self.assertGreater(status['reserved_nanos'], 200000000)
            self.assertEqual(len(fake.completions), 3)
            os.environ['SH_DEEPINFRA_SERVICE_TIER'] = 'priority'
            with self.assertRaisesRegex(AgentError, 'Standard service tier'):
                list(DeepInfra(fake.host).stream(payload))
