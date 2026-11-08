"""Visual interface server: security boundaries and workflows against a simulated Ollama."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from mathagent import cli
from mathagent.agent import Ollama
from mathagent.backends import LlamaCpp, OpenAICompatible
from mathagent.deepinfra import LEGACY_MODEL as DEEPINFRA_LEGACY_MODEL, MODEL as DEEPINFRA_MODEL
from mathagent.gui import server, store
from mathagent.gui.hub import Hub, Task
from mathagent.gui.watch import Watcher
from mathagent.ledger import ProofStore
from mathagent.model_limits import PROFILE as DS4_PROFILE, apply_defaults

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


def deepinfra_tool(name, arguments):
    """The same native tool transaction, streamed through the hosted dialect."""
    return {'events': [
        {'choices': [{'index': 0, 'delta': {'tool_calls': [{
            'index': 0, 'id': 'prepared-proof-task', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}]}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
        {'choices': [], 'usage': {'prompt_tokens': 200, 'completion_tokens': 31}},
    ]}


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
    fake_class = FakeOllama
    model = MODEL

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fake = self.fake_class()
        self.addCleanup(self.fake.close)
        args = cli.parser().parse_args(['--workspace', str(self.root), '--host', self.fake.host, '--model', self.model,
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
        self.assertEqual(value['defaults']['budget_profile'], 'default')
        self.assertIsNone(value['defaults']['effort_presets'])
        self.assertTrue(value['online'])  # new work in the interface searches online unless unticked
        self.assertTrue(value['ollama']['reachable'])
        self.assertNotIn('pair_urls', value)
        self.assertEqual(headers['Cache-Control'], 'no-store')










PROOF = ('Let x be an arbitrary real number. Equality is reflexive, so $x=x$. '
         'Since x was arbitrary, the statement holds for every real number.')
APPROVAL = {'explanation': 'Reflexivity covers every real x.', 'issues': [], 'verdict': 'no_issue_found'}




class ProofWorkflowTests(GuiCase):

    def test_qwen_main_agent_prepares_the_proof_task_with_definitions_and_sources(self):
        definition = 'Throughout this conversation, E denotes the real numbers.'
        source = 'The variable z ranges over E. The assertion to establish is z=z.'
        (self.root / 'definitions.txt').write_text(source)
        query = 'Prove the assertion in definitions.txt using the notation we fixed.'
        prepared = ('You are one child worker of the mathematical assistant. '
                    'E denotes the real numbers, and z is an arbitrary element of E. '
                    'Prove z=z for every such z, using definitions.txt as the original source. '
                    'Return a complete proof or identify the precise unresolved obligation.')
        action = {'mode': 'prove', 'request': prepared, 'files': ['definitions.txt'],
                  'effort': 'medium', 'reason': 'Clarify the notation and delegate the exact proof objective.'}
        final = 'The Proof candidate and its independent review are saved.'
        # Both a selected @prove and a model-chosen Proof delegation use the
        # existing main-agent read/delegate tool loop, without another schema.
        for selected in (True, False):
            with self.subTest(selected=selected):
                offset = len(self.fake.requests)
                self.fake.replies = [tool('read_file', {'path': 'definitions.txt'}),
                                     tool('delegate', action), text(PROOF),
                                     text(json.dumps(APPROVAL)), text(final)]
                chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
                store.append_items(self.root, chat, [{'role': 'user', 'content': definition}])
                body = {'content': ('@prove ' if selected else '') + query, 'files': ['definitions.txt']}
                if selected:
                    body['mode_call'] = {'mode': 'prove', 'start': 0, 'effort': 'medium'}
                self.ok('POST', f'/api/chats/{chat}/route', body)
                task = self.wait_task(timeout=15)
                self.assertEqual(task['state'], 'done', task)
                state = store.load_chat(self.root, chat)['assistant_state']
                self.assertEqual(state['status'], 'complete')
                child = state['children'][0]
                self.assertEqual(child['action'], action)
                self.assertEqual(child['context']['query'], query)
                proof = self.ok('GET', f'/api/proofs/{child["job_id"]}')
                self.assertEqual((proof['goal'], proof['status']), (prepared, 'candidate_complete'))
                self.assertEqual(proof['settings']['reference_context']['query'], query)
                sent = self.fake.requests[offset:]
                self.assertEqual(len(sent), 5)
                if selected:
                    self.assertEqual([request['options']['num_predict'] for request in sent[:2]], [2048, 2048])
                    self.assertEqual([item['function']['name'] for item in sent[1]['tools']], ['delegate'])
                self.assertIn(definition, json.dumps(sent[0]['messages']))
                self.assertIn('definitions.txt', json.dumps(sent[0]['messages']))
                # The main agent reads the attachment before preparing its
                # delegation. Both Proof roles retain that exact source and
                # the original conversation alongside the prepared objective.
                self.assertIn(source, json.dumps(sent[1]['messages']))
                for request in sent[2:4]:
                    content = request['messages'][0]['content']
                    self.assertIn(prepared, content)
                    self.assertIn(definition, content)
                    self.assertIn(source, content)
                    self.assertIn(query, content)
                self.assertFalse(self.fake.replies)

        # Pausing while the main agent prepares the task must not create a
        # premature Proof job or lose the original query. Resume uses the
        # existing pending model-call machinery and then delegates normally.
        jobs_before = self.ok('GET', '/api/proofs')['jobs']
        slow = [{'message': {'content': 'Preparing the mathematical objective. '}, 'done': False}] * 200
        self.fake.replies = [(slow + text('unfinished'), .02)]
        paused_chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.ok('POST', f'/api/chats/{paused_chat}/route', {
            'content': '@prove ' + query, 'files': ['definitions.txt'],
            'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
        self.wait_for(lambda: self.ok('GET', '/api/task').get('live', {}).get('steps'),
                      message='the main agent preparing its delegated objective')
        self.ok('POST', '/api/task/pause')
        self.assertEqual(self.wait_task(timeout=3)['state'], 'paused')
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0, timeout=3)
        paused = store.load_chat(self.root, paused_chat)['assistant_state']
        self.assertEqual(paused['query'], query)
        self.assertEqual(paused['children'], [])
        self.assertEqual(paused['proof_preparation']['calls'], 1)
        self.assertEqual(self.ok('GET', '/api/proofs')['jobs'], jobs_before)
        self.assertGreaterEqual(paused['budget']['tokens'], 2048)
        self.fake.replies = [tool('delegate', action), text(PROOF), text(json.dumps(APPROVAL)), text(final)]
        self.ok('POST', f'/api/chats/{paused_chat}/assistant/resume')
        task = self.wait_task(timeout=15)
        self.assertEqual(task['state'], 'done', task)
        resumed = store.load_chat(self.root, paused_chat)['assistant_state']
        self.assertEqual(len(resumed['children']), 1)
        self.assertEqual(resumed['proof_preparation']['calls'], 2)
        self.assertEqual(resumed['children'][0]['action'], action)
        self.assertGreaterEqual(resumed['budget']['tokens'], paused['budget']['tokens'])

        # A failed preparation cannot delay Proof, execute an unadvertised
        # tool, or create duplicate workers. The bounded fallback delegates
        # the exact original material, without adopting the failed draft.
        duplicate = tool('delegate', action)
        duplicate[0]['message']['tool_calls'] *= 2
        invalid_replies = {
            'direct answer': text('The assertion follows immediately, so I will answer directly.'),
            'unavailable tool': tool('execute_python', {'code': 'print("must not execute")'}),
            'duplicate workers': duplicate,
        }
        for failure, reply in invalid_replies.items():
            with self.subTest(preparation_failure=failure):
                before_jobs = self.ok('GET', '/api/proofs')['jobs']
                offset = len(self.fake.requests)
                self.fake.replies = [reply, text(PROOF), text(json.dumps(APPROVAL)), text(final)]
                invalid = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
                store.append_items(self.root, invalid, [{'role': 'user', 'content': definition}])
                self.ok('POST', f'/api/chats/{invalid}/route', {
                    'content': '@prove ' + query, 'files': ['definitions.txt'],
                    'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
                self.assertEqual(self.wait_task(timeout=15)['state'], 'done')
                invalid_state = store.load_chat(self.root, invalid)['assistant_state']
                self.assertEqual(invalid_state['status'], 'complete')
                self.assertEqual(len(invalid_state['children']), 1)
                fallback = invalid_state['children'][0]
                self.assertEqual(fallback['action']['mode'], 'prove')
                self.assertEqual(fallback['context']['query'], query)
                self.assertIn(query, fallback['action']['request'])
                self.assertEqual(len(self.ok('GET', '/api/proofs')['jobs']), len(before_jobs) + 1)
                self.assertEqual(len(self.fake.requests) - offset, 4)
                self.assertEqual(self.fake.requests[offset]['options']['num_predict'], 2048)
                proof = self.ok('GET', f'/api/proofs/{fallback["job_id"]}')
                self.assertEqual(proof['status'], 'candidate_complete')
                self.assertEqual(proof['settings']['reference_context']['query'], query)
                for request in self.fake.requests[offset + 1:offset + 3]:
                    context = request['messages'][0]['content']
                    self.assertIn(query, context)
                    self.assertIn(definition, context)
                    self.assertIn(source, context)
                    self.assertNotIn('must not execute', context)
                transcript = self.ok('GET', f'/api/chats/{invalid}')['transcript']
                committed = [item['content'] for item in transcript
                             if item['role'] == 'assistant' and not item.get('discarded')]
                self.assertEqual(committed[-1], final)
                self.assertFalse(self.fake.replies)

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


class FakeDeepInfra:
    """Authenticated HTTP/SSE API used by the real GUI transport and runners."""

    key = 'synthetic-deepinfra-test-key'

    def __init__(self):
        self.requests, self.replies = [], []
        self.reject_auth = False
        self.token_count = 200
        self.silent_started = threading.Event()
        self.release_silent = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def authenticated(self):
                fake.requests.append((self.path, self.headers.get('Authorization')))
                if (fake.reject_auth and self.path == '/v1/openai/chat/completions'
                        or self.headers.get('Authorization') != 'Bearer ' + fake.key):
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
                    self.json_response(200, {'count': fake.token_count, 'max_model_len': 1048576})
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
                if isinstance(reply, dict) and reply.get('silent'):
                    # Keep the TCP response open with an unfinished line, so
                    # pausing cannot depend on the next SSE event arriving.
                    self.wfile.write(b'data: {"choices":')
                    self.wfile.flush()
                    fake.silent_started.set()
                    fake.release_silent.wait(15)
                    return
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()

        self.completions = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.release_silent.set()
        self.server.shutdown()
        self.server.server_close()


class DeepInfraWorkflowTests(GuiCase):
    fake_class = FakeDeepInfra
    model = DEEPINFRA_MODEL
    extra_args = ('--backend', 'openai', '--api-profile', 'deepinfra', '--offline',
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

    def test_proof_preparation_is_short_and_hands_off_after_a_capped_or_unproductive_call(self):
        definition = 'In this discussion, E denotes the real numbers.'
        source = 'Let z be arbitrary in E. The requested statement is z=z.'
        (self.root / 'definitions.txt').write_text(source)
        query = 'Prove the statement in definitions.txt using the earlier definition of E.'
        final = 'The Proof candidate and its review are saved.'
        marker = 'DISCARDED_PREPARATION_PROOF_ATTEMPT'
        # Simulate the reported runaway preparation. Even a provider response
        # with a huge partial body must not trigger a larger main-agent retry
        # or become a premise of the real proof.
        capped = {'events': [
            {'choices': [{'index': 0, 'delta': {'content': (marker + ' ') * 3000}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]},
            {'choices': [], 'usage': {'prompt_tokens': 200, 'completion_tokens': 2048}},
        ]}
        cases = {
            'capped draft': [capped],
            # Tool arguments may stream without text events or final usage.
            # Their byte growth is bounded too; a provider cannot hide the
            # runaway draft in a never-ending delegate argument fragment.
            'oversized tool arguments': [{'events': [
                {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                    'index': 0, 'id': 'oversized-proof-preparation', 'type': 'function',
                    'function': {'name': 'delegate', 'arguments': (marker + ' ') * 3000},
                }]}, 'finish_reason': None}]},
            ]}],
            # One context read is useful. A second read instead of the required
            # handoff cannot open an unbounded preparation/source-reading loop.
            'repeated source reads': [deepinfra_tool('read_file', {'path': 'definitions.txt'}),
                                      deepinfra_tool('read_file', {'path': 'definitions.txt'})],
        }
        for label, preparation in cases.items():
            with self.subTest(preparation=label):
                offset = len(self.fake.completions)
                self.fake.replies = preparation + [(PROOF, 'Solver reasoning.', 37),
                                                   (json.dumps(APPROVAL), 'Reviewer reasoning.', 23),
                                                   (final, '', 19)]
                chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
                store.append_items(self.root, chat, [{'role': 'user', 'content': definition}])
                self.ok('POST', f'/api/chats/{chat}/route', {
                    'content': '@prove ' + query, 'files': ['definitions.txt'],
                    'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
                task = self.wait_task(timeout=15)
                self.assertEqual(task['state'], 'done', task)
                state = store.load_chat(self.root, chat)['assistant_state']
                self.assertEqual(state['status'], 'complete')
                self.assertEqual(len(state['children']), 1)
                child = state['children'][0]
                self.assertEqual(child['action']['mode'], 'prove')
                self.assertEqual(child['context']['query'], query)
                self.assertIn(query, child['action']['request'])
                proof = self.ok('GET', f'/api/proofs/{child["job_id"]}')
                self.assertEqual(proof['status'], 'candidate_complete')
                self.assertEqual((proof['settings']['max_predict'], proof['settings']['verify_tokens']),
                                 (4096, 2048))
                sent = self.fake.completions[offset:]
                count = len(preparation)
                self.assertEqual(len(sent), count + 3)
                self.assertEqual([call['max_tokens'] for call in sent[:count]], [2048] * count)
                self.assertEqual([call['reasoning_effort'] for call in sent[:count]], ['none'] * count)
                self.assertEqual(sent[0]['tool_choice'], 'required')
                if count == 2:
                    self.assertEqual(sent[1]['tool_choice'],
                                     {'type': 'function', 'function': {'name': 'delegate'}})
                    self.assertEqual([tool['function']['name'] for tool in sent[1]['tools']], ['delegate'])
                self.assertEqual([call['max_tokens'] for call in sent[count:count + 2]], [4096, 2048])
                for call in sent[count:count + 2]:
                    content = call['messages'][0]['content']
                    self.assertIn(query, content)
                    self.assertIn(definition, content)
                    self.assertIn(source, content)
                    self.assertNotIn(marker, content)
                self.assertFalse(self.fake.replies)

        # A large original context belongs to Proof. The preparation budget
        # must not clip it or spend a model call trying to summarize it first.
        self.fake.token_count = 17000
        large_definition = definition + '\n' + ('User-supplied context. ' * 1000)
        offset = len(self.fake.completions)
        self.fake.replies = [(PROOF, 'Solver reasoning.', 37),
                             (json.dumps(APPROVAL), 'Reviewer reasoning.', 23), (final, '', 19)]
        large = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        store.append_items(self.root, large, [{'role': 'user', 'content': large_definition}])
        self.ok('POST', f'/api/chats/{large}/route', {
            'content': '@prove ' + query, 'files': ['definitions.txt'],
            'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
        self.assertEqual(self.wait_task(timeout=15)['state'], 'done')
        state = store.load_chat(self.root, large)['assistant_state']
        self.assertEqual(state['proof_preparation']['calls'], 0)
        self.assertEqual(state['proof_preparation']['status'], 'fallback')
        self.assertEqual(len(state['children']), 1)
        self.assertTrue(any(item.get('role') == 'user' and item.get('content') == large_definition
                            for item in state['children'][0]['context']['history']),
                        'the complete original user context is retained')
        self.assertEqual(len(self.fake.completions) - offset, 3)
        self.assertEqual([call['max_tokens'] for call in self.fake.completions[offset:offset + 2]], [4096, 2048])
        for call in self.fake.completions[offset:offset + 2]:
            self.assertTrue(json.dumps(large_definition, ensure_ascii=False)[1:-1]
                            in call['messages'][0]['content'],
                            'the complete user context is present in the serialized reference context')
            self.assertIn(query, call['messages'][0]['content'])
        self.assertFalse(self.fake.replies)
        self.fake.token_count = 200

        # A paused pre-fix run may already have exhausted huge main-agent
        # allowances. Resume must hand off immediately, rather than granting
        # another preparation allowance or forwarding that abandoned prose.
        self.addCleanup(self.fake.release_silent.set)
        self.fake.silent_started.clear()
        self.fake.replies = [{'silent': True, 'events': [
            {'choices': [{'index': 0, 'delta': {'content': 'Preparing the task.'}, 'finish_reason': None}]},
        ]}]
        legacy = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        store.append_items(self.root, legacy, [{'role': 'user', 'content': definition}])
        self.ok('POST', f'/api/chats/{legacy}/route', {
            'content': '@prove ' + query, 'files': ['definitions.txt'],
            'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
        self.assertTrue(self.fake.silent_started.wait(5))
        self.ok('POST', '/api/task/pause')
        self.assertEqual(self.wait_task(timeout=3)['state'], 'paused')
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0, timeout=3)
        old = store.load_chat(self.root, legacy)['assistant_state']
        old.pop('proof_preparation', None)
        old['main_calls'] = 3
        old['last_call']['output_limit'] = 32768
        old['last_call']['status'] = 'interrupted'
        old['last_call']['final_recovery'] = True
        old['last_call']['tools_enabled'] = False
        old['last_call']['partial']['content'] = (marker + ' ') * 3000
        store.save_assistant(self.root, legacy, old)
        offset = len(self.fake.completions)
        self.fake.replies = [(PROOF, 'Solver reasoning.', 37),
                             (json.dumps(APPROVAL), 'Reviewer reasoning.', 23), (final, '', 19)]
        self.ok('POST', f'/api/chats/{legacy}/assistant/resume')
        self.assertEqual(self.wait_task(timeout=15)['state'], 'done')
        state = store.load_chat(self.root, legacy)['assistant_state']
        self.assertEqual(state['proof_preparation']['calls'], 2)
        self.assertEqual(state['proof_preparation']['status'], 'fallback')
        self.assertEqual(len(state['children']), 1)
        self.assertEqual(len(self.fake.completions) - offset, 3)
        for call in self.fake.completions[offset:offset + 2]:
            self.assertIn(query, call['messages'][0]['content'])
            self.assertIn(definition, call['messages'][0]['content'])
            self.assertNotIn(marker, call['messages'][0]['content'])
        self.assertFalse(self.fake.replies)

    def test_five_parallel_ds4_chats_hold_model_slots_and_queue_the_sixth(self):
        self.addCleanup(self.fake.release_silent.set)
        apply_defaults(self.hub.args, ['--gui'])
        limits = self.ok('POST', '/api/concurrency', {
            'chats': self.hub.args.gui_concurrency,
            'requests': self.hub.args.gui_model_concurrency})['concurrency']
        self.assertEqual((limits['chats'], limits['requests'],
                          self.hub.args.assistant_concurrency), (5, 5, 5))
        self.fake.replies = [{'silent': True, 'events': [
            {'choices': [{'index': 0, 'delta': {'content': f'Partial response {i}.'},
                          'finish_reason': None}]},
        ]} for i in range(6)]
        tasks = []
        for i in range(6):
            chat = self.ok('POST', '/api/chats', {'mode': 'critic', 'online': False})['id']
            tasks.append(self.ok('POST', f'/api/chats/{chat}/messages', {
                'content': f'Give a short response for independent request {i}.', 'queue': True})['task'])
        self.wait_for(lambda: len(self.fake.completions) == 5,
                      message='five simultaneous provider streams')
        snapshot = self.ok('GET', '/api/task')
        self.assertEqual(len(snapshot['tasks']), 5)
        self.assertEqual([task['id'] for task in snapshot['queue']], [tasks[5]['id']])
        self.assertEqual(snapshot['concurrency'], {'chats': 5, 'requests': 5, 'active': 5})

        # A cancelled stream must free its inference slot even though the
        # simulated provider still keeps its TCP response open. The sixth
        # chat takes that slot; no sixth live task is admitted concurrently.
        self.ok('POST', '/api/task/pause', {'task': tasks[0]['id']})
        self.wait_for(lambda: len(self.fake.completions) == 6,
                      timeout=3, message='the queued sixth stream to acquire the released slot')
        snapshot = self.ok('GET', '/api/task')
        self.assertEqual(len(snapshot['tasks']), 5)
        self.assertEqual(snapshot['queue'], [])
        self.assertNotIn(tasks[0]['id'], [entry['task']['id'] for entry in snapshot['tasks']])
        self.assertIn(tasks[5]['id'], [entry['task']['id'] for entry in snapshot['tasks']])
        self.assertFalse(self.fake.release_silent.is_set())
        for entry in snapshot['tasks']:
            self.ok('POST', '/api/task/pause', {'task': entry['task']['id']})
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0,
                      timeout=3, message='all five cancelled tasks to release their slots')

    def test_api_key_gui_proof_preserves_reasoning_usage_and_private_credentials(self):
        self.fake.replies = [(PROOF, 'Solver reasoning.', 37),
                             (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
        status = self.ok('GET', '/api/status')
        self.assertTrue(status['ollama']['reachable'])
        self.assertFalse(status['online'])
        self.assertTrue(status['online_locked'])
        self.assertEqual(status['defaults']['budget_profile'], DS4_PROFILE)
        self.assertEqual(status['defaults']['effort_presets']['medium']['solve_tokens'], 4096)
        self.assertEqual(status['defaults']['effort_presets']['medium']['verify_tokens'], 2048)
        started = self.ok('POST', '/api/proofs', {
            'goal': 'Prove that x = x for every real x.', 'rounds': 1,
            'tokens': 12000, 'seconds': 120})
        task = self.wait_task(timeout=20)
        self.assertEqual(task['state'], 'done', task)
        detail = self.ok('GET', f'/api/proofs/{started["id"]}')
        self.assertEqual(detail['status'], 'candidate_complete')
        self.assertEqual(detail['answer'], PROOF)
        self.assertEqual(detail['settings']['api_profile'], 'deepinfra')
        self.assertEqual(detail['settings']['model'], DEEPINFRA_MODEL)
        self.assertEqual(detail['settings']['reasoning_effort'], 'high')
        self.assertEqual(detail['budget']['tokens']['used'], 60)
        self.assertEqual(len(self.fake.completions), 2)
        for call in self.fake.completions:
            self.assertEqual(call['model'], DEEPINFRA_MODEL)
            self.assertEqual(call['reasoning_effort'], 'high')
            self.assertNotIn('service_tier', call)
            self.assertIn('max_tokens', call)
            self.assertNotIn('max_completion_tokens', call)
            self.assertNotIn('chat_template_kwargs', call)
        self.assertEqual(self.fake.completions[1]['response_format']['type'], 'json_schema')
        # Explicit small test ceilings survive the larger DS4 effort presets.
        self.assertEqual([call['max_tokens'] for call in self.fake.completions], [4096, 2048])
        self.assertTrue(all(auth is None if path == '/v1/openai/models' else auth == 'Bearer ' + self.fake.key
                            for path, auth in self.fake.requests))
        stored = ProofStore.load(self.root, started['id'])
        self.assertTrue(all('estimate' in call['context']['method'] for call in stored.state['calls']))
        streams = [self.ok('GET', f'/api/proofs/{started["id"]}/stream/{call["stream"]}?from=0')
                   for call in stored.state['calls']]
        self.assertEqual([stream['thinking'] for stream in streams],
                         ['Solver reasoning.', 'Reviewer reasoning.'])
        self.assertEqual([stream['stats']['eval_count'] for stream in streams], [37, 23])
        self.assertNotIn(self.fake.key, json.dumps([status, detail, streams]))

        # Actual provider streams use type:null in tool-call continuation
        # chunks. The full GUI turn must execute the tool and send its result
        # back with the matching ID before returning the written answer.
        (self.root / 'visible.tex').write_text('Every real x satisfies x=x.\n')
        final = 'The available source is visible.tex.'
        self.fake.replies = [{'events': [
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                'index': 0, 'id': 'list-files-call', 'type': 'function',
                'function': {'name': 'list_files', 'arguments': '{'}}]}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                'index': 0, 'id': None, 'type': None,
                'function': {'name': None, 'arguments': '}'}}]}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': {'prompt_tokens': 200, 'completion_tokens': 17}},
        ]}, (final, 'The requested file is listed.', 19)]
        chat = self.ok('POST', '/api/chats', {'mode': 'critic', 'online': False})['id']
        self.ok('POST', f'/api/chats/{chat}/messages', {'content': 'List the available source files.'})
        conversation_task = self.wait_task(timeout=20)
        self.assertEqual(conversation_task['state'], 'done', conversation_task)
        conversation = self.ok('GET', f'/api/chats/{chat}')
        self.assertEqual(next(item['content'] for item in reversed(conversation['transcript'])
                              if item['role'] == 'assistant'), final)
        followup = self.fake.completions[3]['messages']
        sent_call = next(message for message in followup if message.get('tool_calls'))['tool_calls'][0]
        self.assertEqual(sent_call['id'], 'list-files-call')
        self.assertEqual(sent_call['function'], {'name': 'list_files', 'arguments': '{}'})
        result = next(message for message in followup if message['role'] == 'tool')
        self.assertEqual(result['tool_call_id'], 'list-files-call')
        self.assertIn('visible.tex', result['content'])
        self.assertEqual(len(self.fake.completions), 4)
        self.assertTrue(all('service_tier' not in call for call in self.fake.completions))

        # A provider rejection is visible as an outage, without echoing its body
        # (even if the provider reflects credentials in that body).
        self.fake.reject_auth = True
        failed = self.ok('POST', '/api/proofs', {
            'goal': 'Prove the same statement after provider access expires.', 'rounds': 1,
            'tokens': 12000, 'seconds': 120})
        failure = self.wait_task(timeout=20)
        self.assertEqual(failure['result_status'], 'paused', failure)
        self.assertNotIn(self.fake.key, json.dumps(failure))
        failed_detail = self.ok('GET', f'/api/proofs/{failed["id"]}')
        self.assertEqual(failed_detail['status'], 'paused')
        self.assertIn('401', failed_detail['stop_reason'])
        self.assertIsNone(failed_detail['answer'])
        self.assertNotIn(self.fake.key, json.dumps(failed_detail))
        self.assertEqual(len(self.fake.completions), 4)
        for file in self.root.rglob('*'):
            if file.is_file():
                self.assertNotIn(self.fake.key.encode(), file.read_bytes(), str(file))

    def test_proof_effort_keeps_full_ds4_calls_for_solves_repairs_and_reviews(self):
        # Priority is transport-wide: the main-agent handoff, solver, repair
        # and reviews all use it without changing their saved allowances.
        os.environ['SH_DEEPINFRA_SERVICE_TIER'] = 'priority'
        # These are explicit launch ceilings. Exercise the real HTTP route,
        # shared Assistant budget, durable Proof runner and provider requests;
        # reading the preset table alone would miss a later hidden reduction.
        args = self.hub.args
        args.ctx = 1048576
        args.proof_solve_tokens = args.proof_verify_tokens = args.proof_repair_tokens = 384000
        args.proof_tokens = 11520000
        args.proof_seconds = 1000000
        args.assistant_tokens = 16000000
        args.assistant_input_tokens = 100000000
        args.assistant_seconds = 1000000
        args.request_timeout = 10800
        jobs_before = self.ok('GET', '/api/proofs')['jobs']
        requests_before = len(self.fake.requests)
        status, rejected, _ = self.request('POST', '/api/proofs', {
            'goal': 'Prove that x=x for every real x.', 'rounds': 1,
            'solve_tokens': 400000, 'verify_tokens': 384000,
            'ctx': 1048576, 'tokens': 11520000, 'seconds': 120})
        self.assertEqual(status, 400)
        self.assertIn('384000', rejected['error'])
        self.assertEqual(self.ok('GET', '/api/proofs')['jobs'], jobs_before)
        self.assertEqual(len(self.fake.requests), requests_before)
        self.assertEqual(self.fake.completions, [])
        expected = {'low': (1, 32768, 16384, 65536),
                    'medium': (3, 384000, 384000, 3456000),
                    'high': (5, 384000, 384000, 5760000),
                    'xhigh': (7, 384000, 384000, 8064000),
                    'poincare': (10, 384000, 384000, 11520000)}
        presets = self.ok('GET', '/api/status')['defaults']['effort_presets']
        objection = {'explanation': 'The candidate needs an explicit quantifier justification.',
                     'issues': [{'location': 'Conclusion', 'kind': 'missing_justification',
                                 'evidence': 'The universal quantifier was not justified.'}],
                     'verdict': 'issues_found'}
        for level, (rounds, solve, review, total) in expected.items():
            with self.subTest(effort=level):
                self.assertEqual((presets[level]['tries'], presets[level]['solve_tokens'],
                                  presets[level]['verify_tokens'], presets[level]['tokens']),
                                 (rounds, solve, review, total))
                offset = len(self.fake.completions)
                prepared = ('You are one child worker of the mathematical assistant. '
                            'Let x be an arbitrary real number. Prove x=x for every real x, '
                            'with a complete argument that retains the universal quantifier.')
                action = {'mode': 'prove', 'request': prepared, 'files': [], 'effort': level,
                          'reason': 'Prepare the exact mathematical task before Proof.'}
                if level == 'medium':
                    # A review-format retry stays on the same candidate. A
                    # mathematical objection then starts a repair round.
                    self.fake.replies = [deepinfra_tool('delegate', action),
                                         (PROOF, 'Initial reasoning.', 37),
                                         ('not valid review JSON', 'First review.', 23),
                                         (json.dumps(objection), 'Repeated review.', 29),
                                         (PROOF, 'Repair reasoning.', 41),
                                         (json.dumps(APPROVAL), 'Final review.', 31)]
                else:
                    self.fake.replies = [deepinfra_tool('delegate', action),
                                         (PROOF, 'Solver reasoning.', 37),
                                         (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
                self.fake.replies.append(('The Proof candidate and review are saved.', '', 19))
                chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
                self.ok('POST', f'/api/chats/{chat}/route', {
                    'content': '@prove Prove that x=x for every real x.', 'files': [],
                    'mode_call': {'mode': 'prove', 'start': 0, 'effort': level}})
                task = self.wait_task(timeout=20)
                self.assertEqual(task['state'], 'done', task)
                state = store.load_chat(self.root, chat)['assistant_state']
                self.assertEqual(state['status'], 'complete')
                self.assertEqual(len(state['children']), 1)
                child = state['children'][0]
                self.assertEqual(child['action']['request'], prepared)
                detail = self.ok('GET', f'/api/proofs/{child["job_id"]}')
                self.assertEqual(detail['status'], 'candidate_complete', detail['stop_reason'])
                self.assertEqual(detail['goal'], prepared)
                self.assertEqual(detail['settings']['reference_context']['query'],
                                 'Prove that x=x for every real x.')
                settings = detail['settings']
                self.assertEqual((settings['max_rounds'], settings['max_predict'],
                                  settings['verify_tokens'], settings['repair_tokens'], settings['max_tokens']),
                                 (rounds, solve, review, solve, total))
                sent = self.fake.completions[offset:]
                self.assertTrue(all(call.get('service_tier') == 'priority' for call in sent))
                self.assertEqual(sent[0]['reasoning_effort'], 'none')
                self.assertIn('delegate', [item['function']['name'] for item in sent[0]['tools']])
                worker_calls = [call for call in sent if call['reasoning_effort'] == 'high']
                self.assertEqual([call['max_tokens'] for call in worker_calls],
                                 [solve, review, review, solve, review] if level == 'medium' else [solve, review])
                proof = ProofStore.load(self.root, child['job_id']).state
                self.assertEqual(proof['rounds_started'], 2 if level == 'medium' else 1)
                if level == 'medium':
                    self.assertEqual([item['kind'] for item in proof['candidates']], ['initial', 'repair'])
                    self.assertIsNotNone(proof['reviews'][0]['protocol_error'])
                    self.assertEqual(len(proof['reviews']), 3)
                if level != 'low':
                    # A full call can take more than the old 30-minute Medium
                    # deadline; effort must not reinstate that hidden limit.
                    self.assertGreater(settings['max_seconds'], 1800)
                self.assertFalse(self.fake.replies, 'The expected calls were not all dispatched')

        # A hard manual cap must refuse a full DS4 proof rather than silently
        # changing the solver/reviewer to smaller calls. Main-agent reporting,
        # if needed, remains a separate non-thinking call.
        args.assistant_tokens = 500000
        offset = len(self.fake.completions)
        self.fake.replies = [deepinfra_tool('delegate', {
            'mode': 'prove', 'request': 'Prove the same statement.', 'files': [], 'effort': 'medium',
            'reason': 'Run the requested Proof workflow.'}),
            ('The remaining budget cannot fund a full Proof solve and review.', '', 19)]
        limited = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.ok('POST', f'/api/chats/{limited}/route', {
            'content': '@prove Prove the same statement.', 'files': [],
            'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
        self.wait_task(timeout=20)
        limited_state = store.load_chat(self.root, limited)['assistant_state']
        self.assertFalse(any(call['reasoning_effort'] == 'high'
                             for call in self.fake.completions[offset:]))
        for child in limited_state['children']:
            if child.get('job_id') and not child['job_id'].startswith('child_'):
                self.assertEqual(ProofStore.load(self.root, child['job_id']).state['calls'], [])
        limited_view = self.ok('GET', f'/api/chats/{limited}')
        self.assertRegex(json.dumps(limited_view).lower(), r'budget|allowance')

        # A physically smaller context must also refuse the full allowance,
        # rather than quietly fitting a smaller solver response into it.
        args.assistant_tokens = 16000000
        args.ctx = 65536
        offset = len(self.fake.completions)
        self.fake.replies = [deepinfra_tool('delegate', {
            'mode': 'prove', 'request': 'Prove the same statement in this context.', 'files': [], 'effort': 'medium',
            'reason': 'Run the requested Proof workflow.'}),
                             ('The current context cannot accommodate the full Proof output allowance.', '', 19)]
        narrow = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.ok('POST', f'/api/chats/{narrow}/route', {
            'content': '@prove Prove the same statement in this context.', 'files': [],
            'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
        self.wait_task(timeout=20)
        narrow_state = store.load_chat(self.root, narrow)['assistant_state']
        self.assertFalse(any(call['reasoning_effort'] == 'high'
                             for call in self.fake.completions[offset:]))
        proof_id = narrow_state['children'][0]['job_id']
        detail = self.ok('GET', f'/api/proofs/{proof_id}')
        self.assertEqual(detail['status'], 'needs_context')
        self.assertEqual(detail['calls'], [])
        self.assertTrue(detail['settings']['full_output_ceiling'])
        self.assertEqual(detail['settings']['max_predict'], 384000)

    def test_pause_interrupts_a_silent_provider_and_releases_the_model_slot(self):
        self.addCleanup(self.fake.release_silent.set)
        partial = 'Let x be an arbitrary real number. '
        self.fake.replies = [{'silent': True, 'events': [
            {'choices': [{'index': 0, 'delta': {'content': partial}, 'finish_reason': None}]},
        ]}, (PROOF, 'A fresh complete solve.', 37),
            (json.dumps(APPROVAL), 'A fresh complete review.', 23)]
        started = self.ok('POST', '/api/proofs', {
            'goal': 'Prove that x = x for every real x.', 'rounds': 2,
            'tokens': 12000, 'seconds': 120})
        self.assertTrue(self.fake.silent_started.wait(5))
        live = self.wait_for(lambda: self.ok('GET', f'/api/proofs/{started["id"]}')['live'])
        self.wait_for(lambda: self.ok('GET', f'/api/proofs/{started["id"]}/stream/{live["file"]}?from=0')['text'],
                      message='the saved partial response')

        pause_started = time.monotonic()
        self.ok('POST', '/api/task/pause')
        task = self.wait_task(timeout=2)
        self.assertEqual(task['state'], 'paused', task)
        self.assertLess(time.monotonic() - pause_started, 2)
        detail = self.ok('GET', f'/api/proofs/{started["id"]}')
        self.assertEqual(detail['status'], 'paused')
        self.assertEqual(detail['calls'][0]['status'], 'interrupted')
        self.assertEqual(detail['budget']['tokens']['used'], 4096)
        self.assertFalse(detail['candidates'][0]['transport_complete'])
        stream = self.ok('GET', f'/api/proofs/{started["id"]}/stream/{live["file"]}?from=0')
        self.assertEqual(stream['text'], partial)
        self.assertFalse(stream['done'])

        # The old fake remains silent throughout the next proof. A completed
        # fresh run proves that cancellation released the shared single slot.
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0,
                      timeout=2, message='the cancelled task to release its slot')
        fresh = self.ok('POST', '/api/proofs', {
            'goal': 'Prove the same statement in a new run.', 'rounds': 1,
            'tokens': 12000, 'seconds': 120})
        self.assertEqual(self.wait_task(timeout=5)['state'], 'done')
        completed = self.ok('GET', f'/api/proofs/{fresh["id"]}')
        self.assertEqual((completed['status'], completed['answer']), ('candidate_complete', PROOF))
        self.assertFalse(self.fake.release_silent.is_set())
        self.assertEqual(len(self.fake.completions), 3)

        # Tool-only deltas do not yield native text/thinking events. Pause
        # still interrupts them before a finished call can execute a tool.
        self.fake.silent_started.clear()
        self.fake.replies = [{'silent': True, 'events': [
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                'index': 0, 'id': 'unfinished-read', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{'}}]}, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{
                'index': 0, 'type': None, 'function': {'arguments': '"path":'}}]}, 'finish_reason': None}]},
        ]}, ('The next request completed.', '', 11)]
        chat = self.ok('POST', '/api/chats', {'mode': 'critic', 'online': False})['id']
        self.ok('POST', f'/api/chats/{chat}/messages', {'content': 'Inspect the source files.'})
        self.assertTrue(self.fake.silent_started.wait(5))
        pause_started = time.monotonic()
        self.ok('POST', '/api/task/pause')
        self.assertEqual(self.wait_task(timeout=2)['state'], 'paused')
        self.assertLess(time.monotonic() - pause_started, 2)
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0,
                      timeout=2, message='the tool-only stream to release its slot')
        next_chat = self.ok('POST', '/api/chats', {'mode': 'critic', 'online': False})['id']
        self.ok('POST', f'/api/chats/{next_chat}/messages', {'content': 'Give a short answer.'})
        self.assertEqual(self.wait_task(timeout=5)['state'], 'done')
        next_detail = self.ok('GET', f'/api/chats/{next_chat}')
        self.assertEqual(next(item['content'] for item in reversed(next_detail['transcript'])
                              if item['role'] == 'assistant'), 'The next request completed.')
        self.assertFalse(self.fake.release_silent.is_set())
        self.assertEqual(len(self.fake.completions), 5)

        # Simulate a paused 0731 pre-policy job. A new launch's defaults must
        # not change its model, upgrade its caps or add the full-ceiling rule.
        old = ProofStore.load(self.root, started['id'])
        old.state['settings']['model'] = DEEPINFRA_LEGACY_MODEL
        old.state['settings'].pop('full_output_ceiling', None)
        old.save()
        self.hub.args.ctx = 1048576
        self.hub.args.proof_solve_tokens = self.hub.args.proof_verify_tokens = 384000
        self.hub.args.proof_repair_tokens = 384000
        self.hub.args.proof_tokens = 11520000
        self.hub.args.proof_seconds = 324000
        self.fake.replies = [(PROOF, 'Continuation under the saved allowance.', 37),
                             (json.dumps(APPROVAL), 'Review under the saved allowance.', 23)]
        self.ok('POST', f'/api/proofs/{started["id"]}/resume')
        self.assertEqual(self.wait_task(timeout=5)['state'], 'done')
        resumed = self.ok('GET', f'/api/proofs/{started["id"]}')
        self.assertEqual(resumed['status'], 'candidate_complete')
        self.assertEqual((resumed['settings']['max_predict'], resumed['settings']['verify_tokens'],
                          resumed['settings']['max_tokens'], resumed['settings']['max_seconds']),
                         (4096, 2048, 12000, 120))
        self.assertNotIn('full_output_ceiling', resumed['settings'])
        self.assertEqual(resumed['settings']['model'], DEEPINFRA_LEGACY_MODEL)
        self.assertEqual([call['max_tokens'] for call in self.fake.completions[5:]], [4096, 2048])
        self.assertEqual([call['model'] for call in self.fake.completions[5:]],
                         [DEEPINFRA_LEGACY_MODEL, DEEPINFRA_LEGACY_MODEL])


    def test_assistant_routes_nontrivial_proofs_before_answering_and_keeps_elementary_answers_direct(self):
        self.addCleanup(self.fake.release_silent.set)
        self.hub.args.assistant_concurrency = 5
        query = ('Prove that every bounded sequence in a separable Hilbert space '
                 'has a weakly convergent subsequence.')
        decision = {'route': 'prove', 'effort': 'medium',
                    'reason': 'This requires a substantive compactness argument.'}
        candidate = ('Choose an orthonormal basis and use a diagonal subsequence for coordinate convergence. '
                     'The uniform norm bound extends the coordinate limit to a bounded linear functional. '
                     'Riesz representation gives its representing vector, and approximation by finite basis '
                     'sums proves weak convergence to that vector.')
        approved = {'explanation': 'The coordinate and density argument covers the stated hypotheses.',
                    'issues': [], 'verdict': 'no_issue_found'}
        final = 'The saved Proof candidate and its review are available in the Proof card.'
        prepared = ('You are one child worker of the mathematical assistant. '
                    'A bounded sequence here has uniformly bounded Hilbert-space norm. '
                    'Weak convergence means convergence of every continuous linear functional. '
                    'Keep the separability hypothesis and prove the exact objective: ' + query)
        action = {'mode': 'prove', 'request': prepared, 'files': [], 'effort': 'medium',
                  'reason': 'Supply definitions and the precise compactness objective to Proof.'}
        self.fake.replies = [(json.dumps(decision), '', 13),
                             deepinfra_tool('delegate', action),
                             (candidate, 'Solver reasoning.', 37),
                             (json.dumps(approved), 'Reviewer reasoning.', 23),
                             {'silent': True, 'events': [
                                 {'choices': [{'index': 0, 'delta': {
                                     'reasoning_content': 'Inspecting the completed worker result.'},
                                     'finish_reason': None}]},
                             ]}]
        chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.ok('POST', f'/api/chats/{chat}/route', {'content': query, 'files': []})
        self.assertTrue(self.fake.silent_started.wait(10))
        before_pause = store.load_chat(self.root, chat)['assistant_state']
        self.assertEqual(before_pause['limits']['concurrency'], 5)
        self.assertEqual(before_pause['proof_routing']['route'], 'prove')
        self.assertEqual(before_pause['proof_routing']['stats']['eval_count'], 13)
        self.assertEqual(before_pause['query'], query)
        self.assertEqual(len(before_pause['children']), 1)
        child = before_pause['children'][0]
        self.assertEqual(child['status'], 'complete')
        self.assertEqual(child['action']['mode'], 'prove')
        self.assertEqual(child['context']['query'], query)
        self.assertEqual(child['action']['request'], prepared)
        proof_id = child['job_id']
        proof = self.ok('GET', f'/api/proofs/{proof_id}')
        self.assertEqual((proof['status'], proof['answer']), ('candidate_complete', candidate))
        self.assertIn(query, proof['goal'])
        self.assertEqual(proof['settings']['reference_context']['query'], query)
        self.assertEqual([call['reasoning_effort'] for call in self.fake.completions[:4]],
                         ['none', 'none', 'high', 'high'])
        self.assertEqual(self.fake.completions[0]['max_tokens'], 1024)
        self.assertIn('route', self.fake.completions[0]['response_format']['json_schema']['schema']['properties'])
        routing_input = json.loads(self.fake.completions[0]['messages'][-1]['content'])
        self.assertEqual(routing_input['current_user_request'], query)
        self.assertIn('delegate', [item['function']['name'] for item in self.fake.completions[1]['tools']])
        self.assertEqual(self.fake.completions[1]['max_tokens'], 2048)
        self.assertEqual(self.fake.completions[2]['max_tokens'], 4096)
        self.assertIn(query, json.dumps(self.fake.completions[2]['messages']))
        self.assertIn(prepared, self.fake.completions[2]['messages'][0]['content'])

        # A pause after the worker completed keeps both its identity and the
        # recorded routing decision; resuming only needs the final synthesis.
        self.ok('POST', '/api/task/pause')
        self.assertEqual(self.wait_task(timeout=2)['state'], 'paused')
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0, timeout=2)
        self.fake.replies = [(final, '', 19)]
        self.ok('POST', f'/api/chats/{chat}/assistant/resume')
        resumed = self.wait_task(timeout=10)
        self.assertEqual(resumed['state'], 'done', resumed)
        after_resume = store.load_chat(self.root, chat)['assistant_state']
        self.assertEqual(after_resume['status'], 'complete')
        self.assertEqual(after_resume['limits']['concurrency'], 5)
        self.assertEqual(after_resume['proof_routing'], before_pause['proof_routing'])
        self.assertEqual([item['job_id'] for item in after_resume['children']], [proof_id])
        self.assertEqual(after_resume['children'][0]['action'], child['action'])
        cards = [item for item in self.ok('GET', f'/api/chats/{chat}')['transcript'] if item['role'] == 'route']
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['job_id'], proof_id)
        classifiers = [call for call in self.fake.completions if
                       'route' in call.get('response_format', {}).get('json_schema', {}).get('schema', {}).get('properties', {})]
        self.assertEqual(len(classifiers), 1)
        self.assertEqual(len(self.fake.completions), 6)  # routing, main delegation, solve, review, paused/resumed final

        # A one-line algebraic identity remains a direct answer in Assistant.
        direct_start = len(self.fake.completions)
        self.fake.replies = [(json.dumps({'route': 'direct', 'effort': 'low',
                                         'reason': 'One immediate distributive identity.'}), '', 11),
                             ('By distributivity, (a+b)^2=a^2+2ab+b^2.', '', 17)]
        elementary = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.ok('POST', f'/api/chats/{elementary}/route', {'content': 'Expand (a+b)^2.', 'files': []})
        completed = self.wait_task(timeout=10)
        self.assertEqual(completed['state'], 'done', completed)
        direct_state = store.load_chat(self.root, elementary)['assistant_state']
        self.assertEqual(direct_state['status'], 'complete')
        self.assertEqual(direct_state['proof_routing']['route'], 'direct')
        self.assertEqual(direct_state['children'], [])
        self.assertEqual(len(self.fake.completions) - direct_start, 2)
        direct_view = self.ok('GET', f'/api/chats/{elementary}')
        self.assertFalse(any(item['role'] == 'route' for item in direct_view['transcript']))
        self.assertIn('a^2+2ab+b^2', next(item['content'] for item in reversed(direct_view['transcript'])
                                        if item['role'] == 'assistant'))

        # An invalid decision must not authorize a long unreviewed proof.
        invalid_start = len(self.fake.completions)
        self.fake.replies = [(json.dumps({'route': 'unknown', 'effort': 'medium',
                                         'reason': 'Invalid synthetic decision.'}), '', 11)]
        invalid = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        self.ok('POST', f'/api/chats/{invalid}/route', {'content': query, 'files': []})
        self.wait_task(timeout=10)
        invalid_state = store.load_chat(self.root, invalid)['assistant_state']
        self.assertNotEqual(invalid_state['status'], 'complete')
        self.assertEqual(invalid_state['children'], [])
        self.assertEqual(len(self.fake.completions) - invalid_start, 1)
        invalid_view = self.ok('GET', f'/api/chats/{invalid}')
        self.assertFalse(any(item['role'] == 'assistant' and not item.get('discarded')
                             for item in invalid_view['transcript']))














if __name__ == '__main__':
    unittest.main()
