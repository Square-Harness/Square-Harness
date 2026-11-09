# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Skills with Agent Skills style metadata (``SKILL.md`` YAML frontmatter).

The report skills (literature, review, ...) are plain Markdown loaded by their
runners. Skills with frontmatter also declare how they may be activated::

    ---
    name: numerical-sanity-check
    description: >
      One paragraph used for discovery.
    metadata:
      invocation: automatic      # or: explicit
    ---

``explicit`` skills are bound only by controller code reacting to the user's own
command; ``automatic`` skills may be offered to the model in ordinary chat. The
binding code in ``mathagent.numerics`` enforces this; the text is not trusted to.
References (``references/*.md``) are loaded on demand (progressive disclosure).
"""
from dataclasses import dataclass
from importlib.resources import files
import re

INVOCATIONS = ('explicit', 'automatic')
SKILL_NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    invocation: str
    body: str
    references: tuple

    def section(self, title):
        """The text of one ``## title`` section of the body ('' when absent)."""
        match = re.search(r'^## ' + re.escape(title) + r'[ \t]*\n(.*?)(?=^## |\Z)', self.body, re.M | re.S)
        return match[1].strip() if match else ''

    def reference(self, name):
        if name == 'skill':
            return self.body
        if name not in self.references:
            raise ValueError(f'{self.name} has no reference {name!r}; available: ' + ', '.join(self.references))
        return _folder(self.name).joinpath('references', name + '.md').read_text(encoding='utf-8')


def _folder(name):
    if not isinstance(name, str) or not SKILL_NAME.match(name):
        raise ValueError('Invalid skill name')
    return files('mathagent').joinpath('skills', name)


def parse_frontmatter(text):
    """The small YAML subset used by SKILL.md: scalars, folded/literal blocks, one nested map."""
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---\n', 4)
    if end < 0:
        raise ValueError('Unterminated SKILL.md frontmatter')
    lines, body = text[4:end].split('\n'), text[end + 5:]
    data, i = {}, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', line)
        if not match:
            raise ValueError(f'Unsupported frontmatter line: {line!r}')
        key, value = match[1], match[2].strip()
        block = []
        while i < len(lines) and (lines[i].startswith(' ') or not lines[i].strip()):
            block.append(lines[i])
            i += 1
        if value in ('>', '|'):
            parts = [b.strip() for b in block]
            data[key] = (' ' if value == '>' else '\n').join(p for p in parts if p).strip()
        elif not value and block:
            nested = {}
            for item in block:
                if not item.strip():
                    continue
                inner = re.match(r'^\s+([A-Za-z_][\w-]*):\s*(.*?)\s*(?:#.*)?$', item)
                if not inner:
                    raise ValueError(f'Unsupported nested frontmatter line: {item!r}')
                nested[inner[1]] = inner[2].strip('\'"')
            data[key] = nested
        else:
            data[key] = value.strip('\'"')
    return data, body.lstrip('\n')


def load(name):
    folder = _folder(name)
    text = folder.joinpath('SKILL.md').read_text(encoding='utf-8')
    meta, body = parse_frontmatter(text)
    if meta.get('name') != name or not meta.get('description'):
        raise ValueError(f'Skill {name} needs frontmatter with its name and a description')
    invocation = (meta.get('metadata') or {}).get('invocation')
    if invocation not in INVOCATIONS:
        raise ValueError(f'Skill {name} must declare metadata.invocation: explicit or automatic')
    refs = folder.joinpath('references')
    references = tuple(sorted(p.name[:-3] for p in refs.iterdir() if p.name.endswith('.md'))) if refs.is_dir() else ()
    return Skill(name, meta['description'], invocation, body, references)


def catalog():
    """Every skill that declares Agent Skills metadata."""
    found = []
    for folder in sorted(files('mathagent').joinpath('skills').iterdir(), key=lambda p: p.name):
        try:
            text = folder.joinpath('SKILL.md').read_text(encoding='utf-8')
        except (OSError, NotADirectoryError):
            continue
        if text.startswith('---\n'):
            found.append(load(folder.name))
    return found
