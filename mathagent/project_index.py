# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Model-free views of saved specialist evidence. IDs include the full native job ID.

Modes own scientific labels. This module owns task identity, safe record discovery
and membership, and labels a sandbox run superseded when the finished answer of its
own worker passed it over for a later run. A job follows its conversation on project
moves. Unlinked native jobs belong to the workspace. No index file or reread of mutable source files:
a scan reuses the rows of every record whose files are unchanged and forgets the
records that are gone.
"""
from functools import lru_cache
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import re
import threading

from .ledger import ProofStore, _directory, _ID, _read
from .numerics.artifacts import EXPERIMENT_ID, FAILED_RUNS
from .research import _job_directory, _read_state

VERSION = 1
_PREFIX = {'proof': 'P', 'reference': 'C', 'source': 'L', 'review': 'R', 'experiment': 'E', 'lead': 'A',
           'unreadable': 'U'}
# How a reader names each kind of record; the entry ID is only for read_index.
_NAMES = {'proof': 'Proof', 'reference': 'Reference check', 'source': 'Source', 'review': 'Review',
          'experiment': 'Python run', 'lead': 'Exploration', 'unreadable': 'Unreadable record'}
# A run that a finished answer of its own passed over for a later run (workers.export_answer_evidence).
SUPERSEDED = 'superseded run'
_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, IndexError)
_WORKSPACES = 4  # remembered scans; an interface serves one folder at a time
_SCANS = {}  # workspace root -> {record: (stamp, rows)}; a scan publishes a new table, never changes one
_SCANS_LOCK = threading.Lock()


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def goal_sha256(goal):
    return digest(goal.strip())


def _pinned(state):
    """What the rows of one job share. A long reference context is hashed once per job, not once per row."""
    settings = state.get('settings') or {}
    context = state.get('reference_context') or settings.get('verifier_reference', settings.get('reference_context')) or {}
    return {'sources': [{'path': s['path'], 'sha256': s.get('sha256') or digest(s['content'])} for s in state.get('sources', [])],
            'images': [ref['sha256'] for ref in state.get('images', [])],
            'context_sha256': digest(_json(context)) if context else '',
            'original_request': context.get('query') or state.get('original_request') or '',
            'launch_project': (context.get('project_snapshot') or {}).get('id') or state.get('launch_project')}


def _entry(folder, job_id, row, state, pinned=None):
    pinned = pinned or _pinned(state)
    goal = row.get('text', state.get('goal', '')).strip()
    original = pinned['original_request']
    identity = {'text': goal, **{key: pinned[key] for key in ('sources', 'images', 'context_sha256')}}
    task = {**identity, 'goal_sha256': goal_sha256(goal), 'original_request': original,
            'sha256': digest(_json({'mode': row['mode'], **identity}))}
    qualifiers = list(row.get('qualifiers', []))
    if pinned['sources'] or pinned['images'] or pinned['context_sha256']:
        qualifiers.append('relative to pinned context')
    kind = row['kind']
    # A proof row states the task its job was given as the user reads it (proof.request_text).
    worded = goal if kind == 'proof' else state.get('goal', goal).strip()
    if original and worded != original.strip():
        qualifiers.append('worded by a model brief')
        if kind == 'proof' and row.get('direction') and pinned['context_sha256']:
            # The reviewer read the user's request as authoritative over the brief.
            qualifiers.append('reviewed with the request outranking the brief')
    entry_id = f'{_PREFIX[kind]}-{folder}-{job_id}' if kind == 'unreadable' else f'{_PREFIX[kind]}-{job_id}'
    if 'number' in row:
        entry_id += '.' + str(row['number'])
    evidence = dict(row.get('evidence', {}))
    if 'sha256' not in evidence:
        evidence['sha256'] = digest(_json({'status': row['status'], 'evidence': evidence,
                                          'documents': row.get('documents', []), 'task': task['sha256']}))
    return {'id': entry_id, 'job_id': job_id, 'folder': folder, 'mode': row['mode'], 'kind': kind,
            'status': row['status'], 'direction': row.get('direction'), 'qualifiers': list(dict.fromkeys(qualifiers)),
            'task': task, 'summary': row.get('summary', ''), 'evidence': evidence, 'documents': row.get('documents', []),
            'created_at': state.get('created_at') or state.get('created') or '',
            'launch_project': pinned['launch_project'],
            'links': row.get('links', []), 'chat_id': None}


def _children(parent):
    if parent.is_symlink() or not parent.exists():
        return []
    _directory(parent)
    return sorted((p for p in parent.iterdir() if not p.is_symlink()), key=lambda p: p.name)


def _stamp(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return info.st_mode, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size


def _native_rows(root, folder, job_id):
    if folder == 'proofs':
        from .proof import export_evidence
        state = ProofStore.load(root, job_id).state
    else:
        state = _read_state(_job_directory(root, job_id, folder), job_id, verify=False)
        if folder == 'checks' and state['kind'] == 'check':
            from .refcheck import export_evidence
        elif folder == 'research' and state['kind'] == 'literature':
            from .litreview import export_evidence
        elif folder == 'research' and state['kind'] == 'referee':
            from .review import export_evidence
        else:
            return []
    rows = export_evidence(state)
    for row in rows:
        if row['kind'] == 'review':
            report = root / '.mathagent' / folder / job_id / 'report.md'
            if report.exists():
                row.setdefault('evidence', {})['report_sha256'] = digest(_read(report))
    pinned = _pinned(state)
    return [_entry(folder, job_id, row, state, pinned) for row in rows]


def _last_scan(root):
    with _SCANS_LOCK:
        return _SCANS.get(root, {})


def _publish(root, seen):
    with _SCANS_LOCK:
        _SCANS.pop(root, None)
        _SCANS[root] = seen
        while len(_SCANS) > _WORKSPACES:
            del _SCANS[next(iter(_SCANS))]


def collect(root):
    """Discover extant records only. The remembered rows exclude chat membership."""
    root = Path(root).resolve(strict=True)
    base = root / '.mathagent'
    if not base.exists():
        return []
    _directory(base)
    known, seen, result = _last_scan(root), {}, []

    def remembered(record, stamp, build):
        saved = known.get(record)
        if saved is None or saved[0] != stamp:
            saved = (stamp, build())
        seen[record] = saved
        return saved[1]

    for folder in ('proofs', 'research', 'checks'):
        for job in _children(base / folder):
            if not _ID.fullmatch(job.name) or not job.is_dir():
                continue
            try:
                stamps = tuple(_stamp(job / name) for name in ('state.json', 'state.backup.json', 'report.md', 'artifacts'))
                result.extend(remembered((folder, job.name), stamps, lambda: _native_rows(root, folder, job.name)))
            except _ERRORS:
                result.append(_unreadable(folder, job.name))
    result.extend(_experiment_entries(base, remembered))
    for owner in _children(base / 'answers'):
        if not owner.is_dir() or not _ID.fullmatch(owner.name):
            continue
        for path in _children(owner):
            if path.suffix != '.json' or not _ID.fullmatch(path.stem) or not path.is_file():
                continue
            try:
                result.append(remembered(('answers', owner.name, path.stem), _stamp(path), lambda: _answer(path)))
            except _ERRORS:
                result.append({**_unreadable('answers', path.stem), 'chat_id': owner.name})
    _publish(root, seen)
    passed_over = {run for e in result if e['kind'] == 'lead' for run in e['evidence'].get('superseded_runs', [])}
    result = [{**e, 'status': SUPERSEDED} if e['kind'] == 'experiment' and e['job_id'] in passed_over else e
              for e in result]
    return sorted(result, key=lambda e: (e['created_at'], e['id']))


def _unreadable(folder, job_id):
    return _entry(folder, job_id, {'kind': 'unreadable', 'mode': folder, 'status': 'unreadable job'}, {})


def line(entry, *, room=None):
    """One earlier record, named by its kind and task; its ID comes last, for read_index.

    A task a model brief worded is shown after the user's request it came from.
    """
    label = ' · '.join([_NAMES[entry['kind']], entry['status'], *entry['qualifiers']])
    task = ' '.join(entry['task']['text'].split())
    original = ' '.join(entry['task'].get('original_request', '').split())
    statement = f'Recorded task: {task}'
    if original and 'worded by a model brief' in entry['qualifiers']:
        statement = f'Recorded request: {original} | Model brief: {task}'
    summary = ' '.join(entry['summary'].split())
    text = f'{label} | {statement}' + (f' | {summary}' if summary else '')
    locator = f" | entry id (for read_index only): {entry['id']}"
    if room is not None and len(text) + len(locator) > room:
        marker = '… [open request/task/summary with read_index]'
        text = text[:max(0, room - len(locator) - len(marker))] + marker
    return text + locator


def render(view):
    entries = view['entries']
    lines = [f"Research record ({view['scope']}, {len(entries)} entries). Model reviews are fallible.",
             *(line(e) + f" | statement {e['task']['sha256'][:12]} | {e['created_at'][:10]}" for e in entries)]
    if view.get('scopes'):
        lines.append('Scopes: ' + ', '.join(f"{s['key']} ({s['count']})" for s in view['scopes']))
    return '\n'.join(lines)


def _experiment_entries(base, remembered):
    live = {p.name: p / 'record.json' for p in _children(base / 'experiments')
            if EXPERIMENT_ID.fullmatch(p.name) and p.is_dir()}
    retained = {}
    index = base / 'index'
    if not index.is_symlink():
        retained = {p.stem: p for p in _children(index / 'experiments')
                    if p.suffix == '.json' and EXPERIMENT_ID.fullmatch(p.stem) and p.is_file()}
    result = []
    for job_id in sorted(live.keys() | retained.keys()):
        try:
            path = retained.get(job_id) or live[job_id]
            row = remembered(('experiments', job_id), (path, _stamp(path)), lambda: _experiment(path, job_id))
            if job_id not in live:
                row = {**row, 'qualifiers': [*row['qualifiers'], 'kept copy']}
            result.append(row)
        except _ERRORS:
            result.append(_unreadable('experiments', job_id))
    return result


def _experiment(path, job_id):
    from .numerics.artifacts import export_evidence, kept
    state = json.loads(_read(path))
    if state['id'] != job_id:
        raise ValueError('Invalid experiment identity')
    state = kept(state)
    return _entry('experiments', job_id, export_evidence(state)[0], state)


def _answer(path):
    state = json.loads(_read(path))
    if state['id'] != path.stem or state['chat_id'] != path.parent.name:
        raise ValueError('Invalid answer identity')
    from .workers import export_answer_evidence
    row = export_answer_evidence(state)
    if 'answer_sha256' in state and row['evidence']['sha256'] != state['answer_sha256']:
        raise ValueError('Saved worker answer digest differs')
    return {**_entry('answers', state['id'], row, state), 'chat_id': state['chat_id']}


def _carries_nothing(entry):
    """A failed worker's empty answer, a proof without a candidate yet, a run its answer passed over, and a
    failed or placeholder run that left nothing (no output, no result, no file)."""
    if entry['kind'] == 'lead':
        return not entry['summary']
    if entry['kind'] == 'proof':
        return entry['status'] == 'no candidate' and 'unfinished' in entry['qualifiers']
    if entry['kind'] != 'experiment':
        return False
    empty = not entry['summary'] and not entry['evidence'].get('artifacts')
    return entry['status'] == SUPERSEDED or empty and (
        entry['status'] in FAILED_RUNS or entry['task']['text'].strip().casefold() == 'placeholder')


def priority_order(entries):
    """Positions of `entries`, most worth keeping first: proofs and reviews, then checks, leads and runs,
    then literature sources, taken in turn from each review with the sources it read first."""
    turn, taken = {}, {}
    sources = [i for i, entry in enumerate(entries) if entry['kind'] == 'source']
    for position in sorted(sources, key=lambda i: (not entries[i]['documents'], i)):
        job = entries[position]['job_id']
        turn[position] = taken[job] = taken.get(job, -1) + 1

    def rank(position):
        if position in turn:
            return 2, turn[position], position
        return entries[position]['kind'] not in ('proof', 'review'), 0, position
    return sorted(range(len(entries)), key=rank)


def scoped(entries, links, chats, scope, limit=None, findings=False):
    """Derive membership from current chats and their child jobs. Ambiguous links stay in each linked scope.

    `findings` leaves out entries that carry nothing; their links still place their checks and runs.
    `limit` keeps the entries of highest priority and counts the others in `omitted`.
    """
    membership = {key: set(owners) for key, owners in links.items()}
    # A small answer belongs only to its owning chat; deleting it removes its private record.
    entries = [e for e in entries if e['folder'] != 'answers' or e['chat_id'] in chats]
    for entry in entries:
        owner = entry['chat_id']
        if owner in chats:
            membership[(entry['folder'], entry['job_id'])] = {owner}
    for entry in entries:
        owners = membership.get((entry['folder'], entry['job_id']), set())
        for child in entry['links']:
            membership.setdefault(tuple(child), set()).update(owners)
    known = {e['job_id'] for e in entries if e['folder'] == 'experiments'}
    missing = [_entry('experiments', job, {'kind': 'experiment', 'mode': 'numerics', 'status': 'artifact unavailable'}, {})
               for folder, job in membership if folder == 'experiments' and job not in known]
    native_jobs = {(e['folder'], e['job_id']) for e in [*entries, *missing]}
    visible, counts = [], {}
    for entry in [*entries, *missing]:
        if findings and _carries_nothing(entry):
            continue
        owners = sorted(membership.get((entry['folder'], entry['job_id']), []))
        locations = {}
        for owner in owners:  # A deleted conversation (`deleted:<id>`) keeps the scope but has no chat to open.
            if owner in chats and (locations.get(chats[owner]) is None or not owner.startswith('deleted:')):
                locations[chats[owner]] = None if owner.startswith('deleted:') else owner
        locations = locations or {'workspace': None}
        for key in locations:
            counts[key] = counts.get(key, 0) + 1
        if scope not in locations and scope != 'all':
            continue
        key = scope if scope != 'all' else sorted(locations)[0]
        item = deepcopy(entry)
        item.update(scope=key, chat_id=locations[key])
        # Resolve these specific child jobs inside this very view, including for a later investigation decision.
        child_ids = []
        for child in item.pop('links', []):
            child = tuple(child)
            owners = membership.get(child, set())
            child_scopes = {chats[owner] for owner in owners if owner in chats} or {'workspace'}
            if child in native_jobs and key in child_scopes:
                child_ids.append(child[1])
        item['linked_job_ids'] = list(dict.fromkeys(child_ids))
        if item['launch_project'] and key != 'project:' + item['launch_project']:
            item['qualifiers'].append('started in another project' if key.startswith('project:') else 'started in a project')
        visible.append(item)
    visible.sort(key=lambda e: (e['created_at'], e['id']), reverse=True)
    kept = visible if limit is None else [visible[i] for i in sorted(priority_order(visible)[:limit])]
    return {'version': VERSION, 'scope': scope, 'entries': kept, 'omitted': len(visible) - len(kept),
            'scopes': [{'key': k, 'count': counts[k]} for k in sorted(counts) if scope in ('all', 'workspace', k)]}


def read_text(root, view, entry_id):
    """Read only a native object represented in the frozen view, checking content against its saved digest."""
    entry = next((e for e in view['entries'] if e['id'] == entry_id), None)
    if not entry:
        raise ValueError('Unknown entry in this research record')
    root = Path(root).resolve(strict=True)
    base = root / '.mathagent'
    _directory(base)
    folder, job, ev = entry['folder'], entry['job_id'], entry['evidence']
    try:
        if folder == 'proofs' and ev.get('artifact'):
            text = ProofStore.load(root, job).read_artifact(ev['artifact'])
            if digest(text) != ev['sha256']:
                raise ValueError('Candidate changed')
            if ev.get('issues'):
                text += '\n\nSelected review objections:\n' + _json(ev['issues'])
        elif folder == 'answers':
            owner = entry.get('chat_id')
            if not isinstance(owner, str) or not _ID.fullmatch(owner) or not _ID.fullmatch(job):
                raise ValueError('Invalid answer identity')
            _directory(base / 'answers')
            _directory(base / 'answers' / owner)
            data = json.loads(_read(base / 'answers' / owner / (job + '.json')))
            text = data['answer']
            if data['id'] != job or data['chat_id'] != owner or digest(text) != ev.get('sha256'):
                raise ValueError('Answer changed')
        elif folder == 'experiments':
            from .numerics.artifacts import ExperimentStore, _encode, kept
            if not EXPERIMENT_ID.fullmatch(job):
                raise ValueError('Invalid experiment identity')
            copy = base / 'index' / 'experiments' / (job + '.json')
            if copy.exists():
                _directory(copy.parent.parent)
                _directory(copy.parent)
                data = json.loads(_read(copy))
            else:
                data = ExperimentStore(root).load(job)
            if data['id'] != job:
                raise ValueError('Experiment identity changed')
            text = _encode(kept(data)).decode()
            if digest(text) != ev.get('sha256'):
                raise ValueError('Experiment changed')
        elif folder == 'research' and ev.get('report_sha256'):
            text = _read(_job_directory(root, job) / 'report.md')
            if digest(text) != ev['report_sha256']:
                raise ValueError('Report changed')
        else:
            # Precise metadata and (for Check) the saved gated quote, both frozen in this view.
            text = line(entry) + '\n' + _json(ev)
    except _ERRORS as exc:
        raise ValueError('The saved evidence is unavailable or has changed') from exc
    return text


@lru_cache(maxsize=8192)  # with fewer slots than cited papers, every request would re-read and re-hash each paper
def _paper_ranges(directory, doc, passages, expected_quote, stamp):
    """Remember verified ranges and a text digest, never the complete public paper."""
    from .literature import DOCUMENT_META_BYTES, MAX_TEXT
    metadata = json.loads(_read(directory / (doc + '.json'), DOCUMENT_META_BYTES))
    text = _read(directory / (doc + '.txt'), MAX_TEXT)
    text_sha256 = digest(text)
    if metadata.get('document_id') != doc or text_sha256 != metadata.get('text_sha256'):
        raise ValueError('The cached paper changed')
    lines = text.splitlines()
    verified = []
    for tag, saved_hash in passages:
        match = re.fullmatch(r'L([1-9]\d*)-L([1-9]\d*)', tag)
        if not match:
            continue
        first, last = map(int, match.groups())
        if not 1 <= first <= last <= len(lines):
            continue
        source = ' '.join(' '.join(lines[first-1:last]).split())
        if (saved_hash and digest(source) == saved_hash) or (expected_quote and expected_quote in source):
            verified.append(tag)
    return text_sha256, verified


def freeze_documents(root, view):
    """Bind authorized source locators to the cached extraction seen by this snapshot."""
    from .literature import DOC_ID
    directory = Path(root) / '.mathagent' / 'literature' / 'documents'
    for entry in view['entries']:
        available = []
        for doc in entry['documents']:
            key = doc['document_id']
            try:
                if not isinstance(key, str) or not DOC_ID.fullmatch(key):
                    raise ValueError('Invalid cached document id')
                for parent in (directory.parent.parent, directory.parent, directory):
                    _directory(parent)
                stamp = tuple(_stamp(directory / (key + ext)) for ext in ('.json', '.txt'))
                passages = tuple((p['range'], p.get('sha256')) for p in doc['passages'])
                quote = ' '.join((entry['evidence'].get('quote') or '').split())
                sha, tags = _paper_ranges(directory, key, passages, quote, stamp)
                confirmed = [p for p in doc['passages'] if p['range'] in tags]
                if confirmed:
                    available.append({**doc, 'passages': confirmed, 'text_sha256': sha})
                if len(confirmed) != len(passages):
                    raise ValueError('The original passage differs from its current extraction')
            except _ERRORS:
                if 'cached passage unavailable or changed' not in entry['qualifiers']:
                    entry['qualifiers'].append('cached passage unavailable or changed')
        entry['documents'] = available
