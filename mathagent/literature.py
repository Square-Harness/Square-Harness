# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Bounded, opt-in literature retrieval with local, source-addressable evidence.

Network responses are untrusted source data, never agent instructions. Credentials
stay in HTTP headers. HTTPS connects to a validated address with the original
hostname for TLS, so a second DNS lookup cannot turn a public URL into localhost.
"""
from datetime import datetime, timezone
from bisect import bisect_right
import hashlib
from html import unescape
from html.parser import HTMLParser
import http.client
from functools import lru_cache
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import textwrap
import tempfile
import time
import threading
import zlib
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from .tools import ENCODING_NAMES, decode_text, result_budget
from .arxiv_source import SourceArchiveError, extract_source, is_source_archive

BACKEND_VERSION = 'literature-v1'
QUOTA_HOSTS = {'api.semanticscholar.org': 'semantic_scholar', 'api.crossref.org': 'crossref',
               'api.zbmath.org': 'zbmath', 'export.arxiv.org': 'arxiv', 'arxiv.org': 'arxiv',
               'openlibrary.org': 'openlibrary', 'api.opencitations.net': 'opencitations',
               'api.search.brave.com': 'brave'}
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = {}
_HOST_LOCKS = {}
MAX_BYTES = 10_000_000
# Scholarly hosts whose plain-HTTP links (old papers, MathSciNet exports, .bib files) are read over HTTPS.
_UPGRADABLE = re.compile(r'http://((?:www\.)?arxiv\.org|export\.arxiv\.org|(?:dx\.)?doi\.org|(?:www\.)?zbmath\.org)(?=[/?#]|$)', re.I)
ARXIV_BACKOFF = (15.0, 30.0)  # seconds after an arXiv HTTP 429 without Retry-After: at least its 15 s pacing
STRIKES = 2  # failed requests in a row (each retried once) before a provider is marked down
DOWN_COOLDOWN = 240  # seconds before a provider marked down is probed again (never for TLS failures)
MAX_TEXT = 4_000_000
DOCUMENT_META_BYTES = 1_000_000
ARXIV_HOSTS = frozenset({'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'})
UNLIMITED_SOURCE = (1 << 53) - 1  # JSON-safe integer for fresh jobs without an operator quota.
TOOL_NAMES = {'search_papers', 'search_web', 'find_quotes', 'open_paper', 'read_paper', 'search_paper', 'lookup_identifier'}
PROVIDERS = ('arxiv', 'semantic_scholar', 'zbmath', 'crossref', 'openlibrary')
API_HOSTS = frozenset({'api.crossref.org', 'api.opencitations.net', 'api.search.brave.com',
                      'api.semanticscholar.org', 'api.zbmath.org', 'export.arxiv.org'})
ARXIV_ID = re.compile(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?', re.I)
DOC_ID = re.compile(r'doc-[0-9a-f]{24}')


def upgrade_url(value):
    """A plain-HTTP link to arXiv, doi.org or zbMATH read over HTTPS; every other value unchanged."""
    return _UPGRADABLE.sub(lambda m: 'https://' + m.group(1), value, count=1) if isinstance(value, str) and _UPGRADABLE.match(value) else value


class _Gate:
    """One request at a time per host, shared by every job of this process; an urgent requester (an interactive
    check) is let through before waiting background requesters. The pacing between requests is unchanged."""

    def __init__(self):
        self.condition = threading.Condition()
        self.busy, self.urgent = False, 0

    def __enter__(self):
        return self

    def hold(self, urgent, cancelled=lambda: False):
        with self.condition:
            self.urgent += bool(urgent)
            try:
                while self.busy or (not urgent and self.urgent):
                    if cancelled():
                        raise KeyboardInterrupt  # a pause request stops waiting for another job's turn
                    self.condition.wait(0.1)
            finally:
                self.urgent -= bool(urgent)
            self.busy = True
        return self

    def __exit__(self, *exc):
        with self.condition:
            self.busy = False
            self.condition.notify_all()


class LiteratureError(ValueError):
    """A safe user-visible error (never containing request credentials)."""
    def __init__(self, message, *, code='source_error'):
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=1)
def _tls_context():
    return ssl.create_default_context()


# Mozilla-derived CA bundles that some Python builds do not read by default: a
# standalone Python on macOS reads /etc/ssl/cert.pem, which can lag behind the
# roots a source has moved to (zbMATH's HARICA chain, in November 2026).
EXTRA_CA_FILES = ('/opt/homebrew/etc/ca-certificates/cert.pem', '/usr/local/etc/ca-certificates/cert.pem',
                  '/opt/homebrew/etc/openssl@3/cert.pem', '/usr/local/etc/openssl@3/cert.pem',
                  '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt')


@lru_cache(maxsize=1)
def _wider_tls_context():
    """The default trust plus the platform's other Mozilla-derived CA bundles, or None if there are none.

    Used only after the default store failed to verify a chain; hostname checks stay on."""
    context, loaded = ssl.create_default_context(), 0
    candidates = list(EXTRA_CA_FILES)
    try:
        import certifi  # optional: present in many environments, never required
        candidates.insert(0, certifi.where())
    except ImportError:
        pass
    for path in candidates:
        try:
            if os.path.isfile(path):
                context.load_verify_locations(cafile=path)
                loaded += 1
        except (OSError, ssl.SSLError):
            continue
    return context if loaded else None


def decode_page(body, content_type=''):
    """Decode declared web charsets, falling back to the shared text decoder."""
    match = re.search(r'charset\s*=\s*[\"\']?([A-Za-z0-9_.-]+)', content_type, re.I)
    if match is None:
        match = re.search(br'<meta\b[^>]*charset\s*=\s*[\"\']?([A-Za-z0-9_.-]+)', body[:8192], re.I)
    if match:
        encoding = match[1].decode('ascii') if isinstance(match[1], bytes) else match[1]
        try:
            return body.decode(encoding)
        except (UnicodeError, LookupError):
            pass
    return decode_text(body, 'public source')[0]


def line_matches(lines, query):
    """Literal, whitespace-tolerant matches with exact original line ranges."""
    text, starts, cursor = '\n'.join(lines), [], 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line) + 1
    previous = -1
    pattern = r'\s+'.join(re.escape(word) for word in query.split())
    for match in re.finditer(pattern, text, re.I):
        first = bisect_right(starts, match.start()) - 1
        if first == previous:
            continue
        previous = first
        last = bisect_right(starts, match.end() - 1) - 1
        begin, end = max(starts[first], match.start() - 160), starts[last] + len(lines[last])
        yield {'line': first + 1, 'end_line': last + 1, 'passage': text[begin:min(end, begin + 500)],
               'partial_line': begin > starts[first] or end > begin + 500 or last >= first + 80}


def source_page(lines, source_id, fields, start_line, end_line, start_column, cap, *, label=""):
    """Fit a numbered source excerpt and its exact continuation into the serialized result budget."""
    if end_line is None:
        end_line = len(lines)
    if (any(type(n) is not int for n in (start_line, end_line, start_column))
            or not 1 <= start_line <= min(end_line, len(lines))
            or not 1 <= start_column <= max(1, len(lines[start_line - 1]))):
        raise LiteratureError('Read an existing range of lines and a valid starting column', code='invalid_arguments')
    requested_end = min(end_line, len(lines))
    # Reserve the real JSON envelope (including the source URL), using the longest line/column
    # numbers this page could return. Account for escaping, so _emit never clips an advanced cursor.
    result = {**fields, 'total_lines': len(lines), 'start_line': start_line, 'start_column': start_column,
              'end_line': requested_end, 'citation': f'{source_id}:L{start_line}-L{requested_end}',
              'passage': '', 'lines_complete': False, 'truncated': False,
              'next_line': requested_end + 1, 'next_column': MAX_TEXT + 1}
    available = cap - len(json.dumps(result, ensure_ascii=False)) - 2
    selected, end, column = [], start_line - 1, start_column
    next_line = next_column = None
    for number in range(start_line, requested_end + 1):
        line = lines[number - 1]
        prefix = f'{label}{number}: '
        start = column - 1
        room = available - len(prefix) - (2 if selected else 0)  # JSON's escaped newline
        count = min(max(0, room), len(line) - start)
        part = line[start:start + count]
        size = len(json.dumps(part, ensure_ascii=False)) - 2
        if size > room and room > 0:
            lo, hi = 0, count
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(json.dumps(part[:mid], ensure_ascii=False)) - 2 <= room:
                    lo = mid
                else:
                    hi = mid - 1
            part, count = part[:lo], lo
            size = len(json.dumps(part, ensure_ascii=False)) - 2
        if room < 0 or (not count and line) or (selected and start + count < len(line)):
            next_line, next_column = number, column
            break
        selected.append(prefix + part)
        available = room - size
        end = number
        if start + count < len(line):
            next_line, next_column = number, column + count
            break
        column = 1
    if not selected:
        raise LiteratureError('The remaining evidence budget cannot fit one source character and its citation.', code='web_budget_exhausted')
    if next_line is None and end < len(lines):
        next_line, next_column = end + 1, 1
    complete = start_column == 1 and (next_line != end)
    return {**result, 'end_line': end, 'citation': f'{source_id}:L{start_line}-L{end}',
            'passage': '\n'.join(selected), 'truncated': not complete or end < requested_end, 'lines_complete': complete,
            'next_line': next_line, 'next_column': next_column}


def match_page(lines, query, source_id, fields, offset, limit, cap, annotate):
    if type(offset) is not int or offset < 0 or limit is not None and (type(limit) is not int or limit <= 0):
        raise LiteratureError('Use a nonnegative match offset and a positive limit', code='invalid_arguments')
    result = {**fields, 'query': query, 'matches': [], 'total_matches': 0, 'next_offset': None, 'truncated': False}
    room = cap - len(json.dumps(result, ensure_ascii=False)) - 30
    total, full = 0, False
    for hit in line_matches(lines, query):
        total += 1
        if total <= offset or full or limit is not None and len(result['matches']) >= limit:
            continue
        hit.update(citation=f'{source_id}:L{hit["line"]}-L{hit["end_line"]}')
        annotate(hit)
        size = len(json.dumps(hit, ensure_ascii=False)) + 2
        if size > room:
            full = True
            continue
        result['matches'].append(hit)
        room -= size
    if total > offset and not result['matches']:
        raise LiteratureError('One matching excerpt exceeds the remaining evidence budget', code='web_budget_exhausted')
    following = offset + len(result['matches'])
    result.update(total_matches=total, next_offset=following if following < total else None, truncated=following < total)
    return result


def arxiv_query(query):
    """Conjoin plain topic words, preserving explicit fields and Boolean syntax."""
    if re.search(r'\b(?:all|ti|au|abs|co|jr|cat|rn|id|submittedDate):', query):
        return query
    tokens = list(re.finditer(r'"[^"\n]*"|[()]|[^\s()"]+', query))
    parts, depth, operand, cursor = [], 0, False, 0
    for match in tokens:
        if query[cursor:match.start()].strip():
            raise LiteratureError('Unbalanced quotes in arXiv search query')
        token, cursor = match[0], match.end()
        if token == ')':
            if not depth or not operand:
                raise LiteratureError('Invalid arXiv Boolean query')
            depth -= 1
        elif token in ('AND', 'OR', 'ANDNOT'):
            if not operand:
                raise LiteratureError('Invalid arXiv Boolean query')
            operand = False
        else:
            if operand:
                parts.append('AND')
            if token == '(':
                depth += 1
                operand = False
            else:
                token = 'all:' + (token if token.startswith('"') else json.dumps(token, ensure_ascii=False))
                operand = True
        parts.append(token)
    if depth or not operand or query[cursor:].strip():
        raise LiteratureError('Unbalanced or incomplete arXiv search query')
    return ' '.join(parts)


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def _plain(value, size=1000):
    return ' '.join(str(value or '').split())[:size]


def _crossref_title(raw):
    """Some records carry equivalent TeX and MathML presentations of the same inline formula."""
    title = str(raw or '')
    def choose_formula(match):
        tex = re.search(r'<(?:[\w-]+:)?tex-math\b[^>]*>(.*?)</(?:[\w-]+:)?tex-math\s*>', match[1], re.I | re.S)
        return tex[1] if tex else match[1]
    title = re.sub(r'<(?:[\w-]+:)?alternatives\b[^>]*>(.*?)</(?:[\w-]+:)?alternatives\s*>',
                   choose_formula, title, flags=re.I | re.S)
    tags = (r'</?(?:[\w-]+:)?(?:title|p|inline-formula|disp-formula|tex-math|math|semantics|annotation|'
            r'm[a-z]+|i|b|em|strong|italic|bold|sub|sup|span|scp|sc)\b[^<>]*>')
    title = _plain(unescape(re.sub(tags, '', title.replace('<![CDATA[', '').replace(']]>', ''), flags=re.I)), 1200)
    # Some feeds (and older cached records) have already flattened the two alternatives.
    title = re.sub(r'\$\$(\\math(?:bb|bf|rm)\s*\{([A-Za-z])\}\s*\^\s*\{?([A-Za-z0-9]+)\}?)\$\$'
                   r'\s+\2\s+\3(?!\w)', r'$\1$', title)
    return re.sub(r'\$\$(.+?)\$\$', r'$\1$', title)


def _schema(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': required, 'additionalProperties': False}}}


PLACE_WORDS = r'(?<![A-Za-z])(?:theorem|thm|lemma|lem|proposition|prop|corollary|cor|section|sect|sec|chapter|chap|ch|remark|rem|§)'


def _fold(text):
    import unicodedata
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).casefold()


LABEL_WORDS = r'(?:theorem|thm|lemma|proposition|prop|corollary|cor|remark)'


def place_number(locator):
    """The checkable part of a locator: '9.26' in 'Theorem 9.26', 'A' or 'B.2' in 'Theorem A' or 'Lemma B.2'.
    A theorem-like label wins over a section number; single numbers are too common to check. Identifiers
    ('Zbl 0276.60013', a DOI, an arXiv number) are not places."""
    text = re.sub(r'\b(?:zbl|mr|doi|arxiv)\b\.?:?\s*\S+|\b10\.\d{4,9}/\S+|\b\d{4}\.\d{4,5}(?:v\d+)?\b', ' ',
                  str(locator or ''), flags=re.I)
    named = re.findall(r'(?<![A-Za-z])(?i:' + LABEL_WORDS + r')\.?\s*(\d+(?:\.\d+)+[a-z]?|[A-Z](?:\.\d+)*(?![\w]|\.\d))', text)
    found = named or re.findall(r'\d+(?:\.\d+)+[a-z]?', text)
    return found[0] if found else None


def _num(number):
    if number[:1].isalpha():  # a lettered label: the capital itself, never a word that starts with it
        return r'(?<![\w.])(?-i:' + re.escape(number) + r')(?![\w]|\.\d)'
    return r'(?<![\d.])' + re.escape(number) + r'(?![\d])'


def has_place(text, number):
    """Whether the text names this place. A lettered label counts only after a theorem word:
    'Theorem A', not the article 'A' or 'Appendix A'."""
    label = LABEL_WORDS + r'\.?\s*' if number[:1].isalpha() else ''
    return bool(number) and re.search(label + _num(number), text or '', re.I) is not None


TITLE_STOP = {'theory', 'equations', 'equation', 'introduction', 'methods', 'problems', 'applications', 'second',
              'order', 'their', 'which', 'about', 'between', 'classical', 'modern', 'edition', 'volume', 'lectures'}


def title_words(title):
    """Distinctive words of a title, to tell a work from the same author's other works."""
    return [w for w in re.findall(r'[a-z]{5,}', _fold(title)) if w not in TITLE_STOP][:8]


def bibliography_entries(lines):
    """(key, text) for each bibliography entry such as "[5] D. Gilbarg …" or "[GT]" on its own line,
    the text running to the next entry (at most five lines)."""
    entries = []
    for i, line in enumerate(lines):
        match = re.match(r'\s*\[([^\[\]]{1,12})\]\s*(.*)', line)
        if not match:
            continue
        parts = [match.group(2)] if match.group(2).strip() else []
        for following in lines[i + 1:i + 6]:
            if re.match(r'\s*\[[^\[\]]{1,12}\]', following):
                break
            parts.append(following.strip())
        text = ' '.join(' '.join(parts).split())[:500]
        if text:
            entries.append((match.group(1).strip(), text))
    return entries


def bibliography_keys(lines, surname, title=''):
    """Citation keys of the bibliography entries naming the author, with a year.
    With a title, only entries sharing one of its distinctive words count (not the author's other works)."""
    name, words, keys = _fold(surname), title_words(title), []
    for key, text in bibliography_entries(lines):
        folded = _fold(text)
        if (name in folded and re.search(r'(?:19|20)\d\d', text) and (not words or any(w in folded for w in words))
                and key not in keys):
            keys.append(key)
    return keys


def bibliography_entry(lines, key):
    """The text of the bibliography entry for a key such as 1 or Bre11 (up to the next entry)."""
    for i, line in enumerate(lines):
        match = re.match(r'\s*\[' + re.escape(key) + r'\]\s*(.*)', line)
        if not match:
            continue
        parts = [match.group(1)] if match.group(1).strip() else []
        for following in lines[i + 1:i + 5]:
            if re.match(r'\s*\[[^\]]{1,12}\]', following):
                break
            parts.append(following.strip())
        entry = ' '.join(' '.join(parts).split())[:400]
        if entry:
            return entry
    return ''


def cited_keys(text, number, surname, keys=()):
    """The bibliography keys under which the text cites this number."""
    found = [key for key, place in keyed_citations(text, keys) if has_place(place, number)]
    match = re.search(PLACE_WORDS + r'\.?\s*' + _num(number) + r'\s*(?:of|in)\s*\[([^\]]{1,12})\]', text, re.I)
    if match and match.group(1).strip() in keys:
        found.append(match.group(1).strip())
    return list(dict.fromkeys(found))


def keyed_citations(text, keys):
    """(key, place) for in-text citations such as [5, Theorem 9.26] or [GT, Thm. 8.12; 7]."""
    found = []
    for bracket in re.findall(r'\[([^\[\]]{1,120})\]', text):
        for part in bracket.split(';'):
            key, comma, place = part.partition(',')
            if comma and key.strip() in keys and re.search(r'\d|' + LABEL_WORDS + r'\.?\s*[A-Z]\b', place, re.I):
                found.append((key.strip(), ' '.join(place.split())[:60]))
    return found


def _limit_output():
    """In a PDF extractor's process: no file larger than twice the text limit (a deflate bomb stops early)."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * MAX_TEXT, 2 * MAX_TEXT))
    except (ImportError, ValueError, OSError):
        pass


def cites_place(text, number, surname, keys=()):
    """Whether the text cites the author's work at this number (not merely an equation (9.26))."""
    if not number:
        return False
    n = _num(number)
    if any(has_place(place, number) for _, place in keyed_citations(text, keys)):
        return True
    for bracket in re.findall(r'\[([^\[\]]{1,120})\]', text):
        for part in bracket.split(';'):
            key, comma, place = part.partition(',')
            if comma and has_place(place, number):
                return key.strip() in keys
    if re.search(PLACE_WORDS + r'\.?\s*' + n + r'\s*(?:of|in)\s*\[([^\]]{1,12})\]', text, re.I):
        key = re.search(PLACE_WORDS + r'\.?\s*' + n + r'\s*(?:of|in)\s*\[([^\]]{1,12})\]', text, re.I).group(1).strip()
        return key in keys
    if keys or not surname:
        return False  # the paper's own keys for this work were known and none cites this place
    # Without keys: the surname as a whole word ("Stein", not "Einstein"), with the place next to it.
    folded = _fold(text)
    for match in re.finditer(r'(?<![a-z])' + re.escape(_fold(surname)) + r'(?![a-z])', folded):
        if re.search(PLACE_WORDS + r'\.?\s*\(?' + _num(_fold(number)), folded[max(0, match.start() - 150):match.end() + 150], re.I):
            return True
    return False


def _s2_record(p):
    ids = p.get('externalIds') or {}
    return {'identifier': 's2:' + str(p.get('paperId', '')), 'title': _plain(p.get('title'), None),
            'authors': [_plain(a.get('name'), None) for a in p.get('authors') or [] if isinstance(a, dict)],
            'year': p.get('year'), 'abstract': _plain(p.get('abstract'), None),
            'url': p.get('url'), 'external_ids': ids, 'doi': (ids.get('DOI') or '').lower() or None,
            'arxiv_id': ids.get('ArXiv'), 'cited_by': p.get('citationCount'),
            'open_access_url': (p.get('openAccessPdf') or {}).get('url')}


def _review(text):
    return _plain(re.sub(r'\\textit\{([^}]*)\}', r'\1', text or ''), None)


ZBMATH_HIDDEN = 'contents unavailable'  # zbMATH Open's placeholder for records it may not show


def _zbmath_record(r):
    source = r.get('source') or {}
    hidden = lambda value: '' if ZBMATH_HIDDEN in str(value or '') else value
    book = (source.get('book') or [{}])[0] or {}
    links = [x for x in r.get('links') or [] if isinstance(x, dict)]
    doi = next((x.get('identifier') for x in links if x.get('type') == 'doi' and x.get('identifier')), None)
    reviews = [e.get('text') for e in r.get('editorial_contributions') or [] if isinstance(e, dict) and e.get('text')
               and 'contents unavailable' not in e['text']]
    return {'identifier': 'zbl:' + str(r.get('identifier') or r.get('id') or ''),
            'title': _plain(hidden((r.get('title') or {}).get('title')), None),
            'authors': [_plain(a.get('name'), None) for a in (r.get('contributors') or {}).get('authors') or []
                        if isinstance(a, dict) and hidden(a.get('name'))],
            'year': r.get('year'), 'type': (r.get('document_type') or {}).get('description'),
            'source': _plain(hidden(source.get('source')), None), 'publisher': _plain(hidden(book.get('publisher')), None) or None,
            'doi': doi, 'url': r.get('zbmath_url'), 'review': _review(reviews[0]) if reviews else '',
            'msc': [m.get('code') for m in r.get('msc') or [] if isinstance(m, dict) and m.get('code')]}


class _HTMLText(HTMLParser):
    """Readable HTML; retain TeX math alttext where available, omit scripts."""
    def __init__(self, check=lambda: None):
        super().__init__(convert_charrefs=True)
        self.check = check
        self.parts = []
        self.hidden = 0
        self.math_depth = 0
        self.suppress_math = False
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        self.check()
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'nav', 'svg'):
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if self.hidden:
            return
        if tag == 'math':
            self.math_depth += 1
            if self.math_depth == 1 and (attrs.get('alttext') or attrs.get('aria-label')):
                self.parts.append(' $' + (attrs.get('alttext') or attrs['aria-label']) + '$ ')
                self.suppress_math = True
        if tag in ('p', 'div', 'section', 'article', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5'):
            self.parts.append('\n')
        if tag == 'img' and attrs.get('alt') and not self.math_depth:
            self.parts.append(' ' + attrs['alt'] + ' ')

    def handle_endtag(self, tag):
        self.check()
        if tag in ('script', 'style', 'noscript', 'nav', 'svg'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'math':
            self.math_depth = max(0, self.math_depth - 1)
            if not self.math_depth:
                self.suppress_math = False
        if not self.hidden and tag in ('p', 'div', 'section', 'article', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5'):
            self.parts.append('\n')

    def handle_data(self, data):
        self.check()
        if self.in_title:
            self.title.append(data)
        if not self.hidden and not self.suppress_math:
            self.parts.append(data)

    def text(self):
        # Stable line numbers refer to this exact stored extraction, not PDF lines.
        paragraphs = []
        for part in ''.join(self.parts).splitlines():
            self.check()
            text = _plain(part, MAX_TEXT)
            if text:
                paragraphs.append(textwrap.fill(text, width=180, break_long_words=False, break_on_hyphens=False))
        self.check()
        return '\n'.join(paragraphs)


class LiteratureTools:
    def __init__(self, root, *, online=True, max_requests=UNLIMITED_SOURCE, max_chars=UNLIMITED_SOURCE, timeout=15):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Literature workspace must be a directory')
        self.result_chars = result_budget(None)
        self.online = bool(online)
        self.timeout = min(30, max(1, float(timeout)))
        self._last_request = {}
        self.down = set()  # sources that kept failing: not asked again until DOWN_COOLDOWN has passed
        self._down_at, self._strikes = {}, {}
        self.on_budget_change = None
        self.deadline = None
        # A pause request (the interface sets the task's flag): rate-limit waits and an open request stop at once.
        self.cancelled = lambda: False
        # An interactive check goes before background reviews at a shared rate gate (the pacing stays the same).
        self.urgent = False
        self._robots = {}
        self.reset_budget(max_requests=max_requests, max_chars=max_chars)

    def reset_budget(self, *, max_requests=UNLIMITED_SOURCE, max_chars=UNLIMITED_SOURCE):
        for value in (max_requests, max_chars):
            if type(value) is not int or value < 0:
                raise ValueError('Literature budgets must be nonnegative integers')
        self.max_requests, self.max_chars = max_requests, max_chars
        self.stats = {'requests': 0, 'returned_chars': 0}

    def snapshot(self):
        return {'backend_version': BACKEND_VERSION, 'online': self.online,
                'max_requests': self.max_requests, 'max_chars': self.max_chars,
                'timeout': self.timeout, 'stats': dict(self.stats)}

    def restore(self, snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError('Invalid literature snapshot')
        # A saved job may further restrict a caller, never grant online permission.
        self.online = self.online and snapshot.get('online') is True
        for name in ('max_requests', 'max_chars'):
            value = snapshot.get(name, getattr(self, name))
            if type(value) is not int or value < 0:
                raise ValueError('Invalid saved literature budget')
            setattr(self, name, min(getattr(self, name), value))
        saved = snapshot.get('stats', {})
        for name in self.stats:
            value = saved.get(name, 0)
            if type(value) is not int or value < 0:
                raise ValueError('Invalid saved literature counter')
            self.stats[name] = max(self.stats[name], value)

    def schemas(self):
        string, integer = {'type': 'string'}, {'type': 'integer'}
        offline = ' Offline: cached data only.' if not self.online else ''
        return [
            _schema('search_papers', 'Search scholarly metadata; abstracts are not proof evidence. zbmath also finds '
                    'books, and its reviews often list their chapters; query it with fields, e.g. "au:Brezis ti:Functional analysis". '
                    'Crossref finds journal articles and books (plain author/title/topic query); Open Library is a book catalog. '
                    'For arXiv author searches use au:"Full Name"; sort=newest returns newest submissions first '
                    '(arXiv only). Request the number of records needed.' + offline,
                    {'query': string, 'provider': {'type': 'string', 'enum': list(PROVIDERS)}, 'limit': integer, 'offset': integer,
                     'sort': {'type': 'string', 'enum': ['relevance', 'newest']}}, ['query']),
            _schema('search_web', 'Search web metadata with Brave; requires BRAVE_SEARCH_API_KEY. Up to 20 results per Brave request.' + offline,
                    {'query': string, 'limit': integer, 'offset': integer}, ['query']),
            _schema('find_quotes', 'See how open papers cite a work at a given place: finds papers that cite the work '
                    '(OpenCitations and Crossref reference lists) and are about the topic, opens their arXiv versions and '
                    'returns the exact lines (with citation locators) that cite it at that number, e.g. author "Brezis", '
                    'locator "Theorem 9.26"; without a number, the places they cite. Give the title: it identifies the work '
                    '(not the author\'s other works). Also returns the bibliography entry.' + offline,
                    {'author': string, 'locator': string, 'topic': string, 'title': string, 'offset': integer, 'limit': integer}, ['author']),
            _schema('lookup_identifier', 'Registry record of one DOI (Crossref, else the DOI registry) or arXiv ID (arXiv API: '
                    'title, authors, journal reference, comments, latest version, withdrawal notice). found=false means the '
                    'registry has no such identifier. One request; use it before a topic search when an identifier is given.' + offline,
                    {'identifier': string}, ['identifier']),
            _schema('open_paper', 'Cache a public HTTPS paper, arXiv ID, or DOI. Explicit arXiv /src/ or /e-print/ URLs request TeX source, including source archives, only when site access rules permit. Source files are never executed. Returns source ID/index; continue the heading index with next_section_offset. Use read_paper for full heading text.' + offline,
                    {'identifier_or_url': string, 'section_offset': integer}, ['identifier_or_url']),
            _schema('read_paper', 'Read exact numbered lines of a cached extraction. Follow next_line and next_column for the next page. Verify uncertain math against the original.',
                    {'document_id': string, 'start_line': integer, 'end_line': integer, 'start_column': integer}, ['document_id']),
            _schema('search_paper', 'Literal search within a cached paper; returns matches with citation locations. Continue with next_offset.',
                    {'document_id': string, 'query': string, 'offset': integer, 'limit': integer}, ['document_id', 'query']),
        ]

    def execute(self, name, args):
        if self.max_chars - self.stats['returned_chars'] < 100:
            return json.dumps({'error': 'Literature returned-character budget exhausted; use existing evidence.'})
        try:
            if name not in TOOL_NAMES:
                raise LiteratureError('Unknown literature tool')
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                raise LiteratureError('Tool arguments must be an object')
            result = getattr(self, name)(**args)
        except LiteratureError as exc:
            result = {'error': str(exc), 'error_code': exc.code}
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, ET.ParseError, http.client.HTTPException):
            # Third-party exceptions can embed URLs/headers; never echo them.
            result = {'error': 'Literature operation failed. Check arguments, network availability, provider access, and document format.'}
        return self._emit(result)

    def result_room(self):
        return min(self.result_chars, max(0, self.max_chars - self.stats['returned_chars']))

    def _emit(self, result):
        cap = self.result_room()
        if cap < 100:
            # Control errors contain no source data and remain valid JSON even when
            # the evidence budget has no room left for a JSON envelope.
            return json.dumps({'error': 'Literature output budget exhausted; use existing evidence.'})
        value = json.dumps(result, ensure_ascii=False)
        if len(value) > cap:
            # Source identity is immutable. Shorten descriptive fields first,
            # then omit whole low-ranked records rather than clipping titles,
            # author names, URLs, identifiers, queries or provenance envelopes.
            result = json.loads(value)
            result['truncated'] = True
            descriptions = {'review', 'abstract', 'description', 'summary', 'snippet',
                            'reference_texts', 'references', 'passage'}

            def descriptive_fields(value):
                found = []
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in descriptions and isinstance(item, (str, list)) and item:
                            found.append((len(json.dumps(item, ensure_ascii=False)), value, key))
                        elif isinstance(item, (dict, list)):
                            found.extend(descriptive_fields(item))
                elif isinstance(value, list):
                    for item in value:
                        found.extend(descriptive_fields(item))
                return found

            def size():
                return len(json.dumps(result, ensure_ascii=False))

            while size() > cap:
                fields = descriptive_fields(result)
                if not fields:
                    break
                _, owner, key = max(fields, key=lambda item: item[0])
                item = owner[key]
                owner[key] = item[:len(item) // 2]
                if owner is not result or key == 'passage':
                    owner[key + '_truncated'] = True
                if key == 'passage':
                    owner['lines_complete'] = False
            while size() > cap:
                lists = [(len(json.dumps(items, ensure_ascii=False)), key) for key, items in result.items()
                         if key in ('results', 'matches', 'quotes', 'sections', 'opened', 'source_files')
                         and isinstance(items, list) and items]
                if not lists:
                    break
                _, key = max(lists)
                result[key].pop()
                if key == 'source_files':
                    result['source_files_truncated'] = True
                omitted = key + '_omitted'
                result[omitted] = result.get(omitted, 0) + 1
            if size() > cap:
                # Even the complete envelope does not fit. Return an explicit
                # control error, never a misleading fragment of source metadata.
                result = {'error': 'Literature output budget exhausted; request smaller excerpts.',
                          'truncated': True}
            if result.get('sections_omitted') and 'section_offset' in result:
                result['next_section_offset'] = result['section_offset'] + len(result['sections'])
            if result.get('quotes_omitted') and 'offset' in result:
                result['next_offset'] = result['offset'] + len(result['quotes'])
            if result.get('results_omitted') and 'offset' in result:
                result['next_offset'] = result['offset'] + len(result.get('results', []))
            value = json.dumps(result, ensure_ascii=False)
        if len(value) > cap:
            value = '{}' if cap >= 2 else ''
        self.stats['returned_chars'] += len(value)
        if self.on_budget_change:
            self.on_budget_change()
        return value

    def _path(self, *parts, create=False):
        p = self.root
        for part in ('.mathagent', 'literature', *parts):
            if not isinstance(part, str) or part in ('.', '..') or '/' in part or '\\' in part:
                raise LiteratureError('Invalid cache path')
            p = p / part
            if p.is_symlink():
                raise LiteratureError('Symlinks are excluded from the literature cache')
        if not p.resolve().is_relative_to(self.root):
            raise LiteratureError('Cache path escapes the workspace')
        if create:
            p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _write(self, parts, value):
        p = self._path(*parts, create=True)
        data = value if isinstance(value, bytes) else value.encode('utf-8')
        fd, temp = tempfile.mkstemp(prefix='tmp-', dir=p.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            self._path(*parts)
            os.replace(temp, p)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _read(self, parts, limit=MAX_TEXT):
        p = self._path(*parts)
        if not p.exists():
            return None
        if not p.is_file() or p.stat().st_size > limit:
            raise LiteratureError('Invalid or oversized cache file')
        with p.open('rb') as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise LiteratureError('Oversized cache file')
        return data.decode('utf-8')

    def _query(self, query, limit):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise LiteratureError('Provide a nonempty search query up to 500 characters')
        if type(limit) is not int or limit < 1:
            raise LiteratureError('Search limit must be a positive integer')
        return query.strip()

    def _cache_search(self, provider, query, limit, fetch, *, offset=0):
        key = _digest(json.dumps([provider, query, limit] + ([offset] if offset else [])))
        parts = ('searches', key + '.json')
        try:
            raw = self._read(parts, MAX_BYTES)
        except UnicodeError:
            raw = None
        if raw is not None:
            try:
                result = json.loads(raw)
                if not isinstance(result, dict) or not isinstance(result.get('results'), list):
                    raise ValueError('Invalid search cache')
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(result['retrieved_at'])).total_seconds()
            except (ValueError, KeyError, TypeError):
                result = None
                age = float('inf')
            stale = age >= (86400 if provider in ('arxiv-recent', 's2-citations', 'brave') or not (result or {}).get('results') else 7 * 86400)
            if result is not None and (not stale or not self.online):
                result['cached'] = True
                if stale:
                    result['stale_cache'] = True
                return result
        if not self.online:
            raise LiteratureError('Offline mode: this search is not cached or its cache is damaged. Enable online research explicitly to access the internet.', code='offline_uncached')
        results = fetch()
        result = {'provider': provider, 'query': query, 'results': results,
                  'retrieved_at': _stamp(), 'backend_version': BACKEND_VERSION,
                  'cached': False, 'evidence_kind': 'discovery_metadata_not_verified_full_text'}
        self._write(parts, json.dumps(result, ensure_ascii=False))
        return result

    def search_papers(self, query, provider='arxiv', limit=5, sort='relevance', offset=0):
        query = self._query(query, limit)
        if limit > 100:
            raise LiteratureError('Catalog search pages accept up to 100 results')
        if provider not in PROVIDERS:
            raise LiteratureError('Unknown provider; choose ' + ', '.join(PROVIDERS))
        if sort not in ('relevance', 'newest') or sort == 'newest' and provider != 'arxiv':
            raise LiteratureError('Use sort=relevance, or sort=newest with provider=arxiv')
        if type(offset) is not int or offset < 0:
            raise LiteratureError('Search offset must be a nonnegative integer')
        source = 'arxiv-recent' if sort == 'newest' else provider
        def fetch(start):
            def page():
                if not start:
                    return self._search(provider, query, limit, recent=True) if sort == 'newest' else self._search(provider, query, limit)
                return self._search(provider, query, limit, recent=sort == 'newest', offset=start)
            return self._cache_search(source, query, limit, page, offset=start)
        # zbMATH numbers pages; for an unaligned continuation return the rest of the cached page.
        start = offset // limit * limit if provider == 'zbmath' else offset
        result = fetch(start)
        fetched = len(result['results'])
        result['results'] = result['results'][offset-start:]
        result['offset'] = offset
        result['next_offset'] = start + fetched if fetched == limit else None
        if provider == 'zbmath':
            result['results'] = self._unhide(result['results'])
        if provider == 'crossref':
            result['results'] = [{key: value for key, value in record.items()
                                  if key not in ('references', 'reference_texts')}
                                 for record in result['results']]
        return result

    def _search(self, provider, query, limit, recent=False, offset=0):
        if provider == 'crossref':
            # Reuse the controller's normalized DOI records and its bounded
            # transport. The public search envelope is cached independently.
            return [{key: value for key, value in record.items() if key not in ('references', 'reference_texts')}
                    for record in self.crossref(query=query, limit=limit, offset=offset)]
        if provider == 'openlibrary':
            params = {'q': query, 'limit': limit, **({'offset': offset} if offset else {}),
                      'fields': 'key,title,author_name,first_publish_year,publisher'}
            body, _, _ = self._fetch('https://openlibrary.org/search.json?' + urlencode(params),
                                     max_bytes=1_000_000)
            records = []
            for entry in (json.loads(body).get('docs') or [])[:limit]:
                if not isinstance(entry, dict):
                    continue
                key = entry.get('key')
                if not isinstance(key, str) or not re.fullmatch(r'/works/OL[0-9]+W', key):
                    continue
                title = _plain(entry.get('title'), None)
                if not title:
                    continue
                names = entry.get('author_name')
                authors = [_plain(name, None) for name in names if isinstance(name, str) and name.strip()] \
                    if isinstance(names, list) else []
                publishers = entry.get('publisher')
                publishers = list(dict.fromkeys(_plain(name, None) for name in publishers
                                                if isinstance(name, str) and name.strip())) \
                    if isinstance(publishers, list) else []
                year = entry.get('first_publish_year')
                records.append({'identifier': 'openlibrary:' + key.rsplit('/', 1)[-1],
                                'title': title, 'authors': authors,
                                'year': year if type(year) is int else None,
                                'year_kind': 'catalog_first_publication', 'type': 'book',
                                'url': 'https://openlibrary.org' + key,
                                # A work record can aggregate many editions. Do
                                # not pair one edition's publisher with another's year.
                                'publisher': publishers[0] if len(publishers) == 1 else None,
                                'source': 'Open Library work catalog; edition unspecified'})
            return records
        if provider == 'arxiv':
            # An unfielded phrase is a conjunction; arXiv's implicit operators
            # otherwise broaden all but the first word to unrelated records.
            expression = arxiv_query(query)
            params = {'search_query': expression,
                      'start': offset, 'max_results': limit, 'sortBy': 'submittedDate' if recent else 'relevance', 'sortOrder': 'descending'}
            return self._arxiv_feed(params, limit)
        if provider == 'semantic_scholar':
            headers = {}
            if os.environ.get('SEMANTIC_SCHOLAR_API_KEY'):
                headers['x-api-key'] = os.environ['SEMANTIC_SCHOLAR_API_KEY']
            params = {'query': query, 'limit': limit, **({'offset': offset} if offset else {}),
                      'fields': 'title,authors,year,abstract,url,externalIds,openAccessPdf,citationCount'}
            body, _, _ = self._fetch('https://api.semanticscholar.org/graph/v1/paper/search?' + urlencode(params), headers=headers, max_bytes=1_000_000)
            return [_s2_record(p) for p in json.loads(body).get('data', [])[:limit] if isinstance(p, dict)]
        if provider == 'zbmath':
            params = {'search_string': query, 'page': offset // limit, 'results_per_page': limit}
            try:
                body, _, _ = self._fetch('https://api.zbmath.org/v1/document/_search?' + urlencode(params), max_bytes=2_000_000)
            except LiteratureError as exc:
                if 'HTTP 404' in str(exc):
                    return []  # zbMATH answers an empty search with 404
                raise
            return [_zbmath_record(r) for r in (json.loads(body).get('result') or [])[:limit] if isinstance(r, dict)]
        raise LiteratureError('Unknown provider')

    def _unhide(self, records):
        """Fill the title and authors zbMATH Open hides from Crossref's record of the same DOI.

        The cache keeps zbMATH's own records; this costs at most one (cached) Crossref
        request per search, within the usual request budget. Without it (offline,
        budget spent, Crossref down) the records stay as zbMATH gave them.
        """
        hidden = [r for r in records if r.get('doi') and not (r.get('title') and r.get('authors'))]
        if not hidden:
            return records
        try:
            found = {r['doi']: r for r in self.crossref(dois=[r['doi'] for r in hidden], limit=min(100, len(hidden)))
                     if r.get('doi')}
        except (LiteratureError, ValueError):
            return records
        for record in hidden:
            match = found.get(record['doi'].strip().lower())
            if not match:
                continue
            filled = [key for key in ('title', 'authors') if not record.get(key) and match.get(key)]
            for key in filled:
                record[key] = match[key]
            if filled:
                record['filled_from_crossref'] = filled
        return records

    def arxiv_recent(self, query, limit=6):
        """The newest arXiv preprints matching a query, for controller code (the research frontier)."""
        return self.search_papers(query, 'arxiv', limit, sort='newest')['results']

    def _arxiv_feed(self, params, limit, *, abstract=None):
        body, _, _ = self._fetch('https://export.arxiv.org/api/query?' + urlencode(params), max_bytes=4_000_000)
        atom = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
        feed = ET.fromstring(body)
        total = feed.findtext('{http://a9.com/-/spec/opensearch/1.1/}totalResults')
        if not feed.findall('a:entry', atom) and total and total.isdigit() and int(total) > 0 and not params.get('id_list'):
            raise LiteratureError('arXiv returned an incomplete search feed; try later or another provider.', code='source_unavailable')
        results = []
        for entry in feed.findall('a:entry', atom)[:limit]:
            identifier = (entry.findtext('a:id', '', atom)).rsplit('/abs/', 1)[-1]
            if not ARXIV_ID.fullmatch(identifier):
                continue
            doi = entry.findtext('x:doi', '', atom).strip().lower() or None
            results.append({'identifier': 'arxiv:' + identifier,
                'title': _plain(entry.findtext('a:title', '', atom), None),
                'authors': [_plain(e.text, None) for e in entry.findall('a:author/a:name', atom)],
                'published': entry.findtext('a:published', '', atom),
                'updated': entry.findtext('a:updated', '', atom),
                'abstract': _plain(entry.findtext('a:summary', '', atom), abstract),
                'categories': [c.get('term') for c in entry.findall('a:category', atom) if c.get('term')],
                'journal_ref': _plain(entry.findtext('x:journal_ref', '', atom), None) or None,
                **({'comment': comment} if (comment := _plain(entry.findtext('x:comment', '', atom), None)) else {}),
                **({'doi': doi} if doi and re.fullmatch(r'10\.\d{4,9}/\S+', doi) else {}),
                'url': 'https://arxiv.org/abs/' + identifier,
                'open_access_url': 'https://arxiv.org/pdf/' + identifier})
        return results

    def arxiv_search(self, query, *, limit=25, newest=False, categories=()):
        """An arXiv search for controller code: up to 50 records, optionally limited to categories (math.AP, ...)."""
        query = ' '.join(str(query or '').split())[:300]
        if not query or type(limit) is not int or not 1 <= limit <= 50:
            raise LiteratureError('Give a query and a limit from 1 to 50')
        cats = [c for c in categories if isinstance(c, str) and re.fullmatch(r'[a-z\-]+(?:\.[A-Za-z\-]+)?', c)]
        expression = arxiv_query(query)
        if cats:
            expression = f'({expression}) AND (' + ' OR '.join('cat:' + c for c in cats) + ')'
        params = {'search_query': expression, 'start': 0, 'max_results': limit,
                  'sortBy': 'submittedDate' if newest else 'relevance', 'sortOrder': 'descending'}
        return self._cache_search('arxiv-recent' if newest else 'arxiv', expression, limit,
                                  lambda: self._arxiv_feed(params, limit))['results']

    def arxiv_records(self, identifiers):
        """arXiv metadata and full abstracts for up to 100 identifiers in one request."""
        ids = list(dict.fromkeys(re.sub(r'v\d+$', '', str(i).strip()) for i in identifiers
                                 if isinstance(i, str) and ARXIV_ID.fullmatch(i.strip())))[:100]
        if not ids:
            return []
        params = {'id_list': ','.join(ids), 'start': 0, 'max_results': len(ids)}
        return self._cache_search('arxiv-ids', ','.join(sorted(ids)), len(ids),
                                  lambda: self._arxiv_feed(params, len(ids)))['results']

    def lookup_identifier(self, identifier):
        """The registry record of one DOI or arXiv identifier, or found=false when the registry has none."""
        value = upgrade_url(str(identifier or '').strip())
        value = re.sub(r'^https://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/', 'arxiv:', value, flags=re.I)
        value = re.sub(r'\.pdf$|/$', '', value, flags=re.I)
        value = re.sub(r'^https://(?:dx\.)?doi\.org/', 'doi:', value, flags=re.I)
        arxiv_doi = re.fullmatch(r'(?:doi:\s*)?10\.48550/arxiv\.(.+)', value, re.I)
        if arxiv_doi:
            value = 'arxiv:' + arxiv_doi.group(1)
        bare = re.sub(r'^(?:arxiv|doi)\s*:\s*', '', value, flags=re.I)
        if ARXIV_ID.fullmatch(bare) and not bare.startswith('10.'):
            versioned = re.search(r'v(\d+)$', bare)
            records = self.arxiv_records([bare])
            if not records:
                return {'identifier': 'arxiv:' + bare, 'registry': 'arXiv API', 'found': False,
                        'note': 'arXiv has no paper with this identifier.'}
            record = dict(records[0])
            latest = re.search(r'v(\d+)$', record['identifier'])
            if latest:
                record['latest_version'] = int(latest.group(1))
                record['identifier'] = re.sub(r'v\d+$', '', record['identifier'])
            if versioned and latest and int(versioned.group(1)) > int(latest.group(1)):
                return {'identifier': 'arxiv:' + bare, 'registry': 'arXiv API', 'found': False,
                        'note': f'arXiv has this paper only up to version {latest.group(1)}.', 'record': record}
            text = ' '.join(str(record.get(k) or '') for k in ('comment', 'abstract'))
            if re.search(r'\b(?:this (?:paper|article|submission) (?:has been|is) withdrawn|withdrawn by (?:the )?(?:author|arxiv))', text, re.I):
                record['withdrawn'] = True
            return {'identifier': 'arxiv:' + bare, 'registry': 'arXiv API', 'found': True, 'record': record}
        if not re.fullmatch(r'10\.\d{4,9}/\S+', bare):
            raise LiteratureError('Give one DOI (10.xxxx/...) or arXiv identifier (2303.09521, math/0601001)')
        doi = bare.lower().rstrip('.,;')
        records = self.crossref(dois=[doi], limit=1, abstract=1200)
        if records:
            return {'identifier': 'doi:' + doi, 'registry': 'Crossref', 'found': True,
                    'record': {k: v for k, v in records[0].items() if k not in ('references', 'reference_texts')}}
        # Not every DOI is Crossref's (DataCite, mEDRA): the DOI registry itself says whether it exists.
        try:
            body = self._cache_search('doi-handle', doi, 1, lambda: [self._doi_handle(doi)])['results'][0]
        except LiteratureError as exc:
            if 'Offline mode' in str(exc):
                raise
            return {'identifier': 'doi:' + doi, 'registry': 'Crossref', 'found': False,
                    'note': 'Crossref has no record of this DOI; the DOI registry could not be asked (' + str(exc)[:120] + ').'}
        if not body.get('exists'):
            return {'identifier': 'doi:' + doi, 'registry': 'DOI registry', 'found': False,
                    'note': 'The DOI registry (doi.org) has no such DOI: it does not resolve.'}
        return {'identifier': 'doi:' + doi, 'registry': 'DOI registry', 'found': True,
                'note': 'The DOI exists but Crossref has no metadata for it (another registration agency).',
                'record': {'identifier': 'doi:' + doi, 'doi': doi, 'url': body.get('url') or 'https://doi.org/' + doi}}

    def _doi_handle(self, doi):
        try:
            body, _, _ = self._fetch('https://doi.org/api/handles/' + quote(doi, safe='/():._-;<>'), max_bytes=200_000)
        except LiteratureError as exc:
            if 'HTTP 404' in str(exc):
                return {'exists': False}
            raise
        value = json.loads(body)
        url = next((v.get('data', {}).get('value') for v in value.get('values') or []
                    if isinstance(v, dict) and v.get('type') == 'URL' and isinstance(v.get('data'), dict)), None)
        return {'exists': value.get('responseCode') == 1,
                **({'url': url} if isinstance(url, str) and url.startswith('https://') else {})}

    def zbmath_search(self, query, *, limit=25):
        """A zbMATH Open search for controller code: up to 50 records, with their open reference lists."""
        query = ' '.join(str(query or '').split())[:300]
        if not query or type(limit) is not int or not 1 <= limit <= 50:
            raise LiteratureError('Give a query and a limit from 1 to 50')

        def fetch():
            params = {'search_string': query, 'page': 0, 'results_per_page': limit}
            try:
                body, _, _ = self._fetch('https://api.zbmath.org/v1/document/_search?' + urlencode(params), max_bytes=8_000_000)
            except LiteratureError as exc:
                if 'HTTP 404' in str(exc):
                    return []  # zbMATH answers an empty search with 404
                raise
            out = []
            for r in (json.loads(body).get('result') or [])[:limit]:
                if not isinstance(r, dict):
                    continue
                record = _zbmath_record(r)
                record['zbmath_document'] = r.get('id') if type(r.get('id')) is int else None
                # Open reference lists: the cited work's internal zbMATH document number, DOI and citation text.
                record['cited_works'] = [{'zbmath_document': (x.get('zbmath') or {}).get('document_id'),
                                          'doi': str(x.get('doi') or '').lower() or None,
                                          'text': _plain(x.get('text'), None)}
                                         for x in r.get('references') or [] if isinstance(x, dict)]
                out.append(record)
            return out
        return self._unhide(self._cache_search('zbmath-full', query, limit, fetch)['results'])

    def s2_links(self, identifier, direction='citations', *, limit=500):
        """Works citing (direction='citations') or cited by ('references') a paper, from Semantic Scholar.

        The identifier is a DOI, an arXiv ID or a Semantic Scholar paper ID. Without a key the shared
        quota refuses in bursts; the transport backs off and marks the source down after repeated refusals."""
        if direction not in ('citations', 'references') or type(limit) is not int or not 1 <= limit <= 1000:
            raise LiteratureError('Give a direction (citations or references) and a limit from 1 to 1000')
        ident = str(identifier or '').strip()
        if re.fullmatch(r'10\.\d{4,9}/\S+', ident, re.I):
            key = 'DOI:' + ident.lower()
        elif ARXIV_ID.fullmatch(ident):
            key = 'ARXIV:' + re.sub(r'v\d+$', '', ident)
        elif re.fullmatch(r'[0-9a-f]{40}', ident):
            key = ident
        else:
            raise LiteratureError('Give a DOI, an arXiv ID or a Semantic Scholar paper ID')

        def fetch():
            headers = {}
            if os.environ.get('SEMANTIC_SCHOLAR_API_KEY'):
                headers['x-api-key'] = os.environ['SEMANTIC_SCHOLAR_API_KEY']
            params = {'fields': 'title,authors,year,abstract,url,externalIds,venue,publicationTypes,citationCount',
                      'limit': limit}
            body, _, _ = self._fetch(f'https://api.semanticscholar.org/graph/v1/paper/{quote(key, safe="/().-_;:")}/{direction}?'
                                     + urlencode(params), headers=headers, max_bytes=8_000_000)
            side = 'citingPaper' if direction == 'citations' else 'citedPaper'
            out = []
            for item in json.loads(body).get('data') or []:
                paper = item.get(side) if isinstance(item, dict) else None
                if isinstance(paper, dict) and paper.get('title'):
                    record = _s2_record(paper)
                    record['venue'] = _plain(paper.get('venue'), None) or None
                    out.append(record)
            return out
        return self._cache_search('s2-' + direction, key, limit, fetch)['results']

    def references_of(self, doi):
        """DOIs a DOI cites, from the OpenCitations index (open, no key)."""
        if not isinstance(doi, str) or not re.fullmatch(r'10\.\d{4,9}/\S+', doi.strip()):
            raise LiteratureError('Give a DOI')
        doi = doi.strip().lower()

        def fetch():
            body, _, _ = self._fetch('https://api.opencitations.net/index/v2/references/doi:' + quote(doi, safe='/().-_;:'),
                                     max_bytes=10_000_000)
            found = []
            for item in json.loads(body):
                match = re.search(r'(?:^|\s)doi:(\S+)', str(item.get('cited') or '')) if isinstance(item, dict) else None
                if match:
                    found.append(match.group(1).lower())
            return list(dict.fromkeys(found))
        return self._cache_search('opencitations-references', doi, 1, fetch)['results']

    def citing(self, doi, limit=10):
        """Works citing a DOI, from Semantic Scholar, for controller code ("cited by"). Without a key its
        shared quota is small; a refusal marks it unavailable for this session."""
        if not isinstance(doi, str) or not re.fullmatch(r'10\.\d{4,9}/\S+', doi.strip()) or type(limit) is not int or not 1 <= limit <= 50:
            raise LiteratureError('Give a DOI and a limit from 1 to 50')
        doi = doi.strip().lower()

        def fetch():
            headers = {}
            if os.environ.get('SEMANTIC_SCHOLAR_API_KEY'):
                headers['x-api-key'] = os.environ['SEMANTIC_SCHOLAR_API_KEY']
            params = {'fields': 'title,authors,year,abstract,url,externalIds,openAccessPdf,citationCount', 'limit': limit}
            body, _, _ = self._fetch(f'https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe="/().-_;:")}/citations?'
                                     + urlencode(params), headers=headers, max_bytes=2_000_000)
            return [_s2_record(item['citingPaper']) for item in json.loads(body).get('data', [])[:limit]
                    if isinstance(item, dict) and isinstance(item.get('citingPaper'), dict)]
        return self._cache_search('s2-citations', doi, limit, fetch)['results']

    def crossref(self, *, query=None, dois=(), limit=10, texts=False, offset=0, abstract=None):
        """Crossref works for controller code: a bibliographic search, or a batch of DOIs. Free and
        uncapped; records carry open reference lists (DOIs), used to follow citations backwards."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise LiteratureError('Crossref batches hold 1 to 100 works')
        params = {'rows': limit, 'select': 'DOI,title,author,issued,type,container-title,is-referenced-by-count,reference,abstract,link'}
        if type(offset) is int and offset > 0:
            params['offset'] = offset
        if dois:
            clean = [d.strip().lower() for d in dois if isinstance(d, str) and re.fullmatch(r'10\.\d{4,9}/\S+', d.strip())][:limit]
            if not clean:
                return []
            params['filter'] = ','.join('doi:' + d.replace(',', '') for d in clean)
        elif isinstance(query, str) and query.strip():
            params['query.bibliographic'] = query.strip()[:300]
        else:
            raise LiteratureError('Give a Crossref query or DOIs')

        def fetch():
            body, _, _ = self._fetch('https://api.crossref.org/works?' + urlencode(params), max_bytes=4_000_000)
            out = []
            for r in (json.loads(body).get('message') or {}).get('items', [])[:limit]:
                year = ((r.get('issued') or {}).get('date-parts') or [[None]])[0][0]
                refs = [str(x['DOI']).lower() for x in r.get('reference') or [] if isinstance(x, dict) and x.get('DOI')]
                out.append({'identifier': 'doi:' + str(r.get('DOI', '')).lower(), 'doi': str(r.get('DOI', '')).lower() or None,
                            'title': _crossref_title((r.get('title') or [''])[0]), 'type': r.get('type'), 'year': year,
                            'authors': [_plain(' '.join(x for x in (a.get('given'), a.get('family')) if x), None)
                                        for a in r.get('author') or [] if isinstance(a, dict)],
                            'venue': _plain(unescape(re.sub(r'<[^>]+>', '', (r.get('container-title') or [''])[0])), None) or None,
                            'full_text_url': next((link['URL'] for link in r.get('link') or [] if isinstance(link, dict)
                                                   and link.get('content-type') == 'application/pdf'
                                                   and isinstance(link.get('URL'), str) and link['URL'].startswith('https://')), None),
                            'cited_by': r.get('is-referenced-by-count'), 'references': ['doi:' + d for d in refs],
                            **({'reference_texts': [_plain(' '.join(str(x.get(k) or '') for k in (
                                'unstructured', 'author', 'volume-title', 'article-title', 'year')), 300)
                                for x in r.get('reference') or [] if isinstance(x, dict)]} if texts else {}),
                            'abstract': _plain(re.sub(r'<[^>]+>', ' ', r.get('abstract') or ''), abstract),
                            'url': 'https://doi.org/' + str(r.get('DOI', ''))})
            return out
        key = json.dumps([params.get('query.bibliographic'), params.get('filter'), params.get('offset', 0)] + (['texts'] if texts else [])
                         + ([f'abstract {abstract}'] if abstract is not None and abstract != 900 else []))
        return self._cache_search('crossref', key, limit, fetch)['results']

    def repository_versions(self, doi):
        """OpenAIRE's links to OpenDOAR deposits for an exact DOI. Read a deposit's own
        abstract and file fields, rather than the aggregator's merged description."""
        if not isinstance(doi, str) or len(doi) > 200 or not re.fullmatch(r'10\.\d{4,9}/\S+', doi):
            return []
        doi = doi.lower()
        def objects(value):
            return [x for x in (value if isinstance(value, list) else [value]) if isinstance(x, dict)]
        def first(value, key='$'):
            return next((x[key] for x in objects(value) if isinstance(x.get(key), str)), '')
        def fetch():
            params = urlencode({'doi': doi, 'format': 'json', 'size': 1})
            body, _, _ = self._fetch('https://api.openaire.eu/search/publications?' + params, max_bytes=1000000)
            result_set = json.loads(body).get('response', {}).get('results') or {}
            versions = []
            for row in objects(result_set.get('result')):
                parent = row.get('metadata', {}).get('oaf:entity', {}).get('oaf:result') or {}
                if not any(x.get('@classid') == 'doi' and str(x.get('$', '')).lower() == doi for x in objects(parent.get('pid'))):
                    continue
                for item in objects((parent.get('children') or {}).get('result')):
                    if not any(str(x.get('@id', '')).startswith('opendoar____::') for x in objects(item.get('collectedfrom'))):
                        continue
                    for inst in objects(item.get('instance')):
                        if first(inst.get('accessright'), '@classid') != 'OPEN':
                            continue
                        url = first(inst.get('fulltext'))
                        abstract = _plain(first(item.get('description')), None)
                        # OpenAIRE's legacy index clips some descriptions at 500 characters.
                        # Only complete sentences from those deposits are admissible evidence.
                        if len(abstract) >= 500 and not re.search(r'[.!?][”")]*$', abstract):
                            abstract = re.sub(r'[^.!?]*$', '', abstract)
                        versions.append({'title': first(item.get('title')),
                                         'authors': [x['$'] for x in objects(item.get('creator')) if x.get('$')],
                                         'abstract': abstract, 'url': first(inst.get('url')),
                                         'full_text_url': url if url.startswith('https://') else ''})
            return sorted(versions, key=lambda v: (bool(v['full_text_url']), bool(v['abstract'])), reverse=True)
        return self._cache_search('openaire-version', doi, 8, fetch)['results']

    def citations_of(self, doi):
        """DOIs of the works citing a DOI, from the OpenCitations index (open, no key)."""
        if not isinstance(doi, str) or not re.fullmatch(r'10\.\d{4,9}/\S+', doi.strip()):
            raise LiteratureError('Give a DOI')
        doi = doi.strip().lower()

        def fetch():
            body, _, _ = self._fetch('https://api.opencitations.net/index/v2/citations/doi:' + quote(doi, safe='/().-_;:'),
                                     max_bytes=10_000_000)
            found = []
            for item in json.loads(body):
                match = re.search(r'(?:^|\s)doi:(\S+)', str(item.get('citing') or '')) if isinstance(item, dict) else None
                if match:
                    found.append(match.group(1).lower())
            return list(dict.fromkeys(found))
        return self._cache_search('opencitations', doi, 1, fetch)['results']

    def arxiv_by_titles(self, titles):
        """arXiv versions of several papers in one request: a few distinctive title words each."""
        groups, batches = [], []
        for title in titles:
            words = [w for w in re.findall(r'[A-Za-z]{4,}', re.sub(r'\$.*?\$', ' ', str(title)))
                     if w.lower() not in TITLE_STOP and w.lower() not in ('with', 'from', 'that', 'this', 'into')][:4]
            if len(words) < 2:
                continue
            group = '(' + ' AND '.join('ti:' + w for w in words) + ')'
            if groups and len(' OR '.join(groups + [group])) > 900:
                batches.append(groups)
                groups = []
            groups.append(group)
        if groups:
            batches.append(groups)
        results = {}
        for batch in batches:
            query = ' OR '.join(batch)
            limit = max(20, min(100, len(batch) * 4))
            hits = self._cache_search('arxiv-titles', query, limit, lambda: self._search('arxiv', query, limit))['results']
            results.update((r['identifier'], r) for r in hits)
        return list(results.values())

    def _chain(self, surname, title, topic):
        """Papers that cite the work and are about the topic, with an arXiv version (no full-text search).

        The work's DOI comes from Crossref; who cites it from OpenCitations and from the reference lists of
        Crossref's papers on the topic; their arXiv versions from one grouped title search."""
        info = {'work_dois': [], 'citing_known': 0, 'topic_papers': 0, 'topic_citing': 0, 'on_arxiv': 0}
        words, name = title_words(title), _fold(surname)
        if title:
            def fits(r):
                found = set(title_words(r.get('title')))
                return (words and len(set(words) & found) >= max(1, (len(words) + 1) // 2)
                        and name in _fold(' '.join(r.get('authors') or [])))
            info['work_dois'] = [r['doi'] for r in self.crossref(query=f'{title} {surname}', limit=6) if r.get('doi') and fits(r)]
        citing = set()
        for doi in info['work_dois']:
            try:
                citing.update(self.citations_of(doi))
            except LiteratureError:
                pass
        info['citing_known'] = len(citing)
        works = {'doi:' + d for d in info['work_dois']}

        def cites(r):
            if r.get('doi') in citing or works & set(r.get('references') or []):
                return True
            return any(name in _fold(t) and (not words or any(w in _fold(t) for w in words)) for t in r.get('reference_texts') or [])
        papers, chosen = [], []
        for offset in (0, 100, 200):  # further pages when the first holds too few citing papers
            page = self.crossref(query=topic or title or surname, limit=100, texts=True, offset=offset)
            papers += page
            chosen += [r for r in page if cites(r)]
            if len(page) < 100:
                break
        info['topic_papers'] = len(papers)
        # Crossref's relevance order, recent papers first (likelier to have an arXiv version with HTML).
        chosen.sort(key=lambda r: not (str(r.get('year') or '').isdigit() and int(r['year']) >= 2010))
        info['topic_citing'] = len(chosen)
        found = self.arxiv_by_titles([r['title'] for r in chosen]) if chosen else []
        hits = []
        for r in chosen:
            mine = set(re.findall(r'[a-z]{4,}', _fold(re.sub(r'\$.*?\$', ' ', r['title']))))
            for a in found:
                theirs = set(re.findall(r'[a-z]{4,}', _fold(re.sub(r'\$.*?\$', ' ', a['title']))))
                if mine and len(mine & theirs) / len(mine | theirs) >= 0.6:
                    hits.append({'arxiv_id': a['identifier'][6:], 'title': r['title'], 'year': r.get('year'), 'doi': r.get('doi')})
                    break
        info['on_arxiv'] = len(hits)
        return hits, info

    def find_quotes(self, author, locator='', topic='', title='', offset=0, limit=None):
        """Find papers that cite the work, open them, and read the lines that cite it.

        The citation chain: papers citing the work (OpenCitations, Crossref reference lists) and about the topic,
        read through their arXiv versions. Failing that, papers on the topic from arXiv."""
        if not isinstance(author, str) or not re.fullmatch(r"[^\W\d_][\w'\-. ]{1,60}", author.strip()):
            raise LiteratureError('Give the author\'s surname, e.g. "Brezis"')
        if type(offset) is not int or offset < 0 or (limit is not None and (type(limit) is not int or limit < 1)):
            raise LiteratureError('Give a nonnegative quote offset and a positive limit', code='invalid_arguments')
        surname = author.strip().split()[-1]
        locator = locator.strip() if isinstance(locator, str) else ''
        topic = ' '.join(topic.split()) if isinstance(topic, str) else ''
        title = ' '.join(title.split()) if isinstance(title, str) else ''
        # A theorem-like number wins over a section number when the locator names both.
        number = place_number(locator)
        if not number and not topic:
            raise LiteratureError('Give a numbered locator (e.g. "Theorem 9.26") or a topic phrase')
        hits, chain, searched = [], None, ''
        if topic or title:
            try:
                hits, chain = self._chain(surname, title, topic)
            except LiteratureError as exc:  # e.g. Crossref unreachable: fall through to the last resort
                hits, chain = [], {'error': str(exc)[:200]}
            searched = 'citation chain: papers citing the work on the topic, via arXiv'
        if not hits:
            # Last resort: papers on the topic, scanned for citations of the work.
            searched = 'arXiv topic search'
            hits = [{**h, 'arxiv_id': h['identifier'][6:]} for h in self.search_papers(topic or surname, 'arxiv', 6)['results']]
        quotes, opened, failures = [], [], 0
        # Prefer the primary arXiv text when its citation brackets are available.
        seen_quotes = 0
        quote_room = self.result_room()

        def add_quote(row):
            nonlocal seen_quotes, quote_room
            seen_quotes += 1
            if seen_quotes <= offset:
                return True
            size = len(json.dumps(row, ensure_ascii=False))
            if quotes and ((limit is not None and len(quotes) >= limit) or size > quote_room):
                return False
            quotes.append(row)
            quote_room -= size
            return True

        more = False
        for hit in sorted(hits, key=lambda h: not h.get('arxiv_id')):
            url = hit.get('arxiv_id') or hit.get('open_access_url')
            if not url:
                continue
            try:
                document = self.open_paper(url)
                doc = document['document_id']
                metadata, lines = self._document(doc)
            except LiteratureError:
                failures += 1
                continue
            opened.append({'document_id': doc, 'title': hit.get('title'), 'year': hit.get('year'), 'source_url': document['source_url']})
            keys = bibliography_keys(lines, surname, title)
            # Scan the whole cached text locally; only the lines returned are charged.
            hits_here = []
            words = [w for w in re.findall(r'[^\W\d_]{5,}', _fold(topic))][:4]
            for i in range(len(lines)):
                window = ' '.join(lines[max(0, i - 1):i + 2])
                if number:
                    if re.search(_num(number), lines[i]) and cites_place(window, number, surname, keys):
                        hits_here.append(i + 1)
                elif keyed_citations(lines[i], keys) or (
                        _fold(surname) in _fold(lines[i]) and re.search(PLACE_WORDS + r'\.?\s*\d+(?:\.\d+)+', window, re.I)
                        and not re.search(r'(?:19|20)\d\d', lines[i])):
                    hits_here.append(i + 1)
            if not number:  # citations near the topic words first
                near = lambda n: any(w in _fold(' '.join(lines[max(0, n - 3):n + 2])) for w in words)
                hits_here = sorted(hits_here, key=lambda n: not near(n))
            for line in hits_here:
                if self.cancelled():
                    raise KeyboardInterrupt
                self._remaining_timeout()
                passage = self._paper_page(doc, metadata, lines, max(1, line - 1), line + 1, 1)
                cited = [place for _, place in keyed_citations(passage['passage'], keys)]
                if not add_quote({'title': hit.get('title'), 'cited_as': cited, **{k: passage[k] for k in (
                    'document_id', 'source_url', 'citation', 'start_line', 'end_line', 'passage', 'truncated')}}):
                    more = True
                    break
            if more:
                break
            entry = bibliography_entry(lines, keys[0]) if keys else ''
            if entry:  # how this paper lists the work: its edition matters for the numbering
                if not add_quote({'title': hit.get('title'), 'document_id': doc, 'bibliography_entry': entry, 'bibliography_keys': keys}):
                    more = True
                    break
        return {'author': surname, 'searched_with': searched, 'number': number,
                **({'citation_chain': chain} if chain else {}), 'papers_found': len(hits), 'papers_opened': opened,
                'unreadable_papers': failures, 'quotes': quotes, 'offset': offset,
                'next_offset': offset + len(quotes) if more else None,
                'evidence_kind': 'untrusted_cached_extraction',
                'note': 'Quotes show how other papers cite the work; check that the cited statement is the result sought.'}

    def search_web(self, query, limit=5, offset=0):
        if type(limit) is int and limit > 20:
            raise LiteratureError('Brave search pages accept up to 20 results')
        query = self._query(query, limit)
        if type(offset) is not int or offset < 0 or offset // limit > 9:
            raise LiteratureError('Brave accepts page offsets 0 through 9')
        page, skip = divmod(offset, limit)
        def fetch():
            key = os.environ.get('BRAVE_SEARCH_API_KEY')
            if not key:
                raise LiteratureError('Web search unavailable: set BRAVE_SEARCH_API_KEY, or use search_papers with arxiv.')
            body, _, _ = self._fetch('https://api.search.brave.com/res/v1/web/search?' + urlencode({'q': query, 'count': limit, **({'offset': page} if page else {})}),
                                     headers={'X-Subscription-Token': key}, max_bytes=1_000_000)
            return [{'title': _plain(r.get('title'), None), 'url': r.get('url'),
                     'description': _plain(r.get('description'), None)}
                    for r in json.loads(body).get('web', {}).get('results', [])[:limit]]
        result = self._cache_search('brave', query, limit, fetch, offset=page * limit)
        length = len(result['results'])
        result['results'] = result['results'][skip:]
        result.update(offset=offset, next_offset=(page + 1) * limit if page < 9 and length == limit else None)
        return result

    def _canonical(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise LiteratureError('Provide an arXiv identifier, DOI, or public HTTPS URL')
        value = upgrade_url(value.strip())
        if value.lower().startswith('arxiv-source:'):
            identifier = value[len('arxiv-source:'):]
            if not ARXIV_ID.fullmatch(identifier):
                raise LiteratureError('Provide a valid arXiv source identifier')
            return 'arxiv-source:' + identifier, 'https://arxiv.org/src/' + identifier, identifier
        if value.lower().startswith('arxiv:'):
            value = value[6:]
        if value.lower().startswith('doi:'):
            value = value[4:]
        if ARXIV_ID.fullmatch(value):
            return 'arxiv:' + value, 'https://arxiv.org/html/' + value, value
        if value.startswith('10.') and '/' in value:
            value = 'https://doi.org/' + quote(value.lower(), safe='/():._-')
        split = self._url(value)
        if split.hostname in ('doi.org', 'dx.doi.org'):
            doi = unquote(split.path).lstrip('/').lower()
            canonical = 'https://doi.org/' + quote(doi, safe='/():._-')
            return canonical, canonical, None
        if split.hostname in ARXIV_HOSTS:
            source = re.fullmatch(r'/(?:src|e-print)/(.+?)/?', split.path)
            if source:
                identifier = re.sub(r'\.(?:tar\.gz|tgz|tar|gz)$', '', source.group(1), flags=re.I)
                if not ARXIV_ID.fullmatch(identifier) or split.query:
                    raise LiteratureError('Use an arXiv /src/ or /e-print/ URL with an exact paper identifier and no query')
                host = 'export.arxiv.org' if split.hostname == 'export.arxiv.org' else 'arxiv.org'
                return 'arxiv-source:' + identifier, 'https://' + host + '/src/' + identifier, identifier
            match = re.fullmatch(r'/(?:abs|pdf|html)/(.+?)(?:\.pdf)?/?', split.path)
            if match and ARXIV_ID.fullmatch(match.group(1)):
                identifier = match.group(1)
                return 'arxiv:' + identifier, 'https://arxiv.org/html/' + identifier, identifier
        if is_source_archive(split.path):
            raise LiteratureError('Remote source archives are supported only through exact arXiv source URLs; upload a downloaded source bundle to read it locally')
        host = split.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        canonical = urlunsplit(('https', host, split.path or '/', split.query, ''))
        return canonical, canonical, None

    def open_paper(self, identifier_or_url, *, cache_only=False, section_offset=0):
        if type(section_offset) is not int or section_offset < 0:
            raise LiteratureError('Give a nonnegative section offset', code='invalid_arguments')
        canonical, url, arxiv_id = self._canonical(identifier_or_url)
        source_request = canonical.startswith('arxiv-source:')
        canonical_id = document_id = 'doc-' + _digest(canonical)[:24]
        index = self._read(('document-index', canonical_id + '.json'), 1000)
        if index is not None:
            document_id = json.loads(index).get('document_id')
            if not isinstance(document_id, str) or not DOC_ID.fullmatch(document_id):
                raise LiteratureError('Invalid cached document index')
        metadata = None
        raw = self._read(('documents', document_id + '.json'), DOCUMENT_META_BYTES)
        if raw is not None:
            metadata = json.loads(raw)
            if cache_only and metadata.get('canonical_identifier') != canonical:
                raise LiteratureError('Cached document does not match the requested identifier')
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(metadata['retrieved_at'])).total_seconds()
            except (KeyError, ValueError, TypeError):
                age = float('inf')
            immutable = bool(arxiv_id and re.search(r'v\d+$', arxiv_id))
            stale = not immutable and age >= 86400
            if not stale or not self.online or cache_only:
                metadata['cached'] = True
                if stale:
                    metadata['stale_cache'] = True
                return self._document_summary(metadata, section_offset)
        if cache_only or not self.online:
            raise LiteratureError('Offline mode: paper is not cached. Online retrieval must be enabled explicitly.')
        try:
            if source_request:
                body, content_type, source_url = self._fetch(url, allowed_hosts=ARXIV_HOSTS)
                final_canonical, _, final_identifier = self._canonical(source_url)
                if (not final_canonical.startswith('arxiv-source:')
                        or re.sub(r'v\d+$', '', final_identifier or '') != re.sub(r'v\d+$', '', arxiv_id)
                        or re.search(r'v\d+$', arxiv_id) and final_identifier != arxiv_id):
                    raise LiteratureError('arXiv source redirect changed the requested paper or version')
            else:
                body, content_type, source_url = self._fetch(url)
            if arxiv_id and not source_request and ('html' not in content_type or b'ltx_' not in body):
                raise LiteratureError('arXiv HTML unavailable')
        except LiteratureError as exc:
            if source_request:
                if 'robots.txt' in str(exc):
                    raise LiteratureError('arXiv robots.txt disallows this automated source download. Download the source yourself and attach its .tar.gz, .tgz or .tex file; SH can read it safely without executing it.', code='source_access_denied') from None
                raise
            if not arxiv_id or not ('HTTP 404' in str(exc) or 'HTML unavailable' in str(exc)):
                raise
            body, content_type, source_url = self._fetch('https://arxiv.org/pdf/' + arxiv_id)
        title, source_details = '', {}
        if source_request:
            if 'html' in content_type or body.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                raise LiteratureError('arXiv returned a webpage instead of TeX source; attach a downloaded source bundle')
            extracted = self._source(body)
            text, extractor = extracted['text'], 'bounded read-only TeX source reader v1'
            suffix = '.source.' + extracted['format']
            source_details = {'source_kind': 'tex_source', 'source_format': extracted['format'],
                              'source_files': extracted['files'], 'source_warnings': extracted['warnings']}
            title = 'arXiv ' + (final_identifier or arxiv_id) + ' TeX source'
        elif body.startswith(b'%PDF') or 'application/pdf' in content_type:
            text, extractor = self._pdf(body)
            suffix = '.pdf'
        elif 'html' in content_type or body.lstrip().lower().startswith((b'<!doctype html', b'<html')):
            parser = _HTMLText(self._remaining_timeout)
            decoded = decode_page(body, content_type)
            for offset in range(0, len(decoded), 8192):
                self._remaining_timeout()
                parser.feed(decoded[offset:offset + 8192])
            text, title, extractor, suffix = parser.text(), _plain(' '.join(parser.title), 300), 'stdlib HTMLParser with math alttext', '.html'
            self._remaining_timeout()
        elif content_type.startswith('text/plain'):
            text, extractor, suffix = decode_page(body, content_type), 'decoded public text', '.txt'
        else:
            raise LiteratureError('Unsupported document type. Supply a public HTML/PDF/text paper URL; paywalls are not bypassed.')
        if not text.strip():
            raise LiteratureError('No readable text found (possibly scanned PDF). Provide a text-accessible paper; OCR is not available.')
        if len(text.encode('utf-8')) > MAX_TEXT:
            raise LiteratureError('Extracted document exceeds the 4 MB text limit')
        if canonical.startswith('https://doi.org/') and suffix == '.html':
            self._landing_check(unquote(canonical[len('https://doi.org/'):]), title, text, source_url)
        if metadata is not None and (metadata.get('sha256') != _digest(body)
                                     or metadata.get('text_sha256') != _digest(text)):
            # Preserve saved locators when the source OR its extraction changes.
            document_id = 'doc-' + _digest(canonical + ':' + _digest(body) + ':' + _digest(text))[:24]
        lines = text.splitlines()
        metadata = {'document_id': document_id, 'canonical_identifier': canonical,
            'source_url': source_url, 'requested_url': url, 'title': title,
            'version': (re.search(r'v\d+$', (final_identifier if source_request else arxiv_id) or '') or ['unspecified; use versioned arXiv ID for reproducibility'])[0],
            'retrieved_at': _stamp(), 'sha256': _digest(body), 'text_sha256': _digest(text),
            'backend_version': BACKEND_VERSION, 'extractor': extractor,
            'content_type': content_type, 'original_file': document_id + suffix,
            'total_lines': len(lines), 'sections_from_text': True, 'cached': False,
            'warning': 'Untrusted external source. Extraction can damage mathematical notation; line numbers refer to this cached text. Verify exact hypotheses and important formulas against the original.',
            **source_details}
        self._write(('documents', document_id + suffix), body)
        self._write(('documents', document_id + '.txt'), text)
        self._write(('documents', document_id + '.json'), json.dumps(metadata, ensure_ascii=False))
        if document_id != canonical_id:
            self._write(('document-index', canonical_id + '.json'), json.dumps({'document_id': document_id}))
        return self._document_summary(metadata, section_offset)

    def _landing_check(self, doi, title, text, source_url):
        """A DOI's web page must show its work: a page that never names the registered title (a journal's home
        or current issue) is refused, so it is not cached and read as the paper."""
        try:
            records = self.crossref(dois=[doi], limit=1)
        except LiteratureError:
            return  # the registry could not be asked: nothing to compare with
        expected = records[0].get('title') if records else ''
        words = {w for w in re.findall(r'[a-z]{4,}', _fold(expected))} - {'with', 'from', 'that', 'this', 'their', 'into', 'some', 'remarks'}
        if len(words) < 2:
            return
        page = set(re.findall(r'[a-z]{4,}', _fold(title + '\n' + '\n'.join(text.splitlines()[:600]))))
        if len(words & page) < .6 * len(words):
            raise LiteratureError(f'The page this DOI leads to ({source_url}) does not show the work: Crossref registers it as '
                                  f'"{expected[:200]}", which the page never names (a journal or issue page?). '
                                  'Open the PDF or an arXiv version of the paper instead.', code='landing_page_mismatch')

    def import_local(self, path):
        """Import a workspace manuscript without uploading or permitting symlinks."""
        if not isinstance(path, str) or not path:
            raise LiteratureError('Provide a relative manuscript path in the workspace')
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts or any(x.startswith('.') for x in relative.parts):
            raise LiteratureError('Local manuscripts must be nonhidden relative workspace paths')
        source = self.root
        for part in relative.parts:
            source = source / part
            if source.is_symlink():
                raise LiteratureError('Symlinks are excluded from local manuscripts')
        if not source.resolve().is_relative_to(self.root) or not source.is_file() or source.stat().st_size > MAX_BYTES:
            raise LiteratureError('Manuscript must be a regular workspace file up to 10 MB')
        archive = is_source_archive(source.name)
        if source.suffix.lower() not in ('.pdf', '.tex', '.md', '.txt') and not archive:
            raise LiteratureError('Supported local manuscripts: PDF, TeX, Markdown, text, or a TeX source .tar.gz/.tgz bundle')
        body = source.read_bytes()
        if len(body) > MAX_BYTES:
            raise LiteratureError('Manuscript exceeds the 10 MB limit')
        source_url = 'workspace:' + relative.as_posix()
        document_id = 'doc-' + _digest(source_url + ':' + _digest(body))[:24]
        raw = self._read(('documents', document_id + '.json'), DOCUMENT_META_BYTES)
        if raw is not None:
            return self._document_summary({**json.loads(raw), 'cached': True})
        source_details = {}
        if archive:
            extracted = self._source(body)
            text, extractor = extracted['text'], 'bounded read-only TeX source reader v1'
            source_details = {'source_kind': 'tex_source', 'source_format': extracted['format'],
                              'source_files': extracted['files'], 'source_warnings': extracted['warnings']}
        elif source.suffix.lower() == '.pdf':
            text, extractor = self._pdf(body)
            if not text.strip():
                raise LiteratureError('This PDF has no extractable text. Scanned pages need text recognition (OCR)')
        else:
            try:
                text, encoding = decode_text(body, relative.as_posix())
            except ValueError as exc:  # names the file
                raise LiteratureError(str(exc)) from None
            extractor = 'UTF-8 local text' if encoding is None else f'local text ({ENCODING_NAMES.get(encoding, encoding)})'
        if not text.strip() or len(text.encode('utf-8')) > MAX_TEXT:
            raise LiteratureError('Manuscript extraction is empty or exceeds 4 MB')
        metadata = {'document_id': document_id, 'canonical_identifier': source_url,
                    'source_url': source_url, 'title': source.name, 'version': 'local content hash',
                    'retrieved_at': _stamp(), 'sha256': _digest(body), 'text_sha256': _digest(text),
                    'backend_version': BACKEND_VERSION, 'extractor': extractor,
                    'original_file': document_id + '.source' + ('.tar.gz' if source.name.lower().endswith('.tar.gz') else source.suffix.lower()),
                    'total_lines': len(text.splitlines()), 'sections': [], 'cached': False,
                    'warning': 'Local manuscript; never uploaded by the literature reader. Extraction can damage mathematical notation; verify against the original.',
                    **source_details}
        self._write(('documents', metadata['original_file']), body)
        self._write(('documents', document_id + '.txt'), text)
        self._write(('documents', document_id + '.json'), json.dumps(metadata, ensure_ascii=False))
        return self._document_summary(metadata)

    def _document_summary(self, metadata, offset=0):
        summary = {**metadata, 'next_action': 'Use read_paper or search_paper with document_id for exact cited passages.'}
        if 'source_files' in metadata:
            summary.update(source_files_total=len(metadata['source_files']), source_files_truncated=False)
        if metadata.get('sections_from_text') or metadata.get('sections'):
            # Derive the outline from the durable, size-checked text; dense input cannot overflow the metadata file.
            _, lines = self._document(metadata['document_id'])
            heading = re.compile(r'^(?:\d+(?:\.\d+)*\s+\S|abstract\b|references\b|bibliography\b|appendix\b|theorem\b|lemma\b|proposition\b)', re.I)
            room = self.result_room() - len(json.dumps({**summary, 'sections': []}, ensure_ascii=False)) - 128
            page, total, full = [], 0, False
            for n, line in enumerate(lines, 1):
                if not heading.match(line):
                    continue
                total += 1
                if total <= offset or full:
                    continue
                item = {'line': n, 'title': line}
                size = len(json.dumps(item, ensure_ascii=False)) + 2
                if size > room:
                    if not page:
                        page.append({'line': n, 'title': '', 'read_line_for_title': True})
                    full = True
                else:
                    page.append(item)
                    room -= size
            if offset > total:
                raise LiteratureError('Section offset is beyond the outline', code='invalid_arguments')
            summary.update(sections=page, sections_total=total, section_offset=offset,
                           next_section_offset=offset + len(page) if offset + len(page) < total else None)
        return summary

    def _source(self, body):
        deadline = time.monotonic() + self._remaining_timeout()
        try:
            return extract_source(body, deadline=deadline)
        except SourceArchiveError as exc:
            raise LiteratureError(str(exc), code='unsupported_source_archive') from None

    def _document(self, document_id):
        if not isinstance(document_id, str) or not DOC_ID.fullmatch(document_id):
            raise LiteratureError('Invalid document_id; use the ID returned by open_paper')
        raw = self._read(('documents', document_id + '.json'), DOCUMENT_META_BYTES)
        text = self._read(('documents', document_id + '.txt'))
        if raw is None or text is None:
            raise LiteratureError('Document not found in this workspace cache; use open_paper first')
        metadata = json.loads(raw)
        if metadata.get('text_sha256') != _digest(text):
            raise LiteratureError('Cached extraction changed; source hash verification failed')
        # Both local PDF extractors terminate every extracted page with a form
        # feed, including blank pages. Read it before splitlines loses the
        # boundaries. This also works for verified extractions cached by older builds.
        if metadata.get('extractor') in ('pdftotext -layout', 'pypdf subprocess') and text.rstrip('\r\n').endswith('\f'):
            starts, next_line = [], 1
            for page in text.rstrip('\r\n').removesuffix('\f').split('\f'):
                starts.append(next_line)
                next_line += len((page + '\f').splitlines())
            metadata['extracted_pages'] = len(starts)
            metadata['pdf_page_starts'] = starts
        return metadata, text.splitlines()

    def page_starts(self, document_id):
        """The first line of each page of an extracted PDF, numbered as _document numbers lines; [] without pages.

        pdftotext ends every page with a form feed, which splitlines() treats as a line break: the
        separator is lost from the lines themselves, so the page map is read from the cached text."""
        _, lines = self._document(document_id)
        pieces = self._read(('documents', document_id + '.txt')).splitlines(keepends=True)
        if len(pieces) != len(lines):
            return []
        starts = [1] + [n + 1 for n, piece in enumerate(pieces, 1) if piece.endswith('\x0c') and n < len(pieces)]
        return starts if len(starts) > 1 else []

    def read_paper(self, document_id, start_line=1, end_line=None, start_column=1):
        return self._paper_page(document_id, *self._document(document_id), start_line, end_line, start_column)

    def _paper_page(self, document_id, metadata, lines, start_line, end_line, start_column):
        fields = {'document_id': document_id, 'source_url': metadata['source_url'],
                  'text_sha256': metadata['text_sha256'], 'evidence_kind': 'untrusted_cached_extraction'}
        starts = metadata.get('pdf_page_starts', ())
        page = source_page(lines, document_id, fields, start_line, end_line, start_column, self.result_room())
        if starts:
            pages = {p: max(start_line, starts[p - 1]) for p in
                     range(bisect_right(starts, start_line), bisect_right(starts, page['end_line']) + 1) if p}
            if pages:
                extra = {'pdf_pages_first_excerpt_line': pages}
                # Exact passage and a correct cursor are more important than redundant page labels.
                if len(json.dumps({**page, **extra}, ensure_ascii=False)) <= self.result_room():
                    page.update(extra)
        return page

    def search_paper(self, document_id, query, offset=0, limit=None):
        query = self._query(query, 1)
        metadata, lines = self._document(document_id)
        def annotate(hit):
            if starts := metadata.get('pdf_page_starts'):
                hit['pdf_page'] = bisect_right(starts, hit['line'])
        return match_page(lines, query, document_id, {'document_id': document_id, 'source_url': metadata['source_url'],
                          'text_sha256': metadata['text_sha256']}, offset, limit, self.result_room(), annotate)

    def _pdf(self, body):
        if b'%PDF-' not in body[:1024]:
            head = body[:4000].decode('utf-8', 'replace')
            raise LiteratureError('This file is not a PDF' + (
                ': it looks like LaTeX source; rename it to .tex.' if re.search(r'\\(?:documentclass|begin\{|section\b)', head)
                else ' (it does not start with %PDF-).' if body.strip() else ': it is empty.'))
        command = shutil.which('pdftotext')
        if command:
            with tempfile.TemporaryDirectory(prefix='mathagent-paper-') as directory:
                source, target = Path(directory) / 'paper.pdf', Path(directory) / 'paper.txt'
                source.write_bytes(body)
                try:
                    result = subprocess.run([command, '-layout', '-enc', 'UTF-8', str(source), str(target)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=self._remaining_timeout(), check=False, preexec_fn=_limit_output)
                except subprocess.TimeoutExpired:
                    raise LiteratureError('PDF extraction timed out') from None
                if result.returncode or not target.exists():
                    raise LiteratureError('This PDF could not be read: pdftotext failed on it (the file may be damaged '
                                          'or truncated). Export it again, or supply the TeX source.')
                if target.stat().st_size > MAX_TEXT:
                    raise LiteratureError('This PDF could not be read: its extracted text exceeds 4 MB.')
                return target.read_text(encoding='utf-8'), 'pdftotext -layout'
        import importlib.util
        if importlib.util.find_spec('pypdf') is None:
            raise LiteratureError('PDF reader unavailable. Install poppler-utils (pdftotext) or the optional pypdf package; HTML papers work without it.')
        # Run the fallback out of process: even one malformed page must obey timeout.
        script = """import sys
import pypdf
try:
 import resource
 resource.setrlimit(resource.RLIMIT_FSIZE, (4000000, 4000000))
except ImportError:
 pass
reader = pypdf.PdfReader(sys.argv[1])
if reader.is_encrypted:
 raise ValueError('Encrypted PDF')
with open(sys.argv[2], 'w', encoding='utf-8') as out:
 for page in reader.pages:
  out.write(page.extract_text() or '')
  out.write('\\n\\f\\n')
"""
        with tempfile.TemporaryDirectory(prefix='mathagent-paper-') as directory:
            source, target = Path(directory) / 'paper.pdf', Path(directory) / 'paper.txt'
            source.write_bytes(body)
            try:
                result = subprocess.run([sys.executable, '-I', '-c', script, str(source), str(target)],
                    cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    timeout=self._remaining_timeout(), check=False)
            except subprocess.TimeoutExpired:
                raise LiteratureError('PDF extraction timed out') from None
            if result.returncode or not target.exists():
                # The reader's own last error line (bounded), never a path: it names what is wrong with the file.
                last = (result.stderr or b'')[-2000:].decode('utf-8', 'replace').strip().splitlines()[-1:] or ['']
                reason = re.sub(r'^[\w.]+(?:Error|Exception):\s*', '', last[0]).replace(directory, '')[:200]
                raise LiteratureError('This PDF could not be read' + (
                    ': it is encrypted.' if 'Encrypted PDF' in reason else
                    f': {reason}.' if reason else '.') + ' Export it again, or supply the TeX source.')
            if target.stat().st_size > MAX_TEXT:
                raise LiteratureError('This PDF could not be read: its extracted text exceeds 4 MB.')
            return target.read_text(encoding='utf-8'), 'pypdf subprocess'

    def _url(self, url):
        if not isinstance(url, str) or len(url) > 4000 or any(ord(c) < 33 for c in url) or '\\' in url:
            raise LiteratureError('Invalid public HTTPS URL')
        try:
            parts = urlsplit(url)
            if parts.scheme != 'https' or not parts.hostname or parts.username is not None or parts.password is not None or parts.port not in (None, 443):
                raise ValueError()
            host = parts.hostname
            if '%' in host or host.endswith('.') or host.casefold() == 'localhost' or host.casefold().endswith(('.localhost', '.local', '.internal')):
                raise ValueError()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host) or '.' not in host:
                    raise ValueError()
            else:
                if not self._public(address):
                    raise ValueError()
        except ValueError:
            raise LiteratureError('Only public HTTPS URLs without credentials or custom ports are allowed') from None
        return parts

    @staticmethod
    def _public(address):
        return address.is_global and not address.is_multicast and not address.is_unspecified and not address.is_reserved

    def _addresses(self, host):
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)},
                               key=lambda address: (ipaddress.ip_address(address).version, address))
        except OSError:
            raise LiteratureError('Could not resolve the public source hostname', code='source_unavailable') from None
        if not addresses or any(not self._public(ipaddress.ip_address(value)) for value in addresses):
            raise LiteratureError('Source hostname resolves to a nonpublic address; retrieval refused')
        return addresses

    def _request_once(self, url, headers, max_bytes):
        parts = self._url(url)
        addresses = self._addresses(parts.hostname)
        context = _tls_context()
        timeout = self._remaining_timeout()
        conn = http.client.HTTPSConnection(parts.hostname, timeout=timeout, context=context)
        finished = threading.Event()

        def stop_on_pause():
            # A blocked read wakes when its socket is shut down; a pause need not wait for the source.
            while not finished.wait(0.1):
                if self.cancelled():
                    try:
                        if conn.sock is not None:
                            conn.sock.shutdown(socket.SHUT_RDWR)
                    except (OSError, ValueError):
                        pass
                    return
        threading.Thread(target=stop_on_pause, daemon=True, name='mathagent-literature-pause').start()
        try:
            # Pin the validated IP while retaining the original TLS verification name.
            deadline = time.monotonic() + timeout
            for index, address in enumerate(addresses):
                raw = None
                try:
                    remaining = timeout if index == 0 else max(.1, deadline - time.monotonic())
                    raw = socket.create_connection((address, 443), timeout=remaining)
                    try:
                        conn.sock = context.wrap_socket(raw, server_hostname=parts.hostname)
                    except ssl.SSLCertVerificationError:
                        wider = _wider_tls_context()
                        if wider is None or context is wider:
                            raise
                        # The default store may lack a newer root; try the platform's other bundles once.
                        raw.close()
                        context = wider
                        raw = socket.create_connection((address, 443), timeout=max(.1, deadline - time.monotonic()))
                        conn.sock = context.wrap_socket(raw, server_hostname=parts.hostname)
                    break
                except ssl.SSLCertVerificationError:
                    if raw is not None:
                        raw.close()
                    raise
                except OSError:
                    if raw is not None:
                        raw.close()
                    if index == len(addresses) - 1 or time.monotonic() >= deadline:
                        raise
            path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
            from . import __version__
            conn.request('GET', path, headers={'User-Agent': f'SquareHarness/{__version__} literature research',
                                              'Accept-Encoding': 'gzip', **headers})
            response = conn.getresponse()
            status = response.status
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            length = response_headers.get('content-length', '')
            if length.isdigit() and int(length) > max_bytes:
                raise LiteratureError(f'The document is larger than the {max_bytes // 1_000_000} MB download limit. Attach a smaller part '
                                      '(a chapter or section) or download it yourself and attach it.', code='document_too_large')
            encoding = response_headers.get('content-encoding', 'identity').lower()
            if encoding not in ('', 'identity', 'gzip'):
                raise LiteratureError('Unsupported response compression')
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None
            chunks, size, downloaded = [], 0, 0
            deadline = time.monotonic() + timeout
            if self.deadline is not None:
                deadline = min(deadline, self.deadline)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LiteratureError('Document download timed out', code='source_unavailable')
                if conn.sock:
                    conn.sock.settimeout(min(self.timeout, remaining))
                chunk = response.read(min(65536, max_bytes + 1 - downloaded))
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise LiteratureError(f'The document is larger than the {max_bytes // 1_000_000} MB download limit. Attach a smaller part '
                                      '(a chapter or section) or download it yourself and attach it.', code='document_too_large')
                if decoder is not None:
                    chunk = decoder.decompress(chunk, max_bytes + 1 - size)
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise LiteratureError(f'The document is larger than the {max_bytes // 1_000_000} MB download limit. Attach a smaller part '
                                      '(a chapter or section) or download it yourself and attach it.', code='document_too_large')
            if decoder is not None and (not decoder.eof or decoder.unused_data):
                raise LiteratureError('Invalid or concatenated gzip response')
            return status, response_headers, b''.join(chunks)
        except LiteratureError:
            raise
        except ssl.SSLCertVerificationError:
            raise LiteratureError('HTTPS certificate verification failed; configure SSL_CERT_FILE with a current trusted CA bundle', code='source_unavailable') from None
        except (OSError, http.client.HTTPException, zlib.error):
            if self.cancelled():
                raise KeyboardInterrupt from None  # Paused: not a source failure, no strike.
            raise LiteratureError('HTTPS retrieval failed; check network and source availability', code='source_unavailable') from None
        finally:
            finished.set()
            conn.close()

    def _remaining_timeout(self):
        if self.deadline is None:
            return self.timeout
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise LiteratureError('Literature workflow time budget exhausted', code='workflow_budget')
        return min(self.timeout, remaining)

    def _pause(self, duration):
        if self.deadline is not None and time.monotonic() + duration >= self.deadline:
            raise LiteratureError('Literature workflow time budget insufficient for provider rate limit', code='workflow_budget')
        end = time.monotonic() + duration
        while (left := end - time.monotonic()) > 0:
            if self.cancelled():
                raise KeyboardInterrupt
            time.sleep(min(0.1, left))

    def _check_robots(self, url):
        parts = self._url(url)
        if (parts.hostname in API_HOSTS and (parts.hostname != 'export.arxiv.org' or parts.path == '/api/query')
                or parts.hostname == 'openlibrary.org' and parts.path.endswith('.json')):
            return
        origin = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
        if origin not in self._robots:
            rules = RobotFileParser()
            parts_cache = ('robots', _digest(origin) + '.json')
            try:
                cached = json.loads(self._read(parts_cache, 600_000) or '{}')
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached['retrieved_at'])).total_seconds()
            except (ValueError, KeyError, TypeError):
                cached, age = {}, float('inf')
            if age < 86400 and isinstance(cached.get('lines'), list):
                rules.parse(cached['lines'])
            else:
                try:
                    body, _, _ = self._fetch(origin + '/robots.txt', max_bytes=512_000, _robots=False)
                except LiteratureError as exc:
                    if 'HTTP 404' in str(exc) or 'HTTP 410' in str(exc):
                        lines = ['User-agent: *', 'Allow: /']
                    elif 'HTTP 401' in str(exc) or 'HTTP 403' in str(exc):
                        lines = ['User-agent: *', 'Disallow: /']
                    else:
                        raise
                else:
                    lines = body.decode('utf-8', errors='replace').splitlines()
                rules.parse(lines)
                self._write(parts_cache, json.dumps({'retrieved_at': _stamp(), 'lines': lines}))
            self._robots[origin] = rules
        if not self._robots[origin].can_fetch('SquareHarness', url):
            raise LiteratureError('Source robots.txt disallows automated retrieval of this URL')

    def _fetch(self, url, *, headers=None, max_bytes=MAX_BYTES, _robots=True, allowed_hosts=None):
        if not self.online:
            raise LiteratureError('Offline mode: internet access is disabled')
        headers = dict(headers or {})
        original_host = self._url(url).hostname
        if allowed_hosts is not None and original_host not in allowed_hosts:
            raise LiteratureError('Source download host is outside the permitted arXiv origins')
        provider = QUOTA_HOSTS.get(original_host)
        if provider in self.down:
            since = self._down_at.get(provider)
            if since is None or time.monotonic() - since < DOWN_COOLDOWN:
                raise LiteratureError(f'{provider} unavailable earlier in this session; use cached evidence or another provider', code='source_unavailable')
            # A rate limit or an outage may have passed: one probe, and one more failure marks it down again.
            self.down.discard(provider)
            self._strikes[provider] = STRIKES - 1
        auth_headers = {'authorization', 'x-api-key', 'x-subscription-token'}
        if any(k.casefold() in auth_headers for k in headers) and original_host not in ('api.semanticscholar.org', 'api.search.brave.com'):
            raise LiteratureError('API credentials may only be sent to their fixed provider host')
        retries, redirects, failures = 0, 0, 0
        while True:
            host = self._url(url).hostname
            if allowed_hosts is not None and host not in allowed_hosts:
                raise LiteratureError('Source redirect left the permitted arXiv origins')
            self._remaining_timeout()
            if _robots:
                self._check_robots(url)
            if self.stats['requests'] >= self.max_requests:
                raise LiteratureError('Literature HTTP request budget exhausted; use cached evidence')
            interval = 3.0 if host.endswith('arxiv.org') else 0.25
            origin = 'https://' + host
            rules = self._robots.get(origin)
            if rules:
                interval = max(interval, rules.crawl_delay('SquareHarness') or 0)
            # Share politeness across jobs in this server, rather than multiplying
            # the allowed rate by the number of concurrent DS4 workers.
            rate_host = 'arxiv.org' if host.endswith('arxiv.org') else host
            with _RATE_LOCK:
                host_lock = _HOST_LOCKS.setdefault(rate_host, _Gate())
            with host_lock.hold(self.urgent, self.cancelled):  # a pause stops waiting for other jobs' turns
                delay = interval - (time.monotonic() - _LAST_REQUEST.get(rate_host, -1e9))
                if delay > 0:
                    self._pause(delay)
                self._remaining_timeout()
                if not self.online:
                    raise LiteratureError('Offline mode: internet access is disabled')
                if self.stats['requests'] >= self.max_requests:
                    raise LiteratureError('Literature HTTP request budget exhausted; use cached evidence', code='request_budget')
                self.stats['requests'] += 1
                _LAST_REQUEST[rate_host] = time.monotonic()
            if self.on_budget_change:
                self.on_budget_change()
            self._last_request[host] = time.monotonic()
            try:
                status, response_headers, body = self._request_once(url, headers, max_bytes)
            except LiteratureError as exc:
                untrusted = 'certificate' in str(exc)
                if exc.code == 'source_unavailable' and not untrusted and failures < 1:
                    failures += 1  # one transient network failure is retried after a short pause
                    self._pause(1.5)
                    continue
                if provider and exc.code == 'source_unavailable':
                    self._strike(provider, permanent=untrusted)
                raise
            if status in (301, 302, 303, 307, 308):
                if redirects >= 3 or not response_headers.get('location'):
                    raise LiteratureError('Too many redirects or missing redirect destination')
                target = urljoin(url, response_headers['location'])
                target_host = self._url(target).hostname
                if target_host != host:
                    headers = {k: v for k, v in headers.items() if k.casefold() not in auth_headers}
                url, redirects = target, redirects + 1
                continue
            if status == 429 and retries < 2:
                # Shared key-less quotas (Semantic Scholar) refuse in bursts: back off twice before giving up.
                # arXiv asks for at least 15 s between requests: wait that long (or its Retry-After), and hold
                # every job of this process back at the shared gate meanwhile.
                retry = response_headers.get('retry-after', '')
                slow = rate_host == 'arxiv.org'
                delay = float(retry) if retry.isdigit() else (ARXIV_BACKOFF if slow else (2.0, 6.0))[retries]
                if delay <= (60 if slow else 15):
                    delay = max(ARXIV_BACKOFF[0] if slow else 1, delay)
                    if self.deadline is not None and time.monotonic() + delay >= self.deadline:
                        raise LiteratureError('Literature workflow time budget insufficient for provider rate limit', code='workflow_budget')
                    with _RATE_LOCK:  # the shared gate makes this request and every other one wait
                        _LAST_REQUEST[rate_host] = max(_LAST_REQUEST.get(rate_host, 0), time.monotonic() + delay - interval)
                    retries += 1
                    continue
            if status >= 500 and failures < 1:
                failures += 1
                self._pause(2.0)
                continue
            if not 200 <= status < 300:
                if (status == 429 or status >= 500) and provider:
                    self._strike(provider)
                raise LiteratureError(f'Source returned HTTP {status}; check provider access or use another public source',
                                      code='source_unavailable' if status == 429 or status >= 500 else 'source_error')
            if provider:
                self._strikes.pop(provider, None)
            return body, response_headers.get('content-type', '').lower(), url

    def _strike(self, provider, *, permanent=False):
        """Count a failed request; a provider is marked down after STRIKES failures in a row (each already retried)."""
        self._strikes[provider] = self._strikes.get(provider, 0) + 1
        if permanent or self._strikes[provider] >= STRIKES:
            self.down.add(provider)
            self._down_at[provider] = None if permanent else time.monotonic()
