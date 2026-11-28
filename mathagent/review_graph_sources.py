# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Editorial evidence derived from the same frozen sources as the proof readers."""
from pathlib import PurePosixPath

from . import manuscript
from .review_evidence import _dependencies, _references
from .writeup import compile_latex


def check_sources(sources, cancel=None):
    """Find citation keys in the pinned source tree and compile a standalone TeX paper."""
    primary = sources[0]
    if not primary['path'].lower().endswith(('.tex', '.ltx', '.latex')):
        return {'status': 'No TeX source to compile or scan for citation keys.'}
    pinned = {doc['path']: doc for doc in sources}
    pending, seen, missing = [primary['path']], set(), set()
    entries, citations = {}, []
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path not in pinned:
            missing.add(path)
            continue
        source = pinned[path]
        lines = source['content'].splitlines()
        for key, first, _ in _references(lines, path):
            entries.setdefault(key, []).append(f'{path} L{first}')
        if path.lower().endswith('.bib'):
            continue
        pending.extend(_dependencies(source, lines))
        for line, text in enumerate(manuscript._active_tex(lines), 1):
            for _, _, group in manuscript.TEX_CITE.findall(text):
                citations.extend((key.strip(), f'{path} L{line}') for key in group.split(','))
    result = {'missing_source_files': sorted(missing),
              'duplicate_bibliography_keys': {k: v for k, v in entries.items() if len(v) > 1},
              'citation_keys_without_pinned_entry': [
                  {'key': key, 'location': location} for key, location in citations if key and key not in entries],
              'scope': 'Bibliographic key matching does not verify a publication or a cited theorem.'}
    if primary.get('source_archive'):
        result['compilation'] = {'available': False, 'reason': 'Assembled archive, original build tree required.'}
        return result
    folder = PurePosixPath(primary['path']).parent
    assets = []
    for doc in sources[1:]:
        path = PurePosixPath(doc['path'])
        if (path.is_relative_to(folder) and path.suffix.lower() in
                ('.tex', '.ltx', '.latex', '.bib', '.bbl', '.bst', '.sty', '.cls', '.bbx', '.cbx', '.def', '.clo')):
            assets.append((str(path.relative_to(folder)), doc['content'].encode()))
    compiled = compile_latex(primary['content'], assets=assets, cancel=cancel)
    result['compilation'] = {key: compiled[key] for key in ('available', 'ok', 'references', 'errors', 'page_count')
                             if key in compiled}
    return result
