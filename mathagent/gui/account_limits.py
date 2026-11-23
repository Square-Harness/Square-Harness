# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Durable per-account request and token limits for the shared GUI backend."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

from ..agent import AgentError


@dataclass(frozen=True)
class AccountLimitPolicy:
    active_workflows: int = 2
    concurrent_requests: int = 2
    requests_per_minute: int = 60
    daily_tokens: int = 1_000_000

    @classmethod
    def from_environment(cls):
        names = {
            'active_workflows': ('SQUARE_ACCOUNT_ACTIVE_WORKFLOWS', 1, 8),
            'concurrent_requests': ('SQUARE_ACCOUNT_MODEL_CONCURRENCY', 1, 8),
            'requests_per_minute': ('SQUARE_ACCOUNT_REQUESTS_PER_MINUTE', 1, 100_000),
            'daily_tokens': ('SQUARE_ACCOUNT_DAILY_TOKENS', 1, 10**12),
        }
        values = {}
        defaults = cls()
        for field, (variable, lower, upper) in names.items():
            raw = os.environ.get(variable)
            value = getattr(defaults, field) if raw is None else raw
            try:
                number = int(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'{variable} must be an integer from {lower} to {upper}') from exc
            if str(number) != str(value) or not lower <= number <= upper:
                raise ValueError(f'{variable} must be an integer from {lower} to {upper}')
            values[field] = number
        return cls(**values)


class AccountLimitExceeded(AgentError):
    """An account reached its rolling request or daily token allowance."""


class AccountLimits:
    def __init__(self, path: Path, policy: AccountLimitPolicy):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError('Account usage database path must be absolute')
        self.policy = policy
        self._create()

    def _create(self):
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or
                (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
            raise ValueError('Account usage directory must be owned by the backend')
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(directory, 0o700)
        try:
            self.path.lstat()
        except FileNotFoundError:
            temporary = self.path.with_name('.account-limits-' + secrets.token_hex(16))
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            try:
                db = sqlite3.connect(temporary)
                try:
                    with db:
                        db.execute('CREATE TABLE requests (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, '
                                   'started REAL NOT NULL, utc_day TEXT NOT NULL, reserved INTEGER NOT NULL, '
                                   'charged INTEGER, input_tokens INTEGER, output_tokens INTEGER)')
                        db.execute('CREATE INDEX requests_by_account_day ON requests(account_id, utc_day)')
                        db.execute('CREATE INDEX requests_by_account_time ON requests(account_id, started)')
                finally:
                    db.close()
                try:
                    os.link(temporary, self.path, follow_symlinks=False)
                except FileExistsError:
                    pass
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                temporary.unlink()
        with self._transaction() as db:
            db.execute('SELECT id, account_id, started, utc_day, reserved, charged, input_tokens, output_tokens '
                       'FROM requests LIMIT 0')

    @contextmanager
    def _transaction(self):
        fd, db = None, None
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
                raise AgentError('Account usage database must be an owned private regular file (mode 600)')
            db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=15)
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise AgentError('Account usage database changed while opening it')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        except (OSError, sqlite3.Error):
            raise AgentError('Account usage storage is unavailable; no new model request was admitted') from None
        finally:
            if db is not None:
                db.close()
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _account_id(account_id):
        if (not isinstance(account_id, str) or len(account_id) != 64
                or any(char not in '0123456789abcdef' for char in account_id)):
            raise AgentError('Account usage requires a verified account identifier')

    def reserve(self, account_id: str, input_cap: int, output_cap: int) -> str:
        self._account_id(account_id)
        if any(type(value) is not int or value < 0 for value in (input_cap, output_cap)) or output_cap == 0:
            raise AgentError('Account usage admission requires explicit token ceilings')
        reservation = input_cap + output_cap
        if reservation > self.policy.daily_tokens:
            raise AccountLimitExceeded('This request exceeds the account daily token limit.')
        now = time.time()
        utc_day = datetime.now(timezone.utc).date().isoformat()
        identifier = secrets.token_hex(16)
        with self._transaction() as db:
            requests = db.execute('SELECT COUNT(*) FROM requests WHERE account_id=? AND started>=?',
                                  (account_id, now - 60)).fetchone()[0]
            if requests >= self.policy.requests_per_minute:
                raise AccountLimitExceeded('This account reached its model-request rate limit; try again shortly.')
            used = db.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM requests '
                              'WHERE account_id=? AND utc_day=?', (account_id, utc_day)).fetchone()[0]
            if reservation > self.policy.daily_tokens - used:
                raise AccountLimitExceeded('This account reached its daily token limit.')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL,NULL,NULL)',
                       (identifier, account_id, now, utc_day, reservation))
            db.execute('DELETE FROM requests WHERE started<?', (now - 8 * 86400,))
        return identifier

    def reserve_payload(self, account_id: str, payload: dict, image_count: int = 0) -> str:
        try:
            input_cap = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise AgentError('Cannot estimate this request for account usage admission') from exc
        if type(image_count) is not int or image_count < 0:
            raise AgentError('Invalid image count for account usage admission')
        input_cap += image_count * 4096
        options = payload.get('options')
        output_cap = options.get('num_predict') if isinstance(options, dict) else None
        if output_cap is None:
            output_cap = payload.get('max_completion_tokens')
        if output_cap is None:
            output_cap = payload.get('max_tokens')
        return self.reserve(account_id, input_cap, output_cap)

    def settle(self, identifier: str, input_tokens: int, output_tokens: int):
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise AgentError('Provider returned invalid token usage for account accounting')
        with self._transaction() as db:
            row = db.execute('SELECT charged FROM requests WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise AgentError('Account usage reservation is missing')
            amount = input_tokens + output_tokens
            if row[0] is not None:
                if row[0] == amount:
                    return
                raise AgentError('Account usage reservation was already settled differently')
            db.execute('UPDATE requests SET charged=?, input_tokens=?, output_tokens=? WHERE id=?',
                       (amount, input_tokens, output_tokens, identifier))

    def status(self, account_id: str) -> dict:
        self._account_id(account_id)
        now = time.time()
        utc_day = datetime.now(timezone.utc).date().isoformat()
        with self._transaction() as db:
            charged, reserved, inputs, outputs = db.execute(
                'SELECT COALESCE(SUM(charged),0), '
                'COALESCE(SUM(CASE WHEN charged IS NULL THEN reserved ELSE 0 END),0), '
                'COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) '
                'FROM requests WHERE account_id=? AND utc_day=?', (account_id, utc_day)).fetchone()
            requests = db.execute('SELECT COUNT(*) FROM requests WHERE account_id=? AND started>=?',
                                  (account_id, now - 60)).fetchone()[0]
        return {'daily_token_limit': self.policy.daily_tokens, 'charged_tokens': charged,
                'reserved_tokens': reserved, 'input_tokens': inputs, 'output_tokens': outputs,
                'requests_per_minute': self.policy.requests_per_minute,
                'requests_last_minute': requests,
                'active_workflows_limit': self.policy.active_workflows,
                'concurrent_requests_limit': self.policy.concurrent_requests}
