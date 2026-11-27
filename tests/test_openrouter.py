# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The OpenRouter profile: the DeepInfra hosted transport with OpenRouter's address, key, model name, wording and
routing rules, against a loopback server that streams the way OpenRouter does (measured 2026-11-27)."""
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from mathagent import cli
from mathagent.agent import AgentError
from mathagent.api_profiles import create_profile_client, profile_class
from mathagent.deepinfra import (MODEL, DeepInfraGenerationError, DeepInfraRateLimit, generation_stream,
                                 provider_busy)
from mathagent.gui import store
from mathagent.model_limits import HOSTED_DS4, MODEL_OUTPUT_TOKENS, ds4_args, is_ds4
from mathagent.openrouter import DEFAULT_PROVIDERS, MAX_PRICE, WIRE_MODEL, OpenRouter, routing
from test_gui import GuiCase

BUSY_BODY = {'error': {'code': 429, 'message': 'deepseek/deepseek-v4.1-flash-1017 is temporarily rate-limited upstream. '
                                               'Please retry shortly.', 'metadata': {'provider_name': 'DeepInfra'}}}


class FakeOpenRouter:
    """OpenRouter's API on loopback: catalog, chat completions; replies are (content, reasoning, count) tuples,
    {'http_status', 'body'} refusals, or {'events': [...]} raw events."""

    key = 'sk-or-v1-' + '0123456789abcdef' * 4

    def __init__(self):
        self.replies, self.completions, self.requests = [], [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append(('GET', self.path, self.headers.get('Authorization')))
                if self.path == '/api/v1/models':
                    self.respond(200, {'data': [{'id': WIRE_MODEL}, {'id': 'someone/other-model'}]})
                else:
                    self.respond(404, {'error': {'code': 404, 'message': 'Not found'}})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fake.requests.append(('POST', self.path, self.headers.get('Authorization')))
                if self.headers.get('Authorization') != 'Bearer ' + fake.key:
                    self.respond(401, {'error': {'code': 401, 'message': 'No auth credentials found'}})
                    return
                if self.path != '/api/v1/chat/completions':
                    self.respond(404, {'error': {'code': 404, 'message': 'Not found'}})
                    return
                fake.completions.append(data)
                reply = fake.replies.pop(0)
                if isinstance(reply, dict) and 'http_status' in reply:
                    self.respond(reply['http_status'], reply.get('body', {}))
                    return
                frame = {'id': 'gen-1', 'object': 'chat.completion.chunk', 'created': 1, 'model': WIRE_MODEL,
                         'provider': 'DeepInfra'}
                if isinstance(reply, dict):
                    events = reply['events']
                else:
                    content, reasoning, count = reply
                    # The shape OpenRouter streams: reasoning as delta.reasoning (+ reasoning_details), content, then
                    # the finish frame twice, the second carrying usage and cost.
                    events = [
                        {**frame, 'choices': [{'index': 0, 'delta': {'content': '', 'role': 'assistant', 'reasoning': reasoning,
                                                                     'reasoning_details': [{'type': 'reasoning.text', 'text': reasoning}]},
                                               'finish_reason': None}]},
                        {**frame, 'choices': [{'index': 0, 'delta': {'content': content, 'role': 'assistant'}, 'finish_reason': None}]},
                        {**frame, 'choices': [{'index': 0, 'delta': {'content': '', 'role': 'assistant', 'reasoning': None},
                                               'finish_reason': 'stop', 'native_finish_reason': 'stop'}]},
                        {**frame, 'service_tier': 'default',
                         'choices': [{'index': 0, 'delta': {'content': '', 'role': 'assistant'}, 'finish_reason': 'stop',
                                      'native_finish_reason': 'stop'}],
                         'usage': {'prompt_tokens': 200, 'completion_tokens': count, 'total_tokens': 200 + count,
                                   'cost': 0.000013, 'is_byok': False}},
                    ]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b': OPENROUTER PROCESSING\n\n')
                for event in events:
                    self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.host = f'http://127.0.0.1:{self.server.server_port}/api/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def environment(fake, **extra):
    values = {'OPENROUTER_ALLOW_LOOPBACK': '1', 'OPENROUTER_API_KEY': fake.key, 'OPENROUTER_API_KEY_FILE': '',
              'SH_DEEPINFRA_QUOTA_FILE': '', 'SH_OPENROUTER_PROVIDERS': '', **extra}
    return patch.dict(os.environ, values)


PAYLOAD = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Synthetic example'}],
           'options': {'num_predict': 128, 'num_ctx': 16384}, 'think': True}


class OpenRouterTransportTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOpenRouter()
        self.addCleanup(self.fake.close)
        self.env = environment(self.fake)
        self.env.start()
        self.addCleanup(self.env.stop)
        backoff = patch('mathagent.deepinfra.BACKOFF', (0.02,) * 5)
        backoff.start()
        self.addCleanup(backoff.stop)
        cooldown = patch('mathagent.deepinfra._cooldown', {'until': 0.0})
        cooldown.start()
        self.addCleanup(cooldown.stop)

    def client(self):
        return OpenRouter(self.fake.host, timeout=30)

    def test_profile_is_hosted_ds4_and_selectable(self):
        self.assertIn('openrouter', HOSTED_DS4)
        self.assertIs(profile_class('openrouter'), OpenRouter)
        client = create_profile_client('openrouter', self.fake.host)
        self.assertTrue(is_ds4(client))
        self.assertEqual(OpenRouter.default_base_url(), 'https://openrouter.ai/api/v1')
        args = cli.parser().parse_args(['--backend', 'openai', '--api-profile', 'openrouter'])
        self.assertTrue(ds4_args(args))
        # The model name stays Square Harness's own; only the wire name differs, so saved work stays portable.
        self.assertEqual(client.models()[0], MODEL)

    def test_request_carries_the_routing_rules(self):
        client = self.client()
        data = client._payload({**PAYLOAD, 'format': {'type': 'object', 'properties': {'x': {'type': 'integer'}}}})
        self.assertEqual(data['model'], WIRE_MODEL)
        self.assertEqual(data['provider'], {'order': list(DEFAULT_PROVIDERS), 'only': list(DEFAULT_PROVIDERS),
                                            'allow_fallbacks': True, 'require_parameters': True, 'data_collection': 'deny',
                                            'zdr': True, 'quantizations': ['fp8'], 'max_price': MAX_PRICE})
        self.assertEqual((data['service_tier'], data['usage'], data['plugins']),
                         ('default', {'include': True}, [{'id': 'context-compression', 'enabled': False}]))
        self.assertNotIn('stream_options', data)
        self.assertEqual((data['reasoning_effort'], data['max_tokens']), ('high', 128))
        self.assertEqual(data['response_format']['type'], 'json_schema')
        with patch.dict(os.environ, {'SH_OPENROUTER_PROVIDERS': 'deepinfra, cohere'}):
            self.assertEqual(routing()['only'], ['deepinfra', 'cohere'])
        with patch.dict(os.environ, {'SH_OPENROUTER_PROVIDERS': 'deepinfra;rm -rf'}), self.assertRaises(AgentError):
            routing()
        # With a tester ledger, the price cap is the ledger's rates, so its reservations stay an upper bound.
        with patch.dict(os.environ, {'SH_DEEPINFRA_QUOTA_FILE': '/tmp/ledger.sqlite'}):
            self.assertEqual(routing()['max_price'], {'prompt': '0.20', 'completion': '0.60'})
        with self.assertRaises(AgentError):  # the legacy V4 model is not offered here
            client._payload({**PAYLOAD, 'model': 'deepseek-ai/DeepSeek-V4-Flash-0731'})

    def test_stream_reads_reasoning_and_the_repeated_finish_frame(self):
        self.fake.replies = [('The answer.', 'Some reasoning.', 7)]
        events = list(self.client().stream(PAYLOAD))
        text = ''.join(e['message'].get('content', '') for e in events if not e.get('done'))
        thinking = ''.join(e['message'].get('thinking', '') for e in events if not e.get('done'))
        self.assertEqual((text, thinking), ('The answer.', 'Some reasoning.'))
        self.assertEqual((events[-1]['done_reason'], events[-1]['eval_count'], events[-1]['prompt_eval_count']), ('stop', 7, 200))
        # Output after the finish is still refused: only an empty repeated finish frame is accepted.
        frame = {'choices': [{'index': 0, 'delta': {'content': 'late'}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
        self.fake.replies = [{'events': [{'choices': [{'index': 0, 'delta': {'content': 'x'}, 'finish_reason': 'stop'}]}, frame]}]
        with self.assertRaises(DeepInfraGenerationError):
            list(self.client().stream(PAYLOAD))

    def test_refusals_are_classified_in_openrouter_wording(self):
        notices = []
        # Rate-limited upstream: waited out and announced with the provider's name.
        self.fake.replies = [{'http_status': 429, 'body': BUSY_BODY}, ('Ready', '', 3)]
        self.assertTrue(list(generation_stream(self.client(), PAYLOAD, lambda kind, text: notices.append(text)))[-1]['done'])
        self.assertTrue(notices and notices[0].startswith('OpenRouter is busy ('), notices)
        # The in-flight budget (a 402 that frees up as calls finish) is waited out, and never reads as 'HTTP 402'.
        inflight = {'http_status': 402, 'body': {'error': {'code': 402, 'message': 'Request would exceed your available credits',
                                                           'metadata': {'limit_source': 'openrouter_in_flight_budget'}}}}
        self.fake.replies = [inflight, ('Ready', '', 3)]
        self.assertTrue(list(generation_stream(self.client(), PAYLOAD, lambda *_: None))[-1]['done'])
        with patch('mathagent.deepinfra.BUSY_PATIENCE', 0.01), self.assertRaises(DeepInfraRateLimit) as stopped:
            self.fake.replies = [inflight] * 3
            list(generation_stream(self.client(), PAYLOAD, lambda *_: None))
        self.assertNotIn('HTTP 402', str(stopped.exception))
        self.assertTrue(provider_busy(stopped.exception))
        # Out of credit, or a request no allowed provider can serve: fatal at once.
        for body, status, text in ((
                {'error': {'code': 402, 'message': 'Insufficient credits'}}, 402, 'OpenRouter HTTP 402'),
                ({'error': {'code': 503, 'message': 'No endpoints found matching your data policy'}}, 503, 'No allowed provider')):
            self.fake.replies = [{'http_status': status, 'body': body}]
            before = len(self.fake.completions)
            with self.assertRaises(AgentError) as stopped:
                list(generation_stream(self.client(), PAYLOAD, lambda *_: None))
            self.assertIn(text, str(stopped.exception))
            self.assertEqual(len(self.fake.completions) - before, 1)
        # A gateway failure before streaming is waited out like DeepInfra's.
        self.fake.replies = [{'http_status': 502, 'body': {'error': {'code': 502, 'message': 'Bad gateway'}}}, ('Ready', '', 3)]
        self.assertTrue(list(generation_stream(self.client(), PAYLOAD, lambda *_: None))[-1]['done'])

    def test_an_upstream_failure_inside_the_stream_is_replaced_before_output(self):
        failed = {'events': [{'id': 'gen-1', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': ''},
                              'finish_reason': 'error'}], 'error': {'code': 502, 'message': 'Provider returned error'}}]}
        notices = []
        self.fake.replies = [failed, ('Recovered', '', 4)]
        events = list(generation_stream(self.client(), PAYLOAD, lambda kind, text: notices.append(text)))
        self.assertTrue(events[-1]['done'])
        self.assertIn('ended the request before producing output', notices[0])

    def test_credentials_and_endpoints_are_pinned(self):
        with patch.dict(os.environ, {'OPENROUTER_ALLOW_LOOPBACK': ''}), self.assertRaises(AgentError) as stopped:
            OpenRouter(self.fake.host)
        self.assertIn('OpenRouter requires https://openrouter.ai/api/v1', str(stopped.exception))
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}), self.assertRaises(AgentError) as stopped:
            OpenRouter(self.fake.host)
        self.assertIn('OPENROUTER_API_KEY_FILE', str(stopped.exception))
        client = self.client()
        with self.assertRaises(AgentError):
            client._request_url(self.fake.host.rsplit('/api/v1', 1)[0] + '/api/v1/keys', {})
        # The public catalog is read without the key.
        client.models()
        self.assertIsNone(next(auth for method, path, auth in self.fake.requests if path == '/api/v1/models'))
        # Native images stay DeepInfra-only until verified on OpenRouter.
        client.image_refs = [{'id': 'x'}]
        with self.assertRaises(AgentError):
            client._payload(PAYLOAD)


class OpenRouterWorkflowTests(GuiCase):
    fake_class = FakeOpenRouter
    model = MODEL
    extra_args = ('--backend', 'openai', '--api-profile', 'openrouter', '--offline', '--allow-remote-model',
                  '--gui-model-concurrency', '1', '--ctx', '65536')

    def setUp(self):
        env = patch.dict(os.environ, {'OPENROUTER_ALLOW_LOOPBACK': '1', 'OPENROUTER_API_KEY': FakeOpenRouter.key,
                                      'OPENROUTER_API_KEY_FILE': '', 'SH_DEEPINFRA_QUOTA_FILE': '', 'SH_OPENROUTER_PROVIDERS': ''})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()

    def test_an_assistant_turn_runs_on_openrouter_with_ds4_budgets(self):
        status = self.ok('GET', '/api/status')
        self.assertEqual(status['defaults']['budget_profile'], 'ds4.1-flash-1017')  # DS4 presets, not local budgets
        chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.fake.replies = [(json.dumps({'route': 'direct', 'effort': 'low', 'tasks': [], 'reason': 'A greeting.'}), '', 9),
                             ('Hello! How can I help?', 'The user greets me.', 7)]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'hi !'})
        task = self.wait_task(timeout=30)
        self.assertEqual(task['state'], 'done', task)
        state = store.load_chat(self.root, chat)['assistant_state']
        self.assertEqual((state['proof_routing']['route'], state['status']), ('direct', 'complete'))
        self.assertIn('Hello! How can I help?', state['answer'])
        sent = self.fake.completions
        self.assertEqual({c['model'] for c in sent}, {WIRE_MODEL})
        self.assertTrue(all(c['provider']['zdr'] and c['provider']['data_collection'] == 'deny' for c in sent))
        # The DS4 answer ceiling (384k), fitted into this test's 64k context, not the local default of --predict 1024.
        self.assertTrue(1024 < sent[-1]['max_tokens'] < MODEL_OUTPUT_TOKENS, sent[-1]['max_tokens'])


if __name__ == '__main__':
    unittest.main()
