# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Durable evidence calls within one solver or verifier attempt."""
import json
import math
from contextlib import contextmanager
import time

from .agent import AgentError
from .backends import _arguments
from .call_policy import CallContextError, MIN_PROOF_REVIEW_TOKENS
from .ledger import LedgerError
from .proof_policy import VERIFIER_SCHEMA
from .tools import result_budget


def review_evidence(items):
    """Render contained, complete passages once, preserving each citation and its original metadata."""
    result = list(items)
    pages = []
    for index, item in enumerate(items):
        if ('passage_in' in item or item.get('kind') != 'passage' or item.get('lines_complete') is not True
                or item.get('start_column') != 1):
            continue
        start, end = item.get('start_line'), item.get('end_line')
        source = tuple(item.get(key) for key in ('document_id', 'text_sha256', 'source_url'))
        if (type(start) is not int or type(end) is not int or not 1 <= start <= end
                or not all(isinstance(v, str) and v for v in source)
                or item.get('citation') != f'{source[0]}:L{start}-L{end}' or not isinstance(item.get('passage'), str)):
            continue
        lines = item['passage'].split('\n')
        if len(lines) == end - start + 1:
            pages.append((index, source, start, lines))
    # Larger ranges first, so every pointer refers directly to an excerpt printed in full.
    retained = {}
    for index, source, start, lines in sorted(pages, key=lambda page: -len(page[3])):
        for outer_start, outer_lines, outer_index in retained.get(source, ()):
            offset = start - outer_start
            if offset >= 0 and outer_lines[offset:offset + len(lines)] == lines:
                link = {'record_number': outer_index + 1, 'citation': items[outer_index]['citation']}
                if len(json.dumps({'passage_in': link}, ensure_ascii=False)) < len(items[index]['passage']):
                    result[index] = {key: value for key, value in items[index].items() if key != 'passage'}
                    result[index]['passage_in'] = link
                break
        else:
            retained.setdefault(source, []).append((start, lines, index))
    return result


def _function(name, description, parameters):
    return {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': parameters}}


REQUEST = _function('request_evidence', 'Ask the coordinator for a focused reference check or a broader literature '
    'review. State a self-contained public mathematical question with its hypotheses, without quoting private text.',
    {'type': 'object', 'additionalProperties': False, 'properties': {
        'kind': {'type': 'string', 'enum': ['check', 'literature']}, 'question': {'type': 'string'}},
     'required': ['kind', 'question']})
SUBMIT = _function('submit_review', 'Finish your independent review of this candidate. Submit this alone.', VERIFIER_SCHEMA)
READ = _function('read_proof_evidence', 'Page your saved evidence by ID, or use id=index for the available records.',
                 {'type': 'object', 'properties': {'id': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0},
                  'chars': {'type': 'integer', 'minimum': 1}}, 'required': ['id']})


class _Deadline:
    def __init__(self, deadline, cancel):
        self.deadline, self.cancel = deadline, cancel

    def is_set(self):
        return time.monotonic() >= self.deadline or bool(self.cancel and self.cancel.is_set())

    def wait(self, timeout):
        delay = min(timeout, max(0, self.deadline - time.monotonic()))
        if self.cancel:
            self.cancel.wait(delay)
        else:
            time.sleep(delay)
        return self.is_set()


class ProofTurns:
    def __init__(self, runner, desk, coordinator):
        self.runner, self.desk, self.coordinator = runner, desk, coordinator
        self.names = set()

    def configure(self, settings):
        self.names = set(settings.get('evidence_tools', []))
        if 'execute_python' in self.names:
            self.desk.bind_numerics(settings.get('sandbox_max_runs'))
            if self.desk.numerics is not None and self.desk.numerics.run_limit is not None:
                settings['sandbox_max_runs'] = self.desk.numerics.run_limit

    @contextmanager
    def meter(self):
        library = self.desk.literature if self.names else None
        sandbox = self.desk.numerics if 'execute_python' in self.names else None
        if library is not None:
            old_change = library.on_budget_change
            def record_literature():
                if old_change:
                    old_change()
                self.state['evidence_literature'] = library.snapshot()
                self.runner._save()
            library.on_budget_change = record_literature
            self.state['evidence_literature'] = library.snapshot()
        if sandbox is not None:
            old_run_change = sandbox.on_run_change
            sandbox.runs = max(sandbox.runs, self.state.get('evidence_sandbox_runs', 0))
            def record_run(count):
                if old_run_change:
                    old_run_change(count)
                self.state['evidence_sandbox_runs'] = count
                self.runner._save()
            sandbox.on_run_change = record_run
        try:
            yield
        finally:
            if library is not None:
                library.on_budget_change = old_change
            if sandbox is not None:
                sandbox.on_run_change = old_run_change

    def available(self):
        return [*self.desk.schemas(), *([REQUEST] if self.coordinator and self.desk.assigned_documents is None else [])]

    def schemas(self, role):
        schemas = [s for s in self.available() if s['function']['name'] in self.names]
        if self.runner.store and (self.state.get('pending') or {}).get(role + '_indexed'):
            schemas.append(READ)
        return [*schemas, SUBMIT] if schemas and role == 'verifier' else schemas

    @property
    def state(self):
        return self.runner.state

    def read(self, filename):
        return self.runner.store.read_artifact(filename)

    def primary(self, attempts):
        items = {}
        for entry in self.state.get('evidence', []):
            if entry['attempt'] in attempts and entry['role'] == 'solver' and entry.get('primary'):
                for item in json.loads(self.read(entry['primary'])):
                    items[json.dumps(item, sort_keys=True)] = item
        return list(items.values())

    def lineage(self, candidate):
        attempts = set()
        while candidate is not None:
            attempts.add(candidate['attempt'])
            parent = candidate['parent']
            candidate = self.runner._get_candidate(parent) if parent else None
        return attempts

    def visible(self, role):
        pending = self.state['pending']
        inherited = self.lineage(self.runner._get_candidate(pending['candidate'])) if role == 'verifier' else set()
        records = {}
        for e in self.state.get('evidence', []):
            if e.get('status') != 'complete':
                continue
            if e['role'] == role and e['attempt'] == pending['index']:
                records[e['id']] = (e['name'], e['result'])
            elif e['role'] == 'solver' and e['attempt'] in inherited and e.get('primary'):
                records[e['id']] = ('primary evidence', e['primary'])
        return records

    def page(self, role, args):
        records = self.visible(role)
        target, offset = args.get('id'), args.get('offset', 0)
        room = result_budget(self.runner.agent.ctx)
        chars = args.get('chars', room)
        if not isinstance(target, str) or type(offset) is not int or offset < 0 or type(chars) is not int or chars < 1:
            return 'Provide an evidence id, a nonnegative offset and a positive number of characters.'
        if target == 'index':
            content = '\n'.join(f'{key} {name}' for key, (name, _) in records.items() if name != 'read_proof_evidence')
        elif target in records:
            content = self.read(records[target][1])
        else:
            return 'Unknown evidence id for this role and candidate.'
        end = min(len(content), offset + min(chars, room))
        return json.dumps({'id': target, 'offset': offset, 'next_offset': end if end < len(content) else None,
                           'content': content[offset:end]}, ensure_ascii=False)

    def fit_indexed(self, role, choices, allocate):
        """Fit a marked prefix of the latest visible record beside the mathematical input and role output."""
        records = self.visible(role)
        if not records:
            return allocate(next(choices))
        last_id = next(reversed(records))
        name, artifact = records[last_id]
        text = self.read(artifact)
        input_left = getattr(self.runner.agent.client, 'remaining_input_tokens', math.inf)
        for payload in choices:
            offered = {s['function']['name'] for s in payload.get('tools', [])}
            note = f'Evidence history exceeded the context. Latest record {last_id} ({name}). '
            if len(records) > 1:
                note += 'Earlier records are omitted. '
            if 'read_proof_evidence' in offered:
                note += 'Use read_proof_evidence with id=index or an evidence ID and offset for further pages. '
                if 'read_paper' in offered:
                    note += 'Use read_paper for the text at a cited document locator. '
                else:
                    note += 'A source locator alone does not establish its contents. '
            else:
                note += 'Further evidence reads are unavailable in this response. '
            note += ('Report any essential gap you cannot resolve.' if role == 'solver' else
                     'Record uncertainty for any essential dependency you cannot independently check.')

            def with_page(size):
                label = 'Complete latest record:' if size == len(text) else f'First {size} of {len(text)} characters:'
                msg = {'role': 'user', 'content': f'{note}\n{label}\n' + text[:size]}
                return allocate({**payload, 'messages': [*payload['messages'], msg]})

            try:
                best = with_page(0)
            except CallContextError:
                if offered:
                    continue
                raise
            # Keep the actual output allowance this request has before adding the excerpt.
            # A smaller reader schema can also rescue a request close to its shared input limit.
            if best[1]['input_tokens'] > input_left and offered:
                continue
            low, high = 0, len(text)
            while low < high:
                size = high if high == len(text) else (low + high + 1) // 2
                try:
                    attempt = with_page(size)
                except CallContextError:
                    attempt = None
                if (attempt and attempt[1]['output_tokens'] >= best[1]['output_tokens']
                        and attempt[1]['input_tokens'] <= input_left):
                    low, best = size, attempt
                else:
                    high = size - 1
            return best

    def history(self, role):
        history = []
        pending = self.state['pending']
        for key in pending.get(role + '_turns', []):
            result = self.runner._read_stream(self.runner._find_call(key)['stream'])
            message = {'role': 'assistant', 'content': result['text'], 'tool_calls': result['calls']}
            if result['thinking']:
                message['thinking'] = result['thinking']
            history.append(message)
            for entry in self.state['evidence']:
                if entry['call'] == key:
                    call = result['calls'][entry['ordinal']]
                    reply = {'role': 'tool', 'tool_name': entry['name'], 'content': self.read(entry['result'])}
                    if call.get('id'):
                        reply['tool_call_id'] = call['id']
                    history.append(reply)
        return history

    def remaining(self, role):
        settings = self.state['settings']
        minimum = MIN_PROOF_REVIEW_TOKENS
        if role == 'solver':
            initial = self.state['pending']['kind'] in {'initial', 'retry'}
            minimum += settings['initial_min_solve_tokens' if initial else 'min_solve_tokens']
        return max(0, self.runner._remaining() - minimum)

    def final_allowance(self, role):
        reserve = self.runner._call_cap(self.state['settings']['verify_tokens'])
        if role == 'solver':
            reserve += self.runner._call_cap(self.runner._attempt_cap(self.state['pending']['kind']))
        return reserve

    def available_for_turn(self, role):
        # Every new role can investigate, including when the whole job starts with a short allowance.
        # Subsequent optional calls spend only the surplus over the ordinary final role output(s).
        if self.state['pending'].get(role + '_turns'):
            return max(0, self.runner._remaining() - self.final_allowance(role))
        # The first generation may be the finished answer. Its existing role allocation already fits the
        # remaining job, and the solver allocation already reserves the whole-candidate verifier.
        return self.runner._remaining()

    def resource_note(self, role, *, tools):
        client = self.runner.agent.client
        resources = {'shared output tokens': self.runner._remaining(),
                     'active seconds': min(self.state['settings']['max_seconds'] - self.runner._elapsed(),
                                           getattr(client, 'remaining_seconds', math.inf)),
                     'shared input tokens': getattr(client, 'remaining_input_tokens', math.inf)}
        available = ', '.join(f'{max(0, int(n))} {key}' for key, n in resources.items() if math.isfinite(n))
        if tools:
            action = 'Finish as soon as the evidence settles the question, or identify the remaining gap.'
        else:
            action = ('Use the evidence already gathered to give the finished proof, refutation or remaining gap.'
                      if role == 'solver' else 'Give the required review JSON from the evidence already gathered.')
        return 'Remaining in this job: ' + available + '. ' + action

    def assistance_remaining(self, role):
        """Fit the support job beside the requested unfinished solve/review outputs under the shared allowance."""
        left = self.runner._remaining()
        if not math.isfinite(left):
            return left
        reserve = self.final_allowance(role)
        help_output = self.runner._find_call(self.runner._call_key(role))['reserved_tokens']
        share = int(left) * help_output // (reserve + help_output)
        return max(0, min(self.remaining(role), max(int(left) - reserve, share)))

    def _reply(self, entry, result, primary=()):
        store = self.runner.store
        entry['result'] = store.write_artifact('evidence-result', result)
        if primary:
            entry['primary'] = store.write_artifact('primary-evidence', json.dumps(primary, ensure_ascii=False))
            if len(primary) == 1 and primary[0].get('kind') == 'experiment':
                entry['job_link'] = ['experiments', primary[0]['experiment_id']]
        entry['status'] = 'complete'
        self.runner._save()

    def _request(self, entry, arguments):
        kind, question = arguments.get('kind'), arguments.get('question')
        if kind not in ('check', 'literature') or not isinstance(question, str) or not question.strip():
            self._reply(entry, 'Provide kind (check or literature) and the precise mathematical question.')
            return
        question = question.strip()
        for prior in self.state['evidence']:
            request = prior.get('request', {})
            if request.get('kind') == kind and request.get('question') == question and prior.get('receipt'):
                primary = json.loads(self.read(prior['primary'])) if prior.get('primary') else []
                result = self.read(prior['result']) if entry['role'] == prior['role'] else json.dumps({
                    'prior_evidence_request': prior['id'], 'primary_sources': primary}, ensure_ascii=False)
                entry['reuses'] = prior['id']
                self._reply(entry, result, primary)
                return
        remaining = self.assistance_remaining(entry['role'])
        seconds = min(self.state['settings']['max_seconds'] - self.runner._elapsed(),
                      getattr(self.runner.agent.client, 'remaining_seconds', math.inf))
        if remaining <= 0 or seconds <= 0:
            self._reply(entry, 'No remaining allowance for assistance. Use the available evidence or report the gap.')
            return
        request = {'id': f'{self.state["id"]}:{entry["id"]}', 'kind': kind, 'question': question, 'role': entry['role'],
                   'candidate': entry['candidate'], 'remaining_tokens': int(remaining) if math.isfinite(remaining) else None,
                   'remaining_seconds': seconds}
        if entry['candidate']:
            request['candidate_sha256'] = self.runner._get_candidate(entry['candidate'])['sha256']
        shared_input = getattr(self.runner.agent.client, 'remaining_input_tokens', math.inf)
        if math.isfinite(shared_input):
            last = self.runner._find_call(entry['call'])
            reserve = last['context']['input_tokens'] + last.get('charged_tokens', last['reserved_tokens'])
            if entry['role'] == 'solver':
                reserve += (self.state['settings'].get('empty_review_input_tokens', 0) +
                            self.runner._attempt_cap(self.state['pending']['kind']))
            request['remaining_input_tokens'] = max(0, int(shared_input - reserve))
        library = self.desk.literature
        if library is not None:
            request.update(remaining_requests=max(0, library.max_requests-library.stats['requests']),
                           remaining_chars=max(0, library.max_chars-library.stats['returned_chars']))
        entry.update(status='waiting', request=request)
        self.state['evidence_request'] = request
        self.runner._stop('waiting_for_evidence', f'{entry["role"].capitalize()} requested {kind}: {question}')
        self.runner._save()

    def accept(self, receipt):
        if not isinstance(receipt, dict) or 'result' not in receipt:
            raise ValueError('Supply an evidence receipt with its request id and result.')
        entry = next((e for e in self.state.get('evidence', []) if e.get('request', {}).get('id') == receipt.get('id')), None)
        if entry is None or 'request' not in entry:
            raise ValueError('Evidence does not belong to a saved request in this proof.')
        encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if entry.get('receipt'):
            if self.read(entry['receipt']) != encoded:
                raise ValueError('A different reply has already been recorded for this evidence request.')
            return
        if entry['status'] != 'waiting' or self.state.get('evidence_request') != entry['request']:
            raise LedgerError('The evidence request is no longer pending.')
        tokens, seconds = receipt.get('tokens', 0), receipt.get('seconds', 0)
        measured = receipt.get('measured_tokens', 0)
        requests, chars = receipt.get('requests', 0), receipt.get('returned_chars', 0)
        if (any(type(x) is not int or x < 0 for x in (tokens, measured, requests, chars)) or measured > tokens or
                type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0):
            raise ValueError('Invalid evidence usage.')
        primary = receipt.get('primary_sources', [])
        if not isinstance(primary, list) or any(not isinstance(s, dict) for s in primary):
            raise ValueError('Invalid primary evidence sources.')
        # The original shared client has already settled its charges. This is the native Proof ledger.
        entry['receipt'] = self.runner.store.write_artifact('assistance-receipt', encoded)
        value = receipt['result']
        if isinstance(value, dict) and isinstance(value.get('job_id'), str) and value['job_id']:
            entry['job_link'] = ['checks' if entry['request']['kind'] == 'check' else 'research', value['job_id']]
        entry['measured_tokens'] = measured
        self.state['tokens_charged'] += tokens
        self.state['seconds_used'] += seconds
        snapshot = self.state.get('evidence_literature')
        if snapshot:
            snapshot['stats']['requests'] += requests
            snapshot['stats']['returned_chars'] += chars
        del self.state['evidence_request']
        self.runner._stop('paused', 'Evidence received. Continuing the requesting role.')
        self._reply(entry, json.dumps({'result': receipt['result'], 'primary_sources': primary}, ensure_ascii=False), primary)

    def process(self, role, result):
        """Commit each tool result before emitting the next role generation. Return true for an intermediate turn."""
        if not result['calls'] or not result['done'] or result['stats'].get('done_reason') not in ('tool_calls', 'stop'):
            return False
        key = self.runner._call_key(role)
        dispatched = self.runner._find_call(key)
        payload = json.loads(self.read(dispatched['request']))
        offered = {s['function']['name'] for s in payload.get('tools', [])}
        if not offered:
            return False
        calls = result['calls']
        # A malformed or duplicate native call is a protocol failure. Do not execute a prefix of the batch.
        if any(not isinstance(c, dict) or not isinstance(c.get('function'), dict) or
               not isinstance(c['function'].get('name'), str) or not c['function']['name'] for c in calls):
            return False
        ids = [c['id'] for c in calls if 'id' in c]
        if any(not isinstance(cid, str) or not cid for cid in ids) or len(set(ids)) != len(ids):
            return False
        try:
            arguments = [_arguments(c['function'].get('arguments', {})) for c in calls]
        except AgentError:
            return False
        if len(calls) == 1 and calls[0]['function']['name'] == 'submit_review' and 'submit_review' in offered:
            result.update(text=json.dumps(arguments[0], ensure_ascii=False), calls=[], complete=True)
            return False
        pending = self.state['pending']
        records = self.state.setdefault('evidence', [])
        for ordinal, call in enumerate(calls):
            fn = call['function']
            name, args = fn['name'], arguments[ordinal]
            entry = next((e for e in records if e['call'] == key and e['ordinal'] == ordinal), None)
            recovering = entry is not None
            if recovering and entry['status'] == 'complete':
                continue
            if not recovering:
                entry = {'id': f'E{len(records) + 1}', 'attempt': pending['index'], 'role': role,
                         'candidate': pending.get('candidate') or pending.get('parent'), 'name': name,
                         'ordinal': ordinal, 'call': key, 'status': 'started'}
                if name == 'execute_python':
                    entry['execution_id'] = self.desk.new_receipt_id()
                records.append(entry)
                self.runner._save()
            live = {s['function']['name'] for s in self.schemas(role)}
            if name not in offered or (name not in live and not (recovering and name == 'execute_python')):
                self._reply(entry, 'This tool is unavailable for this proof.')
            elif name == 'submit_review':
                self._reply(entry, 'Submit the final review by itself once all evidence is available.')
            elif name == 'request_evidence':
                self._request(entry, args)
                if self.state['status'] == 'waiting_for_evidence':
                    return True
            elif name == 'read_proof_evidence':
                self._reply(entry, self.page(role, args))
            else:
                self.runner.emit('tool', f'{role}: {name} ({entry["id"]})')
                run_id = entry.get('execution_id')
                if recovering:
                    reply = self.desk.recover(name, args, run_id)
                    if reply is None:
                        reply = json.dumps({'error': 'Interrupted before a result was recorded. Outcome unknown.'})
                else:
                    # Do not let a series of reads or a sandbox run continue beyond the job's active time.
                    remaining = min(self.state['settings']['max_seconds'] - self.runner._elapsed(),
                                    getattr(self.runner.agent.client, 'remaining_seconds', math.inf))
                    if remaining <= 0:
                        reply = json.dumps({'error': 'Proof time allowance exhausted before tool dispatch.'})
                    else:
                        deadline = time.monotonic() + remaining
                        library, sandbox = self.desk.literature, self.desk.numerics
                        if library is not None:
                            old_deadline = library.deadline
                            library.deadline = min(deadline, old_deadline if old_deadline is not None else math.inf)
                        if sandbox is not None:
                            old_cancel = sandbox.cancel
                            sandbox.cancel = _Deadline(deadline, old_cancel)
                        try:
                            reply = self.desk.execute(name, args, receipt_id=run_id)
                        finally:
                            if library is not None:
                                library.deadline = old_deadline
                            if sandbox is not None:
                                sandbox.cancel = old_cancel
                primary = self.desk.primary_evidence(name, args, reply)
                self._reply(entry, reply, [primary] if primary else [])
                self.runner.emit('result', f'{role}: {name} saved ({entry["id"]})')
        pending.setdefault(role + '_turns', []).append(key)
        self.runner._save()
        return True
