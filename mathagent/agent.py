# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Native Ollama HTTP client and bounded, backend-independent agent loop."""
import copy
import hashlib
import json
import re
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


_PAGED_READS = {'read_file', 'search_text', 'list_files', 'read_page', 'read_index', 'list_index', 'read_experiment',
                'read_skill_reference', 'execute_python'}


def _fit_read_history(messages, projected, seen):
    """Release already delivered reads first. Raw results remain in history and the original request stays exact."""
    for i, item in enumerate(messages[:seen]):
        if item.get('role') == 'tool' and item.get('tool_name') in _PAGED_READS:
            marker = '[Earlier reading omitted for context. Reopen the source using the preceding tool arguments.]'
            if len(projected.get(i, item.get('content', ''))) > len(marker):
                if item.get('tool_name') == 'execute_python':
                    # A sandbox run has side effects. Preserve the experiment ID for reading without a rerun.
                    try:
                        experiment = json.loads(item['content'])['experiment_id']
                    except (ValueError, KeyError, TypeError):
                        continue
                    marker = f'Experiment {experiment} was delivered earlier. Use read_experiment to reopen its record.'
                if projected.get(i) != marker:
                    projected[i] = marker
                    return True
    # Even one fresh reply can exceed the space left by other turns or a tool batch. Retain a
    # prefix with a cursor into the saved original. A new execution can be reopened by ID.
    names = {'read_file', 'read_experiment', 'execute_python', 'read_skill_reference'}
    candidates = [(len(projected.get(i, m.get('content', ''))), i) for i, m in enumerate(messages)
                  if i >= seen and m.get('role') == 'tool' and m.get('tool_name') in names]
    for size, i in sorted(candidates, reverse=True):
        source = messages[i]['content']
        if messages[i]['tool_name'] == 'read_file':
            excerpt = source[:size // 2]
            pos = excerpt.rfind('\n') + 1
            match = re.match(r'(?:\[PDF page \d+\] )?(\d+)(?: \(from column (\d+)\))?: ', excerpt[pos:])
            if not match:
                continue
            column = int(match[2] or 1) + len(excerpt[pos:]) - match.end()
            fitted = excerpt + f'\n[Continue read_file at start_line={match[1]}, start_column={column}.]'
        else:
            try:
                value = json.loads(projected.get(i, source))
                if messages[i]['tool_name'] == 'execute_python':
                    fitted = json.dumps({'experiment_id': value['experiment_id'], 'status': value['status'],
                                         'message': 'Open the saved output with read_experiment.'})
                elif isinstance(value.get('text'), str) and value['text']:
                    value['text'] = value['text'][:len(value['text']) // 2]
                    if not value['text']:
                        continue
                    value['next_offset'] = value['offset'] + len(value['text'])
                    fitted = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                elif messages[i]['tool_name'] == 'read_experiment' and value.get('id'):
                    fitted = json.dumps({'experiment_id': value['id'], 'message': 'Use read_experiment part record.'})
                else:
                    continue
            except (TypeError, KeyError, ValueError):
                continue
        if len(fitted) < size:
            projected[i] = fitted
            return True
    return False


class Agent:
    def __init__(self, client, workspace, model='qwen3.8:27b', ctx=8192,
                 predict=4096, think=True, mode='prove', max_rounds=None,
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

    @property
    def ctx(self):
        return self._ctx

    @ctx.setter
    def ctx(self, value):
        # The workspace tools size their results from the model's context (tools.result_budget).
        self._ctx = value
        if hasattr(self.workspace, 'result_chars'):
            from .tools import result_budget
            self.workspace.result_chars = result_budget(value)
            if getattr(self.workspace, 'numerics', None) is not None:
                self.workspace.numerics.result_chars = self.workspace.result_chars
            if getattr(self.workspace, 'literature', None) is not None:
                self.workspace.literature.result_chars = self.workspace.result_chars

    def _turn_messages(self, query, instruction):
        message = {'role': 'user', 'content': query}
        if hasattr(self, 'turn_image_refs'):
            message['images'] = copy.deepcopy(self.turn_image_refs)
        instruction = MODES[self.mode] if instruction is None else instruction
        system = {'role': 'system', 'content': SYSTEM + ('\nMODE: ' + instruction if instruction else '')}
        context = [{'role': 'user', 'content': value} for value in
                   (getattr(self, 'personal_context', ''), getattr(self, 'project_context', '')) if value]
        return system, context, message

    def source_fits(self, query, *, tools, minimum=128, history=()):
        """Fit the actual fresh-turn request, including schemas, images and the current shared input allowance."""
        from .call_policy import allocate_call, CallContextError
        system, context, message = self._turn_messages(query, None)
        desired = int(min(self.predict, getattr(self.client, 'remaining_tokens', self.predict)))
        if desired < minimum:
            return False
        prior = [{key: value for key, value in row.items() if key not in ('thinking', 'reasoning', 'reasoning_content')} for row in history]
        payload = self._payload([system, *context, *prior, message], self.workspace.schemas() if tools else [],
                                self.think, desired, 0)
        try:
            _, fit = allocate_call(self.client, payload, desired, minimum=minimum)
        except CallContextError:
            return False
        return fit['input_tokens'] <= getattr(self.client, 'remaining_input_tokens', float('inf'))

    def _payload(self, messages, tools, think, predict, round_no):
        payload = {'model': self.model, 'messages': messages, 'stream': True, 'think': think,
                   'options': {'num_ctx': self.ctx, 'num_predict': predict,
                               'temperature': self.temperature, 'top_p': self.top_p}}
        if self.seed is not None:
            payload['options']['seed'] = (self.seed + round_no) % (2 ** 31)
        if tools:
            payload['tools'] = tools
        return payload

    def run(self, query, emit=lambda kind, value: None, fresh=False, *, instruction=None, recover_empty_tool_answer=False, on_first_response=None):
        # Only commit history when a full turn completes; interruptions cannot
        # leave a dangling assistant tool call in the next API request.
        messages = copy.deepcopy([] if fresh else self.history)
        for message in messages:
            for key in ('thinking', 'reasoning', 'reasoning_content'):
                message.pop(key, None)
        self.last_stats = {}
        self.last_stop_reason = ''
        recovery_notes = ''
        system, context, message = self._turn_messages(query, instruction)
        messages.append(message)
        schemas = self.workspace.schemas()
        seen_tools, stalled_rounds = set(), 0
        projected, seen_messages = {}, len(messages) - 1
        round_no, answer_now, forced_final = -1, False, False
        continued = 0
        while True:
            round_no += 1
            open_ended = self.max_rounds is None
            tools = schemas if (open_ended or round_no < self.max_rounds) and not (answer_now or forced_final) else []
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
            payload = self._payload([system, *context, *messages], tools, self.think and not answer_now, predict, round_no)
            # The complete native request is counted, including tool schemas.
            # Context fitting adjusts output room, never mathematical premises.
            from .call_policy import allocate_call, CallContextError
            while True:
                visible = [dict(item, content=projected[i]) if i in projected else item for i, item in enumerate(messages)]
                payload['messages'] = [system, *context, *visible]
                if recovery_notes:
                    payload['messages'] = copy.deepcopy(payload['messages'])
                    payload['messages'][-1]['content'] += ('\n\nUNVERIFIED WORKING NOTES (final excerpt of generated reasoning):\n'
                                                          + recovery_notes)
                try:
                    payload, allocation = allocate_call(self.client, payload, predict)
                    if allocation['input_tokens'] > getattr(self.client, 'remaining_input_tokens', float('inf')):
                        raise CallContextError('The remaining input allowance does not cover this request.', allocation)
                    break
                except CallContextError:
                    sandbox = getattr(self.workspace, 'numerics', None)
                    if tools and getattr(sandbox, 'invocation', None) == 'automatic':
                        essential = [t for t in tools if t['function']['name'] not in sandbox.tool_names]
                        if len(essential) < len(tools):
                            schemas = tools = essential
                            if tools:
                                payload['tools'] = tools
                            else:
                                payload.pop('tools', None)
                            continue
                    if not recovery_notes:
                        if (self.mode in ('free', 'explore', 'python_lab', 'sanity_check') and
                                _fit_read_history(messages, projected, seen_messages)):
                            continue
                        raise
                    # Only generated scratch notes may be omitted, never the task or source text.
                    recovery_notes = recovery_notes[len(recovery_notes) // 2:] if len(recovery_notes) > 256 else ''
            optional = getattr(self, 'optional_leads', '')
            if optional:
                extra = dict(payload, messages=[payload['messages'][0], {'role': 'user', 'content': optional},
                                                *payload['messages'][1:]])
                try:
                    extra, fit = allocate_call(self.client, extra, predict)
                    if (extra['options']['num_predict'] >= payload['options']['num_predict']
                            and fit['input_tokens'] <= getattr(self.client, 'remaining_input_tokens', float('inf'))):
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
            seen_messages = len(messages)
            if on_first_response is not None:
                on_first_response()
                on_first_response = None
            if calls:
                assistant['tool_calls'] = calls
            messages.append(assistant)
            if self.last_stats.get('done_reason') == 'length':
                emit('notice', 'Generation hit its output limit; any displayed argument may be incomplete.')
            if calls:
                if not tools:
                    raise AgentError('Tool budget exceeded; this turn was not saved. Previously approved actions remain applied.')
                progress = False
                for call in calls:
                    fn = call.get('function', {})
                    name = fn.get('name', '')
                    args = fn.get('arguments', {})
                    emit('tool', name + ' ' + json.dumps(args, ensure_ascii=False)[:240])
                    result = self.workspace.execute(name, args)
                    if open_ended and not result.startswith('Tool error'):
                        signature = hashlib.sha256(json.dumps([name, args, result], sort_keys=True).encode()).digest()
                        progress |= signature not in seen_tools
                        seen_tools.add(signature)
                    tool_message = {'role': 'tool', 'tool_name': name, 'content': result}
                    if call.get('id'):
                        tool_message['tool_call_id'] = call['id']
                    messages.append(tool_message)
                    emit('result', result[:200])
                stalled_rounds = 0 if progress else stalled_rounds + 1
                if open_ended and stalled_rounds >= 3:
                    emit('notice', 'The last three tool rounds repeated earlier work or failed. Requesting the findings.')
                    messages.append({'role': 'user', 'content': RESULTS_ONLY})
                    forced_final = True
                elif round_no + 1 == self.max_rounds:
                    emit('notice', 'Tool-round limit reached. Requesting a final answer without further tools.')
                    messages.append({'role': 'user', 'content': RESULTS_ONLY})
                    forced_final = True
                continue
            exhausted = self.last_stats.get('done_reason') == 'length'
            empty_after_tool = recover_empty_tool_answer and round_no > 0 and not exhausted
            note = getattr(self, 'continue_after_length', '')
            if (note and exhausted and not assistant['content'].strip() and not answer_now
                    and (open_ended or round_no + 1 < self.max_rounds) and continued < getattr(self, 'continue_limit', 3)):
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
