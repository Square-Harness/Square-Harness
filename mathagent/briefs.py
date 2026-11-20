# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Role-specific views of immutable, scoped prior results.

The frozen index is the scope authority. Only the assistant and explorer can browse
it. Solver leads are fallible and optional. A proof reviewer receives only the
task, the conversation as the user read it (proof_reference), selected
primary-source text and the written candidate.
"""
import hashlib
import json
import re

DOC_ID = re.compile(r'doc-[0-9a-f]{24}\Z')
WORDS = re.compile(r'[^\W_]{3,}', re.UNICODE)
STOP_WORDS = set('the and this that for with from prove proof show result using about theorem '
                 'all any are every has have holds let not one such then there use when where which '
                 'des les pour que une avec dans est sur tout'.split())
LEAD_START = 'BEGIN PRIOR RESEARCH LEADS (fallible earlier work; never premises)'
LEAD_END = 'END PRIOR RESEARCH LEADS'
LEAD_BODY = 2400
OBJECTIONS = '\n\nSelected review objections:\n'  # appended to a proof candidate by project_index.read_text
# A reply written after one of these workers reports an attempt or a verdict. The solver
# reads that record as a lead; a fresh reviewer never does.
WITHHELD_REPLY_MODES = ('prove', 'quick_review', 'detailed_review')


def freeze_view(root, chat, *, workspace=False):
    from .gui.store import index_view
    scope = {} if workspace else {'project': chat['project_id']} if chat.get('project_id') else {'chat': chat['id']}
    view = index_view(root, **scope, limit=200, findings=True)
    compact = compact_view(view)
    # compact_view counts its own cuts; the limit may already have left older entries out.
    return {**compact, 'omitted': compact['omitted'] + view['omitted']}


def compact_view(view):
    # Preserve identity, source locators and body digests. A frozen discovery view
    # need not embed thousands of copies of long native task statements.
    from . import project_index
    kept, remaining = {}, 512_000
    # When the size bound cuts, proofs and reviews stay and literature sources go first.
    for position in project_index.priority_order(view['entries']):
        original = view['entries'][position]
        task = {**original['task'], 'text': original['task']['text'][:1400],
                'original_request': original['task'].get('original_request', '')[:1400]}
        entry = {**original, 'task': task, 'summary': original.get('summary', '')[:800]}
        size = len(json.dumps(entry, ensure_ascii=False).encode())
        if size <= remaining and len(kept) < 200:
            kept[position] = entry
            remaining -= size
    return {**view, 'entries': [kept[position] for position in sorted(kept)], 'omitted': len(view['entries']) - len(kept)}


def _words(text):
    return set(WORDS.findall(text.casefold())) - STOP_WORDS


def _inert(text):
    """Saved text cannot open or close the lead block: inside it, the words of the markers are spelled in lower case."""
    return text.replace('PRIOR RESEARCH LEADS', 'prior research leads')


def _lead_body(body, room):
    """Earlier work within ``room`` characters. The objections to a rejected proof come first and the attempt is cut."""
    note = '\n[excerpt of earlier work]'
    attempt, mark, objections = body.rpartition(OBJECTIONS)
    if not mark:
        return body if len(body) <= room else body[:room - len(note)] + note
    head = 'Review objections to this attempt (fallible): '
    if len(head) + len(objections) > room * 3 // 4:
        objections = objections[:room * 3 // 4 - len(head) - len(note)] + note
    head += objections + '\nThe attempt:\n'
    return head + (attempt if len(head) + len(attempt) <= room else attempt[:room - len(head) - len(note)] + note)


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
    room = max_chars - len(LEAD_START) - len(LEAD_END) - 100
    for entry in entries:
        if role == 'solver' and not any(rank(entry)):
            break  # ranked last: neither linked to the task, nor of this conversation, nor about its words
        line = _inert(project_index.line(entry))
        left = room - len(line) - 2
        if left < 0:
            continue
        if role == 'solver' and entry.get('kind') not in ('reference', 'source'):
            if left < 400:
                continue  # too little room for a useful part of the saved text: do not read it
            try:
                body = project_index.read_text(root, view, entry['id'])
            except (ValueError, OSError):
                continue
            if not body or body in exclude:
                continue
            line += '\n' + _inert(_lead_body(body, min(LEAD_BODY, left - 1)))
        lines.append(line)
        ids.append(entry['id'])
        room -= len(line) + 2
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
            lines = [_inert(project_index.line(entry)) for entry in entries]
            hits = [line for line in lines if query.casefold() in line.casefold()]
            page, size = [], 0
            for line in hits[offset:offset + 20]:
                if size + len(line) > 6000:
                    break
                page.append(line)
                size += len(line) + 1
            end = offset + len(page)
            # `omitted` counts the older entries this frozen index left out; they are not among `total`.
            return json.dumps(dict(entries=page, total=len(hits), next_offset=end if end < len(hits) else None,
                                   omitted=self.view.get('omitted', 0)), ensure_ascii=False)
        entry = next((item for item in entries if item['id'] == args.get('entry')), None)
        if entry is None:
            raise ValueError('Entry is outside this frozen index')
        body = project_index.read_text(self.root, self.view, entry['id'])
        lines = body.splitlines() or ['']
        start, column = args.get('start_line', 1), args.get('start_column', 1)
        end = args.get('end_line', min(len(lines), start + 199) if type(start) is int else 0)
        if any(type(n) is not int for n in (start, end, column)) or not 1 <= start <= end or end-start >= 200 or start > len(lines) or not 1 <= column <= max(1, len(lines[start-1])):
            raise ValueError('Read an existing range of up to 200 lines and a valid start column')
        out = [LEAD_START + '\n' + _inert(project_index.line(entry)) + '\nEarlier model output; check the evidence and its qualifications.']
        available = 5900 - len(out[0])
        for n in range(start, min(end, len(lines)) + 1):
            col = column if n == start else 1
            value = _inert(lines[n-1][col-1:])
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


def _turns(history):
    """Earlier messages grouped by turn: each group opens with a user message."""
    turn = []
    for message in history:
        if message.get('role') == 'user' and turn:
            yield turn
            turn = []
        turn.append(message)
    if turn:
        yield turn


def _reports_verdict(turn):
    return any(call.get('function', {}).get('name') == 'delegate'
               and isinstance(call['function'].get('arguments'), dict)
               and call['function']['arguments'].get('mode') in WITHHELD_REPLY_MODES
               for message in turn for call in message.get('tool_calls') or [])


def _shared(messages, *, replies):
    for message in messages:
        role = message.get('role')
        if role == 'user':
            yield {k: message[k] for k in ('role', 'content', 'images') if k in message}
        elif role == 'tool' and message.get('tool_name') in ('read_file', 'search_text'):
            yield {k: message[k] for k in ('role', 'tool_name', 'content') if k in message}
        elif role == 'assistant' and replies and (message.get('content') or '').strip():
            yield {'role': 'assistant', 'content': message['content']}


def proof_reference(context):
    """The conversation a delegated Proof's solver and reviewer both receive, beside the task and its pinned sources.

    The human's messages, the Assistant's replies of earlier turns (a statement to
    prove may live there) and the file excerpts the controller read. Worker records,
    tool traffic, hidden reasoning and a reply that reports an earlier proof or
    review are strategy: they reach the solver alone, as leads.
    """
    clean = {k: v for k, v in context.items() if k in ('query', 'files', 'images', 'project_snapshot', 'project_note_status')}
    clean['history'] = [message for turn in _turns(context.get('history', []))
                        for message in _shared(turn, replies=not _reports_verdict(turn))]
    clean['messages'] = list(_shared(context.get('messages', []), replies=False))
    return clean


def named_entries(view, text):
    """IDs of the view's entries that ``text`` names. A period ends a sentence unless an entry number follows."""
    return [entry['id'] for entry in (view or {}).get('entries', [])
            if re.search(r'(?<![\w.-])' + re.escape(entry['id']) + r'(?![\w-]|\.\w)', text)]


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
