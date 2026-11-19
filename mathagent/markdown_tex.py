# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors.

"""Render a supplied Markdown document as LaTeX without rewriting its contents."""

import re
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.rules_block.table import escapedSplit, getLine, table
from markdown_it.tree import SyntaxTreeNode


_DELIMITERS = {r'\(': r'\)', r'\[': r'\]', '$$': '$$', '$': '$'}
_DISPLAY_ENVS = frozenset('equation align alignat gather multline flalign displaymath'.split())
_MATH_ENVS = _DISPLAY_ENVS | set('aligned alignedat gathered split cases matrix pmatrix bmatrix Bmatrix '
                                 'vmatrix Vmatrix smallmatrix array'.split())
_ENV = re.compile(r'\\(begin|end)\s*\{([a-zA-Z]+\*?)\}')
# Only these math commands may be sent to a host TeX engine. Everything else
# is still exported verbatim for the author to compile with their own preamble.
_PREVIEW_COMMANDS = frozenset((
    'alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa varkappa lambda mu nu xi pi varpi '
    'rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon '
    'Phi Psi Omega mathbb mathcal mathfrak mathrm mathbf mathit mathsf mathtt operatorname text frac dfrac tfrac '
    'sqrt overline underline hat widehat tilde widetilde bar dot ddot vec subset subseteq supset supseteq in '
    'notin ni le leq ge geq ne neq approx sim simeq equiv cong to mapsto rightarrow leftarrow leftrightarrow '
    'implies iff times otimes oplus cdot cdots ldots vdots ddots dots ast star circ pm mp partial nabla infty '
    'ell hbar Re Im sum prod int iint iiint oint lim limsup liminf sup inf max min det ker dim exp log ln '
    'sin cos tan pi cap cup bigcap bigcup wedge vee emptyset varnothing langle rangle lvert rvert lVert rVert '
    'vert Vert quad qquad forall exists setminus left right big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr '
    'Biggl Biggr binom overset underset not triangle triangleq perp parallel bullet colon top bot prime '
    'underbrace overbrace mathop limits nolimits textstyle displaystyle leqslant geqslant boldsymbol '
    'lbrace rbrace lfloor rfloor lceil rceil substack nonumber notag'
).split())
_TEXT_ESCAPES = str.maketrans({**{char: '\\' + char for char in '{}$&#_%'},
                               '\\': r'\textbackslash{}', '^': r'\textasciicircum{}',
                               '~': r'\textasciitilde{}'})
_PREAMBLE = r"""\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{amsmath,amssymb,textcomp,longtable,array}
\usepackage[hidelinks]{hyperref}
"""


def _math_end(src, start, limit, opening):
    """Exclusive end of an unescaped delimiter. Ordinary currency stays text."""
    close = _DELIMITERS[opening]
    after = start + len(opening)
    if opening == '$' and (after >= limit or src[after].isspace()):
        return None
    pos = after
    while (pos := src.find(close, pos, limit)) != -1:
        slashes = 0
        while pos - slashes > after and src[pos - slashes - 1] == '\\':
            slashes += 1
        end = pos + len(close)
        if not slashes % 2:
            if opening == '$' and (src[pos - 1].isspace() or (end < limit and src[end].isdigit())):
                return None
            return end
        pos = end
    return None


def _math_inline(state, silent):
    start = state.pos
    opening = state.src[start:start + 2]
    if opening not in _DELIMITERS:
        if state.src[start] != '$':
            return False
        opening = '$'
    end = _math_end(state.src, start, state.posMax, opening)
    if end is None:
        if opening != '$' and not silent:
            raise ValueError(f'Close the Markdown math delimiter {opening} with {_DELIMITERS[opening]}.')
        return False
    if not silent:
        token = state.push('math', '', 0)
        token.content = state.src[start:end]
        token.markup = opening
    state.pos = end
    return True


def _display_block(state, first, last, silent):
    line = getLine(state, first).strip()
    env = _ENV.match(line)
    if line in (r'\[', '$$'):
        close = _DELIMITERS[line]
    elif env and env[1] == 'begin' and env[2].rstrip('*') in _DISPLAY_ENVS:
        close = r'\end{' + env[2] + '}'
    else:
        return False
    if silent:
        return True
    stop = first
    while stop < last:
        if (stop > first or env) and getLine(state, stop).rstrip().endswith(close):
            break
        stop += 1
    if stop == last:
        raise ValueError(f'Close the displayed formula on line {first + 1} with {close}.')
    token = state.push('math', '', 0)
    token.content = state.getLines(first, stop + 1, state.blkIndent, False).strip()
    token.markup = '' if env else line
    token.map = [first, stop + 1]
    state.line = stop + 1
    return True


def _mask_pipes(line, marker):
    """The table parser splits rows before its inline parser can recognize math."""
    spans = []
    pos = 0
    for m in re.finditer(r'\\.|\$\$?|`+', line):
        if m.start() < pos:
            continue
        if m[0] in _DELIMITERS:
            end = _math_end(line, m.start(), len(line), m[0])
        elif m[0].startswith('`'):
            close = re.search(r'(?<!`)' + m[0] + r'(?!`)', line[m.end():])
            end = m.end() + close.end() if close else None
        else:
            continue
        if end:
            spans.extend((line[pos:m.start()], line[m.start():end].replace('|', marker)))
            pos = end
    return ''.join(spans) + line[pos:]


def _table(state, first, last, silent):
    if first + 1 >= last or '|' not in getLine(state, first):
        return False
    if not re.fullmatch(r'[\s:|\-]+', getLine(state, first + 1)):
        return False
    marker = state.env['pipe_marker']
    src = state.src
    changes = []
    line = first
    while line < last and getLine(state, line).strip():
        start, end = state.bMarks[line], state.eMarks[line]
        original = src[start:end]
        if '|' in original:
            masked = _mask_pipes(original, marker)
            if masked != original:
                changes.append((start, end, masked))
        line += 1
    if changes:
        # Retain every byte outside a changed row (including container prefixes)
        # and every character offset used by the block parser.
        parts, previous = [], 0
        for start, end, masked in changes:
            parts.extend((src[previous:start], masked))
            previous = end
        state.src = ''.join(parts) + src[previous:]
    before = len(state.tokens)
    try:
        matched = table(state, first, last, silent)
        if matched and not silent:
            tokens = state.tokens[before:]
            width = sum(t.type == 'th_open' for t in tokens)
            for t in tokens:
                if t.type == 'inline':
                    t.content = t.content.replace(marker, '|')
                if t.type == 'tr_open':
                    row = escapedSplit(getLine(state, t.map[0]).strip())
                    if row and not row[0]:
                        row.pop(0)
                    if row and not row[-1]:
                        row.pop()
                    if len(row) > width:
                        raise ValueError(f'Table row {t.map[0] + 1} has more cells than its header.')
        return matched
    finally:
        state.src = src


def _preview_note(formula, delimiter):
    math = formula[len(delimiter):-len(delimiter)] if delimiter else formula
    if '^^' in math:
        return 'The formula uses TeX character escapes.'
    for token in re.finditer(r'\\(?:[a-zA-Z]+|.)|[%#$]', math, re.S):
        command = token[0]
        if command[0] != '\\':
            return f'The formula contains a raw {command} character.'
        name = command[1:]
        if name in ('begin', 'end'):
            env = _ENV.match(math, token.start())
            if env and env[2].rstrip('*') in _MATH_ENVS:
                continue
        elif name in _PREVIEW_COMMANDS or (len(name) == 1 and name in '{}|,;:! %_&#$\\'):
            continue
        return f'The formula uses {command}, which requires a user-managed TeX build.'
    return None


def _code(text):
    return text.translate(_TEXT_ESCAPES).replace(' ', r'\ ')


def convert_markdown(text):
    """Return the body, standalone document, and optional reason to omit a host PDF preview."""
    if not text.strip():
        raise ValueError('The attached Markdown document is empty.')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text):
        raise ValueError('Remove the binary control characters from the Markdown document.')
    if re.match(r'\A---[ \t]*\n.+?\n(?:---|\.\.\.)[ \t]*(?:\n|$)', text, re.S) and re.match(
            r'\A---[ \t]*\n[\w-]+:', text):
        raise ValueError('Move YAML metadata into the Markdown body before converting it.')
    parser = MarkdownIt('commonmark').enable(['table', 'strikethrough'])
    parser.inline.ruler.before('escape', 'math', _math_inline)
    parser.block.ruler.before('fence', 'math', _display_block, {'alt': ['paragraph', 'blockquote', 'list']})
    parser.block.ruler.at('table', _table, {'alt': ['paragraph', 'reference']})
    marker = next((chr(ch) for ch in range(0xE000, 0xF900) if chr(ch) not in text), None)
    if marker is None:
        raise ValueError('The document uses every table placeholder character.')
    env = {'pipe_marker': marker}
    tree = SyntaxTreeNode(parser.parse(text, env))
    if any(label.startswith('^') for label in env.get('references', {})):
        raise ValueError('Move Markdown footnotes into the body before converting this document.')
    first = tree.children[0] if tree.children else None
    title = first if first and first.type == 'heading' and first.tag == 'h1' else None
    preview_note = None

    def children(node):
        return ''.join(render(child) for child in node.children)

    def render(node):
        nonlocal preview_note
        kind = node.type
        if kind in ('root', 'inline'):
            return children(node)
        if kind == 'text':
            return node.content.translate(_TEXT_ESCAPES)
        if kind == 'math':
            preview_note = preview_note or _preview_note(node.content, node.markup)
            return node.content + ('\n\n' if node.map is not None else '')
        if kind == 'heading':
            headings = ('section', 'subsection', 'subsubsection', 'paragraph', 'subparagraph', 'subparagraph')
            level = max(0, int(node.tag[1]) - (2 if title else 1))
            return '\\' + headings[level] + '*{' + children(node) + '}\n\n'
        if kind == 'paragraph':
            return children(node) + '\n\n'
        if kind in ('em', 'strong', 's'):
            command = {'em': 'emph', 'strong': 'textbf', 's': 'sout'}[kind]
            return '\\' + command + '{' + children(node) + '}'
        if kind == 'softbreak':
            return '\n'
        if kind == 'hardbreak':
            return r'\newline{}' + '\n'
        if kind == 'hr':
            return r'\par\noindent\rule{\linewidth}{0.4pt}\par' + '\n\n'
        if kind == 'blockquote':
            return '\\begin{quote}\n' + children(node) + '\\end{quote}\n\n'
        if kind in ('bullet_list', 'ordered_list'):
            env = 'enumerate' if kind == 'ordered_list' else 'itemize'
            start = int(node.attrs.get('start', 1))
            items = []
            for i, item in enumerate(node.children, start):
                label = f'[{i}.]' if kind == 'ordered_list' else ''
                items.append(r'\item' + label + '{} ' + children(item).strip() + '\n')
            return '\\begin{' + env + '}\n' + ''.join(items) + '\\end{' + env + '}\n\n'
        if kind == 'code_inline':
            return r'\texttt{' + _code(node.content) + '}'
        if kind in ('code_block', 'fence'):
            # Explicitly escaped text also handles a literal "\end{verbatim}" in the code.
            lines = [r'\strut{}' + _code(line.expandtabs(4)) for line in node.content.removesuffix('\n').split('\n')]
            return '\\begin{quote}\\ttfamily\\raggedright\n' + '\\\\{}\n'.join(lines) + '\n\\end{quote}\n\n'
        if kind == 'link':
            url = str(node.attrs['href'])
            if url.startswith('#') or node.attrs.get('title'):
                raise ValueError('Use an external link without a tooltip for this Markdown conversion.')
            url = quote(url, safe=":/?#[]@!$&'()*+,;=%~_-").replace('%', r'\%').replace('#', r'\#')
            return r'\href{' + url + '}{' + children(node) + '}'
        if kind == 'table':
            rows = [row for group in node.children for row in group.children]
            cols = []
            for cell in rows[0].children:
                align = cell.attrs.get('style', '')
                align = {'text-align:right': r'\raggedleft', 'text-align:center': r'\centering'}.get(align, r'\raggedright')
                cols.append('>{' + align + r'\arraybackslash}p{\dimexpr\linewidth/' +
                            str(len(rows[0].children)) + r'-2\tabcolsep\relax}')
            contents = [' & '.join(children(cell) for cell in row.children) + r' \\' for row in rows]
            contents.insert(1, r'\hline')
            return '\\begin{longtable}{' + ''.join(cols) + '}\n' + '\n'.join(contents) + '\n\\end{longtable}\n\n'
        if kind == 'image':
            raise ValueError('The Markdown contains an image. Supply its image files for an illustrated write-up.')
        if kind in ('html_inline', 'html_block'):
            raise ValueError('Convert embedded HTML to Markdown before requesting a formatting-only conversion.')
        raise ValueError(f'The Markdown element {kind} requires an editorial write-up.')

    front = ''
    nodes = tree.children
    if title is not None:
        front = r'\title{' + children(title) + '}\n' + r'\author{}\date{}\maketitle' + '\n\n'
        nodes = nodes[1:]
    content = ''.join(render(node) for node in nodes)
    body = content.rstrip() + '\n' if content else ''
    extra = '\\usepackage[normalem]{ulem}\n' if any(node.type == 's' for node in tree.walk()) else ''
    document = _PREAMBLE + extra + '\\begin{document}\n' + (front + content).rstrip() + '\n\\end{document}\n'
    return body, document, preview_note
