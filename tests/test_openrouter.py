# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The OpenRouter profile: the DeepInfra hosted transport with OpenRouter's address, key, model name, wording and
routing rules, against a loopback server that streams the way OpenRouter does (measured 2026-11-27)."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.deepinfra import MODEL
from mathagent.gui import store
from mathagent.model_limits import MODEL_OUTPUT_TOKENS
from mathagent.openrouter import OpenRouter, WIRE_MODEL
from mathagent.tools import Workspace
from test_gui import GuiCase
from test_research import FakeClient, response, tool


class FakeOpenRouter:
    """Serve the OpenRouter catalog and stream scripted (content, reasoning, count) replies."""

    key = 'sk-or-v1-' + '0123456789abcdef' * 4

    def __init__(self):
        self.replies, self.completions = [], []
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
                if self.path == '/api/v1/models':
                    self.respond(200, {'data': [{'id': WIRE_MODEL}, {'id': 'someone/other-model'}]})
                else:
                    self.respond(404, {'error': {'code': 404, 'message': 'Not found'}})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.headers.get('Authorization') != 'Bearer ' + fake.key:
                    self.respond(401, {'error': {'code': 401, 'message': 'No auth credentials found'}})
                    return
                if self.path != '/api/v1/chat/completions':
                    self.respond(404, {'error': {'code': 404, 'message': 'Not found'}})
                    return
                fake.completions.append(data)
                frame = {'id': 'gen-1', 'object': 'chat.completion.chunk', 'created': 1, 'model': WIRE_MODEL,
                         'provider': 'DeepInfra'}
                content, reasoning, count = fake.replies.pop(0)
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


class OpenRouterWorkflowTests(GuiCase):
    fake_class = FakeOpenRouter
    model = MODEL
    extra_args = ('--backend', 'openai', '--api-profile', 'openrouter', '--allow-remote-model',
                  '--gui-model-concurrency', '1', '--ctx', '65536')

    def setUp(self):
        env = patch.dict(os.environ, {'OPENROUTER_ALLOW_LOOPBACK': '1', 'OPENROUTER_API_KEY': FakeOpenRouter.key,
                                      'OPENROUTER_API_KEY_FILE': '', 'SH_DEEPINFRA_QUOTA_FILE': '', 'SH_OPENROUTER_PROVIDERS': ''})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()

    def test_explore_returns_tool_reasoning_only_inside_the_current_task(self):
        call = dict(tool('list_files'), id='call_lookup')
        first = response(calls=[call])
        first[0]['message']['thinking'] = 'Check the available evidence.'
        client = FakeClient([first, response('Done.'), response('Fresh answer.')])
        client.profile = 'openrouter'
        agent = Agent(client, Workspace(self.root), model=MODEL, ctx=65536, mode='explore')
        self.assertEqual(agent.run('List the available files.'), 'Done.')
        transport = OpenRouter()
        messages = transport._payload(client.requests[1])['messages']
        assistant, result = messages[-2:]
        self.assertEqual(assistant['reasoning_content'], first[0]['message']['thinking'])
        self.assertEqual(assistant['tool_calls'][0]['id'], result['tool_call_id'])
        self.assertEqual(agent.run('Start a new task.'), 'Fresh answer.')
        later = transport._payload(client.requests[2])['messages']
        self.assertFalse(any('reasoning_content' in message for message in later))

    def test_an_assistant_turn_runs_on_openrouter_with_ds4_budgets(self):
        status = self.ok('GET', '/api/status')
        self.assertEqual(status['defaults']['budget_profile'], 'ds4.1-flash-1017')  # DS4 presets, not local budgets
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
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
        self.assertEqual([c['temperature'] for c in sent], [1.0, 1.0])
        self.assertTrue(all(c['provider']['zdr'] and c['provider']['data_collection'] == 'deny' for c in sent))
        # The DS4 answer ceiling (384k), fitted into this test's 64k context, not the local default of --predict 1024.
        self.assertTrue(1024 < sent[-1]['max_tokens'] < MODEL_OUTPUT_TOKENS, sent[-1]['max_tokens'])
