# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The tester ledger: calls are admitted while the allowance is not spent and charged when they end."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import AgentError
from mathagent.assistant_budget import BudgetClient
from mathagent.deepinfra import DeepInfra, MODEL, TesterAllowanceInsufficient, rejected_before_inference
from mathagent.hosted_quota import quota_status
from tests.test_gui import FakeDeepInfra


class HostedQuotaTests(unittest.TestCase):
    def test_real_http_stream_charges_reported_usage_and_estimates_unknown_usage(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key,
            'DEEPINFRA_API_KEY_FILE': '', 'SH_DEEPINFRA_SERVICE_TIER': 'standard',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory).resolve() / 'quota.sqlite'),
            'SH_DEEPINFRA_QUOTA_USD': '0.0001',
            'SH_DEEPINFRA_QUOTA_INPUT_PER_MILLION': '0.20',
            'SH_DEEPINFRA_QUOTA_OUTPUT_PER_MILLION': '0.60',
        }):
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}],
                       'options': {'num_predict': 128}, 'think': False}
            client = DeepInfra(fake.host)
            # Tokenization is free and is not charged.
            self.assertGreater(client.count_input_tokens(payload), 0)
            fake.replies = [('Ready', '', 5), ('Ready again', '', 5), {'events': [
                {'choices': [{'index': 0, 'delta': {'content': 'Incomplete'}, 'finish_reason': 'stop'}]},
            ]}]
            # Nothing is reserved: a full-context call is admitted under a $0.0001 allowance, and the calls run
            # until the spending reaches it (43,000 then 86,000 of 100,000 nanodollars).
            for _ in range(2):
                self.assertTrue(list(client.stream({**payload, 'options': {'num_predict': 384000, 'num_ctx': 1048576}}))[-1]['done'])
            self.assertEqual(quota_status()['charged_nanos'], 86000)
            # The third call is still under the limit when it starts; its usage is unknown, so it is charged an
            # estimate, and the total ends above the limit.
            with self.assertRaisesRegex(AgentError, 'without finish reason and token usage'):
                list(client.stream(payload))
            parent = BudgetClient(DeepInfra(fake.host), max_tokens=1000)
            with self.assertRaises(TesterAllowanceInsufficient) as refused:
                list(parent.stream({**payload, 'options': {**payload['options'], 'num_ctx': 16384}}))
            self.assertTrue(rejected_before_inference(refused.exception))
            self.assertIn('used up', str(refused.exception))
            self.assertEqual((parent.snapshot()['tokens'], parent.snapshot()['input_tokens']), (0, 0))
            with sqlite3.connect(os.environ['SH_DEEPINFRA_QUOTA_FILE']) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM calls WHERE charged IS NOT NULL').fetchone()[0], 3)
            status = quota_status()
            # Reported usage only in the counters; the broken third stream is charged an estimate.
            self.assertEqual((status['input_tokens'], status['output_tokens']), (400, 10))
            self.assertGreater(status['charged_nanos'], status['limit_nanos'])
            self.assertLess(status['charged_nanos'], 2000000)
            self.assertEqual(len(fake.completions), 3)
