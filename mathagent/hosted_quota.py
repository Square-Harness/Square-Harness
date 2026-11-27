# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Optional spending limit for a hosted tester; stores amounts, never content.

A call is admitted while the tester has spent less than the limit. Nothing is reserved before a call, so any
number of calls can run in parallel. When a call ends it is charged what it cost: the price the provider reports
(OpenRouter), otherwise its reported tokens at the configured rates. A call whose usage the provider never
reported (a silent, stalled or broken stream) is charged an estimate: its whole input and every streamed
character plus 256 tokens. Calls already running when the limit is reached finish and are charged, so the total
can end slightly above the limit; no new call starts after that. Opening an existing ledger never grants a new
allowance. The administrator must place this file outside every served workspace.
"""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat

from .agent import AgentError
from .deepinfra import TesterAllowanceInsufficient

NANODOLLARS = Decimal(1_000_000_000)
MAX_INTEGER = 2 ** 63 - 1


def _decimal(value):
    try:
        number = Decimal(value)
        if number.is_finite() and number > 0:
            return number
    except (InvalidOperation, ValueError, TypeError):
        pass
    raise AgentError('Tester allowance configuration must contain positive finite amounts')


def quota_status():
    """Return public aggregate counters, or None for ordinary installations."""
    quota = HostedQuota.from_environment()
    return quota.status() if quota is not None else None


class HostedQuota:
    @classmethod
    def from_environment(cls, service_tier=None):
        """The configured ledger, or None. A call passes its service tier: the rates are Standard prices."""
        path = os.environ.get('SH_DEEPINFRA_QUOTA_FILE')
        if not path:
            return None
        if service_tier not in (None, 'standard'):
            raise AgentError('Tester allowance requires the Standard service tier')
        return cls(path, os.environ.get('SH_DEEPINFRA_QUOTA_USD', '2.50'),
                   os.environ.get('SH_DEEPINFRA_QUOTA_INPUT_PER_MILLION', '0.20'),
                   os.environ.get('SH_DEEPINFRA_QUOTA_OUTPUT_PER_MILLION', '0.60'))

    def __init__(self, path, usd='2.50', input_per_million='0.20', output_per_million='0.60'):
        self.path = Path(path)
        self.input_rate, self.output_rate = _decimal(input_per_million), _decimal(output_per_million)
        self.budget = int((_decimal(usd) * NANODOLLARS).to_integral_value(rounding=ROUND_FLOOR))
        if not 0 < self.budget <= MAX_INTEGER or not self.path.is_absolute():
            raise AgentError('Invalid tester allowance configuration')
        self.settings = (self.budget, str(self.input_rate.normalize()), str(self.output_rate.normalize()))
        try:
            info = self.path.parent.stat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise AgentError('Tester allowance directory must be owned and private (mode 700)')
            self._create()
            with self._transaction() as db:
                if db.execute('SELECT budget, input_rate, output_rate FROM settings WHERE id=1').fetchone() != self.settings:
                    raise AgentError('Tester allowance settings changed; existing allowance was preserved')
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='charges'").fetchone():
                    # The short-lived charges-only schema. Keep every row and make the ledger readable by
                    # earlier releases again. A reservation-era ledger already has this shape and needs no rewrite.
                    db.execute(_CALLS)
                    db.execute('INSERT INTO calls (id, charged, input_tokens, output_tokens) '
                               'SELECT lower(hex(randomblob(16))), charged, input_tokens, output_tokens FROM charges')
                    db.execute('DROP TABLE charges')
        except (OSError, sqlite3.Error):
            raise AgentError('Tester allowance storage is unavailable; no new allowance was granted') from None

    def _create(self):
        try:
            self.path.lstat()
            return
        except FileNotFoundError:
            pass
        temporary = self.path.with_name('.quota-' + secrets.token_hex(16))
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        try:
            db = sqlite3.connect(temporary)
            try:
                with db:
                    db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), budget INTEGER, input_rate TEXT, output_rate TEXT)')
                    db.execute('INSERT INTO settings VALUES (1,?,?,?)', self.settings)
                    db.execute(_CALLS)
            finally:
                db.close()
            try:
                os.link(temporary, self.path, follow_symlinks=False)
            except FileExistsError:
                pass  # Another process initialized it; its config is checked below.
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink()

    @contextmanager
    def _transaction(self):
        fd, db = None, None
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise AgentError('Tester allowance must be an owned private regular file (mode 600)')
            db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=15)
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise AgentError('Tester allowance file changed while opening it')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        except (OSError, sqlite3.Error):
            raise AgentError('Tester allowance storage is unavailable') from None
        finally:
            if db is not None:
                db.close()
            if fd is not None:
                os.close(fd)

    def admit(self):
        """Refuse a new call once the tester has spent the whole allowance."""
        with self._transaction() as db:
            if db.execute('SELECT COALESCE(SUM(charged),0) FROM calls').fetchone()[0] >= self.budget:
                raise TesterAllowanceInsufficient()

    def charge(self, input_tokens, output_tokens, cost_usd=None, estimated=False):
        """Record a finished call: the provider's reported price in USD when it gives one, otherwise the tokens at
        the configured rates. With estimated=True the tokens are an upper estimate for a stream whose usage the
        provider never reported; they are charged but left out of the token counters."""
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise AgentError('Tester allowance received invalid usage')
        if (type(cost_usd) in (int, float) and math.isfinite(cost_usd) and cost_usd >= 0 and not estimated):
            amount = Decimal(repr(cost_usd)) * NANODOLLARS
        else:
            amount = (input_tokens * self.input_rate + output_tokens * self.output_rate) * 1000
        charged = int(amount.to_integral_value(rounding=ROUND_CEILING))
        if not 0 <= charged <= MAX_INTEGER:
            raise AgentError('Tester allowance cost exceeds its supported range')
        with self._transaction() as db:
            db.execute('INSERT INTO calls (id, charged, input_tokens, output_tokens) VALUES (?,?,?,?)',
                       (secrets.token_hex(16), charged, None if estimated else input_tokens, None if estimated else output_tokens))

    def status(self):
        with self._transaction() as db:
            charged, inputs, outputs = db.execute(
                'SELECT COALESCE(SUM(charged),0), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) '
                'FROM calls WHERE charged IS NOT NULL').fetchone()
        return {'limit_nanos': self.budget, 'charged_nanos': charged, 'input_tokens': inputs, 'output_tokens': outputs,
                'input_per_million': self.settings[1], 'output_per_million': self.settings[2]}


# Older releases use the cap and reserved columns. This version only inserts completed charges.
_CALLS = ('CREATE TABLE calls (id TEXT PRIMARY KEY, input_cap INTEGER, output_cap INTEGER, reserved INTEGER, '
          'charged INTEGER, input_tokens INTEGER, output_tokens INTEGER)')
