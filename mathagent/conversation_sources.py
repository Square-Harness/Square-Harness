# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Resolve source references from the human's current request and attachments."""
import re
import unicodedata

from .worker_errors import WorkerInputError


_NAMED = re.compile(r'(?<![\w./-])([\w-][\w.-]*(?:/[\w.-]+)*\.(?:tar\.gz|tgz|tar|gz|pdf|tex|md|txt|sty|cls|bib|py|json|ya?ml|toml|csv|png|jpe?g|webp))(?![\w-]|\.\w)', re.I)
_DOCUMENTS = ('.pdf', '.tex', '.md', '.txt', '.tar.gz', '.tgz', '.tar', '.gz')
_TEMPLATES = ('.bib', '.sty', '.cls')
_IMAGES = ('.png', '.jpg', '.jpeg', '.webp')
_DOCUMENT = (r'(?:articles?|papers?|manuscripts?|documents?|preprints?|(?:lecture )?notes?|lectures?|'
             r'handouts?|chapters?|texts?|sources?|manuscrits?|fichiers?|cours|polycopies?|chapitres?|textes?)')
_IMAGE = r'(?:images?|screenshots?|pictures?|captures?)'
_POINTER = (r'\b(?:this|that|attached|same|previous|earlier|the|my|your|these|those|all(?: the)?|'
            r'cet|cette|ce|le|la|les|au|aux|du|mon|ma|meme|precedent|ces|tous les|toutes les)\s+'
            r'(?:(?:same|previous(?:ly)?|earlier|attached|uploaded|meme|precedent)\s+)*')
_PDF_POINTER = re.compile(_POINTER + r'pdfs?\b')
_OLDER_PDF = re.compile(r'\b(?:earlier|previous(?:ly (?:sent|uploaded|attached))?|precedent|anterieur)\s+pdfs?\b|'
                        r'\bpdfs?\s+(?:precedent|anterieur|.{0,45}\b(?:earlier|previously|plus tot))\b')


def _instruction(query):
    text = re.sub(r'```.*?```|~~~.*?~~~|\\(?:input|include|includegraphics|addbibresource)\{[^}]*\}',
                  ' ', query, flags=re.S)
    return re.sub(r'(?m)^\s*>.*$', ' ', text)


def _prose(instruction):
    text = re.sub(r'`[^`\n]*`', ' ', instruction)
    return ''.join(c for c in unicodedata.normalize('NFKD', text).casefold() if not unicodedata.combining(c))


def references_previous_pdf(query):
    return bool(_PDF_POINTER.search(_prose(_instruction(query))))


def _named_sources(query, attachments, suffixes):
    # A dropped file may contain spaces. Prefer the whole recorded name over
    # the last word of that name, including when the user uses inline code.
    inline = list(re.finditer(r'`([^`\n]*)`', query))
    plain = re.sub(r'`[^`\n]*`', lambda m: ' ' * len(m[0]), query)
    explicit = [(m.start(), m.end(), m[1]) for m in inline if m[1] in attachments or _NAMED.fullmatch(m[1].replace(' ', '-'))]
    for name in attachments:
        if name not in plain:
            continue
        matches = re.finditer(r'(?<![\w./-])' + re.escape(name) + r'(?![\w/-]|\.\w)', plain)
        explicit.extend((match.start(), match.end(), name) for match in matches)
    explicit = sorted(explicit, key=lambda value: (value[0], -value[1]))
    full_names = []
    for start, end, name in explicit:
        if not full_names or start >= full_names[-1][1]:
            full_names.append((start, end, name))
    matches = [(m.start(), m.end(), m[0]) for m in _NAMED.finditer(plain)
               if not any(start < m.end() and m.start() < end for start, end, _ in full_names)]
    return list(dict.fromkeys(name for _, _, name in sorted(full_names + matches)
                              if name in attachments or name.lower().endswith(suffixes)))


def conversation_sources(query, supplied, transcript, *, mode=None, inherit=True,
                         suffixes=_DOCUMENTS + _TEMPLATES + _IMAGES, known_names=()):
    """Inherit only an unambiguous user attachment for a source-dependent follow-up.

    Worker output, workspace discovery and attached document contents cannot
    select another source. New explicit attachments always take precedence.
    """
    if supplied:
        return list(supplied)
    # Pasted source text and generated-proof follow-ups select their own text.
    proof = re.search(r'(?i)\b(?:proof|preuve|démonstration)\s*[.:]', query)
    statement = re.search(r'(?i)\b(?:lemma|theorem|proposition|lemme|théorème)\b', query)
    corrected = re.search(r'(?i)\b(?:corrected (?:proof|argument)|your (?:proof|argument)|(?:proof|argument) you (?:gave|wrote)|preuve corrigée?|ta preuve|votre preuve)\b', query)
    if proof and statement:
        return []
    history = [list(dict.fromkeys(item['files'])) for item in reversed(transcript)
               if item.get('role') == 'user' and item.get('files')]
    instruction = _instruction(query)
    attached = dict.fromkeys(name for group in [known_names, *history] for name in group)
    named = _named_sources(instruction, attached, suffixes)
    if named or not inherit:
        return named
    if corrected:
        return []
    if re.search(r'(?i)\b(?:previous|last|above|précédente?|ci-dessus)\s+(?:proof|result|preuve|résultat)\b', query):
        return []
    text = _prose(instruction)
    target = bool(re.search(r'\b(?:theorem|lemma|proposition|corollary|theoreme|lemme)\s+(?:\d+(?:\.\d+)*|[a-z])\b', text))
    plural = bool(re.search(r'\b(?:these|those|all(?: the)?|ces|tous les|toutes les)\s+'
                            r'(?:pdfs?|' + _DOCUMENT + r')\b', text))
    if _PDF_POINTER.search(text):
        suffixes = ('.pdf',)
        if not plural and _OLDER_PDF.search(text):
            alternatives = [name for name in attached if name.lower().endswith(suffixes)]
            if len(alternatives) > 1:
                raise WorkerInputError('Which earlier PDF should I use? Name one of: ' + ', '.join(alternatives) + '.',
                                       code='ambiguous_source', details={'sources': alternatives})
    elif re.search(_POINTER + _IMAGE + r'\b', text):
        suffixes = _IMAGES
    elif (target or mode in ('referee', 'detailed_review')
          or re.search(r'\b(?:its|son|sa|ses)\s+(?:(?:first|main|next|last|premier|premiere|principal|principale)\s+)?'
                       r'(?:theorem|lemma|proposition|corollary|result|theoreme|lemme|corollaire|resultat)\b', text)
          or re.search(_POINTER + r'(?:' + _DOCUMENT + r'|proof|result|preuve|resultat)\b', text)):
        suffixes = _DOCUMENTS
    else:
        return []
    for group in history:
        sources = [name for name in group if name.lower().endswith(suffixes)]
        if not sources:
            continue
        if len(sources) == 1 or plural:
            if mode == 'writeup':
                return [name for name in group if name in sources or name.lower().endswith(_TEMPLATES)]
            return sources
        raise WorkerInputError('Which source should I use: ' + ', '.join(sources) + '?',
                               code='ambiguous_source', details={'sources': sources})
    return []
