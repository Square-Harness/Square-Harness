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
# The Assistant reads its digest beside the conversation: no record may pass for the request.
ASSISTANT_NOTE = ('Earlier records of this conversation or project, for reference only. None of them is the '
                  "current request, which is the user's latest message. In an answer, name a record by what it "
                  'is about; its entry id is only for read_index.')
OBJECTIONS = '\n\nSelected review objections:\n'  # appended to a proof candidate by project_index.read_text
# A reply written after one of these workers reports an attempt or a verdict. The solver
# reads that record as a lead; a fresh reviewer never does.
WITHHELD_REPLY_MODES = ('prove', 'quick_review', 'detailed_review')


_PAGE_BYTES = 512_000  # A storage page, never a limit on how many records the frozen view can discover.


def _page_path(root, digest, *, create=False):
    from pathlib import Path
    from .ledger import _directory
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid frozen research index page')
    folder = Path(root) / '.mathagent'
    _directory(folder, create=create)
    folder /= 'index-pages'
    _directory(folder, create=create)
    return folder / (digest + '.json')


def freeze_view(root, chat, *, workspace=False):
    from .gui.store import index_view
    from .ledger import _atomic_write
    scope = {} if workspace else {'project': chat['project_id']} if chat.get('project_id') else {'chat': chat['id']}
    view = index_view(root, **scope, findings=True)
    pages, entries, size = [], [], 0
    def write_page():
        data = ('[' + ','.join(entries) + ']').encode()
        digest = hashlib.sha256(data).hexdigest()
        path = _page_path(root, digest, create=True)
        if not path.exists():
            _atomic_write(path, data)
        pages.append(digest)
    for entry in view['entries']:
        row = json.dumps(entry, ensure_ascii=False, separators=(',', ':'))
        n = len(row.encode())
        if entries and size + n >= _PAGE_BYTES:
            write_page()
            entries, size = [], 0
        entries.append(row)
        size += n + 1
    if not pages and size < _PAGE_BYTES:
        return view
    if entries:
        write_page()
    return dict(view, entries=[], pages=pages)


def load_view(root, view):
    """Resolve immutable pages or a saved inline view from an earlier version."""
    from .ledger import _read
    if not view or not view.get('pages'):
        return view
    entries = []
    for digest in view['pages']:
        raw = _read(_page_path(root, digest))
        if hashlib.sha256(raw.encode()).hexdigest() != digest:
            raise ValueError('The frozen research index page changed')
        entries.extend(json.loads(raw))
    # This is the materialized view. A worker can now prepend the outputs of
    # completed sibling jobs. Loading its IndexReader must preserve that merge.
    resolved = dict(view, entries=entries)
    del resolved['pages']
    return resolved


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


def for_worker(root, view, objective, role, *, chat_id=None, preferred=(), max_chars=None, exclude=''):
    from . import project_index
    if max_chars is None:
        from .tools import MAX_RESULT
        max_chars = MAX_RESULT
    words = _words(objective)
    preferred = set(preferred)
    def rank(entry):
        return (entry['id'] in preferred or entry.get('job_id') in preferred,
                bool(chat_id and entry.get('chat_id') == chat_id),
                len(words & _words(entry.get('task', {}).get('text', '') + ' ' + entry.get('summary', ''))))
    entries = sorted((view or {}).get('entries', []), key=rank, reverse=True)
    lines, ids = [], []
    room = max_chars - len(LEAD_START) - len(LEAD_END) - 100
    if role == 'assistant':
        room -= len(ASSISTANT_NOTE) + 100  # the note and the count of records left out
    for entry in entries:
        if role in ('solver', 'assistant') and not any(rank(entry)):
            break  # ranked last: neither linked to the task, nor of this conversation, nor about its words
        line = _inert(project_index.line(entry, room=max(0, room // 2)))
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
            line += '\n' + _inert(_lead_body(body, left - 1))
        lines.append(line)
        ids.append(entry['id'])
        room -= len(line) + 2
    omitted = len(entries) - len(ids)
    if role == 'assistant' and entries:
        left_out = [f'Earlier records not listed here: {omitted} (list_index lists them when available).'] if omitted else []
        lines = [ASSISTANT_NOTE, *lines, *left_out]
    text = ('\n'.join([LEAD_START, *lines, LEAD_END]) if lines else '')
    return dict(text=text, entries=ids, omitted=omitted,
                sha256=hashlib.sha256(text.encode()).hexdigest())


class IndexReader:
    names = ('list_index', 'read_index')

    def __init__(self, root, view):
        self.root, self.view = root, load_view(root, view)

    def schemas(self):
        from .tools import schema
        string, integer = {'type': 'string'}, {'type': 'integer'}
        return [schema('list_index', 'List earlier results in this conversation or project; optional literal query.',
                       {'offset': integer, 'query': string}),
                schema('read_index', 'Read numbered text from the saved result body or exact request, task or summary.',
                       {'entry': string, 'part': {'type': 'string', 'enum': ['body', 'request', 'task', 'summary']}, 'start_line': integer, 'end_line': integer, 'start_column': integer}, ['entry'])]

    def execute(self, name, args, budget=6000):
        """`budget`: characters a result may hold, the workspace's result budget (tools.result_budget)."""
        from . import project_index
        entries = self.view['entries']
        if name == 'list_index':
            offset, query = args.get('offset', 0), args.get('query', '')
            if type(offset) is not int or offset < 0 or not isinstance(query, str):
                raise ValueError('Invalid index query or offset')
            hits = [e for e in entries if not query or query.casefold() in project_index.line(e).casefold()]
            page, size = [], 0
            for entry in hits[offset:]:
                line = _inert(project_index.line(entry, room=budget - 180))
                if page and size + len(line) + 180 > budget:
                    break
                page.append(line)
                size += len(line) + 1
            end = offset + len(page)
            # `omitted` counts the older entries this frozen index left out; they are not among `total`.
            return json.dumps(dict(entries=page, total=len(hits), next_offset=end if end < len(hits) else None,
                                   omitted=self.view.get('omitted', 0),
                                   naming='Name a record by what it is about; its entry id is only for read_index.'),
                              ensure_ascii=False)
        entry = next((item for item in entries if item['id'] == args.get('entry')), None)
        if entry is None:
            raise ValueError('Entry is outside this frozen index')
        part = args.get('part', 'body')
        if part == 'body':
            body = project_index.read_text(self.root, self.view, entry['id'])
        elif part in ('request', 'task', 'summary'):
            body = entry['summary'] if part == 'summary' else entry['task'].get(
                'original_request' if part == 'request' else 'text', '')
        else:
            raise ValueError('Choose body, request, task or summary')
        lines = body.splitlines() or ['']
        start, column = args.get('start_line', 1), args.get('start_column', 1)
        end = args.get('end_line', len(lines) if type(start) is int else 0)
        if any(type(n) is not int for n in (start, end, column)) or not 1 <= start <= end or start > len(lines) or not 1 <= column <= max(1, len(lines[start-1])):
            raise ValueError('Read an existing range of lines and a valid start column')
        out = [LEAD_START + '\n' + _inert(project_index.line(entry, room=budget // 3)) + '\nEarlier model output; check the evidence and its qualifications.']
        available = budget - 100 - len(out[0])
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


def _shared(messages, *, replies, current=False):
    for message in messages:
        role = message.get('role')
        if role == 'user':
            yield {k: message[k] for k in ('role', 'content', 'images') if k in message}
        elif role == 'tool' and (message.get('tool_name') in ('read_file', 'search_text') or
                                 current and message.get('tool_name') in ('read_page', 'search_page', 'read_references')):
            yield {k: message[k] for k in ('role', 'tool_name', 'content') if k in message}
        elif role == 'assistant' and replies and (message.get('content') or '').strip():
            yield {'role': 'assistant', 'content': message['content']}


def proof_reference(context):
    """The conversation a delegated Proof's solver and reviewer both receive, beside the task and its pinned sources.

    The human's messages, the Assistant's replies of earlier turns (a statement to
    prove may live there) and the file and web excerpts the controller read. Worker records,
    tool traffic, hidden reasoning and a reply that reports an earlier proof or
    review are strategy: they reach the solver alone, as leads.
    """
    clean = {k: v for k, v in context.items() if k in
             ('query', 'files', 'images', 'project_snapshot', 'project_note_status', 'notebook_source')}
    clean['history'] = [message for turn in _turns(context.get('history', []))
                        for message in _shared(turn, replies=not _reports_verdict(turn))]
    clean['messages'] = list(_shared(context.get('messages', []), replies=False, current=True))
    return clean


def earlier_project_questions(root, view, entry_ids, chat_id):
    """Recover the human's actual questions behind relevant findings of other project chats."""
    from .gui.store import NotFound, load_chat
    if not view or not view.get('scope', '').startswith('project:'):
        return []
    questions = []
    chats = {}
    for entry in view['entries']:
        owner = entry.get('chat_id')
        original = entry['task'].get('original_request')
        if entry['id'] not in entry_ids or not owner or owner == chat_id or not original:
            continue
        if owner not in chats:
            try:
                chat = load_chat(root, owner)
            except (NotFound, ValueError, OSError):
                chat = {}
            transcript = chat.get('transcript', []) if view['scope'] == 'project:' + str(chat.get('project_id')) else []
            chats[owner] = [item.get('content', '') for item in transcript if item.get('role') == 'user']
        # The frozen index stores up to 1400 characters. Resolve a prefix only
        # if it identifies one exact original. A shortened theorem is ambiguous.
        matches = {query for query in chats[owner] if query == original or len(original) == 1400 and query.startswith(original)}
        if len(matches) == 1:
            question = matches.pop()
            if question not in questions:
                questions.append(question)
    return questions


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
