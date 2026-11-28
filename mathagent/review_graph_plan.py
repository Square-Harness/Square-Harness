# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Plan mathematical assignments from frozen source text, without an inference call."""
import re
from collections import defaultdict
from pathlib import Path

from . import manuscript
from .arxiv_source import TEX_SUFFIXES, SourceArchiveError, assemble_tex, main_document
from .review_evidence import _merged


ROLE_PROMPTS = {
    'family': (
        "Check the assigned arguments as a mathematical referee. Establish what the printed proof actually "
        "derives and compare it with the printed conclusion, including its constants, quantifiers and hypotheses. "
        "You may assume another assigned result as printed while checking whether it applies at your use site. "
        "Inspect unmarked dependencies when the mathematics requires it. Identify any nonroutine step you had to "
        "supply. Distinguish a false statement, a defect in its submitted proof, and a possible repair. "
        "Send located evidence when available. Complete the assignment with the implications you checked and "
        "any unresolved issues. When assess_graph_units is available, submit the unit ID, status and basis "
        "for each assigned proof in one batch. Name the decisive implication checked or the exact unresolved "
        "step. Reopen a settled implication only for a specific new reason."
    ),
    'test': (
        "Independently test the assigned statement starting from its hypotheses. Before reading its submitted "
        "proof or another reader's conclusions, develop an admissible example, a boundary regime or an "
        "independent derivation that could distinguish the printed claim from a plausible weaker one. "
        "Consult the original definitions and check all hypotheses of your test. Give your calculation and its "
        "precise consequence. An unsuccessful counterexample search establishes only what you tested."
    ),
    'lead': (
        "Review the manuscript for its author before submission, respecting the author's requested scope. "
        "Read the advertised conclusions, connect the arguments and check the portions without a proof reader, "
        "including implicit hypotheses, unnumbered arguments, references and presentation. Verify source "
        "passages when a citation matters. Keep working on other mathematics during independent lookups. "
        "Track exactly what a failed step would affect. Treat the graph as a reading aid and correct its "
        "dependencies against the paper. Use request_graph_check for a consequential missing or cross-family "
        "dependency. Return a handoff with located evidence and remaining uncertainties for the final report."
    ),
}
_RECORD_FINDINGS = (
    " Before finishing, save each supported objection with record_finding. Give the source location, evidence "
    "under the printed hypotheses, and mathematical consequence. Use major_gap for a central unproved step, "
    "fatal for a refuted central claim, and needs_judgment for a disputed basis or a substantive repair."
)
ROLE_PROMPTS = {role: prompt + _RECORD_FINDINGS for role, prompt in ROLE_PROMPTS.items()}


def _subtract(spans, excluded):
    """Closed line intervals in spans outside the excluded intervals."""
    result = []
    cuts = _merged(excluded)
    for start, end in _merged(spans):
        for a, b in cuts:
            if b < start:
                continue
            if a > end:
                break
            if a > start:
                result.append([start, a - 1])
            start = max(start, b + 1)
        if start <= end:
            result.append([start, end])
    return result


def _statements(unit):
    return [unit['statement']] if unit.get('statement') else []


def _span_text(spans):
    return ', '.join(f'L{a}' if a == b else f'L{a}–{b}' for a, b in spans)


def _line_references(mapping, lines):
    """Index visible reference sites once; an unresolved influence is never an inferred edge."""
    labels = {u['label']: u['id'] for u in mapping['units'] if u['label']}
    numbers = {(u['type'], u['number']): u['id'] for u in mapping['units'] if u['number']}
    sites = {}
    for n, line in enumerate(lines, 1):
        found = {labels[key.strip()] for group in manuscript.TEX_REF.findall(line)
                 for key in group.split(',') if key.strip() in labels}
        for kind, group in manuscript.TEXT_REF.findall(line):
            for number in re.findall(manuscript.NUMBER, group):
                uid = numbers.get((manuscript._kind(kind), number))
                if uid:
                    found.add(uid)
        if found:
            sites[n] = found
    return sites


def _edges(mapping, lines):
    sites = _line_references(mapping, lines)
    result = []
    for unit in mapping['units']:
        for kind, spans, refs in [('statement', _statements(unit), unit['statement_refs']),
                                  ('proof', unit['own_proof_ranges'], unit['refs'])]:
            found = defaultdict(list)
            for a, b in spans:
                for n in range(a, b + 1):
                    for uid in sites.get(n, ()):
                        if uid != unit['id'] and uid in refs:
                            found[uid].append(n)
            for uid in refs:
                if uid in found or not unit.get('nested'):
                    result.append({'from': unit['id'], 'to': uid, 'kind': kind, 'lines': found[uid]})
    return result


def _family_ids(units, selected, edges, sections, lines):
    """Keep shared or cyclic arguments together; preserve independent conclusions as owners."""
    parent = {uid: uid for uid in selected}

    def root(uid):
        while uid != parent[uid]:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    def join(left, right):
        parent[root(right)] = root(left)

    deps = {uid: set() for uid in selected}
    consumers = {uid: set() for uid in selected}
    for edge in edges:
        user, premise = edge['from'], edge['to']
        if user in deps and premise in deps:
            deps[user].add(premise)
            consumers[premise].add(user)

    # Strongly connected components of the proof graph, including indirect cycles.
    # Iterative postorder avoids dependence on the interpreter's recursion limit.
    visited, order = set(), []
    for uid in selected:
        stack = [(uid, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                order.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in deps[node])
    visited.clear()
    for uid in reversed(order):
        if uid in visited:
            continue
        stack = [uid]
        visited.add(uid)
        while stack:
            node = stack.pop()
            join(uid, node)
            for child in consumers[node] - visited:
                visited.add(child)
                stack.append(child)

    shared = {}
    for uid in selected:
        unit = units[uid]
        for span in unit['proofs']:
            key = tuple(span)
            join(shared.setdefault(key, uid), uid)
        local = [other for other in consumers[uid] if unit['section'] is not None
                 and units[other]['section'] == unit['section']]
        if unit['type'] in ('lemma', 'claim', 'proposition') and len(consumers[uid]) == 1 and local:
            join(uid, local[0])
        if unit['type'] == 'lemma':
            for other in local:
                if units[other]['type'] == 'lemma':
                    join(uid, other)
        if unit['type'] == 'corollary':
            principal = [dep for dep in deps[uid] if units[dep]['main']]
            if len(principal) == 1:
                join(uid, principal[0])
            elif len(deps[uid]) == 1:
                join(uid, next(iter(deps[uid])))

    # Sibling lemmas can use the same local setting with no explicit cross-reference.
    # Require a shared subject word from their actual section title and both statements.
    subjects = {s['number']: set(re.findall(r'[^\W\d_]{4,}', s['title'].casefold())) for s in sections}
    generic = set('this that with from into some main other their these those section proof proofs '
                  'results applications examples lemma lemmas theorem theorems preliminaries using'.split())
    previous = None
    for uid, unit in units.items():
        if uid not in parent or unit['type'] != 'lemma' or not unit.get('statement'):
            previous = None
            continue
        a, b = unit['statement']
        vocab = set(re.findall(r'[^\W\d_]{4,}', ' '.join(lines[a - 1:b]).casefold()))
        subject = vocab.intersection(subjects.get(unit['section'], ())) - generic
        if previous and previous[1] == unit['section'] and subject.intersection(previous[2]):
            join(uid, previous[0])
        previous = (uid, unit['section'], subject)

    # A standalone supporting lemma with a single consumer belongs to that argument even
    # when the author states their theorem in the introduction and proves it much later.
    # Retain an existing local family of lemmas as a shared unit.
    local_members = defaultdict(list)
    for uid in selected:
        local_members[root(uid)].append(uid)
    for members in local_members.values():
        if len(members) == 1:
            uid = members[0]
            if units[uid]['type'] in ('lemma', 'claim') and len(consumers[uid]) == 1:
                join(uid, next(iter(consumers[uid])))

    families = defaultdict(list)
    for uid in selected:
        families[root(uid)].append(uid)
    return list(families.values())


def _scope(mapping, target, pages):
    lines = mapping['lines']
    if not target or re.fullmatch(r'(?:the |this )?(?:whole |entire |full )?(?:manuscript|paper)', target, re.I):
        return mapping['units'], [[1, lines]] if lines else [], ''
    named = list(_NAMED_PASSAGE.finditer(target))
    units = []
    if len(named) > 1:
        for match in named:
            unit = manuscript.resolve(mapping, match.group())
            if unit is None:
                break
            if unit not in units:
                units.append(unit)
        else:
            return units, [span for u in units for span in _statements(u) + u['proofs']], ''
        return [], [], 'The focus names several passages. The lead must locate all of them in the source.'
    if not re.search(r'(?:\d|[A-Z])\s*(?:and|et|,|[-–])\s*(?:\d|[A-Z])', target):
        unit = manuscript.resolve(mapping, target)
        if unit:
            return [unit], _statements(unit) + unit['proofs'], ''
    request = re.fullmatch(r'(?:pages?|pp?\.)\s*(\d+)(?:\s*[-–]\s*(\d+))?', target, re.I)
    if request:
        a, b = int(request[1]), int(request[2] or request[1])
        if 1 <= a <= b <= len(pages):
            start, end = pages[a - 1], pages[b] - 1 if b < len(pages) else lines
            selected = [u for u in mapping['units'] if any(start <= x and y <= end
                        for x, y in _statements(u) + u['proofs'])]
            return selected, [[start, end]], ''
    for i, section in enumerate(mapping['sections']):
        if target.casefold() in {section['title'].casefold(), f"section {section['number']}".casefold(),
                                 f"§{section['number']}".casefold()}:
            end = mapping['sections'][i + 1]['line'] - 1 if i + 1 < len(mapping['sections']) else lines
            return [u for u in mapping['units'] if u['section'] == section['number']], [[section['line'], end]], ''
    return [], [], 'The focus has no unambiguous graph location. The lead must locate it in the frozen paper.'


def _context(mapping, lines, unit, role):
    """Local standing assumptions and explicitly referenced equations or other labelled premises."""
    spans = _statements(unit)
    if role != 'test':
        spans += unit['own_proof_ranges']
    context = []
    if unit.get('statement'):
        start = unit['statement'][0]
        sec = next((s for s in reversed(mapping['sections']) if s['line'] <= start), None)
        if sec:
            preceding = [b for u in mapping['units'] for a, b in _statements(u) + u['proofs']
                         if sec['line'] <= b < start]
            first = min((a for u in mapping['units'] for a, _ in _statements(u) + u['proofs']
                         if sec['line'] <= a <= start), default=start)
            context.append([sec['line'], max(sec['line'], first - 1)])
            if preceding and max(preceding) + 1 < start:
                context.append([max(preceding) + 1, start - 1])
            context.extend(u['statement'] for u in mapping['units']
                           if u.get('statement') and u['statement'][1] < start and u['section'] == sec['number']
                           and u['type'] in ('assumption', 'hypothesis', 'condition', 'definition', 'notation'))
    labels = mapping['labels']
    equations = mapping['equations']
    unit_labels = {u['label'] for u in mapping['units']}
    for a, b in _merged(spans + context):
        for group in manuscript.TEX_REF.findall('\n'.join(lines[a - 1:b])):
            for name in (s.strip() for s in group.split(',')):
                if name in equations:
                    context.append(equations[name])
                elif name in labels and name not in unit_labels:
                    context.append([labels[name], labels[name]])
    return context


def _informal_passages(mapping, scope_ranges, pages):
    # Missing formal proof boundaries must never turn an inferred section into a verified claim.
    # Use author-labelled divisions first and keep overview and bibliography passages with the lead.
    sections = mapping['sections']
    for i, section in enumerate(sections):
        if re.search(r'\b(?:abstract|introduction|conclusion|references|bibliography|acknowledg\w*)\b',
                     section['title'], re.I):
            continue
        start = section['line']
        end = sections[i + 1]['line'] - 1 if i + 1 < len(sections) else mapping['lines']
        for a, b in scope_ranges:
            lo, hi = max(a, start), min(b, end)
            if lo <= hi:
                yield section['title'], [lo, hi]
    if sections:
        return
    # With only physical page boundaries, the lead reads the opening and a reader takes the
    # rest. A single division gives parallel coverage without inventing a dependency per page.
    for a, b in scope_ranges:
        boundaries = [start for start in pages if a < start <= b]
        if boundaries:
            cut = min(boundaries, key=lambda n: abs(n - (a + b + 1) / 2))
            first = pages.index(cut) + 1
            last = sum(n <= b for n in pages)
            yield f'Extracted pages {first}–{last}', [cut, b]


def _plan(sources, target):
    main = sources[0]
    sid = main['id']
    source_info = {s['id']: {'path': s['path'], 'lines': len(s['content'].splitlines())} for s in sources}
    preamble = '\n'.join(s['content'] for s in sources[1:] if Path(s['path']).suffix.lower() in ('.sty', '.cls'))
    aux = '\n'.join(s['content'] for s in sources[1:] if Path(s['path']).suffix.lower() == '.aux')
    mapping = manuscript.build(main['content'], main['path'], preamble, aux)
    lines = main['content'].splitlines()
    units = {u['id']: u for u in mapping['units']}
    for unit in units.values():
        unit['source'] = sid
        unit['own_proof_ranges'] = _subtract(unit['proofs'], unit.get('nested', []))
    edges = _edges(mapping, lines)
    target = (target or '').strip()
    pages = main.get('pages') or []
    scoped, scope_ranges, warning = _scope(mapping, target, pages)
    scoped_ids = {u['id'] for u in scoped}
    owners = [u['id'] for u in scoped if u['proofs']]
    families = []
    occupied = []
    contexts = {}
    def context(unit, role):
        key = (unit['id'], role)
        if key not in contexts:
            contexts[key] = _context(mapping, lines, unit, role)
        return contexts[key]
    for ids in _family_ids(units, owners, edges, mapping['sections'], lines):
        fid = f'F{len(families) + 1}'
        spans = []
        for uid in ids:
            unit = units[uid]
            unit['owner'] = fid
            spans.extend(context(unit, 'family'))
            primary = _statements(unit) + unit['own_proof_ranges']
            occupied.extend(primary)
            spans.extend(primary)
        group_edges = [edge for edge in edges if edge['from'] in ids or edge['to'] in ids]
        for edge in group_edges:
            other = edge['from'] if edge['to'] in ids else edge['to']
            spans.extend(_statements(units[other]))
            spans.extend([[n, n] for n in edge['lines']])
            if edge['from'] in ids:
                spans.extend(context(units[edge['to']], 'test'))
        owned = [span for uid in ids for span in _statements(units[uid]) + units[uid]['own_proof_ranges']]
        families.append({'id': fid, 'role': 'family', 'units': ids,
                         'title': ', '.join(units[u]['name'] for u in ids),
                         'inline_ranges': {sid: _merged(spans)}, 'owned_ranges': {sid: _merged(owned)},
                         'edges': group_edges})

    if not owners:
        for title, (a, b) in _informal_passages(mapping, scope_ranges, pages):
            fid = f'F{len(families) + 1}'
            ids = [u['id'] for u in scoped if u['statement'] and a <= u['statement'][0] <= b]
            for uid in ids:
                units[uid]['owner'] = fid
            ranges = {sid: [[a, b]]}
            families.append({'id': fid, 'role': 'family', 'units': ids, 'title': title,
                             'owned_ranges': ranges, 'inline_ranges': ranges,
                             'uncertainty': 'Passage assignment. Identify the claims, proof boundaries and actual '
                                            'dependencies from the source. The parser could not establish them.'})
            occupied.append([a, b])
    lead_ids = []
    for uid, unit in units.items():
        if uid in scoped_ids and 'owner' not in unit:
            unit['owner'] = 'LEAD'
            lead_ids.append(uid)
        elif uid not in scoped_ids:
            unit['owner'] = None
    lead_ranges = _subtract(scope_ranges, occupied)
    lead_inline = list(lead_ranges)
    for unit in scoped:
        if unit['main']:
            lead_inline.extend(_statements(unit))
    lead = {'id': 'LEAD', 'role': 'lead', 'title': 'Paper-wide reasoning and unassigned passages',
            'units': lead_ids, 'owned_ranges': {sid: lead_ranges}, 'inline_ranges': {sid: _merged(lead_inline)},
            'edges': [edge for edge in edges if edge['from'] in scoped_ids or edge['to'] in scoped_ids]}
    # The primary manuscript has one mathematical owner for every line, including unmapped prose.
    # Supporting files are standing evidence; the lead inspects relevant passages as needed.
    for src in sources[1:]:
        source_info[src['id']]['use'] = 'Available to all readers. Lead checks relevance to the manuscript.'
    candidates = [u for u in scoped if u.get('statement') and u['type'] in manuscript.PROVABLE and u['proofs']]
    test = None
    if candidates:
        uses = defaultdict(set)
        for edge in edges:
            uses[edge['to']].add(edge['from'])
        claim = max(candidates, key=lambda u: (u['main'], len(uses[u['id']]), u['type'] == 'theorem'))
        spans = _statements(claim) + context(claim, 'test')
        statement_edges = [e for e in edges if e['from'] == claim['id'] and e['kind'] == 'statement']
        for edge in statement_edges:
            spans.extend(_statements(units[edge['to']]))
        # Even a proof that shares a physical line with its statement is kept out of the initial test.
        all_proofs = [span for u in units.values() for span in u['own_proof_ranges']]
        test = {'id': 'TEST', 'role': 'test', 'title': claim['name'], 'units': [claim['id']],
                'edges': statement_edges, 'inline_ranges': {sid: _subtract(spans, all_proofs)}}
        # TeX or extracted text can start its proof on the same line as the statement ends.
        # Preserve the statement prefix, without marking the remainder of that line as read.
        a, b = claim['statement']
        for start, _ in claim['proofs']:
            if a <= start <= b:
                line = lines[start - 1]
                marker = re.search(r'\\begin\{proof\}', line) or manuscript.INLINE_PROOF.search(line)
                if marker and line[:marker.start()].strip():
                    test.setdefault('prefixes', []).append(
                        {'source': sid, 'line': start, 'text': line[:marker.start()].rstrip()})
    warnings = list(mapping['warnings'])
    if warning:
        warnings.append(warning)
    if not owners and not warning:
        warnings.append('No formal proof boundaries were resolved within scope. Read the assigned passages '
                        'directly. Their mathematical dependencies are uncertain.')
    return {'source': sid, 'sources': source_info, 'units': units, 'sections': mapping['sections'],
            'format': mapping['format'], 'target': target, 'edges': edges, 'families': families,
            'test': test, 'lead': lead, 'warnings': warnings}


_NAMED_PASSAGE = re.compile(
    r'\b(?:section|appendix|page|lemma|theorem|proposition|corollary|claim)\s+(?:[A-Z](?![a-z])|\d+)(?:\.\d+)*\b',
    re.I)


def _request_focus(goal, sources, target):
    explicit = (target or '').strip()
    if explicit and not any(explicit in (s['path'], Path(s['path']).name) for s in sources):
        return explicit
    # A request that identifies a particular argument is sufficient even when Assistant has
    # passed it as the goal rather than the separate target. An emphasis on one section of a
    # whole-paper review is not a restriction. Preserve compound targets for the lead to resolve.
    scope = (re.search(r'\b(?:focus only on|limited to|restrict (?:the |your |this )?review to|'
                       r'(?:review|check|verify|audit) only|only (?:review|check|verify|audit))\s+([^\n;!?]+)', goal, re.I)
             or re.match(r'\s*(?:please\s+)?(?:review|check|verify|audit)\s+(?:the proof of\s+)?'
                         r'((?:section|appendix|pages?|lemma|theorem|proposition|claim|corollary)\s+[^\n;!?]+)', goal, re.I))
    if not scope:
        return ''
    phrase = scope[1].strip(' .')
    if re.search(r'\b(?:whole|entire|full)\s+(?:paper|manuscript)\b', phrase, re.I):
        return ''
    named = list(_NAMED_PASSAGE.finditer(phrase))
    if len(named) == 1:
        tail = phrase[named[0].end():].strip()
        if not re.match(r'^(?:and|et|or|,|[-–])\s*(?:\d|[A-Z]\b)', tail, re.I):
            return named[0].group()
    return phrase


_TEX_INCLUSION = re.compile(r'\\(?:input|include|subfile|(?:sub)?(?:import|inputfrom|includefrom))\b')


def unique_tex_root(candidates, sources):
    """Recognize an unambiguous included project among otherwise ambiguous manuscript attachments."""
    if not candidates or any(s.get('source_archive') or Path(s['path']).suffix.lower() not in TEX_SUFFIXES
                             for s in candidates):
        return None
    required = {s['path'] for s in candidates}
    members = {s['path']: s['content'] for s in sources}
    main, other_mains = main_document({s['path']: s['content'] for s in candidates})
    possible = set([main, *other_mains]) if main else required
    matched = []
    for s in candidates:
        if s['path'] in possible and _TEX_INCLUSION.search(s['content']):
            try:
                assembled = assemble_tex(members, s['path'])
            except SourceArchiveError:
                continue
            if required.issubset(assembled['members']) and not any('circular inclusion' in problem
                                                                   for problem in assembled['missing']):
                matched.append(s)
    return matched[0] if len(matched) == 1 else None


def build_graph_plan(sources, target='', *, goal=''):
    """The first source is the selected manuscript. Track every excerpt back to a frozen file."""
    if not sources:
        raise ValueError('A frozen manuscript is required for a proof graph.')
    target = _request_focus(goal, sources, target)
    main = sources[0]
    if (main.get('line_map') or Path(main['path']).suffix.lower() not in TEX_SUFFIXES
            or not _TEX_INCLUSION.search(main['content'])):
        return _plan(sources, target)
    members = {s['path']: s['content'] for s in sources}
    try:
        assembled = assemble_tex(members, main['path'])
    except SourceArchiveError as exc:
        plan = _plan(sources, target)
        plan['warnings'].append(f'File inclusion could not be mapped: {exc}. Inspect all pinned inputs.')
        return plan
    plan = _plan([dict(main, content=assembled['text']), *sources[1:]], target)
    plan['warnings'].extend('Unresolved file inclusion: ' + msg for msg in assembled['missing'])
    sources_by_path = {s['path']: s['id'] for s in sources}
    for s in sources:
        plan['sources'][s['id']]['lines'] = len(s['content'].splitlines())
        if s['path'] in assembled['members']:
            plan['sources'][s['id']]['use'] = 'Included manuscript source.'

    def locations(spans):
        result = defaultdict(list)
        for a, b in spans:
            for start, member, first, count in assembled['line_map']:
                lo, hi = max(start, a), min(start + count - 1, b)
                if lo <= hi:
                    source_id = sources_by_path[member]
                    last = min(first + hi - start, plan['sources'][source_id]['lines'])
                    if first + lo - start <= last:
                        result[source_id].append([first + lo - start, last])
        return {sid: _merged(ranges) for sid, ranges in result.items()}
    for unit in plan['units'].values():
        unit['statement_locations'] = locations(_statements(unit))
        unit['proof_locations'] = locations(unit['own_proof_ranges'])
    for edge in plan['edges']:
        edge['locations'] = locations([[n, n] for n in edge['lines']])
    for assignment in [*plan['families'], plan['lead'], plan['test']]:
        if assignment:
            for key in ('inline_ranges', 'owned_ranges'):
                if key in assignment:
                    assignment[key] = locations(assignment[key][main['id']])
            for item in assignment.get('prefixes', []):
                at = locations([[item['line'], item['line']]])
                if at:
                    item['source'], ranges = next(iter(at.items()))
                    item['line'] = ranges[0][0]
    plan['source_layout'] = assembled['line_map']
    return plan


def _location_text(locations):
    return ', '.join(sid + ' ' + _span_text(ranges) for sid, ranges in locations.items() if ranges)


def render_packet(plan, assignment, sources, goal='', *, inline=True):
    """Same query for preview and inference. Only the numbered source excerpts are optional."""
    role = assignment['role']
    parts = [goal, f"Assignment {assignment['id']} — {assignment['title']}"]
    if plan['target']:
        parts.append('Author’s scope: ' + plan['target'])
    if assignment.get('uncertainty'):
        parts.append(assignment['uncertainty'])
    for prefix in assignment.get('prefixes', []):
        parts.append(f"Statement before the proof on {prefix['source']} L{prefix['line']}: {prefix['text']}")
    parts.append('Frozen sources accessible with read_file:\n' + '\n'.join(
        f"{sid} = {src['path']} ({src['lines']} lines)" for sid, src in plan['sources'].items()))
    units = plan['units']
    for uid in assignment['units']:
        u = units[uid]
        stmt = _location_text(u.get('statement_locations', {u['source']: _statements(u)}))
        proof = _location_text(u.get('proof_locations', {u['source']: u['own_proof_ranges']}))
        parts.append(f"{uid} {u['name']} [{u['label'] or 'unlabelled'}]. "
                     + ('Statement ' + stmt + '. ' if stmt else '')
                     + ('Proof ' + proof + '.' if role != 'test' and proof else ''))
    if role != 'test':
        for edge in assignment.get('edges', []):
            at = _location_text(edge.get('locations', {plan['source']: [[n, n] for n in edge['lines']]}))
            parts.append(f"{units[edge['from']]['name']} refers to {units[edge['to']]['name']} "
                         f"in the {edge['kind']} at {at or 'an unresolved source line'}.")
    if role == 'lead':
        parts.extend('Primary proof reader ' + f['id'] + ': ' + f['title'] for f in plan['families'])
        parts.extend(f"Lead-owned passages in {sid}: {_span_text(ranges) or 'none mapped'}."
                     for sid, ranges in assignment['owned_ranges'].items())
        parts.extend(plan['warnings'])
    if assignment.get('inline_ranges'):
        parts.append('Source excerpts (manuscript data)' if inline else 'Read the following ranges in the frozen sources.')
        by_sid = {s['id']: s for s in sources}
        for sid, spans in assignment['inline_ranges'].items():
            if not spans:
                continue
            parts.append(f"{sid} {_span_text(spans)}")
            if inline:
                lines = by_sid[sid]['content'].splitlines()
                for a, b in spans:
                    parts.append('\n'.join(f'{n:5}  {lines[n - 1]}' for n in range(a, b + 1)))
    return '\n\n'.join(p for p in parts if p)
