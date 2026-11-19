# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Source checker of write-ups: deterministic findings on a compiled LaTeX document, with no model call.

`check(document, body_line, log, material, kind)` returns findings `{category, line, rule, message, excerpt}`:

- BLOCKER: the text cannot go into a paper as it is (a LaTeX error, an unresolved reference, a TODO, a draft
  notice, an equation number typed by hand, text that talks about its source, generated mathematics between
  % BEGIN COMPOSED and % END COMPOSED that the review has not checked, an exam's points that are missing or do
  not add up to the total asked for);
- MATH: mathematics typeset wrongly ($$, Unicode symbols, ASCII relations, operators in italics, words
  without \\text, ...);
- PROSE: leftovers of rough notes or Markdown, hand-made structure, positional references;
- LAYOUT: lines that run more than 5pt into the margin, from the log;
- FIDELITY: numbers in the mathematics that the material does not contain (a signal, never a blocker).

`log_warnings(log, body_line)` lists every warning of the log, classified as fix, hold or list (writeup's
`tidy` phase fixes, its gates hold).

Lines are lines of `document`. Only the body is read (from `body_line` to \\end{document}), so the
controller's preamble and its "Points to check" block are never findings. Comments, code listings,
mathematics and the arguments of \\ref, \\label, \\cite... are blanked before the prose rules, keeping
positions. These are heuristics: a finding is a point to look at, and the gate (`GATE`) says which ones
keep a document a draft.
"""
import re

CATEGORIES = ('BLOCKER', 'MATH', 'PROSE', 'LAYOUT', 'FIDELITY')
# The gate each blocking rule belongs to (writeup.WriteupRunner._gates); PROSE and FIDELITY never block.
GATE = {'latex-error': 'G1', 'undefined-reference': 'G2', 'undefined-citation': 'G2', 'duplicate-label': 'G2',
        'todo': 'G4', 'draft-notice': 'G4', 'composed': 'G4', 'part-missing': 'G6', 'overfull': 'G7'}
LAYOUT_POINTS = 5.0

# Formulas referred to by position or by a number typed by hand (writeup.reference_problems uses it too).
POSITIONAL = re.compile(r'\b(?:the\s+(?:equation|display|formula|identity|inequality|estimate)\s+above|the\s+(?:above|previous|preceding)\s+'
                        r'(?:equation|display|formula|identity|inequality|estimate)|(?:equation|formula|identity|inequality|estimate)s?'
                        r'\s*~?\(\d+(?:\.\d+)*\))', re.I)
_HAND_NUMBER = re.compile(r'\b(?:by|in|from|see|eq\.|(?:equation|formula|identity|inequality|estimate)s?)\s*~?\(\d+(?:\.\d+)*\)', re.I)

_LITERAL = re.compile(r'\\begin\{(verbatim\*?|Verbatim\*?|lstlisting\*?|minted\*?|tikzpicture|tikzcd)\}.*?\\end\{\1\}', re.S)
_INLINE_CODE = re.compile(r'\\(?:verb|lstinline)(?![A-Za-z])\*?([^\s\w{])(?:(?!\1)[^\r\n])*\1')
_COMMENT = re.compile(r'(?<!\\)%[^\n]*')
_BIBLIOGRAPHY = re.compile(r'\\begin\{thebibliography\}.*?\\end\{thebibliography\}', re.S)
_MATH = re.compile(r'(?<!\\)\\\[(.*?)\\\]|\\begin\{(equation|align|gather|multline|flalign|alignat|displaymath|math)\*?\}(.*?)\\end\{\2\*?\}'
                   r'|(?<![\\$])\$(?!\$)((?:\\.|[^$\\])+?)\$|(?<!\\)\\\((.*?)\\\)', re.S)  # \\[2pt] is a line break
_ARGUMENT = re.compile(r'\\(?:eqref|ref|cref|Cref|autoref|pageref|(?:no|paren|text|auto|foot|super)?cite[pt]?|label|input|include'
                       r'|includegraphics|url|href|begin|end|usepackage|bibliography|bibliographystyle)\*?'
                       r'(?:\s*\[[^\]]*\])*\s*\{[^{}]*\}')
_IN_MATH_TEXT = re.compile(r'\\(?:text|mathrm|operatorname|textup|textnormal|mbox|label|tag|intertext|textit|textbf|mathbf'
                           r'|mathsf|mathit|mathcal|mathbb|mathfrak|eqref|ref)\*?\s*\{[^{}]*\}')

_OPERATORS = 'sin|cos|tan|cot|sinh|cosh|tanh|log|ln|exp|max|min|sup|inf|lim|liminf|limsup|det|dim|ker|arg|deg|gcd|Pr'
_SHORTHAND = (r'wlog|w\.l\.o\.g\.?|s\.t\.|cts|pf|ptwise|w/o|w/|wrt|cv|cvg|seq|fct|fcts|thm|lem|prop|bdry|nbhd|RHS|LHS|IBP|C-S')
_UNICODE_MATH = '≤≥→⇒⇔←↦∞∈∉×·≠≈≡∑∏∫∂∇∀∃∅⊂⊆⊃⊇∪∩∘√−±αβγδεζηθικλμνξπρστφχψωΓΔΘΛΞΠΣΦΨΩ‖ℝℕℤℚℂ'
_HAND_HEADING = (r'\\textbf\{\s*(?:Theorem|Lemma|Proposition|Corollary|Definition|Remark|Proof|Exercise|Example|Claim'
                 r'|Question|Part|Th[ée]or[èe]me|Lemme|Preuve|D[ée]monstration|Exercice|D[ée]finition|Remarque|Partie'
                 r'|Aufgabe|Frage|Domanda|Pregunta)\b')
_SOURCE_TALK = re.compile(r"\b(?:the|these|those|your|author's|user's|original) (?:rough |draft |source )?"
                          r'(?:notes|source|material|draft|conversation|transcript)\b|\bthe (?:user|author) (?:asks?|writes?|says?|notes?)\b', re.I)
_DRAFT_NOTICE = re.compile(r'Points to check before using this draft|\bthis is a draft\b|\b(?:preliminary|rough) draft\b', re.I)
_OVERFULL = re.compile(r'Overfull \\hbox \(([\d.]+)pt too wide\) (?:(?:in paragraph|in alignment) at lines (\d+)--\d+'
                       r'|detected at line (\d+)|has occurred while \\output is active)')
# -file-line-error also points into listed scripts and nested source files.
LOG_FILE_ERROR = re.compile(r'^(?:\./)?((?![./])[^:\r\n]+\.[A-Za-z0-9]+):(\d+): (.+)$', re.M)
_LOG_REFERENCE = re.compile(r"(Reference|Citation) [`'‘]([^'’\n]+)['’] on page \S+ undefined(?: on input line (\d+))?")
_LOG_LABEL = re.compile(r"Label [`'‘]([^'’\n]+)['’] multiply defined")
_LOG_GLYPH = re.compile(r'Missing character: There is no (\S+)')
_NUMBER = re.compile(r'(?<![\w.{_^\\])\d+(?:[.,]\d+)?(?![\w}])')
_PER_RULE = 12  # findings kept per rule: enough to fix, not a flood


def _blank(match):
    return re.sub(r'[^\n]', ' ', match.group(0))


def finding(category, rule, message, excerpt='', line=None):
    return {'category': category, 'line': line, 'rule': rule, 'message': message,
            'excerpt': ' '.join(str(excerpt).split())[:90]}


def check(document, body_line, log='', material='', kind=None, points_total=None):
    """Findings on the body of `document` (from line `body_line`), its compile `log` and the `material`. `kind` is
    the outline's kind of document; for an exam, `points_total` is the total the request asks for, if any."""
    out, kept = [], {}

    def add(category, rule, message, excerpt='', position=None, line=None):
        if kept.get(rule, 0) >= _PER_RULE:
            return
        kept[rule] = kept.get(rule, 0) + 1
        out.append(finding(category, rule, message, excerpt,
                           line if position is None else document.count('\n', 0, position) + 1))

    lines = document.split('\n')
    start = len('\n'.join(lines[:max(0, body_line - 1)])) + (1 if body_line > 1 else 0)
    end = document.find('\\end{document}', start)
    end = len(document) if end == -1 else end
    # Everything outside the body is blanked; positions (and so line numbers) stay those of the document.
    text = re.sub(r'[^\n]', ' ', document[:start]) + document[start:end] + re.sub(r'[^\n]', ' ', document[end:])
    text = _INLINE_CODE.sub(_blank, _LITERAL.sub(_blank, text))
    body = _COMMENT.sub(_blank, text)                                   # comments blanked
    prose = _ARGUMENT.sub(_blank, _BIBLIOGRAPHY.sub(_blank, _MATH.sub(_blank, body)))
    def around(source, m, before=25, after=25):
        """The match with some context, cut at whole words."""
        left, right = source[max(0, m.start() - before):m.start()], source[m.end():m.end() + after]
        left = left if m.start() <= before else re.sub(r'^\S*\s?', '', left)
        right = right if m.end() + after >= len(source) else re.sub(r'\s?\S*$', '', right)
        return left + m.group(0) + right

    # -- blockers in the text
    for m in re.finditer(r'(?<!\\)%\s*TODO\b[^\n]*', text):
        add('BLOCKER', 'todo', 'TODO comment left in the source', m.group(0), m.start())
    for m in _DRAFT_NOTICE.finditer(prose):
        add('BLOCKER', 'draft-notice', 'draft notice printed in the text', around(prose, m, 0, 0), m.start())
    for m in _HAND_NUMBER.finditer(prose):
        add('BLOCKER', 'hand-number', 'equation number typed by hand (label the formula and use \\eqref)', m.group(0), m.start())
    if kind != 'summary':
        for m in _SOURCE_TALK.finditer(prose):
            add('BLOCKER', 'source-talk', 'the text talks about its source instead of stating the mathematics',
                around(prose, m, 0, 40), m.start())

    # -- generated mathematics (checked by the review, not by the source checker) and an exam's points
    begins = list(re.finditer(r'(?<!\\)%\s*BEGIN COMPOSED\b[^\n]*', text))
    ends = list(re.finditer(r'(?<!\\)%\s*END COMPOSED\b', text))
    if len(begins) != len(ends):
        add('BLOCKER', 'composed-markers', 'unbalanced % BEGIN COMPOSED and % END COMPOSED comments', '',
            (begins or ends)[0].start())
    for m in begins:
        following = next((line for line in body[m.end():m.end() + 400].split('\n') if line.strip()), '')
        add('BLOCKER', 'composed', 'generated mathematics not checked yet (between % BEGIN COMPOSED and % END COMPOSED)',
            following, m.start())
    if kind == 'exam':
        number = lambda value: float(value.replace(',', '.'))
        points = r'\\points\s*\{\s*(\d+(?:[.,]\d+)?)\s*\}'
        values = [number(v) for v in re.findall(points, body)]
        if not values:
            add('BLOCKER', 'points-missing', 'no question carries its points (\\points{n})')
        elif points_total is not None and abs(sum(values) - points_total) > 1e-6:
            sums = [sum(number(v) for v in re.findall(points, part)) for part in re.split(r'\\begin\{exercise\}', body)[1:]]
            add('BLOCKER', 'points-total', f'the points add up to {sum(values):g}, not the {points_total:g} the request asks for',
                'exercises: ' + ' + '.join(f'{v:g}' for v in sums) if len(sums) > 1 else '')

    # -- mathematics
    for m in re.finditer(r'\$\$', body):
        add('MATH', 'display-dollars', 'plain-TeX $$ display (use \\[ \\] or an equation environment)', '', m.start())
        break
    seen = set()
    for m in re.finditer('[' + _UNICODE_MATH + ']', body):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            add('MATH', 'unicode-math', f'Unicode character {m.group(0)} in the source (use its LaTeX command)',
                around(body, m, 20, 20), m.start())
    for m in re.finditer(r'(?<![<\-=!])(<=|>=|->|=>|!=|<->)(?![>=\-])', body):
        add('MATH', 'ascii-relation', f'ASCII {m.group(1)} instead of a LaTeX symbol', around(body, m, 15, 15), m.start())
    for m in re.finditer(r'\\mathrm\s*\{\s*([NZQR])\s*\}', body):
        add('MATH', 'roman-set', f'number set \\mathrm{{{m.group(1)}}} in roman (use \\mathbb{{{m.group(1)}}})', m.group(0), m.start())
    for m in _MATH.finditer(body):
        inner = next(g for g in (m.group(1), m.group(3), m.group(4), m.group(5)) if g is not None)
        clean = _IN_MATH_TEXT.sub(' ', inner)
        operator = re.search(r'(?<![\\A-Za-z])(' + _OPERATORS + r')(?![A-Za-z])', clean)
        if operator:
            add('MATH', 'operator', f'"{operator.group(1)}" without a backslash is typeset as a product of letters', inner, m.start())
        words = re.search(r'(?<![\\A-Za-z])[A-Za-z]{3,}\s+[A-Za-z]{2,}(?![A-Za-z])', clean)
        if words:
            add('MATH', 'math-words', 'words inside mathematics without \\text', words.group(0), m.start())
        if re.search(r'(?<!\\)\.\.\.', inner):
            add('MATH', 'math-dots', '... instead of \\dots in mathematics', inner, m.start())

    # -- prose
    for m in re.finditer(r'\*\*[^*\n]+\*\*|^[ \t]*#{1,4} \S|(?<![`\\])`(?![`\s])[^`\'\n]{1,80}`(?!`)', prose, re.M):
        add('PROSE', 'markdown', 'Markdown leftover', m.group(0), m.start())
    for m in re.finditer(r'(?<![\w\\])(?:' + _SHORTHAND + r')(?![\w])', prose):
        add('PROSE', 'shorthand', 'shorthand of rough notes', around(prose, m), m.start())
    for m in re.finditer(_HAND_HEADING, body):
        add('PROSE', 'hand-heading', 'heading typed by hand instead of an environment', m.group(0), m.start())
    for m in re.finditer(r'\b(?:Theorem|Lemma|Proposition|Corollary|Definition|Remark|Section|Figure|Table|Exercise'
                         r'|Th[ée]or[èe]me|Lemme|Exercice) \\ref\b', body):
        add('PROSE', 'ref-space', 'breakable space before \\ref (use ~)', m.group(0), m.start())
    for m in re.finditer(r'"[A-Za-zÀ-ÿ][^"\n]{0,60}"', prose):
        add('PROSE', 'straight-quotes', "straight double quotes (use ``...'')", m.group(0), m.start())
    for m in re.finditer(r'\\section\*?\{[^{}]*\}\s*(?:\\label\{[^{}]*\}\s*)?\\(?:sub)?section\b', body):
        add('PROSE', 'empty-section', 'a section with no text before its first subsection', m.group(0), m.start())
    for m in POSITIONAL.finditer(prose):
        if not re.search(r'\(\d', m.group(0)):  # a number typed by hand is a blocker (above)
            add('PROSE', 'positional', 'formula referred to by position (label it and use \\eqref)', m.group(0), m.start())

    # -- the compile log
    for name, number, message in LOG_FILE_ERROR.findall(log):
        if 'Fatal error occurred' not in message:
            add('BLOCKER', 'latex-error', 'LaTeX error: ' + message.strip(), '' if name == 'document.tex' else name,
                line=int(number) if name == 'document.tex' else None)
    if not kept.get('latex-error'):
        for message in re.findall(r'^! (.+)$', log, re.M):
            add('BLOCKER', 'latex-error', 'LaTeX error: ' + message.strip())
    reported = set()
    for kind_name, key, number in _LOG_REFERENCE.findall(log):
        if number and int(number) < body_line:
            continue  # in the controller's block of points to check, not in the text
        if (kind_name, key) not in reported:
            reported.add((kind_name, key))
            add('BLOCKER', 'undefined-' + kind_name.lower(), f'undefined {kind_name.lower()} {key}', key,
                line=int(number) if number else None)
    for key in dict.fromkeys(_LOG_LABEL.findall(log)):
        add('BLOCKER', 'duplicate-label', f'label {key} defined twice', key)
    for glyph in dict.fromkeys(_LOG_GLYPH.findall(log)):
        add('MATH', 'missing-glyph', f'character {glyph} is missing from the font', glyph)
    page_head = False
    for m in _OVERFULL.finditer(log):
        width, number = float(m.group(1)), m.group(2) or m.group(3)
        if width <= LAYOUT_POINTS or (number and int(number) < body_line) or (not number and page_head):
            continue  # small, in the preamble or the controller's block, or the same page head on another page
        page_head = page_head or not number
        line = int(number) if number else None
        excerpt = lines[line - 1] if line and line <= len(lines) else 'the page head or foot (a long title?)'
        add('LAYOUT', 'overfull', f'a line runs {width:g}pt into the margin', excerpt, line=line)

    # -- numbers the material does not contain
    if material.strip():
        known, seen = set(_NUMBER.findall(material)), set()
        for m in _MATH.finditer(body):
            inner = re.sub(r'\\(?:label|ref|eqref|tag|hspace|vspace|kern)\*?\{[^{}]*\}', ' ', m.group(0))
            for number in _NUMBER.findall(inner):
                if number not in known and number not in seen and number not in ('0', '1', '2'):
                    seen.add(number)
                    add('FIDELITY', 'number', f'the number {number} is not in the material', inner, m.start())
    order = {category: index for index, category in enumerate(CATEGORIES)}
    return sorted(out, key=lambda f: (order[f['category']], f['line'] or 0))


# Every warning of the compile log, classified (log_warnings). `fix`: the controller has an automatic fix (the
# `tidy` phase of writeup); `hold`: the document stays a draft while it is there (G2, G7, G8); `list`: the report
# shows it. A fix that fails leaves the warning in the next log, where its gate holds the draft.
_WARNING_KINDS = (
    ('citation', r"(?:LaTeX|Package natbib|Package biblatex) Warning: Citation [`'‘]([^'’\n]+)['’]"),
    ('reference', r"LaTeX Warning: Reference [`'‘]([^'’\n]+)['’] on page \S+ undefined"),
    ('duplicate', r"LaTeX Warning: Label [`'‘]([^'’\n]+)['’] multiply defined"),
    ('summary', r'LaTeX Warning: There were (?:undefined references|multiply-defined labels)'),
    ('rerun', r'LaTeX Warning: Label\(s\) may have changed|Rerun to get|Package rerunfilecheck Warning'),
    ('class', r'Class (\S+) Warning: (.*)'),
    ('float', r"LaTeX Warning: [`'‘]([^'’\n]+)['’] float specifier changed"),
    ('float', r'LaTeX Warning: (Float too large) for page'),
    ('overfull', r'Overfull \\hbox \(([\d.]+)pt too wide\)'),
    ('layout', r'(?:Overfull|Underfull) \\[hv]box'),
    ('pdfstring', r'Package hyperref Warning: (Token not allowed in a PDF string)'),
    ('links', r'destination with the same identifier \(name\{([^}]*)\}\)'),
    ('font', r'LaTeX Font Warning: (.*)'),
    ('font', r'Missing character: There is no (\S+)'),
    ('package', r'Package (\S+) Warning: (.*)'),
    ('other', r'LaTeX Warning: (.*)'))
_WARNING_ACTION = {'reference': 'fix', 'citation': 'fix', 'duplicate': 'fix', 'float': 'fix', 'overfull': 'fix',
                   'pdfstring': 'fix', 'class': 'hold'}
_LOG_LINE = re.compile(r'on input line (\d+)|at lines (\d+)--\d+|detected at line (\d+)')


def log_warnings(log, body_line=1):
    """Every distinct warning of the compile log, as {kind, key, message, line, count, action}, in the order of the
    log. `line` is a line of the document (None when the log gives none). An overfull line is fixable only in the
    body (from `body_line`); one in the head, or without a line, is held (G7 reads it from `check`); one of at most
    LAYOUT_POINTS is only listed."""
    out, seen = [], {}
    lines = log.split('\n')
    for index, line in enumerate(lines):
        text = line.strip()
        # A package continues its warning on lines that start with its name in parentheses.
        for following in lines[index + 1:index + 4]:
            if not re.match(r'\([A-Za-z@.-]+\)\s', following.strip()):
                break
            text += ' ' + re.sub(r'^\([A-Za-z@.-]+\)\s+', '', following.strip())
        for kind, pattern in _WARNING_KINDS:
            m = re.search(pattern, text)
            if not m:
                continue
            if kind == 'summary':
                break
            number = _LOG_LINE.search(text)
            at = int(next(g for g in number.groups() if g)) if number else None
            key = ' '.join(g for g in m.groups() if g)[:120] if m.groups() else m.group(0)
            action = _WARNING_ACTION.get(kind, 'list')
            if kind == 'overfull':
                if float(m.group(1)) <= LAYOUT_POINTS:
                    kind, action = 'layout', 'list'
                elif at is None or at < body_line:
                    action = 'hold' if at is None else 'list'
                key = str(at)
            elif kind == 'float' and key == 'Float too large':
                key = f'too large at {at}'
            elif kind in ('class', 'package', 'font', 'other', 'pdfstring'):
                key = re.sub(r'[;,]?\s*(?:reported )?on input line \d+\.?', '', key)[:120]
            record = seen.get((kind, key))
            if record:
                record['count'] += 1
            else:
                record = seen[(kind, key)] = {'kind': kind, 'key': key, 'message': text[:300], 'line': at, 'count': 1,
                                              'action': action}
                out.append(record)
            break
    return out


def counts(findings):
    """{category: number of findings}, in the order of CATEGORIES."""
    return {category: n for category in CATEGORIES if (n := sum(f['category'] == category for f in findings))}


def gate(found):
    """The gate a finding fails: G1 compile, G2 references, G4 draft markers, G7 layout, G3 any other BLOCKER or
    MATH finding; None for PROSE and FIDELITY."""
    if found['rule'] in GATE:
        return GATE[found['rule']]
    return 'G3' if found['category'] in ('BLOCKER', 'MATH') else None
