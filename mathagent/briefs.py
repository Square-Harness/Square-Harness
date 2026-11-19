# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Role-specific views of immutable, scoped prior results.

The frozen index is the scope authority. Only the assistant and explorer can browse
it. Solver leads are fallible and optional. A proof reviewer receives only the
task, user premises, selected primary-source text and the written candidate.
"""
import hashlib
import json
import re

DOC_ID = re.compile(r'doc-[0-9a-f]{24}\Z')
WORDS = re.compile(r'[^\W_]{3,}', re.UNICODE)
STOP_WORDS = set('the and this that for with from prove proof show result using about theorem'.split())
LEAD_START = 'BEGIN PRIOR RESEARCH LEADS (fallible earlier work; never premises)'
LEAD_END = 'END PRIOR RESEARCH LEADS'


def freeze_view(root, chat, *, workspace=False):
    from .gui.store import index_view
    scope = {} if workspace else {'project': chat['project_id']} if chat.get('project_id') else {'chat': chat['id']}
    return compact_view(index_view(root, **scope, limit=200))


def compact_view(view):
    # Preserve identity, source locators and body digests. A frozen discovery view
    # need not embed thousands of copies of long native task statements.
    entries, remaining = [], 512_000
    for original in view['entries']:
        task = {**original['task'], 'text': original['task']['text'][:1400],
                'original_request': original['task'].get('original_request', '')[:1400]}
        entry = {**original, 'task': task, 'summary': original.get('summary', '')[:800]}
        size = len(json.dumps(entry, ensure_ascii=False).encode())
        if size <= remaining and len(entries) < 200:
            entries.append(entry)
            remaining -= size
    return {**view, 'entries': entries, 'omitted': len(view['entries']) - len(entries)}


def _words(text):
    return set(WORDS.findall(text.casefold())) - STOP_WORDS


def for_worker(root, view, objective, role, *, chat_id=None, preferred=(), max_chars=5000, exclude=''):
    from . import project_index
    words = _words(objective)
    preferred = set(preferred)
    def rank(entry):
        return (entry['id'] in preferred or entry.get('job_id') in preferred,
                bool(chat_id and entry.get('chat_id') == chat_id),
                len(words & _words(entry.get('task', {}).get('text', '') + ' ' + entry.get('summary', ''))))
    entries = sorted((view or {}).get('entries', []), key=rank, reverse=True)
    lines, ids = [], []
    size = len(LEAD_START) + len(LEAD_END) + 100
    for entry in entries:
        line = project_index.line(entry)
        if role == 'solver' and entry.get('kind') not in ('reference', 'source'):
            try:
                body = project_index.read_text(root, view, entry['id'])
            except (ValueError, OSError):
                continue
            if not body or body in exclude:
                continue
            if len(body) > 2400:
                body = body[:2400] + '\n[excerpt of earlier work]'
            line += '\n' + body
        if size + len(line) + 2 > max_chars:
            continue
        lines.append(line)
        ids.append(entry['id'])
        size += len(line) + 2
    text = ('\n'.join([LEAD_START, *lines, LEAD_END]) if lines else '')
    return dict(text=text, entries=ids, omitted=len(entries)-len(ids),
                sha256=hashlib.sha256(text.encode()).hexdigest())


class IndexReader:
    names = ('list_index', 'read_index')

    def __init__(self, root, view):
        self.root, self.view = root, view

    def schemas(self):
        from .tools import schema
        string, integer = {'type': 'string'}, {'type': 'integer'}
        return [schema('list_index', 'List earlier results in this conversation or project; optional literal query.',
                       {'offset': integer, 'query': string}),
                schema('read_index', 'Read a numbered excerpt of a saved, fallible earlier result.',
                       {'entry': string, 'start_line': integer, 'end_line': integer, 'start_column': integer}, ['entry'])]

    def execute(self, name, args):
        from . import project_index
        entries = self.view['entries']
        if name == 'list_index':
            offset, query = args.get('offset', 0), args.get('query', '')
            if type(offset) is not int or offset < 0 or not isinstance(query, str):
                raise ValueError('Invalid index query or offset')
            lines = [project_index.line(entry) for entry in entries]
            hits = [line for line in lines if query.casefold() in line.casefold()]
            page, size = [], 0
            for line in hits[offset:offset + 20]:
                if size + len(line) > 6000:
                    break
                page.append(line)
                size += len(line) + 1
            end = offset + len(page)
            return json.dumps(dict(entries=page, total=len(hits), next_offset=end if end < len(hits) else None), ensure_ascii=False)
        entry = next((item for item in entries if item['id'] == args.get('entry')), None)
        if entry is None:
            raise ValueError('Entry is outside this frozen index')
        body = project_index.read_text(self.root, self.view, entry['id'])
        lines = body.splitlines() or ['']
        start, column = args.get('start_line', 1), args.get('start_column', 1)
        end = args.get('end_line', min(len(lines), start + 199) if type(start) is int else 0)
        if any(type(n) is not int for n in (start, end, column)) or not 1 <= start <= end or end-start >= 200 or start > len(lines) or not 1 <= column <= max(1, len(lines[start-1])):
            raise ValueError('Read an existing range of up to 200 lines and a valid start column')
        out = [LEAD_START + '\n' + project_index.line(entry) + '\nEarlier model output; check the evidence and its qualifications.']
        available = 5900 - len(out[0])
        for n in range(start, min(end, len(lines)) + 1):
            col = column if n == start else 1
            value = lines[n-1][col-1:]
            prefix = f'{n}: '
            room = max(0, available-len(prefix)-1)
            out.append(prefix + value[:room])
            if len(value) > room:
                out.append(f'[continue at line {n}, column {col + room}]')
                break
            available -= len(prefix) + len(value) + 1
            if available < 30:
                break
        return '\n'.join([*out, LEAD_END])


def proof_reference(context):
    """Exact human statements and controller-supplied file excerpts for a fresh reviewer."""
    clean = {k: v for k, v in context.items() if k in ('query', 'files', 'images', 'project_snapshot', 'project_note_status')}
    for key in ('history', 'messages'):
        clean[key] = []
        for message in context.get(key, []):
            if message.get('role') == 'user':
                clean[key].append({k: message[k] for k in ('role', 'content', 'images') if k in message})
            elif message.get('role') == 'tool' and message.get('tool_name') in ('read_file', 'search_text'):
                clean[key].append({k: message[k] for k in ('role', 'tool_name', 'content') if k in message})
    return clean


def primary_sources(view, dependencies):
    """Resolve only explicitly linked bibliographic jobs/entries to cached, read passages."""
    selected = set(dependencies)
    refs = []
    for entry in (view or {}).get('entries', []):
        if entry.get('id') not in selected and entry.get('job_id') not in selected:
            continue
        for doc in entry.get('documents', []):
            for passage in doc.get('passages', []):
                locator = passage.get('range')
                if locator:
                    ref = (doc['document_id'], locator)
                    refs.append(ref + (doc['text_sha256'],) if doc.get('text_sha256') else ref)
    return list(dict.fromkeys(refs))
