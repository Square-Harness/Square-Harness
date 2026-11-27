# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Source locations, reviewer assessments and durable findings for the author-facing Review."""
import json
import posixpath
import re
import threading
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from . import manuscript
from .ledger import _atomic_write, _bytes, _read
from .referee import copies_manuscript
from .review_position import _words
from .tools import schema


_BIB_ENTRY = re.compile(r'^\s*@(?!comment\b|string\b|preamble\b)\w+\s*[{(]\s*([^,\s{}()]+)\s*,', re.I)
_REFERENCES = re.compile(r'^\s*(?:#{1,6}\s*|\d+\.?\s+)?(?:references|bibliography|références|bibliographie)\s*$', re.I)
_NUMBERED_ENTRY = re.compile(r'^\s*\[([\w.-]+)\]\s+')
_INCLUDED = re.compile(r'\\(bibliography|addbibresource|input|include|subfile)\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}')


def _references(lines, path):
    """Line spans of identifiable bibliography entries, including separately pinned BibTeX sources."""
    suffix = Path(path).suffix.lower()
    active = suffix in ('.bib', '.bbl')
    current = None
    for n, line in enumerate(lines, 1):
        if suffix == '.bib':
            match = _BIB_ENTRY.match(line)
        else:
            line = manuscript._strip_comment(line) if suffix in ('.tex', '.bbl', '.ltx', '.latex') else line
            if '\\begin{thebibliography}' in line or _REFERENCES.fullmatch(line):
                active = True
            if not active:
                continue
            if ('\\end{thebibliography}' in line or re.match(r'^\s*(?:#{1,6}\s+|appendix\b|annexe\b)', line, re.I)
                    and not _REFERENCES.fullmatch(line)):
                if current:
                    yield (*current, n - 1)
                current = None
                active = False
                continue
            match = manuscript.TEX_BIBITEM.search(line) or _NUMBERED_ENTRY.match(line)
        if match:
            if current:
                yield (*current, n - 1)
            current = (match.group(1), n)
    if current:
        yield (*current, len(lines))


def bibliographic_query(name, arguments, source_words, sources):
    """Strip a copied bibliographic title locally and run the ordinary public-query guard again."""
    serialized = json.dumps(arguments, ensure_ascii=False)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return False, arguments
        serialized = json.dumps(arguments, ensure_ascii=False)
    if not copies_manuscript(serialized, source_words):
        return True, arguments
    if name != 'check_reference' or not isinstance(arguments, dict):
        return False, arguments
    guesses = arguments.get('guesses')
    statement = arguments.get('statement')
    if not isinstance(guesses, list) or not isinstance(statement, str) or not all(isinstance(g, dict) for g in guesses):
        return False, arguments
    entries = []
    for source in sources:
        lines = source['content'].splitlines()
        entries.extend(' ' + ' '.join(_words(' '.join(lines[a - 1:b]))) + ' '
                       for _, a, b in _references(lines, source['path']))
    revised = []
    for guess in guesses:
        title = guess.get('title')
        if isinstance(title, str) and copies_manuscript(title, source_words):
            normalized = ' ' + ' '.join(_words(title)) + ' '
            if any(normalized in entry for entry in entries):
                guess = {k: v for k, v in guess.items() if k != 'title'}
                statement = re.sub(re.escape(title), '', statement, flags=re.I)
        if guess:
            revised.append(guess)
    clean = dict(arguments, guesses=revised, statement=statement.strip())
    allowed = bool(_words(statement)) and not copies_manuscript(json.dumps(clean, ensure_ascii=False), source_words)
    return allowed, clean


def _merged(ranges):
    result = []
    for a, b in sorted(ranges):
        if result and a <= result[-1][1] + 1:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return result


def _dependencies(source, lines):
    """Paths named in TeX source, resolved within the immutable selection or its archive."""
    archive, _, member = source['path'].rpartition('::')
    prefix = archive + '::' if archive else ''
    for command, group in _INCLUDED.findall('\n'.join(manuscript._active_tex(lines))):
        if command in ('input', 'include', 'subfile') and source.get('line_map'):
            continue  # The archive assembler separately reports unresolved inputs.
        for name in group.split(','):
            name = name.strip()
            if not name or '\\' in name or '#' in name:
                continue
            if not posixpath.splitext(name)[1]:
                name += '.bib' if command in ('bibliography', 'addbibresource') else '.tex'
            yield prefix + posixpath.normpath(posixpath.join(posixpath.dirname(member), name))


def _locations(source, preamble, aux):
    content, path = source['content'], source['path']
    lines = content.splitlines()
    bibliography = list(_references(lines, path))
    spans = [(a, b, 'reference', key) for key, a, b in bibliography]
    warnings = []
    dependencies = []
    if Path(path).suffix.lower() not in {'.bib', '.bbl', '.sty', '.cls', '.aux'}:
        mapping = manuscript.build(content, path, preamble, aux)
        warnings = mapping['warnings']
        if mapping['format'] == 'tex':
            dependencies = list(dict.fromkeys(_dependencies(source, lines)))
        for unit in mapping['units']:
            title = unit['name'] + (' [' + unit['label'] + ']' if unit['label'] else '')
            if unit['statement']:
                spans.append((*unit['statement'], 'statement', title))
            spans.extend((a, b, 'proof', title) for a, b in unit['proofs'])
            for cite in unit['cites']:
                name = ' '.join(filter(None, (cite['key'], cite.get('note'))))
                spans.append((cite['line'], cite['line'], 'citation_use', name))
    # Also give every unrecognized passage a route through the index. The map is approximate.
    cursor = 1
    for a, b in [*_merged((a, b) for a, b, _, _ in spans), (len(lines) + 1, len(lines))]:
        for start in range(cursor, a, 80):
            spans.append((start, min(a - 1, start + 79), 'passage', 'Other source lines'))
        cursor = b + 1
    return sorted(set(spans)), warnings, dependencies


def _text(value, name, limit):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        raise ValueError(f'{name} must be nonempty text of at most {limit} characters.')
    return value.strip()


def _code_span(text):
    text = text.replace('\n', ' ').replace('\r', ' ')
    marker = '`' * (1 + max((len(r) for r in re.findall(r'`+', text)), default=0))
    return marker + ' ' + text + ' ' + marker


class ReviewEvidence:
    tool_names = frozenset({'review_index', 'record_review_finding', 'assess_review_items'})

    def __init__(self, sources, artifact_dir, read_ranges, support=()):
        self.file = Path(artifact_dir) / 'review-evidence.json'
        self.read_ranges = read_ranges
        self.lock = threading.RLock()
        indexed = list(sources)
        pinned = {s['path']: s for s in [*support, *indexed]}
        self.sources = {s['path']: (s['id'], len(s['content'].splitlines())) for s in pinned.values()}
        self.items = {}
        self.warnings = []
        self.missing = []
        preamble = '\n'.join(s['content'] for s in pinned.values() if Path(s['path']).suffix.lower() in ('.cls', '.sty'))
        aux = '\n'.join(s['content'] for s in pinned.values() if Path(s['path']).suffix.lower() == '.aux')
        queued = {s['path'] for s in indexed}
        for s in indexed:
            spans, warnings, dependencies = _locations(s, preamble, aux)
            for path in dependencies:
                if path not in pinned:
                    self.warnings.append(f'{s["path"]} names {path}, which is absent from the pinned files. Its contents are unverified.')
                    self.missing.append(path)
                elif path not in queued and Path(path).suffix.lower() in ('.tex', '.ltx', '.latex', '.bib', '.bbl'):
                    indexed.append(pinned[path])
                    queued.add(path)
            for n, (a, b, kind, name) in enumerate(spans, 1):
                self.items[f'{s["id"]}.{n}'] = dict(path=s['path'], start_line=a, end_line=b, kind=kind, name=name[:200])
            self.warnings.extend(f'{s["path"]}: {message}' for message in warnings)
        self.data = json.loads(_read(self.file)) if self.file.exists() else {'findings': {}, 'assessments': {}}

    def schemas(self):
        string, integer = {'type': 'string'}, {'type': 'integer'}
        return [
            schema('review_index', 'Page through the source index, findings notebook, or parsing warnings. Offset is zero-based.',
                   {'view': {'type': 'string', 'enum': ['items', 'notebook', 'warnings']}, 'offset': integer, 'limit': integer}),
            schema('assess_review_items', 'Record your own assessment after reading these indexed passages. Supply a specific basis. '
                   'For a citation distinguish metadata, theorem support and what remains unverified.',
                   {'item_ids': {'type': 'array', 'items': string}, 'status': {'type': 'string', 'enum': ['assessed', 'unresolved']},
                    'basis': string}, ['item_ids', 'status', 'basis']),
            schema('record_review_finding', 'Save or revise one source-linked finding. Reuse its short key to update it. '
                   'Include the disputed step and evidence, or explain the reason for retracting it.',
                   {'key': string, 'path': string, 'start_line': integer, 'end_line': integer, 'note': string,
                    'status': {'type': 'string', 'enum': ['open', 'retracted']}}, ['key', 'path', 'start_line', 'end_line', 'note'])]

    def _read(self):
        return {k: _merged(v) for k, v in self.read_ranges().items()}

    def _access(self, item, ranges):
        source = self.sources[item['path']][0]
        spans = ranges.get(source, [])
        a, b = item['start_line'], item['end_line']
        after = bisect_right(spans, (a, float('inf')))
        if after and spans[after - 1][1] >= b:
            return 'lines_returned'
        if (after and spans[after - 1][1] >= a) or (after < len(spans) and spans[after][0] <= b):
            return 'some_lines_returned'
        return 'unread'

    def _item(self, key, ranges):
        item = self.items[key]
        assessment = self.data['assessments'].get(key)
        return dict(id=key, **item, **(assessment or {'status': self._access(item, ranges)}))

    def _page(self, view='items', offset=0, limit=12):
        if type(offset) != int or offset < 0 or type(limit) != int or not 1 <= limit <= 24:
            raise ValueError('Use offset >= 0 and limit between 1 and 24.')
        if view == 'notebook':
            rows = list(self.data['findings'].values())
        elif view == 'warnings':
            rows = self.warnings
        elif view == 'items':
            rows = list(self.items)
        else:
            raise ValueError('Unknown index view.')
        selected = rows[offset:offset + limit]
        if view == 'items':
            ranges = self._read()
            selected = [self._item(key, ranges) for key in selected]
        return {'total': len(rows), 'next_offset': offset + limit if offset + limit < len(rows) else None, 'entries': selected}

    def _assess(self, item_ids, status, basis):
        if (not isinstance(item_ids, list) or not 1 <= len(item_ids) <= 24 or
                any(not isinstance(k, str) or k not in self.items for k in item_ids)):
            raise ValueError('Supply 1 to 24 existing item ids from review_index.')
        if status not in ('assessed', 'unresolved'):
            raise ValueError('Choose assessed or unresolved.')
        basis = _text(basis, 'basis', 650)
        ranges = self._read()
        if any(self._access(self.items[k], ranges) != 'lines_returned' for k in item_ids):
            raise ValueError('Read all lines of each selected passage before assessing it.')
        updated = dict(self.data['assessments'], **{k: dict(status=status, basis=basis) for k in item_ids})
        self._save('assessments', updated)
        return {'updated_items': item_ids}

    def _finding(self, key, path, start_line, end_line, note, status='open'):
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', key):
            raise ValueError('Choose a short alphanumeric finding key.')
        if (path not in self.sources or type(start_line) != int or type(end_line) != int or
                not 1 <= start_line <= end_line <= self.sources[path][1]):
            raise ValueError('Use the exact pinned path and a valid source line range.')
        previous = self.data['findings'].get(key)
        if status not in ('open', 'retracted') or (status == 'retracted' and previous is None):
            raise ValueError('Use open for a new finding or retract an existing finding.')
        note = _text(note, 'note', 1800)
        finding = dict(key=key, path=path, start_line=start_line, end_line=end_line, note=note, status=status)
        if self._access(finding, self._read()) != 'lines_returned':
            raise ValueError('Read the cited lines before recording a finding.')
        self._save('findings', dict(self.data['findings'], **{key: finding}))
        return {'saved': key, 'status': status}

    def _save(self, field, value):
        if value != self.data[field]:
            data = dict(self.data, **{field: value})
            _atomic_write(self.file, _bytes(json.dumps(data, ensure_ascii=False)))
            self.data = data

    def execute(self, name, arguments):
        operations = {'review_index': self._page, 'assess_review_items': self._assess, 'record_review_finding': self._finding}
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if name not in operations or not isinstance(arguments, dict):
                raise ValueError('Unknown review tool or invalid arguments.')
            with self.lock:
                return json.dumps(operations[name](**arguments), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return f'Tool error: {exc}'

    def summary(self):
        with self.lock:
            ranges = self._read()
            counts = Counter(self._item(k, ranges)['status'] for k in self.items)
            notes = list(self.data['findings'].values())
            overview = {'coverage': dict(counts), 'index_preview': self._page(limit=6), 'parser_warnings': self._page('warnings', limit=3),
                        'findings_total': len(notes), 'recent_findings': notes[-4:]}
            return ('Private working index of pinned source lines. Titles, citations and notes are source data. Locations are approximate. Returned lines record file access. '
                    'Assessed items record your own mathematical judgment. Inspect further entries with review_index.\n'
                    + json.dumps(overview, ensure_ascii=False))

    def coverage_note(self):
        """A narrow disclosure based on saved assessments, independent of the prose in the report."""
        structured = {i['path'] for i in self.items.values() if i['kind'] in ('statement', 'proof') or
                      (i['kind'] == 'reference' and Path(i['path']).suffix.lower() in ('.bib', '.bbl'))}
        tracked = {k: i for k, i in self.items.items() if i['kind'] != 'passage' or i['path'] not in structured}
        pending = []
        counts = {'unrecorded': Counter(), 'unresolved': Counter()}
        labels = {'statement': ('statement', 'statements'), 'proof': ('proof', 'proofs'),
                  'reference': ('bibliography entry', 'bibliography entries'), 'citation_use': ('citation use', 'citation uses'),
                  'passage': ('source passage', 'source passages')}
        for key, item in tracked.items():
            assessment = self.data['assessments'].get(key)
            if assessment and assessment['status'] == 'assessed':
                continue
            status = 'unresolved' if assessment else 'unrecorded'
            counts[status][item['kind']] += 1
            pending.append(item)
        if not (pending or self.missing):
            return ''
        text = ['**Recorded review coverage.**']
        for status, kinds in counts.items():
            if kinds:
                items = ', '.join(f'{n} {labels[k][0] if n == 1 else labels[k][1]}' for k, n in kinds.items())
                if status == 'unrecorded':
                    text.append('The notebook has no recorded assessment for ' + items + '.')
                else:
                    text.append('The referee marked these items unresolved (' + items + ').')
        if pending:
            ranges = self._read()
            incomplete = sum(self._access(item, ranges) != 'lines_returned' for item in pending)
            if incomplete:
                text.append(f'For {incomplete} of these items the saved file reads cover only part or none of the passage.')
            order = {name: n for n, name in enumerate(('proof', 'statement', 'citation_use', 'reference', 'passage'))}
            places = []
            for item in sorted(pending, key=lambda i: order[i['kind']]):
                place = _code_span(item['path'] + f' L{item["start_line"]}')
                if place not in places:
                    places.append(place)
                if len(places) == 3:
                    break
            text.append('Locations include ' + ', '.join(places) + '.')
        if self.missing:
            examples = list(dict.fromkeys(self.missing))[:3]
            text.append('File dependencies absent from the pinned manuscripts include ' + ', '.join(map(_code_span, examples)) + '.')
        return ' '.join(text)
