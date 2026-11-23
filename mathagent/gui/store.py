# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Read-only views of saved proof and research jobs, and the interface's chats.

Job views never write engine state. Chat sessions are the interface's own
records: the terminal keeps chat in memory, while a browser tab or phone needs
the conversation to survive a reload.
"""
import copy
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid

from ..reporting import one_line
from ..ledger import ProofStore, _ID, _ARTIFACT, _atomic_write, _directory, _read, _regular, _now, _sync_directory
from ..format_request import can_recover_previous_markdown, compile_followup_request, markdown_conversion_request, previous_markdown_request
from ..worker_errors import WorkerInputError
from ..literature import LiteratureTools
from ..litreview import ReviewRunner, STATEMENT, _has_locator, _publication_record
from ..reporting import job_output_budget, output_totals, reported_output
from ..research import _job_directory, _read_state as _read_research
from ..review import variant_for_state
from ..tools import READ_GROUPS, SKIP, TEXT, Workspace, is_source_archive, source_suffix
from ..style import list_profiles, load_choice, macro_definitions, public_profile, read_template, save_choice, scan, set_template
from ..writeup import SOURCE_BUNDLE_BYTES, TEMPLATE_SUFFIXES, download_name, entry_identifiers
from .. import images
from . import agent_views, personalization, projects

CHAT_MODES = ('explore', 'free')
SAVED_CHAT_MODES = (*CHAT_MODES, 'critic')
# How the interface names a suggestion in chat previews, including saved suggestions.
ROUTE_NAMES = {'prove': 'Proof', 'critic': 'Answer', 'explore': 'Answer', 'literature': 'Literature review',
               'referee': 'Review', 'detailed_review': 'Review', 'typo_check': 'Typo check', 'quick_review': 'Quick review',
               'explain': 'Explanation', 'writeup': 'Write-up', 'summarize': 'Summary', 'clarify': 'Question', 'python_lab': 'Python Lab',
               'sanity_check': 'Numerical sanity check'}
_EXPERIMENT = re.compile(r'^exp-\d{8}-\d{6}-[0-9a-f]{6}$')
_ARTIFACT_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
# Types a sandbox run may return and the interface can show (served by /api/artifacts).
ARTIFACT_MIME = {'.png': 'image/png', '.svg': 'image/svg+xml', '.pdf': 'application/pdf', '.html': 'text/html',
                 '.csv': 'text/csv', '.txt': 'text/plain', '.json': 'application/json'}


def artifact_url(experiment_id, name):
    return f'/api/artifacts/{experiment_id}/{name}'


def experiment_item(experiment_id, status, artifacts, purpose=''):
    """A transcript item listing a sandbox run's files as artifacts, or None when there are none.

    Each artifact is {artifact_id, filename, mime_type, url, width, height, bytes}; ``artifacts``
    may give a ``name`` or a workspace ``path`` per file. The endpoint validates both again.
    """
    if not isinstance(experiment_id, str) or not _EXPERIMENT.match(experiment_id):
        return None
    shown = []
    for artifact in artifacts or []:
        name = artifact.get('name') or str(artifact.get('path', '')).rsplit('/', 1)[-1]
        mime = ARTIFACT_MIME.get(Path(name).suffix.lower())
        if mime is None or not _ARTIFACT_NAME.match(name):
            continue
        shown.append({'artifact_id': f'{experiment_id}/{name}', 'filename': name, 'mime_type': mime,
                      'url': artifact_url(experiment_id, name), 'width': artifact.get('width'),
                      'height': artifact.get('height'), 'bytes': artifact.get('bytes')})
    if not shown:
        return None
    return {'role': 'experiment', 'content': '', 'experiment': experiment_id, 'run_status': str(status or ''),
            'purpose': str(purpose or '')[:300], 'artifacts': shown[:16], 'time': _now()}


def _tool_experiment(message):
    """The experiment item for a saved execute_python result, if it produced figures."""
    if message.get('tool_name') != 'execute_python':
        return None
    try:
        value = json.loads(message.get('content') or '')
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return experiment_item(value.get('experiment_id'), value.get('status'),
                           list(value.get('figures') or []) + list(value.get('files') or []))
UPLOAD_SUFFIXES = {'.tex', '.sty', '.cls', '.bib', '.md', '.txt', '.pdf', '.py', '.png', '.jpg', '.jpeg', '.webp'}
BINARY_UPLOADS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}  # figures and visual inputs, and PDFs to read
UPLOAD_BYTES = 20 * 1024 * 1024
_chat_lock = threading.RLock()
CHAT_BYTES = 16 * 1024 * 1024
STREAM_BYTES = 4 * 1024 * 1024
_RESEARCH_ARTIFACT = re.compile(r'[0-9]{4,}-[A-Za-z0-9_-]+\.md\Z')
_cache_lock = threading.Lock()
_cache = {}


class NotFound(LookupError):
    """A requested job, chat, artifact or approval does not exist."""


def _cached(path, load, revision=None):
    """Parse a state file once per (mtime, size); listings run on every job event."""
    info = path.lstat()
    key = (str(path), info.st_mtime_ns, info.st_size, revision)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    value = load()
    with _cache_lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[key] = value
    return value


def locked(directory):
    """True while an engine process holds the job's run lock.

    Probing takes a shared lock for microseconds and is only used for jobs whose
    saved status says running, to tell live work from an interrupted process.
    """
    try:
        fd = os.open(directory / 'run.lock', os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def parse_stream(text):
    """Accumulate an Ollama NDJSON stream; a killed writer may leave a partial last line."""
    out = {'text': '', 'thinking': '', 'tool_calls': [], 'done': False, 'stats': {}}
    fragments = {'text': [], 'thinking': []}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            break
        if not isinstance(event, dict):
            continue
        message = event.get('message') if isinstance(event.get('message'), dict) else {}
        for key, field in (('text', 'content'), ('thinking', 'thinking')):
            if isinstance(message.get(field), str):
                fragments[key].append(message[field])
        if isinstance(message.get('tool_calls'), list):
            out['tool_calls'].extend(message['tool_calls'])
        if event.get('done'):
            out['done'] = True
            out['stats'] = {k: event[k] for k in ('eval_count', 'prompt_eval_count', 'done_reason', 'eval_duration')
                            if k in event}
    out.update({key: ''.join(parts) for key, parts in fragments.items()})
    return out


def stream_chunk(path, start=0):
    """Complete lines of a stream file from byte offset `start`."""
    if type(start) is not int or start < 0:
        raise ValueError('Stream offset must be a nonnegative integer')
    _regular(path)
    size = path.lstat().st_size
    reset = start > size
    if reset:
        start = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        stream.seek(start)
        data = stream.read(min(size - start, STREAM_BYTES))
    cut = data.rfind(b'\n') + 1
    parsed = parse_stream(data[:cut].decode('utf-8', errors='replace'))
    return {'file': path.name, 'start': start, 'end': start + cut, 'reset': reset, **parsed}


def _json_artifact(name, text):
    out = {'name': name, 'size': len(text.encode()), 'text': text}
    if name.endswith('.jsonl') or '-stream' in name:
        out['stream'] = parse_stream(text)
    else:
        try:
            out['json'] = json.loads(text)
        except ValueError:
            pass
    return out


def _artifacts(directory, pattern):
    found = []
    for path in sorted((directory / 'artifacts').iterdir()):
        if pattern.fullmatch(path.name) and not path.is_symlink():
            found.append({'name': path.name, 'size': path.lstat().st_size})
    return found


# -- proofs -----------------------------------------------------------------

def _proof_directory(root, proof_id):
    if not isinstance(proof_id, str) or not _ID.fullmatch(proof_id):
        raise ValueError('Invalid proof ID')
    directory = Path(root) / '.mathagent' / 'proofs' / proof_id
    if not directory.exists():
        raise NotFound('Unknown proof job')
    for path in (directory.parent.parent, directory.parent, directory, directory / 'artifacts'):
        _directory(path)
    return directory


def _budget(used, limit):
    return {'used': used, 'limit': limit}


def _live_context(call):
    allocation = (call or {}).get('allocation') or (call or {}).get('context') or {}
    if 'input_tokens' in allocation and 'context_tokens' in allocation:
        return {'context': _budget(allocation['input_tokens'], allocation['context_tokens'])}
    return {}


def _working_time(state, running):
    used = state['seconds_used']
    checkpoint = state.get('active_checkpoint_wall')
    if running and isinstance(checkpoint, (int, float)):
        used += max(0, time.time() - checkpoint)
    return _budget(round(used, 1), state['settings']['max_seconds'])


def _review_counts(state):
    """Candidates by review status. Legacy v1 jobs recorded claims instead and show none."""
    counts = {}
    for candidate in state['candidates'] if state['version'] == 2 else []:
        status = candidate.get('review_status', 'unreviewed')
        counts[status] = counts.get(status, 0) + 1
    return counts


def _candidate_text(store, candidate):
    """A candidate's saved text, or None when the file no longer matches its digest."""
    try:
        text = store.read_artifact(candidate['artifact'])
    except ValueError:
        return None
    return text if hashlib.sha256(text.encode()).hexdigest() == candidate.get('sha256') else None


def proof_request_text(state):
    """The task as the user reads it (a saved handoff Proof shows the user's request, not controller text)."""
    from ..proof import request_text
    return request_text(state)


def list_proofs(root, active=None):
    root = Path(root)
    base = root / '.mathagent' / 'proofs'
    if not base.is_dir() or base.is_symlink():
        return []
    jobs = []
    for path in base.iterdir():
        if not _ID.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            state = _cached(path / 'state.json', lambda: ProofStore.load(root, path.name).state)
        except (OSError, ValueError):
            continue
        running = state['status'] == 'running' and (path.name == active or locked(path))
        jobs.append({'id': state['id'], 'kind': 'proof', 'version': state['version'], 'status': state['status'],
                     'title': one_line(proof_request_text(state), 140), 'updated_at': state['updated_at'],
                     'created_at': state['created_at'], 'running': running,
                     'sources': [s['path'] for s in state['sources']],
                     'rounds': _budget(state['rounds_started'], state['settings']['max_rounds']),
                     'tokens': job_output_budget(state, running),
                     'reviews': _review_counts(state)})
    return sorted(jobs, key=lambda job: job['updated_at'], reverse=True)


def proof_detail(root, proof_id, active=None):
    directory = _proof_directory(root, proof_id)
    store = ProofStore.load(root, proof_id)
    s = store.state
    running = s['status'] == 'running' and (proof_id == active or locked(directory))
    call = next((c for c in reversed(s['calls']) if c.get('status') == 'running'), None) if running else None
    settings = s['settings']
    detail = {
        'id': s['id'], 'kind': 'proof', 'version': s['version'], 'legacy': s['version'] != 2,
        'goal': proof_request_text(s), 'status': s['status'], 'stop_reason': s.get('stop_reason'),
        'recovery_notice': s.get('recovery_notice'),
        'created_at': s['created_at'], 'updated_at': s['updated_at'], 'revision': s['revision'],
        'settings': settings,
        'images': s.get('images', []),
        'budget': {'rounds': _budget(s['rounds_started'], settings['max_rounds']),
                   'tokens': job_output_budget(s, running),
                   'seconds': _working_time(s, running),
                   **_live_context(call)},
        'progress': s.get('progress'),
        'sources': [{'path': src['path'], 'sha256': src.get('sha256'),
                     'lines': len(src['content'].splitlines())} for src in s['sources']],
        'calls': s['calls'], 'running': running,
        'live': {'file': call['stream'], 'role': call['role'], 'round': call['round']} if call else None,
        'artifacts': _artifacts(directory, _ARTIFACT),
    }
    if s['version'] != 2:
        # A v0.4 job: readable, never resumed. Its proof.md is the audited candidate, if any.
        proof = directory / 'proof.md'
        detail.update(candidates=[], reviews=[], pending=None, selected_candidate=None, initial_candidate=None,
                      selection_history=[], answer=_read(proof) if proof.exists() else None)
        return detail
    selected = next((c for c in s['candidates'] if c['id'] == s['selected_candidate']), None)
    detail.update(candidates=s['candidates'], reviews=s['reviews'], pending=s['pending'],
                  selected_candidate=s['selected_candidate'], initial_candidate=s['initial_candidate'],
                  selected_review_status=selected.get('review_status', 'unreviewed') if selected else 'unreviewed',
                  claim_outcome='unclassified',
                  selection_history=s.get('selection_history', []),
                  answer=_candidate_text(store, selected) if selected else None)
    return detail


def proof_sources(root, proof_id):
    _proof_directory(root, proof_id)
    return [{'path': src['path'], 'sha256': src.get('sha256'), 'content': src['content']}
            for src in ProofStore.load(root, proof_id).state['sources']]


def proof_report(root, proof_id):
    directory = _proof_directory(root, proof_id)
    out = {}
    for name in ('report', 'ledger'):
        path = directory / (name + '.md')
        out[name] = _read(path) if path.exists() else ''
    return out


def proof_artifact(root, proof_id, name):
    directory = _proof_directory(root, proof_id)
    if not _ARTIFACT.fullmatch(name):
        raise ValueError('Invalid artifact name')
    path = directory / 'artifacts' / name
    if not path.exists():
        raise NotFound('Unknown artifact')
    return _json_artifact(name, _read(path))


def proof_stream(root, proof_id, name, start=0):
    directory = _proof_directory(root, proof_id)
    if not _ARTIFACT.fullmatch(name) or not name.endswith('.jsonl'):
        raise ValueError('Invalid stream name')
    path = directory / 'artifacts' / name
    if not path.exists():
        raise NotFound('Unknown stream')
    return stream_chunk(path, start)


# -- literature and referee reports ----------------------------------------

def research_state(root, job_id, *, verify=True):
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise ValueError('Invalid research ID')
    if not (Path(root) / '.mathagent' / 'research' / job_id).exists():
        raise NotFound('Unknown research job')
    return _read_research(_job_directory(root, job_id), job_id, verify=verify)


def _variant(state):
    """The kind of a saved job as the interface shows it: a review saved at Low effort was a typo check."""
    return 'typos' if variant_for_state(state) == 'typos' else state.get('variant')


def list_research(root, active=None):
    root = Path(root)
    base = root / '.mathagent' / 'research'
    if not base.is_dir() or base.is_symlink():
        return []
    jobs = []
    for path in base.iterdir():
        if not _ID.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            state = _cached(path / 'state.json', lambda: research_state(root, path.name, verify=False))
        except (OSError, ValueError, KeyError, NotFound):
            continue
        running = state['status'] == 'running' and (path.name == active or locked(path))
        jobs.append({'id': state['id'], 'kind': state['kind'], 'status': state['status'],
                     'phase': state['phase'], 'title': one_line(proof_request_text(state), 140),
                     'updated_at': state['updated_at'], 'created_at': state['created_at'],
                     'running': running, 'sources': [s['path'] for s in state['sources']],
                     'variant': _variant(state), 'verdict': state.get('verdict') or None,
                     'tokens': job_output_budget(state, running)})
    return sorted(jobs, key=lambda job: job['updated_at'], reverse=True)


def research_detail(root, job_id, active=None):
    s = research_state(root, job_id)
    ReviewRunner.refresh_screening_preview(s)
    directory = _job_directory(root, job_id)
    running = s['status'] == 'running' and (job_id == active or locked(directory))
    call = next((c for c in reversed(s['calls']) if c.get('status') == 'running'), None) if running else None
    settings = s['settings']
    usage = (s.get('literature_snapshot') or {}).get('stats', {})
    report = directory / 'report.md'
    return {
        'id': s['id'], 'kind': s['kind'], 'goal': s['goal'], 'status': s['status'], 'phase': s['phase'],
        'stop_reason': s.get('stop_reason'), 'created_at': s['created_at'], 'updated_at': s['updated_at'],
        'settings': settings,
        'images': s.get('images', []),
        # A literature review's interface meter is "Model calls": the calls made, with no round limit.
        'budget': {'rounds': (_budget(len(s.get('calls') or []), None) if s.get('pipeline') == ReviewRunner.PIPELINE
                              else _budget(s['rounds_started'], settings['max_rounds'])),
                   'tokens': job_output_budget(s, running),
                   'input_tokens': _budget(s['input_tokens_charged'], settings['max_input_tokens']),
                   'seconds': _working_time(s, running),
                   'requests': _budget(usage.get('requests', 0), settings['max_requests']),
                   'chars': _budget(usage.get('returned_chars', 0), settings['max_chars']),
                   **_live_context(call)},
        'progress': s.get('progress'),
        'sources': [{'id': src['id'], 'path': src['path'], 'sha256': src['sha256'],
                     'lines': len(src['content'].splitlines())} for src in s['sources']],
        'plan': s['plan'], 'notes': s['notes'], 'evidence': s['evidence'], 'draft': s['draft'],
        'pipeline': s.get('pipeline'), 'variant': _variant(s), 'verdict': s.get('verdict') or None,
        'level': s.get('level'), 'unit': (s.get('unit') or {}).get('name'),
        'review': s['review'], 'draft_complete': s.get('draft_complete'),
        'review_complete': s.get('review_complete'), 'warnings': s.get('warnings', []),
        'citation_issues': s.get('citation_issues', []),
        'review_context_issues': s.get('review_context_issues', []),
        'manuscript_ranges': s.get('manuscript_ranges', {}), 'calls': s['calls'],
        'report': _read(report) if report.exists() else '', 'running': running,
        'live': {'file': call['stream'], 'role': call['role']} if call else None,
        'artifacts': _artifacts(directory, _RESEARCH_ARTIFACT),
        **({key: s.get(key) for key in ('outline', 'sections', 'document', 'section_lines', 'compile',
                                        'outputs', 'macros', 'theorems', 'output_name', 'style', 'scoped', 'deliver',
                                        'title', 'style_checked', 'readiness', 'gates', 'checks', 'needs', 'document_kind',
                                        'reviews', 'review_round', 'conversion_source')}
           if s['kind'] == 'writeup' else {}),
        # Candidates without their texts: the page shows each version's round, score and readiness.
        **({'candidates': [{key: c.get(key) for key in ('round', 'score', 'readiness', 'compile_ok')} | {'selected': c.get('id') == s.get('selected')}
                           for c in s.get('candidates') or []]} if s['kind'] == 'writeup' else {}),
        # The parts hold the material itself: the page needs only how far the writing is.
        **({'parts': [{'complete': bool(part.get('complete')), 'characters': len(part.get('text', ''))} for part in s['parts']]}
           if s['kind'] == 'writeup' and isinstance(s.get('parts'), list) else {}),
        **({key: s.get(key) for key in ('style_name', 'articles', 'domains', 'observations', 'profile', 'saved_to')}
           if s['kind'] == 'style' else {}),
        **({'review_evidence_count': len(_review_evidence_order(s))} if s.get('pipeline') == ReviewRunner.PIPELINE else {}),
        **({key: s[key] for key in ('references', 'coverage', 'outputs') if key in s}
           if s['kind'] == 'literature' else {}),
    }


def research_pdf(root, job_id):
    research_state(root, job_id)
    path = _job_directory(root, job_id) / 'artifacts' / 'compiled.pdf'
    if not path.exists():
        raise NotFound('This write-up has no compiled PDF')
    _regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(UPLOAD_BYTES + 1)
        if len(data) > UPLOAD_BYTES:
            raise ValueError('Compiled PDF exceeds the 20 MB download limit')
        return data


def research_tex(root, job_id):
    """A write-up's LaTeX document and the file name to save it under."""
    state = research_state(root, job_id)
    if state['kind'] != 'writeup' or not state.get('document'):
        raise NotFound('This write-up has no document yet')
    name = (state.get('outputs') or {}).get('download') or download_name(state)
    return name, state['document'].encode('utf-8')


def research_source(root, job_id):
    state = research_state(root, job_id)
    name = (state.get('outputs') or {}).get('source')
    if state['kind'] != 'writeup' or not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.zip', name):
        raise NotFound('This write-up has no source bundle')
    path = _job_directory(root, job_id) / 'artifacts' / 'source.zip'
    _regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(SOURCE_BUNDLE_BYTES + 1)
    if len(data) > SOURCE_BUNDLE_BYTES:
        raise ValueError('Source bundle exceeds the download limit')
    return name, data


def research_body(root, job_id):
    """A write-up's body alone (to paste into a paper) and the file name to save it under."""
    state = research_state(root, job_id)
    name = Path(str((state.get('outputs') or {}).get('body') or '')).name
    if state['kind'] != 'writeup' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.tex', name):
        raise NotFound('This write-up has no body file')
    path = _job_directory(root, job_id) / 'artifacts' / 'body.tex'
    _regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(UPLOAD_BYTES + 1)
    if len(data) > UPLOAD_BYTES:
        raise ValueError('The body file exceeds the 20 MB download limit')
    return name, data


# A literature review's files: fixed names in its job directory, never a name taken from its state.
REVIEW_FILES = {'review': 'review.md', 'bib': 'references.bib'}


def research_review_file(root, job_id, output):
    """A literature review's Markdown text ('review') or BibTeX file ('bib') and the file name to save it under."""
    state = research_state(root, job_id)
    path = _job_directory(root, job_id) / REVIEW_FILES[output]
    if state['kind'] != 'literature' or not path.exists():
        raise NotFound('This literature review has no ' + ('review' if output == 'review' else 'BibTeX') + ' file yet')
    _regular(path)
    corrected_bib = ReviewRunner.refresh_screening_preview(state)
    if corrected_bib is not None:
        text = state['draft'] if output == 'review' else corrected_bib
        return _review_name(state) + path.suffix, text.encode('utf-8')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(UPLOAD_BYTES + 1)
    if len(data) > UPLOAD_BYTES:
        raise ValueError('The file exceeds the 20 MB download limit')
    return _review_name(state) + path.suffix, data


def _review_name(state):
    """A download name for a literature review: its title (else its topic, else its request), made safe."""
    scope = state.get('scope') if isinstance(state.get('scope'), dict) else {}
    title = next((value for value in (scope.get('title'), scope.get('topic')) if isinstance(value, str) and value.strip()),
                 state['goal'])
    title = re.sub(r'\\[a-zA-Z]+|[$^_{}]', ' ', title.lower())
    return re.sub(r'[^a-z0-9]+', '-', title).strip('-')[:60].rstrip('-') or 'literature-review'


def discard_download(root, job_id):
    """Delete a write-up kept for download (its conversation is gone); other jobs stay."""
    try:
        state = research_state(root, job_id)
        directory = _job_directory(root, job_id)
    except (NotFound, OSError, ValueError):
        return False
    if state['kind'] != 'writeup' or state.get('deliver') != 'download' or locked(directory):
        return False
    shutil.rmtree(directory)
    return True


def _review_evidence_order(state):
    # Final citation numbers correspond to the report. Read cards have pool IDs until assembly.
    cited = state.get('bibliography') or [r['n'] for r in state.get('references') or [] if type(r.get('n')) is int]
    if cited or state.get('phase') == 'done':
        return [(n, k) for k, n in enumerate(cited, 1)]
    cards = state.get('cards') or {}
    return [(n, None) for n in state.get('selected') or [] if str(n) in cards]


def _legacy_review_passages(root, record, document, results):
    """Recover a quote from an earlier card only if it occurs uniquely in the cached public paper."""
    if not document.get('document_id') or not results:
        return []
    try:
        meta, lines = LiteratureTools(root)._document(document['document_id'])
    except (OSError, ValueError, TypeError):
        return []
    canon = str(meta.get('canonical_identifier') or '')
    if not ((record.get('arxiv') and canon == 'arxiv:' + record['arxiv'])
            or (record.get('doi') and canon == 'https://doi.org/' + record['doi'])):
        return []
    body = '\n'.join(lines)
    passages = []
    for result in results[:8]:
        quote = result.get('quote') or ''
        if not isinstance(quote, str) or not 20 <= len(quote) <= 1600:
            continue
        pattern = re.compile(r'\s+'.join(re.escape(w) for w in quote.split()))
        match = pattern.search(body)
        if not match or pattern.search(body, match.end()):
            continue
        first = body.count('\n', 0, match.start()) + 1
        end = first + match[0].count('\n')
        locator = str(result.get('locator') or '').strip().rstrip('.')
        before = ' '.join(lines[max(0, first - 5):end]).strip()
        if not STATEMENT.match(locator) or not _has_locator(locator, before):
            locator = ''
        item = {'lines': f'L{first}-L{end}', 'locator': locator, 'text': match[0]}
        if item not in passages:
            passages.append(item)
    return passages


def review_evidence(root, job_id):
    state = research_state(root, job_id)
    if state.get('pipeline') != ReviewRunner.PIPELINE:
        raise NotFound('This job has no literature-review evidence')
    ReviewRunner.refresh_screening_preview(state)
    pool = {r['n']: r for r in state.get('pool') or []}
    references = {r['n']: r for r in state.get('references') or []}
    items = []
    for n, k in _review_evidence_order(state):
        r = pool.get(n) or references.get(n)
        if not r:
            continue
        r = _publication_record(dict(r))
        card = (state.get('cards') or {}).get(str(n)) or {}
        verified = [v for v in card.get('results') or [] if isinstance(v, dict) and v.get('verified') is True]
        passages = []
        # Current cards store only passages selected from the source, with their original extraction ranges.
        for lines, text in (card.get('passages') or {}).items():
            if not re.fullmatch(r'L[1-9]\d*-L[1-9]\d*', lines) or not isinstance(text, str) or not text.strip():
                continue
            locators = [(card.get('primary_locators') or {}).get(lines, '')]
            locators.extend(v.get('locator') for v in verified if v.get('lines') == lines)
            locator = next((loc.rstrip('.') for loc in locators if isinstance(loc, str)
                            and STATEMENT.match(loc) and _has_locator(loc.rstrip('.'), text)), '')
            passages.append({'lines': lines, 'locator': locator, 'text': text})
        if card.get('mode') == 'full_text' and 'passages' not in card:
            document = (state.get('documents') or {}).get(str(n)) or {}
            passages = _legacy_review_passages(root, r, document, verified)
        items.append({'k': k, 'n': n, 'title': r.get('title'), 'doi': r.get('doi'), 'arxiv': r.get('arxiv'),
                      'zbl': r.get('zbl'), 's2': r.get('s2'), 'passages': passages,
                      'abstract': '' if passages else r.get('abstract') or ''})
    return items


def research_sources(root, job_id):
    return [{'id': src['id'], 'path': src['path'], 'sha256': src['sha256'], 'content': src['content']}
            for src in research_state(root, job_id)['sources']]


def research_artifact(root, job_id, name, stream_from=None):
    research_state(root, job_id)
    if not _RESEARCH_ARTIFACT.fullmatch(name):
        raise ValueError('Invalid artifact name')
    path = _job_directory(root, job_id) / 'artifacts' / name
    if not path.exists():
        raise NotFound('Unknown artifact')
    if stream_from is not None:
        return stream_chunk(path, stream_from)
    return _json_artifact(name, _read(path))


# -- workspace files that can be pinned ------------------------------------

def list_files(root, limit=2000):
    root = Path(root)
    found = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP
                         and not (Path(folder) / d).is_symlink())
        for name in sorted(files):
            path = Path(folder) / name
            suffix = source_suffix(path)
            if name.startswith('.') or path.is_symlink() or (suffix not in TEXT and suffix != '.pdf' and not images.is_image(path)):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.append({'path': path.relative_to(root).as_posix(), 'size': size,
                          'kind': 'image' if images.is_image(path) else 'pdf' if suffix == '.pdf' else 'text'})
            if len(found) >= limit:
                return found
    return found


# -- chats --------------------------------------------------------------------

def _locked_chat(function):
    def wrapper(*args, **kwargs):
        with _chat_lock:
            return function(*args, **kwargs)
    wrapper.__name__, wrapper.__doc__ = function.__name__, function.__doc__
    return wrapper


def _chats(root, create=False):
    base = Path(root) / '.mathagent'
    if not create and not (base / 'chats').exists():
        return None
    _directory(base, create=create)
    _directory(base / 'chats', create=create)
    return base / 'chats'


def _read_chat(path):
    _regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Refusing non-regular chat file')
        data = stream.read(CHAT_BYTES + 1)
    if len(data) > CHAT_BYTES:
        raise ValueError('Chat file is too large')
    return json.loads(data.decode('utf-8'))


def _save_chat(root, chat):
    chat['updated_at'] = _now()
    data = json.dumps(chat, ensure_ascii=False, indent=1).encode('utf-8')
    if len(data) > CHAT_BYTES:
        raise ValueError('This chat reached the 16 MB storage limit; start a new chat')
    _atomic_write(_chats(root, create=True) / (chat['id'] + '.json'), data)


def create_chat(root, mode, think=True, model='', online=False, project_id=None):
    if mode not in CHAT_MODES:
        raise ValueError('Chat mode must be explore or free')
    if project_id is not None:
        projects.read_project(root, project_id)
    now = _now()
    chat = {'version': 1, 'id': str(uuid.uuid4()), 'mode': mode, 'title': '',
            'created_at': now, 'updated_at': now,
            'settings': {'think': bool(think), 'model': model, 'online': bool(online)},
            'history': [], 'transcript': [], 'project_id': project_id}
    _save_chat(root, chat)
    return chat


@_locked_chat
def set_chat_project(root, chat_id, project_id):
    if project_id is not None:
        projects.read_project(root, project_id)
    chat = load_chat(root, chat_id)
    chat['project_id'] = project_id
    _save_chat(root, chat)
    return chat


@_locked_chat
def set_chat_title(root, chat_id, title, *, manual=True):
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ValueError('Give the conversation a title of at most 120 characters')
    chat = load_chat(root, chat_id)
    if manual or chat.get('title_source') != 'manual':
        chat['title'] = one_line(title, 120)
        chat['title_source'] = 'manual' if manual else 'generated'
        _save_chat(root, chat)
    return chat


def chat_images(chat):
    """The original image refs supplied to this chat, never workspace rereads."""
    result = {}
    for ref in chat.get('images', []):
        result[ref['sha256']] = dict(ref)
    for item in chat.get('transcript', []):
        if item.get('role') == 'user':
            for ref in item.get('images', []):
                result[ref['sha256']] = dict(ref)
    return list(result.values())


def image_history(chat, history=None):
    """Annotate legacy native user messages with their original saved images.

    This is a read-only view: existing anchors win, and unmatched messages are
    explicitly text-only rather than receiving the conversation's image pool.
    """
    result = copy.deepcopy(chat.get('history', []) if history is None else history)
    users = [item for item in chat.get('transcript', [])
             if item.get('role') == 'user' and not item.get('discarded')
             and isinstance(item.get('content'), str) and item['content']]
    cursor = 0
    for message in result:
        if message.get('role') != 'user':
            continue
        content = message.get('content', '')
        match = next((index for index in range(cursor, len(users))
                      if content == users[index]['content']
                      or (isinstance(content, str) and content.startswith(users[index]['content'] + '\n\n'))), None)
        if 'images' not in message:
            message['images'] = copy.deepcopy(users[match].get('images', [])) if match is not None else []
        if match is not None:
            cursor = match + 1
    return result


@_locked_chat
def save_chat_images(root, chat_id, refs):
    chat = load_chat(root, chat_id)
    chat['images'] = [dict(ref) for ref in refs]
    _save_chat(root, chat)


@_locked_chat
def claim_auto_title(root, chat_id):
    """Reserve one optional title request without racing transcript writers."""
    chat = load_chat(root, chat_id)
    if chat.get('auto_title_attempted') or chat.get('title_source') == 'manual':
        return None
    first = next((item for item in chat['transcript']
                  if item.get('role') == 'user' and not item.get('discarded')
                  and len(item.get('content', '').strip()) >= 12), None)
    if first is None:
        return None
    chat['auto_title_attempted'] = True
    _save_chat(root, chat)
    latest = next((item for item in reversed(chat['transcript']) if item.get('role') == 'user'), {})
    saved = chat.get('assistant_state') or {}
    if latest.get('compile_job') or latest.get('local_conversion') or ((saved.get('previous_markdown') or saved.get('conversion_style_mode'))
                                         and saved.get('query') == latest.get('content')):
        return None
    # The existing prompt preview is enough for a local file conversion.
    query = first['content'].strip()
    if markdown_conversion_request(query, first.get('files') or []):
        return None
    return title_material(root, query, first.get('files') or [])


TITLE_WORDS_BYTES, TITLE_DOCUMENTS_BYTES, TITLE_JSON_BYTES = 1400, 900, 2500
# Where LaTeX source pasted into a message begins: a line that opens with a preamble command.
_LATEX_SOURCE = re.compile(r'^[ \t]*\\(?:documentclass|usepackage|newcommand|renewcommand|providecommand|def\b|input\b|'
                           r'title\b|begin\{document\})', re.M)


def _prefix(text, size):
    """The first `size` UTF-8 bytes of `text`, never cutting a character."""
    return text.encode('utf-8')[:size].decode('utf-8', errors='ignore')


def title_material(root, message, files=()):
    """What the conversation-title call reads: the user's own words (their first 1400 bytes, before any LaTeX source
    pasted into the message), then the title and abstract of each LaTeX document the message pastes or attaches (about
    900 bytes in all). A preamble of packages and macros says nothing a title can use, and sent alone it made DeepSeek
    write titles in Chinese. The message's first 1400 bytes, as before, are sent only when there is nothing else. The
    whole stays within the DeepInfra title bound: at most 2500 bytes once JSON-escaped."""
    start = _LATEX_SOURCE.search(message)
    words = (message[:start.start()] if start else message).strip()
    sources = [('', message[start.start():])] if start else []
    workspace = Workspace(root)
    for name in list(files)[:5]:
        if source_suffix(name) == '.tex':
            try:
                sources.append((Path(name).name, workspace.text(workspace.path(name))))
            except (OSError, ValueError, UnicodeError):
                continue  # an unreadable attachment only leaves the title less informed
    documents = []
    for name, text in sources:
        found = scan(text)
        parts = [f'title: {found["title"]}'] * bool(found['title']) + [f'abstract: {found["abstract"]}'] * bool(found['abstract'])
        if parts:
            documents.append('Document' + (f' {name}' if name else '') + ': ' + '. '.join(parts))
    if not words and not documents:
        return _prefix(message, TITLE_WORDS_BYTES)
    material, room = _prefix(words, TITLE_WORDS_BYTES), TITLE_DOCUMENTS_BYTES
    for line in documents:
        piece = _prefix(line, room)
        if piece:
            material += ('\n\n' if material else '') + piece
            room -= len(piece.encode('utf-8'))
    while len(json.dumps(material, ensure_ascii=False).encode('utf-8')) > TITLE_JSON_BYTES:
        material = _prefix(material, len(material.encode('utf-8')) - 100)  # backslashes count twice once escaped
    return material


def load_chat(root, chat_id):
    if not isinstance(chat_id, str) or not _ID.fullmatch(chat_id):
        raise ValueError('Invalid chat ID')
    base = _chats(root)
    if base is None or not (base / (chat_id + '.json')).exists():
        raise NotFound('Unknown chat')
    chat = _read_chat(base / (chat_id + '.json'))
    if (not isinstance(chat, dict) or chat.get('version') != 1 or chat.get('id') != chat_id
            or chat.get('mode') not in SAVED_CHAT_MODES or not isinstance(chat.get('history'), list)
            or not isinstance(chat.get('transcript'), list) or not isinstance(chat.get('settings'), dict)):
        raise ValueError('Invalid or unsupported chat file')
    # Preserve the maximum budget of route cards saved before the effort rename.
    for item in chat['transcript']:
        if isinstance(item, dict) and item.get('role') == 'route' and item.get('effort') == 'brezis':
            item['effort'] = 'poincare'
    return chat


@_locked_chat
def delete_chat(root, chat_id):
    """Remove one saved conversation, preserving jobs and workspace sources.

    Chat calls have no separate disk streams: their transcript and Assistant
    checkpoint live in this JSON. Its worker answers and investigation records
    go with it. Proof/research stream files belong to jobs.
    The Hub guards active work before calling this storage operation.
    """
    if not isinstance(chat_id, str) or not _ID.fullmatch(chat_id):
        raise ValueError('Invalid chat ID')
    private = Path(root) / '.mathagent'
    # Check dangling directory links too; _chats normally treats an absent
    # chats directory as a workspace with no saved conversations.
    if private.is_symlink() or (private / 'chats').is_symlink():
        raise ValueError('Refusing symlink in chat storage')
    base = _chats(root)
    if base is None:
        raise NotFound('Unknown chat')
    path = base / (chat_id + '.json')
    try:
        path.lstat()
    except FileNotFoundError:
        raise NotFound('Unknown chat') from None
    _regular(path)
    load_chat(root, chat_id)  # Validate the record's identity before deletion.
    answers = private / 'answers'
    if answers.is_symlink():
        raise ValueError('Refusing symlink in answer storage')
    folder = answers / chat_id
    if folder.exists() or folder.is_symlink():
        _directory(folder)
        shutil.rmtree(folder)
    from ..investigation import delete_records
    delete_records(root, chat_id)
    path.unlink()
    _sync_directory(base)
    with _cache_lock:
        for key in [key for key in _cache if key[0] == str(path)]:
            del _cache[key]
    return {'id': chat_id, 'deleted': True}


@_locked_chat
def update_chat_settings(root, chat_id, *, think=None, online=None):
    chat = load_chat(root, chat_id)
    if think is not None:
        chat['settings']['think'] = bool(think)
    if online is not None:
        chat['settings']['online'] = bool(online)
    _save_chat(root, chat)
    return chat


def list_chats(root, templates_root=None):
    base = _chats(root)
    if base is None:
        return []
    chats = []
    preferences = personalization.load(templates_root or root)
    def load(path):
        chat = load_chat(root, path.name[:-5])
        chat['_display_macros'] = chat_macros(root, chat, templates_root, preferences=preferences)
        return chat
    for path in base.iterdir():
        if not path.name.endswith('.json') or not _ID.fullmatch(path.name[:-5]) or path.is_symlink():
            continue
        try:
            chat = _cached(path, lambda: load(path), preferences['revision'])
        except (OSError, ValueError, NotFound):
            continue
        last = next((item['content'] if item.get('role') != 'route' else
                     f"{ROUTE_NAMES.get(item.get('mode'), 'Suggestion')}: {item.get('request', '')}"
                     for item in reversed(chat['transcript'])
                     if (item.get('role') in ('assistant', 'review') and item.get('content'))
                     or (item.get('role') == 'route' and item.get('status') not in ('dismissed',))), '')
        chats.append({'id': chat['id'], 'kind': chat['mode'], 'title': chat['title'] or 'New conversation',
                      'project_id': chat.get('project_id'),
                      'updated_at': chat['updated_at'], 'created_at': chat['created_at'],
                      'agent_statuses': [{k: lane[k] for k in ('id', 'mode', 'status')}
                                         for lane in agent_views.agents(chat, current_only=True)]
                      if (chat.get('assistant_state') or {}).get('status') == 'running' else [],
                      'messages': sum(1 for item in chat['transcript'] if item.get('role') == 'user'),
                      'preview': one_line(last, 140), 'math_macros': chat['_display_macros']})
    return sorted(chats, key=lambda chat: chat['updated_at'], reverse=True)


@_locked_chat
def commit_turn(root, chat_id, history, calls, notices, echo_user=True, mode=None, *, files=(), attached_files=(), user_content=None,
                project_snapshot=None, image_refs=(), job_id=None):
    """Save a completed turn: exact model context plus an append-only transcript."""
    chat = load_chat(root, chat_id)
    start = max(i for i, m in enumerate(history) if m.get('role') == 'user')
    calls = [call for call in calls if not call.get('discarded')]
    stamp, index, items, figures = _now(), 0, [], []
    for message in history[start:]:
        if message['role'] != 'tool' and figures:
            # Sandbox figures follow the whole block of tool results, keeping calls and results paired.
            items.extend(figures)
            figures = []
        if message['role'] == 'user' and not echo_user:
            continue  # Default mode already shows the message and its route card
        item = {'role': message['role'], 'content': message.get('content', ''), 'time': stamp}
        if message['role'] == 'user':
            item['files'] = list(files)
            item['attached_files'] = list(attached_files)
            if image_refs:
                item['images'] = [dict(ref) for ref in image_refs]
            if project_snapshot is not None:
                item['project_snapshot'] = project_snapshot
            if user_content is not None:
                item['content'] = user_content
        if mode and message['role'] != 'user':
            item['mode'] = mode  # which workflow answered (Default mode mixes them)
        if message['role'] == 'assistant':
            if job_id and mode == 'check':
                item['job_id'] = job_id
            if message.get('tool_calls'):
                item['tool_calls'] = message['tool_calls']
            if index < len(calls):
                item['thinking'], item['stats'] = calls[index]['thinking'], calls[index]['stats']
            index += 1
        elif message['role'] == 'tool':
            item['tool_name'] = message.get('tool_name', '')
            experiment = _tool_experiment(message)
            if experiment is not None:
                figures.append(experiment)
        items.append(item)
    items.extend(figures)
    if notices:
        items.append({'role': 'notice', 'content': '\n'.join(dict.fromkeys(notices)), 'time': stamp})
    # The model context may drop old turns to fit; the transcript never does.
    chat['history'] = history
    chat['transcript'].extend(items)
    if not chat['title']:
        chat['title'] = one_line(history[start]['content'] if user_content is None else user_content, 80)
    _save_chat(root, chat)


@_locked_chat
def commit_review(root, chat_id, result, calls, notices):
    chat = load_chat(root, chat_id)
    # Same history record as the terminal's /review.
    chat['history'].extend([{'role': 'user', 'content': 'Critique the preceding answer.'},
                            {'role': 'assistant', 'content': result}])
    item = {'role': 'review', 'content': result, 'time': _now(),
            'thinking': ''.join(call['thinking'] for call in calls),
            'stats': calls[-1]['stats'] if calls else {}}
    chat['transcript'].append(item)
    if notices:
        chat['transcript'].append({'role': 'notice', 'content': '\n'.join(dict.fromkeys(notices)), 'time': item['time']})
    _save_chat(root, chat)


@_locked_chat
def record_discarded(root, chat_id, content, reason, *, files=(), attached_files=(), image_refs=()):
    """Keep an unfinished turn visible without adding it to the model context."""
    chat = load_chat(root, chat_id)
    stamp = _now()
    if content:
        chat['transcript'].append({'role': 'user', 'content': content, 'time': stamp, 'discarded': True,
                                   'files': list(files), 'attached_files': list(attached_files),
                                   'images': [dict(ref) for ref in image_refs]})
        if not chat['title']:
            chat['title'] = one_line(content, 80)
    chat['transcript'].append({'role': 'notice', 'content': 'Not added to the conversation: ' + reason,
                               'time': stamp, 'discarded': True})
    _save_chat(root, chat)


@_locked_chat
def append_items(root, chat_id, items):
    """Append display items (Default mode messages and route cards); returns the first index."""
    chat = load_chat(root, chat_id)
    index = len(chat['transcript'])
    chat['transcript'].extend(items)
    if not chat['title']:
        first = next((item['content'] for item in items if item.get('role') == 'user'), '')
        chat['title'] = one_line(first, 80) if first else ''
    _save_chat(root, chat)
    return index


@_locked_chat
def start_worker_card(root, chat_id, item, index):
    """Recover the first card if a crash preceded the checkpoint of its transcript index."""
    chat = load_chat(root, chat_id)
    items = chat['transcript']
    if index is not None and (type(index) is not int or not 0 <= index < len(items)):
        raise NotFound('Unknown worker card')
    if index is None:
        index = next((i for i, old in enumerate(items) if old.get('role') == 'route'
                      and old.get('child_id') == item['child_id']), len(items))
    if index == len(items):
        items.append(item)
    else:
        old = items[index]
        if (old.get('role') != 'route' or old.get('child_id', item['child_id']) != item['child_id']
                or (old.get('mode'), old.get('request')) != (item['mode'], item['request'])):
            raise ValueError('Saved worker points to a different card')
        old.update(status='started', task=item['task'], status_time=item['time'], ended_at=None,
                   outcome='', error=None, error_code=None, agent_error=None, native_status=None,
                   child_id=item['child_id'], turn_id=item['turn_id'], batch_id=item['batch_id'])
    _save_chat(root, chat)
    return index, items[index].get('job_id')


@_locked_chat
def update_item(root, chat_id, index, **fields):
    chat = load_chat(root, chat_id)
    if type(index) is not int or not 0 <= index < len(chat['transcript']):
        raise NotFound('Unknown conversation item')
    item = chat['transcript'][index]
    status = fields.get('status')
    if item.get('role') == 'route' and status and status != item.get('status'):
        fields['status_time'] = _now()
        if status == 'started':
            fields.setdefault('started_at', item.get('started_at') or fields['status_time'])
            fields['ended_at'] = None
        elif status in ('done', 'failed', 'stopped', 'cancelled', 'dismissed'):
            fields['ended_at'] = fields['status_time']
    item.update(fields)
    _save_chat(root, chat)
    return chat['transcript'][index]


def _archive_experiments(chat, next_state):
    """Keep a replaced turn's sandbox runs on its worker cards, for the research index (_chat_evidence).

    An interrupted worker has no job or answer that would otherwise link them.
    """
    previous = chat.get('assistant_state') or {}
    if not previous.get('id') or previous['id'] == next_state.get('id'):
        return
    for child in previous.get('children') or []:
        index, runs = child.get('card_index'), child.get('experiment_ids')
        if not runs or type(index) is not int or not 0 <= index < len(chat['transcript']):
            continue
        card = chat['transcript'][index]
        if card.get('role') == 'route' and card.get('child_id') in (None, child.get('id')):
            card['experiment_ids'] = list(dict.fromkeys([*(card.get('experiment_ids') or []), *runs]))


@_locked_chat
def save_assistant(root, chat_id, state):
    """Checkpoint the parent and its child identities in the same atomic chat file."""
    chat = load_chat(root, chat_id)
    _archive_experiments(chat, state)
    agent_views.archive_turn(chat, state)
    chat['assistant_state'] = state
    _save_chat(root, chat)


@_locked_chat
def checkpoint_investigation(root, chat_id, state):
    """Commit the final report and checkpoint together, including after a crash or a repeated stop."""
    chat = load_chat(root, chat_id)
    _archive_experiments(chat, state)
    agent_views.archive_turn(chat, state)
    chat['assistant_state'] = state
    inv = state['investigation']
    record_id = inv['id']
    ident = 'clarification_id' if inv['phase'] == 'clarification' else 'investigation_id'
    if state.get('status') == 'complete' and not any(
            record_id in (item.get('investigation_id'), item.get('clarification_id')) for item in chat['transcript']):
        answer = {'role': 'assistant', 'content': state['answer']}
        prefix = state.get('history_prefix_count', len(state.get('history', ())))
        user = next((item for item in state.get('messages', [])[prefix:] if item['role'] == 'user'),
                    {'role': 'user', 'content': state['query']})
        chat['history'] = list(state.get('history', ())) + [user, answer]
        chat['transcript'].append({**answer, 'mode': 'free', ident: record_id, 'time': _now(),
                                   'synthesis': inv.get('synthesis', ''), 'synthesis_steps': inv.get('synthesis_steps', [])})
        if not chat['title']:
            chat['title'] = one_line(state['query'], 80)
    _save_chat(root, chat)


def assistant_accounting(root, state, transcript=()):
    """Recover only independently identifiable usage for turns saved before measured totals existed."""
    budget = dict(state.get('budget') or {})
    if not budget or 'measured_tokens' in budget:
        return budget
    rejected = {entry['main_call']: reported_output(entry) for entry in state.get('rejected_tool_calls', [])}
    last = 0 if state.get('main_calls') in rejected else reported_output(state.get('last_call') or {})
    # A completed turn also committed its accepted main calls to the chat.
    # The last-call checkpoint may duplicate one of them. Rejected tool batches
    # have separate audit entries and were excluded from the committed turn.
    committed = 0
    if state.get('status') == 'complete':
        for item in reversed(transcript):
            if item.get('role') == 'user':
                if item.get('content') != state.get('query'):
                    committed = 0
                break
            if item.get('role') == 'assistant' and item.get('mode') == 'free' and not item.get('discarded'):
                committed += reported_output(item)
        else:
            committed = 0
    # The typed routing call is separately budgeted, outside the main-call ordinal.
    measured = max(last, committed) + sum(rejected.values()) + reported_output(state.get('proof_routing') or {})
    seen = set()
    for child in state.get('children', []) if root is not None else []:
        job = child.get('job_id')
        if not isinstance(job, str) or not _ID.fullmatch(job) or job in seen:
            continue
        seen.add(job)
        try:
            if (child.get('action') or {}).get('mode') == 'prove':
                directory = _proof_directory(root, job)
                journal = _cached(directory / 'state.json', lambda: ProofStore.load(root, job).state)
            else:
                directory = _job_directory(root, job)
                journal = _cached(directory / 'state.json', lambda: research_state(root, job, verify=False))
        except (OSError, ValueError, KeyError, NotFound):
            continue
        # A later turn can reopen a job. Do not attribute another turn's calls
        # to this one when the job no longer matches its saved worker charge.
        if journal['tokens_charged'] == (child.get('usage') or {}).get('tokens'):
            measured += sum(map(reported_output, journal['calls']))
    budget['measured_tokens'] = min(measured, max(0, budget.get('tokens', 0) - budget.get('reserved_tokens', 0)))
    return budget


def _agent_receipts(root, chat, lanes):
    """Fill available timing and counts of older workers from their immutable native identity."""
    for lane in lanes:
        if lane['ended_at'] is not None or lane['status'] not in ('finished', 'failed', 'stopped'):
            continue
        try:
            job_id = lane.get('job_id')
            answer_id = lane.get('answer_id')
            if answer_id:
                from ..workers import _answer_path
                path = _answer_path(root, chat['id'], answer_id)
                def load():
                    data = json.loads(_read(path))
                    if (data.get('id'), data.get('chat_id'), data.get('mode')) != (answer_id, chat['id'], lane['mode']):
                        raise ValueError('Worker identity differs')
                    return data.get('created'), {}, None
            elif isinstance(job_id, str) and _ID.fullmatch(job_id):
                folder = 'proofs' if lane['mode'] == 'prove' else 'checks' if lane['mode'] == 'check' else 'research'
                directory = _proof_directory(root, job_id) if folder == 'proofs' else _job_directory(root, job_id, folder)
                path = directory / 'state.json'
                def load():
                    data = ProofStore.load(root, job_id).state if folder == 'proofs' else _read_research(directory, job_id, verify=False)
                    usage = {dest: data[key] for key, dest in (('tokens_charged', 'output_tokens'),
                             ('input_tokens_charged', 'input_tokens'), ('seconds_used', 'seconds'))
                             if type(data.get(key)) in (int, float)}
                    return data.get('updated_at'), usage, agent_views._progress(data.get('progress'))
            else:
                continue
            ended, usage, progress = _cached(path, load, 'agent-receipt-v1')
            lane['ended_at'] = ended
            lane['usage'] = {**usage, **lane['usage']}
            lane['progress'] = lane['progress'] or progress
        except (LookupError, OSError, ValueError, KeyError, TypeError, AttributeError):
            continue


def budget_view(budget, running=False):
    reserved = budget.get('reserved_tokens', 0) if running else 0
    return {'tokens': output_totals(budget.get('tokens', 0), budget.get('max_tokens'),
                                    reserved, budget.get('measured_tokens', 0)),
            'input_tokens': _budget(budget.get('input_tokens', 0), budget.get('max_input_tokens')),
            'seconds': _budget(round(budget.get('seconds', 0), 1), budget.get('max_seconds', 900))}


def assistant_view(chat, root=None):
    state = chat.get('assistant_state') or {}
    status = state.get('status')
    status = 'paused' if status in ('interrupted', 'incomplete') else status
    budget = assistant_accounting(root, state, chat.get('transcript', ()))
    progress = state.get('progress') if status != 'set_aside' else None
    if not isinstance(progress, dict) and status != 'set_aside':
        children = state.get('children') or []
        result = children[-1].get('result') if children else None
        progress = result.get('progress') if isinstance(result, dict) and not result.get('error') else None
        if not isinstance(progress, dict):
            progress = None
    # Standard-reference answers are rendered directly from one completed
    # worker. Its inherited research-round counters describe an old phase,
    # not work still owed by this finished, bounded bibliographic lookup.
    source = state.get('answer_source') or {}
    if status == 'complete' and source.get('kind') == 'worker_result':
        child = next((child for child in state.get('children', [])
                      if child.get('job_id') == source.get('job_id')), None)
        if (child and child.get('status') == 'complete'
                and child.get('action', {}).get('mode') == 'check'
                and (child.get('result') or {}).get('disposition') == 'answer_with_qualification'):
            progress = None
    last_call = state.get('last_call') or {}
    context = _live_context(last_call) if status == 'running' and last_call.get('status') == 'running' else {}
    lanes = agent_views.agents(chat)
    if root is not None:
        _agent_receipts(root, chat, lanes)
    from ..mode_calls import waiting_routed_tasks
    waiting = [{'mode': t['mode'], 'objective': t['request']} for t in waiting_routed_tasks(state)] if status == 'running' else []
    return {'agents': lanes, 'waiting_for_results': waiting, 'agent_concurrency': (state.get('limits') or {}).get('concurrency'),
            'assistant_status': status,
            'assistant_resumable': status in ('paused', 'running'),
            'assistant_progress': progress,
            'assistant_budget': {**budget_view(budget, status == 'running'), **context}}


# -- workspace settings, folders, uploads and templates -------------------------------

def _private_json(path):
    if not path.exists():
        return None
    try:
        value = json.loads(_read(path))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def load_settings(workspace):
    value = _private_json(Path(workspace) / '.mathagent' / 'gui-settings.json') or {}
    saved = value.get('read') if isinstance(value.get('read'), dict) else {}
    return {'read': {group: saved.get(group) is not False for group in READ_GROUPS}}


def save_settings(workspace, read):
    if not isinstance(read, dict) or any(k not in READ_GROUPS or type(v) is not bool for k, v in read.items()):
        raise ValueError('read must map tex, pdf, py and text to true or false')
    settings = load_settings(workspace)
    settings['read'].update(read)
    base = Path(workspace) / '.mathagent'
    _directory(base, create=True)
    _atomic_write(base / 'gui-settings.json', json.dumps(settings, indent=1).encode())
    return settings


def read_types(workspace):
    return [group for group, allowed in load_settings(workspace)['read'].items() if allowed]


def resolve_folder(root, relative=''):
    """A folder at or below the interface root: no hidden parts, no symlink escape."""
    root = Path(root).resolve(strict=True)
    if not isinstance(relative, str) or len(relative) > 1000:
        raise ValueError('Folder path must be text')
    rel = Path(relative.strip('/')) if relative.strip('/') else Path('.')
    if rel.is_absolute() or any(part == '..' or (part.startswith('.') and part != '.') for part in rel.parts):
        raise ValueError('Folders are relative to the interface root; hidden folders are excluded')
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or any(part.startswith('.') for part in target.relative_to(root).parts):
        raise ValueError('That folder is outside the interface root')
    if not target.is_dir():
        raise NotFound('No such folder')
    return target


def _counts(folder):
    counts = {'tex': 0, 'pdf': 0, 'py': 0}
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return counts
    for entry in entries[:5000]:
        if entry.name.startswith('.') or not entry.is_file(follow_symlinks=False):
            continue
        suffix = source_suffix(entry.name)
        key = 'tex' if suffix in READ_GROUPS['tex'] else 'pdf' if suffix == '.pdf' else 'py' if suffix == '.py' else None
        if key:
            counts[key] += 1
    return counts


def list_folders(root, relative=''):
    root = Path(root).resolve(strict=True)
    target = resolve_folder(root, relative)
    folders = []
    for entry in sorted(os.scandir(target), key=lambda e: e.name.lower()):
        if (entry.name.startswith('.') or entry.name in SKIP or entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)):
            continue
        path = Path(entry.path)
        folders.append({'name': entry.name, 'path': path.relative_to(root).as_posix(), 'files': _counts(path),
                        'jobs': (path / '.mathagent' / 'proofs').is_dir() or (path / '.mathagent' / 'research').is_dir()})
        if len(folders) >= 500:
            break
    rel = target.relative_to(root).as_posix()
    return {'root': str(root), 'path': '' if rel == '.' else rel, 'files': _counts(target), 'folders': folders}


def create_folder(root, relative, name):
    parent = resolve_folder(root, relative)
    if (not isinstance(name, str) or not name.strip() or len(name) > 100 or name.strip().startswith('.')
            or any(c in name for c in '/\\\0')):
        raise ValueError('Folder names cannot be empty, hidden, or contain slashes')
    target = parent / name.strip()
    target.mkdir()
    return target.relative_to(Path(root).resolve()).as_posix()


def remember_folder(root, relative):
    base = Path(root) / '.mathagent'
    _directory(base, create=True)
    state = _private_json(base / 'gui-state.json') or {}
    recent = [relative] + [r for r in state.get('recent', []) if isinstance(r, str) and r != relative]
    state['recent'] = recent[:8]
    _atomic_write(base / 'gui-state.json', json.dumps(state, indent=1).encode())


def recent_folders(root):
    state = _private_json(Path(root) / '.mathagent' / 'gui-state.json') or {}
    return [r for r in state.get('recent', []) if isinstance(r, str)][:8]


def save_upload(workspace, name, data):
    """Save a dropped file in the workspace; never replaces an existing file."""
    workspace = Path(workspace).resolve(strict=True)
    base = re.sub(r'[^\w.\- ()+]', '_', str(name).replace('\\', '/').split('/')[-1]).strip()
    archive = is_source_archive(base)
    suffix = next((ext for ext in ('.tar.gz', '.tgz', '.tar', '.gz') if base.lower().endswith(ext)), '') if archive else Path(base).suffix.lower()
    if not base or base.startswith('.') or not archive and suffix not in UPLOAD_SUFFIXES:
        raise ValueError('Drop text/LaTeX, a TeX source archive (.tar.gz/.tgz/.tar/.gz), PDF, Python or PNG/JPEG/WebP image files')
    if len(data) > UPLOAD_BYTES:
        raise ValueError('Files are limited to 20 MB')
    if archive:
        from ..arxiv_source import extract_source
        extract_source(data, deadline=time.monotonic() + 10)  # Validate before publishing any workspace file.
    if not archive and suffix not in BINARY_UPLOADS and b'\x00' in data:
        raise ValueError('That file is not text')
    stem, number = base[:-len(suffix)] if archive else Path(base).stem, 2
    target = workspace / base
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=workspace)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        while True:
            try:
                os.link(temporary, target)  # link() never replaces an existing name
                break
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS):
                        raise
                    try:
                        exclusive = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    except FileExistsError:
                        target = workspace / f'{stem}-{number}{suffix}'
                        number += 1
                        continue
                    with os.fdopen(exclusive, 'wb') as stream:
                        stream.write(data)
                    break
                target = workspace / f'{stem}-{number}{suffix}'
                number += 1
    finally:
        os.unlink(temporary)
    return target.name


def _template_dir(root, name):
    if not isinstance(name, str) or not re.fullmatch(r'[\w][\w .-]{0,59}', name):
        raise ValueError('Template names use letters, digits, spaces, dots and dashes (at most 60)')
    return Path(root) / '.mathagent' / 'templates' / name


def list_templates(root):
    base = Path(root) / '.mathagent' / 'templates'
    if not base.is_dir() or base.is_symlink():
        return []
    found = []
    for folder in sorted(base.iterdir()):
        if folder.is_dir() and not folder.is_symlink():
            files = sorted(p.name for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                           and p.suffix.lower() in TEMPLATE_SUFFIXES)
            found.append({'name': folder.name, 'files': files})
    return found


def save_template(root, workspace_obj, name, paths):
    """Copy template files from the workspace so they can be reused in any folder."""
    target = _template_dir(root, name)
    contents = []
    for path in paths:
        p = workspace_obj.path(path)
        if p.suffix.lower() not in TEMPLATE_SUFFIXES:
            raise ValueError('Template files must be .sty, .cls, .tex or .bib')
        contents.append((p.name, workspace_obj.text(p)))
    if not contents:
        raise ValueError('Choose the template files to remember')
    _directory(Path(root) / '.mathagent', create=True)
    _directory(target.parent, create=True)
    target.mkdir(exist_ok=True)
    for old in target.iterdir():
        if old.is_file() and not old.is_symlink():
            old.unlink()
    for file_name, content in contents:
        _atomic_write(target / file_name, content.encode('utf-8'))
    return {'name': name, 'files': [n for n, _ in contents]}


def load_template(root, name):
    folder = _template_dir(root, name)
    if not folder.is_dir() or folder.is_symlink():
        raise NotFound('Unknown template')
    return [(p.name, _read(p)) for p in sorted(folder.iterdir())
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in TEMPLATE_SUFFIXES]


def list_styles(root):
    """Saved writing styles; like templates, they are shared by every folder."""
    return [public_profile(profile) for profile in list_profiles(root)]


def set_style_template(root, workspace, name, paths):
    """Give a saved style the LaTeX template made of these workspace files (none removes it)."""
    try:
        return set_template(root, name, read_template(Workspace(workspace), paths))
    except FileNotFoundError:
        raise NotFound('Unknown style') from None


_RESULT_NAMES = {'prove': 'Proof', 'critic': 'Critique', 'explore': 'Exploration', 'check': 'Literature check',
                 'literature': 'Literature review', 'referee': 'Review', 'detailed_review': 'Review', 'typo_check': 'Typo check',
                 'quick_review': 'Quick review', 'explain': 'Explanation', 'writeup': 'Write-up', 'summarize': 'Summary'}
_PROOF_STATUS = {'candidate_complete': 'response reviewed; it may be a proof or counterexample', 'uncertain': 'the model review is uncertain',
                 'review_unavailable': 'not reviewed'}


def _saved_result(root, mode, job_id):
    """A finished job's final text from its saved state: the selected proof, the document or the report."""
    try:
        if mode == 'prove':
            detail = proof_detail(root, job_id)
            return detail.get('answer') or '', _PROOF_STATUS.get(detail['status'], detail['status'])
        if mode == 'check':  # literature checks are saved in their own folder; their text is the answer
            state = _read_research(_job_directory(root, job_id, 'checks'), job_id)
            return state.get('answer') or state.get('draft') or '', state['status']
        state = research_state(root, job_id)
        return state.get('document') or state.get('draft') or '', state['status']
    except (NotFound, OSError, ValueError, KeyError, TypeError, AttributeError):
        return '', ''


def previous_writeup_for_compile(root, chat, *, before=None):
    """A single delivered Write-Up in this conversation, across compile-only follow-ups."""
    cards = []
    for item in reversed(chat['transcript'][:before]):
        if item.get('role') == 'route':
            cards.append(item)
        elif item.get('role') == 'user':
            if cards:
                if len(cards) != 1 or cards[0].get('mode') not in ('writeup', 'summarize'):
                    return None
                card = cards[0]
                job = card.get('job_id')
                if not job or card.get('status') != 'done':
                    return None
                state = research_state(root, job)
                if state.get('kind') == 'writeup' and state.get('phase') == 'done' and state.get('document'):
                    return job
                return None
            if item.get('files') or item.get('images') or not compile_followup_request(item.get('content')):
                return None
    return None


def previous_writeup_source(root, chat, *, before=None):
    """Identify the previous turn's single deliverable, including any displayed experiment files."""
    transcript = chat['transcript'][:before]
    start = next((i for i in range(len(transcript) - 1, -1, -1) if transcript[i].get('role') == 'user'), -1)
    turn = list(enumerate(transcript[start + 1:], start + 1))
    if start < 0 or any(item.get('discarded') for _, item in turn):
        return None
    answers = [(i, item['content']) for i, item in turn if item.get('role') == 'assistant'
               and not item.get('tool_calls') and isinstance(item.get('content'), str) and item['content'].strip()]
    routes = [(i, item) for i, item in turn if item.get('role') == 'route']
    if len(answers) > 1:
        return None
    bibliography = ''
    origin = {'chat_id': chat['id']}
    final = turn[-1][1] if answers and turn[-1][0] == answers[0][0] else {}
    synthesis = final.get('synthesis')
    if final.get('investigation_id') and isinstance(synthesis, str) and synthesis.strip():
        # The final research answer is a single deliverable. Its child jobs and
        # technical evidence report are separate from the narrative shown in chat.
        origin['investigation_id'] = final['investigation_id']
        answers = [(answers[0][0], synthesis)]
        routes = []
    else:
        if (len(routes) > 1 or any(item.get('status') != 'done' or item.get('mode') in ('writeup', 'summarize')
                                  for _, item in routes)):
            return None
        experiments = _shown_experiments(item for _, item in turn)
        if experiments:
            return {'experiment_ids': experiments}
    if routes and routes[0][1].get('mode') == 'literature':
        index, route = routes[0]
        job = route.get('job_id')
        try:
            state = research_state(root, job)
            content = state.get('draft')
            if (state.get('kind') != 'literature' or state.get('phase') != 'done'
                    or state.get('status') not in ('complete', 'reviewed', 'partial') or not content
                    or (answers and answers[0][1] != content)):
                return None
            name = _review_name(state) + '.md'
            if (state.get('outputs') or {}).get('bib'):
                try:
                    _, data = research_review_file(root, job, 'bib')
                    bibliography = data.decode('utf-8')
                except NotFound:
                    # Conversation exports can omit the derived file but retain
                    # the exact ordered records from which its worker rendered it.
                    if state.get('pipeline') != ReviewRunner.PIPELINE:
                        return None
                    review = ReviewRunner(None)
                    review.state = state
                    bibliography = review._bibtex()
        except (NotFound, OSError, ValueError, TypeError, KeyError):
            return None
        origin['job_id'] = job
    elif answers:
        index, content = answers[0]
        name = 'previous-answer.md'
    else:
        return None
    if len(content.encode()) + len(bibliography.encode()) > 2_000_000:
        raise WorkerInputError('The selected answer and bibliography exceed Write-Up’s 2 MB pinned-source limit. '
                               'Split the Markdown into smaller files to convert it.', code='writeup_source_too_large')
    origin['transcript_index'] = answers[0][0] if answers else index
    source = {'id': 'M1', 'path': name, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
              'origin': origin}
    return {'source': source, 'bibliography': bibliography}


def previous_markdown(root, chat, *, before=None):
    snapshot = previous_writeup_source(root, chat, before=before)
    return snapshot if snapshot and 'source' in snapshot else None


def previous_markdown_for_job(root, state):
    """Resolve a pre-upgrade Write-Up card from the transcript that owns that job."""
    if (state.get('conversion_source') or state.get('kind') != 'writeup'
            or state.get('phase') not in ('outline', 'skeleton', 'draft')
            or not previous_markdown_request(state['goal'], style_mode=(state.get('style_choice') or {}).get('mode', 'auto'))):
        return None
    base = _chats(root)
    if base is None:
        return None
    resolved = None
    for path in base.glob('*.json'):
        if not _ID.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            chat = load_chat(root, path.stem)
        except (OSError, ValueError, NotFound):
            continue
        user = None
        for index, item in enumerate(chat['transcript']):
            if item.get('role') == 'user':
                user = index
            elif (user is not None and item.get('role') == 'route' and item.get('mode') == 'writeup'
                  and item.get('job_id') == state['id']):
                request = chat['transcript'][user]
                if request.get('content') == state['goal'] and not request.get('files'):
                    snapshot = previous_markdown(root, chat, before=user)
                    if can_recover_previous_markdown(state, snapshot):
                        if resolved is not None and snapshot != resolved:
                            return None
                        resolved = snapshot
    return resolved


def conversation_experiments(root, chat_id, before=None):
    """Only the sandbox runs shown in this conversation, in transcript order."""
    return _shown_experiments(load_chat(root, chat_id)['transcript'][:before])


def _shown_experiments(items):
    return list(dict.fromkeys(item['experiment'] for item in items
                              if item.get('role') == 'experiment' and isinstance(item.get('experiment'), str)
                              and _EXPERIMENT.fullmatch(item['experiment'])))


def conversation_references(root, chat_id, before=None, skip=None):
    """Metadata for selected works actually identified in this conversation's material."""
    chat = load_chat(root, chat_id)
    mentioned = entry_identifiers(conversation_record(root, chat_id, before=before, skip=skip))
    references, seen = [], set()
    for index, item in enumerate(chat['transcript'][:before]):
        if index == skip or item.get('role') != 'route' or item.get('status') != 'done':
            continue
        mode, job_id = item.get('mode'), item.get('job_id')
        if mode not in ('literature', 'check') or not isinstance(job_id, str):
            continue
        try:
            state = _read_research(_job_directory(root, job_id, 'checks' if mode == 'check' else 'research'), job_id)
        except (NotFound, OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if state.get('kind') != mode:
            continue
        if mode == 'literature':
            if state.get('pipeline') == ReviewRunner.PIPELINE:
                # A review's reference [k] is pool number bibliography[k-1].
                bibliography = state.get('bibliography')
                numbers = list(bibliography) if isinstance(bibliography, list) else []
            else:  # an old reading list: its entry points and themes
                organisation = state.get('organisation')
                if not isinstance(organisation, dict):
                    continue
                numbers = list(organisation.get('entry') or []) if isinstance(organisation.get('entry'), list) else []
                themes = organisation.get('themes') or []
                for theme in themes if isinstance(themes, list) else []:
                    if isinstance(theme, dict):
                        for key in ('core', 'deeper'):
                            if isinstance(theme.get(key), list):
                                numbers.extend(theme[key])
            pool = state.get('pool') or []
            records = {record['n']: record for record in pool if isinstance(record, dict) and type(record.get('n')) is int} \
                if isinstance(pool, list) else {}
            chosen = [records[n] for n in dict.fromkeys(n for n in numbers if type(n) is int) if n in records]
        else:
            candidates = state.get('candidates') or []
            chosen = []
            for candidate in candidates if isinstance(candidates, list) else []:
                if not isinstance(candidate, dict) or not isinstance(candidate.get('verdict'), dict):
                    continue
                verdict = candidate['verdict']
                if verdict.get('level') in ('located', 'read', 'cited') and (
                        candidate.get('selected') is True or verdict['level'] in ('read', 'cited')):
                    chosen.append(verdict.get('record'))
        for record in chosen:
            if not isinstance(record, dict) or not isinstance(record.get('title'), str) or not record['title'].strip():
                continue
            arxiv, zbl = record.get('arxiv_id') or record.get('arxiv'), record.get('zbl')
            identity = [record.get(key) for key in ('identifier', 'doi', 'url')]
            identity += ['arXiv:' + arxiv] if isinstance(arxiv, str) else []
            identity += ['Zbl ' + zbl] if isinstance(zbl, str) else []
            identifiers = entry_identifiers('\n'.join(value for value in identity if isinstance(value, str)))
            if not identifiers.intersection(mentioned) or identifiers.intersection(seen):
                continue
            authors = record.get('authors') or []
            if not isinstance(authors, list) or any(not isinstance(name, str) for name in authors):
                continue
            normalized = {key: record[key] for key in ('title', 'type', 'venue', 'publisher', 'volume', 'issue', 'pages', 'url')
                          if isinstance(record.get(key), str) and record[key].strip()}
            normalized.update(authors=list(authors), identifier=sorted(identifiers)[0])
            if type(record.get('year')) in (int, str):
                normalized['year'] = record['year']
            for prefix, key in (('doi:', 'doi'), ('arxiv:', 'arxiv_id'), ('zbl:', 'zbl')):
                value = next((identifier[len(prefix):] for identifier in sorted(identifiers) if identifier.startswith(prefix)), None)
                if value:
                    normalized[key] = value
            references.append(normalized)
            seen.update(identifiers)
    return references


def _card_answer(root, chat, card):
    from ..workers import _answer_path
    from ..mode_calls import USER_MODES
    if card.get('mode') not in ('explore', *USER_MODES):
        raise NotFound('No small worker answer for this card')
    ident = card['answer_id']
    path = _answer_path(root, chat['id'], ident)
    if not path.exists():
        raise NotFound('Saved worker answer is unavailable')
    record = json.loads(_read(path))
    answer = record.get('answer')
    if (record.get('id') != ident or record.get('chat_id') != chat['id'] or record.get('mode') != card['mode']
            or record.get('request') != card.get('request') or not isinstance(answer, str)
            or not isinstance(record.get('status'), str)):
        raise ValueError('Saved answer identity differs from its conversation card')
    for child in (chat.get('assistant_state') or {}).get('children', []):
        if ident in (child.get('job_id'), child.get('native_id')) and record.get('child_id') != child.get('id'):
            raise ValueError('Saved answer identity differs from its worker')
    digest = hashlib.sha256(answer.encode()).hexdigest()
    if any(known is not None and known != digest for known in (record.get('answer_sha256'), card.get('answer_sha256'))):
        raise ValueError('Saved answer differs from its recorded digest')
    return {'answer': answer, 'status': record['status']}


def _has_chat_job(root, chat_id, folder, job_id):
    chat = load_chat(root, chat_id)
    links = _chat_evidence(chat)
    if (folder, job_id) in links:
        return True
    from ..project_index import collect
    return any((folder, job_id) in e.get('links', []) and
               (e.get('chat_id') == chat_id or (e['folder'], e['job_id']) in links) for e in collect(root))


def saved_chat_experiment(root, chat_id, experiment_id):
    """Read native or retained numerical evidence only if this conversation produced or cited the run."""
    from ..project_index import collect, read_text
    if not isinstance(experiment_id, str) or not _EXPERIMENT.fullmatch(experiment_id):
        raise ValueError('Invalid experiment ID')
    if not _has_chat_job(root, chat_id, 'experiments', experiment_id):
        raise NotFound('Unknown experiment in this chat')
    entry = next((e for e in collect(root) if e['folder'] == 'experiments' and e['job_id'] == experiment_id), None)
    if not entry:
        raise NotFound('The saved experiment is no longer available')
    return {'answer': read_text(root, {'entries': [entry]}, entry['id']), 'status': entry['status']}


def saved_chat_check(root, chat_id, job_id):
    """Read a check through its originating conversation, including a recorded nested check."""
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise ValueError('Invalid check ID')
    if not _has_chat_job(root, chat_id, 'checks', job_id):
        raise NotFound('Unknown check in this chat')
    directory = _job_directory(root, job_id, 'checks')
    record = _read_research(directory, job_id)
    if record.get('kind') != 'check':
        raise NotFound('Unknown check in this chat')
    report = directory / 'report.md'
    return {'answer': _read(report) if report.exists() else record.get('answer') or '',
            'status': record['status']}


def saved_chat_answer(root, chat_id, answer_id):
    """Read a durable worker answer through the matching card in its owning chat.

    A failed card that names an answer keeps the partial text its worker wrote.
    """
    if not isinstance(answer_id, str) or not _ID.fullmatch(answer_id):
        raise ValueError('Invalid answer ID')
    chat = load_chat(root, chat_id)
    card = next((item for item in chat['transcript'] if item.get('role') == 'route'
                 and item.get('status') in ('done', 'failed') and item.get('answer_id') == answer_id), None)
    if card is None:
        raise NotFound('Unknown answer in this chat')
    return _card_answer(root, chat, card)


def conversation_record(root, chat_id, before=None, skip=None):
    """An Assistant conversation as one text source for a write-up or a summary.

    In the order the user saw it: their messages, the answers, and the full final
    text of each finished job, and sandbox code, output and figure names.
    Reading and web tools are left out: their output is source material, not a result.
    An answer that repeats a job's result verbatim (a literature review) appears once, as that result.
    `before` stops at that transcript index; `skip` leaves out one item (the current request, so that
    results finished earlier in the same turn still count).
    """
    chat = load_chat(root, chat_id)
    delegated, calls = {}, {}
    for message in chat.get('history') or []:
        for call in message.get('tool_calls') or []:
            calls[call.get('id')] = call.get('function') or {}
        if message.get('role') != 'tool' or message.get('tool_name') != 'delegate':
            continue
        try:
            value = json.loads(message.get('content') or '')
        except ValueError:
            continue
        arguments = (calls.get(message.get('tool_call_id')) or {}).get('arguments') or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        if isinstance(value, dict) and isinstance(value.get('result'), dict):
            delegated.setdefault(value.get('mode') or arguments.get('mode'), []).append(value)
    parts, results = [], set()
    for index, item in enumerate(chat['transcript'][:before]):
        if index == skip:
            continue
        role, content = item.get('role'), (item.get('content') or '').strip()
        if role == 'user' and content:
            files = item.get('files') or []
            parts.append('## User' + (f' (attached: {", ".join(files)})' if files else '') + '\n\n' + content)
        elif role == 'assistant' and content and content.strip() not in results:
            parts.append('## Assistant\n\n' + content)
        elif role == 'experiment':
            from ..numerics.artifacts import ExperimentStore
            try:
                record = ExperimentStore(root).load(item.get('experiment'))
            except (ValueError, OSError):
                parts.append('## Sandbox result\n\nThe saved experiment is no longer available.')
                continue
            figures = [a['name'] for a in record.get('artifacts', []) if Path(a.get('name', '')).suffix.lower()
                       in ('.png', '.pdf', '.jpg', '.jpeg')]
            parts.append(f'## Sandbox result ({record["status"]})\n\nExecuted Python code (seed: {record.get("seed")}):\n\n'
                         + '```python\n' + record['code'] + '\n```\n\nOutput:\n' + record.get('stdout', '')
                         + '\nFigures available to the document: ' + ', '.join(figures))
        elif role == 'route' and item.get('status') == 'done':
            mode = item.get('mode')
            # The worker's own result when the Assistant ran it; a suggested job's saved state otherwise.
            queue = delegated.get(mode, [])
            value = next((v for v in queue if item.get('job_id') and v.get('job_id') == item['job_id']), None)
            if value is None and not item.get('job_id') and queue:
                value = queue[0]
            if item.get('answer_id'):
                # A frozen worker receipt may outlive the tool result in the model's history.
                try:
                    saved = _card_answer(root, chat, item)
                except (NotFound, OSError, ValueError, KeyError, TypeError, AttributeError):
                    parts.append('## Worker answer unavailable\n\nThe saved answer could not be verified.')
                    continue
                text, status = saved['answer'], saved['status']
                if value is not None and value.get('job_id') == item['answer_id']:
                    queue.remove(value)
            elif value is not None:
                queue.remove(value)
                result = value['result']
                text = result.get('answer') or ''
                status = _PROOF_STATUS.get(result.get('review_status'), result.get('status') or '')
            elif item.get('job_id'):
                text, status = _saved_result(root, mode, item['job_id'])
            else:
                text, status = '', ''
            if mode in ('writeup', 'summarize'):
                # A document about the results above, not a further result.
                if text:
                    parts.append(f'## {_RESULT_NAMES[mode]} saved\n\nA {_RESULT_NAMES[mode].lower()} of the results above was written.')
            elif isinstance(text, str) and text.strip():
                results.add(text.strip())
                parts.append(f'## {_RESULT_NAMES.get(mode, mode)} result' + (f' ({status})' if status else '')
                             + '\n\nTask: ' + ' '.join(str(item.get('request') or '').split())[:1500] + '\n\n' + text.strip())
    return '\n\n'.join(parts)


def style_choice(root):
    """Which saved style write-ups use."""
    return load_choice(root)


def chat_macros(root, chat, base=None, *, preferences=None):
    """Display notation from the selected style, templates, attachments and user requests."""
    base = base or root
    profiles, choice = list_profiles(base), load_choice(base)
    profile = next((p for p in profiles if p['name'] == choice.get('style')), None) if choice['mode'] in ('style', 'domain') else None
    if choice['mode'] == 'auto' and len(profiles) == 1:
        profile = profiles[0]
    texts = []
    if profile:
        domain = next((d for d in profile['domains'] if d['name'] == choice.get('domain')), None)
        articles = set(domain['articles']) if domain else None
        texts.extend(m.get('definition', '') for m in profile['macros']
                     if articles is None or articles.intersection(m.get('articles', [])))
        texts.extend(f.get('content', '') for f in profile.get('template', []))
    templates = list_templates(base)
    if len(templates) == 1:
        texts.extend(content for _, content in load_template(base, templates[0]['name']))
    defaults = personalization.definitions((preferences or personalization.load(base))['macros'])
    definitions = {}
    for text in texts:
        definitions.update(macro_definitions(text))
    definitions.update(defaults)
    texts.clear()
    workspace = Workspace(root)
    files = list(dict.fromkeys(name for item in chat['transcript'] if item.get('role') in ('user', 'route')
                               for name in item.get('files') or []))
    for name in files[-20:]:
        try:
            path = workspace.path(name)
            if source_suffix(path) in ('.tex', '.sty', '.cls'):
                texts.append(workspace.text(path))
        except (OSError, ValueError, UnicodeError):
            continue
    texts.extend(item.get('content', '') for item in chat['transcript'] if item.get('role') == 'user')
    for text in texts:
        definitions.update(macro_definitions(text))
    return dict(list(definitions.items())[-200:])


def set_style_choice(root, mode, style=None, domain=None):
    try:
        return save_choice(root, mode, style, domain)
    except FileNotFoundError:
        raise NotFound('Unknown style') from None


def _chat_evidence(chat):
    """Native identifiers only. Use the stored transcript, which survives history compaction."""
    jobs = set()

    def link(mode, value):
        if not isinstance(value, str):
            return
        if mode == 'experiment' and _EXPERIMENT.fullmatch(value):
            jobs.add(('experiments', value))
        elif _ID.fullmatch(value):
            folder = {'prove': 'proofs', 'check': 'checks', 'answer': 'answers'}.get(mode, 'research')
            jobs.add((folder, value))

    for item in chat['transcript']:
        if not isinstance(item, dict):
            continue
        role, mode = item.get('role'), item.get('mode')
        if role == 'route':
            link(mode, item.get('job_id'))
            link('answer', item.get('answer_id'))
            for run in item.get('experiment_ids') or []:  # an earlier turn's worker (_archive_experiments)
                link('experiment', run)
        elif role == 'assistant' and mode == 'check':
            if item.get('job_id'):
                link('check', item['job_id'])
            else:
                # Legacy check chats carried the native ID in the controller-generated footer.
                match = re.search(r'(?:^|\n)Full log: `\.mathagent/checks/([a-f0-9-]{36})/report\.md`\s*\Z',
                                  str(item.get('content') or ''))
                if match:
                    link('check', match[1])
        elif role == 'experiment':
            link('experiment', item.get('experiment'))
        elif role == 'tool' and item.get('tool_name') in ('check_reference', 'execute_python'):
            try:
                data = json.loads(item['content'])
            except (ValueError, TypeError, KeyError):
                continue
            if isinstance(data, dict):
                if item['tool_name'] == 'check_reference':
                    link('check', data.get('check_id'))
                else:
                    result = data.get('sandbox_result') or data
                    if isinstance(result, dict):
                        link('experiment', result.get('experiment_id'))
    parent = chat.get('assistant_state') or {}
    for child in (parent.get('children') or []):
        action = child.get('action') or {}
        link(action.get('mode') or child.get('mode'), child.get('job_id'))
        # Sandbox runs of a worker, including one interrupted before it saved an answer.
        for run in child.get('experiment_ids') or []:
            link('experiment', run)
    for task in ((parent.get('investigation') or {}).get('tasks') or {}).values():
        link(task.get('mode'), task.get('job_id'))
    return jobs


def evidence_links(root):
    """Return chat-derived links alongside each chat's current project or private scope."""
    links, chats = {}, {}
    base = _chats(root)
    if base:
        for path in sorted(base.iterdir()):
            if path.suffix != '.json' or not _ID.fullmatch(path.stem) or path.is_symlink():
                continue
            def read():
                chat = load_chat(root, path.stem)
                return chat.get('project_id'), _chat_evidence(chat)
            try:
                info = path.lstat()
                project, jobs = _cached(path, read, ('evidence-links-v1', info.st_ino, info.st_ctime_ns))
            except (NotFound, OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
                continue
            chats[path.stem] = 'project:' + project if project else 'chat:' + path.stem
            for job in jobs:
                links.setdefault(job, set()).add(path.stem)
    return links, chats


def index_view(root, *, chat=None, project=None, all_scopes=False, limit=None, findings=False):
    """The research record of one scope. `findings` lists only what a reader can use (see project_index.scoped)."""
    from .. import project_index
    if sum(bool(x) for x in (chat is not None, project is not None, all_scopes)) > 1:
        raise ValueError('Select one research-record scope')
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError('Invalid research-record limit')
    scope = 'all' if all_scopes else 'workspace'
    links, chats = evidence_links(root)
    if chat is not None:
        if not isinstance(chat, str) or not _ID.fullmatch(chat):
            raise ValueError('Invalid chat ID')
        if chat not in chats:
            raise NotFound('Unknown chat')
        scope = chats[chat]
    if project is not None:
        try:
            projects.read_project(root, project)
        except FileNotFoundError:
            raise NotFound('Unknown project') from None
        scope = 'project:' + project
    view = project_index.scoped(project_index.collect(root), links, chats, scope, limit, findings)
    project_index.freeze_documents(root, view)
    return view
