# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ENPC / SquareHarness

"""Visual interface server: security boundaries and workflows against a simulated Ollama."""
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
from mathagent.gui import server, store
from mathagent.gui.hub import Hub
from mathagent.gui.watch import Watcher
from mathagent.ledger import ProofStore
from mathagent.model_limits import PROFILE as DS4_PROFILE

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


class ProjectTests(GuiCase):

    def test_project_notebook_revisions_and_proof_resume_keep_the_submitted_context(self):
        original_notes = 'Definitions: E is the real line. Conjecture A is unproved. ORIGINAL_PROJECT_NOTES.'
        a = self.ok('POST', '/api/projects', {'name': 'Renewal project', 'notes': original_notes})['project']
        b = self.ok('POST', '/api/projects', {'name': 'Other project', 'notes': 'OTHER_PROJECT_NOTES'})['project']
        chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False, 'project_id': a['id']})['id']
        self.assertEqual(self.hub.root, self.root)
        self.assertTrue((self.root / a['folder'] / 'PROJECT.md').is_file())
        status, uploaded, _ = self.request('POST', f'/api/files/upload?name=lemma.txt&project_id={a["id"]}',
                                           raw=b'A synthetic project lemma.')
        self.assertEqual(status, 200, uploaded)
        self.assertEqual(uploaded['path'], a['folder'] + '/lemma.txt')
        self.assertEqual((self.root / uploaded['path']).read_text(), 'A synthetic project lemma.')
        self.assertFalse((self.root / 'lemma.txt').exists())
        status, uploaded, _ = self.request('POST', f'/api/files/upload?name=PROJECT.md&project_id={a["id"]}',
                                           raw=b'An uploaded note must not overwrite the notebook.')
        self.assertEqual(status, 200, uploaded)
        self.assertEqual(uploaded['path'], a['folder'] + '/PROJECT-2.md')
        self.assertEqual(self.ok('GET', f'/api/projects/{a["id"]}')['project']['notes'], original_notes)
        status, _, _ = self.request('POST', '/api/files/upload?name=escape.txt&project_id=..', raw=b'Unsafe target')
        self.assertEqual(status, 400)
        self.assertFalse((self.root.parent / 'escape.txt').exists())
        status, uploaded, _ = self.request('POST', '/api/files/upload?name=unassigned.txt', raw=b'No project')
        self.assertEqual(status, 200, uploaded)
        self.assertEqual(uploaded['path'], 'unassigned.txt')
        query = 'Prove that every element x of E satisfies x=x, with the project notation.'
        slow = [{'message': {'thinking': 'Preparing definitions. '}, 'done': False}] * 200
        self.fake.replies = [(slow + text('not finished'), .02)]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': '@prove ' + query,
            'mode_call': {'mode': 'prove', 'start': 0, 'effort': 'medium'}})
        self.wait_for(lambda: self.fake.requests, message='project preparation request')
        status, _, _ = self.request('POST', f'/api/chats/{chat}/project', {'project_id': b['id']})
        self.assertEqual(status, 409)
        self.ok('POST', '/api/task/pause')
        self.assertEqual(self.wait_task(timeout=3)['state'], 'paused')
        self.wait_for(lambda: self.ok('GET', '/api/status')['concurrency']['active'] == 0)
        changed = self.ok('POST', f'/api/projects/{a["id"]}', {
            'notes': 'CHANGED_PROJECT_NOTES', 'revision': a['revision']})['project']
        self.assertNotEqual(changed['revision'], a['revision'])
        status, _, _ = self.request('POST', f'/api/projects/{a["id"]}', {
            'notes': 'must not overwrite', 'revision': a['revision']})
        self.assertEqual(status, 409)
        self.assertEqual(self.ok('GET', f'/api/projects/{a["id"]}')['project']['notes'], 'CHANGED_PROJECT_NOTES')
        self.ok('POST', f'/api/chats/{chat}/project', {'project_id': b['id']})
        action = {'mode': 'prove', 'request': 'Let E be the real line. Prove x=x for every x in E.',
                  'files': [], 'effort': 'medium', 'reason': 'Give the exact proof task.'}
        offset = len(self.fake.requests)
        self.fake.replies = [tool('delegate', action), text(PROOF), text(json.dumps(APPROVAL)), text('The proof is saved.')]
        self.ok('POST', f'/api/chats/{chat}/assistant/resume')
        self.assertEqual(self.wait_task(timeout=15)['state'], 'done')
        saved = store.load_chat(self.root, chat)
        state = saved['assistant_state']
        self.assertEqual(saved['project_id'], b['id'])
        self.assertEqual(state['project_snapshot']['notes'], original_notes)
        self.assertEqual(state['children'][0]['context']['project_snapshot']['revision'], a['revision'])
        for sent in self.fake.requests[offset:offset + 3]:
            material = json.dumps(sent['messages'])
            self.assertIn('ORIGINAL_PROJECT_NOTES', material)
            self.assertNotIn('CHANGED_PROJECT_NOTES', material)
            self.assertNotIn('OTHER_PROJECT_NOTES', material)
        other = self.ok('POST', '/api/chats', {'mode': 'critic', 'online': False, 'project_id': b['id']})['id']
        self.fake.replies = [text('The requested notation is recorded.')]
        self.ok('POST', f'/api/chats/{other}/messages', {'content': 'Explain the project notation briefly.'})
        self.assertEqual(self.wait_task()['state'], 'done')
        material = json.dumps(self.fake.requests[-1]['messages'])
        self.assertIn('OTHER_PROJECT_NOTES', material)
        self.assertNotIn('ORIGINAL_PROJECT_NOTES', material)
        self.assertEqual(self.hub.root, self.root)
        legacy = self.ok('POST', '/api/chats', {'mode': 'free'})
        self.assertIsNone(legacy['project_id'])
        self.assertEqual(len(self.ok('GET', '/api/projects')['projects']), 2)


if __name__ == '__main__':
    unittest.main()
