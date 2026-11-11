# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Bounded TeX-source reading without extracting archive paths or executing TeX.

Archive members are untrusted data. Only their regular text-source contents
are decoded; no pathname is created and no TeX include or command is run.
"""
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import re
import tarfile
import time
import unicodedata
import zlib

from .tools import decode_text, unicode_source

MAX_ARCHIVE_BYTES = 10_000_000
MAX_UNCOMPRESSED_BYTES = 32_000_000
MAX_SOURCE_FILE_BYTES = 2_000_000
MAX_SOURCE_TEXT_BYTES = 4_000_000
MAX_MEMBERS = 512
MAX_PATH_BYTES = 500
MAX_METADATA_BYTES = 64_000
SOURCE_SUFFIXES = frozenset({'.tex', '.ltx', '.latex', '.bib', '.bbl', '.sty', '.cls', '.bst', '.def'})
TEX_SUFFIXES = frozenset({'.tex', '.ltx', '.latex'})
_CHUNK = 32_768
_TEX_SIGNATURE = re.compile(r'\\(?:documentclass|documentstyle|begin|end|input|include|section|chapter|def|newcommand|bye)\b')


class SourceArchiveError(ValueError):
    """An invalid, unsafe or over-limit source response; no archive path is written."""


def is_source_archive(filename):
    return isinstance(filename, str) and filename.lower().endswith(('.tar.gz', '.tgz', '.tar', '.gz'))


def _digest(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def _check(deadline, cancelled):
    if cancelled():
        raise KeyboardInterrupt
    if time.monotonic() >= deadline:
        raise SourceArchiveError('Source archive reading exceeded its time limit')


def _gunzip(body, deadline, cancelled):
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pieces, total = [], 0
    try:
        for offset in range(0, len(body), _CHUNK):
            pending = body[offset:offset + _CHUNK]
            while pending:
                _check(deadline, cancelled)
                piece = decoder.decompress(pending, min(_CHUNK, MAX_UNCOMPRESSED_BYTES - total + 1))
                total += len(piece)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise SourceArchiveError('Source archive exceeds the 32 MB decompressed limit')
                pieces.append(piece)
                pending = decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or pending or offset + _CHUNK < len(body):
                        raise SourceArchiveError('Trailing data or concatenated gzip streams are not supported')
                    break
        if not decoder.eof:
            raise SourceArchiveError('Truncated or invalid gzip source response')
    except zlib.error as exc:
        raise SourceArchiveError('Invalid gzip source response') from exc
    _check(deadline, cancelled)
    return b''.join(pieces)


def _pax_fields(data):
    """Validate bounded PAX records before tarfile follows extension headers."""
    fields, position = {}, 0
    while position < len(data):
        space = data.find(b' ', position)
        token = data[position:space] if space >= 0 else b''
        if not token or len(token) > len(str(MAX_METADATA_BYTES)) or not token.isdigit():
            raise SourceArchiveError('Malformed tar metadata record')
        length = int(token)
        end = position + length
        if length <= space - position + 2 or end > len(data) or data[end - 1:end] != b'\n':
            raise SourceArchiveError('Malformed tar metadata record')
        record = data[space + 1:end - 1]
        key, equal, value = record.partition(b'=')
        if not equal or not key or key in fields:
            raise SourceArchiveError('Malformed or duplicate tar metadata field')
        if len(fields) >= 64:
            raise SourceArchiveError('Tar metadata contains too many fields')
        if key in (b'size', b'linkpath') or b'sparse' in key.lower() or key == b'SCHILY.realsize':
            raise SourceArchiveError('Tar size overrides, links and sparse storage are not supported')
        fields[key] = value
        position = end
    return fields


def _scan_tar(body, deadline, cancelled):
    """Check physical headers, including extension records hidden by tarfile.

    This bounds metadata-header recursion and requires complete member padding
    and the terminating zero blocks before the semantic parser is invoked.
    """
    position, members, metadata_run = 0, 0, 0
    extensions = {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME}
    allowed = {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE} | extensions
    while position + 512 <= len(body):
        _check(deadline, cancelled)
        header = body[position:position + 512]
        if header == b'\0' * 512:
            if len(body) - position < 1024 or any(body[position:]):
                raise SourceArchiveError('Truncated tar terminator or trailing nonzero tar data')
            return
        try:
            member = tarfile.TarInfo.frombuf(header, 'utf-8', 'surrogateescape')
        except (tarfile.HeaderError, ValueError, OverflowError) as exc:
            raise SourceArchiveError('Invalid or truncated tar source archive') from exc
        members += 1
        if members > MAX_MEMBERS:
            raise SourceArchiveError('Source archive exceeds the 512-member limit')
        if member.type not in allowed or member.sparse is not None:
            raise SourceArchiveError('Source archive contains links, sparse files or special members')
        if member.size < 0 or member.size > MAX_UNCOMPRESSED_BYTES:
            raise SourceArchiveError('Invalid or oversized archive member')
        end = position + 512 + member.size
        padded = position + 512 + ((member.size + 511) // 512) * 512
        if padded > len(body) or any(body[end:padded]):
            raise SourceArchiveError('Truncated member or invalid tar padding')
        if member.type in extensions:
            metadata_run += 1
            if metadata_run > 16 or member.size > MAX_METADATA_BYTES:
                raise SourceArchiveError('Source archive has excessive tar metadata')
            if member.type in (tarfile.XHDTYPE, tarfile.XGLTYPE):
                _pax_fields(body[position + 512:end])
            elif member.size > MAX_PATH_BYTES + 1:
                raise SourceArchiveError('Source archive pathname exceeds the length limit')
        else:
            metadata_run = 0
            if member.isdir() and member.size:
                raise SourceArchiveError('Directory members cannot contain file data')
        position = padded
    raise SourceArchiveError('Source archive has no complete tar terminator')


def _safe_name(name, directory=False):
    if not isinstance(name, str) or not name:
        raise SourceArchiveError('Source archive has an empty pathname')
    if any(unicodedata.category(char).startswith('C') for char in name):
        raise SourceArchiveError('Source archive pathname contains control or invalid characters')
    if '\\' in name or ':' in name or name.startswith('/'):
        raise SourceArchiveError('Absolute and Windows archive paths are not supported')
    while name.startswith('./'):
        name = name[2:]
    if directory:
        name = name.rstrip('/')
    if name in ('', '.') and directory:
        return None  # The harmless root-directory marker used by tar -cf archive .
    if not name or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in name.split('/')):
        raise SourceArchiveError('Source archive pathname contains traversal or ambiguous components')
    if len(name.encode('utf-8')) > MAX_PATH_BYTES or len(name.split('/')) > 32:
        raise SourceArchiveError('Source archive pathname exceeds the length or depth limit')
    return name


def _source_text(data, name):
    if len(data) > MAX_SOURCE_FILE_BYTES:
        raise SourceArchiveError('A source text member exceeds the 2 MB per-file limit')
    try:
        text, encoding = decode_text(data, name)
    except (ValueError, UnicodeError, LookupError) as exc:
        raise SourceArchiveError('Cannot decode source member ' + repr(name) + ': ' + str(exc)) from exc
    if any(ord(char) < 32 and char not in '\r\n\t\f' for char in text):
        raise SourceArchiveError('Source text member contains binary control characters: ' + repr(name))
    # Cached text is Unicode; line ranges refer to these normalized decoded
    # lines. The original archive and each raw member keep their own digest.
    text = '\n'.join(unicode_source(text, encoding).splitlines())
    return text, encoding


def extract_source(body, *, deadline=None, cancelled=lambda: False):
    """Read a tar/gzip/plain TeX response into bounded text and a file manifest.

    Included member start_line/end_line locate the decoded source itself in
    ``text`` (one-based, excluding banners). Input bytes and normalized text
    have separate hashes. Gzip original filenames are intentionally ignored.
    """
    if not isinstance(body, bytes) or not body:
        raise SourceArchiveError('Provide a nonempty binary source response')
    if len(body) > MAX_ARCHIVE_BYTES:
        raise SourceArchiveError('Source archive exceeds the 10 MB input limit')
    if not callable(cancelled):
        raise SourceArchiveError('Invalid source cancellation callback')
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
        raise SourceArchiveError('Invalid source reading deadline')
    deadline = min(deadline, time.monotonic() + 10) if deadline is not None else time.monotonic() + 10
    _check(deadline, cancelled)
    compressed = body.startswith(b'\x1f\x8b')
    payload = _gunzip(body, deadline, cancelled) if compressed else body
    files, sources, source_bytes = [], [], 0
    archive = None
    is_tar = False
    if len(payload) >= 512:
        try:
            tarfile.TarInfo.frombuf(payload[:512], 'utf-8', 'surrogateescape')
            is_tar = True
        except tarfile.HeaderError:
            is_tar = payload[:512] == b'\0' * 512
    if is_tar:
        # Scan physical metadata before tarfile recursively consumes PAX/GNU
        # extension chains while opening even the first logical member.
        _scan_tar(payload, deadline, cancelled)
        try:
            archive = tarfile.open(fileobj=io.BytesIO(payload), mode='r:')
        except (tarfile.TarError, OSError, EOFError, ValueError, OverflowError, RecursionError) as exc:
            raise SourceArchiveError('Invalid or unsafe tar source archive') from exc
    if archive is None:
        if payload.startswith(b'%PDF'):
            raise SourceArchiveError('This source response is a PDF, not TeX source')
        text, encoding = _source_text(payload, 'main.tex')
        if not text.strip() or not _TEX_SIGNATURE.search(text):
            raise SourceArchiveError('No readable TeX source found in this response')
        files.append({'path': 'main.tex', 'bytes': len(payload), 'sha256': _digest(payload),
                      'included': True, 'encoding': encoding})
        sources.append((files[-1], text))
        format_name = 'gzip-tex' if compressed else 'tex'
    else:
        try:
            seen, parents, spellings = {}, set(), {}
            for member in archive:
                _check(deadline, cancelled)
                if not (member.isfile() or member.isdir()) or member.issparse():
                    raise SourceArchiveError('Source archive contains links, sparse files or special members')
                name = _safe_name(member.name, member.isdir())
                if name is None:
                    continue
                parts = name.split('/')
                for index in range(1, len(parts) + 1):
                    path = '/'.join(parts[:index])
                    key = unicodedata.normalize('NFC', path).casefold()
                    if key in spellings and spellings[key] != path:
                        raise SourceArchiveError('Archive paths collide after case or Unicode normalization')
                    spellings[key] = path
                key = unicodedata.normalize('NFC', name).casefold()
                parent_keys = [unicodedata.normalize('NFC', '/'.join(parts[:index])).casefold() for index in range(1, len(parts))]
                if key in seen or any(seen.get(parent) == 'file' for parent in parent_keys) or member.isfile() and key in parents:
                    raise SourceArchiveError('Duplicate or conflicting archive path: ' + repr(name))
                seen[key] = 'directory' if member.isdir() else 'file'
                parents.update(parent_keys)
                if member.isdir():
                    continue
                data = memoryview(payload)[member.offset_data:member.offset_data + member.size]
                item = {'path': name, 'bytes': member.size, 'sha256': _digest(data), 'included': False}
                files.append(item)
                if PurePosixPath(name).suffix.lower() not in SOURCE_SUFFIXES:
                    item['reason'] = 'non-source asset; not decoded or executed'
                    continue
                text, encoding = _source_text(bytes(data), name)
                if not text.strip():
                    item['reason'] = 'empty source text'
                    continue
                item.update(included=True, encoding=encoding)
                sources.append((item, text))
                source_bytes += len(text.encode('utf-8'))
                if source_bytes > MAX_SOURCE_TEXT_BYTES:
                    raise SourceArchiveError('Combined source text exceeds the 4 MB limit')
            format_name = 'tar.gz' if compressed else 'tar'
        except (tarfile.TarError, OSError, EOFError, ValueError, OverflowError, RecursionError) as exc:
            if isinstance(exc, SourceArchiveError):
                raise
            raise SourceArchiveError('Invalid or unsafe tar source archive') from exc
        finally:
            archive.close()
        if not any(item['included'] and PurePosixPath(item['path']).suffix.lower() in TEX_SUFFIXES for item in files):
            raise SourceArchiveError('Source archive contains no readable TeX source file')
    warnings = ['Untrusted source data. Files are listed in archive order, not compilation order; TeX includes and macros are not expanded and no commands are executed.']
    omitted = sum(not item['included'] for item in files)
    if omitted:
        warnings.append(f'{omitted} non-source or empty asset(s) omitted from the text; consult the file manifest and original archive.')
    if archive is None:
        warnings.append('The single TeX response is labelled main.tex; the gzip filename is not used.')
    lines = ['TEX SOURCE BUNDLE — UNTRUSTED SOURCE DATA', *warnings, '', 'FILE MANIFEST:']
    lines.extend(json.dumps({'path': item['path'], 'bytes': item['bytes'], 'included': item['included']}, ensure_ascii=False) for item in files)
    for item, source in sources:
        _check(deadline, cancelled)
        lines.extend(['', 'BEGIN SOURCE FILE ' + json.dumps(item['path'], ensure_ascii=False)])
        item['start_line'] = len(lines) + 1
        contents = source.split('\n')
        lines.extend(contents)
        item.update(end_line=len(lines), lines=len(contents), text_sha256=_digest(source))
        lines.append('END SOURCE FILE ' + json.dumps(item['path'], ensure_ascii=False))
    text = '\n'.join(lines)
    if len(text.encode('utf-8')) > MAX_SOURCE_TEXT_BYTES:
        raise SourceArchiveError('Combined source text and manifest exceed the 4 MB limit')
    _check(deadline, cancelled)
    return {'text': text, 'files': files, 'warnings': warnings, 'format': format_name,
            'sha256': _digest(body), 'text_sha256': _digest(text)}
