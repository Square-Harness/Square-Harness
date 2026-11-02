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

from .executor import ARTIFACT_NAME
from ..ledger import _directory, _read, _regular

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
            self._prune(keep=experiment_id)
        return record

    def load(self, experiment_id):
        path = self.path(experiment_id, 'record.json')
        for folder in (self.root / '.mathagent', self.directory, path.parent):
            _directory(folder)
        return json.loads(_read(path))

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

    def _prune(self, keep):
        folders = sorted(p for p in self.directory.iterdir()
                         if EXPERIMENT_ID.match(p.name) and p.is_dir() and not p.is_symlink())
        sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file() and not f.is_symlink()) for p in folders}
        total = sum(sizes.values())
        while folders and (len(folders) > self.max_records or total > self.max_bytes):
            oldest = folders.pop(0)
            if oldest.name == keep:
                break
            total -= sizes[oldest]
            shutil.rmtree(oldest, ignore_errors=True)
