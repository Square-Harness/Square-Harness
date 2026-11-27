# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Experiment records: a small, bounded, managed directory per workspace.

Layout: ``<workspace>/.mathagent/experiments/<experiment-id>/`` holding
``record.json`` (code, parameters, seed, image, limits, status, results),
``code.py`` and the validated figures/data files of that run. Names come from
the executor's validated artifacts and the store's own identifiers; a model can
read a record by identifier only, never by host path. Oldest records are pruned
past ``max_records`` or ``max_bytes``.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
import warnings

from .executor import ARTIFACT_NAME
from ..ledger import _atomic_write, _directory, _read, _regular

EXPERIMENT_ID = re.compile(r'^exp-\d{8}-\d{6}-[0-9a-f]{6}$')


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


class ExperimentStore:
    def __init__(self, workspace_root, max_records=None, max_bytes=256_000_000):
        self.root = Path(workspace_root).resolve(strict=True)
        self.directory = self.root / '.mathagent' / 'experiments'
        self.max_records, self.max_bytes = max_records, max_bytes
        self._lock = threading.Lock()

    def _ensure(self):
        for folder in (self.root / '.mathagent', self.directory):
            if folder.is_symlink():
                raise ValueError(f'{folder} must not be a symbolic link')
            folder.mkdir(mode=0o700, exist_ok=True)

    def path(self, experiment_id, name=None):
        """The host path of a record or one of its files, after validating both names."""
        if not isinstance(experiment_id, str) or not EXPERIMENT_ID.match(experiment_id):
            raise ValueError('Unknown experiment id')
        folder = self.directory / experiment_id
        if name is None:
            return folder
        if not isinstance(name, str) or not (ARTIFACT_NAME.match(name) or name in ('record.json', 'code.py')):
            raise ValueError('Invalid experiment file name')
        return folder / name

    @staticmethod
    def new_id():
        return f'exp-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}'

    def save(self, *, code, result, limits, policy, image, seed=None, parameters=None, purpose='',
             explicit=False, invocation=None, harness_version='', experiment_id=None):
        """Persist one run and return its record (with relative artifact paths)."""
        if invocation is None:
            invocation = 'explicit' if explicit else 'automatic'
        if invocation not in ('explicit', 'routed', 'automatic'):
            raise ValueError('Unknown experiment invocation')
        now = datetime.now(timezone.utc)
        if experiment_id is None:
            experiment_id = self.new_id()
        with self._lock:
            self._ensure()
            folder = self.path(experiment_id)
            folder.mkdir(mode=0o700)
            files = []
            for artifact in result.artifacts:
                _write(self.path(experiment_id, artifact.name), artifact.data)
                files.append({**artifact.describe(), 'sha256': hashlib.sha256(artifact.data).hexdigest(),
                              'path': (folder / artifact.name).relative_to(self.root).as_posix()})
            _write(self.path(experiment_id, 'code.py'), code.encode('utf-8'))
            record = {
                'evidence_version': 2, 'id': experiment_id, 'created': now.isoformat(timespec='seconds'),
                'skill': policy, 'invocation': invocation,
                'purpose': purpose, 'code': code, 'seed': seed, 'parameters': parameters or {},
                'image': image, 'limits': limits.as_dict(), 'harness_version': harness_version,
                'status': result.status, 'exit_code': result.exit_code,
                'elapsed_seconds': result.elapsed_seconds, 'wall_seconds': result.wall_seconds,
                'stdout': result.stdout, 'stderr': result.stderr,
                'stdout_truncated': result.stdout_truncated,
                'stderr_truncated': result.stderr_truncated,
                'error': result.error, 'result': result.result, 'artifacts': files,
                'warnings': result.warnings, 'message': result.message,
            }
            _write(self.path(experiment_id, 'record.json'),
                   _encode(record))
            try:
                self._retain(record)
            except (OSError, ValueError) as exc:
                warnings.warn(f'Could not retain experiment {experiment_id}: {exc}', RuntimeWarning, stacklevel=2)
            self._prune(keep=experiment_id)
        return record

    def load(self, experiment_id):
        path = self.path(experiment_id, 'record.json')
        for folder in (self.root / '.mathagent', self.directory, path.parent):
            _directory(folder)
        record = json.loads(_read(path))
        if record.get('id') != experiment_id:
            raise ValueError('Invalid experiment identity')
        return record

    def read_saved(self, experiment_id):
        """Prefer the native run, then its retained evidence after artifact rotation."""
        path = self.path(experiment_id)
        if path.exists() or path.is_symlink():
            return self.load(experiment_id)
        directory = self.root / '.mathagent' / 'index' / 'experiments'
        for folder in (directory.parent.parent, directory.parent, directory):
            _directory(folder)
        saved = json.loads(_read(directory / (experiment_id + '.json')))
        if saved.get('id') != experiment_id:
            raise ValueError('Invalid retained experiment identity')
        return saved

    def figure(self, experiment_id, name):
        """A declared, unchanged graphic of this run, addressed by filename or experiment/filename."""
        try:
            record = self.load(experiment_id)
            for artifact in record.get('artifacts', []):
                filename = artifact['name']
                relative = Path(name)
                portable_alias = (not relative.is_absolute() and relative.parts
                                  and not any(p.startswith('.') for p in relative.parts)
                                  and relative.name in (filename, str(Path(filename).with_suffix(''))))
                if name not in (filename, f'{experiment_id}/{filename}', str(Path(filename).with_suffix('')),
                                f'.mathagent/experiments/{experiment_id}/{filename}') and not portable_alias:
                    continue
                if Path(filename).suffix.lower() not in ('.png', '.pdf', '.jpg', '.jpeg'):
                    continue
                path = self.path(experiment_id, filename)
                _regular(path)
                if path.stat().st_size <= 16_000_000 and hashlib.sha256(path.read_bytes()).hexdigest() == artifact.get('sha256'):
                    return path
        except (ValueError, OSError, KeyError, TypeError):
            pass
        return None

    def list(self, limit=None):
        native = self.directory
        kept = self.root / '.mathagent' / 'index' / 'experiments'
        names = set()
        if kept.is_dir() and not kept.is_symlink():
            names.update(p.stem for p in kept.glob('*.json') if EXPERIMENT_ID.fullmatch(p.stem) and not p.is_symlink())
        if native.is_dir() and not native.is_symlink():
            names.update(p.name for p in native.iterdir() if EXPERIMENT_ID.fullmatch(p.name) and not p.is_symlink())
        names = sorted(names, reverse=True)
        records = []
        for name in names[:limit]:
            try:
                record = self.read_saved(name)
            except (ValueError, OSError):
                continue
            records.append({k: record.get(k) for k in ('id', 'created', 'skill', 'status', 'purpose',
                                                        'elapsed_seconds')})
        return records

    def _retain(self, record):
        if not isinstance(record.get('id'), str) or not EXPERIMENT_ID.fullmatch(record['id']):
            raise ValueError('Invalid experiment identity')
        base = self.root / '.mathagent'
        _directory(base)
        directory = base / 'index' / 'experiments'
        _directory(directory.parent, create=True)
        _directory(directory, create=True)
        _atomic_write(directory / (record['id'] + '.json'), _encode(retained(record)))

    def _prune(self, keep):
        folders = sorted(p for p in self.directory.iterdir()
                         if EXPERIMENT_ID.match(p.name) and p.is_dir() and not p.is_symlink())
        sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file() and not f.is_symlink()) for p in folders}
        total = sum(sizes.values())
        older = sorted((p for p in folders if p.name != keep), key=lambda p: (p.stat().st_mtime_ns, p.name))
        while older and ((self.max_records is not None and len(older) + 1 > self.max_records) or total > self.max_bytes):
            oldest = older.pop(0)
            try:
                original = self.load(oldest.name)
                compact = _encode(kept(original))
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                # A damaged run (an interrupted save, an invalid record) is left in place: the next oldest is pruned.
                warnings.warn(f'Left damaged experiment {oldest.name} in place: {exc}', RuntimeWarning, stacklevel=2)
                continue
            # Before upgrading, this may be the only copy. Preserve it before pruning.
            try:
                copy = self.root / '.mathagent' / 'index' / 'experiments' / (oldest.name + '.json')
                _directory(self.root / '.mathagent')
                _directory(copy.parent.parent, create=True)
                _directory(copy.parent, create=True)
                try:
                    intact = _encode(kept(json.loads(_read(copy)))) == compact
                except (OSError, ValueError, TypeError, AttributeError):
                    intact = False
                if not intact:
                    self._retain(original)
            except (OSError, ValueError) as exc:
                warnings.warn(f'Could not retain old experiment {oldest.name}: {exc}', RuntimeWarning, stacklevel=2)
                break
            total -= sizes[oldest]
            shutil.rmtree(oldest, ignore_errors=True)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8')


def kept(record):
    """Stable scientific evidence for index snapshots. V1 retains its original digest."""
    if record.get('evidence_version') == 2:
        fields = ('evidence_version', 'id', 'created', 'skill', 'invocation', 'purpose', 'code', 'seed',
                  'parameters', 'status', 'exit_code', 'stdout', 'stderr', 'result', 'artifacts',
                  'stdout_truncated', 'stderr_truncated', 'warnings', 'error', 'limits', 'image',
                  'message', 'elapsed_seconds', 'wall_seconds', 'harness_version')
        return {key: record.get(key) for key in fields}
    out = {k: record.get(k) for k in ('id', 'created', 'skill', 'invocation', 'purpose', 'code', 'seed',
                                      'parameters', 'status', 'exit_code', 'stdout', 'stderr', 'result')}
    for key in ('id', 'created', 'skill', 'invocation', 'status'):
        if not isinstance(out[key], str) or len(out[key]) > 256:
            raise ValueError('Invalid experiment metadata field: ' + key)
    for key in ('seed', 'exit_code'):
        if out[key] is not None and (type(out[key]) is not int or out[key].bit_length() > 64):
            raise ValueError('Invalid numeric experiment field: ' + key)
    if not all(isinstance(a, dict) for a in record.get('artifacts', [])):
        raise ValueError('Invalid experiment artifacts')
    out['artifacts'] = [{k: a.get(k) for k in ('name', 'sha256', 'bytes')} for a in record.get('artifacts', [])]
    out['truncated'] = bool(record.get('truncated') or record.get('stdout_truncated') or record.get('stderr_truncated'))
    for key, cap in (('stdout', 20000), ('stderr', 4000), ('code', 24000), ('purpose', 4000)):
        if isinstance(out[key], str) and len(out[key]) > cap:
            out[key] = out[key][:cap]
            out['truncated'] = True
    while len(_encode(out)) > 65536:
        key = max(('stdout', 'stderr', 'code', 'parameters', 'result', 'artifacts', 'purpose'),
                  key=lambda k: len(_encode(out[k])))
        value = out[key]
        if isinstance(value, (str, list)):
            out[key] = value[:len(value) // 2]
        else:
            raw = _encode(value)
            out[key] = {'truncated': True, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'preview': raw[:2000].decode('utf-8', 'ignore')}
        out['truncated'] = True
    return out


_STOP_REASONS = {'error': 'The code raised an error.', 'timeout': 'Stopped at the time limit.',
                 'memory': 'Killed at the memory limit.', 'output_limit': 'Stopped: it printed more than the sandbox returns.',
                 'cancelled': 'Cancelled.', 'sandbox_error': 'The sandbox could not run it.'}
DETAIL_FIELDS = ('error', 'stop_reason', 'limits', 'stdout_tail', 'seconds', 'run_status')


def details(record):
    """How a run ended, for the Outputs view: its error, why it stopped, its limits, the end of what it
    printed and how long it ran. Not part of the evidence digest (see kept)."""
    status = record.get('status')
    error = record.get('error') if isinstance(record.get('error'), dict) else None
    limits = record.get('limits') if isinstance(record.get('limits'), dict) else None
    stdout = record.get('stdout') if isinstance(record.get('stdout'), str) else ''
    seconds = record.get('elapsed_seconds')
    message = record.get('message') if isinstance(record.get('message'), str) else ''
    return {'run_status': status if isinstance(status, str) else None,
            'error': {k: str(error.get(k) or '')[:cap] for k, cap in (('type', 200), ('message', 1000), ('traceback', 3000))}
            if error else None,
            'stop_reason': None if status == 'ok' else (message[:500] or _STOP_REASONS.get(status, status)),
            'limits': {k: limits.get(k) for k in ('timeout_seconds', 'memory_mb', 'cpus')} if limits else None,
            'stdout_tail': stdout[-2000:] if stdout else '',
            'seconds': seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None}


def retained(record):
    """The kept scientific evidence and display details. Preserve the historical 64 KiB encoding for V1."""
    out = kept(record)
    extra = details(record)
    if out.get('evidence_version') == 2:
        return {**extra, **out}
    for _ in range(3):
        if len(_encode({**out, **extra})) <= 65536:
            return {**out, **extra}
        extra = {**extra, 'stdout_tail': extra['stdout_tail'][-500:],
                 'error': extra['error'] and {**extra['error'], 'traceback': extra['error']['traceback'][-500:]}}
    return out


def _finding(record):
    """What a run found: its returned result, else the last printed lines (a conclusion is usually
    printed last; the first line is often a timing or a header)."""
    result = record.get('result')
    if result not in (None, '', {}, []):
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return ' '.join(text.split())[:500]
    lines = [line.strip() for line in (record.get('stdout') or '').splitlines() if line.strip()]
    return ' · '.join(lines[-3:])[-500:]


# A run that did not finish is never listed as a result: what it printed is output before it stopped.
_STOPPED = {'timeout': 'stopped at the time limit'}
FAILED_RUNS = ('failed run', *_STOPPED.values())  # the index statuses of runs that did not finish


def export_evidence(record):
    status = record.get('status') or 'unknown'
    summary = _finding(record)
    if status != 'ok' and summary:
        summary = 'Printed before it stopped: ' + summary
    return [{'kind': 'experiment', 'mode': 'numerics',
             'status': 'ran' if status == 'ok' else _STOPPED.get(status, FAILED_RUNS[0]),
             'text': record.get('purpose') or '', 'summary': summary,
             'qualifiers': ['bounded record'] if (record.get('truncated') or record.get('stdout_truncated') or record.get('stderr_truncated')) else [],
             'evidence': {'sha256': hashlib.sha256(_encode(kept(record))).hexdigest(),
                          'artifacts': [{k: a.get(k) for k in ('name', 'sha256', 'bytes')}
                                        for a in record.get('artifacts', [])]}}]
