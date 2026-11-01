"""Visual interface server: security boundaries and workflows against a simulated Ollama."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from mathagent import cli
from mathagent.agent import Ollama
from mathagent.backends import LlamaCpp, OpenAICompatible
from mathagent.gui import server, store
from mathagent.gui.hub import Hub, Task
from mathagent.gui.watch import Watcher
from mathagent.ledger import ProofStore

MODEL = 'test-model:latest'


def text(content, eval_count=20, chunks=1, prompt_eval_count=200):
    size = max(1, len(content) // chunks)
    parts = [content[i:i + size] for i in range(0, len(content), size)] or ['']
    events = [{'message': {'content': part}, 'done': False} for part in parts[:-1]]
    return events + [{'message': {'content': parts[-1]}, 'done': True, 'done_reason': 'stop',
                      'eval_count': eval_count, 'prompt_eval_count': prompt_eval_count}]


def tool(name, arguments):
    return [{'message': {'content': '', 'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]},
             'done': True, 'done_reason': 'stop', 'eval_count': 8, 'prompt_eval_count': 200}]


class FakeOllama:
    """Scripted /api/chat replies; a (events, delay) tuple streams slowly."""

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
                reply = fake.replies.pop(0) if fake.replies else text('Unexpected extra call.')
                events, delay = reply if isinstance(reply, tuple) else (reply, 0)
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                for event in events:
                    if delay:
                        time.sleep(delay)
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

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        args = cli.parser().parse_args(['--workspace', str(self.root), '--host', self.fake.host, '--model', MODEL,
                                        '--ctx', '16384', '--predict', '1024', '--proof-solve-tokens', '4096',
                                        '--proof-verify-tokens', '2048', '--gui-concurrency', '1', *self.extra_args])
        args.workspace = self.root
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

    def wait_for(self, predicate, timeout=10, message='condition'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail('timed out waiting for ' + message)


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
        self.assertTrue(value['online'])  # new work in the interface searches online unless unticked
        self.assertTrue(value['ollama']['reachable'])
        self.assertNotIn('pair_urls', value)
        self.assertEqual(headers['Cache-Control'], 'no-store')










PROOF = ('Let x be an arbitrary real number. Equality is reflexive, so $x=x$. '
         'Since x was arbitrary, the statement holds for every real number.')
APPROVAL = {'explanation': 'Reflexivity covers every real x.', 'issues': [], 'verdict': 'no_issue_found'}


class ProofWorkflowTests(GuiCase):

    def test_pause_saves_a_checkpoint_and_resume_keeps_the_budget(self):
        slow = [{'message': {'thinking': 'step '}, 'done': False}] * 200 + text('Unfinished.')
        self.fake.replies = [(slow, 0.02)]
        started = self.ok('POST', '/api/proofs', {'goal': 'Prove that 1 = 1.', 'rounds': 2, 'tokens': 16000, 'seconds': 120})
        proof_id = started['id']
        live = self.wait_for(lambda: self.ok('GET', f'/api/proofs/{proof_id}')['live'], message='a live solver call')
        self.wait_for(lambda: self.ok('GET', f'/api/proofs/{proof_id}/stream/{live["file"]}?from=0')['thinking'],
                      message='streamed thinking')
        status, value, _ = self.request('POST', '/api/proofs', {'goal': 'Another proof.'})
        self.assertEqual(status, 409)
        self.assertIn('active task slots', value['error'])
        self.assertEqual(self.ok('POST', '/api/task/pause')['task']['state'], 'pausing')
        task = self.wait_task()
        self.assertEqual(task['state'], 'paused')
        detail = self.ok('GET', f'/api/proofs/{proof_id}')
        self.assertEqual(detail['status'], 'paused')
        self.assertEqual(detail['calls'][-1]['status'], 'interrupted')
        # The interrupted call keeps its whole reservation, and its notes are kept as partial work.
        charged = detail['budget']['tokens']['used']
        self.assertEqual(charged, 4096)
        self.assertEqual([(c['id'], c['transport_complete']) for c in detail['candidates']], [('P1', False)])
        self.assertIsNone(detail['answer'])
        self.assertEqual(len(self.fake.requests), 1)

        # Resuming does not repeat the saved call: it continues from the saved notes, then reviews.
        self.fake.replies = [text(PROOF), text(json.dumps(APPROVAL))]
        resumed = self.ok('POST', f'/api/proofs/{proof_id}/resume')
        self.assertEqual(resumed['id'], proof_id)
        task = self.wait_task(timeout=30)
        self.assertEqual(task['state'], 'done', task)
        self.assertIn('SAVED WORKING NOTES', self.fake.requests[1]['messages'][0]['content'])
        after = self.ok('GET', f'/api/proofs/{proof_id}')
        self.assertEqual(after['status'], 'candidate_complete')
        self.assertGreaterEqual(after['budget']['tokens']['used'], charged)
        self.assertEqual(after['settings']['max_tokens'], 16000)
        continued = after['candidates'][1]
        self.assertEqual((continued['kind'], continued['parent'], continued['review_status']), ('continue', 'P1', 'no_issue_found'))
        self.assertEqual((after['selected_candidate'], after['answer']), ('P2', PROOF))














if __name__ == '__main__':
    unittest.main()
