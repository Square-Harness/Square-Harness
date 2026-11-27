# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The tester ledger: calls are admitted while the allowance is not spent and charged when they end."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from mathagent.agent import Agent, AgentError
from mathagent.assistant_budget import BudgetClient
from mathagent.backends import MeasuredStreamCancellation, TokenBudgetError, ToolCallError
from mathagent.deepinfra import (DeepInfra, DeepInfraGenerationError, DeepInfraRateLimit, MODEL, RETRIES,
                                 TesterAllowanceInsufficient, generation_stream, rejected_before_inference)
from mathagent.gui import store
from mathagent.hosted_quota import HostedQuota, quota_status
from mathagent.proof import ProofRunner
from mathagent.reporting import job_output_budget, output_summary
from mathagent.tools import Workspace
from tests.test_gui import APPROVAL, PROOF, FakeDeepInfra
from tests.test_research import FakeClient, response


class HostedQuotaTests(unittest.TestCase):
    def test_terminal_failure_reenters_proof_parent_and_hosted_accounting(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key, 'DEEPINFRA_API_KEY_FILE': '',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory).resolve() / 'quota.sqlite'), 'SH_DEEPINFRA_QUOTA_USD': '4',
            'SH_DEEPINFRA_SERVICE_TIER': 'standard',
        }):
            client = BudgetClient(DeepInfra(fake.host), max_tokens=11000)
            runner = ProofRunner(Agent(client, Workspace(directory), model=MODEL, ctx=1048576))
            failure = {'events': [{'error': {'code': 'internal_server_error'}}]}
            fake.replies = [failure, (PROOF, '', 37), (json.dumps(APPROVAL), '', 23)]
            result = runner.start('Prove x=x.', max_tokens=10240, max_predict=4096, verify_tokens=2048)
            self.assertEqual(result['status'], 'candidate_complete')
            self.assertEqual([c['charged_tokens'] for c in runner.state['calls']], [4096, 37, 23])
            self.assertEqual(len(set(c['key'] for c in runner.state['calls'])), 3)
            self.assertEqual((client.snapshot()['tokens'], client.snapshot()['measured_tokens']), (4156, 60))
            self.assertEqual((len(fake.completions), quota_status()['output_tokens']), (3, 60))
            with sqlite3.connect(os.environ['SH_DEEPINFRA_QUOTA_FILE']) as db:
                self.assertEqual(db.execute('SELECT output_tokens FROM calls WHERE charged IS NOT NULL ORDER BY rowid').fetchall(),
                                 [(None,), (37,), (23,)])

            # Each attempt fits the remaining shared output. The unknown first
            # dispatch consumes its fitted reservation. Only actual usage is
            # charged for the smaller replacement and its independent review.
            smaller = BudgetClient(DeepInfra(fake.host), max_tokens=4096)
            runner = ProofRunner(Agent(smaller, Workspace(directory), model=MODEL, ctx=1048576))
            fake.replies = [failure, (PROOF, '', 37), (json.dumps(APPROVAL), '', 23)]
            recovered = runner.start('Prove y=y.', max_tokens=10240, max_predict=4096, verify_tokens=2048)
            self.assertEqual(recovered['status'], 'candidate_complete')
            self.assertEqual([c['charged_tokens'] for c in runner.state['calls']], [2730, 37, 23])
            self.assertEqual([c['max_tokens'] for c in fake.completions[-3:]], [2730, 910, 1329])
            self.assertEqual((smaller.snapshot()['tokens'], smaller.snapshot()['measured_tokens']), (2790, 60))
            self.assertEqual((len(fake.completions), quota_status()['output_tokens']), (6, 120))
            # A configured solver minimum plus even a minimal verifier cannot
            # fit after an unknown failure. No replacement goes to the provider.
            small_floor = BudgetClient(DeepInfra(fake.host), max_tokens=4096)
            runner = ProofRunner(Agent(small_floor, Workspace(directory), model=MODEL, ctx=1048576))
            fake.replies = [failure]
            stopped = runner.start('Prove w=w.', max_tokens=10240, max_predict=4096, verify_tokens=2048,
                                   initial_min_solve_tokens=3968)
            self.assertEqual([c['charged_tokens'] for c in runner.state['calls']], [3968])
            self.assertEqual((small_floor.snapshot()['tokens'], len(fake.completions)), (3968, 7))
            # A local context refusal is also a confirmed non-dispatch, even
            # when the worker already checkpointed its proposed replacement.
            guarded = BudgetClient(DeepInfra(fake.host), max_tokens=11000)
            runner = ProofRunner(Agent(guarded, Workspace(directory), model=MODEL, ctx=1048576))
            fake.replies = [failure]
            with patch('mathagent.assistant_budget.count_input', side_effect=[(50, 'fixture'), (2000000, 'fixture')]):
                stopped = runner.start('Prove z=z.', max_tokens=10240, max_predict=4096, verify_tokens=2048)
            self.assertEqual(stopped['status'], 'paused')
            self.assertEqual([c['charged_tokens'] for c in runner.state['calls']], [4096, 0])
            self.assertEqual((guarded.snapshot()['tokens'], len(fake.completions)), (4096, 8))

    def test_a_busy_provider_is_waited_out_by_time_and_said_each_time(self):
        """DeepInfra's overload refusal (2026-11-26): it costs nothing, so a call keeps trying until its patience is
        used, says so at every refusal, and recognises the same refusal inside an accepted stream."""
        import mathagent.deepinfra as D
        from mathagent.deepinfra import busy_patience, generation_window, provider_busy
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        body = {'error': {'message': 'Model busy, retry later', 'type': 'invalid_request_error', 'code': 'engine_overloaded'}}
        busy = {'http_status': 429, 'body': body}
        with patch('mathagent.deepinfra.BACKOFF', (0.05,) * 5), patch.dict('mathagent.deepinfra._cooldown', {'until': 0.0}), \
                patch.dict(os.environ, {'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key,
                                        'DEEPINFRA_API_KEY_FILE': '', 'SH_DEEPINFRA_QUOTA_FILE': ''}):
            client = DeepInfra(fake.host, timeout=30)
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Synthetic example'}],
                       'options': {'num_predict': 128, 'num_ctx': 16384}, 'think': False}
            # More refusals than RETRIES: they are waited out and the answer arrives. The first refusal is said at
            # once, the next ones at most every NOTICE_EVERY seconds (all of them when that is 0).
            for every, expected in ((D.NOTICE_EVERY, 1), (0, RETRIES + 3)):
                notices = []
                fake.replies = [busy] * (RETRIES + 3) + [('Ready', '', 4)]
                with patch('mathagent.deepinfra.NOTICE_EVERY', every):
                    events = list(generation_stream(client, payload, lambda kind, text: notices.append((kind, text))))
                self.assertTrue(events[-1]['done'])
                self.assertEqual(len(notices), expected)
            self.assertTrue(all(kind == 'notice' and text.startswith('DeepInfra is busy (the model is temporarily at capacity); '
                                                                     'trying again in ') for kind, text in notices), notices)
            # The patience is a time bound: past it, the refusal is raised (and is the provider's being busy).
            fake.replies = [busy] * 50
            before = len(fake.completions)
            with busy_patience(0.12), self.assertRaises(DeepInfraRateLimit) as stopped:
                list(generation_stream(client, payload, lambda *_: None))
            self.assertTrue(provider_busy(stopped.exception))
            self.assertLess(len(fake.completions) - before, 10)
            # A caller that replaces the whole call itself (litreview) keeps no refusal retries here.
            fake.replies = [busy, ('Unused', '', 1)]
            before = len(fake.completions)
            with generation_window(client, replacement=True), self.assertRaises(DeepInfraRateLimit):
                list(client.stream(payload))
            self.assertEqual(len(fake.completions) - before, 1)
            fake.replies = []
            # The same refusal inside an accepted stream, before any output, is a temporary failure: sent again.
            fake.replies = [{'events': [body]}, ('Recovered', '', 4)]
            notices = []
            self.assertTrue(list(generation_stream(client, payload, lambda kind, text: notices.append(text)))[-1]['done'])
            self.assertIn('ended the request before producing output', notices[0])
            # Reworded busy messages are retried through the real classifier; credit limits and unreadable 429s
            # are still refused at once (one send).
            for detail, retried in [({'error': {'message': 'Model is busy, please retry later'}}, True),
                                    ({'error': {'message': 'Model busy, retry later (queue full)'}}, True),
                                    ({'detail': {'error': 'Engine overloaded'}}, False),  # nested deeper than we read
                                    ({'error': {'message': 'Insufficient balance, add credit; retry later'}}, False),
                                    ({'detail': 'something else'}, False)]:
                fake.replies = [{'http_status': 429, 'body': detail}, ('Ready', '', 4)]
                before = len(fake.completions)
                if retried:
                    self.assertTrue(list(generation_stream(client, payload, lambda *_: None))[-1]['done'])
                    self.assertEqual(len(fake.completions) - before, 2, detail)
                else:
                    with self.assertRaises(DeepInfraRateLimit) as stopped:
                        list(generation_stream(client, payload, lambda *_: None))
                    self.assertFalse(stopped.exception.retryable, detail)
                    self.assertEqual(len(fake.completions) - before, 1, detail)
                fake.replies = []
            # A plain HTTP 500 may come from the request: counted retries (RETRIES), not five minutes.
            fake.replies = [{'http_status': 500, 'body': {}}] * (RETRIES + 1) + [('Unused', '', 1)]
            before = len(fake.completions)
            with self.assertRaises(DeepInfraGenerationError):
                list(generation_stream(client, payload, lambda *_: None))
            self.assertEqual(len(fake.completions) - before, RETRIES + 1)
            fake.replies = []
            # Busy refusals do not use up the counted retries of a plain 500 that follows them.
            fake.replies = [busy] * RETRIES + [{'http_status': 500, 'body': {}}, ('Ready', '', 4)]
            before = len(fake.completions)
            self.assertTrue(list(generation_stream(client, payload, lambda *_: None))[-1]['done'])
            self.assertEqual(len(fake.completions) - before, RETRIES + 2)
            # Another call's cooldown that would outlast this call's patience: a call not yet sent is sent once
            # anyway (its own answer decides); refused, it gives up as busy at once.
            for reply, succeeds in ((('Ready', '', 4), True), (busy, False)):
                fake.replies = [reply]
                before = len(fake.completions)
                with patch.dict('mathagent.deepinfra._cooldown', {'until': time.monotonic() + 60}), busy_patience(5):
                    if succeeds:
                        self.assertTrue(list(generation_stream(client, payload, lambda *_: None))[-1]['done'])
                    else:
                        with self.assertRaises(DeepInfraRateLimit) as stopped:
                            list(generation_stream(client, payload, lambda *_: None))
                        self.assertTrue(provider_busy(stopped.exception))
                self.assertEqual(len(fake.completions) - before, 1)
            # A wait on another call's cooldown is said, and so is this call's own first refusal right after it.
            notices = []
            fake.replies = [busy, ('Ready', '', 4)]
            with patch.dict('mathagent.deepinfra._cooldown', {'until': time.monotonic() + 1.2}):
                list(generation_stream(client, payload, lambda kind, text: notices.append(text)))
            self.assertEqual(len(notices), 2, notices)
            self.assertIn('another request was just refused', notices[0])
            self.assertIn('trying again in', notices[1])
        self.assertEqual(D._generation_failure(body)[0], 'service_unavailable')
        self.assertEqual(D._generation_failure({'error': {'message': 'Insufficient credit, retry later'}})[0], 'quota')
        # The pause: never under the backoff step (a Retry-After of 0 must not make calls send back to back), at most
        # BUSY_STEP when the provider names no wait, the provider's longer Retry-After otherwise.
        with patch('mathagent.deepinfra.random.uniform', return_value=1.0):
            self.assertEqual(D._refusal_delay(0, '0', 0), D.BACKOFF[0])
            self.assertEqual(D._refusal_delay(60, None, 4), D.BUSY_STEP)
            self.assertEqual(D._refusal_delay(90, '90', 1), 90)
            self.assertEqual(D._refusal_delay(0, '0', 9), D.BUSY_STEP)

    def test_conversation_retries_only_failures_before_output_within_one_shared_allowance(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        with patch('mathagent.deepinfra.BACKOFF', (0,) * 5), patch.dict(os.environ, {'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key,
                                     'DEEPINFRA_API_KEY_FILE': '', 'SH_DEEPINFRA_QUOTA_FILE': ''}):
            client = DeepInfra(fake.host, timeout=5)
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Synthetic example'}],
                       'options': {'num_predict': 128, 'num_ctx': 16384}, 'think': False}
            failure = {'error': {'code': 'server_error'}}
            busy = {'http_status': 429, 'body': {'detail': 'Rate limited'}, 'headers': {'Retry-After': '0'}}
            ending = {'events': [failure]}
            for events in ([{'choices': [{'delta': {'content': 'prefix'}}]}, failure],
                           [{'choices': [{'delta': {'reasoning_content': 'prefix'}}]}, failure],
                           [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'x'}]}}]}, failure],
                           [{**failure, 'choices': [{'delta': {'content': 'prefix'}}]}],
                           [{'error': {'code': 'insufficient_quota'}}], [{'error': {'code': 'invalid_request'}}],
                           [{'error': {'code': 'unclassified'}}], []):
                with self.subTest(events=events):
                    before = len(fake.completions)
                    fake.replies = [{'events': events}]
                    with self.assertRaises(DeepInfraGenerationError):
                        list(generation_stream(client, payload, lambda *_: self.fail('Unexpected retry')))
                    self.assertEqual(len(fake.completions) - before, 1)
            # Failures before any output share RETRIES replacements; then the call fails. A refusal (429 or 5xx before
            # generation) costs nothing and is waited out by time instead, so it does not use a replacement.
            for sequence in ([busy] + [ending] * (RETRIES + 1), [ending, busy] + [ending] * RETRIES, [ending] * (RETRIES + 1)):
                before = len(fake.completions)
                fake.replies = list(sequence)
                with self.assertRaises(DeepInfraGenerationError):
                    list(generation_stream(client, payload, lambda *_: None))
                self.assertEqual(len(fake.completions) - before, len(sequence))
            before = len(fake.completions)
            fake.replies = [ending, busy, ending, ('Recovered', '', 4)]
            self.assertTrue(list(generation_stream(client, payload, lambda *_: None))[-1]['done'])
            self.assertEqual(len(fake.completions) - before, 4)
            # A replacement differs only by leading spaces, which made silent starts rarer on DeepInfra; a refused
            # request is sent again unchanged, and the refusal does not use a replacement (so two spaces at the end).
            self.assertEqual([c['messages'][0]['content'] for c in fake.completions[-4:]],
                             ['Synthetic example', ' Synthetic example', ' Synthetic example', '  Synthetic example'])
            before = len(fake.completions)
            client.cancel = threading.Event()
            fake.replies = [ending]
            with self.assertRaises(KeyboardInterrupt):
                list(generation_stream(client, payload, lambda *_: client.cancel.set()))
            self.assertEqual(len(fake.completions) - before, 1)
            client.cancel.clear()
            before = len(fake.completions)
            fake.replies = [ending]
            # A faked clock: the shared cooldown left by the 429s above was set on the real clock.
            with patch.dict('mathagent.deepinfra._cooldown', {'until': 0.0}), \
                    patch('mathagent.deepinfra.time.monotonic', return_value=100) as clock:
                with self.assertRaises(DeepInfraGenerationError):
                    list(generation_stream(client, payload, lambda *_: setattr(clock, 'return_value', 110)))
            self.assertEqual(len(fake.completions) - before, 1)

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
                self.assertEqual(db.execute('SELECT input_tokens, output_tokens FROM calls WHERE charged IS NOT NULL ORDER BY rowid').fetchall(),
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

    def test_pre_stream_rejection_retries_and_charges_only_the_served_call(self):
        fake = FakeDeepInfra()
        self.addCleanup(fake.close)
        # A provider that asks for no wait (Retry-After: 0) is retried until MAX_REFUSALS; here as many as RETRIES + 1.
        with tempfile.TemporaryDirectory() as directory, patch('mathagent.deepinfra.BACKOFF', (0,) * 5), \
                patch('mathagent.deepinfra.MAX_REFUSALS', RETRIES + 1), patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': fake.key, 'DEEPINFRA_API_KEY_FILE': '',
            'SH_DEEPINFRA_SERVICE_TIER': 'standard', 'SH_DEEPINFRA_QUOTA_USD': '4',
            'SH_DEEPINFRA_QUOTA_FILE': str(Path(directory).resolve() / 'quota.sqlite'),
        }):
            payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}],
                       'options': {'num_predict': 128}, 'think': False}
            client = DeepInfra(fake.host)
            busy = {'http_status': 429, 'body': {'error': {'code': 'engine_overloaded',
                    'type': 'invalid_request_error', 'message': 'Model busy, retry later', 'param': None}},
                    'headers': {'Retry-After': '0'}}
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
                self.assertEqual(stopped.exception.retryable, reason == 'temporarily')
                self.assertNotIn(fake.key, str(stopped.exception))
            fake.replies = [{**busy, 'body': {'error': {'code': 'engine_overloaded'}}}] * (RETRIES + 1)
            with self.assertRaises(DeepInfraRateLimit) as stopped:
                list(client.stream(payload))
            self.assertTrue(stopped.exception.retryable)
            self.assertIn('temporarily at capacity', str(stopped.exception))
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
            self.assertEqual(len(fake.completions), 9 + RETRIES)
            with sqlite3.connect(os.environ['SH_DEEPINFRA_QUOTA_FILE']) as db:
                rows = db.execute('SELECT input_tokens, output_tokens FROM calls WHERE charged IS NOT NULL ORDER BY rowid').fetchall()
            self.assertEqual(rows, [(200, 5)])  # a refusal before the stream costs nothing and records nothing

            # A failure in an accepted stream has unknown usage: it is charged an estimate (the whole input and
            # the streamed characters plus a margin).
            client.cancel.clear()
            fake.replies = [busy, {'events': [{'choices': [{'index': 0, 'delta': {'content': 'Partial'}, 'finish_reason': None}]},
                                        {'error': {'code': 429, 'message': fake.key}}]}]
            with self.assertRaises(DeepInfraGenerationError) as stopped:
                list(client.stream(payload))
            self.assertFalse(rejected_before_inference(stopped.exception))
            self.assertNotIn(fake.key, str(stopped.exception))
            self.assertEqual(len(fake.completions), 11 + RETRIES)
            status = quota_status()
            self.assertNotIn('reserved_nanos', status)
            self.assertGreater(status['charged_nanos'], 43000)

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
            os.environ['SH_DEEPINFRA_SERVICE_TIER'] = 'priority'
            with self.assertRaisesRegex(AgentError, 'Standard service tier'):
                list(DeepInfra(fake.host).stream(payload))
            self.assertEqual(quota_status()['limit_nanos'], 100000)  # the status page does not depend on the tier

    def test_ledger_charges_prices_and_keeps_what_an_old_ledger_charged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'quota.sqlite'
            quota = HostedQuota(str(path), '0.001')
            quota.admit()
            quota.charge(1000, 100, cost_usd=0.0004)  # a reported price wins over the rates
            quota.charge(1000, 100)  # 1000 * 200 + 100 * 600 nanodollars
            quota.charge(5000, 500, cost_usd=0.5, estimated=True)  # an estimate never takes a price
            self.assertEqual(quota.status()['charged_nanos'], 400000 + 260000 + 1300000)
            self.assertEqual((quota.status()['input_tokens'], quota.status()['output_tokens']), (2000, 200))
            with self.assertRaises(TesterAllowanceInsufficient):
                quota.admit()
            for bad in ((-1, 0), (1, None), (True, 1)):
                with self.assertRaises(AgentError):
                    quota.charge(*bad)
            with self.assertRaisesRegex(AgentError, 'settings changed'):
                HostedQuota(str(path), '5')

            # Individual legacy calls survive. Pending holds are ignored and remain settleable by the old process.
            legacy = Path(directory).resolve() / 'legacy.sqlite'
            with sqlite3.connect(legacy) as db:
                db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), budget INTEGER, input_rate TEXT, output_rate TEXT)')
                db.execute("INSERT INTO settings VALUES (1, 2500000000, '0.2', '0.6')")
                db.execute('CREATE TABLE calls (id TEXT PRIMARY KEY, input_cap INTEGER, output_cap INTEGER, reserved INTEGER, charged INTEGER, input_tokens INTEGER, output_tokens INTEGER)')
                db.executemany('INSERT INTO calls VALUES (?,?,?,?,?,?,?)', [
                    ('a', 9, 9, 900000, 43000, 200, 5), ('b', 9, 9, 900000, 70000, None, None), ('c', 9, 9, 900000, None, None, None)])
            db.close()
            os.chmod(legacy, 0o600)
            migrated = HostedQuota(str(legacy))
            self.assertEqual(migrated.status()['charged_nanos'], 113000)
            self.assertEqual((migrated.status()['input_tokens'], migrated.status()['output_tokens']), (200, 5))
            self.assertEqual(HostedQuota(str(legacy)).status()['charged_nanos'], 113000)  # once only

            with sqlite3.connect(legacy) as db:
                self.assertEqual(db.execute('SELECT id, charged FROM calls ORDER BY id').fetchall(),
                                 [('a', 43000), ('b', 70000), ('c', None)])

            # The interim charges-only schema can be reopened by the reservation-era binary after migration.
            interim = Path(directory).resolve() / 'interim.sqlite'
            with sqlite3.connect(interim) as db:
                db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY, budget INTEGER, input_rate TEXT, output_rate TEXT)')
                db.execute("INSERT INTO settings VALUES (1, 2500000000, '0.2', '0.6')")
                db.execute('CREATE TABLE charges (charged INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER)')
                db.executemany('INSERT INTO charges VALUES (?,?,?)', [(300, 7, 4), (500, None, None)])
            os.chmod(interim, 0o600)
            HostedQuota(interim).charge(10, 20, cost_usd=.000001)
            restored = HostedQuota(interim)
            self.assertEqual(restored.status()['charged_nanos'], 1800)
            with sqlite3.connect(interim) as db:
                self.assertEqual(db.execute('SELECT SUM(COALESCE(charged,reserved)), SUM(input_tokens), SUM(output_tokens), '
                                            'COUNT(DISTINCT id) FROM calls').fetchone(), (1800, 17, 24, 3))

    def test_accepted_cancellations_and_errors_settle_once(self):
        from mathagent.openrouter import OpenRouter
        from tests.test_openrouter import FakeOpenRouter, PAYLOAD, environment
        fake = FakeOpenRouter()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, environment(fake, SH_DEEPINFRA_QUOTA_FILE=str(
                Path(directory).resolve() / 'quota.sqlite')):
            client = OpenRouter(fake.host)
            part = {'choices': [{'delta': {'content': 'partial'}}]}
            terminal = {'choices': [{'delta': {'content': 'end'}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 25, 'completion_tokens': 3, 'cost': .000127}}
            fake.replies = [{'events': [part]}, {'events': [terminal]}, {'events': [terminal]}]
            stream = client.stream(PAYLOAD)
            next(stream)
            stream.close()
            estimate = quota_status()['charged_nanos']
            self.assertGreater(estimate, 0)
            self.assertEqual(quota_status()['output_tokens'], 0)
            stream = client.stream(PAYLOAD)
            next(stream)  # a terminal delta already carried the price
            stream.close()
            self.assertEqual(quota_status()['charged_nanos'], estimate + 127000)
            stream = client.stream(PAYLOAD)
            next(stream)
            next(stream)  # accounting completes before the done event is delivered
            stream.close()
            self.assertEqual(quota_status()['charged_nanos'], estimate + 254000)
            error = {'choices': [], 'error': {'code': 500}, 'usage': terminal['usage']}
            too_many = {**terminal, 'usage': {**terminal['usage'], 'completion_tokens': 129}}
            bad_call = {'choices': [{'finish_reason': 'tool_calls', 'delta': {'tool_calls': [
                {'index': 0, 'id': 't', 'function': {'name': 'f', 'arguments': '{'}}]}}], 'usage': terminal['usage']}
            for frame, failure in [(error, DeepInfraGenerationError), (too_many, TokenBudgetError), (bad_call, ToolCallError)]:
                before = quota_status()['charged_nanos']
                fake.replies = [{'events': [frame]}]
                with self.assertRaises(failure) as stopped:
                    list(client.stream(PAYLOAD))
                self.assertEqual(stopped.exception.stats['cost_usd'], .000127)
                self.assertEqual(quota_status()['charged_nanos'] - before, 127000)

            read_stream = client._sse
            def cancel_early(response, deadline):
                for raw in read_stream(response, deadline):
                    if raw == '[DONE]':
                        raise KeyboardInterrupt
                    yield raw
            fake.replies = [{'events': [part]}]
            before = quota_status()['charged_nanos']
            with patch.object(client, '_sse', side_effect=cancel_early), self.assertRaises(KeyboardInterrupt):
                list(client.stream(PAYLOAD))
            self.assertEqual(quota_status()['charged_nanos'] - before, estimate)

    def test_parallel_admission_and_busy_retry_at_threshold(self):
        from concurrent.futures import ThreadPoolExecutor
        from mathagent.openrouter import OpenRouter
        from tests.test_openrouter import FakeOpenRouter, PAYLOAD, environment
        fake = FakeOpenRouter()
        self.addCleanup(fake.close)
        with tempfile.TemporaryDirectory() as directory, environment(fake, SH_DEEPINFRA_QUOTA_FILE=str(
                Path(directory).resolve() / 'quota.sqlite'), SH_DEEPINFRA_QUOTA_USD='2'):
            quota = HostedQuota.from_environment()
            quota.charge(1, 1, 1.999)
            starts = threading.Barrier(10)
            def finish(_):
                quota.admit()
                starts.wait(timeout=10)
                quota.charge(1, 1, .0004)
            def finish_other_calls(_):
                with ThreadPoolExecutor(max_workers=10) as pool:
                    list(pool.map(finish, range(10)))

            fake.replies = [{'http_status': 429, 'body': {'error': {'code': 429}}}, ('Unused', '', 1)]
            with patch('mathagent.deepinfra._cooldown', {'until': 0.0}), \
                    patch('mathagent.deepinfra._refusal_delay', return_value=0), \
                    patch('mathagent.deepinfra.time.sleep', side_effect=finish_other_calls):
                with self.assertRaises(TesterAllowanceInsufficient):
                    list(OpenRouter(fake.host).stream(PAYLOAD))
            self.assertEqual(quota.status()['charged_nanos'], 2003000000)
            self.assertEqual(len(fake.completions), 1)
