# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The typo check in three lanes: rules, language, mathematics.

One reader that does everything misses half of the slips and "corrects" correct formulas. Measured on a 3,000-line
paper (Nov 2026), three lanes do better, each with the check that suits it:
- rules (typolint): no model, right almost always, so their findings need no second opinion;
- language: small units with no paper in front, read twice (two unit sizes, two temperatures); the model returns
  corrected lines and the edits come from a word diff, which gives fewer junk remarks than a list of remarks. A quick
  second look sorts the edits into error / style / wrong; an edit both readings made is never dropped (the second
  look called one such key edit wrong);
- mathematics: the whole paper in front, one formula-reading call per part and one call on notation; every remark is
  then checked alone, with hidden reasoning, because the wrong corrections came from the model's own computations
  and a no-reasoning batch check let them through.

TypoLanes is a mixin for the typo check's runner (review.TypoCheckRunner). The lanes run when the whole paper fits in
a call and the source is not a PDF; otherwise the runner reads part by part as before, plus the rules.
"""

import difflib
import re
from pathlib import Path

from . import typolint

# -- the language lane ---------------------------------------------------------------------------------------

LANGUAGE_SAMPLES = ((60, 0.2), (40, 0.5))  # (unit lines, temperature): two readings cut in different places
LANGUAGE_CAP = 3000
CONTEXT = 4  # lines shown around a unit
MERGE_GAP = 3  # edits of one line this many words apart are one edit ("aims at proving" → "aims to prove")
MAX_EDITS = 4  # more edits on one line is a rewrite, not a correction
VERDICT_BATCH, VERDICT_CAP = 8, 1500
MASKS = set(typolint.OPEN + typolint.FILL + typolint.CLOSE + typolint.ARG)

ERRORS = """Errors to look for (authors are often not native speakers):
- misspelled words; a word typed twice; a stray leftover word or letter; a missing word
- missing or wrong article ("is composition of" -> "is the composition of", "an unique", "the Theorem 2")
- subject-verb agreement ("the difference ... have"), singular/plural ("at each cardinal directions", "there exists two")
- wrong verb pattern or preposition ("allows to conclude", "on each point of", "depends of", "entrance time the set")
- wrong word form ("almost uniformly distribution"), wrong mood or tense ("if there was" -> "if there were")
- a sentence that starts with a lowercase letter, a capital in mid-sentence, wrong or missing punctuation
- a sentence that is broken or cannot be parsed, a statement said twice in one sentence"""
RULES = """Do NOT change: mathematics (anything inside $...$ or a display), LaTeX commands, labels, citations, names of
people, British or American spelling, the authors' terminology or style. Do not rephrase a sentence that is correct.
Only correct definite errors of English, with the smallest change."""
FIX = ("You are a copy editor for mathematics papers written in English. You receive numbered lines of LaTeX "
       "source. Proofread the lines between BEGIN and END, word by word.\n" + ERRORS + "\n" + RULES +
       "\nReturn JSON {\"lines\": [{\"n\": line number, \"corrected\": the whole line, corrected}]} with only the "
       "lines you changed. If nothing is wrong, return {\"lines\": []}.")
FIX_SCHEMA = {'type': 'object', 'required': ['lines'], 'properties': {'lines': {'type': 'array', 'items': {
    'type': 'object', 'required': ['n', 'corrected'], 'properties': {'n': {'type': 'integer'}, 'corrected': {'type': 'string'}}}}}}

VERDICT = """You check proposed corrections of the English of a mathematics paper (LaTeX source). For each proposal you get
the lines around it, the original words and the proposed words. Decide:
- "error": the original is definitely wrong English (spelling, grammar, a missing or doubled word, punctuation,
  capitalisation) and the proposal is a correct fix that keeps the meaning.
- "style": the original is acceptable English; the proposal is an optional improvement, a matter of taste, or only
  changes British/American spelling or hyphenation of an accepted variant.
- "wrong": the proposal is not correct English in this sentence, changes the meaning or the mathematics, or
  misreads the sentence (for instance because the sentence continues on the next line).
Read the whole sentence, which may run over several lines, before deciding. Return JSON
{"items": [{"id": ..., "verdict": "error" | "style" | "wrong"}]} with every id once."""
VERDICT_SCHEMA = {'type': 'object', 'required': ['items'], 'properties': {'items': {'type': 'array', 'items': {
    'type': 'object', 'required': ['id', 'verdict'], 'properties': {'id': {'type': 'integer'},
    'verdict': {'type': 'string', 'enum': ['error', 'style', 'wrong']}}}}}}

# -- the mathematics lane ------------------------------------------------------------------------------------

FIND_CAP = 6000
FIND = """The whole manuscript is above, with line numbers. Proofread the formulas of lines {a}-{b}, one formula at a time
(inline formulas too). For each formula compare it, symbol by symbol, with the places of the manuscript that fix what
it must be: the definition of each symbol, the same formula or statement elsewhere (introduction, theorem, proof), the
line before and the line after in a computation, the equation a reference points to. Look for:
- a wrong or missing index, subscript, superscript, exponent, sign, factor or constant;
- a wrong set or domain (of a function, an integral, a sum, a norm, an indicator; open against closed);
- a variable that is free where it should be bound, or bound twice; a quantifier or a bracket missing;
- a symbol that changed (two notations for one object, one notation for two objects, a symbol never defined);
- a formula restated differently from its first statement; a reference to the wrong equation, lemma or theorem;
- a stray symbol or a typographic slip inside a formula.
Report a slip only when another line of the manuscript shows it. Give that line: evidence_line and evidence_quote
(copied exactly). A formula that merely looks unusual, or that your own computation would write differently, is not
a slip: mathematics papers are mostly right, and a correct formula "corrected" into an error is the worst outcome. If
you believe a formula is wrong but no line of the manuscript settles it, you may report it with evidence_line 0; it
will be shown as a place to check, not as a correction. Do not report anything outside lines {a}-{b}, and nothing
about English, style, spacing or LaTeX commands.
Return JSON {{"summary": two to five sentences saying what lines {a}-{b} do, "remarks": [{{"line": n, "quote": the
formula or part of it, copied exactly (at most 15 words), "problem": one sentence, "correction": the corrected formula
or words, "evidence_line": n or 0, "evidence_quote": copied exactly or ""}}]}}. An empty list of remarks is a good
answer for correct lines."""
REMARKS = {'type': 'array', 'items': {
    'type': 'object', 'required': ['line', 'quote', 'problem', 'correction', 'evidence_line', 'evidence_quote'],
    'properties': {'line': {'type': 'integer'}, 'quote': {'type': 'string'}, 'problem': {'type': 'string'},
                   'correction': {'type': 'string'}, 'evidence_line': {'type': 'integer'}, 'evidence_quote': {'type': 'string'}}}}
FIND_SCHEMA = {'type': 'object', 'required': ['summary', 'remarks'],
               'properties': {'summary': {'type': 'string'}, 'remarks': REMARKS}}
NOTATION_SCHEMA = {'type': 'object', 'required': ['remarks'], 'properties': {'remarks': REMARKS}}
NOTATION = """The whole manuscript is above, with line numbers. Check its notation across sections. Report:
- one object written in two ways (two fonts, two letters, with and without an index or a tilde);
- one symbol used for two different objects;
- a formula or a statement given twice (in the introduction and in a theorem, in a statement and in its proof, in two
  sections) where the two versions differ: a different set, constant, index, sign or hypothesis;
- a symbol used but never defined.
Each remark needs BOTH places: line and quote of the occurrence to change, and evidence_line and evidence_quote of the
occurrence it conflicts with (for a symbol never defined, evidence_line 0). A remark with one side only is not a
remark. At most 20, the surest first.
Return JSON {"remarks": [{"line": n, "quote": copied exactly, "problem": one sentence, "correction": what to write,
"evidence_line": n, "evidence_quote": copied exactly}]}."""
CHECK1 = """The whole manuscript is above, with line numbers. A proofreader claims the formula or notation below is
misprinted. Decide whether the manuscript is wrong there. Do the computation or the comparison yourself, from the
manuscript's own definitions (its macros and notation included); a formula quoted from another paper, an equivalent
form and a convention the authors may choose are not slips. Verdict: "correction" only if the original is certainly
wrong and the proposed correction certainly right; "look" if the place is wrong or inconsistent but the right fix is
not certain; "drop" if the original is right. Return JSON {"check": two sentences, "verdict": "correction" | "look" | "drop"}."""
CHECK1_SCHEMA = {'type': 'object', 'required': ['check', 'verdict'], 'properties': {'check': {'type': 'string'},
                 'verdict': {'type': 'string', 'enum': ['correction', 'look', 'drop']}}}


# -- pure helpers ----------------------------------------------------------------------------------------------

def units(lines, size, begin, end):
    """Units of about `size` lines between begin and end, each ending at the end of a sentence when one is near."""
    out, a = [], begin
    while a <= end:
        b = min(end, a + size - 1)
        while b < end and b - a < size + 6 and lines[b - 1].strip() and not re.search(r'[.:]\s*$|\\end\{', lines[b - 1]):
            b += 1
        if any(line.strip() and not line.lstrip().startswith('%') for line in lines[a - 1:b]):
            out.append((a, b))
        a = b + 1
    return out


def numbered(lines, a, b):
    """The unit between BEGIN and END, with CONTEXT lines around it."""
    rows = []
    for n in range(max(1, a - CONTEXT), min(len(lines), b + CONTEXT) + 1):
        if n == a:
            rows.append('--- BEGIN (proofread from here) ---')
        rows.append(f'{n}| {lines[n - 1]}')
        if n == b:
            rows.append('--- END ---')
    return '\n'.join(rows)


def tokens(text):
    return re.findall(r'\s+|\\[a-zA-Z]+|\w+|[^\w\s]', text)


def _american(word):
    w = word.lower()
    w = dict(typolint.BRITISH_PAIRS).get(w, w)
    w = re.sub(r'is(e|ed|es|ing|ation|ations|er|ers)$', r'iz\1', w)
    w = re.sub(r'ys(e|ed|es|ing)$', r'yz\1', w)
    w = re.sub(r'our(s|ed|ing|able|ite|ites)?$', r'or\1', w)
    w = re.sub(r'tre(s)?$', r'ter\1', w)
    return re.sub(r'([aeiou])ll(ed|ing|er|ers)$', r'\1l\2', w)


def spelling_swap(old, new):
    """True when new only swaps British and American spelling of old (colour/color, -ise/-ize, modelling)."""
    a, b = re.findall(r'[A-Za-z]+', old), re.findall(r'[A-Za-z]+', new)
    return (len(a) == len(b) and re.sub(r'[A-Za-z]+', '', old) == re.sub(r'[A-Za-z]+', '', new)
            and any(x.lower() != y.lower() for x, y in zip(a, b))
            and all(x.lower() == y.lower() or _american(x) == _american(y) for x, y in zip(a, b)))


def line_edits(n, old, new, masked):
    """The edits that turn line n into its correction, or None for a rewrite (more than MAX_EDITS edits).

    Neighbouring edits are merged first: judged word by word, "aims at proving" → "aims to prove" is two wrong edits.
    An edit is left out when it touches masked math, a command argument or a comment, when its new text has a line
    break or LaTeX, or when it only swaps British and American spelling."""
    a, b = tokens(old), tokens(new)
    offsets, at = [], 0
    for t in a:
        offsets.append(at)
        at += len(t)
    changes = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
               if tag != 'equal' and (''.join(a[i1:i2]).strip() or ''.join(b[j1:j2]).strip())]
    if len(changes) > MAX_EDITS:
        return None
    spans = []
    for i1, i2, j1, j2 in changes:
        if spans and sum(bool(t.strip()) for t in a[spans[-1][1]:i1]) <= MERGE_GAP:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    out = []
    for i1, i2, j1, j2 in spans:
        was, now = ''.join(a[i1:i2]), ''.join(b[j1:j2])
        start = offsets[i1] if i1 < len(a) else len(old)
        stop = offsets[i2 - 1] + len(a[i2 - 1]) if i2 > i1 else start
        span = masked[start:stop]
        if MASKS & set(span) or (start == stop and MASKS & set(masked[max(0, start - 1):start + 1])):
            continue  # inside a formula or a label
        if was.strip() and not span.strip():
            continue  # a LaTeX comment, blanked in the masks
        if '\n' in now or '\r' in now or re.search(r'[$\\{}^_]', was + now) or spelling_swap(was, now):
            continue
        lo, hi = max(0, i1 - 3), min(len(a), i2 + 3)
        out.append({'start_line': n, 'end_line': n, 'quote': ''.join(a[lo:hi]).strip(),
                    'suggestion': (''.join(a[lo:i1]) + now + ''.join(a[i2:hi])).strip(), 'old': was, 'new': now})
    return out


def edit_id(edit):
    """Two readings propose the same edit when line, old words and new words agree (case aside)."""
    return (edit['start_line'], ' '.join(edit['old'].lower().split()), ' '.join(edit['new'].lower().split()))


def importance_of(verdict, unanimous):
    """The second look decides; an edit both readings made is never dropped (one such key edit was called wrong)."""
    if verdict == 'error':
        return 'typo'
    if verdict in ('style', 'wrong'):
        return 'suggestion' if verdict == 'style' or unanimous else None
    return 'typo' if unanimous else 'suggestion'  # no answer


def squash(text):
    return re.sub(r'[\s{}]+|\\left|\\right|\\,|\\!|\\;', '', text)


def near(lines, n, quote, reach=8):
    """The line within `reach` of n that holds the quote, ignoring spaces, braces and sizing commands; a long quote
    may match by its first or last 20 characters (a model rarely copies a whole formula exactly)."""
    q = squash(quote)
    if not q or not isinstance(n, int):
        return None
    for d in sorted(range(-reach, reach + 1), key=abs):
        m = n + d
        if 1 <= m <= len(lines):
            window = squash(' '.join(lines[m - 1:m + 2]))
            if q in window or (len(q) > 20 and (q[:20] in window or q[-20:] in window)):
                return m
    return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clip(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + ' […]'


# -- the runner mixin ----------------------------------------------------------------------------------------------

class TypoLanes:
    """Overrides of the paper review runner for the typo check. Everything a lane decides is kept in
    state['typo_lanes'] and state['presentation'], so a stopped job resumes without asking again."""

    def _lanes_state(self):
        s = self.state
        if s.get('typo_lanes') is None:
            s['typo_lanes'] = {'rules': False, 'units': {}, 'edits': [], 'aggregated': False, 'notation': None,
                               'math': [], 'unlocated': 0}
        return s['typo_lanes']

    def _lane_source(self):
        sid = self.state['sources'][0]['id']
        return sid, self._source(sid)['path'].lower()

    def _lanes_on(self):
        return (self.typos_only and not self._followup_mode() and bool(self.state.get('whole'))
                and not self._lane_source()[1].endswith('.pdf') and bool(self._reads()))

    def _audit_jobs(self):
        _, path = self._lane_source()
        if self.state.get('typo_lanes') is None and self._lanes_on():
            # A job saved by the earlier one-reader typo check: its notes and unchecked remarks have another shape.
            self.state['reading'], self.state['presentation'] = {}, []
        if self.typos_only and not self._followup_mode() and not path.endswith('.pdf'):
            self._rules()
        if not self._lanes_on():
            return super()._audit_jobs()
        return self._lane_jobs()

    # -- lane A: rules

    def _rules(self):
        lanes = self._lanes_state()
        if lanes['rules']:
            return
        sid, path = self._lane_source()
        tex = (self.state.get('map') or {}).get('format') == 'tex'
        if tex or path.endswith(('.md', '.markdown')):
            preamble = '\n'.join(p.get('content') or '' for p in self.state.get('packages') or [])
            for finding in typolint.lint(self._lines(sid), tex=tex, preamble=preamble):
                self.state['presentation'].append({**finding, 'lane': 'rule', 'checked': True, 'located': 'yes'})
        lanes['rules'] = True
        self._typo_counts()
        self._save()

    def _typo_counts(self):
        super()._typo_counts()
        # A rule finding is marked checked so that the job can complete; it was not checked against the paper.
        self.state['typo_check']['checked'] = sum(bool(c.get('checked')) and c.get('lane') != 'rule'
                                                  for c in self.state['presentation'])

    # -- scheduling

    def _lane_units(self):
        if getattr(self, '_units_cache', None) is None:
            reads = self._reads()
            lines = self._lines(self._lane_source()[0])
            self._units_cache = [units(lines, size, reads[0][0], reads[-1][1]) for size, _ in LANGUAGE_SAMPLES]
            self._lane_pending = set()
        return self._units_cache

    def _language_keys(self):
        return [(f'W{sample}:{a}', sample, a, b) for sample, found in enumerate(self._lane_units()) for a, b in found]

    def _lane_jobs(self):
        """Formula and notation calls first (the longest), then the language units, then the second looks and the
        checks, each as soon as what it needs is there. Called after every finished job: it must stay cheap."""
        lanes, reads = self._lanes_state(), self._reads()
        jobs = [(0, f'L{a}', self._formula_job(a, b)) for a, b in reads if not self._reading_usable(f'L{a}')]
        if not lanes['notation']:
            jobs.append((0, 'N', self._notation_job(reads[0][0], reads[-1][1])))
        keys = self._language_keys()
        for key, sample, a, b in keys:
            if not (lanes['units'].get(key) or {}).get('answered'):
                jobs.append((1, key, self._language_job(key, a, b, LANGUAGE_SAMPLES[sample][1])))
        if not lanes['aggregated'] and all(key in lanes['units'] for key, *_ in keys):
            self._aggregate()
        waiting = [i for i, e in enumerate(lanes['edits']) if not e.get('judged') and i not in self._lane_pending]
        for k in range(0, len(waiting), VERDICT_BATCH):
            batch = waiting[k:k + VERDICT_BATCH]
            self._lane_pending |= set(batch)
            jobs.append((2, f'V{batch[0]}', self._verdict_job(batch)))
        jobs += [(2, f'C{i}', self._check_job(i)) for i, r in enumerate(lanes['math']) if not r.get('checked')]
        return jobs

    def _lanes_done(self):
        lanes = self._lanes_state()
        return (bool(lanes['notation']) and lanes['aggregated']
                and all((lanes['units'].get(key) or {}).get('answered') for key, *_ in self._language_keys())
                and all(e.get('judged') for e in lanes['edits']) and all(r.get('checked') for r in lanes['math']))

    def _complete(self):
        done = super()._complete()
        if not self._lanes_on():
            return done
        lanes = self._lanes_state()
        failed = [key for key, *_ in self._language_keys() if not (lanes['units'].get(key) or {}).get('answered')]
        if failed:
            self._warn(f'{len(failed)} part(s) of the language reading got no usable answer; resume the typo check to read them again.')
        if not lanes['notation']:
            self._warn('The notation of the paper was not checked: that call got no usable answer.')
        return done and self._lanes_done()

    # -- lane B: language

    def _language_job(self, key, a, b, temperature):
        def run():
            sid = self._lane_source()[0]
            lines = self._lines(sid)
            self.emit('notice', f'Proofreading the English of lines {a}-{b}')
            result = self._ask('read', FIX + '\n\n' + numbered(lines, a, b), cap=LANGUAGE_CAP, schema=FIX_SCHEMA,
                               key=key, temperature=temperature, reference=False)
            value = result['value'] if isinstance(result['value'], dict) else {}
            answered = bool(result['complete']) and isinstance(value.get('lines'), list)
            masked = self._masked()
            found, rewrites = [], 0
            for item in value.get('lines') or [] if answered else []:
                n = item.get('n') if isinstance(item, dict) else None
                if not isinstance(n, int) or not a <= n <= b or not isinstance(item.get('corrected'), str):
                    continue
                new = re.sub(r'^\s*%d\|\s?' % n, '', item['corrected'])
                got = line_edits(n, lines[n - 1], new if lines[n - 1].endswith(' ') else new.rstrip(), masked[n - 1])
                if got is None:
                    rewrites += 1
                else:
                    found += got
            with self._mutex:
                lanes = self._lanes_state()
                lanes['units'][key] = {'answered': answered, 'edits': found, 'rewrites': rewrites}
                lanes['aggregated'] = False  # a late answer (after a resume) adds its edits and votes
                self._save()
        return run

    def _masked(self):
        if getattr(self, '_masked_cache', None) is None:
            sid = self._lane_source()[0]
            tex = (self.state.get('map') or {}).get('format') == 'tex'
            self._masked_cache = typolint._Source(self._lines(sid), tex).prose.split('\n')
        return self._masked_cache

    def _aggregate(self):
        """The edits of both readings, one entry per edit with its votes; an edit a rule already reports is left out."""
        lanes = self._lanes_state()
        rules = [c for c in self.state['presentation'] if c.get('lane') == 'rule']
        votes = {}
        for key, unit in lanes['units'].items():
            for edit in unit['edits']:
                votes.setdefault(edit_id(edit), (edit, set()))[1].add(key.split(':')[0])
        known = {edit_id(e): e for e in lanes['edits']}
        for ident, (edit, samples) in sorted(votes.items()):
            if ident in known:
                known[ident]['votes'] = len(samples)
            elif not self._ruled(ident, rules):
                lanes['edits'].append({**edit, 'votes': len(samples)})
        lanes['aggregated'] = True
        self._save()

    @staticmethod
    def _ruled(ident, rules):
        n, old, new = ident
        return any(r['start_line'] <= n <= r['end_line'] and ((old and old in r['quote'].lower())
                                                               or (new and new in (r.get('suggestion') or '').lower()))
                   for r in rules)

    def _verdict_job(self, batch):
        def run():
            lines = self._lines(self._lane_source()[0])
            rows = []
            for i in batch:
                e = self.state['typo_lanes']['edits'][i]
                n = e['start_line']
                context = '\n'.join(f'{m}| {lines[m - 1]}' for m in range(max(1, n - 2), min(len(lines), n + 2) + 1))
                rows.append(f'PROPOSAL {i} (line {n})\n{context}\noriginal: "{e["quote"]}"\nproposed: "{e["suggestion"]}"')
            self.emit('notice', f'Second look at {len(rows)} language corrections')
            result = self._ask('typo-check', VERDICT + '\n\nPROPOSALS:\n\n' + '\n\n'.join(rows), cap=VERDICT_CAP,
                               schema=VERDICT_SCHEMA, key=f'V{batch[0]}', temperature=0, reference=False)
            value = result['value'] if isinstance(result['value'], dict) else {}
            verdicts = {v.get('id'): v.get('verdict') for v in value.get('items') or [] if isinstance(v, dict)}
            with self._mutex:
                for i in batch:
                    e = self.state['typo_lanes']['edits'][i]
                    e['judged'], e['verdict'] = True, verdicts.get(i)
                    importance = importance_of(e['verdict'], e['votes'] >= len(LANGUAGE_SAMPLES))
                    if importance:
                        self.state['presentation'].append(self._language_comment(e, importance))
                self._typo_counts()
                self._save()
        return run

    @staticmethod
    def _language_comment(e, importance):
        old, new = ' '.join(e['old'].split()), ' '.join(e['new'].split())
        comment = (f'Read "{new}" for "{old}".' if old and new else f'"{new}" is missing.' if new else f'"{old}" is not needed.')
        single = len(old.split()) == 1 and len(new.split()) == 1 and typolint._distance_one(old.lower(), new.lower())
        return {'start_line': e['start_line'], 'end_line': e['end_line'], 'quote': e['quote'], 'kind': 'typo' if single else 'grammar',
                'importance': importance, 'comment': comment, 'suggestion': e['suggestion'], 'located': 'yes',
                'lane': 'language', 'votes': e['votes'], 'checked': True}

    # -- lane C: mathematics

    def _formula_job(self, a, b):
        key = f'L{a}'

        def run():
            self.emit('notice', f'Reading the formulas of lines {a}-{b}')
            result = self._ask('read', self._block() + '\n\n' + FIND.format(a=a, b=b), cap=self._call_cap(False, FIND_CAP),
                               schema=FIND_SCHEMA, key=key)
            value = result['value'] if isinstance(result['value'], dict) else {}
            usable = (bool(result['complete']) and isinstance(value.get('remarks'), list)
                      and isinstance(value.get('summary'), str) and bool(value['summary'].strip()))
            with self._mutex:
                if usable:
                    self._add_remarks(value['remarks'], a, b, 'formula')
                self.state['reading'][key] = {'start_line': a, 'end_line': b, 'summary': value['summary'] if usable else '',
                                              'usable': usable, 'complete': result['complete'], 'dropped': 0}
                self._save()
        return run

    def _notation_job(self, a, b):
        def run():
            self.emit('notice', 'Checking the notation across the paper')
            result = self._ask('read', self._block() + '\n\n' + NOTATION, cap=self._call_cap(False, FIND_CAP),
                               schema=NOTATION_SCHEMA, key='N')
            value = result['value'] if isinstance(result['value'], dict) else {}
            usable = bool(result['complete']) and isinstance(value.get('remarks'), list)
            with self._mutex:
                if usable:
                    self._add_remarks(value['remarks'], a, b, 'notation')
                self._lanes_state()['notation'] = usable
                self._save()
        return run

    def _add_remarks(self, items, a, b, source):
        """Remarks located with a loose match; one outside its part or not found is dropped. The same correction on
        the same line (±1) from the formula reading and the notation call is one remark."""
        lanes = self._lanes_state()
        lines = self._lines(self._lane_source()[0])
        for item in items:
            if not isinstance(item, dict):
                continue
            quote, correction = str(item.get('quote') or '').strip(), str(item.get('correction') or '').strip()
            n = near(lines, _int(item.get('line')), quote)
            if n is None or not a <= n <= b or squash(quote) == squash(correction):
                lanes['unlocated'] += 1
                continue
            evidence = near(lines, _int(item.get('evidence_line')), str(item.get('evidence_quote') or '')) if _int(item.get('evidence_line')) else None
            same = next((m for m in lanes['math'] if abs(m['start_line'] - n) <= 1 and squash(m['suggestion']) == squash(correction)), None)
            if same:
                same['votes'] += 1
                if not same['evidence_line'] and evidence:
                    same['evidence_line'], same['evidence_quote'] = evidence, str(item.get('evidence_quote') or '')
                continue
            lanes['math'].append({'start_line': n, 'end_line': n, 'quote': _clip(quote, 300), 'comment': _clip(item.get('problem'), 600),
                                  'suggestion': _clip(correction, 600), 'evidence_line': evidence,
                                  'evidence_quote': str(item.get('evidence_quote') or '') if evidence else '',
                                  'source': source, 'votes': 1})

    def _check_job(self, i):
        def run():
            r = self.state['typo_lanes']['math'][i]
            row = (f'REMARK (line {r["start_line"]}): "{r["quote"]}" — {r["comment"]} Proposed: {r["suggestion"]}'
                   + (f' Evidence given: line {r["evidence_line"]}, "{r["evidence_quote"]}"' if r['evidence_line'] else ''))
            self.emit('notice', f'Checking a formula remark on line {r["start_line"]}')
            result = self._ask('typo-check', self._block() + '\n\n' + CHECK1 + '\n\n' + row, cap=self._call_cap(True),
                               schema=CHECK1_SCHEMA, key=f'C{i}', think=True)
            value = result['value'] if isinstance(result['value'], dict) else {}
            verdict = value.get('verdict') if value.get('verdict') in ('correction', 'look', 'drop') else None
            check = _clip(value.get('check'), 600)
            with self._mutex:
                r['checked'], r['verdict'], r['check'] = True, verdict or 'look', check
                comment = {'start_line': r['start_line'], 'end_line': r['end_line'], 'quote': r['quote'],
                           'kind': 'notation' if r['source'] == 'notation' else 'typo', 'importance': 'error',
                           'comment': r['comment'], 'suggestion': r['suggestion'], 'located': 'yes', 'lane': 'math',
                           'checked': True}
                if verdict == 'drop':
                    comment['rejected'] = check or 'The check found the original right.'
                elif verdict != 'correction':  # something is off, the right fix is not certain: never a correction
                    comment.update(importance='typo', look=True, suggestion='',
                                   comment=r['comment'] + (f' Check: {check}' if check else ''))
                self.state['presentation'].append(comment)
                self._typo_counts()
                self._save()
        return run

    # -- the report

    def _presentation_section(self, items, sid, unread, read_any, capped=True):
        if not self._lanes_on() or not items:
            return super()._presentation_section(items, sid, unread, read_any, capped=capped)
        blocks = {
            'math': ('Corrections in formulas and notation',
                     '*Each correction was checked against the paper; check it again before changing a formula.*'),
            'look': ('Formulas to check', '*Something looks wrong at these places, but the right correction is not certain.*'),
            'language': ('Typos and language', ''),
            'references': ('References and labels', ''),
            'patterns': ('Repeated patterns', '*The same slip in several places: one line each, with the places.*'),
            'optional': ('Optional', '*Suggestions: the original is acceptable.*')}

        def block(c):
            if c.get('look'):
                return 'look'
            if c.get('lane') == 'math':
                return 'math'
            if c.get('lines'):
                return 'patterns'
            if c.get('importance') == 'suggestion':
                return 'optional'
            return 'references' if c.get('kind') in ('cross_reference', 'reference') else 'language'
        out = []
        for name, (title, note) in blocks.items():
            chosen = sorted((c for c in items if block(c) == name), key=lambda c: (c['start_line'], c['end_line']))
            if not chosen:
                continue
            out += [f'### {title}', ''] + ([note, ''] if note else [])
            for c in chosen:
                place = '' if name == 'patterns' else f'{self._place(sid, c["start_line"], c["end_line"])}: '
                text = '- ' + place + (f'"{c["quote"]}" — ' if c['quote'] else '') + c['comment']
                out.append(text + (f' *Suggested:* {c["suggestion"]}' if c.get('suggestion') else ''))
            out.append('')
        return out

    def _render_typos(self):
        body, appendix = super()._render_typos()
        if not self._lanes_on():
            return body, appendix
        lanes = self._lanes_state()
        shown = self.state['presentation']
        count = lambda lane, test=lambda c: True: sum(bool(c.get('lane') == lane and test(c)) for c in shown)
        wrong = sum(bool(e.get('judged')) and not importance_of(e.get('verdict'), e['votes'] >= len(LANGUAGE_SAMPLES))
                    for e in lanes['edits'])
        how = (f' Rules (no model): {count("rule")} finding(s). Language: the paper read twice in small units with no '
               f'paper in front, {len(lanes["edits"])} proposed correction(s) given a second look, {wrong} left out as wrong. '
               f'Formulas and notation: read with the whole paper in front, {len(lanes["math"])} remark(s) checked one by one '
               f'against the paper: {count("math", lambda c: c.get("importance") == "error" and not c.get("rejected"))} '
               f'correction(s), {count("math", lambda c: c.get("look"))} place(s) to check, '
               f'{count("math", lambda c: c.get("rejected"))} removed.')
        appendix = [line.replace(' Every remark was then checked against the whole paper.', '') for line in appendix]
        if len(appendix) > 2:
            appendix[2] += how
        return body, appendix
