# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ENPC / SquareHarness

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

from ..ledger import ProofStore, _ID, _ARTIFACT, _atomic_write, _directory, _read, _regular, _now, _sync_directory
from ..research import _job_directory, _read_state as _read_research
from ..tools import READ_GROUPS, SKIP, TEXT, Workspace
from ..style import list_profiles, load_choice, macro_definitions, public_profile, read_template, save_choice, set_template
from ..writeup import SOURCE_BUNDLE_BYTES, TEMPLATE_SUFFIXES, download_name, entry_identifiers
from .. import images
from . import projects

CHAT_MODES = ('critic', 'explore', 'free')
# How the interface names a suggestion in chat previews: critic and explore answers
# are both answers in the conversation, and the referee workflow is called Review.
ROUTE_NAMES = {'prove': 'Proof', 'critic': 'Answer', 'explore': 'Answer', 'literature': 'Literature report',
               'referee': 'Review', 'detailed_review': 'Detailed review', 'quick_review': 'Quick review', 'explain': 'Explanation',
               'writeup': 'Write-up', 'summarize': 'Summary', 'clarify': 'Question', 'python_lab': 'Python Lab',
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


def one_line(text, limit=80):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _cached(path, load):
    """Parse a state file once per (mtime, size); listings run on every job event."""
    info = path.lstat()
    key = (str(path), info.st_mtime_ns, info.st_size)
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
                out[key] += message[field]
        if isinstance(message.get('tool_calls'), list):
            out['tool_calls'].extend(message['tool_calls'])
        if event.get('done'):
            out['done'] = True
            out['stats'] = {k: event[k] for k in ('eval_count', 'prompt_eval_count', 'done_reason', 'eval_duration')
                            if k in event}
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


def _output_budget(state, running):
    """Keep in-flight capacity separate from settled cumulative output."""
    charged = state['tokens_charged']
    reserved = min(charged, sum(call.get('reserved_tokens', 0) for call in state['calls']
                                if running and call.get('status') == 'running'
                                and 'charged_tokens' not in call))
    return {**_budget(charged - reserved, state['settings']['max_tokens']), 'reserved': reserved}


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
                     'title': one_line(state['goal'], 140), 'updated_at': state['updated_at'],
                     'created_at': state['created_at'], 'running': running,
                     'sources': [s['path'] for s in state['sources']],
                     'rounds': _budget(state['rounds_started'], state['settings']['max_rounds']),
                     'tokens': _output_budget(state, running),
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
        'goal': s['goal'], 'status': s['status'], 'stop_reason': s.get('stop_reason'),
        'recovery_notice': s.get('recovery_notice'),
        'created_at': s['created_at'], 'updated_at': s['updated_at'], 'revision': s['revision'],
        'settings': settings,
        'images': s.get('images', []),
        'budget': {'rounds': _budget(s['rounds_started'], settings['max_rounds']),
                   'tokens': _output_budget(s, running),
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

def research_state(root, job_id):
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise ValueError('Invalid research ID')
    if not (Path(root) / '.mathagent' / 'research' / job_id).exists():
        raise NotFound('Unknown research job')
    return _read_research(_job_directory(root, job_id), job_id)


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
            state = _cached(path / 'state.json', lambda: research_state(root, path.name))
        except (OSError, ValueError, KeyError, NotFound):
            continue
        running = state['status'] == 'running' and (path.name == active or locked(path))
        jobs.append({'id': state['id'], 'kind': state['kind'], 'status': state['status'],
                     'phase': state['phase'], 'title': one_line(state['goal'], 140),
                     'updated_at': state['updated_at'], 'created_at': state['created_at'],
                     'running': running, 'sources': [s['path'] for s in state['sources']],
                     'variant': state.get('variant'), 'verdict': state.get('verdict') or None,
                     'tokens': _output_budget(state, running)})
    return sorted(jobs, key=lambda job: job['updated_at'], reverse=True)


def research_detail(root, job_id, active=None):
    s = research_state(root, job_id)
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
        'budget': {'rounds': _budget(s['rounds_started'], settings['max_rounds']),
                   'tokens': _output_budget(s, running),
                   'input_tokens': _budget(s['input_tokens_charged'], settings['max_input_tokens']),
                   'seconds': _working_time(s, running),
                   'requests': _budget(usage.get('requests', 0), settings['max_requests']),
                   'chars': _budget(usage.get('returned_chars', 0), settings['max_chars']),
                   **_live_context(call)},
        'progress': s.get('progress'),
        'sources': [{'id': src['id'], 'path': src['path'], 'sha256': src['sha256'],
                     'lines': len(src['content'].splitlines())} for src in s['sources']],
        'plan': s['plan'], 'notes': s['notes'], 'evidence': s['evidence'], 'draft': s['draft'],
        'pipeline': s.get('pipeline'), 'variant': s.get('variant'), 'verdict': s.get('verdict') or None,
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
                                        'title', 'style_checked')}
           if s['kind'] == 'writeup' else {}),
        # The parts hold the material itself: the page needs only how far the writing is.
        **({'parts': [{'complete': bool(part.get('complete')), 'characters': len(part.get('text', ''))} for part in s['parts']]}
           if s['kind'] == 'writeup' and isinstance(s.get('parts'), list) else {}),
        **({key: s.get(key) for key in ('style_name', 'articles', 'domains', 'observations', 'profile', 'saved_to')}
           if s['kind'] == 'style' else {}),
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
            suffix = path.suffix.lower()
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
        raise ValueError('Chat mode must be critic, explore or free')
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
    first = next((item.get('content', '').strip() for item in chat['transcript']
                  if item.get('role') == 'user' and not item.get('discarded')
                  and len(item.get('content', '').strip()) >= 12), '')
    if not first:
        return None
    chat['auto_title_attempted'] = True
    _save_chat(root, chat)
    return first


def load_chat(root, chat_id):
    if not isinstance(chat_id, str) or not _ID.fullmatch(chat_id):
        raise ValueError('Invalid chat ID')
    base = _chats(root)
    if base is None or not (base / (chat_id + '.json')).exists():
        raise NotFound('Unknown chat')
    chat = _read_chat(base / (chat_id + '.json'))
    if (not isinstance(chat, dict) or chat.get('version') != 1 or chat.get('id') != chat_id
            or chat.get('mode') not in CHAT_MODES or not isinstance(chat.get('history'), list)
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
    checkpoint live in this JSON. Proof/research stream files belong to jobs.
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
    def load(path):
        chat = load_chat(root, path.name[:-5])
        chat['_display_macros'] = chat_macros(root, chat, templates_root)
        return chat
    for path in base.iterdir():
        if not path.name.endswith('.json') or not _ID.fullmatch(path.name[:-5]) or path.is_symlink():
            continue
        try:
            chat = _cached(path, lambda: load(path))
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
                      'messages': sum(1 for item in chat['transcript'] if item.get('role') == 'user'),
                      'preview': one_line(last, 140), 'math_macros': chat['_display_macros']})
    return sorted(chats, key=lambda chat: chat['updated_at'], reverse=True)


@_locked_chat
def commit_turn(root, chat_id, history, calls, notices, echo_user=True, mode=None, *, files=(), user_content=None,
                project_snapshot=None, image_refs=()):
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
            if image_refs:
                item['images'] = [dict(ref) for ref in image_refs]
            if project_snapshot is not None:
                item['project_snapshot'] = project_snapshot
            if user_content is not None:
                item['content'] = user_content
        if mode and message['role'] != 'user':
            item['mode'] = mode  # which workflow answered (Default mode mixes them)
        if message['role'] == 'assistant':
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
def record_discarded(root, chat_id, content, reason, *, files=(), image_refs=()):
    """Keep an unfinished turn visible without adding it to the model context."""
    chat = load_chat(root, chat_id)
    stamp = _now()
    if content:
        chat['transcript'].append({'role': 'user', 'content': content, 'time': stamp, 'discarded': True,
                                   'files': list(files), 'images': [dict(ref) for ref in image_refs]})
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
def update_item(root, chat_id, index, **fields):
    chat = load_chat(root, chat_id)
    if type(index) is not int or not 0 <= index < len(chat['transcript']):
        raise NotFound('Unknown conversation item')
    chat['transcript'][index].update(fields)
    _save_chat(root, chat)
    return chat['transcript'][index]


@_locked_chat
def save_assistant(root, chat_id, state):
    """Checkpoint the parent and its child identities in the same atomic chat file."""
    chat = load_chat(root, chat_id)
    chat['assistant_state'] = state
    _save_chat(root, chat)


def assistant_view(chat):
    state = chat.get('assistant_state') or {}
    status = state.get('status')
    status = 'paused' if status in ('interrupted', 'incomplete') else status
    budget = state.get('budget') or {}
    reserved = budget.get('reserved_tokens', 0)
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
    return {'assistant_status': status,
            'assistant_resumable': status in ('paused', 'running'),
            'assistant_progress': progress,
            'assistant_budget': {'tokens': {**_budget(max(0, budget.get('tokens', 0) - reserved), budget.get('max_tokens')),
                                             'reserved': reserved},
                                 'input_tokens': _budget(budget.get('input_tokens', 0), budget.get('max_input_tokens')),
                                 'seconds': _budget(round(budget.get('seconds', 0), 1), budget.get('max_seconds', 900)),
                                 **context}}


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
        suffix = Path(entry.name).suffix.lower()
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
    suffix = Path(base).suffix.lower()
    if not base or base.startswith('.') or suffix not in UPLOAD_SUFFIXES:
        raise ValueError('Drop text/LaTeX, PDF, Python or PNG/JPEG/WebP image files')
    if len(data) > UPLOAD_BYTES:
        raise ValueError('Files are limited to 20 MB')
    if suffix not in BINARY_UPLOADS and b'\x00' in data:
        raise ValueError('That file is not text')
    stem, number = Path(base).stem, 2
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
                 'literature': 'Literature report', 'referee': 'Review', 'detailed_review': 'Detailed review',
                 'quick_review': 'Quick review', 'explain': 'Explanation', 'writeup': 'Write-up', 'summarize': 'Summary'}
_PROOF_STATUS = {'candidate_complete': 'the model review found no issue', 'uncertain': 'the model review is uncertain',
                 'review_unavailable': 'not reviewed'}


def _saved_result(root, mode, job_id):
    """A finished job's final text from its saved state: the selected proof, the document or the report."""
    try:
        if mode == 'prove':
            detail = proof_detail(root, job_id)
            return detail.get('answer') or '', _PROOF_STATUS.get(detail['status'], detail['status'])
        state = research_state(root, job_id)
        return state.get('document') or state.get('draft') or '', state['status']
    except (NotFound, OSError, ValueError, KeyError, TypeError, AttributeError):
        return '', ''


def conversation_experiments(root, chat_id, before=None):
    """Only the sandbox runs shown in this conversation, in transcript order."""
    return list(dict.fromkeys(item['experiment'] for item in load_chat(root, chat_id)['transcript'][:before]
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


def conversation_record(root, chat_id, before=None, skip=None):
    """An Assistant conversation as one text source for a write-up or a summary.

    In the order the user saw it: their messages, the answers, and the full final
    text of each finished job, and sandbox code, output and figure names.
    Reading and web tools are left out: their output is source material, not a result.
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
    parts = []
    for index, item in enumerate(chat['transcript'][:before]):
        if index == skip:
            continue
        role, content = item.get('role'), (item.get('content') or '').strip()
        if role == 'user' and content:
            files = item.get('files') or []
            parts.append('## User' + (f' (attached: {", ".join(files)})' if files else '') + '\n\n' + content)
        elif role == 'assistant' and content:
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
            if value is not None:
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
                parts.append(f'## {_RESULT_NAMES.get(mode, mode)} result' + (f' ({status})' if status else '')
                             + '\n\nTask: ' + ' '.join(str(item.get('request') or '').split())[:1500] + '\n\n' + text.strip())
    return '\n\n'.join(parts)


def style_choice(root):
    """Which saved style write-ups use."""
    return load_choice(root)


def chat_macros(root, chat, base=None):
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
    workspace = Workspace(root)
    files = list(dict.fromkeys(name for item in chat['transcript'] if item.get('role') in ('user', 'route')
                               for name in item.get('files') or []))
    for name in files[-20:]:
        try:
            path = workspace.path(name)
            if path.suffix.lower() in ('.tex', '.sty', '.cls'):
                texts.append(workspace.text(path))
        except (OSError, ValueError, UnicodeError):
            continue
    texts.extend(item.get('content', '') for item in chat['transcript'] if item.get('role') == 'user')
    definitions = {}
    for text in texts:
        definitions.update(macro_definitions(text))
    return dict(list(definitions.items())[-200:])


def set_style_choice(root, mode, style=None, domain=None):
    try:
        return save_choice(root, mode, style, domain)
    except FileNotFoundError:
        raise NotFound('Unknown style') from None
