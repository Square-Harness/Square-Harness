"""Style profiles: condense an author's earlier .tex articles into a small reusable file.

The controller extracts what can be read exactly (macro definitions and how often the
body uses them, theorem environments, section skeletons); the model only describes
what needs judgement: which articles belong to the same domain, what each kind of
article sets out to do, how it is organised and how it explains. Quoted phrasings are
kept only when they occur verbatim in the articles.

A profile is one JSON file (format below) with bounded fields, so a later write-up can
include the part for one domain in roughly a thousand tokens (see `style_prompt`).

    {"format": "square-style", "version": 1, "name": ..., "created_at": ..., "updated_at": ...,
     "articles": [{"path", "sha256", "title", "domain"}],
     "macros": [{"name", "definition", "uses", "articles": [path]}],
     "environments": [{"name", "definition", "uses"}],
     "domains": [{"name", "description", "articles": [path],
                  "aim", "structure": [str], "habits": [str], "phrases": [str]}],
     "template": [{"name", "content"}]}   (optional: the LaTeX template write-ups in this style build on)
"""
import json
from pathlib import Path
import re

from .ledger import _atomic_write, _directory, _now
from .research import ResearchRunner
from .tools import unicode_source
from .worker_errors import WorkerInputError
from .model_limits import input_limit
from . import images

FORMAT = 'square-style'
NAME = re.compile(r'[\w][\w .-]{0,59}')
LIMITS = {'aim': 400, 'item': 200, 'structure': 10, 'habits': 10, 'phrases': 8, 'macros': 80, 'environments': 30}
PROMPT_CHARS = 6000  # a domain's style text for a later write-up, about 1500 tokens
TEMPLATE_FILES = ('.tex', '.sty', '.cls', '.bib')

_DEFINE = re.compile(r'\\(?:(?:re|provide)?newcommand\*?|DeclareMathOperator\*?|DeclarePairedDelimiter|def)(?![A-Za-z@])')
_THEOREM = re.compile(r'\\(?:newtheorem|declaretheorem)\*?(?![A-Za-z@])')
_HEADING = re.compile(r'\\(section|subsection)\*?\s*(?:\[[^\]]*\]\s*)?\{')
_ENVIRONMENT = re.compile(r'\\begin\{([A-Za-z*]+)\}')
_INPUT = re.compile(r'\\(?:input|include)\{([^}]+)\}')

SCAN_KEYS = ('title', 'abstract', 'headings', 'environments', 'inputs', 'lines')

DOMAINS_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['domains'],
    'properties': {'domains': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['name', 'description', 'articles'],
        'properties': {'name': {'type': 'string', 'maxLength': 60},
                       'description': {'type': 'string', 'maxLength': 300},
                       'articles': {'type': 'array', 'items': {'type': 'string', 'maxLength': 10}}}}}},
}

STYLE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['aim', 'structure', 'habits', 'phrases'],
    'properties': {
        'aim': {'type': 'string', 'maxLength': LIMITS['aim']},
        'structure': {'type': 'array', 'maxItems': LIMITS['structure'], 'items': {'type': 'string', 'maxLength': LIMITS['item']}},
        'habits': {'type': 'array', 'maxItems': LIMITS['habits'], 'items': {'type': 'string', 'maxLength': LIMITS['item']}},
        'phrases': {'type': 'array', 'maxItems': LIMITS['phrases'], 'items': {'type': 'string', 'maxLength': LIMITS['item']}},
    },
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


def document_body(tex):
    text = strip_comments(tex)
    return text.split('\\begin{document}', 1)[1] if '\\begin{document}' in text else text


def uses(body, name):
    return len(re.findall(r'\\' + re.escape(name) + r'(?![A-Za-z@])', body))


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


def excerpts(tex, room):
    """Representative passages within `room` characters: the introduction's opening, the
    opening of every section, one substantial proof and the start of the last section."""
    body = document_body(tex).split('\\end{document}', 1)[0]
    starts = [m.start() for m in re.finditer(r'\\section\*?\s*(?:\[[^\]]*\]\s*)?\{', body)]
    parts = []
    if starts:
        intro_end = starts[1] if len(starts) > 1 else len(body)
        parts.append(('Introduction (opening)', body[starts[0]:min(intro_end, starts[0] + room // 3)]))
        for start in starts[1:]:
            parts.append(('Section opening', body[start:start + 600]))
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
    return '\n\n'.join(shown)


def verbatim(phrase, sources):
    """Whether a quoted phrasing occurs in one of the articles, up to spacing."""
    needle = ' '.join(phrase.strip(' "\'“”').split())
    return len(needle) >= 8 and any(needle in ' '.join(strip_comments(text).split()) for text in sources)


def _items(values, count):
    return [' '.join(str(v).split())[:LIMITS['item']] for v in values or [] if isinstance(v, str) and v.strip()][:count]


def clean_style(value, sources):
    """Bound a model's style description; keep only phrasings found verbatim."""
    value = value if isinstance(value, dict) else {}
    phrases = _items(value.get('phrases'), LIMITS['phrases'] * 2)
    kept = [p for p in phrases if verbatim(p, sources)][:LIMITS['phrases']]
    return {'aim': ' '.join(str(value.get('aim') or '').split())[:LIMITS['aim']],
            'structure': _items(value.get('structure'), LIMITS['structure']),
            'habits': _items(value.get('habits'), LIMITS['habits']),
            'phrases': [p.strip(' "\'“”') for p in kept]}, len(phrases) - len(kept)


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


def public_profile(profile):
    """A style as the interface lists it: its template's file names and sizes, not their contents."""
    value = dict(profile)
    if profile.get('template'):
        value['template'] = [{'name': f['name'], 'size': len(f['content'])} for f in profile['template']]
    return value


def valid_profile(value):
    return (isinstance(value, dict) and value.get('format') == FORMAT and value.get('version') == 1
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


def style_prompt(profile, domain=None, limit=PROMPT_CHARS):
    """Compact style guidance for a write-up: one domain's description, then its macros."""
    chosen = _domain(profile, domain)
    domains = [chosen] if chosen else profile['domains']
    out = [f'AUTHOR STYLE "{profile["name"]}" (condensed from {len(profile["articles"])} of the author\'s articles).']
    for item in domains:
        out.append(f'\nDomain: {item["name"]}. {item.get("description", "")}'.rstrip())
        if item.get('aim'):
            out.append('Aim of these papers: ' + item['aim'])
        if item.get('structure'):
            out.append('Typical structure:\n' + '\n'.join(f'{i}. {s}' for i, s in enumerate(item['structure'], 1)))
        if item.get('habits'):
            out.append('Explanation habits:\n' + '\n'.join('- ' + h for h in item['habits']))
        if item.get('phrases'):
            out.append('Characteristic phrasings (verbatim): ' + '; '.join(f'"{p}"' for p in item['phrases']))
    articles = set(chosen['articles']) if chosen else None
    macros = [m['definition'] for m in profile['macros'] if articles is None or articles & set(m['articles'])]
    text = '\n'.join(out)
    if macros:
        text += '\nUse these macros instead of their expansions:\n'
        for definition in macros:
            if len(text) + len(definition) + 1 > limit:
                break
            text += definition + '\n'
    return text[:limit]


# -- the job -------------------------------------------------------------------------------

class StyleRunner(ResearchRunner):
    """Builds a style profile from pinned .tex articles; saves it under <styles_root>/.mathagent/styles."""
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
            raise ValueError('Choose .tex articles or screenshots to learn the writing style from')
        if any(Path(n).suffix.lower() != '.tex' for n in names):
            raise ValueError('A style is learnt from .tex articles only')
        # An unreadable article is left out and named; the style is learnt from the others.
        readable, self._skipped = [], []
        for article in names:
            try:
                self.agent.workspace.text(self.agent.workspace.path(article))
                readable.append(article)
            except (OSError, ValueError) as exc:
                self._skipped.append(_unreadable(article, exc))
        if names and not readable and not getattr(self.agent, 'image_refs', None):
            raise ValueError('None of the chosen articles could be read: ' + '; '.join(self._skipped) +
                             '. Remove them or save them as text.')
        self._name = name
        return super().start(goal.strip() or f'Condense my writing style "{name}" from these articles.', kind='style',
                             source_files=readable, **budgets)

    def _extra_state(self):
        return {'phase': 'scan', 'style_name': self._name, 'articles': [], 'domains': [],
                'observations': {}, 'profile': None, 'saved_to': None, 'template': self._template,
                'skipped': self._skipped}

    def _material(self, role):
        # Each call carries exactly what it needs; the generic research index adds nothing here.
        return ('Budget remaining (null means no cumulative cap): ' + json.dumps(
            {k: None if self.state['settings'][k] is None else max(0, self.state['settings'][k] - self.state[c])
             for k, c in [('max_tokens', 'tokens_charged'), ('max_input_tokens', 'input_tokens_charged')]}), [])

    def _room(self, cap):
        """Characters of article text that fit beside the instructions (text runs at 3+ characters a token)."""
        return max(2000, min(input_limit(self.agent, 'style_packet', 24000), (self.agent.ctx - cap - 3000) * 2))

    def _article(self, source_id):
        return next(s for s in self.state['sources'] if s['id'] == source_id)

    def _digest(self, article):
        heads = '\n'.join(article['headings'][:30]) or '(no \\section headings)'
        envs = ', '.join(f'{k} ×{v}' for k, v in article['environments'].items()) or 'none'
        return (f'[{article["id"]}] {article["path"]}\nTitle: {article["title"] or "(none)"}\n'
                f'Abstract: {article["abstract"] or "(none)"}\nSections:\n{heads}\nEnvironments: {envs}')

    def _step(self, phase):
        if phase == 'scan':
            self._scan()
        elif phase == 'classify':
            self._classify()
        elif phase == 'observe':
            self._observe()
        elif phase == 'condense':
            self._condense()
        elif phase == 'save':
            self._save_profile()
            self.state['phase'] = 'done'
        else:
            raise ValueError('Unknown style phase: ' + phase)

    def _scan(self):
        self.state['warnings'] += [f'Not used for the style: {problem}.' for problem in self.state.get('skipped', [])]
        self.state['articles'] = [{'id': s['id'], 'path': s['path'], **scan(s['content'])} for s in self.state['sources']]
        for article in self.state['articles']:
            if article['inputs']:
                self.state['warnings'].append(f'{article["path"]} includes other files ({", ".join(article["inputs"][:4])}); '
                                              'only the pinned file was read. Pin the included files too to use them.')
        self.state['phase'] = 'classify'

    def _classify(self):
        articles = self.state['articles']
        instruction = ('Sort these articles by the kind of mathematical writing they are, so the author gets one style per '
                       'domain (for example numerical analysis, theoretical PDE, geometry). Group articles whose aim and '
                       'readership are alike; use a single domain when they all are. Give each domain a short name and a '
                       'one-sentence description of its aim, and list its article identifiers such as "M1". Every article '
                       'belongs to exactly one domain.\n\nAUTHOR\'S NOTE (may be empty): ' + self.state['goal'] +
                       '\n\nARTICLES:\n' + '\n\n'.join(self._digest(a) for a in articles))
        result = self._call('classify', instruction, cap=1500, format_schema=DOMAINS_SCHEMA)
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
        cap = 2500
        domain = next(d for d in self.state['domains'] if article['id'] in d['articles'])
        instruction = (f'Describe the writing style of article {article["id"]}, from the domain "{domain["name"]}". '
                       'Describe how it is written, not what it proves: what the paper sets out to do for its reader '
                       '(aim); its typical structure as an ordered list of parts, each with what that part does; '
                       'the author\'s explanation habits (how results are motivated and announced, how proofs are '
                       'organised and signposted, use of remarks, examples, figures or numerical tables, notation '
                       'conventions, tone, person and tense); and up to eight short characteristic phrasings copied '
                       'exactly from the text (connectives and signposts, not mathematical statements). Be concrete and '
                       'brief: one sentence per item.\n\n' + self._digest(article) +
                       '\n\nEXCERPTS (LaTeX source, comments removed):\n' + excerpts(self._article(article['id'])['content'], self._room(cap)))
        result = self._call('observe', instruction, cap=cap, format_schema=STYLE_SCHEMA)
        try:
            value = json.loads(result['text'])
        except ValueError:
            value = {}
        style, dropped = clean_style(value, [self._article(article['id'])['content']])
        if dropped:
            self.state['warnings'].append(f'{dropped} phrasing(s) quoted for {article["path"]} were not found verbatim and were dropped.')
        if not result['complete'] or not (style['aim'] or style['structure'] or style['habits']):
            self.state['warnings'].append(f'The style description of {article["path"]} is incomplete.')
        self.state['observations'][article['id']] = {**style, 'complete': result['complete']}

    def _condense(self):
        domain = next((d for d in self.state['domains'] if not d['complete']), None)
        if domain is None:
            self.state['phase'] = 'save'
            return
        notes = [self.state['observations'][i] for i in domain['articles'] if i in self.state['observations']]
        sources = [self._article(i)['content'] for i in domain['articles']]
        if len(notes) == 1:  # one article: its description is already the domain's
            domain['style'] = {k: notes[0][k] for k in ('aim', 'structure', 'habits', 'phrases')}
            domain['complete'] = True
            return
        instruction = (f'Merge these style descriptions of {len(notes)} articles from the domain "{domain["name"]}" '
                       f'({domain["description"]}) into one style for writing new papers of this kind. Keep what recurs '
                       'across articles and what is distinctive of this author; drop what is specific to one paper\'s '
                       'mathematics. The structure is the author\'s usual order of parts. Keep phrasings exactly as '
                       'quoted.\n\nDESCRIPTIONS:\n' + json.dumps(
                           [{k: n[k] for k in ('aim', 'structure', 'habits', 'phrases')} for n in notes], ensure_ascii=False, indent=1))
        result = self._call('condense', instruction, cap=2500, format_schema=STYLE_SCHEMA)
        try:
            value = json.loads(result['text'])
        except ValueError:
            value = {}
        style, _ = clean_style(value, sources)
        domain['merged'] = result['complete'] and bool(style['aim'] or style['structure'])
        if not domain['merged']:
            self.state['warnings'].append(f'Merging the "{domain["name"]}" articles failed; the first article\'s description is used.')
            style = {k: notes[0][k] for k in ('aim', 'structure', 'habits', 'phrases')}
        domain['style'], domain['complete'] = style, True

    def _profile(self):
        sources = self.state['sources']
        bodies = {s['path']: document_body(s['content']) for s in sources}
        macros, environments = {}, {}
        for source in sources:
            if source.get('derived_from_images'):
                continue  # Screenshots show wording/layout, not exact TeX macro definitions.
            for name, definition in macro_definitions(source['content']).items():
                if '@' in name:
                    continue
                count = uses(bodies[source['path']], name)
                entry = macros.setdefault(name, {'name': name, 'definition': definition, 'uses': 0, 'articles': [], 'best': -1})
                if count > entry['best']:  # the definition from the article that uses the macro most
                    entry['definition'], entry['best'] = definition, count
                if count:
                    entry['uses'] += count
                    entry['articles'].append(source['path'])
            for name, definition in environment_definitions(source['content']).items():
                entry = environments.setdefault(name, {'name': name, 'definition': definition, 'uses': 0})
                entry['uses'] += len(re.findall(r'\\begin\{' + re.escape(name) + r'\}', bodies[source['path']]))
        used = sorted((m for m in macros.values() if m['uses']), key=lambda m: (-len(m['articles']), -m['uses'], m['name']))
        paths = {s['id']: s['path'] for s in sources}
        domain_of = {i: d['name'] for d in self.state['domains'] for i in d['articles']}
        now = _now()
        return {'format': FORMAT, 'version': 1, 'name': self.state['style_name'],
                'created_at': now, 'updated_at': now, 'job': self.state['id'], 'model': self.state['settings']['model'],
                'articles': [{'path': a['path'], 'sha256': self._article(a['id'])['sha256'], 'title': a['title'],
                              'domain': domain_of.get(a['id'], '')} for a in self.state['articles']],
                'macros': [{k: m[k] for k in ('name', 'definition', 'uses', 'articles')} for m in used[:LIMITS['macros']]],
                'environments': sorted(environments.values(), key=lambda e: -e['uses'])[:LIMITS['environments']],
                'domains': [{'name': d['name'], 'description': d['description'], 'articles': [paths[i] for i in d['articles']],
                             **(d['style'] or {'aim': '', 'structure': [], 'habits': [], 'phrases': []})}
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
                    and all(d.get('merged', True) for d in self.state['domains']))
        self.state['status'] = 'saved' if complete else 'partial'
        self.state['stop_reason'] = ('Style saved; read it and correct anything that does not sound like you.' if self.state.get('saved_to')
                                     else 'The style was not saved.')

    def _progress(self):
        total = len(self.state.get('articles', [])) + len(self.state.get('domains', []))
        done = len(self.state.get('observations', {})) + sum(bool(d.get('complete')) for d in self.state.get('domains', []))
        return {'phase': self.state['phase'], 'completed': done, 'total': total, 'unit': 'articles and domains',
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
            out += ['', '## Style (as given to a write-up)', '', '```text', style_prompt(profile, limit=PROMPT_CHARS * 3), '```']
        out += ['', '## Articles', '']
        domain_of = {i: d['name'] for d in s.get('domains', []) for i in d['articles']}
        for article in s.get('articles', []):
            out.append(f'- {article["id"]} `{article["path"]}`: {article["title"] or "untitled"}'
                       f'{" — " + domain_of[article["id"]] if article["id"] in domain_of else ""}.')
        out += ['', f'Generated tokens charged: {s["tokens_charged"]}/{s["settings"]["max_tokens"]}; input tokens charged: '
                    f'{s["input_tokens_charged"]}/{s["settings"]["max_input_tokens"]}; elapsed seconds: '
                    f'{s["seconds_used"]:.1f}/{s["settings"]["max_seconds"]}.', '']
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


def style_digest(profile, domain, limit=1500):
    """The part of a domain style that helps write one document: how the author explains and phrases.

    The request sets a document's structure, so the papers' structure is left out; macros are
    given separately, after the user's own notation has been removed from them.
    """
    chosen = _domain(profile, domain)
    if chosen is None:
        return ''
    out = [f'Author style "{profile["name"]}", {chosen["name"]}: {chosen.get("description", "")}'.rstrip()]
    if chosen.get('habits'):
        out.append('How the author explains:\n' + '\n'.join('- ' + h for h in chosen['habits'][:6]))
    if chosen.get('phrases'):
        out.append('Phrasings the author uses, where they fit: ' + '; '.join(f'"{p}"' for p in chosen['phrases'][:6]))
    return '\n'.join(out)[:limit]


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
