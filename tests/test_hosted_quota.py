"""Hosted allowances survive contention, restart and incomplete model streams."""
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import AgentError
from mathagent.deepinfra import CONTEXT_TOKENS, DeepInfra, MODEL
from mathagent.hosted_quota import HostedQuota, quota_status
from tests.test_gui import FakeDeepInfra, GuiCase


def _competing_reservation(path, barrier, queue):
    ledger = HostedQuota(path, usd='0.70')
    barrier.wait(15)
    try:
        queue.put(('reserved', ledger.reserve(CONTEXT_TOKENS, 384000)))
    except AgentError:
        queue.put(('blocked', None))


class HostedQuotaTests(unittest.TestCase):
    def test_process_contention_refunds_and_restart_keep_one_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory).resolve() / 'quota.sqlite')
            ledger = HostedQuota(path, usd='0.70')
            context = multiprocessing.get_context('spawn')
            barrier, queue = context.Barrier(3), context.Queue()
            processes = [context.Process(target=_competing_reservation, args=(path, barrier, queue)) for _ in range(2)]
            for process in processes:
                process.start()
            barrier.wait(15)
            results = [queue.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual([result[0] for result in results], ['reserved', 'blocked'])
            reservation = next(result[1] for result in results if result[0] == 'reserved')
            ledger.settle(reservation, 200, 37)
            with self.assertRaisesRegex(AgentError, 'already settled'):
                ledger.settle(reservation, 0, 0)
            held = HostedQuota(path, usd='0.70').reserve(CONTEXT_TOKENS, 384000)
            with self.assertRaisesRegex(AgentError, 'invalid usage'):
                ledger.settle(held, CONTEXT_TOKENS + 1, 1)
            with self.assertRaisesRegex(AgentError, 'insufficient'):
                HostedQuota(path, usd='0.70').reserve(CONTEXT_TOKENS, 384000)
            with self.assertRaisesRegex(AgentError, 'settings changed'):
                HostedQuota(path, usd='2.50')
            with self.assertRaisesRegex(AgentError, 'settings changed'):
                HostedQuota(path, usd='0.70', output_per_million='0.01')
            status = ledger.status()
            self.assertEqual((status['limit_nanos'], status['input_tokens'], status['output_tokens']),
                             (700000000, 200, 37))
            self.assertEqual(status['charged_nanos'], 62200)
            self.assertGreater(status['reserved_nanos'], 400000000)
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)

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

    def test_unsafe_or_uninitialized_existing_ledgers_never_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'existing.sqlite'
            target.touch(mode=0o600)
            with self.assertRaisesRegex(AgentError, 'storage is unavailable'):
                HostedQuota(target)
            self.assertEqual(target.stat().st_size, 0)
            link = root / 'link.sqlite'
            link.symlink_to(target)
            with self.assertRaisesRegex(AgentError, 'storage is unavailable'):
                HostedQuota(link)
            target.chmod(0o644)
            with self.assertRaisesRegex(AgentError, 'owned private regular file'):
                HostedQuota(target)


class HostedQuotaGuiTests(GuiCase):
    fake_class = FakeDeepInfra
    model = MODEL
    extra_args = ('--backend', 'openai', '--api-profile', 'deepinfra', '--offline', '--ctx', '65536')

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': FakeDeepInfra.key,
            'DEEPINFRA_API_KEY_FILE': '', 'SH_DEEPINFRA_SERVICE_TIER': 'standard',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory.name).resolve() / 'quota.sqlite'),
            'SH_DEEPINFRA_QUOTA_USD': '2.50',
            'SH_DEEPINFRA_QUOTA_INPUT_PER_MILLION': '0.20',
            'SH_DEEPINFRA_QUOTA_OUTPUT_PER_MILLION': '0.60',
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('SQUARE_OPENAI_THINKING_FRACTION', 'SQUARE_OPENAI_STRUCTURED_THINKING_FRACTION'):
            os.environ.pop(name, None)
        super().setUp()

    def test_status_shows_shared_tester_usage_without_changing_job_budgets(self):
        before = self.ok('GET', '/api/status')
        self.assertEqual(before['tester_allowance']['limit_nanos'], 2500000000)
        self.assertEqual(before['tester_allowance']['charged_nanos'], 0)
        self.fake.replies = [('Ready', '', 5)]
        chat = self.ok('POST', '/api/chats', {'mode': 'explore', 'think': False, 'online': False})
        self.ok('POST', '/api/chats/' + chat['id'] + '/messages', {'content': 'Say Ready.'})
        task = self.wait_task()
        self.assertEqual(task['state'], 'done', task)
        after = self.ok('GET', '/api/status')
        self.assertEqual(after['defaults'], before['defaults'])
        self.assertEqual(after['tester_allowance']['charged_nanos'], 43000)
        self.assertEqual(after['tester_allowance']['reserved_nanos'], 0)
        self.assertEqual((after['tester_allowance']['input_tokens'], after['tester_allowance']['output_tokens']), (200, 5))
        os.environ.pop('SH_DEEPINFRA_QUOTA_FILE')
        self.assertNotIn('tester_allowance', self.ok('GET', '/api/status'))
