#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Save an OpenRouter API key for `scripts/start-deepinfra.py --provider openrouter`.

The key is read from a hidden prompt (never a command-line argument or an environment variable), checked for the
OpenRouter format, and written to .local/openrouter/credential with mode 600 in a private directory. Only the path
is printed. Unlike DeepInfra's scoped keys, an OpenRouter key has full account power: give it a credit limit in the
OpenRouter dashboard, and turn on Zero Data Retention for all models in its privacy settings.
"""
import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / '.local' / 'openrouter' / 'credential'
KEY = re.compile(r'^sk-or-[A-Za-z0-9_-]{16,200}$')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--replace', action='store_true', help='Replace a saved key without asking')
    options = parser.parse_args()
    if not sys.stdin.isatty():
        print('Run this in a terminal: the key is read from a hidden prompt only; nothing was saved.', file=sys.stderr)
        return 1
    if TARGET.exists() and not options.replace:
        answer = input(f'A key is already saved at {TARGET}. Replace it? [y/N] ').strip().lower()
        if answer not in {'y', 'yes'}:
            print('Nothing was changed.')
            return 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)  # never read the key with echo on
            key = getpass.getpass('OpenRouter API key (hidden): ').strip()
    except getpass.GetPassWarning:
        print('The terminal cannot hide the key; nothing was saved.', file=sys.stderr)
        return 1
    if not KEY.match(key):
        print('This does not look like an OpenRouter key (they start with sk-or-); nothing was saved.', file=sys.stderr)
        return 1
    os.umask(0o077)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    info = TARGET.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        print(f'{TARGET.parent} must be a directory owned by you; nothing was saved.', file=sys.stderr)
        return 1
    os.chmod(TARGET.parent, 0o700)
    temporary = TARGET.with_name('.credential-' + secrets.token_hex(16))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='ascii') as handle:
            descriptor = None
            handle.write(key + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, TARGET)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    print(f'Saved to {TARGET} (mode 600). Start with: python3 scripts/start-deepinfra.py --provider openrouter')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
