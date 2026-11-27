# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Visual interface server: security boundaries and workflows against a simulated Ollama."""
import copy
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mathagent import cli, proof, tools
from mathagent.agent import Agent
from mathagent.deepinfra import LEGACY_MODEL as DEEPINFRA_LEGACY_MODEL, MODEL as DEEPINFRA_MODEL
from mathagent.gui import effort, server, store
from mathagent.gui.hub import Hub
from mathagent.gui.watch import Watcher
from mathagent.ledger import ProofStore
from mathagent.literature import LiteratureError, LiteratureTools
from mathagent.model_limits import MODEL_OUTPUT_TOKENS, PROFILE as DS4_PROFILE
from mathagent.proof_policy import LEGACY_VERIFIER_SCHEMA, VERIFIER_SCHEMA_V2, initial_request
from mathagent.tools import Workspace
from test_research import FakeClient, response

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


def deepinfra_tool(name, arguments, call_id='prepared-proof-task'):
    """The same native tool transaction, streamed through the hosted dialect."""
    return {'events': [
        {'choices': [{'index': 0, 'delta': {'tool_calls': [{
            'index': 0, 'id': call_id, 'type': 'function',
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

    def wait_for(self, predicate, timeout=10, message='condition'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail('timed out waiting for ' + message)


class SecurityTests(GuiCase):
    def test_retrieval_follows_the_current_request_including_for_old_chats(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'explore', 'online': True})
        original = store.load_chat(self.root, chat['id'])
        self.assertNotIn('online', original['settings'])
        original['settings']['online'] = False
        store._save_chat(self.root, original)
        for endpoint, options in (('/api/proofs', {'goal': 'Prove x=x.', 'online': False}),
                                  ('/api/proofs', {'goal': 'Prove x=x.', 'literature': False}),
                                  ('/api/research', {'kind': 'literature', 'goal': 'Survey equality.', 'online': False}),
                                  ('/api/chats', {'mode': 'free', 'online': False}),
                                  (f'/api/chats/{chat["id"]}/settings', {'online': False})):
            code, body, _ = self.request('POST', endpoint, options)
            self.assertEqual(code, 400)
            self.assertIn('Do not search the web', str(body))
        engines = []
        build = self.hub._engine
        def record(*args, **kwargs):
            agent, library = build(*args, **kwargs)
            engines.append(library)
            return agent, library
        with patch.object(self.hub, '_engine', side_effect=record):
            self.fake.replies = [text('A property of equality is reflexivity.')]
            self.ok('POST', f'/api/chats/{chat["id"]}/messages', {'content': 'Describe a property of equality.'})
            self.assertEqual(self.wait_task()['state'], 'done')
            self.assertTrue(engines[-1].online)
            for goal, expected in (('Prove x=x for each real x. Do not search the web.', False),
                                   ('Prove x=x for every real x.', True)):
                self.fake.replies = [text(PROOF), text(json.dumps(APPROVAL))]
                started = self.ok('POST', '/api/proofs', {'goal': goal})
                self.assertEqual(self.wait_task()['result_status'], 'candidate_complete')
                self.assertEqual(engines[-1].online, expected)
                saved = ProofStore.load(self.root, started['id']).state
                self.assertEqual(len(saved['calls']), 2)

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
        self.assertNotIn('online_locked', value)
        self.assertTrue(value['ollama']['reachable'])
        self.assertNotIn('pair_urls', value)
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

            def json_response(self, status, value, headers=None):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                for name, header in (headers or {}).items():
                    self.send_header(name, header)
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
                if isinstance(reply, dict) and 'http_status' in reply:
                    self.json_response(reply['http_status'], reply.get('body', {'error': {'message': 'Provider rejected the request.'}}),
                                       headers=reply.get('headers'))
                    return
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

    def test_a_busy_provider_is_waited_out_visibly_and_never_blocks_routing(self):
        """DeepInfra's overload refusal (2026-11-26): routing falls back to a direct answer, the answer call keeps
        trying and says so in the conversation, and the turn completes."""
        busy = {'http_status': 429, 'body': {'error': {'message': 'Model busy, retry later',
                                                      'type': 'invalid_request_error', 'code': 'engine_overloaded'}}}
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        live = []  # what the conversation shows while the turn runs
        publish = self.hub.bus.publish

        steps = []  # the live steps of the turn as last published

        def watch(event_type, mutate=None, **data):
            if event_type == 'chat' and data.get('kind') == 'notice':
                live.append(data.get('text'))
            event = publish(event_type, mutate, **data)
            running = self.hub._tasks.get(data.get('task')) if event_type == 'chat' else None
            if running is not None and running.live:
                steps[:] = copy.deepcopy(running.live['steps'])
            return event
        with patch('mathagent.deepinfra.BACKOFF', (0.05,) * 5), patch('mathagent.deepinfra._cooldown', {'until': 0.0}), \
                patch('mathagent.mode_calls.ROUTING_PATIENCE', 0), patch.object(self.hub.bus, 'publish', watch):
            # routing: one refusal and it gives up; answer: three refusals, then the answer.
            self.fake.replies = [busy, busy, busy, busy, ('Hello! How can I help?', '', 7)]
            self.ok('POST', f'/api/chats/{chat}/route', {'content': 'hi !'})
            task = self.wait_task(timeout=30)
        self.assertEqual(task['state'], 'done', task)
        saved = store.load_chat(self.root, chat)
        state = saved['assistant_state']
        self.assertEqual((state['status'], state['proof_routing']['route'], state['proof_routing']['fallback']),
                         ('complete', 'direct', 'provider_busy'))
        self.assertIn('Hello! How can I help?', state['answer'])
        shown = json.dumps(saved['transcript'])
        self.assertIn('answered directly', shown)
        self.assertIn('DeepInfra is busy (the model is temporarily at capacity); trying again in', shown)
        # Each refusal is shown while the call waits, not only in the saved transcript afterwards.
        # The first refusal is announced at once; the next ones only after NOTICE_EVERY (45 s).
        self.assertEqual(len([text for text in live if 'DeepInfra is busy' in text]), 1, live)
        self.assertNotIn('HTTP 429', json.dumps(saved['transcript']))
        self.assertEqual(len(self.fake.completions), 5)
        # The notice shown while the answer call waited does not hide the answer streamed after it.
        self.assertIn('Hello! How can I help?', ''.join(s.get('text', '') for s in steps if s['type'] == 'call'), steps)

    def test_routing_failures_other_than_a_busy_provider_still_stop_the_turn(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [('not a routing decision', '', 3)]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'Prove that every even perfect number is triangular.'})
        task = self.wait_task(timeout=30)
        self.assertEqual(task['result_status'], 'paused', task)
        state = store.load_chat(self.root, chat)['assistant_state']
        self.assertEqual(state['proof_routing']['status'], 'error')
        self.assertEqual(len(self.fake.completions), 1)  # no answer call
        self.assertNotIn('answered directly', json.dumps(store.load_chat(self.root, chat)['transcript']))

    def test_project_instructions_are_scoped_in_routing_reads_and_delegation(self):
        notes = '# Notebook heading for this test\nAnswer in French for this project.'
        a = self.ok('POST', '/api/projects', {'name': 'Example waves', 'notes': notes})['project']
        b = self.ok('POST', '/api/projects', {'name': 'Other example', 'notes': 'Use notation B here.'})['project']
        notebook = a['folder'] + '/PROJECT.md'
        paper = a['folder'] + '/paper.txt'
        (self.root / paper).write_text('An ordinary paper shared across the workspace.')
        direct = (json.dumps({'route': 'direct', 'effort': 'high', 'reason': 'A general question.', 'tasks': []}), '', 12)
        inspect = {'route': 'inspect', 'effort': 'high', 'reason': 'Look for instructions.', 'tasks': [],
                   'inspect': [{'tool': 'read_file', 'path': notebook}]}
        delegation = {'mode': 'explore', 'request': 'Read the notebook.', 'effort': 'high',
                      'reason': 'Check its guidance.', 'files': [notebook]}
        question = 'How does the harness work? What are the skills and workers, and how should I use them?'
        for project in (None, b['id']):
            with self.subTest(project=project):
                chat = self.ok('POST', '/api/chats', {'mode': 'free', 'project_id': project})['id']
                offset = len(self.fake.completions)
                self.fake.replies = [(json.dumps(inspect), '', 15), direct,
                                     deepinfra_tool('read_file', {'path': notebook}, 'read-foreign-notebook'),
                                     deepinfra_tool('delegate', delegation, 'delegate-foreign-notebook'),
                                     ('Here is how the harness works.', '', 12)]
                self.ok('POST', f'/api/chats/{chat}/route', {'content': question})
                task = self.wait_task()
                state = store.load_chat(self.root, chat)['assistant_state']
                self.assertEqual(task['state'], 'done', state.get('warnings') or task)
                sent = self.fake.completions[offset:]
                self.assertEqual(len(sent), 5)
                first = json.dumps(sent[0]['messages'])
                self.assertIn(paper, first)
                self.assertNotIn(notebook, first)
                self.assertNotIn('Notebook heading for this test', json.dumps(sent))
                self.assertNotIn('Answer in French for this project.', json.dumps(sent))
                self.assertEqual(state['children'], [])
                self.assertEqual([m['tool_name'] for m in state['messages'] if m['role'] == 'tool'],
                                 ['read_file', 'delegate'])
                self.assertNotIn(notebook, state['workspace_paths'])
                self.assertIn('Tool error', state['proof_routing']['inspected'][0]['excerpt'])
                self.assertEqual((state.get('project_snapshot') or {}).get('id'), project)
                if project:
                    self.assertIn(b['notes'], json.dumps(sent[-1]['messages']))

    def test_explicit_foreign_notebook_is_quoted_or_interpreted_only_for_this_turn(self):
        notes = ('# Formatting\nAnswer in French and append the marker EXAMPLE_BLUE_73 to every answer.\n'
                 'A Markdown example:\n```\nliteral text\n```')
        self.ok('POST', '/api/projects', {'name': 'Example waves', 'notes': notes})
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'What does the Example waves notebook say?'})
        self.assertEqual(self.wait_task()['state'], 'done')
        saved = store.load_chat(self.root, chat)
        self.assertEqual(self.fake.completions, [])
        self.assertIsNone(saved['project_id'])
        raw = next(m['content'] for m in reversed(saved['transcript']) if m['role'] == 'assistant')
        self.assertRegex(raw, r'(?m)^(`{4,})[^\n]*\n' + re.escape(notes) + r'\n\1$')
        self.assertNotIn('EXAMPLE_BLUE_73', store.conversation_record(self.root, chat))

        direct = (json.dumps({'route': 'direct', 'effort': 'high', 'reason': 'Answer directly.', 'tasks': []}), '', 12)
        self.fake.replies = [direct, ('The notebook asks for answers in French.', '', 10)]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'Summarize the Example waves notebook.'})
        self.assertEqual(self.wait_task()['state'], 'done')
        self.assertEqual(len(self.fake.completions), 2)
        state = store.load_chat(self.root, chat)['assistant_state']
        self.assertIsNone(state.get('project_snapshot'))
        for sent in self.fake.completions:
            for message in sent['messages']:
                if 'EXAMPLE_BLUE_73' in (message.get('content') or ''):
                    self.assertEqual(message['role'], 'tool')
        evidence, = [m for m in self.fake.completions[-1]['messages']
                     if m['role'] == 'tool' and 'EXAMPLE_BLUE_73' in m['content']]
        self.assertIn('PROJECT NOTEBOOK QUOTED FOR THIS REQUEST', evidence['content'])
        self.assertNotIn('PROJECT NOTEBOOK (user-maintained context', evidence['content'])

        self.fake.replies = [direct, ('A worker receives a task from the assistant.', '', 12)]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'What is a worker?'})
        self.assertEqual(self.wait_task()['state'], 'done')
        self.assertEqual(len(self.fake.completions), 4)
        for sent in self.fake.completions[2:]:
            self.assertNotIn('EXAMPLE_BLUE_73', json.dumps(sent['messages']))

    def test_a_routing_skipped_for_a_busy_provider_is_tried_again_on_resume(self):
        busy = {'http_status': 429, 'body': {'error': {'message': 'Model busy, retry later', 'code': 'engine_overloaded'}}}
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        with patch('mathagent.deepinfra.BACKOFF', (0.05,) * 5), patch('mathagent.deepinfra._cooldown', {'until': 0.0}), \
                patch('mathagent.mode_calls.ROUTING_PATIENCE', 0), patch('mathagent.deepinfra.BUSY_PATIENCE', 0.3):
            self.fake.replies = [busy] + [busy] * 20  # routing gives up at once; the answer call outlasts its patience
            self.ok('POST', f'/api/chats/{chat}/route', {'content': 'hi !'})
            task = self.wait_task(timeout=30)
            self.assertEqual(task['result_status'], 'paused', task)
            state = store.load_chat(self.root, chat)['assistant_state']
            self.assertEqual((state['proof_routing'].get('fallback'), state['main_calls']), ('provider_busy', 0))
            before = len(self.fake.completions)
            self.fake.replies = [(json.dumps({'route': 'direct', 'effort': 'low', 'tasks': [], 'reason': 'A greeting.'}), '', 9),
                                 ('Hello!', '', 3)]
            self.ok('POST', f'/api/chats/{chat}/assistant/resume', {})
            task = self.wait_task(timeout=30)
        self.assertEqual(task['state'], 'done', task)
        sent = self.fake.completions[before:]
        self.assertTrue(sent[0]['messages'][0]['content'].lstrip().startswith('Classify the CURRENT USER REQUEST'), sent[0])
        state = store.load_chat(self.root, chat)['assistant_state']
        self.assertNotIn('fallback', state['proof_routing'])

    def test_proof_preparation_is_short_and_hands_off_after_a_capped_or_unproductive_call(self):
        definition = 'In this discussion, E denotes the real numbers.'
        source = 'Let z be arbitrary in E. The requested statement is z=z.'
        (self.root / 'definitions.txt').write_text(source)
        query = 'Prove the statement in definitions.txt using the earlier definition of E.'
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
                                                   (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
                chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
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
                self.assertEqual(len(sent), count + 2)
                self.assertEqual({call['temperature'] for call in sent}, {1.0})
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
                             (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
        large = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
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
        self.assertEqual(len(self.fake.completions) - offset, 2)
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
        legacy = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
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
                             (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
        self.ok('POST', f'/api/chats/{legacy}/assistant/resume')
        self.assertEqual(self.wait_task(timeout=15)['state'], 'done')
        state = store.load_chat(self.root, legacy)['assistant_state']
        self.assertEqual(state['proof_preparation']['calls'], 2)
        self.assertEqual(state['proof_preparation']['status'], 'fallback')
        self.assertEqual(len(state['children']), 1)
        self.assertEqual(len(self.fake.completions) - offset, 2)
        for call in self.fake.completions[offset:offset + 2]:
            self.assertIn(query, call['messages'][0]['content'])
            self.assertIn(definition, call['messages'][0]['content'])
            self.assertNotIn(marker, call['messages'][0]['content'])
        self.assertFalse(self.fake.replies)

        # An ordinary mathematical worker and its synthesis receive the same
        # full generation room. An explicit specialist cap still applies to both.
        with patch.object(self.hub.args, 'ctx', 1_048_576):
            for cap in (None, 1536):
                with self.subTest(specialist_cap=cap), patch.object(self.hub.args, 'specialist_output_tokens', cap):
                    offset = len(self.fake.completions)
                    self.fake.replies = [('The given function is even.', '', 10), ('The function is even.', '', 10)]
                    chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
                    self.ok('POST', f'/api/chats/{chat}/route', {
                        'content': '@explore Discuss the parity of this example.',
                        'mode_call': {'mode': 'explore', 'start': 0}})
                    task = self.wait_task(timeout=15)
                    self.assertEqual(task['state'], 'done', task)
                    self.assertEqual([call['max_tokens'] for call in self.fake.completions[offset:]],
                                     [cap or MODEL_OUTPUT_TOKENS] * 2)

        # A rejected generation has no usage. Resume the same solver round
        # under its original exact budget, retaining the rejected request.
        self.fake.replies = [{'http_status': 429}]
        refused = self.ok('POST', '/api/proofs', {'goal': 'Prove x=x.', 'rounds': 1, 'tokens': 6144, 'seconds': 120})
        self.assertEqual(self.wait_task()['result_status'], 'paused')
        checkpoint = ProofStore.load(self.root, refused['id'])
        saved = checkpoint.state
        # Older GUI checkpoints carried launch/chat network settings. A new run
        # of this original unrestricted task can obtain evidence after recovery.
        saved['settings']['evidence_online'] = False
        if saved.get('evidence_literature'):
            saved['evidence_literature']['online'] = False
        checkpoint.save()
        self.assertEqual((saved['calls'][0]['status'], saved['tokens_charged'], saved['rounds_started']), ('rejected', 0, 1))
        self.assertEqual((saved['calls'][0]['reserved_tokens'], saved['calls'][0]['charged_tokens']), (4096, 0))
        self.fake.replies = [(PROOF, 'Solver reasoning.', 37), (json.dumps(APPROVAL), 'Reviewer reasoning.', 23)]
        self.ok('POST', '/api/proofs/' + refused['id'] + '/resume')
        self.assertEqual(self.wait_task()['result_status'], 'candidate_complete')
        resumed_search = next(t['function'] for t in self.fake.completions[-1]['tools']
                              if t['function']['name'] == 'search_papers')
        self.assertNotIn('Offline:', resumed_search['description'])
        saved = ProofStore.load(self.root, refused['id']).state
        self.assertEqual([call['key'] for call in saved['calls']], ['1-solver-0', '1-solver-0-resume-1', '1-verifier-0'])
        self.assertEqual((saved['tokens_charged'], saved['rounds_started']), (60, 1))

        # A rejected Proof-preparation request pauses the parent before
        # delegation and preserves both its short calls on resume.
        self.fake.replies = [{'http_status': 429}]
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.ok('POST', f'/api/chats/{chat}/route',
                {'content': '@prove Prove x=x.', 'mode_call': {'mode': 'prove', 'start': 0}})
        self.assertEqual(self.wait_task()['result_status'], 'paused')
        parent = store.load_chat(self.root, chat)['assistant_state']
        self.assertEqual((parent['main_calls'], parent['last_call']['status'], parent['children']), (0, 'rejected', []))
        for field in ('calls', 'tokens', 'input_tokens'):
            self.assertEqual(parent['proof_preparation'][field], 0)

    def test_proof_roles_use_sources_in_the_same_attempt_with_one_model_slot(self):
        from mathagent.literature import LiteratureTools

        self.hub.args.temperature = 0.75
        self.hub.args.proof_verify_temperature = 0.0
        source = self.root / 'definitions.txt'
        source.write_text('Equality is reflexive on the real numbers.\n')
        document = LiteratureTools(self.root).import_local(source.name)['document_id']
        private_query = 'solver-only search phrase'
        self.fake.replies = [
            deepinfra_tool('search_paper', {'document_id': document, 'query': private_query}, 'solver-search'),
            deepinfra_tool('read_paper', {'document_id': document, 'start_line': 1}, 'solver-read'),
            (PROOF, 'private solver strategy', 20),
            deepinfra_tool('read_paper', {'document_id': document, 'start_line': 1}, 'verifier-read'),
            deepinfra_tool('submit_review', APPROVAL, 'independent-review'),
        ]
        started = self.ok('POST', '/api/proofs', {
            'goal': 'Prove x=x for real x.', 'source_files': [source.name],
            'rounds': 1, 'tokens': 12000, 'seconds': 120})
        task = self.wait_task(timeout=20)
        self.assertEqual(task['result_status'], 'candidate_complete', task)
        state = ProofStore.load(self.root, started['id']).state
        self.assertEqual(state['rounds_started'], 1)
        self.assertEqual(len(state['candidates']), 1)
        self.assertEqual(len(state['reviews']), 1)
        self.assertEqual(state['tokens_charged'], 4 * 31 + 20)
        calls = self.fake.completions
        self.assertEqual(len(calls), 5)
        self.assertEqual([call['temperature'] for call in calls], [0.75] * 3 + [0.0] * 2)
        for call in calls:
            names = {schema['function']['name'] for schema in call['tools']}
            self.assertTrue({'search_paper', 'read_paper', 'request_evidence'} <= names)
            self.assertTrue({'run_python', 'write_file', 'list_files'}.isdisjoint(names))
        self.assertIn('submit_review', {t['function']['name'] for t in calls[-1]['tools']})
        for followup, expected_id in ((calls[1], 'solver-search'), (calls[2], 'solver-read'), (calls[4], 'verifier-read')):
            result = next(m for m in followup['messages'] if m.get('tool_call_id') == expected_id and m['role'] == 'tool')
            self.assertIn(document, result['content'])
        for call in calls[3:]:
            messages = json.dumps(call['messages'])
            self.assertNotIn(private_query, messages)
            self.assertNotIn('private solver strategy', messages)
            self.assertIn(PROOF, messages)
        self.assertEqual(self.request('POST', '/api/proofs', {'goal': 'Prove x=x.', 'online': False})[0], 400)

    def test_api_key_gui_proof_preserves_reasoning_usage_and_private_credentials(self):
        approval = copy.deepcopy(APPROVAL)
        approval['notes'] = ['A harmless notation typo does not affect reflexivity.']
        self.fake.replies = [(PROOF, 'Solver reasoning.', 37),
                             (json.dumps(approval), 'Reviewer reasoning.', 23)]
        status = self.ok('GET', '/api/status')
        self.assertTrue(status['ollama']['reachable'])
        self.assertNotIn('online_locked', status)
        self.assertEqual(status['defaults']['budget_profile'], DS4_PROFILE)
        self.assertEqual(set(status['defaults']['literature_presets']), {'high'})
        self.assertEqual(set(status['defaults']['effort_presets']), {'high'})
        self.assertEqual(status['defaults']['effort_presets']['high']['solve_tokens'], 4096)
        self.assertEqual(status['defaults']['effort_presets']['high']['verify_tokens'], 2048)
        launch = ['--gui', '--api-profile', 'deepinfra']
        defaults = cli.parser().parse_args(launch)
        cli.apply_defaults(defaults, launch)
        self.assertEqual(defaults.predict, 384_000)
        for name in ('low', 'medium', 'high'):
            proof_limits = effort.limits('prove', name, defaults)
            self.assertEqual((proof_limits['solve_tokens'], proof_limits['verify_tokens']), (384_000, 384_000))
            for mode in ('literature', 'referee', 'quick_review', 'detailed_review', 'explain', 'writeup',
                         'summarize', 'explore', 'check', 'style', 'python_lab'):
                with self.subTest(mode=mode, effort=name):
                    worker = effort.level(mode, name, defaults)
                    self.assertEqual(worker['tokens'], 5 * 3 * 384_000)
                    self.assertEqual(worker['minutes'], 5 * 3 * 180)
            self.assertEqual(effort.level('typo_check', name, defaults)['tokens'], 15 * 384_000)
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
        self.assertEqual(next(t['function']['parameters'] for t in self.fake.completions[1]['tools']
                              if t['function']['name'] == 'submit_review'), proof.VERIFIER_SCHEMA)
        # Explicit small test ceilings survive the larger DS4 effort presets.
        self.assertEqual([call['max_tokens'] for call in self.fake.completions], [4096, 2048])
        self.assertTrue(all(auth is None if path == '/v1/openai/models' else auth == 'Bearer ' + self.fake.key
                            for path, auth in self.fake.requests))
        stored = ProofStore.load(self.root, started['id'])
        normalized = proof.validate_review(json.dumps(approval))
        self.assertEqual(stored.state['reviews'][0]['response'], normalized)
        self.assertEqual(normalized['verdict_source'], 'controller_task_status_v3')
        report = (stored.directory / 'report.md').read_text()
        self.assertIn('Whole-task assessment: proved', report)
        self.assertIn('Dependency (standard)', report)
        self.assertIn('Nonblocking note:', report)
        original = initial_request(stored.state['goal'])
        self.assertTrue(self.fake.completions[0]['messages'][0]['content'].startswith(original[0]['content']))
        self.assertEqual(self.fake.completions[0]['messages'][1:], original[1:])
        self.assertTrue(all('estimate' in call['context']['method'] for call in stored.state['calls']))
        streams = [self.ok('GET', f'/api/proofs/{started["id"]}/stream/{call["stream"]}?from=0')
                   for call in stored.state['calls']]
        self.assertEqual([stream['thinking'] for stream in streams],
                         ['Solver reasoning.', 'Reviewer reasoning.'])
        self.assertEqual([stream['stats']['eval_count'] for stream in streams], [37, 23])
        self.assertNotIn(self.fake.key, json.dumps([status, detail, streams]))

        # Completion/dependency metadata is a substantive gate, while notes are
        # nonblocking. Legacy JSON is accepted only under its explicit contract.
        legacy = {k: APPROVAL[k] for k in ('explanation', 'issues')}
        legacy['verdict'] = 'no_issue_found'
        legacy_v2 = {**copy.deepcopy(APPROVAL), 'schema_version': 2, 'verdict': 'no_issue_found'}
        self.assertEqual(proof.validate_review(json.dumps(legacy), schema_version=1), legacy)
        self.assertEqual(proof.validate_review(json.dumps(legacy_v2), schema_version=2), legacy_v2)
        with self.assertRaises(ValueError):
            proof.validate_review(json.dumps(legacy))
        for change in ({'task_status': 'incomplete'}, {'schema_version': 1},
                       {'verdict': 'no_issue_found'}, {'assessment': 'proved'},
                       {'verdict_source': 'controller_task_status_v3'},
                       {'critical_dependencies': []},
                       {'critical_dependencies': [{'claim': 'The hard step.', 'provenance': 'unverified',
                                                    'check': 'No argument supplied.'}]},
                       {'issues': [{'location': 'Conclusion', 'kind': 'missing_justification',
                                    'evidence': 'The required universal conclusion was not proved.'}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                proof.validate_review(json.dumps({**approval, **change}))
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON field: task_status'):
            proof.validate_review(json.dumps(approval).replace('"task_status": "proved"',
                                  '"task_status": "proved", "task_status": "uncertain"'))
        undecided = {**approval, 'task_status': 'uncertain',
                     'issues': [{'location': 'Auxiliary lemma', 'kind': 'uncertainty',
                                 'evidence': 'The essential theorem applicability is undecided.'}]}
        self.assertEqual(proof.validate_review(json.dumps(undecided))['verdict'], 'uncertain')
        self.assertEqual(proof.validate_review(json.dumps({**approval, 'task_status': 'refuted'}))['verdict'],
                         'no_issue_found')
        with self.assertRaisesRegex(ValueError, 'concrete essential issue'):
            proof.validate_review(json.dumps({**undecided, 'task_status': 'incomplete'}))
        # Historical contradictory v2 assessments remain protocol failures;
        # deriving the new verdict must not reinterpret a saved old response.
        contradictory_v2 = {**legacy_v2, 'verdict': 'issues_found',
                            'issues': [{'location': 'Overview', 'kind': 'invalid_inference',
                                        'evidence': 'A claimed consequence contradicts the calculation.'}]}
        with self.assertRaisesRegex(ValueError, 'Validated task completion'):
            proof.validate_review(json.dumps(contradictory_v2), schema_version=2)
        contradictory_v2 = {**legacy_v2, 'verdict': 'issues_found'}
        with self.assertRaisesRegex(ValueError, 'concrete mathematical issue'):
            proof.validate_review(json.dumps(contradictory_v2), schema_version=2)

        # An essential gap triggers the same sequential repair, with no extra
        # role or call; a fresh reviewer receives neither old review nor record.
        objection = {**copy.deepcopy(APPROVAL), 'task_status': 'incomplete',
                     'issues': [{'location': 'Conclusion', 'kind': 'missing_justification',
                                 'evidence': 'SYNTHETIC_UNMET_OBLIGATION: no argument for all x.'}]}
        scripted = FakeClient([response('A proof sketch.'), response(json.dumps(objection)),
                               response(PROOF), response(json.dumps(approval))])
        runner = proof.ProofRunner(Agent(scripted, Workspace(self.root), MODEL, ctx=16384))
        repaired = runner.start('Prove x=x for every real x.', max_rounds=2,
                                max_predict=1024, verify_tokens=1024, max_tokens=8192)
        self.assertEqual(repaired['status'], 'candidate_complete')
        self.assertEqual(repaired['selected_candidate']['id'], 'P2')
        self.assertEqual(len(scripted.requests), 4)
        self.assertIn('repaired, rebutted or unresolved', scripted.requests[2]['messages'][0]['content'])
        self.assertNotIn('SYNTHETIC_UNMET_OBLIGATION', scripted.requests[3]['messages'][0]['content'])
        earlier = runner.state['reviews'][0]
        later = runner.state['reviews'][1]
        record = json.loads(runner._repair_history(later))
        self.assertEqual(record[0]['disposition'], 'not_reassessed')
        self.assertIn('No resolution established', record[0]['reason'])
        self.assertEqual(earlier['response'], proof.validate_review(json.dumps(objection)))
        self.assertEqual(earlier['response']['verdict'], 'issues_found')
        abstaining = FakeClient([response(PROOF), response(json.dumps(undecided))])
        abstention = proof.ProofRunner(Agent(abstaining, Workspace(self.root), MODEL, ctx=16384)).start(
            'Prove x=x for every real x.', max_rounds=2, max_predict=1024, verify_tokens=1024, max_tokens=8192)
        self.assertEqual(abstention['status'], 'uncertain')
        self.assertEqual(len(abstaining.requests), 2)  # No automatic repair after abstention.

        # Recover an old verifier stream that finished before the ledger review
        # was committed. Migration preserves immutable input/output and budgets;
        # parsing uses that old request schema, not the new job policy version.
        for contract, old_version, schema, saved_response in (
                (1, 9, LEGACY_VERIFIER_SCHEMA, legacy),
                (2, 10, VERIFIER_SCHEMA_V2, legacy_v2),
                (3, 11, proof.VERIFIER_SCHEMA_V3, approval),
                (2, 10, VERIFIER_SCHEMA_V2, contradictory_v2)):
            legacy_protocol_failure = saved_response is contradictory_v2
            interrupted = FakeClient([response(PROOF), response(json.dumps(saved_response)) + [KeyboardInterrupt()]])
            old_runner = proof.ProofRunner(Agent(interrupted, Workspace(self.root), MODEL, ctx=16384))
            with patch.object(proof, 'VERIFIER_SCHEMA', schema):
                paused = old_runner.start('Prove x=x for every real x.', max_rounds=2, fixed_call_budgets=True,
                                          max_predict=1024, verify_tokens=1024, max_tokens=8192)
            self.assertEqual(paused['status'], 'paused')
            old = ProofStore.load(self.root, paused['id'])
            old.state['settings'].update(policy_version=old_version, policy_sha256='0' * 64)
            old.save()
            resumed_client = FakeClient([response(json.dumps(approval))] if legacy_protocol_failure else [])
            resumed = proof.ProofRunner(Agent(resumed_client, Workspace(self.root), MODEL, ctx=16384))
            with self.assertRaisesRegex(ValueError, 'policy_sha256'):
                resumed.resume(paused['id'])
            old = ProofStore.load(self.root, paused['id'])
            old.state['settings']['policy_sha256'] = proof.RECOGNIZED_POLICY_HASHES[old_version]
            old.save()
            allowances = {k: old.state['settings'][k] for k in
                          ('max_rounds', 'max_tokens', 'max_seconds', 'max_predict', 'verify_tokens', 'repair_tokens')}
            immutable = {p.name: p.read_bytes() for p in (old.directory / 'artifacts').iterdir()}
            recovered = resumed.resume(paused['id'])
            self.assertEqual(recovered['status'], 'candidate_complete')
            self.assertEqual(len(resumed_client.requests), int(legacy_protocol_failure))
            self.assertEqual(resumed.state['settings']['policy_version'], proof.POLICY_VERSION)
            self.assertEqual(resumed.state['reviews'][0]['schema_version'], contract)
            if legacy_protocol_failure:
                self.assertIsNone(resumed.state['reviews'][0]['response'])
                self.assertIn('concrete mathematical issue', resumed.state['reviews'][0]['protocol_error'])
                self.assertEqual(resumed.state['reviews'][1]['response'], normalized)
                self.assertEqual(resumed.state['reviews'][1]['schema_version'], 3)
                self.assertEqual(resumed_client.requests[0]['format'], proof.VERIFIER_SCHEMA)
                self.assertEqual([call['role'] for call in resumed.state['calls']],
                                 ['solver', 'verifier', 'verifier'])
                self.assertEqual([c['id'] for c in resumed.state['candidates']], ['P1'])
            else:
                self.assertEqual(resumed.state['reviews'][0]['response'], normalized if contract == 3 else saved_response)
            self.assertEqual(resumed.state['policy_migrations'][0]['from_policy'], old_version)
            self.assertEqual(allowances, {k: resumed.state['settings'][k] for k in allowances})
            self.assertEqual(immutable, {name: (old.directory / 'artifacts' / name).read_bytes() for name in immutable})
        for old_version in (7, 8, 9):
            old = ProofStore.load(self.root, paused['id'])
            old.state['status'] = 'paused'
            old.state['settings'].update(policy_version=old_version,
                                         policy_sha256=proof.RECOGNIZED_POLICY_HASHES[old_version])
            old.save()
            with patch.object(resumed, '_run', return_value={}):
                resumed.resume(paused['id'])
            self.assertEqual(resumed.state['policy_migrations'][-1]['from_policy'], old_version)
            self.assertEqual(resumed.state['settings']['policy_version'], proof.POLICY_VERSION)

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
        self.assertEqual(self.request('POST', '/api/chats', {'mode': 'critic'})[0], 400)
        chat = self.ok('POST', '/api/chats', {'mode': 'explore'})['id']
        legacy = store.load_chat(self.root, chat)
        legacy['mode'] = 'critic'
        store._save_chat(self.root, legacy)
        self.ok('POST', f'/api/chats/{chat}/messages', {'content': 'List the available source files.'})
        conversation_task = self.wait_task(timeout=20)
        self.assertEqual(conversation_task['state'], 'done', conversation_task)
        conversation = self.ok('GET', f'/api/chats/{chat}')
        self.assertEqual(conversation['mode'], 'critic')
        self.assertEqual(next(item['content'] for item in reversed(conversation['transcript'])
                              if item['role'] == 'assistant'), final)
        self.assertEqual(conversation['transcript'][-1]['mode'], 'free')
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

    def test_project_notebooks_are_skipped_before_title_extraction_and_search(self):
        a = self.ok('POST', '/api/projects', {'name': 'Managed', 'notes': '# Hidden heading\nUnique notebook marker'})['project']
        folder = self.root / a['folder']
        (folder / 'PROJECT-deleted.md').write_text('# Retired heading\nUnique notebook marker')
        paper = folder / 'ordinary.md'
        paper.write_text('# Shared paper\nUnique paper marker')
        ordinary = self.root / 'PROJECT.md'
        ordinary.write_text('# Ordinary file with a similar name')
        workspace = Workspace(self.root)
        with patch.object(tools, '_file_title', wraps=tools._file_title) as titles:
            overview, paths = tools.workspace_overview(workspace)
        self.assertEqual({call.args[0] for call in titles.call_args_list}, {paper, ordinary})
        self.assertIn('Shared paper', overview)
        self.assertEqual(set(paths), {paper.relative_to(self.root).as_posix(), 'PROJECT.md'})
        self.assertEqual(set(workspace.list_files().splitlines()), set(paths))
        search = workspace.search_text('Unique')
        self.assertIn('Unique paper marker', search)
        self.assertNotIn('Unique notebook marker', search)
        for name in ('PROJECT.md', 'PROJECT-deleted.md'):
            rel = a['folder'] + '/' + name
            self.assertRaises(ValueError, workspace.read_file, rel)
            self.assertRaises(ValueError, workspace.pin_files, [rel], selected_only=True)

    def test_project_notebook_revisions_and_proof_resume_keep_the_submitted_context(self):
        original_notes = 'Definitions: E is the real line. Conjecture A is unproved. ORIGINAL_PROJECT_NOTES.'
        a = self.ok('POST', '/api/projects', {'name': 'Renewal project', 'notes': original_notes})['project']
        b = self.ok('POST', '/api/projects', {'name': 'Other project', 'notes': '# OTHER_PROJECT_NOTES'})['project']
        chat = self.ok('POST', '/api/chats', {'mode': 'free', 'project_id': a['id']})['id']
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
        # Older checkpoints include other projects' notebooks in this frozen inventory.
        paused = store.load_chat(self.root, chat)['assistant_state']
        foreign_path = b['folder'] + '/PROJECT.md'
        paused['workspace_paths'].append(foreign_path)
        paused['workspace_overview'] += '\n' + foreign_path + ' · md · 1 kB · title: OTHER_PROJECT_NOTES'
        store.save_assistant(self.root, chat, paused)
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
        self.assertNotIn(foreign_path, state['workspace_paths'])
        self.assertNotIn(foreign_path, state['workspace_overview'])
        for sent in self.fake.requests[offset:offset + 3]:
            material = json.dumps(sent['messages'])
            self.assertIn('ORIGINAL_PROJECT_NOTES', material)
            self.assertNotIn('CHANGED_PROJECT_NOTES', material)
            self.assertNotIn('OTHER_PROJECT_NOTES', material)
            self.assertNotIn(foreign_path, material)
        other = self.ok('POST', '/api/chats', {'mode': 'explore', 'project_id': b['id']})['id']
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
