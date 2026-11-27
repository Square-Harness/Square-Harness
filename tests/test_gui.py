# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""GUI smoke tests and shared fixtures for scripted HTTP model servers."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mathagent import cli
from mathagent.deepinfra import LEGACY_MODEL as DEEPINFRA_LEGACY_MODEL, MODEL as DEEPINFRA_MODEL
from mathagent.gui import server
from mathagent.gui.hub import Hub
from mathagent.gui.watch import Watcher
from mathagent.ledger import ProofStore
from mathagent.literature import LiteratureError, LiteratureTools

MODEL = 'test-model:latest'


def text(content):
    return [{'message': {'content': content}, 'done': True, 'done_reason': 'stop',
             'eval_count': 20, 'prompt_eval_count': 200}]


class FakeOllama:
    """Serve scripted /api/chat replies on loopback."""

    def __init__(self):
        self.replies, self.requests = [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({'models': [{'name': MODEL}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                fake.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                events = fake.replies.pop(0) if fake.replies else text('Unexpected extra call.')
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                for event in events:
                    try:
                        self.wfile.write(json.dumps(event).encode() + b'\n')
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GuiCase(unittest.TestCase):
    extra_args = ()
    fake_class = FakeOllama
    model = MODEL

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        fetch = patch.object(LiteratureTools, '_fetch', side_effect=LiteratureError('Fixture source unavailable'))
        fetch.start()
        self.addCleanup(fetch.stop)
        self.fake = self.fake_class()
        self.addCleanup(self.fake.close)
        args = cli.parser().parse_args(['--workspace', str(self.root), '--host', self.fake.host, '--model', self.model,
                                        '--ctx', '16384', '--predict', '1024', '--proof-solve-tokens', '4096',
                                        '--proof-verify-tokens', '2048', '--gui-concurrency', '1', *self.extra_args])
        args.workspace = self.root
        args.gui_auto_titles = False  # Scripted workflows opt into cosmetic model calls explicitly.
        self.token = server.workspace_token(self.root)
        self.hub = Hub(args)
        self.server = server.bind('127.0.0.1', 0, self.hub, self.token)
        self.watcher = Watcher(self.root, self.hub.bus, interval=0.05)
        self.watcher.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.addCleanup(self.stop)

    def stop(self):
        self.hub.shutdown(5)
        self.watcher.stop()
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, auth=True, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        sent = {'Host': f'127.0.0.1:{self.port}'}
        if auth:
            sent['Authorization'] = 'Bearer ' + self.token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            sent['Content-Type'] = 'application/json'
        sent.update(headers or {})
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        try:
            value = json.loads(payload) if payload else {}
        except ValueError:
            value = payload.decode(errors='replace')
        return response.status, value, dict(response.getheaders())

    def ok(self, method, path, body=None):
        status, value, _ = self.request(method, path, body)
        self.assertEqual(status, 200, value)
        return value

    def wait_task(self, states=('done', 'paused', 'error'), timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.ok('GET', '/api/task')['task']
            if task and task['state'] in states:
                return task
            time.sleep(0.05)
        self.fail(f'task did not reach {states}: {task}')


class SecurityTests(GuiCase):
    def test_api_requires_the_workspace_token(self):
        status, value, headers = self.request('GET', '/api/status', auth=False)
        self.assertEqual(status, 401)
        self.assertIn('access token', value['error'])
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        status, _, _ = self.request('GET', '/api/status', headers={'Authorization': 'Bearer wrong-token'}, auth=False)
        self.assertEqual(status, 401)
        status, value, headers = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertEqual(value['model'], MODEL)
        self.assertEqual(headers['Cache-Control'], 'no-store')


PROOF = ('Let x be an arbitrary real number. Equality is reflexive, so $x=x$. '
         'Since x was arbitrary, the statement holds for every real number.')
APPROVAL = {'schema_version': 3, 'explanation': 'Reflexivity covers every real x.',
            'issues': [], 'task_status': 'proved',
            'critical_dependencies': [{'claim': 'Every real x equals itself.', 'provenance': 'standard',
                                       'check': 'Reflexivity of equality applies to each real x.'}],
            'notes': []}


class FakeDeepInfra:
    """Authenticated HTTP/SSE API used by the real GUI transport and runners."""

    key = 'synthetic-deepinfra-test-key'

    def __init__(self):
        self.requests, self.replies = [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def authenticated(self):
                fake.requests.append((self.path, self.headers.get('Authorization')))
                if self.headers.get('Authorization') != 'Bearer ' + fake.key:
                    self.json_response(401, {'error': 'Rejected ' + fake.key})
                    return False
                return True

            def json_response(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append((self.path, self.headers.get('Authorization')))
                # DeepInfra's model catalog is public. Scoped inference JWTs
                # must not be sent to it: that authentication is not supported.
                self.json_response(200 if self.path == '/v1/openai/models' else 404,
                                   {'data': [{'id': DEEPINFRA_MODEL}, {'id': DEEPINFRA_LEGACY_MODEL}]})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if not self.authenticated():
                    return
                if self.path == '/v1/tokenize':
                    self.json_response(200, {'count': 200, 'max_model_len': 1048576})
                    return
                if self.path != '/v1/openai/chat/completions':
                    self.json_response(404, {'error': 'Unknown path'})
                    return
                fake.completions.append(data)
                reply = fake.replies.pop(0)
                if isinstance(reply, dict):
                    events = reply['events']
                else:
                    content, reasoning, count = reply
                    events = [
                        {'choices': [{'index': 0, 'delta': {'reasoning_content': reasoning}, 'finish_reason': None}]},
                        {'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]},
                        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
                        {'choices': [], 'usage': {'prompt_tokens': 200, 'completion_tokens': count}},
                    ]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for event in events:
                    self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()

        self.completions = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DeepInfraWorkflowTests(GuiCase):
    fake_class = FakeDeepInfra
    model = DEEPINFRA_MODEL
    extra_args = ('--backend', 'openai', '--api-profile', 'deepinfra',
                  '--gui-model-concurrency', '1', '--ctx', '65536')

    def setUp(self):
        environment = patch.dict(os.environ, {
            'DEEPINFRA_ALLOW_LOOPBACK': '1', 'DEEPINFRA_API_KEY': FakeDeepInfra.key,
            'DEEPINFRA_API_KEY_FILE': '', 'SH_DEEPINFRA_REASONING_EFFORT': 'high'})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('SH_DEEPINFRA_SERVICE_TIER', None)
        os.environ.pop('SQUARE_OPENAI_THINKING_FRACTION', None)
        os.environ.pop('SQUARE_OPENAI_STRUCTURED_THINKING_FRACTION', None)
        super().setUp()

    def test_api_key_gui_proof_preserves_reasoning_usage_and_private_credentials(self):
        self.fake.replies = [(PROOF, 'Solver reasoning.', 37),
                             (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
        status = self.ok('GET', '/api/status')
        started = self.ok('POST', '/api/proofs', {
            'goal': 'Prove that x = x for every real x.', 'rounds': 1,
            'tokens': 12000, 'seconds': 120})
        task = self.wait_task(timeout=20)
        self.assertEqual(task['state'], 'done', task)
        detail = self.ok('GET', f'/api/proofs/{started["id"]}')
        self.assertEqual(detail['status'], 'candidate_complete')
        self.assertEqual(detail['answer'], PROOF)
        self.assertEqual(detail['budget']['tokens']['used'], 60)
        self.assertEqual([call['temperature'] for call in self.fake.completions], [1.0, 1.0])
        self.assertTrue(all(auth is None if path == '/v1/openai/models' else auth == 'Bearer ' + self.fake.key
                            for path, auth in self.fake.requests))
        stored = ProofStore.load(self.root, started['id'])
        streams = [self.ok('GET', f'/api/proofs/{started["id"]}/stream/{call["stream"]}?from=0')
                   for call in stored.state['calls']]
        self.assertEqual([stream['thinking'] for stream in streams],
                         ['Solver reasoning.', 'Reviewer reasoning.'])
        self.assertEqual([stream['stats']['eval_count'] for stream in streams], [37, 23])
        self.assertNotIn(self.fake.key, json.dumps([status, detail, streams]))
