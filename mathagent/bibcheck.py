"""Checking a whole bibliography: every entry of a pasted reference list or an attached .bib, one by one.

No model is involved. A DOI or arXiv identifier is looked up in its registry (one request settles whether it
exists and what it names); an entry without one is matched against Crossref's bibliographic search, then
zbMATH Open. Each entry gets one verdict, and every difference with the registry record (year, authors,
title) is named. The checks share the job's request budget; entries the budget does not reach are listed as
not checked, never silently dropped.
"""

import json
import re
import unicodedata

from .writeup import entry_identifiers

VERDICTS = {
    'matches': 'Matches a registry record',
    'differs': 'Record found; metadata differs',
    'identifier_not_found': 'Identifier does not exist',
    'identifier_mismatch': 'Identifier names another work',
    'not_found': 'No matching record',
    'inconclusive': 'Inconclusive (a source failed)',
    'not_checked': 'Not checked (budget)',
}
_STOP = frozenset('a an the of on in for and to with from by at as is are its into via some new note remarks über '
                  'eine einer der die das des sur les la le une un et de du'.split())


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).casefold().replace('�', '')


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]+', _fold(re.sub(r'\\[A-Za-z]+|[{}$]', ' ', str(text or ''))))
            if len(w) > 2 and w not in _STOP}


def _surname(author):
    author = re.sub(r'[{}]', '', str(author or '')).strip()
    name = author.split(',')[0] if ',' in author else (author.split() or [''])[-1]
    return _fold(re.sub(r'[^\w\-]', '', name))


_ACCENTS = {"'": '\u0301', '`': '\u0300', '^': '\u0302', '"': '\u0308', '~': '\u0303', '=': '\u0304', '.': '\u0307',
            'H': '\u030b', 'c': '\u0327', 'v': '\u030c', 'u': '\u0306', 'k': '\u0328', 'r': '\u030a'}
_LETTERS = {'ss': 'ß', 'o': 'ø', 'O': 'Ø', 'l': 'ł', 'L': 'Ł', 'ae': 'æ', 'AE': 'Æ', 'oe': 'œ', 'OE': 'Œ', 'aa': 'å', 'AA': 'Å', 'i': 'ı'}


def _detex(text):
    """BibTeX accents as Unicode: Erd{\\H o}s becomes Erdős, G{\\"o}del becomes Gödel."""
    text = re.sub(r'\\([`\'^"~=.])\s*\{?\s*([A-Za-z])\s*\}?', lambda m: m.group(2) + _ACCENTS[m.group(1)], text)
    text = re.sub(r'\\([Hcvukr])(?:\s+|\s*\{\s*)([A-Za-z])\s*\}?', lambda m: m.group(2) + _ACCENTS[m.group(1)], text)
    text = re.sub(r'\\(ss|ae|AE|oe|OE|aa|AA|[oOlLi])\b\s*(?:\{\})?', lambda m: _LETTERS[m.group(1)], text)
    return unicodedata.normalize('NFC', text)


def _bib_fields(body):
    fields = {}
    for match in re.finditer(r'(\w+)\s*=\s*', body):
        name, i = match.group(1).lower(), match.end()
        if i >= len(body):
            break
        if body[i] == '{':
            depth, j = 0, i
            while j < len(body):
                depth += (body[j] == '{') - (body[j] == '}')
                if depth == 0:
                    break
                j += 1
            value = body[i + 1:j]
        elif body[i] == '"':
            j = body.find('"', i + 1)
            value = body[i + 1:j if j > 0 else len(body)]
        else:
            value = re.match(r'[^,\n}]*', body[i:]).group()
        fields.setdefault(name, ' '.join(_detex(value).replace('{', '').replace('}', '').split()))
    return fields


def parse_entries(text):
    """Reference entries of a text: BibTeX entries, else the items of a numbered or bulleted list (at least two)."""
    text = str(text or '')
    entries = []
    for match in re.finditer(r'@\s*(\w+)\s*\{\s*([^,\s{}]+)\s*,', text):
        if match.group(1).lower() in ('comment', 'string', 'preamble'):
            continue
        depth, i = 1, match.end()
        while i < len(text) and depth:
            depth += (text[i] == '{') - (text[i] == '}')
            i += 1
        fields = _bib_fields(text[match.end():i - 1])
        authors = [a.strip() for a in re.split(r'\s+and\s+', fields.get('author', '')) if a.strip()]
        raw = ', '.join(v for v in (fields.get('author'), fields.get('title'), fields.get('journal') or fields.get('booktitle'),
                                    fields.get('year')) if v)
        entries.append({'key': match.group(2), 'raw': raw, 'title': fields.get('title', ''), 'authors': authors,
                        'year': fields.get('year', '')[:4], 'venue': fields.get('journal') or fields.get('booktitle') or '',
                        'identifiers': sorted(entry_identifiers(' '.join(
                            [('doi:' + fields['doi']) if fields.get('doi') else '',
                             ('arXiv:' + fields['eprint']) if fields.get('eprint') else '', fields.get('url', '')])))})
    if entries:
        return entries
    items, current = [], None
    for line in text.splitlines():
        head = re.match(r'\s*(?:\[\d+\]|\(\d+\)|\d+[.)]|[-*•])\s+(.*)', line)
        if head:
            current = [head.group(1)]
            items.append(current)
        elif current is not None and line.strip() and not re.match(r'\s*$', line):
            current.append(line.strip())
        else:
            current = None
    for parts in items:
        raw = ' '.join(' '.join(parts).split())
        year = re.search(r'(?<![\d.])(?:1[5-9]|20)\d\d(?![\d.])', raw)
        identifiers = sorted(i for i in entry_identifiers(raw) if i.startswith(('doi:', 'arxiv:')))
        if len(raw.split()) < 4 or not (year or identifiers):
            continue
        quoted = re.search(r'[“"\'‘*_]([^“”"\'‘’*_]{8,250})[”"\'’*_]', raw)
        lead = raw[:quoted.start()] if quoted else raw.split(',')[0]
        authors = [a for a in re.findall(r'[A-ZÀ-Ý][^\W\d_]+(?:-[A-ZÀ-Ý][^\W\d_]+)?', lead)
                   if a.lower() not in _STOP and len(a) > 2]
        entries.append({'key': '', 'raw': raw, 'title': quoted.group(1).strip() if quoted else '', 'authors': authors,
                        'year': year.group() if year else '', 'venue': '', 'identifiers': identifiers})
    return entries if len(entries) >= 2 else []


_PLACE = re.compile(r'\b(?:theorem|lemma|proposition|corollary|definition|remark|example|section|chapter|page|pp?\.|'
                    r'equation|eq\.|§|th[ée]or[èe]me|lemme|does|do|is|are|state|states|prove|proves|show|shows)\b|\?', re.I)


def is_reference_list(text):
    """Whether a text IS a reference list to check entry by entry, rather than a question that cites works.

    It needs at least two BibTeX entries or three list items that parse as references, and the text around them
    must be a short instruction that asks nothing about a place or a statement ("Check my references:"). A
    question about what one cited work states keeps the ordinary check, which reads the cited place."""
    text = str(text or '')
    entries = parse_entries(text)
    bibtex = bool(re.search(r'@\s*\w+\s*\{', text))
    if len(entries) < (2 if bibtex else 3):
        return False
    rest, depth, inside = [], 0, False
    for line in text.splitlines():
        if bibtex:
            if depth == 0 and re.match(r'\s*@\s*\w+\s*\{', line):
                inside = True
            if inside:
                depth += line.count('{') - line.count('}')
                if depth <= 0:
                    inside, depth = False, 0
                continue
        elif re.match(r'\s*(?:\[\d+\]|\(\d+\)|\d+[.)]|[-*•])\s+', line):
            inside = True
            continue
        elif inside and line.strip() and line[:1] in (' ', '\t'):
            continue  # an indented continuation of the item above
        else:
            inside = False
        rest.append(line)
    instruction = ' '.join(' '.join(rest).split())
    return len(instruction) <= 300 and not _PLACE.search(instruction)


def attached_material(workspace, files):
    """The text of the attached reference lists (.bib, else .txt/.md files whose items parse as references), for
    the bibliography check. Unreadable files are skipped: the check then works from the question."""
    parts = []
    for name in files or ():
        if not isinstance(name, str) or not name.lower().endswith(('.bib', '.txt', '.md')):
            continue
        try:
            text = workspace.text(workspace.path(name))
        except (OSError, ValueError, UnicodeError):
            continue
        if name.lower().endswith('.bib') or is_reference_list(text):
            parts.append(text)
    return '\n\n'.join(parts)


def _compare(entry, record):
    """(title overlap of the entry with the record, differences named)."""
    mine = _words(entry['title'] or entry['raw'])
    theirs = _words(record.get('title'))
    overlap = len(mine & theirs) / len(theirs) if theirs else 0
    if entry['title'] and theirs:
        overlap = min(overlap, len(mine & theirs) / max(1, len(mine)))
    differences = []
    year = str(record.get('year') or str(record.get('published') or '')[:4] or '')
    if entry['year'] and year and entry['year'] != year:
        differences.append(f'year: the registry gives {year}, the entry {entry["year"]}')
    given = {_surname(a) for a in entry['authors']} - {''}
    actual = {_surname(a) for a in record.get('authors') or []} - {''}
    if actual and given and entry['key']:  # a .bib lists every author: compare both ways
        missing, extra = sorted(actual - given), sorted(given - actual)
        if missing or extra:
            differences.append('authors: ' + '; '.join(
                part for part in (('the registry also lists ' + ', '.join(missing)) if missing else '',
                                  ('not in the registry: ' + ', '.join(extra)) if extra else '') if part))
    elif actual and not (actual & ({_surname(a) for a in entry['authors']} | _words(entry['raw']))):
        differences.append('authors: the registry lists ' + ', '.join(record.get('authors')))
    if entry['title'] and theirs and overlap < .95:
        differences.append(f'title: the registry has “{record.get("title")}”')
    return overlap, differences


def _best(entry, records):
    scored = []
    for record in records:
        if not isinstance(record, dict) or not record.get('title'):
            continue
        overlap, differences = _compare(entry, record)
        surnames = {_surname(a) for a in record.get('authors') or []}
        author_hit = bool(surnames & ({_surname(a) for a in entry['authors']} | _words(entry['raw'])))
        scored.append((overlap + .3 * author_hit, overlap, author_hit, record, differences))
    scored.sort(key=lambda item: -item[0])
    return scored[0] if scored else None


def check_entry(literature, entry):
    """One entry's verdict, the record it rests on, the differences and the registries asked."""
    result = {'entry': entry['raw'], 'key': entry['key'], 'verdict': 'not_found', 'record': None, 'differences': [],
              'notes': [], 'asked': []}
    failures = 0
    resolved = []
    for identifier in entry['identifiers']:
        value = json.loads(literature.execute('lookup_identifier', {'identifier': identifier}))
        result['asked'].append(identifier)
        if value.get('error'):
            failures += 1
            result['notes'].append(f'{identifier}: {value["error"][:120]}')
            continue
        if value.get('found') is False:
            result['notes'].append(f'{identifier}: {value.get("note") or "not found"}')
            resolved.append(('identifier_not_found', None, []))
            continue
        record = value.get('record') or {}
        if record.get('withdrawn'):
            result['notes'].append(f'{identifier}: withdrawn per arXiv ({record.get("comment") or "withdrawal notice"})')
        if record.get('journal_ref'):
            result['notes'].append(f'{identifier}: journal reference {record["journal_ref"]}')
        verdict, differences = 'matches', []
        if record.get('title'):
            overlap, differences = _compare(entry, record)
            if overlap < .5 and (entry['title'] or len(_words(entry['raw'])) >= 4):
                verdict, differences = 'identifier_mismatch', [f'{identifier} is “{record["title"]}”']
            elif differences:
                verdict = 'differs'
        resolved.append((verdict, record, differences))
    if resolved:
        priority = ['identifier_mismatch', 'identifier_not_found', 'differs', 'matches']
        verdict, record, _ = min(resolved, key=lambda row: priority.index(row[0]))
        result.update(verdict=verdict, record=record, differences=list(dict.fromkeys(d for _, _, ds in resolved for d in ds)))
        return result
    query = ' '.join(entry['raw'].split())[:300]
    for provider, search in (('crossref', query),
                             ('zbmath', ' '.join(filter(None, [('au:' + _surname(entry['authors'][0])) if entry['authors'] else '',
                                                              'ti:' + ' '.join(sorted(_words(entry['title'] or entry['raw']),
                                                                                      key=len, reverse=True)[:5])])))):
        value = json.loads(literature.execute('search_papers', {'query': search, 'provider': provider, 'limit': 5}))
        result['asked'].append(provider)
        if value.get('error'):
            failures += 1
            result['notes'].append(f'{provider}: {value["error"][:120]}')
            continue
        best = _best(entry, value.get('results') or [])
        if best and best[1] >= .7 and best[2]:
            _, _, _, record, differences = best
            result.update(verdict='differs' if differences else 'matches', record=record, differences=differences)
            return result
    if failures and failures >= len(result['asked']):
        result['verdict'] = 'inconclusive'
    return result


def _budget_left(literature):
    return literature.max_chars - literature.stats['returned_chars'] >= 100


def check_entries(literature, entries, *, stop=lambda: False):
    results = []
    for entry in entries:
        if stop() or not _budget_left(literature):
            results.append({'entry': entry['raw'], 'key': entry['key'], 'verdict': 'not_checked', 'record': None,
                            'differences': [], 'notes': [], 'asked': []})
            continue
        results.append(check_entry(literature, entry))
    return results


def compose(results, *, online=True):
    counts = {}
    for r in results:
        counts[r['verdict']] = counts.get(r['verdict'], 0) + 1
    out = [f'**Bibliography check: {len(results)} entries.** ' + '; '.join(
        f'{VERDICTS[k].lower()}: {v}' for k, v in counts.items()) + '.', '']
    if not online:
        out += ['*Web research is off: only registry answers already in the local cache were used.*', '']
    for n, r in enumerate(results, 1):
        label = f'`{r["key"]}` ' if r['key'] else ''
        out.append(f'{n}. {label}{r["entry"]}')
        out.append(f'   - **{VERDICTS[r["verdict"]]}**')
        record = r.get('record') or {}
        if record.get('title'):
            who = ', '.join(record.get('authors') or [])
            year = record.get('year') or str(record.get('published') or '')[:4]
            ident = record.get('identifier') or (('doi:' + record['doi']) if record.get('doi') else '')
            link = record.get('url') or ''
            out.append(f'   - Registry record: {who}, *{record["title"]}*' + (f' ({year})' if year else '')
                       + (f', {record.get("venue") or record.get("source")}' if record.get('venue') or record.get('source') else '')
                       + (f' — [{ident}]({link})' if link.startswith('https://') and ident else f' — {ident}' if ident else ''))
        for difference in r['differences']:
            out.append(f'   - Differs: {difference}')
        for note in r['notes']:
            out.append(f'   - {note}')
    out += ['', '*Each entry was compared with registry metadata only (Crossref, zbMATH Open, arXiv, the DOI registry); '
            'the papers were not read. "No matching record" means these registries returned none, not that the work '
            'cannot exist.*']
    return '\n'.join(out)
