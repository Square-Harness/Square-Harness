# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Service a suspended Proof using its coordinator's evidence and remaining resources."""
import hashlib
import json
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
from .tools import Workspace


class _SupportLibrary(LiteratureTools):
    """Separate research counters within the enclosing task's source allowance."""

    def __init__(self, parent, request):
        self.request_ceiling = min(request.get('remaining_requests', parent.max_requests),
                                   max(0, parent.max_requests - parent.stats['requests']))
        self.char_ceiling = min(request.get('remaining_chars', parent.max_chars),
                                max(0, parent.max_chars - parent.stats['returned_chars']))
        super().__init__(parent.root, online=parent.online, timeout=parent.timeout)
        if parent.assigned_sources is not None:
            self.assigned_sources = set(parent.assigned_sources)
        self.cancelled = parent.cancelled
        self.result_chars = parent.result_chars

    def reset_budget(self, *, max_requests, max_chars):
        # Native research restores its own saved limits. A narrower enclosing allowance still applies.
        super().reset_budget(max_requests=min(max_requests, self.request_ceiling), max_chars=min(max_chars, self.char_ceiling))


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


def _named_proof_source(runner, request):
    """Resolve a source named by the question among successful paper opens in this same model turn."""
    question = request['question']
    desk = runner.turns.desk
    if not desk.requested_urls:
        return None
    records = runner.state['evidence']
    asking = next(e for e in records if e.get('request', {}).get('id') == request['id'])
    matches = {}
    for entry in records:
        if (entry['call'] != asking['call'] or entry['ordinal'] >= asking['ordinal'] or
                entry['name'] != 'open_paper' or entry['status'] != 'complete'):
            continue
        try:
            opened = json.loads(runner.store.read_artifact(entry['result']))
            if not isinstance(opened, dict) or not opened.get('document_id') or not refcheck.names_open_document(question, opened):
                continue
            metadata, _ = desk.literature._document(opened['document_id'])
        except (ValueError, OSError):
            continue
        if (not metadata.get('requested_url') or opened.get('text_sha256') != metadata['text_sha256'] or
                opened.get('requested_url') != metadata['requested_url']):
            continue
        matches[metadata['document_id']] = {k: metadata[k] for k in ('requested_url', 'document_id', 'text_sha256')}
        matches[metadata['document_id']].update(proof_id=runner.state['id'], evidence_id=entry['id'])
    if len(matches) == 1:
        source = next(iter(matches.values()))
        if source['requested_url'] in desk.requested_urls:
            return source
    return None


def _exhausted_transport_pause(state):
    """A provider interruption can arrive as the support job uses the last of its own allowance."""
    if state['status'] != 'paused' or (state.get('worker_error') or {}).get('scope') != 'transport':
        return []
    counters = {'seconds': 'seconds_used', 'tokens': 'tokens_charged', 'input_tokens': 'input_tokens_charged'}
    return [name for name, counter in counters.items()
            if state['settings'].get('max_' + name) is not None and state[counter] >= state['settings']['max_' + name]]


def _native_usage(state):
    stats = (state.get('literature_snapshot') or {}).get('stats', {})
    return {'tokens': state['tokens_charged'], 'measured_tokens': job_output_budget(state, False)['used'],
            'seconds': state['seconds_used'], 'requests': stats.get('requests', 0),
            'returned_chars': stats.get('returned_chars', 0)}


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
    if saved:
        state = _read_state(_job_directory(library.root, native_id, folder), native_id)
        if state['goal'] != question.strip() or state['kind'] != kind:
            raise ValueError('The saved evidence job belongs to a different request.')
    if library.assigned_sources is not None:
        # An older pending request may have been written before the caller restricted its sources.
        # Preserve costs already incurred while keeping its external evidence out of this Proof.
        result = {'status': 'unavailable', 'reason': 'This proof is limited to its assigned sources.'}
        if saved:
            receipt.update(_native_usage(state))
            result.update(job_id=native_id, kind=kind, native_status=state['status'])
        return {**receipt, 'result': result}
    if not saved:
        entries = _prior_entries(view, question)
        primary = _passages(library, entries, runner.agent.workspace.result_chars)
        if primary:
            return {**receipt, 'result': {'status': 'source_read', 'entry_ids': [e['id'] for e in entries]},
                    'primary_sources': primary}
        seconds = min(runner.state['settings']['max_seconds'] - runner.state['seconds_used'],
                      getattr(runner.agent.client, 'remaining_seconds', math.inf))
        if seconds <= 0 or request['remaining_tokens'] == 0:
            return {**receipt, 'result': {'status': 'unavailable', 'reason': 'The remaining proof allowance is reserved.'}}

    # The child sees the internal mathematical question. Only public search terms may leave the source library.
    # It shares the enclosing inference
    # client, including admission, cancellation, input/output accounting and the provider profile.
    child_library = _SupportLibrary(library, request)
    child_library.outbound_guard = desk.check_outbound
    workspace = Workspace(library.root, literature=child_library, read_types=())
    agent = Agent(runner.agent.client, workspace, runner.agent.model, runner.agent.ctx, runner.agent.predict,
                  runner.agent.think, 'check' if kind == 'check' else 'literature', seed=runner.agent.seed,
                  temperature=runner.agent.temperature)
    explicit_caps = (runner.state['settings'].get('specialist_output_cap'),
                     getattr(runner.agent, 'document_output_cap', None))
    agent.document_output_cap = min((cap for cap in explicit_caps if cap is not None), default=None)
    images.bind(agent, [])
    if kind == 'literature':
        # Both registry lanes use the same durable source allowance.
        child = factory(agent, runner.emit, shared_source_budget=True)
        child.cancel = child_library.cancelled
    else:
        child = factory(agent, runner.emit, proof_source=_named_proof_source(runner, request))
    runner.emit('notice', f'Proof {proof_id}: {request["role"]} requested {kind} evidence ({native_id}).')
    if saved:
        # A resumed Proof may find its research job already paused at its deadline. Preserve
        # the native failure and avoid dispatching a research call to rediscover the same limit.
        if not _exhausted_transport_pause(state):
            child.resume(native_id, caller_output_cap=agent.document_output_cap)
            state = child.state
    else:
        tokens = min(runner.turns.assistance_remaining(request['role']),
                     request['remaining_tokens'] if request['remaining_tokens'] is not None else math.inf)
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
    exhausted = _exhausted_transport_pause(state)
    if state['status'] == 'paused' and (not exhausted or child_library.cancelled() or runner.agent.workspace.cancelled()):
        # The native job has checkpointed its usage and pending calls. The proof stays at the
        # request. Resuming it after cancellation or a provider interruption finds the same child.
        return {'paused': state.get('stop_reason') or 'Evidence collection paused.', 'job_id': native_id}
    rows = export(state)
    project_index.freeze_documents(library.root, {'entries': rows})
    primary = _passages(child_library, rows, runner.agent.workspace.result_chars)
    result = {'job_id': native_id, 'kind': kind, 'status': state['status'], 'stop_reason': state.get('stop_reason'),
              'sources': [{'status': row['status'], 'reference': row['summary']} for row in rows],
              'note': 'The attached primary passages can be checked directly. Bibliographic records and citing '
                      'sources alone cannot establish the requested theorem.'}
    if exhausted:
        result.update(status='inconclusive', native_status=state['status'], exhausted_allowances=exhausted,
                      worker_error=state['worker_error'])
        result['note'] += ' The research allowance ended during a provider failure; its saved evidence is partial.'
    return {**receipt, **_native_usage(state), 'primary_sources': primary, 'result': result}


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
