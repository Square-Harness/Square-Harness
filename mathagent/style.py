# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Style profiles: condense an author's earlier articles into a reusable style guide.

The controller extracts what can be read exactly: macro definitions and how often the
bodies use them, the packages the articles load, theorem environments and section
skeletons. The model answers what needs judgement, per domain of articles: who the
papers are written for and how the author explains (`QUESTIONS`: definitions,
motivation, proof strategy, proof organisation, what is left out, the contribution,
tone), which names the author uses for objects with several names, their typesetting
habits, and what a macro is used for when its definition does not say it.

Each article is also compared with the write-up rules (`guideline_rules`: the paper,
abstract and numerics sheets of skills/writeup/guidelines, without the rules no style
sets aside). A rule the
article does not follow, deliberately, is a departure: the rule, the author's habit
instead and an example. A domain keeps the departures seen in a strict majority of its
articles; a write-up in that style sets those rules aside (writeup._set_aside), because
the author's style comes before the guidelines and the user's request before both. Some
rules no style sets aside: the fixed ones and the introduction's (writeup.style_may_set_aside).

Model output is checked against the articles: phrasings and names are kept only when
they occur in the text, typesetting habits and departures only when their example occurs
in the LaTeX source, and a macro's packages only when its files load them (or a known
package provides a command of its definition).

A profile is one JSON file:

    {"format": "square-style", "version": 2, "name", "created_at", "updated_at",
     "articles": [{"path", "sha256", "title", "domain"}],
     "packages": [{"name", "options", "articles": [path]}],
     "macros": [{"name", "definition", "uses", "articles": [path], "usage", "packages": [name]}],
     "environments": [{"name", "definition", "uses"}],
     "domains": [{"name", "description", "articles": [path], "audience", "aim", "structure": [str],
                  "definitions", "motivation", "proof_motivation", "proof_organisation",
                  "omissions", "contribution", "tone": [str],
                  "names": [{"term", "note"}], "typesetting": [{"habit", "example"}], "phrases": [str],
                  "departures": [{"rule", "sheet", "rule_text", "habit", "examples": [str], "articles", "of"}]}],
     "template": [{"name", "content"}]}   (optional: the LaTeX template write-ups in this style build on)

`rule_text` is the rule as it read when the style was learnt: a write-up sets a rule aside
only while its text is the same. Version 1 files, learnt before these questions, have
`habits` instead of the explanation fields, and no packages, macro notes or departures;
version 2 files learnt before departures have none. Both are read as they are.

A write-up reads one domain through `style_digest` and the macros through `annotated`.
"""
from collections import Counter
import json
from pathlib import Path
import re
import unicodedata

from .ledger import _atomic_write, _directory, _now
from .research import ResearchRunner
from .reporting import usage_footer
from .tools import unicode_source, source_suffix
from .worker_errors import WorkerInputError
from .model_limits import input_limit, is_ds4, output_limit, substantive_output
from . import images

FORMAT = 'square-style'
VERSION = 2
NAME = re.compile(r'[\w][\w .-]{0,59}')
LIMITS = {'aim': 400, 'audience': 400, 'item': 300, 'structure': 12, 'question': 5, 'names': 12, 'typesetting': 12,
          'phrases': 10, 'departures': 12, 'usage': 200, 'macros': 100, 'environments': 30, 'packages': 60}
REPORT_CHARS = 40_000  # the whole style, in the job's report
TEMPLATE_FILES = ('.tex', '.sty', '.cls', '.bib')

# What a style answers about how the author explains: key, heading, question.
QUESTIONS = (
    ('definitions', 'Definitions',
     'how and when definitions and notation are introduced: all up front in a preliminaries section, or just before '
     'their first use; in Definition environments or inline; with or without examples'),
    ('motivation', 'Motivation and the results put forward',
     'how the work is motivated and which results the introduction puts forward for this audience: applications, '
     'open problems, the place in a theory; main results stated informally or in full, and how early'),
    ('proof_motivation', 'Motivating the proof strategy',
     'how and when the strategy of a proof is motivated: an outline before the details, the key idea named, the '
     'difficulty and why a naive approach fails, a comparison with earlier methods'),
    ('proof_organisation', 'Organising proofs',
     'how proofs are organised: for example the broad strategy first, then the proofs of the key results from '
     'propositions and lemmas, with the lemma proofs gathered at the end; or every proof in its own section; steps '
     'and claims inside long proofs'),
    ('omissions', 'What is left out',
     'what is left out or taken as given: which steps are called obvious or standard, whether every claim not proved '
     'gets a reference, which results are used without one, whether a technique is pointed to its general framework'),
    ('contribution', 'Pointing to the contribution',
     'how the reader is pointed to the core of the contribution: contributions listed, novelty stated against '
     'earlier work, the key idea or estimate flagged, where the main theorem appears'),
    ('tone', 'Tone and voice',
     'tone and voice: person (we, I, impersonal), tense, register, how formal the explanations are, how the reader '
     'is addressed'),
)

_DEFINE = re.compile(r'\\(?:(?:re|provide)?newcommand\*?|DeclareMathOperator\*?|DeclarePairedDelimiter|def)(?![A-Za-z@])')
_THEOREM = re.compile(r'\\(?:newtheorem|declaretheorem)\*?(?![A-Za-z@])')
_HEADING = re.compile(r'\\(section|subsection)\*?\s*(?:\[[^\]]*\]\s*)?\{')
_ENVIRONMENT = re.compile(r'\\begin\{([A-Za-z*]+)\}')
_INPUT = re.compile(r'\\(?:input|include)\{([^}]+)\}')
_PACKAGE = re.compile(r'\\(?:usepackage|RequirePackage)\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}')
# Layout settings a class or a journal template redefines: they are not the author's notation.
_LAYOUT = re.compile(r'(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge'
                     r'|[a-z]+name|[a-z]+default|[a-z]*rule(?:width)?|baselinestretch|arraystretch|maketitle'
                     r'|the(?:footnote|page|part|chapter|section|subsection|subsubsection|paragraph|equation|figure|table|enum[iv]+)'
                     r'|label(?:enum|item)[iv]+)')
# Packages that provide a command a macro definition may use (beyond LaTeX and amsmath).
KNOWN_PACKAGES = {
    'mathds': 'dsfont', 'mathbbm': 'bbm', 'mathscr': 'mathrsfs', 'bm': 'bm', 'mathbb': 'amssymb',
    'mathfrak': 'amssymb', 'coloneqq': 'mathtools', 'eqqcolon': 'mathtools', 'DeclarePairedDelimiter': 'mathtools',
    'llbracket': 'stmaryrd', 'rrbracket': 'stmaryrd', 'textcolor': 'xcolor', 'SI': 'siunitx', 'upmu': 'upgreek',
}

SCAN_KEYS = ('title', 'abstract', 'headings', 'environments', 'inputs', 'lines')

DOMAINS_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['domains'],
    'properties': {'domains': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['name', 'description', 'articles'],
        'properties': {'name': {'type': 'string', 'maxLength': 60},
                       'description': {'type': 'string', 'maxLength': 300},
                       'articles': {'type': 'array', 'items': {'type': 'string', 'maxLength': 10}}}}}},
}


def _strings(count, length=LIMITS['item']):
    return {'type': 'array', 'maxItems': count, 'items': {'type': 'string', 'maxLength': length}}


def _records(count, fields):
    """An array of objects with these text fields, given as (name, maximum length, characters trimmed)."""
    return {'type': 'array', 'maxItems': count, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': [name for name, _, _ in fields],
        'properties': {name: {'type': 'string', 'maxLength': length} for name, length, _ in fields}}}


# A quoted term loses its quotes; a LaTeX example only spaces and backticks (a final ' can be a prime).
NAME_FIELDS = (('term', 120, ' "\'“”`'), ('note', LIMITS['item'], ' '))
TYPESETTING_FIELDS = (('habit', LIMITS['item'], ' '), ('example', 200, ' `'))

STYLE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['audience', 'aim', 'structure', *(key for key, _, _ in QUESTIONS), 'names', 'typesetting', 'phrases'],
    'properties': {
        'audience': {'type': 'string', 'maxLength': LIMITS['audience']},
        'aim': {'type': 'string', 'maxLength': LIMITS['aim']},
        'structure': _strings(LIMITS['structure']),
        **{key: _strings(LIMITS['question']) for key, _, _ in QUESTIONS},
        'names': _records(LIMITS['names'], NAME_FIELDS),
        'typesetting': _records(LIMITS['typesetting'], TYPESETTING_FIELDS),
        'phrases': _strings(LIMITS['phrases']),
    },
}

# Merging a domain's articles is about the prose answers only: the model never quotes again, so a verified quotation
# cannot be lost or retyped (the controller merges names, typesetting and phrasings: StyleRunner._quotations).
PROSE_FIELDS = ('audience', 'aim', 'structure', *(key for key, _, _ in QUESTIONS))
CONDENSE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': list(PROSE_FIELDS),
                   'properties': {key: STYLE_SCHEMA['properties'][key] for key in PROSE_FIELDS}}

# One article's description also says which write-up rules it does not follow; a domain's merged style does not
# (its departures are counted over its articles by the controller).
DEPARTURE_FIELDS = (('rule', 10, ' '), ('habit', LIMITS['item'], ' '), ('example', 300, ' `'))
OBSERVE_SCHEMA = {**STYLE_SCHEMA, 'required': [*STYLE_SCHEMA['required'], 'departures'],
                  'properties': {**STYLE_SCHEMA['properties'], 'departures': _records(LIMITS['departures'], DEPARTURE_FIELDS)}}

MACROS_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['macros'],
    'properties': {'macros': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['name', 'usage', 'packages'],
        'properties': {'name': {'type': 'string', 'maxLength': 60}, 'usage': {'type': 'string', 'maxLength': LIMITS['usage']},
                       'packages': _strings(4, 40)}}}},
}


# -- exact extraction ------------------------------------------------------------------

def strip_comments(tex):
    """Drop LaTeX comments; an escaped \\% stays."""
    return '\n'.join(re.sub(r'(?<!\\)%.*', '', line) for line in tex.splitlines())


def _group(text, start):
    """The balanced {...} group opening at text[start], as (inner text, index after it)."""
    if start >= len(text) or text[start] != '{':
        return None, start
    depth = 0
    for index in range(start, min(len(text), start + 4000)):
        escaped = index > start and text[index - 1] == '\\'
        if text[index] == '{' and not escaped:
            depth += 1
        elif text[index] == '}' and not escaped:
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return None, start


def _skip(text, index):
    while index < len(text) and text[index] in ' \t\n':
        index += 1
    return index


def _arguments(text, index, groups, trailing=False):
    """Index after `groups` {..} arguments, with [..] options and #1 parameters between them."""
    seen = 0
    while True:
        index = _skip(text, index)
        if index < len(text) and text[index] == '[' and (seen < groups or trailing):
            close = text.find(']', index)
            if close == -1:
                return None
            index = close + 1
        elif index < len(text) and text[index] == '#' and seen < groups:
            index += 2
        elif index < len(text) and text[index] == '{' and seen < groups:
            _, after = _group(text, index)
            if after == index:
                return None
            index, seen = after, seen + 1
        else:
            return index if seen == groups else None


def macro_definitions(tex):
    """Macro names with the exact text that defines them, in order of appearance."""
    text, found = strip_comments(tex), {}
    for match in _DEFINE.finditer(text):
        index = _skip(text, match.end())
        if text.startswith('{', index):
            inner, index = _group(text, index)
            name = re.fullmatch(r'\s*\\([A-Za-z]+)\s*', inner or '')
        else:
            name = re.match(r'\\([A-Za-z]+)', text[index:])
            index += name.end() if name else 0
        end = _arguments(text, index, 2 if 'PairedDelimiter' in match.group(0) else 1) if name else None
        if name and end and name.group(1) not in found:
            found[name.group(1)] = ' '.join(text[match.start():end].split())[:LIMITS['item'] + 100]
    return found


def environment_definitions(tex):
    """Theorem-like environments: \\newtheorem{name}[counter]{Title}[within]."""
    text, found = strip_comments(tex), {}
    for match in _THEOREM.finditer(text):
        inner, _ = _group(text, _skip(text, match.end()))
        end = _arguments(text, match.end(), 2, trailing=True)
        name = (inner or '').strip()
        if re.fullmatch(r'[A-Za-z*]+', name) and end and name not in found:
            found[name] = ' '.join(text[match.start():end].split())
    return found


def packages(tex):
    """The packages a file loads in its preamble, with their options: name -> options."""
    preamble, found = strip_comments(tex).split('\\begin{document}', 1)[0], {}
    for match in _PACKAGE.finditer(preamble):
        for name in match.group(2).split(','):
            name = name.strip()
            if re.fullmatch(r'[A-Za-z0-9-]+', name):
                found.setdefault(name, ' '.join((match.group(1) or '').split()))
    return found


def macro_packages(definition):
    """Packages known to provide a command the definition uses."""
    return {KNOWN_PACKAGES[name] for name in re.findall(r'\\([A-Za-z]+)', definition) if name in KNOWN_PACKAGES}


def document_body(tex):
    text = strip_comments(tex)
    return text.split('\\begin{document}', 1)[1] if '\\begin{document}' in text else text


def written_text(tex):
    """The text where commands are used rather than defined: the document body, or for a file without
    one (a fragment, a .sty) the file without its definition and package lines."""
    if '\\begin{document}' in strip_comments(tex):
        return document_body(tex)
    return '\n'.join(line for line in strip_comments(tex).splitlines()
                     if not (_DEFINE.search(line) or _THEOREM.search(line) or _PACKAGE.search(line)))


def uses(body, name):
    return len(re.findall(r'\\' + re.escape(name) + r'(?![A-Za-z@])', body))


def _referenced(definition):
    """The command names a definition contains, its own included."""
    return set(re.findall(r'\\([A-Za-z]+)', definition))


def needed_macros(macros, text):
    """The macros `text` uses, with the macros their definitions use in turn, in the order given."""
    by_name = {m['name']: m for m in macros}
    pending = [name for name in by_name if uses(text, name)]
    needed = set(pending)
    while pending:
        for name in _referenced(by_name[pending.pop()]['definition']) & by_name.keys() - needed:
            needed.add(name)
            pending.append(name)
    return [m for m in macros if m['name'] in needed]


def contexts(body, name, count=3, width=70):
    """Up to `count` passages of the body around uses of \\name, spread over the text, spaces normalised."""
    starts = [m.start() for m in re.finditer(r'\\' + re.escape(name) + r'(?![A-Za-z@])', body)]
    picked = sorted({starts[i * (len(starts) - 1) // max(1, count - 1)] for i in range(count)}) if starts else []
    return [' '.join(body[max(0, s - width):s + len(name) + 1 + width].split()) for s in picked]


def scan(tex):
    """The skeleton of one article: title, abstract, headings and environment counts."""
    text, body = strip_comments(tex), document_body(tex)
    title, at = None, text.find('\\title')
    if at != -1:
        index = _skip(text, at + len('\\title'))
        if text.startswith('[', index):  # \title[short]{long}
            index = _skip(text, text.find(']', index) + 1)
        title, _ = _group(text, index)
    abstract = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', body, re.S)
    headings = []
    for match in _HEADING.finditer(body):
        inner, _ = _group(body, match.end() - 1)
        if inner is not None:
            headings.append(('  ' if match.group(1) == 'subsection' else '') + ' '.join(inner.split())[:120])
    counts = {}
    for name in _ENVIRONMENT.findall(body):
        counts[name] = counts.get(name, 0) + 1
    return {'title': ' '.join((title or '').split())[:300],
            'abstract': ' '.join(abstract.group(1).split())[:1500] if abstract else '',
            'headings': headings[:60], 'environments': dict(sorted(counts.items(), key=lambda kv: -kv[1])[:20]),
            'inputs': _INPUT.findall(body)[:20], 'lines': len(tex.splitlines())}


def excerpts(tex, room, *, pdf=False):
    """Return the full body when it fits, otherwise sample its TeX sections or the span of a PDF."""
    body = tex if pdf else document_body(tex).split('\\end{document}', 1)[0]
    body = re.sub(r'\n{3,}', '\n\n', body.strip())
    if len(body) <= room:
        return '--- Full text ---\n' + body, True
    if pdf:
        # The extraction has no TeX section boundaries. Sample across the paper.
        width = room // 3
        middle = (len(body) - width) // 2
        return '\n\n[…]\n\n'.join((body[:width], body[middle:middle + width], body[-width:])), False
    starts = [m.start() for m in re.finditer(r'\\section\*?\s*(?:\[[^\]]*\]\s*)?\{', body)]
    parts = []
    if starts:
        opening = max(600, room // (3 * len(starts)))
        intro_end = starts[1] if len(starts) > 1 else len(body)
        parts.append(('Introduction (opening)', body[starts[0]:min(intro_end, starts[0] + room // 3)]))
        for start in starts[1:]:
            parts.append(('Section opening', body[start:start + opening]))
        if len(starts) > 1:
            parts.append(('Last section (opening)', body[starts[-1]:starts[-1] + room // 8]))
    else:
        parts.append(('Opening', body[:room // 2]))
    proof = next((m.group(0) for m in re.finditer(r'\\begin\{proof\}.*?\\end\{proof\}', body, re.S) if len(m.group(0)) > 300), None)
    if proof:
        parts.insert(1, ('A proof', proof[:room // 5]))
    shown, used = [], 0
    for label, text in parts:
        text = re.sub(r'\n{3,}', '\n\n', text.strip())
        if not text or used + len(text) > room:
            continue
        shown.append(f'--- {label} ---\n{text}')
        used += len(text)
    return '\n\n'.join(shown), False


# -- checking the model's description against the articles -------------------------------

def _flat(text):
    return ' '.join(text.split())


def _letters(text):
    """The letters and digits of `text`, lowercased and without accents, with the index in `text` of each."""
    letters, where = [], []
    for index, char in enumerate(text):
        for part in unicodedata.normalize('NFKD', char):
            if part.isalnum():
                folded = part.casefold()
                letters.append(folded)
                where.extend([index] * len(folded))
    return ''.join(letters), where


class Quoted:
    """Article text for checking quotations, with spaces collapsed and LaTeX comments removed from TeX."""

    def __init__(self, text, *, latex=True):
        self.flat = _flat(strip_comments(text) if latex else text)
        self.letters, self.where = _letters(self.flat)

    def find(self, quote, least):
        """The article's own text for a quotation, or None. A quotation found as it is (spaces aside) is kept as
        given. Otherwise: models drop backslashes and spacing commands when they quote LaTeX inside JSON ("intX
        varphi, dnuw" for "\\intX \\varphi \\, d\\nuw"), so a quotation whose letters and digits, at least `least` of
        them, occur in that order in the article gets the article's text for that span, with the marks around it
        (a command's backslash, an opening brace, a closing comma). A span much longer than the quotation is refused."""
        quote = _flat(quote)
        if quote and quote in self.flat:
            return quote
        letters, _ = _letters(quote)
        at = self.letters.find(letters) if len(letters) >= least else -1
        if at < 0:
            return None
        start, end = self.where[at], self.where[at + len(letters) - 1] + 1
        mark = lambda char: not char.isalnum() and not char.isspace()
        if start and self.flat[start - 1] == '\\':
            start -= 1
        for _ in range(len(re.match(r'[^\w\s\\]*', quote).group(0))):
            if start and mark(self.flat[start - 1]):
                start -= 1
                if start and self.flat[start - 1] == '\\':  # an escaped mark, such as \| or \{
                    start -= 1
        for _ in range(len(re.search(r'[^\w\s]*$', quote).group(0))):
            if end < len(self.flat) and mark(self.flat[end]):
                end += 1
        return self.flat[start:end] if end - start <= 3 * len(quote) + 20 else None


def _unique(items, key):
    seen, out = set(), []
    for item in items:
        if item[key] not in seen:
            seen.add(item[key])
            out.append(item)
    return out


def clip(text, length, mark=True):
    """`text` cut to at most `length` characters at the last sentence end, else clause end, else space that lies
    outside mathematics and outside a LaTeX group or command: never inside $...$, \\(...\\), braces or a command
    name. A cut that is not a sentence end ends with '…' when `mark` (not for a verbatim example, which must stay a
    passage of the source)."""
    if len(text) <= length:
        return text
    room = length - (1 if mark else 0)
    sentence = clause = space = 0
    depth, math, i = 0, '', 0
    while i < len(text) and i < room:
        char = text[i]
        if char == '\\' and i + 1 < len(text):
            pair = text[i:i + 2]
            if pair in ('\\(', '\\[') and not math:
                math = ')' if pair == '\\(' else ']'
            elif math and pair == '\\' + math:
                math = ''
            i += 2
            while i < len(text) and text[i].isalpha():
                i += 1  # a command name is never cut
            continue
        if char == '$' and math in ('', '$'):
            math = '' if math else '$'
        elif char == '{':
            depth += 1
        elif char == '}':
            depth = max(0, depth - 1)
        elif not math and not depth and i + 1 <= room:
            following = text[i + 1:i + 2]
            if char in '.!?' and following in (' ', '') and not re.search(r'\b(?:e\.g|i\.e|cf|resp|etc|p|pp|Thm|Lem|Prop|Eq)$', text[:i]):
                sentence = i + 1
            elif char in ';:,)' and following == ' ':
                clause = i + 1
            elif char == ' ':
                space = i
        i += 1
    if sentence >= length * 0.4:
        return text[:sentence]
    cut = clause if clause >= length * 0.4 else space or clause or sentence
    if not cut:
        return ''  # one unbroken formula or command: nothing that can be cut cleanly
    return text[:cut].rstrip(' ,;:') + ('…' if mark else '')


def _text(value, length):
    return clip(_flat(str(value or '')), length)


def _items(values, count):
    return [clip(_flat(str(v)), LIMITS['item']) for v in values or [] if isinstance(v, str) and v.strip()][:count]


def _records_of(values, fields, count):
    """The model's objects whose text fields are all given, trimmed and bounded as `fields` says."""
    out = []
    for value in values or []:
        if isinstance(value, dict) and all(isinstance(value.get(name), str) for name, _, _ in fields):
            item = {name: clip(_flat(value[name].strip(trim)), length, mark=name not in ('example', 'term'))
                    for name, length, trim in fields}
            if all(item.values()):
                out.append(item)
    return out[:count]


def blank_style():
    return {'audience': '', 'aim': '', 'structure': [], **{key: [] for key, _, _ in QUESTIONS},
            'names': [], 'typesetting': [], 'phrases': [], 'departures': []}


def guideline_rules():
    """The write-up rules a paper is held to that a style may set aside, [{id, type, text, sheet}]: the paper,
    abstract and numerics sheets at the size of a whole paper, without the rules write-ups always follow
    (writeup.style_may_set_aside). The introduction's rules only a request sets aside. A learnt style is compared
    with these."""
    from .writeup import load_guidelines, style_may_set_aside  # writeup imports this module
    return [r for r in load_guidelines('paper_part', ('abstract',), 'paper', numerics=True) if style_may_set_aside(r)]


def settable(departures):
    """The departures a style may act on (writeup.style_may_set_aside). A style learnt before the rules were split
    this way may hold others: they are left out."""
    from .writeup import all_rules, style_may_set_aside
    rules = all_rules()
    return [d for d in departures or [] if d.get('rule') in rules and style_may_set_aside(rules[d['rule']])]


def clean_style(value, sources, typeset=None, rules=None):
    """Bound a model's style description and check its quotations against the articles (see Quoted.find).

    Phrasings and names are kept only when they occur in `sources`; a typesetting habit only when its
    example occurs in `typeset`, the LaTeX the author wrote (by default the sources; none for
    screenshots or extracted PDFs). With `rules` (guideline rules), a departure is kept only for one of
    those rules and when its example occurs in `typeset`. Examples and phrasings are kept as the article
    writes them. Returns the style and the number of quotations not found.
    """
    value = value if isinstance(value, dict) else {}
    texts = [Quoted(t, latex=typeset is None or t in typeset) for t in sources]
    latex = texts if typeset is None else [Quoted(t) for t in typeset]

    def found(quote, where, least):
        return next((text for text in (q.find(quote, least) for q in where) if text), None)

    style = {'audience': _text(value.get('audience'), LIMITS['audience']), 'aim': _text(value.get('aim'), LIMITS['aim']),
             'structure': _items(value.get('structure'), LIMITS['structure']),
             **{key: _items(value.get(key), LIMITS['question']) for key, _, _ in QUESTIONS}}
    names = _records_of(value.get('names'), NAME_FIELDS, LIMITS['names'] * 2)
    found_names = [n for n in names if found(n['term'], texts, 5)]
    examples = _records_of(value.get('typesetting'), TYPESETTING_FIELDS, LIMITS['typesetting'] * 2) if latex else []
    found_examples = []
    for item in examples:
        text = found(item['example'], latex, 8)
        if text and len(text) >= 3:
            found_examples.append({**item, 'example': text[:TYPESETTING_FIELDS[1][1]]})
    phrases = [p.strip(' "\'“”') for p in _items(value.get('phrases'), LIMITS['phrases'] * 2)]
    found_phrases = []
    for phrase in phrases:
        text = found(phrase, texts, 10) if len(phrase) >= 8 else None
        if text:
            found_phrases.append(text[:LIMITS['item']])
    known = {r['id'] for r in rules or []}
    departures = []  # one per rule, the first given
    for item in _records_of(value.get('departures'), DEPARTURE_FIELDS, LIMITS['departures'] * 2) if known and latex else []:
        if not any(item['rule'] == other['rule'] for other in departures):
            departures.append(item)
    found_departures = []
    for item in departures:
        text = found(item['example'], latex, 8) if item['rule'] in known else None
        if text and len(text) >= 10:
            found_departures.append({**item, 'example': text[:DEPARTURE_FIELDS[2][1]]})
    style['names'] = found_names[:LIMITS['names']]
    style['typesetting'] = _unique(found_examples, 'example')[:LIMITS['typesetting']]
    style['phrases'] = list(dict.fromkeys(found_phrases))[:LIMITS['phrases']]
    style['departures'] = found_departures[:LIMITS['departures']]
    dropped = (len(names) + len(examples) + len(phrases) + len(departures)
               - len(found_names) - len(found_examples) - len(found_phrases) - len(found_departures))
    return style, dropped


# -- the saved file ------------------------------------------------------------------------

def styles_folder(root):
    return Path(root) / '.mathagent' / 'styles'


def style_path(root, name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('Style names use letters, digits, spaces, dots and dashes (at most 60)')
    return styles_folder(root) / (name + '.json')


def read_template(workspace, paths):
    """Workspace files to keep as a style's LaTeX template: a preamble .tex and/or .sty, .cls, .bib files."""
    files, problems = [], []
    for name in dict.fromkeys(str(p) for p in paths):
        path = workspace.path(name)
        if path.suffix.lower() not in TEMPLATE_FILES:
            raise ValueError('A template is made of .tex, .sty, .cls or .bib files')
        try:
            content = unicode_source(workspace.text(path), workspace.encodings.get(path))  # saved as Unicode
        except (OSError, ValueError) as exc:
            problems.append(_unreadable(name, exc))  # a partial template could break write-ups: report them all
            continue
        if len(content) > 500_000:
            raise ValueError('Each template file must be text of at most 500 KB')
        if any(f['name'] == path.name for f in files):
            raise ValueError('Two template files have the same name: ' + path.name)
        files.append({'name': path.name, 'content': content})
    if problems:
        raise ValueError('These template files could not be read: ' + '; '.join(problems) +
                         '. Remove them from the template or save them as text.')
    if len(files) > 8:
        raise ValueError('A template has at most 8 files')
    return files


def _unreadable(name, exc):
    """Why a chosen file could not be read, naming it."""
    message = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    return message if name in message else f'{name}: {message}'


def set_template(root, name, files):
    """Give a saved style a template (or none, with no files); the rest of the style is unchanged."""
    profile = load_profile(root, name)
    if files:
        profile['template'] = files
    else:
        profile.pop('template', None)
    profile['updated_at'] = _now()
    save_profile(root, profile)
    return public_profile(profile)


_TEXT_FIELDS = {'audience': LIMITS['audience'], 'aim': LIMITS['aim'], 'description': 300}
_LIST_FIELDS = {'structure': LIMITS['structure'], 'phrases': LIMITS['phrases'],
                **{key: LIMITS['question'] for key, _, _ in QUESTIONS}}


def _edited_text(value, field, limit):
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError(f'{field} must be text')
    text = _flat(value)
    if len(text) > limit:
        raise ValueError(f'{field} is limited to {limit} characters')
    return text


def edit_profile(root, name, items):
    """The author's corrections of a saved style: `items` replaces the fields it names in one domain (`domain`, which
    may be left out when the style has one): audience, aim, description (text); structure, phrases and each question's
    list (lists of text); names ([{term, note}]) and typesetting ([{habit, example}]). `departures` and `macros` list
    the rule ids and macro names to keep: an author removes what does not sound like them, and adds nothing the
    articles did not show there. Returns the saved style as the interface lists it."""
    if not isinstance(items, dict) or not items:
        raise ValueError('Give the parts of the style to change')
    known = {'domain', 'names', 'typesetting', 'departures', 'macros', *_TEXT_FIELDS, *_LIST_FIELDS}
    unknown = sorted(set(items) - known)
    if unknown:
        raise ValueError('A style has no part named ' + ', '.join(unknown))
    profile = load_profile(root, name)
    domain = _domain(profile, items.get('domain'))
    if domain is None:
        raise ValueError('Name the kind of paper (domain) to change: ' + ', '.join(d['name'] for d in profile['domains']))
    for field, limit in _TEXT_FIELDS.items():
        if field in items:
            domain[field] = _edited_text(items[field], field, limit)
    for field, count in _LIST_FIELDS.items():
        if field in items:
            values = items[field]
            if not isinstance(values, list) or len(values) > count:
                raise ValueError(f'{field} is a list of at most {count} items')
            domain[field] = [text for text in (_edited_text(v, field, LIMITS['item']) for v in values) if text]
    for field, fields, count in (('names', NAME_FIELDS, LIMITS['names']), ('typesetting', TYPESETTING_FIELDS, LIMITS['typesetting'])):
        if field in items:
            values = items[field]
            if not isinstance(values, list) or len(values) > count or not all(isinstance(v, dict) for v in values):
                raise ValueError(f'{field} is a list of at most {count} entries')
            edited = []
            for value in values:
                entry = {key: _edited_text(value.get(key, ''), f'{field} {key}', length) for key, length, _ in fields}
                if all(entry.values()):
                    edited.append(entry)
            domain[field] = edited
    if 'departures' in items:
        keep = items['departures']
        if not isinstance(keep, list) or not all(isinstance(v, str) for v in keep):
            raise ValueError('departures lists the rules to keep')
        domain['departures'] = [d for d in domain.get('departures') or [] if d.get('rule') in keep]
    if 'macros' in items:
        keep = items['macros']
        if not isinstance(keep, list) or not all(isinstance(v, str) for v in keep):
            raise ValueError('macros lists the macro names to keep')
        profile['macros'] = [m for m in profile['macros'] if m['name'] in keep]
    profile['updated_at'] = _now()
    profile['edited'] = True
    save_profile(root, profile)
    return public_profile(profile)


def rename_profile(root, name, new):
    """Give a saved style another name; the style write-ups follow is renamed with it."""
    new = new.strip() if isinstance(new, str) else new
    target = style_path(root, new)  # refuses a bad name
    profile = load_profile(root, name)
    if new == profile['name']:
        return public_profile(profile)
    if target.exists() or target.is_symlink():
        raise ValueError(f'A style named "{new}" is already saved: choose another name, or delete that one first')
    old = style_path(root, name)
    profile.update(name=new, updated_at=_now())
    save_profile(root, profile)
    old.unlink(missing_ok=True)
    choice = load_choice(root)
    if choice.get('style') == name:
        _atomic_write(choice_path(root), json.dumps({**choice, 'style': new}, ensure_ascii=False).encode('utf-8'))
    return public_profile(profile)


def delete_profile(root, name):
    """Delete a saved style. Write-ups already made keep the copy they used; when it was the style write-ups follow,
    they choose automatically again."""
    path = style_path(root, name)
    load_profile(root, name)  # an unknown or damaged name fails here
    path.unlink()
    if load_choice(root).get('style') == name:
        _atomic_write(choice_path(root), json.dumps({'mode': 'auto'}).encode('utf-8'))


def public_profile(profile):
    """A style as the interface lists it: its template's file names and sizes, not their contents."""
    value = dict(profile, domains=[dict(d, departures=settable(d.get('departures'))) for d in profile['domains']])
    if profile.get('template'):
        value['template'] = [{'name': f['name'], 'size': len(f['content'])} for f in profile['template']]
    return value


def valid_profile(value):
    return (isinstance(value, dict) and value.get('format') == FORMAT and value.get('version') in (1, VERSION)
            and isinstance(value.get('name'), str) and isinstance(value.get('domains'), list)
            and isinstance(value.get('macros'), list) and isinstance(value.get('articles'), list))


def save_profile(root, profile):
    path = style_path(root, profile['name'])
    _directory(Path(root) / '.mathagent', create=True)
    _directory(path.parent, create=True)
    _atomic_write(path, json.dumps(profile, ensure_ascii=False, indent=1).encode('utf-8'))
    return path


def load_profile(root, name):
    path = style_path(root, name)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError('Unknown style: ' + name)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not valid_profile(value):
        raise ValueError('Not a Square Harness style file: ' + path.name)
    return value


def list_profiles(root):
    folder = styles_folder(root)
    if not folder.is_dir() or folder.is_symlink():
        return []
    found = []
    for path in sorted(folder.glob('*.json')):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if valid_profile(value) and not path.is_symlink() and value['name'] == path.stem:
            found.append(value)
    return found


def _domain(profile, domain):
    if domain is None and len(profile['domains']) == 1:
        return profile['domains'][0]
    return next((d for d in profile['domains'] if d['name'].lower() == str(domain or '').lower()), None)


# -- the style as text ---------------------------------------------------------------------

def annotated(macro):
    """A macro's definition with what the author uses it for, as a LaTeX comment."""
    return macro['definition'] + (f'  % {macro["usage"]}' if macro.get('usage') else '')


def _guide(style):
    """How the author explains, as (heading, lines) blocks in the order a writer needs them."""
    blocks = [('', ['Written for: ' + style['audience']] if style.get('audience') else [])]
    blocks += [(label + ':', ['- ' + item for item in style.get(key) or []]) for key, label, _ in QUESTIONS]
    blocks.append(('How the author explains:', ['- ' + item for item in style.get('habits') or []]))  # version 1 styles
    blocks.append(('Names the author uses for things that have several names:',
                   [f'- {n["term"]}: {n["note"]}' for n in style.get('names') or []]))
    blocks.append(('Typesetting habits to reproduce:',
                   [f'- {t["habit"]} Example: {t["example"]}' for t in style.get('typesetting') or []]))
    blocks.append(('Phrasings the author uses, where they fit:', ['- "' + p + '"' for p in style.get('phrases') or []]))
    return blocks


def departure_line(departure):
    """One departure from the write-up rules, as the report and the write-up's priorities say it."""
    seen = (f'in {departure["articles"]} of {departure["of"]} articles; ' if departure.get('of', 1) > 1 else '')
    example = (departure.get('examples') or [''])[0]
    return (f'- {departure["rule"]}: {departure["habit"]}'
            + (f' ({seen}as in "{example}")' if example else f' ({seen.rstrip("; ")})' if seen else ''))


def _fitted(blocks, limit):
    """The blocks within `limit` characters: whole lines only, a heading only above a line it heads."""
    out, size = [], 0
    for heading, lines in blocks:
        taken = []
        for line in lines:
            extra = len(line) + 1 + (len(heading) + 1 if heading and not taken else 0)
            if size + extra > limit:
                break
            taken.append(line)
            size += extra
        if taken:
            out += ([heading] if heading else []) + taken
    return '\n'.join(out)


def style_digest(profile, domain, limit=1500):
    """One domain style as guidelines for writing a document: who it is for and how the author explains,
    names, typesetting and phrasings, the most useful first.

    The request sets a document's structure, so the papers' usual order of sections is left out (how
    proofs are organised stays); macros are given separately, after the user's own notation has been
    removed from them.
    """
    chosen = _domain(profile, domain)
    if chosen is None:
        return ''
    head = (f'AUTHOR STYLE "{profile["name"]}", domain "{chosen["name"]}"'
            + (f': {chosen["description"]}' if chosen.get('description') else '') +
            '. Follow these habits of the author wherever the request and the material leave the choice open.')
    return _fitted([('', [head])] + _guide(chosen), limit)


def style_prompt(profile, domain=None, limit=REPORT_CHARS):
    """The whole style as text, for the job's report: each domain, then the macros with their notes."""
    chosen = _domain(profile, domain)
    domains = [chosen] if chosen else profile['domains']
    source_label = ('supplied text sources; archive members may be article fragments'
                    if any(a.get('source_archive') for a in profile['articles']) else "of the author's articles")
    blocks = [('', [f'AUTHOR STYLE "{profile["name"]}" (condensed from {len(profile["articles"])} {source_label}).'])]
    for item in domains:
        blocks.append(('', ['', f'Domain: {item["name"]}. {item.get("description", "")}'.rstrip()]))
        blocks.append(('', ['Aim of these papers: ' + item['aim']] if item.get('aim') else []))
        blocks.append(('Usual order of a paper:', [f'{i}. {s}' for i, s in enumerate(item.get('structure') or [], 1)]))
        blocks += _guide(item)
        blocks.append(('Write-up rules this style sets aside (the author departs from them):',
                       [departure_line(d) for d in settable(item.get('departures'))]))
    articles = set(chosen['articles']) if chosen else None
    macros = [m for m in profile['macros'] if articles is None or articles & set(m['articles'])]
    blocks.append(('\nMacros (use them instead of their expansions):',
                   [annotated(m) + (f'  [needs {", ".join(m["packages"])}]' if m.get('packages') else '') for m in macros]))
    return _fitted(blocks, limit)


# -- the job -------------------------------------------------------------------------------

class StyleRunner(ResearchRunner):
    """Builds a style profile from pinned articles; saves it under <styles_root>/.mathagent/styles."""
    KINDS = ('style',)
    TERMINAL = ('saved', 'partial', 'budget_exhausted', 'budget_violation')

    def __init__(self, agent, emit=lambda kind, value: None, *, styles_root=None):
        super().__init__(agent, emit)
        self.styles_root = Path(styles_root or agent.workspace.root)

    def start(self, goal, *, name='', source_files=(), template_files=(), **budgets):
        style_path(self.styles_root, name)  # validates the name
        self._template = read_template(self.agent.workspace, template_files)
        names = images.capture(self.agent, source_files)
        if not names and not getattr(self.agent, 'image_refs', None):
            raise ValueError('Choose a PDF, TeX article, TeX source archive or screenshot to learn the writing style from')
        if any(source_suffix(n) not in ('.tex', '.pdf') for n in names):
            raise ValueError('Writing styles accept PDFs with selectable text, TeX articles, TeX source archives and screenshots')
        self._name = name
        return super().start(goal.strip() or f'Condense my writing style "{name}" from these articles.', kind='style',
                             source_files=names, **budgets)

    def _inferred_sources(self, goal):
        return []  # Only explicit examples are used, including in screenshot-only jobs.

    def _pin(self, names, prefix):
        # Pin each article once so one unreadable input does not discard the rest.
        sources, self._skipped = [], []
        for article in dict.fromkeys(names):
            try:
                self.agent.workspace.path(article, pdf=True)  # Require a workspace-relative path.
                pinned = super()._pin([article], prefix)
            except (OSError, ValueError) as exc:
                problem = _unreadable(article, exc)
                if source_suffix(article) == '.pdf':
                    problem += '. Use a PDF with selectable text and no password, the TeX source, or screenshots'
                self._skipped.append(problem)
                continue
            for source in pinned:
                source['id'] = f'{prefix}{len(sources) + 1}'
                sources.append(source)
        if names and not sources and not getattr(self.agent, 'image_refs', None):
            raise ValueError('None of the chosen articles could be read: ' + '; '.join(self._skipped))
        return sources

    def _extra_state(self):
        return {'phase': 'scan', 'style_name': self._name, 'articles': [], 'domains': [],
                'observations': {}, 'macro_notes': None, 'profile': None, 'saved_to': None, 'template': self._template,
                'skipped': self._skipped}

    def _material(self, role):
        # Each call carries exactly what it needs; the generic research index adds nothing here.
        return ('Budget remaining (null means no cumulative cap): ' + json.dumps(
            {k: None if self.state['settings'][k] is None else max(0, self.state['settings'][k] - self.state[c])
             for k, c in [('max_tokens', 'tokens_charged'), ('max_input_tokens', 'input_tokens_charged')]}), [])

    def _room(self, cap):
        """Characters of article text that fit beside the instructions (text runs at 3+ characters a token)."""
        if is_ds4(self.agent):
            cap = min(cap, getattr(self.agent, 'document_output_cap', None) or cap, self.agent.ctx // 2)
        return max(2000, min(input_limit(self.agent, 'style_packet', 24000), (self.agent.ctx - cap - 3000) * 2))

    def _article(self, source_id):
        return next(s for s in self.state['sources'] if s['id'] == source_id)

    def _typeset_sources(self):
        """Only the supplied source code can establish exact LaTeX conventions."""
        return [s for s in self.state['sources'] if not s.get('derived_from_images') and not s['path'].lower().endswith('.pdf')]

    def _digest(self, article):
        if article.get('source_kind') == 'pdf_text':
            opening = _flat(self._article(article['id'])['content'][:2000])[:1500]
            return f'[{article["id"]}] {article["path"]}\nOpening of extracted PDF text: {opening}'
        heads = '\n'.join(article['headings'][:30]) or '(no \\section headings)'
        envs = ', '.join(f'{k} ×{v}' for k, v in article['environments'].items()) or 'none'
        return (f'[{article["id"]}] {article["path"]}\nTitle: {article["title"] or "(none)"}\n'
                f'Abstract: {article["abstract"] or "(none)"}\nSections:\n{heads}\nEnvironments: {envs}' +
                ('\nArchive member: this may be a source fragment, not a complete independent article.' if article.get('source_archive') else ''))

    def _step(self, phase):
        if phase == 'scan':
            self._scan()
        elif phase == 'classify':
            self._classify()
        elif phase == 'observe':
            self._observe()
        elif phase == 'condense':
            self._condense()
        elif phase == 'macros':
            self._annotate_macros()
        elif phase == 'save':
            self._save_profile()
            self.state['phase'] = 'done'
        else:
            raise ValueError('Unknown style phase: ' + phase)

    def _scan(self):
        self.state['warnings'] += [f'Not used for the style: {problem}.' for problem in self.state.get('skipped', [])]
        targets = [s for s in self.state['sources'] if not s.get('source_archive')
                   or Path(s['path']).suffix.lower() in ('.tex', '.ltx', '.latex')]
        articles = []
        for source in targets:
            if source['path'].lower().endswith('.pdf'):
                info = {'source_kind': 'pdf_text', 'title': '', 'abstract': '', 'headings': [],
                        'environments': {}, 'inputs': [], 'lines': len(source['content'].splitlines())}
            else:
                info = scan(source['content'])
            if source.get('source_archive'):
                info['source_archive'] = source['source_archive']
            articles.append({'id': source['id'], 'path': source['path'], **info})
        self.state['articles'] = articles
        support = len(self.state['sources']) - len(targets)
        if support:
            self.state['warnings'].append(f'{support} archive bibliography/style/class support file(s) are pinned but are not classified or observed as articles.')
        if any(s.get('source_archive') for s in targets):
            self.state['warnings'].append('Each archive TeX member is a text source and may be an article fragment; this source count is not a count of independent papers. Includes/macros were not assembled.')
        for article in self.state['articles']:
            if article['inputs']:
                self.state['warnings'].append(f'{article["path"]} includes other files ({", ".join(article["inputs"][:4])}); '
                                              'only the pinned file was read. Pin the included files too to use them.')
        self.state['phase'] = 'classify'

    def _classify(self):
        articles = self.state['articles']
        instruction = ('Sort these articles by the kind of mathematical writing they are, so the author gets one style per '
                       'domain (for example numerical analysis, theoretical PDE, geometry, or mathematics written for '
                       'physicists). Group articles whose aim and readership are alike: the same field, and the same '
                       'audience (specialists, the wider mathematical community, or readers from another science). Use a '
                       'single domain when they all are alike. Give each domain a short name and a one-sentence '
                       'description of its aim and readership, and list its article identifiers such as "M1". Every '
                       'article belongs to exactly one domain.\n\nAUTHOR\'S NOTE (may be empty): ' + self.state['goal'] +
                       '\n\nARTICLES:\n' + '\n\n'.join(self._digest(a) for a in articles))
        result = self._call('classify', instruction, cap=output_limit(self.agent, 'review_metadata', 1500), format_schema=DOMAINS_SCHEMA)
        try:
            value = json.loads(result['text'])
        except ValueError:
            value = {}
        ids, domains, placed = {a['id'] for a in articles}, [], set()
        for item in (value.get('domains') if isinstance(value, dict) else None) or []:
            if not isinstance(item, dict):
                continue
            members = [i for i in dict.fromkeys(item.get('articles') or []) if i in ids and i not in placed]
            if members:
                placed.update(members)
                domains.append({'name': ' '.join(str(item.get('name') or 'General').split())[:60] or 'General',
                                'description': ' '.join(str(item.get('description') or '').split())[:300],
                                'articles': members})
        missing = [a['id'] for a in articles if a['id'] not in placed]
        if missing:
            if domains:
                self.state['warnings'].append(f'{len(missing)} article(s) were not sorted by the model; they form their own domain.')
            domains.append({'name': 'Other' if domains else 'General', 'description': '', 'articles': missing})
        self.state['domains'] = [{**d, 'style': None, 'complete': False} for d in domains]
        self.state['phase'] = 'observe'

    def _observe(self):
        pending = [a for a in self.state['articles'] if a['id'] not in self.state['observations']]
        if not pending:
            self.state['phase'] = 'condense'
            return
        article = pending[0]
        source = self._article(article['id'])
        cap = substantive_output(self.agent, 6000)
        rules = guideline_rules()
        listing = '\n'.join(f'- {r["id"]} [{r["type"]}] {r["text"]}' for r in rules)
        room = max(2000, self._room(cap) - len(listing))
        pdf = article.get('source_kind') == 'pdf_text'
        text, whole = excerpts(source['content'], room, pdf=pdf)
        if not whole:
            self.state['warnings'].append(f'{article["path"]} is longer than one call can read here ({room:,} characters); '
                                          'its style was learnt from representative passages, not the whole text.')
        domain = next(d for d in self.state['domains'] if article['id'] in d['articles'])
        instruction = (f'Describe how article {article["id"]} (domain "{domain["name"]}") is written, not what it proves, '
                       'as the style method says. Answer from what this article actually does: concrete items that another '
                       'writer could follow, never advice that would fit any author. Leave a list empty rather than '
                       'filling it with something generic.\n'
                       '- audience: who the paper is written for (specialists of its field, the wider mathematical '
                       'community, physicists or other applied readers) and what that changes in the exposition.\n'
                       '- aim: what the paper sets out to do for its reader.\n'
                       '- structure: its parts in order, each with what that part does.\n'
                       + ''.join(f'- {key}: {question}.\n' for key, _, question in QUESTIONS) +
                       '- names: objects, results or methods that have several names (by field or country, for example '
                       'Cauchy--Lipschitz or Picard--Lindelöf theorem, overdamped Langevin dynamics or a reversible '
                       'diffusion) with the term copied exactly as the article writes it, and in the note what it names '
                       'and which other names it is preferred to.\n'
                       '- typesetting: habits of the LaTeX source that a write-up should reproduce (spacing or sizing of '
                       'delimiters, \\displaystyle in fractions, how differentials, sets, norms, expectations or '
                       'conditional expectations are written, choice of equation environments, labels), each with an '
                       'example copied exactly from the source.\n'
                       '- phrases: short characteristic connectives and signposts copied exactly from the text, not '
                       'mathematical statements.\n'
                       '- departures: the WRITE-UP RULES below that this article does not follow, deliberately: the same '
                       'way more than once, or as a convention of the whole paper, never a single slip. For each: rule (its '
                       'identifier), habit (what the author does instead, in one sentence) and example (one or two lines '
                       'copied exactly from the article\'s LaTeX source that show it). An empty list when the article '
                       'follows the rules.\n\nWRITE-UP RULES (what a write-up follows unless the author\'s style or the '
                       'request says otherwise):\n' + listing + '\n\n' + self._digest(article) +
                       '\n\n' + ('ARTICLE (text extracted from a PDF; mathematical notation and layout may be distorted). '
                                  'Learn the author’s prose from legible text. Leave typesetting and departures empty. '
                                  'The original LaTeX commands, environments and packages are unavailable.\n' if pdf else
                                  'ARTICLE (LaTeX source, comments removed):\n') + text)
        result = self._call('observe', instruction, cap=cap, format_schema=OBSERVE_SCHEMA)
        try:
            value = json.loads(result['text'])
        except ValueError:
            value = {}
        style, dropped = clean_style(value, [source['content']], [] if pdf or source.get('derived_from_images') else None, rules)
        if dropped:
            self.state['warnings'].append(f'{dropped} name(s), typesetting example(s), phrasing(s) or departure(s) quoted '
                                          f'for {article["path"]} were not found in it and were dropped.')
        if not result['complete'] or not (style['aim'] or style['structure'] or any(style[k] for k, _, _ in QUESTIONS)):
            self.state['warnings'].append(f'The style description of {article["path"]} is incomplete.')
        self.state['observations'][article['id']] = {**style, 'complete': result['complete']}

    def _condense(self):
        domain = next((d for d in self.state['domains'] if not d['complete']), None)
        if domain is None:
            self.state['phase'] = 'macros'
            return
        # The model merges the prose answers; quotations and departures are merged by counting (_quotations, _departures).
        notes = [{k: v for k, v in self.state['observations'][i].items() if k in PROSE_FIELDS}
                 for i in domain['articles'] if i in self.state['observations']]
        counted = {**self._quotations(domain), 'departures': self._departures(domain)}
        if len(notes) == 1:  # one article: its description is already the domain's
            domain['style'] = {**blank_style(), **notes[0], **counted}
            domain['complete'] = True
            return
        instruction = (f'Merge these style descriptions of {len(notes)} articles from the domain "{domain["name"]}" '
                       f'({domain["description"]}) into one style guide for writing new papers of this kind, with the '
                       'same fields. Keep what recurs across articles and what is distinctive of this author; where the '
                       'articles differ, say on what it depends (for example the length of the paper or its readers). '
                       'Drop what is specific to one paper\'s mathematics and anything generic. The structure is the '
                       'author\'s usual order of parts.\n\nDESCRIPTIONS:\n' + json.dumps(notes, ensure_ascii=False, indent=1))
        result = self._call('condense', instruction, cap=substantive_output(self.agent, 6000), format_schema=CONDENSE_SCHEMA)
        try:
            value = json.loads(result['text'])
        except ValueError:
            value = {}
        style = {key: value[key] for key in PROSE_FIELDS} if isinstance(value, dict) and all(key in value for key in PROSE_FIELDS) else {}
        style, _ = clean_style(style, [])
        domain['merged'] = result['complete'] and bool(style['aim'] or style['structure'])
        if not domain['merged']:
            self.state['warnings'].append(f'Merging the "{domain["name"]}" articles failed; the first article\'s description is used.')
            style = {**blank_style(), **notes[0]}
        domain['style'], domain['complete'] = {**style, **counted}, True

    def _quotations(self, domain):
        """A domain's names, typesetting habits and phrasings: its articles' own, each already found in its article,
        merged by counting (the most frequent first, then in order of appearance), never retyped by a model."""
        notes = [self.state['observations'][i] for i in domain['articles'] if i in self.state['observations']]

        def merged(key, field, limit):
            count, first = Counter(), {}
            for note in notes:
                for item in note.get(key) or []:
                    name = item[field] if field else item
                    count[name] += 1
                    first.setdefault(name, item)
            return [first[name] for name, _ in sorted(count.items(), key=lambda kv: -kv[1])][:limit]
        kept = {'names': merged('names', 'term', LIMITS['names']),
                'typesetting': merged('typesetting', 'example', LIMITS['typesetting']),
                'phrases': merged('phrases', None, LIMITS['phrases'])}
        # Jobs resumed after a version 1 observation still describe the author's explanations in habits.
        if habits := merged('habits', None, 10):
            kept['habits'] = habits
        return kept

    def _departures(self, domain):
        """The write-up rules a domain's style departs from (exact counting, no model): those its articles'
        descriptions name, each with an example found in its article, kept when a strict majority of the domain's
        described articles do (both of two, two of three). Each keeps the rule's text as it reads now, so that a
        write-up can tell when the guidelines changed since."""
        rules = {r['id']: r for r in guideline_rules()}
        notes = [self.state['observations'][i] for i in domain['articles'] if i in self.state['observations']]
        seen = {}
        for note in notes:
            for departure in note.get('departures') or []:
                seen.setdefault(departure['rule'], []).append(departure)
        return [{'rule': rid, 'sheet': rule['sheet'], 'rule_text': rule['text'], 'habit': seen[rid][0]['habit'],
                 'examples': list(dict.fromkeys(d['example'] for d in seen[rid]))[:2],
                 'articles': len(seen[rid]), 'of': len(notes)}
                for rid, rule in rules.items() if rid in seen and 2 * len(seen[rid]) > len(notes)]

    def _macros(self):
        """The macros the files define and the texts use, most widely used first (exact, no model).

        Uses are counted in every file, so a macro file pinned with the articles counts. The definition
        kept is the one of the file whose text uses the macro most (the first one, on a tie)."""
        sources = self._typeset_sources()
        commands = {s['path']: Counter(re.findall(r'\\([A-Za-z]+)(?![A-Za-z@])', written_text(s['content']))) for s in sources}
        definitions = {}
        for source in sources:
            for name, definition in macro_definitions(source['content']).items():
                if '@' not in name and not _LAYOUT.fullmatch(name):
                    definitions.setdefault(name, []).append((source['path'], definition))
        found = {}
        for name, defined in definitions.items():
            counts = {path: count[name] for path, count in commands.items() if count[name]}
            path, definition = max(defined, key=lambda item: counts.get(item[0], 0))
            found[name] = {'name': name, 'definition': definition, 'uses': sum(counts.values()),
                           'articles': list(counts), 'source': path}
        kept = sorted((m for m in found.values() if m['uses']), key=lambda m: (-len(m['articles']), -m['uses'], m['name']))
        kept = kept[:LIMITS['macros']]
        # A kept definition may use a macro the texts never use directly (\E inside \Ex): keep it too, for
        # the articles that need it. The list grows while it is read, so dependencies of dependencies come.
        names = {m['name']: m for m in kept}
        for macro in kept:
            for name in sorted(_referenced(macro['definition']) - {macro['name']}):
                if name in found:
                    if name not in names:
                        names[name] = found[name]
                        kept.append(found[name])
                    names[name]['articles'] = list(dict.fromkeys(names[name]['articles'] + macro['articles']))
        return kept

    def _annotate_macros(self):
        """One call: what each macro is used for when its definition does not say it, and the packages it needs."""
        macros = self._macros()
        if not macros:
            self.state['macro_notes'], self.state['phase'] = {}, 'save'
            return
        sources = {s['path']: s['content'] for s in self._typeset_sources()}
        loaded = {path: packages(content) for path, content in sources.items()}
        texts = {path: written_text(content) for path, content in sources.items()}
        cap = substantive_output(self.agent, 6000)
        room = self._room(cap)
        listing = 'PACKAGES EACH FILE LOADS:\n' + '\n'.join(f'{path}: {", ".join(names) or "none"}' for path, names in loaded.items())
        shown = []
        for macro in macros:
            seen = [snippet for path in macro['articles'][:2] for snippet in contexts(texts[path], macro['name'], 3)]
            entry = (f'\n\n\\{macro["name"]} (defined in {macro["source"]}; {macro["uses"]} direct uses)\n'
                     f'Definition: {macro["definition"]}\nIn use: ' + (' | '.join(seen) or 'only inside other macros'))
            if len(listing) + len(entry) > room:
                break
            listing += entry
            shown.append(macro['name'])
        instruction = ('For each macro below, from its definition and the passages where the author uses it, give:\n'
                       '- usage: what the author uses it for, in a few words, when the definition alone does not say it or '
                       'when the author keeps it for one purpose (for example \\rmo defined as \\mathrm{o} and used only for '
                       'the little-o of asymptotic expansions: "little-o in asymptotic expansions only"); empty when the '
                       'definition says it all;\n'
                       '- packages: the packages its definition needs to compile, among those its files load; none when '
                       'LaTeX and amsmath suffice.\nAnswer for every macro listed, by its name without the backslash.\n\n'
                       + listing)
        result = self._call('macros', instruction, cap=cap, format_schema=MACROS_SCHEMA)
        try:
            value = json.loads(result['text'])
        except ValueError:
            value = {}
        by_name = {m['name']: m for m in macros}
        notes = {}
        for item in (value.get('macros') if isinstance(value, dict) else None) or []:
            name = str(item.get('name') or '').lstrip('\\') if isinstance(item, dict) else ''
            if name not in by_name or name in notes:
                continue
            macro = by_name[name]
            available = {p for path in [macro['source'], *macro['articles']] for p in loaded.get(path, {})}
            notes[name] = {'usage': _text(item.get('usage'), LIMITS['usage']),
                           'packages': sorted({str(p).strip() for p in item.get('packages') or []} & available)}
        self.state['macro_notes'] = notes
        self.state['macros_complete'] = result['complete'] and len(shown) == len(macros)
        if not result['complete']:
            self.state['warnings'].append('The call describing how the macros are used did not finish; some macros have no note.')
        elif len(shown) < len(macros):
            self.state['warnings'].append(f'{len(macros) - len(shown)} less used macro(s) did not fit in the call that '
                                          'describes the macros; they are kept without a note.')
        self.state['phase'] = 'save'

    def _profile(self):
        sources = self._typeset_sources()
        bodies = {s['path']: document_body(s['content']) for s in sources}
        notes = self.state.get('macro_notes') or {}
        macros = []
        for macro in self._macros():
            note = notes.get(macro['name'], {})
            macros.append({**{k: macro[k] for k in ('name', 'definition', 'uses', 'articles')}, 'usage': note.get('usage', ''),
                           'packages': sorted(macro_packages(macro['definition']) | set(note.get('packages', [])))})
        loaded, environments = {}, {}
        for source in sources:
            for name, options in packages(source['content']).items():
                entry = loaded.setdefault(name, {'name': name, 'options': options, 'articles': []})
                entry['articles'].append(source['path'])
            for name, definition in environment_definitions(source['content']).items():
                entry = environments.setdefault(name, {'name': name, 'definition': definition, 'uses': 0})
                entry['uses'] += len(re.findall(r'\\begin\{' + re.escape(name) + r'\}', bodies[source['path']]))
        paths = {s['id']: s['path'] for s in self.state['sources']}
        domain_of = {i: d['name'] for d in self.state['domains'] for i in d['articles']}
        now = _now()
        return {'format': FORMAT, 'version': VERSION, 'name': self.state['style_name'],
                'created_at': now, 'updated_at': now, 'job': self.state['id'], 'model': self.state['settings']['model'],
                'articles': [{'path': a['path'], 'sha256': self._article(a['id'])['sha256'], 'title': a['title'],
                              'domain': domain_of.get(a['id'], ''),
                              **({'source_kind': a['source_kind']} if a.get('source_kind') else {}),
                              **({'source_archive': a['source_archive']} if a.get('source_archive') else {})} for a in self.state['articles']],
                'packages': sorted(loaded.values(), key=lambda p: (-len(p['articles']), p['name']))[:LIMITS['packages']],
                'macros': macros,
                'environments': sorted(environments.values(), key=lambda e: -e['uses'])[:LIMITS['environments']],
                'domains': [{'name': d['name'], 'description': d['description'], 'articles': [paths[i] for i in d['articles']],
                             **blank_style(), **(d['style'] or {})}
                            for d in self.state['domains']]}

    def _save_profile(self):
        profile = self._profile()
        if self.state.get('template'):
            profile['template'] = self.state['template']
        try:
            previous = load_profile(self.styles_root, profile['name'])
            profile['created_at'] = previous.get('created_at', profile['created_at'])
            if previous.get('template') and not profile.get('template'):
                profile['template'] = previous['template']  # learning again keeps the template already chosen
        except (OSError, ValueError):
            pass
        try:
            path = save_profile(self.styles_root, profile)
        except OSError as exc:
            raise WorkerInputError(f'The style could not be saved: {exc}', code='style_save_failed', scope='execution') from exc
        self.state['profile'] = profile
        self.state['saved_to'] = str(path)
        self.emit('notice', f'Saved the style "{profile["name"]}" ({len(profile["domains"])} domain(s), {len(profile["macros"])} macros)')

    def _finish(self):
        complete = (self.state.get('saved_to') and all(o.get('complete') for o in self.state['observations'].values())
                    and len(self.state['observations']) == len(self.state['articles'])
                    and all(d.get('merged', True) for d in self.state['domains'])
                    and self.state.get('macros_complete', True))
        self.state['status'] = 'saved' if complete else 'partial'
        self.state['stop_reason'] = ('Style saved; read it and correct anything that does not sound like you.' if self.state.get('saved_to')
                                     else 'The style was not saved.')

    def _progress(self):
        total = len(self.state.get('articles', [])) + len(self.state.get('domains', [])) + 1
        done = (len(self.state.get('observations', {})) + sum(bool(d.get('complete')) for d in self.state.get('domains', []))
                + (self.state.get('macro_notes') is not None))
        return {'phase': self.state['phase'], 'completed': done, 'total': total, 'unit': 'articles, domains and macros',
                'scope': self.state['goal'], 'partial': self.state['status'] in ('partial', 'error', 'budget_exhausted'),
                'resumable': self.state['status'] in ('running', 'paused')}

    def _report(self):
        s = self.state
        profile = s.get('profile')
        out = ['# Writing style', '', '**Model description of your style: read it and correct what does not sound like you.**', '',
               f'Status: `{s["status"]}`. Phase: `{s["phase"]}`. Job ID: `{s["id"]}`.', '',
               s.get('stop_reason', 'The style is being condensed.'), '',
               f'Style name: `{s.get("style_name")}`' + (f'; saved to `{s["saved_to"]}`.' if s.get('saved_to') else '; not saved yet.')]
        if s.get('warnings'):
            out += ['', '## Controller checks', ''] + ['- ' + w for w in dict.fromkeys(s['warnings'])]
        if profile:
            out += ['', '## Style', '', '```text', style_prompt(profile), '```']
        out += ['', '## Articles and source fragments' if any(a.get('source_archive') for a in s.get('articles', [])) else '## Articles', '']
        domain_of = {i: d['name'] for d in s.get('domains', []) for i in d['articles']}
        for article in s.get('articles', []):
            out.append(f'- {article["id"]} `{article["path"]}`: {article["title"] or "untitled"}'
                       f'{" — " + domain_of[article["id"]] if article["id"] in domain_of else ""}.')
        out += ['', usage_footer(s), '']
        _atomic_write(self.directory / 'report.md', '\n'.join(out).encode('utf-8'))


def definition_body(definition):
    """The replacement text of a macro definition (its last {...} group), spaces normalised."""
    text = definition.rstrip()
    if not text.endswith('}'):
        return ''
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        if text[index] == '}' and (index == 0 or text[index - 1] != '\\'):
            depth += 1
        elif text[index] == '{' and (index == 0 or text[index - 1] != '\\'):
            depth -= 1
            if depth == 0:
                return ' '.join(text[index + 1:-1].split())
    return ''


def grouped(definition):
    """A parameterless macro whose replacement is several tokens, with that replacement braced.

    `\\newcommand{\\dt}{\\Delta t}` makes `\\tau^\\dt` read as `\\tau^\\Delta t` and `\\sqrt\\dt` as
    `\\sqrt\\Delta t`: the script or the root takes `\\Delta` alone. `{{\\Delta t}}` keeps the
    meaning everywhere. Definitions with parameters, or already one token, are unchanged.
    """
    match = re.fullmatch(r'(\\(?:(?:re|provide)?newcommand\*?\s*(?:\{\s*\\[A-Za-z]+\s*\}|\\[A-Za-z]+)|def\s*\\[A-Za-z]+))'
                         r'\s*(\{.*\})\s*', definition.strip(), re.S)
    if not match:
        return definition
    body = match.group(2)[1:-1]
    inner, end = _group(body.strip(), 0)
    if (re.fullmatch(r'\s*(?:\\[A-Za-z]+|[^\s\\{}])\s*', body)
            or (inner is not None and end == len(body.strip()))):  # one token, or braced already
        return definition
    # Not notation: a number or length (\arraystretch is read as a number, so {2.2} must stay bare),
    # or text that opens environments, aligns or breaks lines (\newcommand{\be}{\begin{equation}}).
    if (re.fullmatch(r'\s*-?(?:\d+\.?\d*|\.\d+)\s*(?:pt|em|ex|cm|mm|in|bp|sp|pc|dd|cc|mu)?\s*', body)
            or re.search(r'\\(?:begin|end|par)\b|\\\\|&|#', body)):
        return definition
    return match.group(1) + '{{' + body + '}}'


CHOICE_MODES = ('auto', 'none', 'style', 'domain')


def choice_path(root):
    return Path(root) / '.mathagent' / 'style-choice.json'


def load_choice(root):
    """Which saved style write-ups use: chosen automatically (the default), none, one style, or one of its domains."""
    path = choice_path(root)
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() and not path.is_symlink() else {}
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict) or value.get('mode') not in CHOICE_MODES:
        return {'mode': 'auto'}
    return {k: value[k] for k in ('mode', 'style', 'domain') if isinstance(value.get(k), str)}


def save_choice(root, mode, style=None, domain=None):
    if mode not in CHOICE_MODES:
        raise ValueError('Choose automatic, none, a style or one of its domains')
    choice = {'mode': mode}
    if mode in ('style', 'domain'):
        profile = load_profile(root, style)  # an unknown style fails here
        choice['style'] = profile['name']
        if mode == 'domain':
            if not any(d['name'] == domain for d in profile['domains']):
                raise ValueError(f'The style "{style}" has no domain "{domain}"')
            choice['domain'] = domain
    _directory(Path(root) / '.mathagent', create=True)
    _atomic_write(choice_path(root), json.dumps(choice, ensure_ascii=False).encode('utf-8'))
    return choice
