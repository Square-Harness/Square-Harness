# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Typos a program can find: the model-free part of the typo check.

A model that reads a paper misses about half of its small slips, and the ones below it misses most often are
the ones a rule finds every time: "the \\cref{alg:x}" (printed "the Algorithm 1"), a display that ends with
\\cdot where a period was meant, a comma before a new sentence, "allows to conclude", "component wise", a word
typed twice, two spellings of one term. Each rule here is meant to be right almost always, so its findings need
no second opinion; what needs judgement (most grammar, every formula) is left to the model.

lint(lines) works on the numbered lines of one source. Findings have the fields of a reading comment
(start_line, end_line, quote, kind, importance, comment, suggestion) plus `rule`, and `lines` when the finding
is a pattern that occurs in several places.
"""

import bisect
import re
import shutil
import subprocess

DISPLAY_ENVS = ('equation', 'align', 'gather', 'multline', 'eqnarray', 'flalign', 'alignat', 'displaymath')
OPAQUE_ENVS = ('tikzpicture', 'verbatim', 'lstlisting', 'minted', 'thebibliography', 'filecontents', 'comment')
REF_COMMANDS = r'(?:eq|c|C|auto|page|name|v|V)?ref|[cC]refrange|label|cite[a-zA-Z]*\*?|nocite|input|include|' \
               r'includegraphics|bibliography|bibliographystyle|usepackage|url|hypertarget|begin|end|texttt|verb|path|' \
               r'newcommand|renewcommand|providecommand|DeclareMathOperator|color|textcolor|vspace|hspace'
REPEATS = 3  # from this many identical remarks, one remark lists the places
OPEN, FILL, CLOSE, ARG = '\u27e6', '\u00b7', '\u27e7', '\u2022'  # math is masked as ⟦···⟧, arguments as •••

# Printed names of theorem-like environments when the source has no \newtheorem for them.
ENV_KINDS = {'theorem': 'Theorem', 'thm': 'Theorem', 'theo': 'Theorem', 'lemma': 'Lemma', 'lem': 'Lemma',
             'proposition': 'Proposition', 'prop': 'Proposition', 'corollary': 'Corollary', 'cor': 'Corollary',
             'coro': 'Corollary', 'definition': 'Definition', 'defi': 'Definition', 'defn': 'Definition',
             'remark': 'Remark', 'rem': 'Remark', 'rema': 'Remark', 'rmk': 'Remark', 'example': 'Example',
             'algorithm': 'Algorithm', 'figure': 'Figure', 'table': 'Table', 'assumption': 'Assumption',
             'hypothesis': 'Hypothesis', 'conjecture': 'Conjecture', 'claim': 'Claim'}
REF_WORDS = ('Theorem', 'Lemma', 'Proposition', 'Corollary', 'Definition', 'Remark', 'Section', 'Figure', 'Table',
             'Algorithm', 'Appendix', 'Example', 'Assumption', 'Conjecture', 'Claim')
ABBREVIATIONS = {'e', 'g', 'i', 'cf', 'resp', 'etc', 'al', 'vs', 'fig', 'figs', 'eq', 'eqs', 'a', 's', 'w', 'r', 't',
                 'p', 'pp', 'no', 'vol', 'ed', 'eds', 'thm', 'prop', 'sect', 'sec', 'approx', 'const', 'st', 'ch',
                 'th', 'lem', 'cor', 'def', 'rem', 'ex', 'viz', 'ca', 'dr', 'prof', 'mr', 'ms', 'n', 'b', 'd', 'h'}

# Mathematical words an English dictionary does not have; a word outside it is still reported only when one
# letter away from a dictionary word.
MATH_WORDS = set('''adjoint adjointness affine annuli ansatz argmax argmin asymptotics autonomous bijection bijective
bijectivity bilinear boundedness cadlag cardinality coercive coercivity cofinite cokernel colimit commutator
compactness conormal convexity coprime countably covariance cumulant definedness diffeomorphic diffeomorphism
discretization discretisation disjointness eigen eigencouple eigenfunction eigenfunctions eigengap eigenmode
eigenpair eigenspace eigenvalue eigenvalues eigenvector eigenvectors embeddings entropic equicontinuous ergodic
ergodicity extrema foci functor functorial generically geodesic geodesics holomorphic homeomorphism homogenization
homotopy hyperball hyperbolic hypercube hyperplane hypersurface hypoelliptic hypoellipticity infimum injective
injectivity integrability integrable integrand integrands invertibility invertible isometry isomorphism
isotropic laplacian lemmata lipschitz loci maxima measurability minima moduli mesoscopic metastability metastable metastabilities monotonicity
multiindex nondegenerate nonnegative nonlinear nonlocal nontrivial nonzero numerics orthonormal parametrisation
parametrization parametrix pointwise polytope positivity posteriori precompact priori prefactor preimage pseudocode pushforward
quasimode quasimodes radii resolvent rescaled rescaling scalings semiclassical semidefinite semigroup semigroups
semilinear sesquilinear smoothness sobolev solvability stochastically subadditive subcritical subdomain
subharmonic sublinear submanifold submartingale subsequence subsingular subsolution supremum surjective
surjectivity symplectic tensorial timestep topologies tori unsummed variational wavelet wellposedness'''.split())

PREFIXES = re.compile(r'(?:sub|super|non|pre|post|quasi|semi|multi|hyper|hypo|pseudo|anti|inter|intra|micro|macro|meso|'
                      r'eigen|poly|iso|homo|hetero|auto|equi|over|under|co|re|un|bi|tri)')

# -ise / -ize: words that only look like a British spelling.
NOT_BRITISH = re.compile(r'(?:wise|[^aeiou]rise|arise|raise|praise|precise|concise|exercise|promise|premise|'
                         r'comprise|surprise|advise|devise|revise|supervise|improvise|disguise|expertise|franchise|'
                         r'paradise|treatise|noise|poise|demise|incise|excise|despise|otherwise|merchandise|compromise|advertise|'
                         r'surmise|televise|bruise|cruise|guise|chastise)[sd]?$|'
                         r'(?:aris|rais|prais|[^aeiou]ris|^ris|promis|advis|devis|revis|supervis|exercis|compromis|advertis|'
                         r'disguis|nois|pois|despis|bruis|cruis)(?:ing|ed)$')
BRITISH_PAIRS = (('behaviour', 'behavior'), ('neighbour', 'neighbor'), ('colour', 'color'), ('favour', 'favor'),
                 ('centre', 'center'), ('modelling', 'modeling'), ('modelled', 'modeled'), ('labelled', 'labeled'),
                 ('labelling', 'labeling'), ('analyse', 'analyze'), ('analysed', 'analyzed'), ('fulfil', 'fulfill'))

PHRASES = (
    (r'\b(allow|allows|allowing|permit|permits|permitting|enable|enables|enabling)\s+to\s+(?!be\b)([a-z]+)', 'grammar', 'typo',
     '"{0} to {1}" has no object: in English one allows someone to do something.', '{0} us to {1}'),
    (r'\bat\s+the\s+(first|second|leading|lowest|next)\s+order\b', 'grammar', 'typo',
     '"at the {0} order" is not idiomatic.', 'at {0} order'),
    (r'\b(component|point|piece|pair|element|entry|coordinate|term|row|column)\s+wise\b', 'typo', 'typo',
     '"{0} wise" is one word.', '{0}wise'),
    (r'\bnon\s+([a-z]{3,})\b', 'typo', 'typo', '"non" is a prefix, not a word.', 'non-{0}'),
    (r'\b(denote|denotes|denoted|denoting)\s+(\u27e6[^\u27e7]*\u27e7)\s+(the|a|an)\b', 'grammar', 'typo',
     'One denotes an object by a symbol: "by" is missing.', None),
    (r'(?:(?<=[.;:]\s)|(?<=[.;:]\n)|\b(?:then|hence|thus|therefore|so|and)\s+)([Ii]t)\s+exists\s+(a|an|some|\u27e6)', 'grammar', 'typo',
     '"it exists" for "there exists".', None),
    (r'\bthere\s+exists\s+(two|three|four|several|many|infinitely\s+many|finitely\s+many|constants|positive\s+constants)\b',
     'grammar', 'typo', '"there exists" with a plural.', 'there exist {0}'),
    (r'\b(first|second|third|higher|leading|lower|zeroth)\s+order\s+(asymptotic|asymptotics|expansion|expansions|term|terms|'
     r'approximation|correction|corrections|condition|conditions|derivative|derivatives|operator|scheme|method|accuracy|'
     r'differential|elliptic|system|optimality)\b', 'typo', 'typo', 'A compound adjective before a noun takes a hyphen.', '{0}-order {1}'),
    (r'\binformations\b', 'grammar', 'typo', '"information" has no plural.', 'information'),
    (r'\b(depend|depends|depending|depended)\s+of\b', 'grammar', 'typo', 'One depends on something.', '{0} on'),
    (r'\bin\s+function\s+of\b', 'grammar', 'typo', '"in function of" is not English.', 'as a function of'),
    (r'\bthe\s+same\s+(\w+\s+)?than\b', 'grammar', 'typo', '"the same … than" for "the same … as".', None),
    (r'\bsimilar\s+(\w+\s+)?as\b', 'grammar', 'suggestion', '"similar … as" for "similar to".', None),
    (r'\bmore\s+(easier|simpler|larger|smaller|better|higher|lower|faster|weaker|stronger)\b', 'grammar', 'typo',
     'Double comparative.', '{0}'),
    (r'\ban\s+other\b', 'typo', 'typo', '"another" is one word.', 'another'),
    (r'\bin\s+the\s+contrary\b', 'grammar', 'typo', '"in the contrary" for "on the contrary".', 'on the contrary'),
    (r'\b(?:we|us|to)\s+precise\b', 'grammar', 'typo', '"precise" is not a verb.', None),
    (r'\bin\s+a\s+(first|second)\s+time\b', 'grammar', 'typo', '"in a {0} time" is not English.', None),
    (r'\b(LHS|RHS|l\.h\.s\.?|r\.h\.s\.?)(?![\w}])', 'clarity', 'suggestion', 'Abbreviation in running text.',
     lambda m: 'left-hand side' if m.group(1).lower().startswith('l') else 'right-hand side'),
)


def _words(text, limit=12):
    return ' '.join(text.split()[:limit])


def _distance_one(a, b):
    """True when b is a from one insertion, deletion, substitution or transposition."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:] or (a[i:i + 2] == b[i:i + 2][::-1] and a[i + 2:] == b[i + 2:])
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    return longer[i + 1:] == shorter[i:]


class _Source:
    """The source with comments blanked (raw) and with math and non-prose arguments masked (prose): the two
    strings have the length of the original, so an offset in one is an offset in the other."""

    def __init__(self, lines, tex=True):
        text = '\n'.join(lines)
        self.tex = tex
        self.starts = [0]
        for line in lines:
            self.starts.append(self.starts[-1] + len(line) + 1)
        if tex:
            text = re.sub(r'(?<!\\)%[^\n]*', lambda m: ' ' * len(m.group()), text)
        self.raw = text
        prose = list(text)
        self.displays = []

        def blank(a, b, first, fill, last):
            for i in range(a, b):
                if prose[i] != '\n':
                    prose[i] = fill
            if b - a >= 2:
                for i, mark in ((a, first), (b - 1, last)):
                    if prose[i] != '\n':
                        prose[i] = mark

        if tex:
            begin = text.find('\\begin{document}')
            if begin > 0:
                blank(0, begin, ARG, ARG, ARG)
            end = text.find('\\end{document}')
            if end > 0:
                blank(end, len(text), ARG, ARG, ARG)
            for env in OPAQUE_ENVS:
                for m in re.finditer(r'\\begin\{%s\*?\}.*?\\end\{%s\*?\}' % (env, env), text, re.S):
                    blank(m.start(), m.end(), ARG, ARG, ARG)
            pattern = (r'\\begin\{(%s)(\*?)\}.*?\\end\{\1\2\}|\\\[.*?\\\]|(?<!\\)\$\$.*?(?<!\\)\$\$'
                       % '|'.join(DISPLAY_ENVS))
            for m in re.finditer(pattern, text, re.S):
                if prose[m.start()] not in (ARG, FILL, OPEN):
                    blank(m.start(), m.end(), OPEN, FILL, CLOSE)
                    self.displays.append((m.start(), m.end()))
            masked = ''.join(prose)
            for m in re.finditer(r'\\\((?:[^\n]|\n(?!\s*\n))*?\\\)|(?<![\\$])\$(?!\$)(?:[^$\n]|\\\$|\n(?!\s*\n))+?(?<!\\)\$', masked):
                if FILL not in m.group() and ARG not in m.group():
                    blank(m.start(), m.end(), OPEN, FILL, CLOSE)
            masked = ''.join(prose)
            for m in re.finditer(r'\\(?:%s)\s*(?:\[[^\]\n]*\]\s*)*\{([^{}]*)\}' % REF_COMMANDS, masked):
                blank(m.start(1), m.end(1), ARG, ARG, ARG)
        self.prose = ''.join(prose)

    def line(self, offset):
        return bisect.bisect_right(self.starts, offset)

    def finding(self, a, b, kind, importance, comment, suggestion, rule):
        first = self.line(a)
        stop = min(b, self.starts[first] - 1)  # a quote stays on its first line
        return {'start_line': first, 'end_line': self.line(max(a, b - 1)), 'quote': _words(self.raw[a:stop]),
                'kind': kind, 'importance': importance, 'comment': comment, 'suggestion': suggestion or '',
                'rule': rule}


def _phrases(src):
    for pattern, kind, importance, comment, suggestion in PHRASES:
        for m in re.finditer(pattern, src.prose):
            groups = [' '.join(g.split()) for g in m.groups('')]
            fix = suggestion(m) if callable(suggestion) else suggestion.format(*groups) if suggestion else ''
            yield src.finding(m.start(), m.end(), kind, importance, comment.format(*groups), fix, 'phrase')


def _vowel_sound(word):
    w = word.lower()
    if re.match(r'(?:hour|honest|hono|heir)', w):
        return True
    if re.match(r'(?:uni(?!mp|nt|nf|nh|ns|nv|d[^i]|ll|rr)|use|usu|uti|ubi|ura|uro|uto|eu|ewe|one\b|once\b|one-)', w):
        return False
    return w[0] in 'aeiou'


def _articles(src):
    for m in re.finditer(r'\b([Aa]n?)\s+([A-Za-z][a-z]{2,})\b', src.prose):
        article, word = m.group(1), m.group(2)
        if word[0].isupper() or word[0] == 'h' or src.prose[m.end():m.end() + 1] == '.':
            continue  # a name (an Euler scheme), "an historic", an abbreviation read letter by letter
        vowel = _vowel_sound(word)
        if (article.lower() == 'a') == vowel:
            fix = ('an' if vowel else 'a') if article[0].islower() else ('An' if vowel else 'A')
            yield src.finding(m.start(), m.end(), 'grammar', 'typo', f'"{article} {word}": wrong article.',
                              f'{fix} {word}', 'article')


def _doubled(src):
    for m in re.finditer(r'\b([A-Za-z]{2,})\s+\1\b', src.prose):
        if m.group(1).lower() not in {'that', 'had'} and '\n\n' not in m.group():
            yield src.finding(m.start(), m.end(), 'typo', 'typo', f'"{m.group(1)}" is written twice.', m.group(1), 'doubled-word')


def _spacing(src):
    for m in re.finditer(r'(?<=[\w\u27e7}])[ \t]+([,;.])(?=\s)', src.prose):
        if '&' not in src.raw[src.starts[src.line(m.start()) - 1]:m.start()]:
            yield src.finding(max(m.start() - 20, src.starts[src.line(m.start()) - 1]), m.end(), 'typo', 'typo',
                              f'Space before "{m.group(1)}".', '', 'space-before-punctuation')
    for m in re.finditer(r'(?<=[a-z0-9,\u27e7}])\s*\((See|Cf\.?)\b', src.prose):
        yield src.finding(m.start(1) - 1, m.end(), 'typo', 'typo', f'"{m.group(1)}" is capitalised in the middle of a sentence.',
                          '(' + m.group(1).lower(), 'capital-in-parenthesis')
    for m in re.finditer(r'\b([A-Za-z]+)([.!?])[ \t]*\n?[ \t]*([a-z]{2,})\b', src.prose):
        if m.group(2) == '.' and (m.group(1).lower() in ABBREVIATIONS or len(m.group(1)) == 1
                                  or src.prose[m.start() - 1:m.start()] == '.'):
            continue
        if m.group(3) in {'e', 'g', 'i', 'cf', 'resp', 'etc'} or src.prose[m.end():m.end() + 1] == '.':
            continue
        yield src.finding(m.start(3), m.end(), 'typo', 'typo', 'A sentence starts with a lowercase letter.',
                          m.group(3).capitalize(), 'lowercase-sentence-start')


_PEEL = re.compile(r'(?:\s+|\\label\{[^}]*\}|\\(?:nonumber|notag|qedhere)\b|\\end\{[a-zA-Z*]+\}|\\right\s*[.)\]|]|\\right\\[a-zA-Z}|]+'
                   r'|\\\]|\$\$|\\,|\\;|\\quad|\\qquad|\\!|~|\\\\(?:\[[^\]]*\])?)$')
_THEOREM_END = r'\\end\{|\\begin\{(?!%s|itemize|enumerate|cases|aligned)|\\(?:sub)*section|\\paragraph|\\item\b|\\qed' % '|'.join(DISPLAY_ENVS)


def _display_ends(src):
    """What each display ends with and what follows it: (start, end, tail, offset of the tail, next)."""
    out = []
    for a, b in src.displays:
        body = src.raw[a:b]
        while True:
            peeled = _PEEL.sub('', body)
            if peeled == body:
                break
            body = peeled
        after = src.prose[b:b + 400]
        m = re.match(r'(\s*)(?:\\(?:noindent|indent|par|medskip|smallskip|bigskip)\b\s*)*', after)
        rest = after[m.end():]
        if re.match(r'[A-Z][a-z]+\b', rest):
            nxt = 'capital'
        elif re.match(r'[a-z]+\b', rest):
            nxt = 'lower'
        elif re.match(_THEOREM_END, rest) or not rest.strip():
            nxt = 'end'
        else:
            nxt = 'other'
        out.append((a, b, body, a + len(body), nxt))
    return out


def _displays(src):
    ends = _display_ends(src)
    punctuated = sum(1 for *_, body, _, _ in ends if body.endswith(('.', ',', ';', ':')))
    habit = len(ends) >= 8 and punctuated >= 0.6 * len(ends)  # the authors punctuate their displays
    for a, b, body, tail, nxt in ends:
        at = max(a, tail - 40)
        if re.search(r'\\cdot$', body):
            yield src.finding(tail - 5, tail, 'typo', 'typo', 'The display ends with \\cdot, printed as a centred dot: '
                              'a period or a comma was meant.', '.' if nxt in ('capital', 'end') else ',', 'display-cdot')
        elif body.endswith(',') and nxt == 'capital':
            yield src.finding(at, tail, 'typo', 'typo', 'The display ends with a comma but a new sentence follows.',
                              'End the display with a period.', 'display-comma')
        elif body.endswith('.') and not body.endswith(('\\dots.', '...')) and nxt == 'lower':
            word = re.match(r'\s*(?:\\\w+\s*)*([a-z]+)', src.prose[b:b + 400]).group(1)
            yield src.finding(at, tail, 'typo', 'typo', f'The display ends with a period but the sentence goes on ("{word} …").',
                              'End the display with a comma, or start the next sentence with a capital.', 'display-period')
        elif habit and nxt in ('capital', 'end') and not body.endswith(('.', ',', ';', ':', '?')):
            yield src.finding(at, tail, 'typo', 'typo', 'The display ends a sentence without a period.',
                              'Add a period.', 'display-no-period')


def _paragraph_ends(src):
    """A paragraph of prose that stops without a period, before a blank line or the start of a proof."""
    for m in re.finditer(r'([^\n]*[A-Za-z0-9\u2022\u27e7]\}?)[ \t]*\n(?=[ \t]*\n\s*(\S[^\n]*)|[ \t]*\\begin\{proof\})', src.prose):
        line, nxt = m.group(1), m.group(2) or '\\begin{proof}'
        stripped = line.strip()
        if (stripped.startswith(('\\', ARG, OPEN, '&')) or len(re.findall(r'[A-Za-z]{2,}', line)) < 6
                or re.match(r'\\begin\{(?:%s)|\\\[|\$\$|[a-z]|\\item|%s|%s' % ('|'.join(DISPLAY_ENVS), OPEN, ARG), nxt)
                or line.count('{') != line.count('}') or re.search(r'\\(?:caption|title|author|section|subsection|item)\b', line)):
            continue
        yield src.finding(m.end(1) - min(len(stripped), 60), m.end(1), 'typo', 'typo',
                          'The paragraph ends without a period.', 'Add a period.', 'paragraph-no-period')


def _cref_articles(src, kinds):
    for m in re.finditer(r'\b([Tt]he|[Aa]n?)\s+\\[cC]ref\{([^},]*)\}', src.raw):
        if src.prose[m.start()] in (ARG, FILL, OPEN):
            continue
        label = m.group(2).strip()
        kind = kinds.get(label)
        if kind == 'Equation' or (kind is None and re.match(r'eqn?\b|eq[:_.]', label)):
            continue
        printed = f'{m.group(1)} {kind} 1' if kind else f'{m.group(1)} Theorem 1'
        yield src.finding(m.start(), m.end(), 'cross_reference', 'typo',
                          f'\\cref prints the name of what it refers to, so this reads "{printed}".',
                          f'Remove "{m.group(1)}".', 'article-before-cref')


def label_kinds(text, extra=''):
    """Printed kind of every \\label of a source: the theorem-like environment or sectioning command it sits in.
    extra is more source (a preamble or style file) where \\newtheorem may be declared."""
    names = dict(ENV_KINDS)
    for m in re.finditer(r'\\newtheorem\*?\{(\w+)\}(?:\[\w+\])?\{([^}]*)\}', extra + '\n' + text):
        names[m.group(1)] = m.group(2).strip()
    kinds, stack, last_heading = {}, [], None
    token = re.compile(r'\\begin\{(\w+)\*?\}|\\end\{(\w+)\*?\}|\\((?:sub)*section|chapter|appendix)\b|\\label\{([^}]*)\}')
    appendix = False
    for m in token.finditer(text):
        if m.group(1):
            stack.append((m.group(1), m.start()))
        elif m.group(2):
            while stack and stack.pop()[0] != m.group(2):
                pass
        elif m.group(3):
            appendix = appendix or m.group(3) == 'appendix'
            last_heading = m.end()
        else:
            label = m.group(4).strip()
            env = next((e for e, _ in reversed(stack) if e in names or e in DISPLAY_ENVS), None)
            if env in DISPLAY_ENVS:
                kind = 'Equation'
            elif env:
                kind = names[env]
            elif last_heading is not None and m.start() - last_heading < 300:
                kind = 'Appendix' if appendix else 'Section'
            else:
                kind = None
            kinds.setdefault(label, kind)
    return kinds


def _labels(src, kinds, complete):
    seen = {}
    for m in re.finditer(r'\\label\{([^}]*)\}', src.raw):
        label = m.group(1).strip()
        if label in seen:
            yield src.finding(m.start(), m.end(), 'cross_reference', 'error',
                              f'The label "{label}" is defined twice (first on line {seen[label]}): references to it are ambiguous.',
                              'Rename one of the two labels.', 'duplicate-label')
        else:
            seen[label] = src.line(m.start())
    for m in re.finditer(r'\\(?:eq|c|C|auto|page)?ref\{([^}]*)\}', src.raw):
        for label in m.group(1).split(','):
            if complete and label.strip() and label.strip() not in seen:
                yield src.finding(m.start(), m.end(), 'cross_reference', 'error',
                                  f'The reference to "{label.strip()}" has no \\label: it prints as "??".', '', 'undefined-reference')
    for m in re.finditer(r'\b(%s)s?[~ ]+\\ref\{([^},]*)\}' % '|'.join(REF_WORDS), src.raw):
        word, kind = m.group(1), kinds.get(m.group(2).strip())
        if kind and kind in REF_WORDS and kind != word and {kind, word} != {'Section', 'Appendix'}:
            yield src.finding(m.start(), m.end(), 'cross_reference', 'error',
                              f'This reference is called a {word}, but the label is on a {kind}.',
                              m.group().replace(word, kind, 1), 'reference-kind')


def _places(src, offsets):
    lines = sorted({src.line(o) for o in offsets})
    return lines, ', '.join(map(str, lines))


def _pattern(src, offsets, minority, majority, comment, kind='typo'):
    lines, shown = _places(src, offsets)
    finding = src.finding(offsets[0], offsets[0] + len(minority), kind, 'suggestion',
                          comment + f' "{minority}" on line(s) {shown}.', majority, 'variant')
    finding['lines'] = lines
    return finding


def _variants(src):
    words = {}
    for m in re.finditer(r"[A-Za-z]+(?:-[A-Za-z]+)*", src.prose):
        words.setdefault(m.group().lower(), []).append(m.start())
    # British and American spellings of the same paper.
    british, american = {}, {}
    for word, places in words.items():
        m = re.match(r'(\w{3,}?)i([sz])(e|es|ed|ing|ation|ations)$', word)
        if m and not (m.group(2) == 's' and NOT_BRITISH.search(word)):
            if m.group(2) == 's':
                british[word] = places
            elif m.group(2) == 'z':
                american[word] = places
    for uk, us in BRITISH_PAIRS:
        for word, places in words.items():
            if word.startswith(uk) and not word.startswith(us):
                british[word] = places
            elif word.startswith(us) and not (uk.startswith(us) and word.startswith(uk)):
                american[word] = places
    if british and american:
        few, many, name = (british, american, 'British') if sum(map(len, british.values())) <= sum(map(len, american.values())) \
            else (american, british, 'American')
        offsets = sorted(o for places in few.values() for o in places)
        lines, shown = _places(src, offsets)
        finding = src.finding(offsets[0], offsets[0] + 1, 'typo', 'suggestion',
                              f'The paper mixes British and American spellings: {name} forms ({", ".join(sorted(few))}) '
                              f'on line(s) {shown}; the other spelling is used {sum(map(len, many.values()))} times.',
                              'Use one spelling throughout.', 'variant')
        finding['quote'] = src.raw[offsets[0]:].split()[0] if src.raw[offsets[0]:].split() else ''
        finding['lines'] = lines
        yield finding
    # One term hyphenated here and written as one word there.
    for word, places in sorted(words.items()):
        if '-' in word and word.count('-') == 1 and word.replace('-', '') in words and all(len(p) > 2 for p in word.split('-')):
            closed = word.replace('-', '')
            few, many, offsets = (word, closed, places) if len(places) <= len(words[closed]) else (closed, word, words[closed])
            yield _pattern(src, offsets, few, many, f'Two spellings of one term: "{many}" ({len(words[many])} times) and')
    # A capitalised name with and without its final s ("Walk on Sphere", "Walk on Spheres").
    names = {}
    for m in re.finditer(r'\b[A-Z][a-z]+(?:[ \t]+(?:on|of|in|for|and|the))+[ \t]+[A-Z][a-z]+\b', src.prose):
        names.setdefault(re.sub(r's$', '', ' '.join(m.group().split()).lower()), {}).setdefault(' '.join(m.group().split()), []).append(m.start())
    for forms in names.values():
        if len(forms) == 2:
            (few, offsets), (many, others) = sorted(forms.items(), key=lambda kv: len(kv[1]))
            if len(others) > len(offsets):
                yield _pattern(src, offsets, few, many, f'Two forms of one name: "{many}" ({len(others)} times) and')
    # One letter applied to the same object in two fonts (D(L) and \mathcal{D}(L)).
    plain, styled = {}, {}
    argument = r'\s*\(\s*(\\[a-zA-Z]+(?:\{[^{}]*\})?)'
    for m in re.finditer(r'\\math(cal|bb|scr|frak|bf|rm)\{([A-Za-z])\}' + argument, src.raw):
        styled.setdefault((m.group(2), m.group(3)), []).append((m.start(), m.group(1)))
    for m in re.finditer(r'(?<![\\a-zA-Z{_^])([A-Z])' + argument, src.raw):
        if src.prose[m.start()] in (FILL, OPEN):
            plain.setdefault((m.group(1), m.group(2)), []).append(m.start())
    for key in sorted(set(plain) & set(styled)):
        offsets = plain[key] if len(plain[key]) <= len(styled[key]) else [o for o, _ in styled[key]]
        font = styled[key][0][1]
        few = key[0] if offsets is plain[key] else f'\\math{font}{{{key[0]}}}'
        many = f'\\math{font}{{{key[0]}}}' if offsets is plain[key] else key[0]
        lines, shown = _places(src, offsets)
        finding = src.finding(offsets[0], offsets[0] + len(few) + 1, 'notation', 'suggestion',
                              f'Two notations for the same object: {few}({key[1]}…) on line(s) {shown} and {many}({key[1]}…) elsewhere.',
                              f'Use {many} throughout.', 'variant')
        finding['lines'] = lines
        yield finding
    # "Theorem 2" and "theorem 2".
    for word in REF_WORDS:
        upper = [m.start() for m in re.finditer(r'\b%s[~ ]+(?:\\(?:eq)?ref\b|\d)' % word, src.raw)]
        lower = [m.start() for m in re.finditer(r'(?<![.!?]\s)(?<![.!?]\n)\b%s[~ ]+(?:\\(?:eq)?ref\b|\d)' % word.lower(), src.raw)]
        if lower and len(upper) >= 2 * len(lower):
            yield _pattern(src, lower, word.lower(), word, f'"{word}" is capitalised before a number {len(upper)} times, but not')


def _spelling(src, run=None):
    """Misspellings: dictionary misses that are one letter away from a dictionary word. Needs aspell."""
    run = run or _aspell
    text = re.sub(r'\\[a-zA-Z]+|[%s%s%s%s]' % (OPEN, FILL, CLOSE, ARG), lambda m: ' ' * len(m.group()), src.prose)
    found = {}
    for m in re.finditer(r"[A-Za-z\u00c0-\u00ff]{4,}", text):
        if m.group().isascii():  # a word with an accent is a name or another language
            found.setdefault(m.group(), []).append(m.start())
    counts = {}
    for word, places in found.items():
        counts[word.lower()] = counts.get(word.lower(), 0) + len(places)
    glued = lambda o, w: text[o - 1:o] in ("'", '"', '^', '`', '~', '}', '{') or text[o + len(w):o + len(w) + 1] in ("'", '{', '`')
    candidates = [w for w in found if w.lower() not in MATH_WORDS and not w.isupper()
                  and not any(glued(o, w) for o in found[w])]  # "Cl\'ement" is not the word "ment"
    try:
        suggestions = run(candidates)
        # "subleading", "nonconvex": a prefix on a dictionary word is the authors' term, not a misspelling.
        stems = {w: w[len(m.group()):] for w in suggestions if (m := PREFIXES.match(w.lower())) and len(w) - len(m.group()) >= 4}
        stems.update({w: w[:-1] for w in suggestions if w.endswith('s') and len(w) > 4})  # "normals": a plural
        unknown_stems = run(sorted(set(stems.values()))) if stems else {}
    except (OSError, subprocess.SubprocessError):
        return
    suggestions = {w: g for w, g in suggestions.items() if w not in stems or stems[w] in unknown_stems}
    for word, guesses in sorted(suggestions.items()):
        offsets = found[word]
        before = [src.prose[:o].rstrip(' \t') for o in offsets]
        sentence_start = all(not b.strip() or b.endswith('\n\n') or b.rstrip()[-1] in '.!?:}' + CLOSE + ARG for b in before)
        if word[0].isupper() and not sentence_start:
            continue  # a name
        fix = next((g for g in guesses if ' ' not in g and '-' not in g and "'" not in g
                    and (g[0].islower() or word[0].isupper())  # not a proper name for a common word
                    and _distance_one(g.lower(), word.lower())), None)
        if not fix or (counts[word.lower()] > 2 and counts.get(fix.lower(), 0) <= counts[word.lower()]):
            continue  # a technical term, or the authors' own consistent word
        fix = fix.capitalize() if word[0].isupper() else fix
        finding = src.finding(offsets[0], offsets[0] + len(word), 'typo', 'typo', f'"{word}" is not in the dictionary.', fix, 'spelling')
        if len(offsets) > 1:
            finding['lines'] = sorted({src.line(o) for o in offsets})
        yield finding


def _aspell(words):
    """{word: suggestions} for the words aspell does not know; {} when aspell is not installed."""
    binary = shutil.which('aspell')
    if not binary or not words:
        return {}
    out = None
    for language in ('en_US', 'en_GB'):  # a word is unknown only when both dictionaries miss it
        done = subprocess.run([binary, '-a', '--lang=' + language, '--sug-mode=fast'], input='\n'.join('^' + w for w in words),
                              capture_output=True, text=True, timeout=60)
        if done.returncode or not done.stdout.startswith('@'):
            continue  # this dictionary is not installed
        unknown = {}
        for line in done.stdout.splitlines():
            if line.startswith('&'):
                head, _, tail = line.partition(':')
                unknown[head.split()[1]] = [g.strip() for g in tail.split(',')]
            elif line.startswith('#'):
                unknown[line.split()[1]] = []
        out = unknown if out is None else {w: g for w, g in out.items() if w in unknown}
    return out or {}


def looks_english(text):
    words = re.findall(r'[a-zà-ÿ]+', text.lower())
    english = sum(w in {'the', 'of', 'and', 'is', 'that', 'we', 'for', 'with', 'this', 'are'} for w in words)
    french = sum(w in {'le', 'la', 'les', 'des', 'est', 'et', 'une', 'que', 'dans', 'pour', 'nous'} for w in words)
    return english > 2 * french


def lint(lines, *, tex=True, preamble='', complete=True, spelling=True):
    """Model-free typo findings for the numbered lines of one source, in the order of the text.

    tex: the lines are LaTeX source (else plain text or Markdown: only the rules on words apply). preamble: more
    source in which theorem environments may be declared. complete: every \\label of the paper is in these lines
    (else undefined references are not reported)."""
    src = _Source(lines, tex)
    if not looks_english(src.prose):
        return []
    findings = list(_phrases(src)) + list(_articles(src)) + list(_doubled(src)) + list(_spacing(src))
    if tex:
        kinds = label_kinds(src.raw, preamble)
        complete = complete and not re.search(r'\\(?:input|include|subfile)\b', src.prose)
        findings += list(_displays(src)) + list(_paragraph_ends(src)) + list(_cref_articles(src, kinds))
        findings += list(_labels(src, kinds, complete)) + list(_variants(src))
    if spelling and (tex or '\f' not in src.raw):
        findings += list(_spelling(src))
    seen, out, groups = set(), [], {}
    for finding in sorted(findings, key=lambda f: (f['start_line'], f['rule'])):
        key = (finding['start_line'], finding['rule'], finding['quote'])
        if key not in seen and finding['quote']:
            seen.add(key)
            out.append(finding)
            groups.setdefault((finding['rule'], finding['comment']), []).append(finding)
    for same in groups.values():
        if len(same) >= REPEATS and 'lines' not in same[0]:  # the same slip many times is one remark with its places
            same[0]['lines'] = [f['start_line'] for f in same]
            same[0]['comment'] += f' {len(same)} times: lines {", ".join(str(f["start_line"]) for f in same)}.'
            out = [f for f in out if f not in same[1:]]
    return out
