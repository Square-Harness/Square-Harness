# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Native Ollama HTTP client and bounded, backend-independent agent loop."""
import copy
import json
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .prompts import SYSTEM, MODES
from .model_limits import input_limit


class AgentError(Exception):
    pass


class ModelNotDispatched(AgentError):
    """The local admission check refused the request before any generation was sent."""


class _NoModelRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a manuscript or local-only request to a redirect target.
        # Configure the final Ollama host explicitly instead.
        return None


class Ollama:
    backend = 'ollama'

    def __init__(self, host='http://localhost:11434', timeout=600):
        self.host = host.rstrip('/')
        self.timeout = timeout
        # Local model traffic should not inherit an HTTP proxy configuration.
        self.opener = build_opener(ProxyHandler({}), _NoModelRedirects())

    def request(self, endpoint, data=None):
        req = Request(self.host + endpoint,
                      data=None if data is None else json.dumps(data).encode(),
                      headers={'Content-Type': 'application/json'})
        try:
            return self.opener.open(req, timeout=self.timeout)
        except HTTPError as e:
            with e:
                detail = e.read(2000).decode(errors='replace')
            raise AgentError(f'Ollama HTTP {e.code}: {detail}') from e
        except (URLError, TimeoutError, OSError) as e:
            raise AgentError(f'Cannot reach Ollama at {self.host}: {e}. Check ollama serve.') from e

    def models(self):
        try:
            with self.request('/api/tags') as response:
                result = json.load(response)
                models = [m['name'] for m in result['models']]
                if any(not isinstance(name, str) for name in models):
                    raise ValueError('Invalid model name')
                return models
        except (OSError, HTTPException, ValueError, TypeError, KeyError) as exc:
            raise AgentError('Malformed or interrupted Ollama model-list response') from exc

    def stream(self, payload):
        deadline = time.monotonic() + self.timeout
        try:
            with self.request('/api/chat', payload) as response:
                for line in response:
                    # The socket timeout bounds idle reads, not a stream of keepalives.
                    if time.monotonic() >= deadline:
                        raise AgentError('Ollama stream exceeded its elapsed-time guard')
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError('Model event must be an object')
                    if 'error' in event:
                        raise AgentError(str(event['error']))
                    yield event
        except (OSError, HTTPException, ValueError, TypeError) as exc:
            raise AgentError('Malformed or interrupted Ollama stream: ' + str(exc)) from exc


ANSWER_NOW = ('You reached your output limit before giving an answer. Write your final answer now, from your '
              'reasoning so far: what you established, what remains open, and why. No further reasoning or tools.')
# After the tool-round limit the next call has no tools: it must report results, not plan more work.
RESULTS_ONLY = ('No further tools are available. Write your final answer now from the results above: what you '
                'established, the values your runs printed, and what remains open. Report results only; do not '
                'announce further checks, runs or reading.')
ANNOUNCED_ONLY = 'The final answer announced further work instead of reporting results.'


class Agent:
    def __init__(self, client, workspace, model='qwen3.8:27b', ctx=8192,
                 predict=4096, think=True, mode='prove', max_rounds=8,
                 seed=None, temperature=0.6, top_p=0.95):
        self.client, self.workspace = client, workspace
        self.model, self.ctx, self.predict = model, ctx, predict
        self.think, self.mode, self.max_rounds = think, mode, max_rounds
        self.seed, self.temperature, self.top_p = seed, temperature, top_p
        self.history = []
        self.last_stats = {}
        self.last_stop_reason = ''
        # Helpers sometimes share their parent's client. Preserve that task's
        # frozen visual context; newly constructed clients start empty.
        from . import images
        images.bind(self, copy.deepcopy(getattr(client, 'image_refs', [])))

    def run(self, query, emit=lambda kind, value: None, fresh=False, *, instruction=None, recover_empty_tool_answer=False):
        # Only commit history when a full turn completes; interruptions cannot
        # leave a dangling assistant tool call in the next API request.
        messages = copy.deepcopy([] if fresh else self.history)
        for message in messages:
            for key in ('thinking', 'reasoning', 'reasoning_content'):
                message.pop(key, None)
        self.last_stats = {}
        self.last_stop_reason = ''
        recovery_notes = ''
        message = {'role': 'user', 'content': query}
        if hasattr(self, 'turn_image_refs'):
            message['images'] = copy.deepcopy(self.turn_image_refs)
        messages.append(message)
        instruction = MODES[self.mode] if instruction is None else instruction
        system = {'role': 'system', 'content': SYSTEM + ('\nMODE: ' + instruction if instruction else '')}
        context = [{'role': 'user', 'content': value} for value in
                   (getattr(self, 'personal_context', ''), getattr(self, 'project_context', '')) if value]
        schemas = self.workspace.schemas()
        round_no, answer_now, forced_final = -1, False, False
        continued = 0
        while True:
            round_no += 1
            tools = schemas if round_no < self.max_rounds and not answer_now else []
            # A shared budget (an Assistant worker) shrinks every round's output to what is left of it.
            left = getattr(self.client, 'remaining_tokens', None)
            predict = self.predict if not isinstance(left, (int, float)) else int(min(self.predict, left))
            if predict < 128:
                self.last_stop_reason = ('Output limit reached while reasoning; no allowance remains for a final answer.'
                                         if answer_now else 'Generated-token budget exhausted before a final answer.')
                for message in messages:
                    message.pop('thinking', None)
                self.history = messages
                return next((m['content'] for m in reversed(messages) if m.get('role') == 'assistant' and m.get('content')), '')
            payload = {'model': self.model, 'messages': [system] + context + messages, 'stream': True,
                       'think': self.think and not answer_now, 'options': {'num_ctx': self.ctx, 'num_predict': predict,
                           'temperature': self.temperature, 'top_p': self.top_p}}
            if self.seed is not None:
                payload['options']['seed'] = (self.seed + round_no) % (2 ** 31)
            if tools:
                payload['tools'] = tools
            # The complete native request is counted, including tool schemas.
            # Context fitting adjusts output room, never mathematical premises.
            from .call_policy import allocate_call, CallContextError
            while True:
                payload['messages'] = [system] + context + messages
                if recovery_notes:
                    payload['messages'] = copy.deepcopy(payload['messages'])
                    payload['messages'][-1]['content'] += ('\n\nUNVERIFIED WORKING NOTES (final excerpt of generated reasoning):\n'
                                                          + recovery_notes)
                try:
                    payload, allocation = allocate_call(self.client, payload, predict)
                    break
                except CallContextError:
                    if not recovery_notes:
                        raise
                    # Only generated scratch notes may be omitted, never the task or source text.
                    recovery_notes = recovery_notes[len(recovery_notes) // 2:] if len(recovery_notes) > 256 else ''
            optional = getattr(self, 'optional_leads', '')
            if optional:
                extra = dict(payload, messages=[payload['messages'][0], {'role': 'user', 'content': optional},
                                                *payload['messages'][1:]])
                try:
                    extra, fit = allocate_call(self.client, extra, predict)
                    if extra['options']['num_predict'] >= payload['options']['num_predict']:
                        payload, allocation = extra, fit
                    else:
                        self.optional_leads = ''
                except CallContextError:
                    self.optional_leads = ''
            self.last_allocation = allocation
            emit('start', '')
            assistant = {'role': 'assistant', 'content': ''}
            calls, thinking, done = [], '', False
            from .deepinfra import generation_stream
            for event in generation_stream(self.client, payload, emit):
                msg = event.get('message', {})
                if msg.get('thinking'):
                    thinking += msg['thinking']
                    emit('thinking', msg['thinking'])
                if msg.get('content'):
                    assistant['content'] += msg['content']
                    emit('text', msg['content'])
                calls.extend(msg.get('tool_calls') or [])
                if event.get('done'):
                    self.last_stats = {k: event[k] for k in ('eval_count', 'eval_duration',
                        'prompt_eval_count', 'done_reason') if k in event}
                    done = True
            emit('end', '')
            if not done:
                raise AgentError('Model stream ended before its completion event. This turn was not saved.')
            if calls:
                assistant['tool_calls'] = calls
            messages.append(assistant)
            if self.last_stats.get('done_reason') == 'length':
                emit('notice', 'Generation hit its output limit; any displayed argument may be incomplete.')
            if calls:
                if not tools or len(calls) > 16:
                    raise AgentError('Tool budget exceeded; this turn was not saved. Previously approved actions remain applied.')
                for call in calls:
                    fn = call.get('function', {})
                    name = fn.get('name', '')
                    args = fn.get('arguments', {})
                    emit('tool', name + ' ' + json.dumps(args, ensure_ascii=False)[:240])
                    result = self.workspace.execute(name, args)
                    tool_message = {'role': 'tool', 'tool_name': name, 'content': result}
                    if call.get('id'):
                        tool_message['tool_call_id'] = call['id']
                    messages.append(tool_message)
                    emit('result', result[:200])
                if round_no + 1 == self.max_rounds:
                    emit('notice', 'Tool-round limit reached. Requesting a final answer without further tools.')
                    messages.append({'role': 'user', 'content': RESULTS_ONLY})
                    forced_final = True
                continue
            exhausted = self.last_stats.get('done_reason') == 'length'
            empty_after_tool = recover_empty_tool_answer and round_no > 0 and not exhausted
            note = getattr(self, 'continue_after_length', '')
            if (note and exhausted and not assistant['content'].strip() and not answer_now
                    and round_no + 1 < self.max_rounds and continued < getattr(self, 'continue_limit', 3)):
                # A tool loop (the Review's referees) whose round used its whole allowance thinking: the loop goes on
                # with its tools, given the end of that reasoning, instead of stopping with a final answer.
                continued += 1
                emit('notice', 'Output limit reached while reasoning; continuing with the tools.')
                messages.append({'role': 'user', 'content': note + '\n\nTHE END OF YOUR REASONING SO FAR (not kept '
                                 'otherwise):\n' + thinking[-getattr(self, 'continue_tail', input_limit(self, 'recovery_note', 4000)):]})
                continue
            if not assistant['content'].strip() and not answer_now and (exhausted or empty_after_tool):
                # The reasoning (or an unfinished tool call) used the whole allowance, or a worker wrote
                # nothing after its tool results: one more call, without reasoning or tools, asks for
                # the answer instead of returning nothing.
                answer_now = True
                recovery_notes = thinking[-input_limit(self, 'recovery_note', 4000):]
                if empty_after_tool:
                    emit('notice', 'The worker returned an empty answer after a tool call. Requesting its conclusion.')
                    prompt = 'Give your final mathematical answer using the work above. Identify any unresolved points.'
                else:
                    emit('notice', 'Output limit reached while reasoning; asking for the answer now.')
                    prompt = ANSWER_NOW
                messages.append({'role': 'user', 'content': prompt})
                continue
            if not assistant['content'].strip():
                # A worker's stop reason reaches the parent, which repeats it to the user: no controller terms.
                if answer_now and recover_empty_tool_answer:
                    self.last_stop_reason = 'No written conclusion was produced.'
                else:
                    self.last_stop_reason = ('Output limit reached while reasoning; no written conclusion was produced.'
                                             if answer_now else 'The model returned no final answer.')
                emit('notice', self.last_stop_reason)
            elif self.last_stats.get('done_reason') == 'length':
                self.last_stop_reason = 'Generation hit its output limit; the written answer is incomplete.'
            elif forced_final or answer_now:
                from .orchestrator import _ends_with_work_plan  # the orchestrator imports this module
                if _ends_with_work_plan(assistant['content']):
                    self.last_stop_reason = ANNOUNCED_ONLY
                    emit('notice', ANNOUNCED_ONLY)
            # Recovery notes belong only to that one request; scratch thinking is never kept in history.
            for message in messages:
                message.pop('thinking', None)
            self.history = messages
            return assistant['content']
