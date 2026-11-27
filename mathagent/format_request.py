# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Recognize requests whose entire instruction is a Markdown to LaTeX conversion."""
import re
import unicodedata
from pathlib import Path


def _fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(c)).replace('’', "'")


_POLITE = re.compile(r"^(?:(?:please|can you|could you|would you|s'il (?:te|vous) plait|"
                     r"(?:peux|pourrais)-tu|(?:pouvez|pourriez)-vous)[ ,]+)+")
_QUALIFIER = r'(?:juste?|only|simply|entirely|directly|verbatim|integralement|simplement)'
_SOURCE = (r'(?:the|this|my|ce|cet|mon|le|la|son|attached|uploaded|following|original|whole|entire|full|'
           r'from|markdown|\.?md|document|file|review|survey|manuscript|text|notes|fichier|revue|texte|'
           r'joint|ci-joint|it|that|your|last|previous|preceding|above|answer|response|result|literature|'
           r'just|gave|wrote|tu|viens|de|reponse|precedente?|sourcefile)')
_TARGET = r'(?:(?:a|an|un|standalone|complete|full) ){0,3}(?:latex|tex|\.tex)'
_SOURCE_PHRASE = rf'{_SOURCE}(?: {_SOURCE}){{0,9}}'
_INSTRUCTION = re.compile(
    rf'(?:{_QUALIFIER} )?(?:convert|export|rewrite|write|transcribe|typeset|render|turn|put|translate|'
    rf'convertis(?:sez)?|convertir|reecris|reecrire|reecrivez|transcris|transcrivez|mets|mettez) '
    rf'(?:(?:{_SOURCE}|{_QUALIFIER}) ){{0,10}}(?:in|into|to|as|en|au format|au) '
    rf'{_TARGET}'
    r'(?: (?:file|document|format|source))?\b')
_VERSION_REQUEST = re.compile(
    rf'(?:give me|(?:can|could|may) i (?:have|get)) (?:'
    rf'{_TARGET} (?:version|copy|file|document) of {_SOURCE_PHRASE}|'
    rf'{_SOURCE_PHRASE} (?:in|as) {_TARGET}(?: (?:file|document|format|source))?)\b')
_MATERIAL = (r"(?:(?:the|its|my|original|all|any|every|of|le|la|les|aux|mes|a la|l') ?){0,4}"
             r'(?:math(?:s|ematics)?|equations?|formul(?:as?|ae|es?)|mathematiques?|wording|text|texte|'
             r'contents?|contenu|prose|notation|structure|everything|anything|rien)')
_MATERIALS = rf'{_MATERIAL}(?:(?:, |,? and | et ){_MATERIAL}){{0,4}}'
_PRESERVE = re.compile(
    rf"(?:(?:don'?t|do not|never) (?:touch|change|edit|modify|alter)|"
    rf"without (?:touching|changing|editing|modifying|altering)|preserv(?:e|ing)|"
    rf"(?:ne )?(?:touche|change|modifie) pas|sans (?:toucher|changer|modifier)|"
    rf"conserve[rz]?|garde[rz]?) {_MATERIALS}"
    rf"|(?:keep|keeping|leave|leaving) {_MATERIALS} (?:exactly )?(?:unchanged|intact|the same|as[- ]is|as (?:it is|they are))"
    r"|(?:exactly )?as[- ]is|verbatim|with(?:out any| no) (?:other )?(?:changes|edits)"
    r"|no (?:other )?(?:changes|edits)")
_SEPARATOR = re.compile(r'[\s(),;.!?]+(?:and |et )?')


def markdown_conversion_request(query, files, *, style_mode='auto'):
    """Accept one Markdown file and only conversion/preservation clauses.

    Unknown instructions (including editorial edits and section selection) use
    the ordinary writer. Only the user's words and the supplied name are read.
    """
    if (not isinstance(query, str) or len(files) != 1
            or style_mode not in ('auto', 'none')):
        return False
    name = files[0]
    if not isinstance(name, str) or Path(name).suffix.lower() != '.md':
        return False
    text = _fold(query.strip())
    for path in dict.fromkeys((name, Path(name).name)):
        literal = re.escape(_fold(path))
        text = re.sub(r'(?<![\w./-])(?:`' + literal + r'`|"' + literal + r'"|' + literal
                      + r')(?![\w/-]|\.\w)', 'sourcefile', text)
    text = _POLITE.sub('', ' '.join(text.split()))
    text = re.sub(r"[ ,]+(?:please|s'il (?:te|vous) plait)[.!?\s]*$", '', text)
    if re.match(r'(?:write|rewrite) (?:in|as|into|to) ', text):
        return False  # A new document in LaTeX needs an objective or an identified source.
    conversion = _INSTRUCTION.match(text) or _VERSION_REQUEST.match(text) or re.match(r'(?:markdown|\.?md|sourcefile) to (?:latex|tex)\b', text)
    if conversion is None:
        return False
    end = conversion.end()
    while end < len(text):
        separator = _SEPARATOR.match(text, end)
        if not separator:
            return False
        end = separator.end()
        if end == len(text):
            break
        clause = _PRESERVE.match(text, end) or _INSTRUCTION.match(text, end)
        if not clause:
            return False
        end = clause.end()
    return True


_COMPILE_FOLLOWUP = re.compile(
    r"(?:(?:please|can you|could you|would you) )*(?:"
    r"(?:re)?compile(?: (?:it|this|that|(?:(?:the|this|that|my|your|last|previous) )?"
    r"(?:latex |tex )?(?:document|write[ -]?up|file)))?(?: (?:again|to pdf|as a pdf))?|"
    r"(?:can't you |can you |could you )?(?:re)?use (?:the )?write[ -]?up (?:agent|worker) to compile(?: it)?|"
    r"(?:re)?compile[rz]? (?:le|ce document|le document))"
    r"(?:[, ]+please)?[.!? ]*")


def compile_followup_request(query):
    """An explicit build of a previously delivered document, without edits or new inputs."""
    return isinstance(query, str) and bool(_COMPILE_FOLLOWUP.fullmatch(' '.join(_fold(query).split())))


def previous_markdown_request(query, *, style_mode='auto'):
    """A pure conversion specifically pointing to an existing answer, independent of any filenames."""
    if not markdown_conversion_request(query, ['previous-answer.md'], style_mode=style_mode):
        return False
    text = _fold(query)
    return (not re.search(r'\b(?:attached|uploaded|fichier|joint)\b|\bfile\b.*?\b(?:in|into|to|as|en)\b', text)
            and bool(re.search(r'\b(?:it|(?:version|copy|file|document) of (?:this|that)(?=[.!?\s]*$)|'
                               r'(?:this|that)(?=\s+(?:in|into|as|to)\b)|(?:the|your|previous|last|preceding|above|this|that) '
                               r'(?:answer|response|result|(?:literature )?review|survey|text)|(?:la|ta) reponse)\b', text)))


def conversion_source(state):
    """Look through the pinned job inputs, ignoring the automatically added chat and reference catalog."""
    if state.get('variant') or state.get('images') or state.get('inline_material'):
        return None
    sources = [s for s in state['sources'] if not s.get('catalog')
               and not (s['id'] == 'M0' and s['path'] == 'conversation.md')]
    if len(sources) != 1:
        return None
    source = sources[0]
    if not re.fullmatch(r'M[1-9]\d*', source['id']) or source.get('source_archive'):
        return None
    style = (state.get('style_choice') or {}).get('mode', 'auto')
    return source['id'] if markdown_conversion_request(state['goal'], [source['path']], style_mode=style) else None


def can_recover_previous_markdown(state, snapshot):
    """An old conversation-only job must already contain the exact selected answer in its pinned record."""
    if (not snapshot or state.get('kind') != 'writeup' or state.get('phase') not in ('outline', 'skeleton', 'draft')
            or state.get('variant') or state.get('images') or state.get('inline_material')
            or state.get('document') or state.get('body') or state.get('outputs')
            or not previous_markdown_request(state['goal'], style_mode=(state.get('style_choice') or {}).get('mode', 'auto'))):
        return False
    inputs = [s for s in state['sources'] if not s.get('catalog')]
    return (len(inputs) == 1 and inputs[0]['id'] == 'M0' and inputs[0]['path'] == 'conversation.md'
            and '\n\n' + snapshot['source']['content'].strip() + '\n\n' in '\n\n' + inputs[0]['content'].strip() + '\n\n')
