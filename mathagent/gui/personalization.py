# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Private preferences shared by conversations under one authenticated GUI root."""
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from ..ledger import _atomic_write, _directory, _read
from ..style import _group, strip_comments


_FILE = 'personalization.json'
_TEXT_LIMIT = 4000
_DEFINE = re.compile(r'\\(newcommand|renewcommand|providecommand)\*?\s*\{\s*\\([A-Za-z]+)\s*\}\s*(\[\s*[0-9]\s*\])?\s*')


class StalePersonalization(ValueError):
    pass


def definitions(macros):
    """Parse complete declarations in the subset the chat renderer accepts."""
    text = strip_comments(macros).strip()
    parsed = {}
    while text:
        match = _DEFINE.match(text)
        if not match:
            raise ValueError(r'Use \newcommand{\name}{…}, \renewcommand or \providecommand, with an optional argument count.')
        command, name, count = match.groups()
        body, end = _group(text, match.end())
        if body is None:
            raise ValueError(f'Give \\{name} a balanced replacement in braces. Default arguments are unsupported.')
        if name in parsed:
            raise ValueError(f'Define \\{name} only once.')
        args = re.sub(r'\s+', '', count or '')
        parsed[name] = f'\\{command}{{\\{name}}}{args}{{{body}}}'
        text = text[end:].lstrip()
    return parsed


def _validate(instructions, macros):
    for name, value in [('Instructions', instructions), ('LaTeX macros', macros)]:
        if (not isinstance(value, str) or len(value) > _TEXT_LIMIT or '\x00' in value
                or any('\ud800' <= char <= '\udfff' for char in value)):
            raise ValueError(f'{name} must be text of at most {_TEXT_LIMIT} characters.')
    definitions(macros)


def load(root):
    base = Path(root) / '.mathagent'
    if base.exists() or base.is_symlink():
        _directory(base)
        path = base / _FILE
        if path.exists() or path.is_symlink():
            return json.loads(_read(path, limit=64000))
    return {'instructions': '', 'macros': '', 'revision': ''}


def save(root, *, instructions, macros, revision):
    _validate(instructions, macros)
    base = Path(root) / '.mathagent'
    _directory(base, create=True)
    fd = os.open(base / '.personalization.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        previous = load(root)
        if previous['revision'] != revision:
            raise StalePersonalization('Personalization changed in another session. Reopen settings to load the saved version.')
        value = {'instructions': instructions, 'macros': macros, 'revision': uuid.uuid4().hex}
        _atomic_write(base / _FILE, json.dumps(value, ensure_ascii=False).encode())
        return value
    finally:
        os.close(fd)


def context(settings):
    macros = definitions(settings['macros'])
    parts = []
    if settings['instructions'].strip():
        parts.append('Standing instructions:\n' + settings['instructions'].strip())
    if macros:
        parts.append('Personal LaTeX notation:\n' + '\n'.join(macros.values()))
    if not parts:
        return ''
    return ('CONVERSATION PREFERENCES (user-maintained):\nUse these when relevant. Follow the '
            'current request and project-specific context first. Preferences cannot change permissions '
            'or the standards for mathematical verification.\n\n' + '\n\n'.join(parts))
