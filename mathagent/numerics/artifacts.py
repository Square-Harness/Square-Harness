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
STORED_OUTPUT = 200_000


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


class ExperimentStore:
    def __init__(self, workspace_root, max_records=200, max_bytes=256_000_000):
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

    def save(self, *, code, result, limits, policy, image, seed=None, parameters=None, purpose='',
             explicit=False, harness_version=''):
        """Persist one run and return its record (with relative artifact paths)."""
        now = datetime.now(timezone.utc)
        experiment_id = f'exp-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}'
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
                'id': experiment_id, 'created': now.isoformat(timespec='seconds'),
                'skill': policy, 'invocation': 'explicit' if explicit else 'automatic',
                'purpose': purpose, 'code': code, 'seed': seed, 'parameters': parameters or {},
                'image': image, 'limits': limits.as_dict(), 'harness_version': harness_version,
                'status': result.status, 'exit_code': result.exit_code,
                'elapsed_seconds': result.elapsed_seconds, 'wall_seconds': result.wall_seconds,
                'stdout': result.stdout[:STORED_OUTPUT], 'stderr': result.stderr[:STORED_OUTPUT],
                'stdout_truncated': result.stdout_truncated or len(result.stdout) > STORED_OUTPUT,
                'stderr_truncated': result.stderr_truncated or len(result.stderr) > STORED_OUTPUT,
                'error': result.error, 'result': result.result, 'artifacts': files,
                'warnings': result.warnings, 'message': result.message,
            }
            _write(self.path(experiment_id, 'record.json'),
                   json.dumps(record, ensure_ascii=False, indent=1, default=str).encode('utf-8'))
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

    def list(self, limit=50):
        if not self.directory.is_dir():
            return []
        names = sorted((p.name for p in self.directory.iterdir()
                        if EXPERIMENT_ID.match(p.name) and p.is_dir() and not p.is_symlink()), reverse=True)
        records = []
        for name in names[:limit]:
            try:
                record = self.load(name)
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
        _atomic_write(directory / (record['id'] + '.json'), _encode(kept(record)))

    def _prune(self, keep):
        folders = sorted(p for p in self.directory.iterdir()
                         if EXPERIMENT_ID.match(p.name) and p.is_dir() and not p.is_symlink())
        sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file() and not f.is_symlink()) for p in folders}
        total = sum(sizes.values())
        older = sorted((p for p in folders if p.name != keep), key=lambda p: (p.stat().st_mtime_ns, p.name))
        while older and (len(older) + 1 > self.max_records or total > self.max_bytes):
            oldest = older.pop(0)
            # Before upgrading, this may be the only copy. Preserve it before pruning.
            try:
                copy = self.root / '.mathagent' / 'index' / 'experiments' / (oldest.name + '.json')
                _directory(self.root / '.mathagent')
                _directory(copy.parent.parent, create=True)
                _directory(copy.parent, create=True)
                original = self.load(oldest.name)
                try:
                    intact = _read(copy, 65536).encode() == _encode(kept(original))
                except (OSError, ValueError):
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
    """Keep an independently readable, 64 KiB maximum description; binaries remain in the live store."""
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


def export_evidence(record):
    status = record.get('status') or 'unknown'
    text = record.get('stdout') or str(record.get('result') or '')
    return [{'kind': 'experiment', 'mode': 'numerics', 'status': 'ran' if status == 'ok' else status,
             'text': record.get('purpose') or '', 'summary': ' '.join(text.splitlines()[:1])[:500],
             'qualifiers': ['bounded record'] if record.get('truncated') else [],
             'evidence': {'sha256': hashlib.sha256(_encode(kept(record))).hexdigest(),
                          'artifacts': record.get('artifacts', [])}}]
