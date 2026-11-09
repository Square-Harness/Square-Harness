#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Create a capped DeepInfra test credential without saving the parent API key.

The secret is read from a terminal and sent only to DeepInfra over verified
HTTPS. Credentials live in owner-only directories; symlinks and replacement
without an explicit flag are refused. This script makes no inference calls.
"""

import argparse
import fcntl
import getpass
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
import urllib.error
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone


MODEL = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
ENDPOINT = 'https://api.deepinfra.com/v1/scoped-jwt'
ROOT = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    """A user-facing error whose text contains no secrets."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dollar_limit(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('limit must be a USD amount') from None
    if not math.isfinite(number) or not 0.01 <= number <= 1.0:
        raise argparse.ArgumentTypeError('the local test limit must be $0.01 to $1.00')
    return number


def hours_limit(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('hours must be an integer') from None
    if not 1 <= number <= 24:
        raise argparse.ArgumentTypeError('hours must be between 1 and 24')
    return number


def private_directory(parent, name):
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent,
    )
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
            raise SetupError('Credential directories must be owned by your user.')
        os.fchmod(descriptor, 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def existing_credential(directory):
    try:
        info = os.stat('credential', dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise SetupError('Existing credential is unsafe; expected your own regular 0600 file.')
    return info


def lock_directory(directory):
    descriptor = os.open(
        '.setup.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600,
        dir_fd=directory,
    )
    try:
        info = os.fstat(descriptor)
        if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1):
            raise SetupError('Credential setup lock is unsafe.')
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('Another DeepInfra credential setup is already running.') from None
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def request_credential(parent_key, key_name, limit, hours):
    payload = json.dumps({
        'api_key_name': key_name,
        'models': [MODEL],
        'expires_delta': hours * 3600,
        'spending_limit': limit,
    }).encode('utf-8')
    request = urllib.request.Request(
        ENDPOINT, data=payload, method='POST',
        headers={
            'Authorization': 'Bearer ' + parent_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect(),
    )
    try:
        with opener.open(request, timeout=45) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise SetupError('DeepInfra returned an unexpectedly large response; nothing saved.')
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status == 401:
            raise SetupError('DeepInfra rejected the API key (HTTP 401); create or copy it again.') from None
        if status == 403:
            raise SetupError(
                'DeepInfra refused scoped-token creation (HTTP 403). In Dashboard → Keys, '
                'enable inference and account_scoped_jwt on the parent key.'
            ) from None
        if status == 422:
            raise SetupError(
                'DeepInfra rejected the settings (HTTP 422). Check that --key-name '
                'exactly matches the parent key name in Dashboard → Keys.'
            ) from None
        raise SetupError(f'DeepInfra refused the request (HTTP {status}); nothing saved.') from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise SetupError('Could not securely reach DeepInfra; check your network. Nothing saved.') from None
    try:
        token = json.loads(body)['token']
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise SetupError('DeepInfra returned an invalid credential response; nothing saved.') from None
    if (not isinstance(token, str) or len(token) > 32768
            or re.fullmatch(r'jwt:[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token) is None):
        raise SetupError('DeepInfra returned an unexpected token format; nothing saved.')
    return token


def save_credential(directory, token, previous):
    name = '.credential-' + secrets.token_hex(16)
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
        dir_fd=directory,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='ascii') as handle:
            descriptor = None
            handle.write(token + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        current = existing_credential(directory)
        if previous is None:
            if current is not None:
                raise SetupError('A credential appeared during setup; refusing to overwrite it.')
            # link is an atomic create-if-absent; an unexpected file cannot be replaced.
            os.link(name, 'credential', src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False)
        else:
            if current is None or (current.st_dev, current.st_ino) != (previous.st_dev, previous.st_ino):
                raise SetupError('The existing credential changed during setup; refusing replacement.')
            os.replace(name, 'credential', src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-name', default='square-harness-local',
                        help='parent API key name shown in the DeepInfra dashboard')
    parser.add_argument('--limit', type=dollar_limit, default=1.0,
                        help='maximum token allowance in USD, $0.01–$1.00 (default: 1)')
    parser.add_argument('--hours', type=hours_limit, default=24,
                        help='credential lifetime, 1–24 hours (default: 24)')
    parser.add_argument('--replace', action='store_true',
                        help='explicitly mint a NEW allowance and replace the saved credential; '
                             'the old token is not revoked')
    arguments = parser.parse_args()
    if not arguments.key_name.strip() or any(ord(char) < 32 for char in arguments.key_name):
        parser.error('--key-name must be a nonempty dashboard key name')
    descriptors = []
    try:
        root = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root)
        local = private_directory(root, '.local')
        descriptors.append(local)
        directory = private_directory(local, 'deepinfra')
        descriptors.append(directory)
        descriptors.append(lock_directory(directory))
        previous = existing_credential(directory)
        if previous is not None and not arguments.replace:
            raise SetupError(
                'A credential already exists. No new allowance was created. Use --replace '
                'only if you intend to mint a new independent spending allowance.'
            )
        if arguments.replace:
            print('WARNING: --replace creates a NEW independent spending allowance. '
                  'It does not revoke an older token, which may remain usable until expiry.',
                  file=sys.stderr)
        if not sys.stdin.isatty():
            raise SetupError('Run this script in your terminal so the API key can be entered invisibly.')
        print(f'Creating a {arguments.hours}-hour credential for {MODEL}, '
              f'limited to ${arguments.limit:.2f}. No inference calls will be made.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            parent_key = getpass.getpass('Paste the parent DeepInfra API key (hidden): ').strip()
        if not parent_key or any(char.isspace() for char in parent_key):
            raise SetupError('Enter only the API key, without a Bearer prefix or whitespace.')
        expiry = datetime.now(timezone.utc) + timedelta(hours=arguments.hours)
        try:
            token = request_credential(parent_key, arguments.key_name,
                                       arguments.limit, arguments.hours)
        finally:
            parent_key = None
        save_credential(directory, token, previous)
        print('Saved private credential: ' + str(ROOT / '.local/deepinfra/credential'))
        print('Model: ' + MODEL)
        print(f'USD allowance: ${arguments.limit:.2f}')
        print('Requested expiry: ' + expiry.strftime('%Y-%m-%d %H:%M:%S UTC'))
        return 0
    except (SetupError, getpass.GetPassWarning) as error:
        print('Setup stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSetup cancelled; no inference calls were made.', file=sys.stderr)
        return 1
    except OSError:
        print('Setup stopped: could not safely access the private credential files.', file=sys.stderr)
        return 1
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


if __name__ == '__main__':
    sys.exit(main())
