# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Service a suspended Proof using its coordinator's evidence and remaining resources."""
import hashlib
import math
import os
import re
import uuid

from . import briefs, images, litreview, project_index, refcheck
from .agent import Agent
from .ledger import ProofStore
from .literature import LiteratureTools
from .research import _job_directory, _read_state
from .reporting import job_output_budget
from .review_position import copied_phrases
from .tools import Workspace


class _SupportLibrary(LiteratureTools):
    """One research ledger's counters with the enclosing job's live online permission."""

    def __init__(self, parent, request):
        self.parent = parent
        self.request_ceiling = min(request.get('remaining_requests', parent.max_requests),
                                   max(0, parent.max_requests - parent.stats['requests']))
        self.char_ceiling = min(request.get('remaining_chars', parent.max_chars),
                                max(0, parent.max_chars - parent.stats['returned_chars']))
        super().__init__(parent.root, online=parent.online, timeout=parent.timeout)
        self.cancelled = parent.cancelled
        self.result_chars = parent.result_chars

    def reset_budget(self, *, max_requests, max_chars):
        # Native research restores its own saved limits. A narrower enclosing allowance still applies.
        super().reset_budget(max_requests=min(max_requests, self.request_ceiling), max_chars=min(max_chars, self.char_ceiling))

    @property
    def online(self):
        return self._online and self.parent.online

    @online.setter
    def online(self, allowed):
        self._online = bool(allowed)


def _passages(library, entries, room):
    """Return only exact primary passages that can be resolved from their cached extraction."""
    passages, seen = [], set()
    for entry in entries:
        for document in entry.get('documents', []):
            doc_id = document['document_id']
            try:
                metadata, lines = library._document(doc_id)
            except (OSError, ValueError):
                continue
            if document.get('text_sha256') != metadata['text_sha256']:
                continue
            for passage in document.get('passages', []):
                locator = passage.get('range', '')
                match = re.fullmatch(r'L([1-9]\d*)-L([1-9]\d*)', locator)
                if not match or (doc_id, locator) in seen:
                    continue
                start, end = map(int, match.groups())
                if not 1 <= start <= end <= len(lines):
                    continue
                seen.add((doc_id, locator))
                text = '\n'.join(lines[start - 1:end])
                row = {'document_id': doc_id, 'locator': locator, 'source_url': metadata['source_url'],
                       'text_sha256': metadata['text_sha256'], 'sha256': hashlib.sha256(text.encode()).hexdigest()}
                # Retain every locator. Large excerpts can be reopened and paged with read_paper.
                if len(text) <= room:
                    row['content'] = text
                    room -= len(text)
                passages.append(row)
    return passages


def _prior_entries(view, question):
    named = set(briefs.named_entries(view, question))
    return [entry for entry in (view or {}).get('entries', [])
            if entry.get('documents') and (entry['id'] in named or
               entry.get('task', {}).get('text', '').strip() == question.strip())]


def _evidence(runner, request, view):
    proof_id = runner.state['id']
    question, kind = request['question'], request['kind']
    desk = runner.turns.desk
    library = desk.literature
    receipt = {'id': request['id'], 'tokens': 0, 'seconds': 0}
    if library is None:
        return {**receipt, 'result': {'status': 'unavailable', 'reason': 'No paper library is available.'}}
    native_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'square-harness:proof:{proof_id}:{request["id"]}'))
    factory, export = ((refcheck.CheckRunner, refcheck.export_evidence) if kind == 'check' else
                       (litreview.ReviewRunner, litreview.export_evidence))
    folder = factory.FOLDER
    saved = os.path.lexists(library.root / '.mathagent' / folder / native_id)
    if not saved:
        entries = _prior_entries(view, question)
        primary = _passages(library, entries, runner.agent.workspace.result_chars)
        if primary:
            return {**receipt, 'result': {'status': 'source_read', 'entry_ids': [e['id'] for e in entries]},
                    'primary_sources': primary}
        if request['remaining_seconds'] <= 0 or request['remaining_tokens'] == 0:
            return {**receipt, 'result': {'status': 'unavailable', 'reason': 'The remaining proof allowance is reserved.'}}

    # The child sees only the self-contained public question. It shares the enclosing inference
    # client, including admission, cancellation, input/output accounting and the provider profile.
    child_library = _SupportLibrary(library, request)
    child_library.online = (desk.online_allowed and runner.state['settings']['evidence_online']
                            and runner.state.get('evidence_literature', {}).get('online', True) and library.online)
    if not saved and child_library.online and any(copied_phrases({'question': question}, text) for text in desk.private_texts):
        return {**receipt, 'result': {'status': 'unavailable', 'reason':
                'The question repeats private source text. Ask again in public mathematical terms.'}}
    workspace = Workspace(library.root, literature=child_library, read_types=())
    agent = Agent(runner.agent.client, workspace, runner.agent.model, runner.agent.ctx, runner.agent.predict,
                  runner.agent.think, 'check' if kind == 'check' else 'literature', seed=runner.agent.seed)
    call_cap = request.get('call_output_cap')
    if call_cap is None:
        # An already suspended proof has the fitted cap on the request's original role call.
        entry = next(e for e in runner.state['evidence'] if e.get('request') == request)
        call_cap = runner._find_call(entry['call'])['reserved_tokens']
    explicit = getattr(runner.agent, 'document_output_cap', None)
    agent.document_output_cap = min(call_cap, explicit) if explicit is not None else call_cap
    images.bind(agent, [])
    if kind == 'literature':
        # Both HTTP lanes must see cancellations and a user switching the parent offline.
        child = factory(agent, runner.emit, shared_source_budget=True)
        child.cancel = child_library.cancelled
    else:
        child = factory(agent, runner.emit)
    runner.emit('notice', f'Proof {proof_id}: {request["role"]} requested {kind} evidence ({native_id}).')
    if saved:
        state = _read_state(_job_directory(library.root, native_id, folder), native_id)
        if state['goal'] != question.strip() or state['kind'] != kind:
            raise ValueError('The saved evidence job belongs to a different request.')
        child.resume(native_id, caller_output_cap=agent.document_output_cap)
    else:
        tokens = min(runner.turns.assistance_remaining(request['role']),
                     request['remaining_tokens'] if request['remaining_tokens'] is not None else math.inf)
        seconds = min(request['remaining_seconds'], getattr(agent.client, 'remaining_seconds', math.inf))
        input_allowance = request.get('remaining_input_tokens')
        remaining_input = min(getattr(agent.client, 'remaining_input_tokens', math.inf),
                              input_allowance if input_allowance is not None else math.inf)
        budgets = dict(job_id=native_id, max_tokens=int(tokens) if math.isfinite(tokens) else None, max_seconds=seconds,
                       max_input_tokens=int(remaining_input) if math.isfinite(remaining_input) else None,
                       max_requests=child_library.max_requests, max_chars=child_library.max_chars)
        try:
            if kind == 'check':
                child.start(question, asker='proof', lookup='precise', **budgets)
            else:
                # The public question cannot authorize loading a local file mentioned by name.
                child.start_companion(question, **budgets)
        except ValueError as exc:
            if child.state is not None:
                raise
            return {**receipt, 'result': {'status': 'unavailable', 'reason': str(exc)}}
    state = child.state
    if state['status'] == 'paused':
        # The native job has checkpointed its usage and pending calls. The proof stays at the
        # request. Resuming it after cancellation or a provider interruption finds the same child.
        return {'paused': state.get('stop_reason') or 'Evidence collection paused.', 'job_id': native_id}
    rows = export(state)
    project_index.freeze_documents(library.root, {'entries': rows})
    primary = _passages(child_library, rows, runner.agent.workspace.result_chars)
    stats = (state.get('literature_snapshot') or {}).get('stats', {})
    result = {'job_id': native_id, 'kind': kind, 'status': state['status'], 'stop_reason': state.get('stop_reason'),
              'sources': [{'status': row['status'], 'reference': row['summary']} for row in rows],
              'note': 'The attached primary passages can be checked directly. Bibliographic records and citing '
                      'sources alone cannot establish the requested theorem.'}
    return {**receipt, 'tokens': state['tokens_charged'], 'measured_tokens': job_output_budget(state, False)['used'],
            'seconds': state['seconds_used'],
            'requests': stats.get('requests', 0), 'returned_chars': stats.get('returned_chars', 0),
            'primary_sources': primary, 'result': result}


def complete_proof_evidence(runner, result, *, view=None):
    """Drive the native support job outside the Proof lock, then resume the exact saved role."""
    while result.get('status') == 'waiting_for_evidence':
        request = result['evidence_request']
        # This is a separate coordinator lock. Other proof views and model slots remain free
        # while the service runs. A concurrent coordinator cannot create a competing receipt.
        with runner.store._file_lock('evidence-service.lock'):
            current = ProofStore.load(runner.agent.workspace.root, result['id'])
            receipt = None
            if current.state.get('evidence_request') == request:
                try:
                    receipt = _evidence(runner, request, view)
                finally:
                    # Research may bind its empty visual context on the shared model transport.
                    images.bind(runner.agent, runner.agent.image_refs)
        if receipt is not None and 'paused' in receipt:
            return {**result, 'status': 'paused', 'stop_reason': receipt['paused']}
        result = runner.resume(result['id'], evidence=receipt)
    return result
