# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ENPC / SquareHarness

"""Resolve source references from the human's current request and attachments."""
import re
import unicodedata

from .worker_errors import WorkerInputError


_NAMED = re.compile(r'(?<![\w./-])([\w-]+(?:/[\w.-]+)*\.(?:pdf|tex|md|txt|sty|cls|bib|png|jpe?g|webp))(?![\w-]|\.\w)', re.I)


def conversation_sources(query, supplied, transcript):
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
    instruction = re.sub(r'```.*?```|`[^`]*`|\\(?:input|include|includegraphics|addbibresource)\{[^}]*\}', '', query, flags=re.S)
    named = list(dict.fromkeys(_NAMED.findall(instruction)))
    if named:
        return named
    if corrected:
        return []
    if re.search(r'(?i)\b(?:previous|last|above|précédente?|ci-dessus)\s+(?:proof|result|preuve|résultat)\b', query):
        return []
    text = ''.join(c for c in unicodedata.normalize('NFKD', query).casefold()
                   if not unicodedata.combining(c))
    target = bool(re.search(r'\b(?:theorem|lemma|proposition|corollary|theoreme|lemme)\s+(?:\d+(?:\.\d+)*|[a-z])\b', text))
    pointer = bool(re.search(r'\b(?:this|that|attached|same|previous|the|cet|cette|ce|le|la|meme|precedent)\s+'
                             r'(?:article|paper|manuscript|document|proof|result|preuve|resultat|image|screenshot|picture|capture)\b', text))
    plural = bool(re.search(r'\b(?:these |those |all (?:the )?|ces |tous les |toutes les )(?:articles|papers|manuscripts|documents)\b', text))
    if not (target or pointer or plural):
        return []
    previous = next((item.get('files', []) for item in reversed(transcript)
                     if item.get('role') == 'user' and item.get('files')), [])
    previous = list(dict.fromkeys(previous))
    if len(previous) <= 1:
        return previous
    if plural:
        return previous
    raise WorkerInputError('Which source should I use: ' + ', '.join(previous) + '?',
                           code='ambiguous_source', details={'sources': previous})
