# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Source-task selection and contiguous reading of the attachments assigned to Explore."""
from array import array
from bisect import bisect_right
from collections import Counter
from copy import copy
from functools import lru_cache
from io import StringIO
import hashlib
import json
import re
import unicodedata

from .router import FIXED_EFFORT
from .tools import pdf_page_label, source_suffix
from .worker_errors import WorkerInputError


_FILE = re.compile(r'(?<![\w./-])([\w-][\w.-]*(?:/[\w.-]+)*\.(?:tar\.gz|tgz|tar|gz|pdf|tex|md|txt))(?![\w-]|\.\w)', re.I)
_RESULT = re.compile(r'\b(?:theorem|lemma|proposition|corollary|proof|theoreme|lemme|demonstration|preuve)\b')
_ARTICLE = re.compile(r'\b(?:article|paper|manuscript|document|preprint|papier|manuscrit|pdf|lecture notes|notes de cours)\b')
_OVERVIEW = re.compile(r'\b(?:main results?|principal results?|key results?|overview|summary|novelty|contribution|'
                       r'resultats? principaux|principaux resultats?|nouveaute|contribution|resume|vue d.ensemble)\b')
_NEGATION = re.compile(r"\b(?:do not|don.t|don’t|never|no need to|without|avoid|not asking|"
                       r"ne\b.{0,70}\bpas|n.explique\b.{0,30}\bpas|n.ai pas|sans|inutile de|evite)\b")
_EXPLAIN = re.compile(r"\b(?:explain|explique(?:r|z)?|expliquer|walk me through|help me understand|"
                      r"aide.moi a comprendre|aidez.moi a comprendre)\b")
_SUMMARIZE = re.compile(r'\b(?:summari[sz]e|sum up|give me an overview|resum(?:e|er|ez)|presente(?:r|z)?)\b')
# A request for the typos of a manuscript: the typo check, never a general reading of the file.
_TYPOS = re.compile(r"\b(?:typos?|typographical (?:errors?|mistakes?)|misprints?|proof.?read(?:ing)?|spelling|"
                    r"(?:spelling|grammar|grammatical) (?:errors?|mistakes?)|coquilles?|fautes? (?:de frappe|d.orthographe|de grammaire)|"
                    r"orthographe|relecture orthographique)\b")
_ADDITIONAL_WORK = re.compile(
    r'(?:\b(?:and(?: also)?|also|then|et(?: aussi)?|puis|ensuite)\s+|[.!?]\s+)'
    r'(?:please\s+|s.il te plait\s+|s.il vous plait\s+)?'
    r'(?:prove|check|verify|audit|review|referee|write|create|draft|find|search|survey|translate|'
    r'explain|walk me through|prouve(?:r|z)?|demontre(?:r|z)?|verifie(?:r|z)?|controle(?:r|z)?|'
    r'relis|relisez|ecris|ecrivez|redige(?:r|z)?|cree(?:r|z)?|trouve(?:r|z)?|cherche(?:r|z)?|'
    r'traduis|traduisez|explique(?:r|z)?|donne(?:r|z)?\s+(?:moi\s+)?(?:des |les )?references|'
    r'(?:do|give|provide|produce) (?:me )?(?:a |an )?literature (?:review|survey) (?:on|about))\b')


def _fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c)).casefold()


def access_check_source(query, files):
    """Return the unambiguous selected document for a question about access to it."""
    docs = [f for f in files if f.lower().endswith(('.pdf', '.tex', '.md', '.txt', '.tgz', '.tar', '.gz'))]
    if not docs:
        return None
    named = named_attachments(query, docs)
    if len(named or docs) != 1:
        return None
    source = (named or docs)[0]
    # An individually quoted filename is still a reference. Quoted questions
    # and examples must not trigger a read of the surrounding workspace.
    visible = query
    for mark in ('"', "'", '`'):
        visible = visible.replace(mark + source + mark, 'the document')
    prose = _instruction_text(visible.replace(source, 'the document'))
    asks = re.search(
        r"\b(?:(?:can(?:not|'t|’t)?|could(?:n't|n’t)?|are) you (?:still |actually |now )?"
        r"(?:access|open|see|read|able to (?:open|read|access))|"
        r"(?:do|did|don.t|didn.t) you (?:still |already )?have(?: access to)?|"
        r"(?:as[ -]tu|avez[ -]vous|tu (?:n.as pas|as)|vous (?:n.avez pas|avez)) (?:toujours )?acces|"
        r"(?:peux[ -]tu|pouvez[ -]vous|tu peux|vous pouvez) (?:encore )?(?:lire|ouvrir|voir|acceder))\b", prose)
    if not asks or re.search(r"\b(?:do not|don.t|never|without) (?:open|read|use)|\b(?:sans|ne pas) (?:lire|ouvrir)", prose):
        return None
    subject = r'(?:pdf|file|document|paper|attachment|lecture notes|manuscript|fichier|piece jointe)'
    reference = r"\s+(?:(?:to|a|au|aux|the|this|that|my|previous|earlier|uploaded|attached|le|la|ce|mon|mes)\s+)*"
    if re.match(reference + subject + r'\b', prose[asks.end():]):
        return source
    if re.match(r'\s+(?:it|that)\b', prose[asks.end():]) and re.search(r'\b' + subject + r'\b', prose[:asks.start()]):
        return source
    return None


def instruction_prose(query, *, keep_quoted_urls=False):
    # Quoted documents and examples cannot select a workflow. Keep contractions,
    # but omit quoted prose, fenced examples, inline code and Markdown quotes.
    value = re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~', ' ', query)
    value = re.sub(r'(?m)^\s*>.*$', ' ', value)
    def quoted(match):
        content = match.group()[1:-1].strip()
        return content if keep_quoted_urls and re.fullmatch(r'https?://[^\s<>]+', content) else ' '
    value = re.sub(r'"[^"\n]*"|“[^”\n]*”|«[^»]*»|`[^`\n]*`', quoted, value)
    value = re.sub(r"(?<!\w)'[^'\n]*\s[^'\n]*'(?!\w)", ' ', value)
    return value


def _instruction_text(query):
    return _fold(instruction_prose(query))


_PRIOR_WORK = (r"(?:literature|(?:prior|earlier|existing) (?:work|art|results?)|related work|state of the art|"
               r"litterature|(?:travaux|resultats) (?:anterieurs|existants|publies)|etat de l.art)")
_COMPARISON = re.compile(
    r"\b(?:compare|comparing|place|placing|position|positioning|situate|situating|relate|fits?|"
    r"compar(?:e[rz]?|ons)|situ(?:e[rz]?|ons)|positionn(?:e[rz]?|ons))\b.{0,100}?\b" + _PRIOR_WORK + r"\b|"
    r"\b(?:with|include|add|give|provide|write|do|and|et|avec|ajoute[rz]?|inclu(?:re|ez|s))\s+"
    r"(?:(?:a|an|the|une?|la|brief|short|also)\s+)*(?:literature (?:review|survey|comparison)|"
    r"survey of (?:the )?literature|revue de (?:la )?litterature)\b|"
    r"\b(?:assess|evaluate|check|verify|discuss|evalu(?:e|er|ez)|discut(?:e|er|ez))\s+"
    r"(?:(?:its|their|the|claimed|sa|leur|la)\s+)*(?:novelty|originality|nouveaute|originalite)\b")


def simple_manuscript_review(query):
    """A whole-paper instruction with an unambiguous scope, including the common comparison request."""
    return bool(re.fullmatch(
        r"\s*(?:please\s+)?(?:review|referee)\s+(?:(?:my|our|the|this|that|attached|uploaded)\s+)*"
        r"(?:paper|article|manuscript|preprint|document)(?:\s+and (?:also )?compare (?:it|its results) "
        r"(?:with|to|against) (?:the )?" + _PRIOR_WORK + r")?\s*[.!?]?\s*", _fold(query)))


def requests_literature_position(query):
    """A request in the user's own instructions for the paper's position in the literature."""
    for clause in re.split(r'[.!?;,\n]|\b(?:but|mais)\b', _instruction_text(query)):
        match = _COMPARISON.search(clause)
        if match and re.match(r'\s+(?:on|about|sur)\b', clause[match.end():]):
            continue  # A separately named survey has its own topic.
        if match and not _NEGATION.search(clause[:match.end()]) and not re.search(
                r"\b(?:no|skip|exclude|omit|ignore|aucune?)\b.{0,45}$", clause[:match.start()]):
            return True
    return False


def requests_workspace_search(query):
    """An explicit request to use the wider workspace alongside selected sources."""
    location = r'(?:(?:the|my|this|our|project|working|all|entire|whole|le|mon|ce|tout)\s+)*(?:workspace|folder|directory|espace de travail|dossier|repertoire)\b'
    files = r'(?:(?:the |my |les |mes )?(?:other|all|remaining|autres|tous les|toutes les) (?:the )?(?:files|documents|pdfs?|notes|fichiers))\b'
    scan = r'\b(?:search|scan|list|browse|inspect|look through|look in|check|cherch\w*|parcour\w*|list\w*)\s+'
    use = r'\b(?:read|use|look at|compare|check|lis|lire|utilis\w*|compar\w*|consulte\w*)\s+'
    patterns = (scan + r'(?:(?:in|through|dans)\s+)?(?:' + location + '|' + files + ')',
                use + files, r'\b(?:find|look for|search for|cherche\w*)\b.{1,75}?\b(?:in|across|dans) ' + location)
    for clause in re.split(r'[.!?;\n]|\b(?:but|mais)\b', _instruction_text(query)):
        if not _NEGATION.search(clause) and any(re.search(pattern, clause) for pattern in patterns):
            return True
    return False


def only_assigned_sources(query):
    """An instruction to use the supplied material exclusively, outside quotations and examples."""
    english = r'(?:(?:the|my|our|these|those)\s+)?(?:attached|uploaded|pinned|supplied|provided|selected)\s+(?:sources?|material|texts?|documents?|files?|manuscripts?|papers?|notes|pdfs?)'
    french = r'(?:les?|la|mes|ces)?\s*(?:sources?|documents?|fichiers?|manuscrits?|notes|articles?)\s+(?:joint\w*|fourni\w*|selectionne\w*)'
    use = r'(?:use|using|consult|read|work\s+(?:from|with)|rely\s+on|utilis\w*|consult\w*)'
    only = r'(?:only|solely|exclusively|uniquement|seulement|exclusivement)'
    material = f'(?:{english}|{french})'
    for clause in re.split(r'[.!?;\n]|\b(?:but|mais)\b', _instruction_text(query)):
        if re.search(r'\b(?:do not|don.t|never|need not)\s+' + use, clause):
            continue
        if re.search(r'\b(?:' + use + r'\s+' + only + '|' + only + r'\s+' + use + r')\s+' + material + r'\b|' +
                     r'\b' + material + r'\s+' + only + r'\b(?!\s+(?:as|for|if|when|comme|pour))', clause):
            return True
    return False


def excludes_external_sources(query):
    """Detect an explicit per-turn restriction on external retrieval."""
    return only_assigned_sources(query) or bool(re.search(
        r"\b(?:(?:no|without|avoid|sans)\s+(?:(?:any|using|consulting|accessing|the|aucune?)\s+)*"
        r"(?:external (?:sources?|references?|search(?:es)?)|(?:internet|web)(?: searches?)?|"
        r"sources? externes?|recherche en ligne)|"
        r"(?:do not|don.t|never) (?:search|browse|use|consult) (?:the )?(?:web|internet|external sources)|"
        r"(?:without (?:searching|checking|browsing|looking)|(?:do not|don.t|never) (?:search|check|browse|look)) "
        r"(?:online|on(?: the)? (?:internet|web))|(?:no|without) (?:online|web) (?:search|lookup)|"
        r"(?:sans|ne pas) (?:chercher|verifier|regarder) (?:en ligne|sur internet)|"
        r"(?:stay|work|remain|reste[rz]?|travaille[rz]?) (?:offline|hors ligne))\b",
        _instruction_text(query)))


def scoped_literature(library, *instructions, sources=()):
    """A source-limited task can share the caller's cache and accounting without changing the next task."""
    text = [item for item in instructions if isinstance(item, str)]
    if library and any(excludes_external_sources(item) for item in text):
        library = copy(library)
        library.online = False
        if any(only_assigned_sources(item) for item in text):
            allowed = {value for item in sources for value in (item.get('document_id'), 'workspace:' + item['path'])
                       if value}
            inherited = getattr(library, 'assigned_sources', None)
            library.assigned_sources = allowed if inherited is None else allowed & inherited
    return library


def automatic_source_action(query, files=()):
    """Return one narrow source workflow, or None for the ordinary controller.

    Whole-paper explanations use explore: the explain workflow deliberately
    handles one statement and proof. Explicit mode selections take precedence
    at the call site. The delegated request remains byte-for-byte unchanged.
    """
    if not isinstance(query, str) or not query.strip():
        return None
    if not isinstance(files, (tuple, list)) or any(not isinstance(f, str) or not f for f in files):
        return None
    text = _instruction_text(query)
    if _NEGATION.search(text):
        return None
    if _ADDITIONAL_WORK.search(text):
        # One seeded worker must not swallow another explicitly requested job.
        # Let the controller schedule those objectives and their dependencies.
        # Results plus novelty is one article-understanding objective, and has
        # no extra task verb here.
        return None
    # An instruction, or a direct content question, must lead the message. A
    # later mention of a desired example is not enough to choose a worker.
    leading = re.sub(r"^(?:please\s+|bonjour[,.!]?\s*|hello[,.!]?\s*|hi[,.!]?\s*|"
                     r"s.il te plait[,]?\s*|s.il vous plait[,]?\s*)+", '', text.strip())
    request_lead = re.sub(r"^(?:(?:can|could|would|will) you\s+|i (?:want|would like) (?:you )?to\s+|"
                          r"(?:peux|pourrais|pouvez|pourriez)[ -](?:tu|vous)\s+)", '', leading)
    named_files = list(dict.fromkeys(_FILE.findall(query)))
    refs = list(files) or named_files
    if not refs:
        return None
    source_article = bool(_ARTICLE.search(text) or named_files)
    proof_word = bool(re.search(r'\b(?:proofs?|preuves?|demonstrations?)\b', text))
    review = re.match(r'^(?:review|referee|relis|relisez|'
                      r'give (?:me )?(?:a )?(?:(?:detailed|thorough) )?review|'
                      r'fais (?:une )?(?:revue|relecture)|faire (?:une )?(?:revue|relecture))\b', request_lead)
    check = re.match(r'^(?:check|verify|audit|verifie(?:r|z)?|controle(?:r|z)?)\b', request_lead)
    all_proofs = proof_word and bool(re.search(r'\b(?:all|every|toutes?|chaque)\s+(?:the\s+|les\s+)?(?:proofs?|preuves?|demonstrations?)\b', text))
    # "Check the typos of the manuscript", "proofread my paper": only the typos are asked, of a source the user
    # attached, named or calls the paper. With a review asked too, the router plans both tasks.
    if _TYPOS.search(text) and not proof_word and not review and (source_article or len(refs) == 1):
        return {'mode': 'typo_check', 'request': query, 'files': refs, 'effort': FIXED_EFFORT['typo_check'],
                'reason': 'List the typos of the supplied manuscript, each correction checked.'}
    correctness = bool(re.match(r'^(?:is|are|est.ce que)\b', request_lead)
                       and re.search(r'\b(?:correct|valid|right|rigorous|correcte?|valide|rigoureuse?)\b', text))
    if source_article and ((review and not proof_word) or ((review or check) and all_proofs)):
        return {'mode': 'referee', 'request': query, 'files': refs, 'effort': FIXED_EFFORT['referee'],
                'reason': 'Review the supplied manuscript with the explicitly requested scope.'}
    if proof_word and (review or check or correctness) and _RESULT.search(text):
        return {'mode': 'quick_review', 'request': query, 'files': refs, 'effort': 'high',
                'reason': 'Check the specified proof from the supplied source.'}
    imperative = re.match(r"^(?:(?:can|could|would|will) you\s+|i (?:want|would like) (?:you )?to\s+|"
                          r"(?:peux|pourrais|pouvez|pourriez)[ -](?:tu|vous)\s+)?"
                          r"(?:explain|summari[sz]e|sum up|give me an overview|walk me through|help me understand|"
                          r"explique(?:r|z)?|m.expliquer|resum(?:e|er|ez)|presente(?:r|z)?|"
                          r"aide.moi a comprendre|aidez.moi a comprendre)\b", leading)
    direct_question = re.match(r"^(?:what (?:are|is)|how (?:is|are)|quels? (?:sont|est)|"
                               r"quelles? (?:sont|est)|en quoi|quelle est)\b", leading)
    if not imperative and not direct_question:
        return None
    # A named file is an explicit source reference. Workspace validation still
    # decides whether it exists and whether reading it is permitted.
    explain = bool(_EXPLAIN.search(text))
    numbered = re.findall(r'\b(?:theorem|lemma|proposition|corollary|theoreme|lemme)\s+(?:\d+(?:\.\d+)*|[a-z])\b', text)
    proof_walkthrough = bool(explain and re.search(r'\b(?:proof|preuve|demonstration)\b', text)
                             and not re.search(r'\b(?:proof strategy|proof idea|strategie|idee de la preuve|'
                                               r'all proofs|every proof|toutes les preuves|chaque preuve)\b', text))
    # Article-wide main results must not accidentally select a single theorem.
    overview = bool(len(set(numbered)) > 1 or _OVERVIEW.search(text)
                    or re.search(r'\b(?:what is new|how they are new|en quoi.{0,80}nouvea)', text))
    article = bool(_ARTICLE.search(text) or _FILE.search(query))
    article_explanation = re.search(
        r'\b(?:explain|explique(?:r|z)?|expliquer)\s+(?:(?:me|moi|nous|us)\s+)?'
        r'(?:(?:the|this|that|attached|supplied|cet|cette|ce|le|la|les|l.)\s+){0,3}'
        r'(?:article|paper|manuscript|document|preprint|papier|manuscrit|pdf|lecture notes|notes de cours)\b', text)
    if imperative and explain and (len(set(numbered)) == 1 or (proof_walkthrough and len(set(numbered)) <= 1)):
        mode = 'explain'
        reason = 'Explain the specified mathematical result or proof from the supplied source.'
    elif article and (overview or (imperative and (_SUMMARIZE.search(text) or article_explanation))):
        mode = 'explore'
        reason = 'Read the supplied article and explain its results and contribution from source evidence.'
    elif imperative and explain and _RESULT.search(text):
        mode = 'explain'
        reason = 'Explain the specified mathematical result or proof from the supplied source.'
    else:
        return None
    return {'mode': mode, 'request': query, 'files': refs, 'effort': 'high', 'reason': reason}


def _named_file_mentions(text, files):
    """Exact known paths and unambiguous basenames, preferring the complete quoted filename."""
    names = dict.fromkeys(files)
    counts = Counter(name.rsplit('/', 1)[-1] for name in names)
    forms = {name: name for name in names if name in text}
    for name in names:
        base = name.rsplit('/', 1)[-1]
        if base in text and counts[base] == 1:
            forms.setdefault(base, name)
    alternatives = '|'.join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
    if not alternatives:
        return []
    return [(m.start(), m.end(), forms[m[0]]) for m in
            re.finditer(r'(?<![\w./-])(?:' + alternatives + r')(?![\w/-]|\.\w)', text)]


def named_attachments(text, files):
    matched = {name for _, _, name in _named_file_mentions(text, files)}
    return [name for name in files if name in matched]


_EXCLUDE_FILE = re.compile(
    r"\b(?:(?:do not|don.t|never|no need to|without|avoid)\s+(?:read(?:ing)?|us(?:e|ing)|consult(?:ing)?|open(?:ing)?|inspect(?:ing)?|includ(?:e|ing)|look(?:ing)? at)|"
    r"(?:except(?: for)?|excluding|exclude|skip|ignore|omit|but not|without|sans)|"
    r"(?:ne |n.)?(?:lis|lisez|consulte|utilise|ouvre|inclu\w*)\s+pas|"
    r"sans\s+(?:lire|consulter|utiliser|ouvrir)|(?:sauf|exclu\w*|ignor\w*))\b"
    r"(?:\s|[,:'`’“”\"«»]|(?:the|this|that|attached|other|file|files|document|documents|named|and|or|le|la|les|ce|cette|fichier|fichiers|et|ou|sourcefile)\b)*$")


def named_explore_sources(workspace, query, known_files, assigned=()):
    """Resolve user-named readable files and explicit exclusions. Preserve attached-file authority."""
    from .conversation_sources import _instruction
    text = _instruction(query)
    known_names = set(assigned) | set(known_files)
    known_names.update(name.rsplit('/', 1)[-1] for name in list(known_names))
    def literal(match):
        value = match[0][1:-1]
        # A quoted path is useful, while a filename inside a code example or
        # prose quotation cannot authorize reading a local document.
        if value in known_names or _FILE.fullmatch(value):
            return match[0]
        if ' ' in value and value.lower().endswith(('.pdf', '.tex', '.md', '.txt', '.bib')):
            try:
                if workspace.path(value, pdf=True).is_file():
                    return match[0]
            except (OSError, ValueError):
                pass
        return ' '
    text = re.sub(r'`[^`\n]*`|"[^"\n]*"|“[^”\n]*”|«[^»\n]*»', literal, text)
    mentions = _named_file_mentions(text, [*assigned, *known_files])
    # A shorter basename inside an already matched path with spaces is not a second file.
    spans = iter(mentions)
    known = next(spans, None)
    unmatched = []
    for match in _FILE.finditer(text):
        while known and known[1] <= match.start():
            known = next(spans, None)
        if not known or match.end() <= known[0]:
            unmatched.append((match.start(), match.end(), match[1]))
    mentions = sorted(mentions + unmatched)
    requested, excluded = list(assigned), set()
    prefix, previous = '', 0
    for start, end, name in mentions:
        prefix += _fold(text[previous:start])
        tail = _fold(text[end:end + 100])
        if (_EXCLUDE_FILE.search(prefix) or
                re.match(r"^[`'’\"»”]*\s+(?:is|must be|should be|est|doit etre)\s+(?:excluded|ignored|skipped|exclu\w*|ignore\w*)\b", tail)):
            excluded.add(name)
        else:
            requested.append(name)
        prefix, previous = prefix + ' sourcefile ', end
    allowed = []
    for name in dict.fromkeys(requested):
        if name in excluded:
            continue
        if name in assigned:
            allowed.append(name)
            continue
        try:
            path = workspace.path(name, pdf=True)
            # A user-named file can join a previous attachment selection only
            # when the workspace already authorizes this file type.
            if not (workspace.selected_files_only and source_suffix(path) in workspace.allowed_types
                    and path not in workspace.excluded_files):
                workspace._readable(path)
            if path.is_file():
                allowed.append(path.relative_to(workspace.root).as_posix())
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(allowed)), excluded


def requested_source_tasks(query, files):
    """Recognize explicit reading objectives within a list of independent user instructions."""
    if not files or '```' in query or '~~~' in query or _NEGATION.search(_instruction_text(query)):
        return []
    tasks = []
    for part in re.split(r'\n|;|\s+(?:at the same time|meanwhile|en parallèle|en meme temps)[,:]?\s+', query, flags=re.I):
        clause = re.sub(r'^\s*(?:[-*•]|\d+[.)])\s+', '', part).strip()
        clause = re.sub(r'^(?:and|et|also|ensuite)\s+', '', clause, flags=re.I)
        named = named_attachments(clause, files)
        local_document = re.search(r'\b(?:this|that|the attached|my|the supplied|the uploaded|'
                                   r'ce|cet|cette|mon|le|la)\s+(?:lecture|pdf|document|paper|'
                                   r'manuscript|notes|cours|fichier|article)\b', _instruction_text(clause))
        if not named and (len(files) != 1 or not local_document):
            continue
        source = automatic_source_action(clause, named or files)
        if not source and re.match(r'^(?:please )?(?:explain what|tell me what|what|which|according to|'
                                   r'que contient|de quoi parle|quels?|quelle[s]?)\b', _instruction_text(clause).strip()):
            source = {'mode': 'explore', 'request': clause, 'files': named or list(files)}
        if source:
            task = {k: source[k] for k in ('mode', 'request', 'files')}
            if task not in tasks:
                tasks.append(task)
    return tasks


_LINE_SEPARATORS = str.maketrans({char: '\n' for char in '\r\v\f\x1c\x1d\x1e\x85\u2028\u2029'})


def _source_text(workspace, filename):
    path = workspace.path(filename, pdf=True)
    workspace._readable(path)
    if path.suffix.lower() == '.pdf':
        if workspace.literature is None:
            raise ValueError('Local PDF extraction is unavailable')
        summary = workspace.literature.import_local(str(path.relative_to(workspace.root)))
        metadata, lines = workspace.literature._document(summary['document_id'])
        return '\n'.join(lines), metadata.get('pdf_page_starts', ())
    # Match '\n'.join(text.splitlines()) without materializing a Python object for each short line.
    text = workspace.text(path).replace('\r\n', '\n').translate(_LINE_SEPARATORS)
    return text.removesuffix('\n'), ()


class _ExploreDocument:
    """Locators are one-based Unicode columns in the extracted text, including single-line files."""

    def __init__(self, workspace, name):
        self.name, self.error = name, ''
        self.pages = ()
        try:
            self.text, self.pages = _source_text(workspace, name)
            if not self.text.strip():
                self.error = 'no legible text was extracted'
        except (OSError, ValueError, UnicodeError) as exc:
            self.text = ''
            self.error = str(exc)
        self.starts = array('I', [0])
        self.starts.extend(m.end() for m in re.finditer('\n', self.text))
        self.identity = {'path': name, 'characters': len(self.text), 'lines': len(self.starts) if self.text else 0,
                         'sha256': hashlib.sha256(self.text.encode()).hexdigest(), 'error': self.error}

    def locator(self, offset):
        line = bisect_right(self.starts, offset)
        return line, offset - self.starts[line-1] + 1

    def excerpt(self, start, stop):
        line, column = self.locator(start)
        body = StringIO()
        body.write(f'\nSOURCE {self.name} ({self.identity["lines"]} extracted lines)\n')
        for i in range(line, len(self.starts) + 1):
            left = start if i == line else self.starts[i-1]
            if left > stop or (left == stop and stop < len(self.text)):
                break
            right = min(stop, self.starts[i] - 1 if i < len(self.starts) else len(self.text))
            label = pdf_page_label(self.pages, i, first=i == line)
            position = f'{i}:{column}' if i == line and column > 1 else str(i)
            if i != line:
                body.write('\n')
            body.write(f'{label}{position}: ')
            body.write(self.text[left:right])
        return body.getvalue()


_NUMBERED_RESULT = re.compile(r'\b(?:theorem|theoreme|lemma|lemme|proposition|corollary|corollaire|definition)\s+(?:\d+(?:\.\d+)*|[a-z])\b')
_TARGETED_LOOKUP = re.compile(r'\b(?:locate|find|state|quote|where|what (?:is|does|are)|ou|enonce|cite|trouve|quelles? (?:sont|est)|que dit)\b')
_EXTENDED_READING = re.compile(r'\b(?:whole|entire|full|throughout|every|compare|survey|summari[sz]e|overview|proof|prove|verify|audit|also|as well|and then|puis|ensuite|all (?:sections|pages|results)|integral|entier|ensemble|compare\w*|preuve|demontr\w*|verifi\w*)\b')


def _lookup_target(request):
    """Only a localized lookup in a single source can finish before its last page."""
    text = _instruction_text(request)
    targets = set(_NUMBERED_RESULT.findall(text))
    if len(targets) == 1 and _TARGETED_LOOKUP.search(text) and not _EXTENDED_READING.search(text):
        return targets.pop()
    return None


def _located_answer(response, target, document, start, end):
    try:
        value = json.loads(response)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {'answer', 'quote'}:
        return None
    answer, quote = value['answer'], value['quote']
    if not isinstance(answer, str) or not answer.strip() or not isinstance(quote, str) or not quote.strip():
        return None
    if target not in _NUMBERED_RESULT.findall(_fold(quote)) or _fold(quote.strip()).rstrip(':.') == target:
        return None
    offset = document.text.find(quote, start, end)
    if offset < 0:
        return None
    first, col = document.locator(offset)
    last, last_col = document.locator(offset + len(quote) - 1)
    return f'{answer.strip()}\n\nSource: {document.name}, lines {first}:{col}–{last}:{last_col}.\n\n> ' + quote.replace('\n', '\n> ')


def explore_sources(agent, files, goal, emit, saved, checkpoint, *, requested=None, history=()):
    """Read every fitting attachment in one call, otherwise page in order and retain the findings across calls.

    A completed model response acknowledges a page. The next cursor is committed together with its findings,
    so a stopped generation can retry the uncommitted page without skipping it. Hashes bind those cursors
    to the same extracted texts on resume.
    """
    from .assistant_budget import AssistantBudget
    from .call_policy import CallContextError
    from .deepinfra import TesterAllowanceInsufficient

    # A finished reading is an immutable marked result. Reopening its saved
    # answer requires neither access to changed sources nor fresh inference.
    if saved and saved.get('final'):
        agent.last_stats, agent.last_stop_reason = saved['stats'], saved['stop_reason']
        return saved['final'], saved['coverage']
    names = list(dict.fromkeys(files))
    known = {}
    @lru_cache(maxsize=2)
    def document(name):
        if agent.workspace.cancelled():
            raise KeyboardInterrupt
        doc = _ExploreDocument(agent.workspace, name)
        if name in known and doc.identity != known[name]:
            raise WorkerInputError('A selected source changed during this reading. Restore the original files to resume.',
                                   code='changed_source')
        return doc
    for name in names:
        known[name] = document(name).identity
    identities = list(known.values())
    if saved and saved['documents'] != identities:
        raise WorkerInputError('The selected source text changed after this reading began. Restore the original files '
                               'to resume, or start a fresh reading.', code='changed_source')
    state = dict(saved or {'documents': identities, 'cursor': 0, 'seen': 0, 'findings': ''})
    checkpoint(state)
    target = _lookup_target(requested or '') if len(identities) == 1 else None

    ends = []
    for item in identities:
        ends.append((ends[-1] if ends else 0) + (0 if item['error'] else item['characters']))
    total = ends[-1]

    def coverage():
        seen = state['seen']
        received = []
        for i, item in enumerate(identities):
            start = ends[i-1] if i else 0
            if item['error']:
                detail = 'unreadable (' + item['error'] + ')'
            elif seen >= ends[i]:
                detail = f'all {item["lines"]} extracted lines supplied'
            elif seen <= start:
                detail = 'no text supplied'
            else:
                line, col = document(item['path']).locator(seen - start)
                detail = f'next unread text at line {line}, column {col}'
            received.append(f'{item["path"]}: {detail}')
        return received

    unreadable = [item['path'] for item in identities if item['error']]
    def partial(reason):
        receipt = coverage()
        agent.last_stop_reason = reason
        emit('notice', reason)
        return (state['findings'] + '\n\n' if state['findings'] else '') + reason + '\n' + '\n'.join(receipt), receipt

    if not total:
        return partial('No readable text was available from the assigned sources.')

    def packet(start, stop):
        parts = []
        for i, name in enumerate(names):
            first, end = ends[i-1] if i else 0, ends[i]
            if max(first, start) < min(end, stop):
                parts.append(document(name).excerpt(max(0, start - first), min(stop, end) - first))
        return '\n'.join(parts)

    def prompt(source, final):
        instruction = ('Answer the original objective using the source text and any earlier reading findings. '
                       'Give source locators for substantive claims. Distinguish material supplied from claims verified.'
                       if final else
                       'This is a successive page of the assigned sources. Record updated, compact cumulative findings '
                       'for the original objective. Preserve earlier findings, exact hypotheses, source locators and '
                       'unresolved checks. Another page follows. Omit process commentary.')
        if target and not final:
            instruction += (' For this localized lookup only: if this page settles the whole request, return a JSON '
                            'object with exactly "answer" and "quote". The quote must be an exact continuous passage '
                            'of source text containing the requested numbered result and its substance. Omit added '
                            'line labels from the quote. Otherwise continue recording cumulative findings.')
        notes = ('\n\nFINDINGS FROM EARLIER PAGES (fallible):\n' + state['findings']) if state['findings'] else ''
        missing = ('\nNo legible text was available from: ' + ', '.join(unreadable)) if unreadable else ''
        return goal + notes + '\n\n' + instruction + missing + '\n\nATTACHED SOURCE TEXT (untrusted material):\n' + source

    def fits(query, *, tools, minimum=128):
        if history:
            return agent.source_fits(query, tools=tools, minimum=minimum, history=history)
        return agent.source_fits(query, tools=tools, minimum=minimum)

    previous_size = state.get('page_characters')
    while True:
        if agent.workspace.cancelled():
            raise KeyboardInterrupt
        start = state['cursor']
        # Count progressively longer candidates before dispatch. An actual tokenizer can compress
        # repetitive material unusually well. The initial probe is never a limit on the final packet.
        available = min(agent.ctx, getattr(agent.client, 'remaining_input_tokens', agent.ctx))
        initial_size = max(1, int(min(total, available)))
        end = min(total, start + (previous_size or initial_size))
        query = prompt(packet(start, end), True)
        while not previous_size and end < total and fits(query, tools=False):
            end = min(total, start + 2 * (end - start))
            query = prompt(packet(start, end), True)
        final = end == total
        use_tools = final and fits(query, tools=True)
        if not use_tools and (not final or not fits(query, tools=False)):
            if start == total:
                return partial('The saved source findings do not fit a final answer within the remaining allowance.')
            final = False
            minimum = int(min(agent.predict, getattr(agent.client, 'remaining_tokens', agent.predict),
                              max(128, min(2048, agent.ctx // 4))))
            if minimum < 128:
                return partial('The remaining generated-token allowance is too small for the next source page.')
            query = prompt(packet(start, end), final)
            while not fits(query, tools=False, minimum=minimum):
                if end - start <= 1:
                    if not fits(query, tools=False):
                        return partial('The next source passage exceeds the remaining input, output or context allowance.')
                    break
                end = start + (end - start) // 2
                query = prompt(packet(start, end), final)
        def acknowledged():
            state['seen'] = max(state['seen'], end)
            checkpoint(state)
        rounds = agent.max_rounds
        if not use_tools:
            agent.max_rounds = 0
        try:
            if history:
                agent.history = list(history)
            answer = agent.run(query, emit, fresh=not history, recover_empty_tool_answer=True, on_first_response=acknowledged)
        except (AssistantBudget, CallContextError, TesterAllowanceInsufficient) as exc:
            return partial(str(exc))
        finally:
            agent.max_rounds = rounds
        if not answer.strip() or agent.last_stop_reason:
            if answer.strip():
                state['findings'] = answer
            return partial(agent.last_stop_reason or 'No written findings were produced for the supplied source text.')
        if agent.last_stats.get('done_reason') not in ('stop', 'tool_calls'):
            return partial('The source reading ended without a completed model response.')
        located = _located_answer(answer, target, document(names[0]), start, end) if target and not final else None
        if final or located:
            receipt = coverage()
            if located:
                answer = located + '\n\nReading coverage: ' + '; '.join(receipt)
            if unreadable:
                agent.last_stop_reason = 'Some assigned source text could not be read.'
                answer += '\n\n' + agent.last_stop_reason + '\n' + '\n'.join(receipt)
            state.update(cursor=end, final=answer, coverage=receipt, stats=agent.last_stats,
                         stop_reason=agent.last_stop_reason)
            checkpoint(state)
            return answer, receipt
        previous_size = end - start
        state.update(cursor=end, page_characters=previous_size, findings=answer)
        checkpoint(state)
