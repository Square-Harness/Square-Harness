# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Hosted model streams settle reported usage and retain unknown-usage reservations."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import AgentError
from mathagent.deepinfra import DeepInfra, MODEL
from mathagent.hosted_quota import quota_status
from tests.test_gui import FakeDeepInfra


class HostedQuotaTests(unittest.TestCase):
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
