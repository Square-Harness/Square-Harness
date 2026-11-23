"""Clean, standalone downloads of a literature review: Markdown and BibTeX.

The review's own files (`review.md`, `references.bib`) stay as they are: the interface shows the first, and
write-ups pin the second by its keys. These downloads are rendered from the same saved state for a reader
outside Square Harness: the Markdown keeps the numbered citations and reference list without the interface's
evidence marks; the BibTeX holds only the works the review text cites, in citation order, with keys of the
usual `surname2019word` form.
"""

from datetime import date
import re
import unicodedata
from html import unescape

from .litreview import PIPELINE, ReviewRunner, _publication_record, _surname
from .writeup import _bibtex_title, latex_text

# Words that never make a citation key's title word.
_KEY_STOP = frozenset('a an the on of in for and to with from by at as is are its into über uber zur zum der die das '
                      'des den dem ein eine einer sur les la le une un des du et de sulla sui una il lo some new note '
                      'remarks remark'.split())
_BOOK_TYPES = frozenset(('book', 'monograph', 'reference-book', 'edited-book', 'book / book article', 'book-set'))
_CHAPTER_TYPES = frozenset(('book-chapter', 'book-section', 'book-part', 'reference-entry'))
_MEETING = re.compile(r'\b(?:conference|conf|symposium|symp|workshop|congress|colloquium|'
                      r'\d+(?:st|nd|rd|th) (?:international|annual|british|european))\b', re.I)
# A zbMATH-style imprint: "Bull. Am. Math. Soc. 53, 292-294 (1947)", "Ann. Math. (2) 203, No. 3, 869-932 (2026)".
_ZB_IMPRINT = re.compile(r'(?P<journal>.+?)\s+(?P<volume>\d{1,4}),\s*(?:No\.\s*(?P<number>[\w/-]+),\s*)?'
                         r'(?:Article ID \w+,\s*\d+ p\.|(?P<first>\d+)(?:\s*[-–]+\s*(?P<last>\d+))?)\s*\((?P<year>\d{4})\)\.?')


def _runner(state):
    if state.get('kind') != 'literature' or state.get('pipeline') != PIPELINE:
        raise ValueError('This job is not a literature review')
    ReviewRunner.refresh_screening_preview(state)
    runner = ReviewRunner(None)
    runner.state = state
    return runner


def _references(state):
    rows = [r for r in state.get('references') or [] if isinstance(r, dict) and type(r.get('k')) is int]
    return sorted(rows, key=lambda r: r['k'])


def _clean_title(record):
    """The record's title, or a variant without the U+FFFD marks a broken registry record carries."""
    title = ' '.join(str(record.get('title') or '').split())
    if '�' in title:
        for other in record.get('title_variants') or []:
            if isinstance(other, str) and other.strip() and '�' not in other:
                return ' '.join(other.split())
    return title


def review_markdown(state):
    """The review as standalone Markdown: title, text with [n] citations, the numbered reference list."""
    _runner(state)
    text = str(state.get('draft') or '').replace('\r\n', '\n')
    if not text.strip():
        raise ValueError('This literature review has no text yet')
    lines = []
    for line in text.split('\n'):
        if line.startswith('† ') and 'bibliographic record only' in line:
            continue  # the interface's legend for its evidence mark
        line = re.sub(r'\s†$', '', line)
        lines.append(line)
    text = '\n'.join(lines)
    text = _math_dollars(text)
    # zbMATH escapes a dollar in an imprint; a Markdown reader would start math there.
    text = text.replace('{\\$}', '\\$')
    # A saved line range is internal evidence, never part of a citation a reader can use.
    text = re.sub(r'\s?\((?:lines?\s+)?L\d+\s*[-–]\s*L?\d+\)', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'
    return text


def _math_dollars(text):
    """\\( … \\) becomes $…$ and \\[ … \\] becomes $$…$$, outside code."""
    parts = re.split(r'(```.*?```|`[^`\n]*`)', text, flags=re.S)
    for i in range(0, len(parts), 2):
        part = re.sub(r'\\\((.+?)\\\)', lambda m: '$' + m.group(1).strip() + '$', parts[i], flags=re.S)
        parts[i] = re.sub(r'\\\[(.+?)\\\]', lambda m: '$$' + m.group(1).strip() + '$$', part, flags=re.S)
    return ''.join(parts)


def _key_word(title):
    plain = re.sub(r'\$[^$]*\$|\\[A-Za-z]+', ' ', title)
    for word in re.findall(r'[^\W\d_]+', plain):
        folded = unicodedata.normalize('NFKD', word).encode('ascii', 'ignore').decode().lower()
        if len(folded) >= 3 and folded not in _KEY_STOP:
            return folded
    return 'work'


def _entry(record, title):
    """(BibTeX type, fields) of one source-bound record."""
    kind = str(record.get('type') or '').lower()
    venue = ' '.join(str(record.get('venue') or '').replace('{\\$}', '$').split())
    year = re.search(r'\b(?:1[5-9]|20)\d\d\b', str(record.get('year') or record.get('preprint_year') or ''))
    year = year[0] if year else ''
    fields = [('author', ' and '.join(' '.join(a.split()) for a in record['authors'])), ('title', title)]
    journal, volume, number, pages = record.get('journal'), record.get('volume'), record.get('number'), record.get('pages')
    imprint = _ZB_IMPRINT.fullmatch(venue) if venue and not journal else None
    if imprint:
        journal, volume, number = imprint['journal'].rstrip(', '), imprint['volume'], imprint['number']
        pages = imprint['first'] and (imprint['first'] + ('--' + imprint['last'] if imprint['last'] else ''))
        year = year or imprint['year']
    if kind in _BOOK_TYPES and not (kind == 'book / book article' and venue and _MEETING.search(venue)):
        entry = 'book'
        if record.get('publisher'):
            fields.append(('publisher', str(record['publisher'])))
        elif venue:
            fields.append(('note', venue))
    elif kind in _CHAPTER_TYPES and venue:
        entry = 'incollection'
        fields.append(('booktitle', venue))
        fields += [('publisher', str(record['publisher']))] if record.get('publisher') else []
    elif venue and (kind == 'proceedings-article' or _MEETING.search(str(journal or venue))):
        entry = 'inproceedings'
        fields.append(('booktitle', str(journal or venue)))
        fields += [(name, str(value)) for name, value in (('volume', volume), ('pages', pages)) if value]
    elif venue and kind not in ('preprint', 'posted-content', 'dissertation', 'other'):
        entry = 'article'
        fields.append(('journal', str(journal or venue)))
        fields += [(name, str(value)) for name, value in (('volume', volume), ('number', number), ('pages', pages)) if value]
    else:
        entry = 'misc'
        if kind == 'dissertation':
            fields.append(('note', 'Dissertation' + (', ' + venue if venue else '')))
        elif venue:
            fields.append(('howpublished', venue))
        elif record.get('arxiv'):
            fields.append(('howpublished', 'arXiv:' + record['arxiv']))
    if year:
        fields.insert(2, ('year', year))
    if record.get('doi'):
        fields.append(('doi', str(record['doi'])))
    if record.get('arxiv'):
        fields += [('eprint', str(record['arxiv'])), ('archivePrefix', 'arXiv')]
    if record.get('zbl'):
        fields.append(('zbl', str(record['zbl'])))
    url = str(record.get('url') or '').strip()
    if not record.get('doi') and not record.get('arxiv') and re.fullmatch(r'https://[^\s<>{}"\\]+', url):
        fields.append(('url', url))
    return entry, fields, year


def review_bibtex(state):
    """BibTeX of the works the review text cites, in citation order, with unique `surname2019word` keys."""
    runner = _runner(state)
    rows = [row for row in _references(state) if row.get('cited', True)]
    entries, used = [], set()
    for row in rows:
        record = runner._by_n(row.get('n'))
        if not isinstance(record, dict):
            continue
        record = _publication_record(dict(record))
        record['title_variants'] = record.get('titles')
        title = _clean_title(record)
        authors = record.get('authors')
        if not title or not isinstance(authors, list) or not authors or not all(isinstance(a, str) and a.strip() for a in authors):
            continue
        kind, fields, year = _entry(record, title)
        surname = unicodedata.normalize('NFKD', _surname(authors[0])).encode('ascii', 'ignore').decode()
        base = (re.sub(r'[^A-Za-z]', '', surname).lower() or 'anon') + (year or 'nd') + _key_word(title)
        key, suffix = base, 0
        while key in used:
            suffix += 1
            key = base + 'abcdefghijklmnopqrstuvwxyz'[(suffix - 1) % 26] * (1 + (suffix - 1) // 26)
        used.add(key)
        body = []
        for name, value in fields:
            value = unescape(str(value)).replace('�', '?')
            if name in ('doi', 'eprint', 'url', 'archivePrefix'):
                rendered = value.replace('{', '').replace('}', '').replace('%', '\\%').replace('#', '\\#')
            elif name == 'title':
                rendered = _bibtex_title(value)
            elif name == 'author':
                rendered = latex_text(value)
            else:
                rendered = latex_text(value)
            body.append(f'  {name} = {{{rendered}}},')
        entries.append(f'@{kind}{{{key},\n' + '\n'.join(body) + '\n}')
    scope = state.get('scope') if isinstance(state.get('scope'), dict) else {}
    heading = re.match(r'#\s+(.+)', str(state.get('draft') or ''))
    # BibTeX reads an @ anywhere outside an entry as the start of one.
    title = ' '.join(str((heading and heading[1]) or scope.get('title') or scope.get('topic') or state.get('goal') or '').replace('@', ' ').split())[:200]
    head = (f'% Works cited in the literature review "{title}", in citation order.\n'
            f'% Exported by Square Harness on {date.today().isoformat()}. Each entry comes from a retrieved registry record.\n')
    if not entries:
        return head + '% The review cites no work.\n'
    return head + '\n' + '\n\n'.join(entries) + '\n'
