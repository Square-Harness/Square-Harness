# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Explicit tool registry; workspace-scoped text access and approved mutations."""
from bisect import bisect_right
import codecs
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from threading import Lock

TEXT = {'.tex', '.bib', '.sty', '.cls', '.md', '.txt', '.py', '.json', '.yaml', '.yml', '.toml', '.csv'}
# File groups the user can let the model discover and read. Pinned sources are
# explicit choices and stay readable whatever is enabled here.
READ_GROUPS = {'tex': {'.tex', '.bib', '.sty', '.cls'}, 'pdf': {'.pdf'}, 'py': {'.py'},
               'text': {'.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.csv'}}
SKIP = {'__pycache__', 'node_modules', 'venv', 'build', 'dist'}
MAX_BYTES = 1_000_000
MAX_RESULT = 6000
# A workspace tool result (a read_file excerpt, a list_files page, search_text matches) may take this share of the
# model's context, at about 3 characters a token (LaTeX and extracted PDF text run close to that), and never less
# than MAX_RESULT: a 65k-token model keeps 6000 characters, DS4's 1M tokens give about 94,000 (a 79-page paper in
# three reads). Each result stays in the conversation for the rest of the turn and is sent again with every
# call, so a larger share makes long reading turns expensive. Conversation titles have their own limits.
RESULT_SHARE = 0.03
CHARS_PER_TOKEN = 3


def result_budget(ctx):
    """Characters one workspace tool result may hold for a model with `ctx` tokens of context."""
    return max(MAX_RESULT, int(ctx * RESULT_SHARE * CHARS_PER_TOKEN)) if type(ctx) is int and ctx > 0 else MAX_RESULT
_WRITE_LOCK = Lock()
SOURCE_ARCHIVE_SUFFIXES = ('.tar.gz', '.tgz', '.tar', '.gz')


def pdf_page_label(starts, line, *, first=False):
    """Mark a PDF page boundary, or identify the page of a stand-alone excerpt."""
    page = bisect_right(starts, line)
    if page and (first or starts[page - 1] == line):
        return f'[PDF page {page}] '
    return ''


def is_source_archive(path):
    return str(path).lower().endswith(SOURCE_ARCHIVE_SUFFIXES)


def source_suffix(path):
    """Source archives belong to the existing TeX read permission."""
    return '.tex' if is_source_archive(path) else Path(path).suffix.lower()

# Reading user files. UTF-8 first; otherwise the 8-bit encoding the file declares; otherwise UTF-8
# with Windows-1252 for the bytes that are not UTF-8 (French accents saved by an older editor).
FALLBACK = 'utf-8+cp1252'
INPUTENC = {'latin1': 'latin-1', 'latin9': 'iso8859-15', 'ansinew': 'cp1252', 'cp1252': 'cp1252',
            'applemac': 'mac-roman', 'macce': 'mac-latin2', 'cp850': 'cp850', 'cp437': 'cp437', 'latin2': 'iso8859-2'}
TEX_ENCODINGS = {'isolatin': 'latin-1', 'iso latin 1': 'latin-1', 'windows latin 1': 'cp1252',
                 'mac os roman': 'mac-roman', 'macosroman': 'mac-roman', 'isolatin9': 'iso8859-15', 'iso latin 9': 'iso8859-15', 'iso latin 2': 'iso8859-2'}
ENCODING_NAMES = {'iso8859-1': 'Latin-1', 'iso8859-15': 'Latin-9', 'cp1252': 'Windows-1252', 'mac-roman': 'Mac Roman',
                  'mac-latin2': 'Mac Central European', 'cp850': 'DOS (CP850)', 'cp437': 'DOS (CP437)',
                  'iso8859-2': 'Latin-2', 'utf-16': 'UTF-16', FALLBACK: 'UTF-8, with Windows-1252 for its accented bytes'}
_DECLARATION = re.compile(rb'\\usepackage\s*\[([^\]]*)\]\s*\{inputenc\}|\\inputencoding\s*\{([^}]*)\}')
_MAGIC = re.compile(rb'^\s*%\s*!TEX\s+encoding\s*=\s*(.+?)\s*$', re.I)
_TEXT_DECLARATION = re.compile(r'(\\usepackage\s*\[)[^\]]*(\]\s*\{inputenc\})|(\\inputencoding\s*\{)[^}]*(\})'
                               r'|^(\s*%\s*!TEX\s+encoding\s*=\s*).+$', re.I | re.M)


def _windows_1252(error):
    return error.object[error.start:error.end].decode('cp1252', errors='replace'), error.end


codecs.register_error('square-cp1252', _windows_1252)


def _codec(name):
    name = name.strip().decode('ascii', errors='ignore').lower()
    name = INPUTENC.get(name) or TEX_ENCODINGS.get(name) or name
    if name in ('utf8', 'utf8x', 'utf-8 unicode'):
        return 'utf-8'
    try:
        codec = codecs.lookup(name)
        # Exclude registered byte transforms such as hex, base64 and rot13.
        if not getattr(codec, '_is_text_encoding', True):
            raise ValueError(f'{name!r} is a byte transformation, not a text encoding')
        ''.encode(codec.name)
        return codec.name
    except LookupError:
        return None


def _declared_encoding(data):
    """The encoding a TeX source declares, including explicit UTF-8; None if undeclared."""
    for line in data.splitlines():
        magic = _MAGIC.match(line)
        if magic:
            codec = _codec(magic.group(1))
            if codec:
                return codec
        code = re.split(rb'(?<!\\)%', line, maxsplit=1)[0]
        for options, name in _DECLARATION.findall(code):
            for option in reversed((options or name).split(b',')):
                codec = _codec(option)
                if codec:
                    return codec
    return None


def decode_text(data, name):
    """The text of a user file and the encoding read, None for UTF-8 (see FALLBACK above).

    A byte-order mark decides first. A file containing NUL bytes is binary; the error names it.
    """
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text, encoding = data.decode('utf-16', errors='replace'), 'utf-16'
    else:
        try:
            text, encoding = data.decode('utf-8-sig'), None
        except UnicodeDecodeError:
            try:
                encoding = _declared_encoding(data) or FALLBACK
            except ValueError as exc:
                raise ValueError(f'{name}: {exc}') from None
            if encoding in ('utf-8', 'utf-8-sig'):
                encoding = FALLBACK  # Keep recovering older accented bytes in files declaring UTF-8.
            text = data.decode('utf-8', errors='square-cp1252') if encoding == FALLBACK else data.decode(encoding, errors='replace')
    if '\x00' in text:
        raise ValueError(f'{name} is not a text file (it contains NUL bytes)')
    return text, encoding


def unicode_source(text, encoding):
    """A decoded copy of a legacy or UTF-16 file declares UTF-8 when written out.

    Only copies pinned or saved by jobs: the user's own file keeps its encoding and declaration.
    """
    if encoding in (None, FALLBACK):
        return text
    return _TEXT_DECLARATION.sub(lambda m: (m.group(1) + 'utf8' + m.group(2) if m.group(1) else
                                            m.group(3) + 'utf8' + m.group(4) if m.group(3) else m.group(5) + 'UTF-8'), text)


def schema(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': list(required), 'additionalProperties': False}}}


_TEX_TITLE = re.compile(r'\\title\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}', re.S)


def _file_title(path, deadline):
    """(label, words) naming a document when it is cheap to read: \\title{} of a .tex, the first heading of a
    Markdown file, the title in a PDF's metadata, else the first line of its first page ("first line": not a
    checked title). ('', '') otherwise, and once `deadline` (time.monotonic) has passed."""
    if time.monotonic() >= deadline:
        return '', ''
    suffix = path.suffix.lower()
    label = 'title'
    try:
        if suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            title = str((reader.metadata or {}).get('/Title') or '')
            if len(title.strip()) < 8 and reader.pages:
                text = reader.pages[0].extract_text() or ''
                title, label = next((line for line in text.splitlines() if len(line.strip()) > 12), ''), 'first line'

        elif suffix in ('.tex', '.md', '.txt'):
            with open(path, 'rb') as stream:
                head = stream.read(20000).decode('utf-8', 'replace')
            match = _TEX_TITLE.search(head) if suffix == '.tex' else re.search(r'^#\s+(.+)$', head, re.M)
            title = match.group(1) if match else ''
        else:
            return '', ''
    except Exception:  # an unreadable or malformed file simply has no title here
        return '', ''
    greek = ('alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi rho sigma '
             'tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega').split()
    title = re.sub(r'\\([a-zA-Z]+)\*?', lambda m: f' {m.group(1)} ' if m.group(1) in greek else ' ', title)
    title = re.sub(r'[{}$\\~]', ' ', title)
    words = ' '.join(title.split())[:160]
    return (label, words) if words else ('', '')


def workspace_overview(workspace, max_files=300, max_chars=14000, title_seconds=2.0):
    """What the open folder holds, for the planners (the routing step and the main agent): one line per file the
    model may be given (the kinds File access allows, plus the attachments), with its kind, size, date and a
    title when cheap. Files from earlier conversations are there too. With more files than fit, the most
    recently modified are kept. Returns (text, paths)."""
    root, allowed = workspace.root, workspace.allowed_types | {'.png', '.jpg', '.jpeg', '.webp'}
    found = []
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP and not (Path(folder) / d).is_symlink())
        for name in names:
            path = Path(folder) / name
            if name.startswith('.') or path.is_symlink() or (source_suffix(path) not in allowed and path not in workspace.pinned):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                found.append((info.st_mtime, path, info.st_size))
                if len(found) >= 5000:
                    break
        if len(found) >= 5000:
            break
    total = len(found)
    found.sort(key=lambda item: -item[0])
    deadline = time.monotonic() + title_seconds
    lines, paths, size = [], [], 0
    for mtime, path, nbytes in found[:max_files]:
        rel = path.relative_to(root).as_posix()
        kind = 'PDF' if path.suffix.lower() == '.pdf' else 'image' if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') \
            else source_suffix(path).lstrip('.') or 'file'
        amount = f'{nbytes / 1e6:.1f} MB' if nbytes >= 1e6 else f'{max(1, round(nbytes / 1e3))} kB'
        label, title = _file_title(path, deadline)
        line = (f'{rel} · {kind} · {amount} · {time.strftime("%Y-%m-%d", time.localtime(mtime))}'
                + (f' · {label}: {title}' if title else '') + (' · attached to this message' if path in workspace.pinned else ''))
        if size + len(line) + 1 > max_chars:
            break
        lines.append(line)
        paths.append(rel)
        size += len(line) + 1
    order = sorted(range(len(lines)), key=lambda i: paths[i])
    text = '\n'.join(lines[i] for i in order)
    if total > len(lines):
        text += f'\n[{total - len(lines)} older files not listed: use list_files or search_text]'
    return text, sorted(paths)


class Workspace:
    def __init__(self, root, approve=lambda preview: False, allow_python=False, literature=None, read_types=None,
                 numerics=None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Workspace must be a directory')
        self.approve = approve
        self.allow_python = allow_python
        self.numerics = numerics  # A sandbox session bound by controller code (mathagent.numerics).
        self.literature = literature
        self.web = None
        if literature is not None:
            from .web import WebTools
            self.web = WebTools(literature)
        # Set by the interface for answers: runs a literature check (a nested,
        # separately budgeted verifier) and returns its compact JSON result.
        self.checker = None
        self.cancelled = lambda: False
        groups = set(READ_GROUPS) if read_types is None else set(read_types)
        if groups - set(READ_GROUPS):
            raise ValueError('Unknown file group; use tex, pdf, py or text')
        self.readable = set().union(*(READ_GROUPS[g] for g in groups)) if groups else set()
        # The kinds of files File access allows, kept when a message's attachments narrow `readable`:
        # the workspace overview (workspace_overview) lists those, never more.
        self.allowed_types = set(self.readable)
        self.index = None
        self.result_chars = MAX_RESULT  # set from the model's context by its Agent (result_budget)
        self.pinned = set()
        self.selected_files_only = False
        self.encodings = {}  # path -> encoding of the last read, for files that are not UTF-8
        self.source_archives = {}  # path -> last bounded, immutable source extraction
        if self.literature is None:
            self.readable.discard('.pdf')  # PDF extraction uses the literature backend

    def pin_files(self, files, *, selected_only=False):
        """Pin source paths, optionally limiting this engine's local reads to the selection."""
        paths = {self.path(filename, pdf=self.literature is not None) for filename in files}
        if selected_only:
            self.selected_files_only = True
            self.readable.clear()
            self.pinned.clear()
        self.pinned.update(paths)

    def path(self, filename, *, pdf=False):
        if not isinstance(filename, str) or not filename:
            raise ValueError('Provide a nonempty relative filename')
        rel = Path(filename)
        if rel.is_absolute() or '..' in rel.parts or any(p.startswith('.') for p in rel.parts):
            raise ValueError('Use a relative path; parent traversal and hidden paths are excluded')
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError('Path escapes workspace, possibly through a symlink')
        if any(part.startswith('.') for part in p.relative_to(self.root).parts):
            raise ValueError('Hidden paths are excluded, including symlink targets')
        if source_suffix(p) not in TEXT and not (pdf and p.suffix.lower() == '.pdf'):
            raise ValueError('Unsupported extension; use text/LaTeX/Markdown/Python files or a TeX source archive (.tar.gz/.tgz/.tar/.gz)')
        return p

    def _readable(self, p):
        if self.selected_files_only and p not in self.pinned:
            raise ValueError('This file is outside the document selection for this request')
        if source_suffix(p) not in self.readable and p not in self.pinned:
            raise ValueError(f'The user has not allowed reading {p.suffix} files in this workspace; '
                             'ask them to pin the file or enable that file type')

    def text(self, p, *, deadline=None):
        name = p.relative_to(self.root).as_posix() if p.is_relative_to(self.root) else p.name
        archive = is_source_archive(p)
        if archive:
            from .arxiv_source import MAX_ARCHIVE_BYTES
            limit = MAX_ARCHIVE_BYTES
        else:
            limit = MAX_BYTES
        # A non-regular path must not block in open() before fstat can refuse it.
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
                raise ValueError(f'{name}: only regular {"source archives up to 10 MB" if archive else "text files up to 1 MB"} can be read')
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'{name}: file exceeds its bounded read limit')
        if archive:
            from .arxiv_source import extract_source
            if self.cancelled():
                raise KeyboardInterrupt
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError('Source archive reading exceeded its time limit')
            cached = self.source_archives.get(p)
            if cached and cached['sha256'] == hashlib.sha256(data).hexdigest():
                return cached['text']
            until = min(deadline, time.monotonic() + 10) if deadline is not None else time.monotonic() + 10
            extraction = extract_source(data, deadline=until, cancelled=self.cancelled)
            self.source_archives.pop(p, None)
            self.source_archives[p] = extraction
            while len(self.source_archives) > 4:
                self.source_archives.pop(next(iter(self.source_archives)))
            self.encodings.pop(p, None)
            return extraction['text']
        value, encoding = decode_text(data, name)
        if encoding:
            self.encodings[p] = encoding
        else:
            self.encodings.pop(p, None)
        return value

    def files(self, suffixes=None):
        allowed = TEXT if suffixes is None else suffixes
        if not allowed or self.selected_files_only:
            return
        count = 0
        for folder, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP
                             and not (Path(folder) / d).is_symlink())
            for name in sorted(files):
                p = Path(folder) / name
                if name.startswith('.') or source_suffix(p) not in allowed or p.is_symlink():
                    continue
                yield p
                count += 1
                if count >= 2000:
                    return

    def list_files(self, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Use a nonnegative file offset and a limit from 1 to 1000')
        paths = sorted(self.pinned) if self.selected_files_only else self.files(self.readable)
        names = [str(p.relative_to(self.root)) for p in paths]
        selected, size = [], 0
        for name in names[offset:offset + limit]:
            if selected and size + len(name) + 1 > self.result_chars - 300:
                break
            selected.append(name)
            size += len(name) + 1
        next_offset = offset + len(selected)
        suffix = (f'\n[Continue list_files with offset={next_offset}]' if next_offset < len(names) else '')
        return ('\n'.join(selected) or '(no readable files at this offset)') + suffix

    def read_file(self, path, start_line=1, end_line=None, start_column=1):
        # Without end_line, the excerpt runs as far as the result budget allows (result_chars, a share of the
        # model's context); a cut excerpt says where to continue.
        if end_line is None:
            end_line = start_line + 10 ** 9 if type(start_line) is int else 1
        if type(start_column) is not int or start_column < 1:
            raise ValueError('start_column must be a positive character position on the first line')
        if type(start_line) is not int or type(end_line) is not int or not 1 <= start_line <= end_line:
            raise ValueError('Line bounds must be positive integers with start <= end')
        p = self.path(path, pdf=True)
        self._readable(p)
        pagination, page_starts = '', ()
        if p.suffix.lower() == '.pdf':
            # Local extraction, cached with the literature evidence; nothing is uploaded.
            summary = self.literature.import_local(str(p.relative_to(self.root)))
            metadata, lines = self.literature._document(summary['document_id'])
            page_starts = metadata.get('pdf_page_starts', ())
            if metadata.get('extracted_pages'):
                pagination = f"{metadata['extracted_pages']} extracted PDF pages; "
        else:
            lines = self.text(p).splitlines()
        end = min(end_line, len(lines))
        result = f'{path}: {pagination}{len(lines)} total lines; requested excerpt {start_line}-{end}\n'
        remaining = self.result_chars - len(result) - 250
        selected = []
        for index in range(start_line - 1, end):
            column = start_column if index == start_line - 1 else 1
            text = lines[index][column - 1:]
            prefix = f'{index + 1}: ' if column == 1 else f'{index + 1} (from column {column}): '
            prefix = pdf_page_label(page_starts, index + 1, first=index + 1 == start_line) + prefix
            if len(prefix) + len(text) + 1 > remaining:
                if selected:
                    return result + '\n'.join(selected) + f'\n[Continue read_file at start_line={index + 1}, start_column={column}]'
                take = max(1, remaining - len(prefix) - 1)
                return result + prefix + text[:take] + f'\n[Partial line; continue read_file at start_line={index + 1}, start_column={column + take}]'
            selected.append(prefix + text)
            remaining -= len(prefix) + len(text) + 1
        return result + '\n'.join(selected)

    def search_text(self, pattern, offset=0):
        if not isinstance(pattern, str) or not pattern:
            raise ValueError('Provide nonempty literal search text (not regex)')
        if type(offset) is not int or offset < 0:
            raise ValueError('Use a nonnegative file offset')
        hits, needle = [], pattern.casefold()
        deadline = time.monotonic() + 10
        # PDFs are searched in their local text extraction (as read_file reads them): pinned ones,
        # and all readable ones when the user allows PDFs. Nothing is uploaded.
        pdfs = [] if self.literature is None else [q for q in self.pinned if q.suffix.lower() == '.pdf']
        if self.literature is not None and '.pdf' in self.readable:
            pdfs += [q for q in self.files({'.pdf'}) if q not in pdfs]
        paths = list(dict.fromkeys(list(self.files(self.readable - {'.pdf'}))
                                  + sorted(self.pinned - set(pdfs)) + pdfs))
        for index, p in enumerate(paths[offset:], offset):
            if self.cancelled():
                raise KeyboardInterrupt()
            if time.monotonic() >= deadline:
                return ('\n'.join(hits) or '(no matches in files scanned so far)') + f'\n[Search time limit; continue search_text with offset={index}]'
            page_starts = ()
            try:
                if p.suffix.lower() == '.pdf':
                    previous = self.literature.deadline
                    self.literature.deadline = min(deadline, previous) if previous is not None else deadline
                    try:
                        summary = self.literature.import_local(str(p.relative_to(self.root)))
                        metadata, lines = self.literature._document(summary['document_id'])
                        page_starts = metadata.get('pdf_page_starts', ())
                    finally:
                        self.literature.deadline = previous
                else:
                    text = self.text(p, deadline=deadline)
                    if needle not in text.casefold():
                        continue
                    lines = text.splitlines()
            except (OSError, ValueError, UnicodeError):  # LiteratureError is a ValueError
                continue
            for i, line in enumerate(lines, 1):
                if needle in line.casefold():
                    page = pdf_page_label(page_starts, i, first=True)
                    hits.append(f'{p.relative_to(self.root)}:{i}: {page}{line[:500]}')
                    if len(hits) >= 50:
                        return '\n'.join(hits) + '\n[50-match limit]'
        return '\n'.join(hits) or '(no matches in supported files scanned)'

    def write_file(self, path, content):
        return self._write_file(path, content, max_bytes=100_000)

    def _write_file(self, path, content, *, max_bytes, expected=None):
        if not isinstance(content, str) or len(content.encode()) > max_bytes:
            raise ValueError(f'Content must be text, at most {max_bytes} bytes')
        p = self.path(path)
        if is_source_archive(p):
            raise ValueError('Source archives are read-only; request a separate text file instead')
        original = p.stat() if p.exists() else None
        old = self.text(p) if original is not None else ''
        if expected is not None and old != expected:
            raise ValueError(f'{path} changed before the edit preview; read it again. No file changed.')
        encoding = self.encodings.get(p) if original is not None else None
        # Preserve an existing CRLF convention; normalize only the displayed diff.
        if '\r\n' in old and '\n' not in old.replace('\r\n', ''):
            content = content.replace('\r\n', '\n').replace('\n', '\r\n')
        # The replacement may explicitly change/remove its declaration, or be a new file.
        try:
            kept = _declared_encoding(content.encode('utf-8')) or 'utf-8'
        except ValueError as exc:
            raise ValueError(f'{path}: {exc}; no file changed') from None
        try:
            data = content.encode(kept)
        except UnicodeEncodeError as exc:
            raise ValueError(f'{path} declares {ENCODING_NAMES.get(kept, kept)}, which cannot hold '
                             f'{content[exc.start:exc.end]!r}; no file changed. Save the file as UTF-8 first.') from None
        note = (f' (saved as {ENCODING_NAMES.get(kept, kept)})' if kept != 'utf-8' else
                ' (saved as UTF-8; it was not valid UTF-8)' if encoding == FALLBACK else '')
        diff = ''.join(difflib.unified_diff(old.replace('\r\n', '\n').splitlines(True), content.replace('\r\n', '\n').splitlines(True),
                                           fromfile=path + ' (old)', tofile=path + ' (new)'))
        if not self.approve(f'WRITE {p}{note}\n{diff or "(no textual changes)"}'):
            return 'User denied the write. No file changed.'
        with _WRITE_LOCK:
            target = self.path(path)
            current = target.stat() if target.exists() else None
            if (target != p or (original is None) != (current is None)
                    or (original is not None and
                        ((original.st_dev, original.st_ino, original.st_mtime_ns) !=
                         (current.st_dev, current.st_ino, current.st_mtime_ns) or self.text(target) != old))):
                raise ValueError(f'{path} changed while approval was pending; request a new diff. No file changed.')
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.square-write-', dir=p.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    if original is not None:
                        os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, p)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return f'Wrote {path} ({len(data)} bytes)'

    def replace_text(self, path, old_text, new_text):
        """A unique exact replacement with write_file's approval and race checks."""
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ValueError('Provide a nonempty old_text and a text replacement')
        p = self.path(path)
        if is_source_archive(p):
            raise ValueError('Source archives are read-only; request a separate text file instead')
        self._readable(p)
        old = self.text(p)
        if old.count(old_text) != 1:
            raise ValueError('old_text must match exactly once; read a larger unique passage first')
        if max(len(old_text.encode()), len(new_text.encode())) > 100_000:
            raise ValueError('The changed passage must be at most 100 KB')
        return self._write_file(path, old.replace(old_text, new_text, 1), max_bytes=MAX_BYTES, expected=old)

    def run_python(self, code):
        if not self.allow_python:
            return 'Python execution disabled; restart with --allow-python to enable approval prompts.'
        if not isinstance(code, str) or len(code) > 20000:
            raise ValueError('Provide Python source, at most 20000 characters')
        if not self.approve('RUN PYTHON (not sandboxed; has your account permissions and the server\'s environment)\n' + code):
            return 'User denied Python execution.'
        # A timeout and output-file limit are guardrails, NOT a security sandbox.
        wrapper = '''import resource
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
'''
        with tempfile.TemporaryFile() as out:
            p = subprocess.Popen([sys.executable, '-c', wrapper + code], cwd=self.root,
                                 stdout=out, stderr=out, start_new_session=True)
            timed_out = False
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also clean up ordinary descendants after an early parent exit.
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()
            out.seek(0)
            data = out.read(MAX_RESULT + 1)
            output = data[:MAX_RESULT].decode('utf-8', errors='replace')
        return f'Exit {p.returncode}; timeout={timed_out}\n{output}' + (
            f'\n[output cut at {MAX_RESULT} bytes]' if len(data) > MAX_RESULT else '')

    def check_reference(self, statement, guesses=None):
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 2000:
            raise ValueError('Describe the result to find a reference for, in at most 2000 characters')
        if guesses is not None and (not isinstance(guesses, list) or not all(isinstance(g, dict) for g in guesses)):
            raise ValueError('guesses must be a list of objects')
        return self.checker(statement.strip(), (guesses or [])[:3])

    def schemas(self):
        string = {'type': 'string'}
        integer = {'type': 'integer'}
        kinds = 'selected documents only' if self.selected_files_only else ', '.join(sorted(self.readable))
        if '.tex' in self.readable:
            kinds += ', .tar.gz/.tgz/.tar/.gz TeX sources'
        result = [] if not self.readable and not self.pinned else [
            schema('list_files', f'List nonhidden files the user allows you to read ({kinds}; scan cap 2000). Continue with offset when paginated.', {'offset': integer, 'limit': integer}),
            schema('search_text', 'Case-insensitive literal search in allowed files'
                   + (', including the extracted text of attached or readable PDFs' if self.literature is not None else ' (not PDFs)')
                   + '; max 50 matches and 10 seconds. Continue with the returned file offset.',
                   {'pattern': string, 'offset': integer}, ['pattern']),
        ]
        if self.readable or self.pinned:
            result.append(schema('read_file', 'Read numbered lines of an allowed or explicitly pinned file'
                   + (', including extracted PDF text' if self.literature is not None else '')
                   + '; TeX source archives are read as a labelled, read-only source bundle; members are never executed or written to disk'
                   + f'. At most {self.result_chars:,} characters a call; a longer excerpt says where to continue.',
                   {'path': string, 'start_line': integer, 'end_line': integer, 'start_column': integer}, ['path']))
        result += [
            schema('write_file', 'Create/replace a text file ONLY when requested by the user; shows a diff for approval.',
                   {'path': string, 'content': string}, ['path', 'content']),
            schema('replace_text', 'Replace one exact unique passage in an allowed file; shows a diff for approval.',
                   {'path': string, 'old_text': string, 'new_text': string}, ['path', 'old_text', 'new_text']),
        ]
        if self.allow_python and (self.literature is None or self.literature.online):
            result.append(schema('run_python', 'Run Python after explicit approval. Not sandboxed. 30s timeout.',
                                 {'code': string}, ['code']))
        if self.literature is not None:
            web_schemas = self.web.schemas() if self.web is not None else []
            web_names = {item['function']['name'] for item in web_schemas}
            result.extend(item for item in self.literature.schemas() if item['function']['name'] not in web_names)
            result.extend(web_schemas)
        if self.index is not None:
            result.extend(self.index.schemas())
        if self.numerics is not None:
            result.extend(self.numerics.schemas())
        if self.checker is not None and self.literature is not None and self.literature.online:
            guess = {'type': 'object', 'properties': {
                'authors': {'type': 'array', 'items': string}, 'title': string, 'year': string,
                'locator': {'type': 'string', 'description': 'Theorem, proposition or section, e.g. "Theorem 9.26"'}}}
            result.append(schema('check_reference',
                'Find and verify a precise reference (book or paper, and the theorem or section) for a known result. '
                'Give the result and your best guesses of where it is stated; a separate verifier checks them against '
                'zbMATH, Crossref, OpenCitations and open papers and returns how far each was confirmed (read, cited, located, not '
                'found). Takes a few minutes. Use it before giving a theorem number from memory.',
                {'statement': string, 'guesses': {'type': 'array', 'items': guess}}, ['statement']))
        return result

    def execute(self, name, arguments, *, on_result=None):
        allowed = {s['function']['name'] for s in self.schemas()}
        try:
            if name not in allowed:
                raise ValueError(f'Unknown or disabled tool: {name}')
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError('Tool arguments must be an object')
            if self.index is not None and name in self.index.names:
                return self.index.execute(name, arguments, budget=self.result_chars)
            if self.numerics is not None and name in self.numerics.tool_names:
                return self.numerics.execute(name, arguments)
            if self.web is not None and name in {item['function']['name'] for item in self.web.schemas()}:
                return self.web.execute(name, arguments, on_result=on_result)
            if self.literature is not None and name in {
                    item['function']['name'] for item in self.literature.schemas()}:
                # The literature layer returns bounded valid JSON and handles
                # its own offline gate. Do not split citation JSON mid-record.
                return self.literature.execute(name, arguments)
            value = str(getattr(self, name)(**arguments))
            return value if len(value) <= self.result_chars else value[:self.result_chars] + '\n[TRUNCATED: request a smaller excerpt]'
        except (OSError, ValueError, TypeError, UnicodeError) as e:
            return f'Tool error: {e}'
