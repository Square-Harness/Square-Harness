"""The Assistant answers and delegates inside one saved, bounded GUI turn."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

try:
    from .test_gui import GuiCase, text, tool, PROOF, APPROVAL
except ImportError:
    from test_gui import GuiCase, text, tool, PROOF, APPROVAL

from mathagent.gui import store
from mathagent.worker_errors import WorkerInputError


def delegation(mode, request, files=(), effort='low'):
    return tool('delegate', {'mode': mode, 'request': request, 'files': list(files),
                             'effort': effort, 'reason': 'An independent check will help.'})


class FakeOpenAI:
    """The GUI fixture's scripted replies exposed through actual SSE/vLLM APIs."""

    def __init__(self):
        self.replies, self.requests = [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({'data': [{'id': 'test-model:latest'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/tokenize':
                    body = json.dumps({'count': 200, 'max_model_len': 40960}).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                fake.requests.append(payload)
                reply = fake.replies.pop(0) if fake.replies else text('Unexpected extra call.')
                events, delay = reply if isinstance(reply, tuple) else (reply, 0)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for event in events:
                    if delay:
                        time.sleep(delay)
                    message = event.get('message', {})
                    delta = {key: message[key] for key in ('content',) if key in message}
                    if message.get('thinking'):
                        delta['reasoning_content'] = message['thinking']
                    if message.get('tool_calls'):
                        delta['tool_calls'] = [{'index': index, 'id': f'tool_{index}', 'type': 'function',
                                               'function': {'name': call['function']['name'],
                                                            'arguments': (call['function']['arguments'] if isinstance(call['function']['arguments'], str)
                                                                          else json.dumps(call['function']['arguments']))}}
                                              for index, call in enumerate(message['tool_calls'])]
                    output = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}]
                    if event.get('done'):
                        output += [{'choices': [{'index': 0, 'delta': {}, 'finish_reason':
                                                'tool_calls' if message.get('tool_calls') else event.get('done_reason', 'stop')}]},
                                   {'choices': [], 'usage': {'prompt_tokens': event.get('prompt_eval_count', 200),
                                                            'completion_tokens': event.get('eval_count', 20)}}]
                    try:
                        for item in output:
                            self.wfile.write(('data: ' + json.dumps(item) + '\n\n').encode())
                        if event.get('done'):
                            self.wfile.write(b'data: [DONE]\n\n')
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


class AssistantGuiTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def conversation(self, **settings):
        return self.ok('POST', '/api/chats', {'mode': 'free', **settings})['id']

    def ask(self, chat, content, files=()):
        return self.ok('POST', f'/api/chats/{chat}/route',
                       {'content': content, 'files': list(files)})

    def detail(self, chat):
        return self.ok('GET', f'/api/chats/{chat}')

    def openai_fixture(self):
        self.fake.close()
        self.fake = FakeOpenAI()
        self.addCleanup(self.fake.close)
        self.hub.args.backend, self.hub.args.host = 'openai', self.fake.host





    def test_proof_worker_returns_to_parent_and_preserves_exact_sources(self):
        chat = self.conversation()
        source = 'Claim: every real x satisfies x=x.\nHypothesis: x is an arbitrary real number.\n'
        (self.root / 'identity.tex').write_text(source)
        query = 'Prove identity.tex. Keep its arbitrary real quantifier exactly.'
        final = 'The candidate uses reflexivity for arbitrary real x. The model review found no issue.'
        self.fake.replies = [delegation('prove', 'Prove the identity in identity.tex.', ['identity.tex']),
                             text(PROOF, eval_count=53), text(json.dumps(APPROVAL), eval_count=31),
                             text(final, eval_count=27)]
        self.ask(chat, query, files=['identity.tex'])
        task = self.wait_task(timeout=30)
        self.assertEqual(task['state'], 'done', task)
        self.assertEqual((task['kind'], task['target']), ('chat', chat))
        detail = self.detail(chat)
        self.assertEqual(detail['assistant_status'], 'complete')
        cards = [item for item in detail['transcript'] if item['role'] == 'route']
        self.assertEqual(len(cards), 1, cards)
        card = cards[0]
        self.assertTrue(card['orchestrated'])
        self.assertEqual((card['mode'], card['status']), ('prove', 'done'))
        proof = self.ok('GET', f'/api/proofs/{card["job_id"]}')
        self.assertEqual((proof['status'], proof['answer']), ('candidate_complete', PROOF))
        self.assertEqual(proof['goal'], 'Prove the identity in identity.tex.')
        self.assertEqual(proof['settings']['reference_context']['query'], query)
        self.assertEqual(self.ok('GET', f'/api/proofs/{card["job_id"]}/sources')['sources'][0]['content'], source)
        parent_final = self.fake.requests[-1]
        result = json.loads(next(message['content'] for message in reversed(parent_final['messages'])
                                 if message['role'] == 'tool'))
        self.assertEqual(result['result']['status'], 'candidate_complete')
        self.assertIn(PROOF, json.dumps(result))
        self.assertEqual(next(item['content'] for item in reversed(detail['transcript'])
                              if item['role'] == 'assistant'), final)
        self.assertEqual(detail['assistant_budget']['tokens']['used'], 8 + 53 + 31 + 27)
        self.assertEqual(self.ok('GET', '/api/task')['queue'], [])

        # Seen live: a critic whose reasoning filled its output limit returned an empty answer. It is now
        # asked for the answer, without reasoning or tools, and the Assistant gets it.
        critique = 'No gap: reflexivity holds for every real x.'
        self.fake.replies = [delegation('critic', 'Check that proof for gaps.', effort='medium'),
                             [{'message': {'thinking': 'Reasoning about reflexivity. ' * 40}, 'done': True,
                               'done_reason': 'length', 'eval_count': 900, 'prompt_eval_count': 300}],
                             text(critique, eval_count=12), text('The critique found no gap.', eval_count=9)]
        before = len(self.fake.requests)
        self.ask(chat, 'Now check that proof for gaps.')
        cards = lambda: [item for item in self.detail(chat)['transcript'] if item['role'] == 'route']
        idle = lambda: not self.ok('GET', '/api/task')['tasks'] and self.detail(chat)['assistant_status'] == 'complete'
        self.wait_for(lambda: len(cards()) == 2 and idle(), timeout=30)
        self.assertEqual((cards()[-1]['mode'], cards()[-1]['status']), ('critic', 'done'))
        answer_now = self.fake.requests[before + 2]
        self.assertFalse(answer_now['think'])
        self.assertNotIn('tools', answer_now)
        self.assertIn('output limit', answer_now['messages'][-1]['content'])
        worker = json.loads(next(message['content'] for message in reversed(self.fake.requests[-1]['messages'])
                                 if message['role'] == 'tool'))
        self.assertEqual((worker['result']['status'], worker['result']['answer']), ('complete', critique))

        # Seen live: "Let me do targeted checks…" ended a turn. It is now continued once.
        self.fake.replies = [text('The critic found no gap. Let me do targeted checks for these specific works.'),
                             text('Answer: no gap, and no further check is needed.')]
        self.ask(chat, 'Is anything left to check?')
        answer = lambda: [item['content'] for item in self.detail(chat)['transcript'] if item['role'] == 'assistant']
        self.wait_for(lambda: answer()[-1:] == ['Answer: no gap, and no further check is needed.'] and idle(), timeout=30)
        # A new message sets an unfinished turn aside instead of being refused.
        store.save_assistant(self.root, chat, {**store.load_chat(self.root, chat)['assistant_state'], 'status': 'incomplete'})
        self.assertEqual(self.detail(chat)['assistant_status'], 'paused')
        self.fake.replies = [text('A fresh answer.')]
        self.ask(chat, 'Never mind; a new question.')
        self.wait_for(lambda: answer()[-1:] == ['A fresh answer.'] and idle(), timeout=30)
        self.assertTrue(any('set aside' in (item.get('content') or '') for item in self.detail(chat)['transcript']
                            if item['role'] == 'notice'))

        # Checking computations that mention a book is a check of the computations, not a reference lookup.
        self.fake.replies = [text('No mistake: both computations are right.')]
        before = len(self.fake.requests)
        self.ask(chat, "Can you check these computations? Any mistake? The setting is from Karatzas–Shreve's book.")
        self.wait_for(lambda: answer()[-1:] == ['No mistake: both computations are right.'] and idle(), timeout=30)
        self.assertIn('tools', self.fake.requests[before])  # the Assistant itself answered, with its tools
        self.assertEqual(len(cards()), 2)  # no automatic check card
