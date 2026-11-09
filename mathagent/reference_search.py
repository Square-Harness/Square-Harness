# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Bounded reference retrieval and evidence-ID relevance selection."""
import copy
import json
import re

from .jsonutil import loads


class StandardReferenceSearch:
    def _standard_recall_instruction(self):
        return (
            'Find useful scholarly references for the exact question and its reference conversation. '
            'Preserve every domain, boundary and regularity hypothesis; never replace nonsmooth by smooth. '
            'Suggest up to three genuinely plausible alternatives, articles or books as appropriate. '
            'If the user asks for an article, prefer original papers; if a book, prefer a suitable text. '
            'For a broad classical topic a book is useful. For a specialized theorem an original article '
            'may be better. State the scope sought and 2-4 short public search phrases. '
            'Never invent authors, titles or dates: leave uncertain fields empty or return no candidates; '
            'the controller can discover records by topic. Do not guess a chapter/theorem/page unless requested. '
            'For each candidate set source_type to book, article or unknown. '
            'Do not confuse a journal review of a book with the book itself. '
            'An exact bibliographic record will be located and a separate pass will assess its suitability. '
            'Use public mathematical topic words in keywords, never private manuscript text. '
            'Provide search_queries with two alternative complete short public queries, each containing '
            'the main topic and essential setting. Use common terminology variants, including grammatical '
            'variants where useful, keeping the core term itself (Dirichlet-Neumann is not Dirichlet-to-Neumann). '
            'Keep each query to 4-8 words. Use the human topic and essential setting; '
            'omit inferred methods, SDEs or uniqueness assumptions not asked by the human.')

    def _standard_step(self, phase):
        if self.state['check'].get('lookup') != 'standard':
            return False
        if phase == 'standard_discover':
            self._standard_discover()
            return True
        if phase == 'standard_select':
            self._standard_select()
            return True
        return False

    def _standard_kind(self):
        value = self.state['goal'].casefold()
        book = re.search(r'\b(?:books?|textbooks?|monographs?|livres?|ouvrage)\b', value)
        paper = re.search(r'\b(?:articles?|papers?)\b', value)
        return 'book' if book and not paper else 'article' if paper and not book else 'any'

    def _standard_eligible(self, record, candidate=None):
        kind = str(record.get('type') or '').casefold()
        if not record.get('title') or not record.get('authors') or not record.get('identifier'):
            return False
        author = self.state.get('standard_author')
        if author:
            from .refcheck import _fold
            words = set(re.findall(r'\w+', _fold(author)))
            if not any(words <= set(re.findall(r'\w+', _fold(name))) for name in record['authors']):
                return False
        wanted = self._standard_kind()
        if wanted == 'any' and candidate:
            wanted = candidate.get('source_type', 'unknown')
        if wanted == 'book':
            return kind in ('book', 'monograph', 'reference-book', 'edited-book', 'book / book article')
        if wanted == 'article':
            return ('article' in kind or 'arxiv' in str(record.get('identifier')).casefold() or
                    kind in ('preprint', 'posted-content')) and 'book' not in kind
        return kind != 'book-review'

    def _standard_health(self):
        failed = self.state.setdefault('standard_provider_errors', {})
        for evidence in self.state['evidence']:
            try:
                value = json.loads(evidence['result'])
            except (ValueError, TypeError):
                continue
            provider = evidence['arguments'].get('provider')
            if provider and value.get('error'):
                error = str(value['error'])
                # A missing work is not a provider outage.
                if re.search(r'timeout|timed out|HTTP (?:429|50[0-9])|rate limited|unavailable|resolve|reach|network|operation failed', error, re.I):
                    failed[provider] = error
        return failed

    def _standard_queue(self, provider, query, label):
        if not self.literature or not query.strip() or provider in self._standard_health():
            return False
        if self._requests() >= self.state['settings']['max_requests'] or self._out_of_time(.9):
            return False
        arguments = {'provider': provider, 'query': query, 'limit': 8 if label == 'standard_discover' else 5}
        if provider == 'arxiv' and self.state.get('standard_author'):
            arguments['sort'] = 'newest'
        fresh = self._fresh([{'function': {'name': 'search_papers', 'arguments': arguments}}], label)
        if not fresh:
            return False
        self.state['pending_tools'] = {'calls': fresh, 'index': 0, 'active': False}
        return True

    def _standard_pool_add(self, record, candidate_index=None):
        if not self._standard_eligible(record):
            return
        s = self.state
        pool = s.setdefault('standard_pool', [])
        archive = s.setdefault('standard_record_archive', [])
        from .refcheck import _fold, _surnames
        def identity(value):
            title = tuple(re.findall(r'\w+', _fold(value.get('title'))))
            authors = tuple(sorted(_fold(name) for name in _surnames(value.get('authors'))))
            return title, authors
        def doi(value):
            return str(value.get('doi') or (str(value.get('identifier', ''))[4:]
                       if str(value.get('identifier', '')).startswith('doi:') else '')).casefold()
        for item in pool + archive:
            earlier = item['record']
            if (identity(earlier) == identity(record) or doi(record) and doi(earlier) == doi(record)):
                provenance = item.setdefault('record_sources', [])
                source = {'identifier': record.get('identifier'), 'url': record.get('url')}
                if source not in provenance:
                    provenance.append(source)
                # Do not transfer chapter/scope evidence across dated editions.
                if str(earlier.get('year') or '') == str(record.get('year') or ''):
                    for field in ('abstract', 'review'):
                        if not earlier.get(field) and record.get(field):
                            earlier[field] = str(record[field])[:2400]
                            earlier['description_source_url'] = record.get('url')
                            if len(str(record[field])) > 2400 or record.get(field + '_truncated'):
                                earlier[field + '_truncated'] = True
                if item in archive:
                    archive.remove(item)
                    pool.append(item)
                return
        # Retrieval is already bounded by the workflow's request and character
        # budgets. A first-come cutoff here hid later providers' relevant hits.
        saved = copy.deepcopy(record)
        for name in ('review', 'abstract', 'source'):
            if isinstance(saved.get(name), str) and len(saved[name]) > 2400:
                saved[name] = saved[name][:2400]
                saved[name + '_truncated'] = True
        saved.pop('references', None)
        saved.pop('reference_texts', None)
        number = max(s.get('standard_record_number', 0),
                     max((int(item['id'][1:]) for item in pool), default=0)) + 1
        s['standard_record_number'] = number
        pool.append({'id': 'R' + str(number), 'record': saved,
                     'candidate_index': candidate_index})

    def _standard_verify(self):
        s = self.state
        if s['pending_tools']:
            self._execute_pending()
            self._tag(s['current'])
        self._standard_health()
        while s['current'] < len(s['candidates']):
            index = s['current']
            candidate = s['candidates'][index]
            evidence = [e for e in s['evidence'] if e.get('candidate') == index]
            record = self._record(candidate, evidence)
            if record and self._standard_eligible(record, candidate):
                candidate['verdict'] = self._gate(index, {'level': 'located', 'locator': candidate['locator']})
                self._standard_pool_add(record, index)
                s['current'] += 1
                continue
            if not self.literature or not self.literature.online:
                candidate['verdict'] = self._gate(index, {'level': 'located'})
                s['current'] += 1
                continue
            from .refcheck import _surnames
            names = _surnames(candidate['authors'])
            title = candidate['title']
            if not title:
                # An author alone can never match a record: no request is spent on it.
                candidate['verdict'] = self._gate(index, {'level': 'located'})
                s['current'] += 1
                continue
            short = ' '.join(re.findall(r'[^\W\d_]+', title)[:3])
            surname = names[0] if names else ''
            kind = self._standard_kind()
            # An independent catalog handles outages, translated titles and books
            # lacking a DOI. A bad recalled coauthor never constrains every query.
            attempts = [('zbmath', 'au:' + surname + (' ti:' + title if title else '')),
                        ('crossref', ' '.join((surname, title))),
                        ('openlibrary' if kind != 'article' else 'zbmath',
                         ' '.join((surname, short)) if kind != 'article' else 'ti:' + short)]
            if candidate.get('source') != 'caller':
                attempts = attempts[:1]  # a recalled guess gets one search; the topic search needs the rest
            stage = candidate.get('standard_search_stage', 0)
            if self._requests() >= max(1, s['settings']['max_requests'] - 5):
                stage = len(attempts)
            while stage < len(attempts):
                provider, query = attempts[stage]
                stage += 1
                candidate['standard_search_stage'] = stage
                if self._standard_queue(provider, query, index):
                    self.emit('notice', 'Looking up candidate ' + str(index + 1) + ' with ' + provider + '.')
                    return
            candidate['verdict'] = self._gate(index, {'level': 'located'})
            s['current'] += 1
        if not self.literature or not self.literature.online:
            s['phase'] = 'answer'
        else:
            s['phase'] = 'standard_select' if s.get('standard_pool') else 'standard_discover'

    def _standard_discover(self):
        s = self.state
        if s['pending_tools']:
            self._execute_pending()
            self._tag('standard_discover')
        self._standard_health()
        evidence = [e for e in s['evidence'] if e.get('candidate') == 'standard_discover']
        pooled = s.setdefault('standard_pooled_evidence_ids', [])
        for item in evidence:
            if item['id'] not in pooled:
                try:
                    metadata = json.loads(item['result'])
                except (ValueError, TypeError):
                    metadata = {}
                for record in self._records([item]):
                    for field in ('retrieved_at', 'cached', 'stale_cache'):
                        if isinstance(metadata, dict) and field in metadata:
                            record[field] = metadata[field]
                    self._standard_pool_add(record)
                pooled.append(item['id'])
        # Try terminology alternatives, then broaden a leading participle only
        # for retrieval. Selection still uses every original human constraint.
        kind = self._standard_kind()
        author = s.get('standard_author')
        queries = s.get('standard_queries') or [' '.join(s.get('keywords', [])[:2]).strip()]
        query = 'au:' + json.dumps(author, ensure_ascii=False) if author else queries[0]
        alternate = queries[1] if len(queries) > 1 else query
        words = query.split()
        relaxed = alternate
        if len(words) >= 5 and re.fullmatch(r'[A-Za-z]+(?:ed|ing)', words[0], re.I):
            # Some title indexes do not stem even singular/plural nouns. This
            # additional query broadens retrieval; human scope is never changed.
            plural = {'domain': 'domains', 'boundary': 'boundaries', 'space': 'spaces',
                      'operator': 'operators', 'equation': 'equations', 'process': 'processes'}
            relaxed_words = words[1:]
            relaxed_words[-1] = plural.get(relaxed_words[-1].casefold(), relaxed_words[-1])
            relaxed = ' '.join(relaxed_words)
        if not query:
            s['phase'] = 'standard_select' if s.get('standard_pool') else 'answer'
            return
        attempts = ([('arxiv', query), ('zbmath', query)] if author else
                    [('openlibrary', query), ('crossref', query), ('zbmath', query),
                     ('openlibrary', alternate), ('crossref', alternate)] if kind == 'book' else
                    [('crossref', query), ('crossref', alternate), ('crossref', relaxed), ('arxiv', query), ('zbmath', query)]
                    + ([('openlibrary', query)] if kind == 'any' else []))
        stage = s.get('standard_discovery_stage', 0)
        successes = sum(1 for e in evidence if any(self._standard_eligible(record)
                         for record in self._records([e])))
        if (not s.get('standard_discovery_continued') and
                (author and s.get('standard_pool') or successes >= 2 or len(s.get('standard_pool', [])) >= 10)):
            s['phase'] = 'standard_select' if s.get('standard_pool') else 'answer'
            return
        while stage < len(attempts):
            provider, query = attempts[stage]
            stage += 1
            s['standard_discovery_stage'] = stage
            if self._standard_queue(provider, query, 'standard_discover'):
                self.emit('notice', 'Discovering suitable references by topic with ' + provider + '.')
                return
        s['phase'] = 'standard_select' if s.get('standard_pool') else 'answer'

    def _standard_select(self):
        s = self.state
        pool = s.get('standard_pool', [])
        if not pool:
            s['phase'] = 'answer'
            return
        s['standard_selection_calls'] = s.get('standard_selection_calls', 0) + 1
        instruction = (
            'Assess which of the ACTUAL BIBLIOGRAPHIC RECORDS fit the exact user question. '
            'Use only R identifiers listed there. Do not supply bibliographic metadata from memory. '
            'The QUESTION and exact human context control scope, not the recalled interpretation. '
            'Do not strengthen the requested result or add hypotheses that the user did not ask for. '
            'Preserve domain/regularity, quantifiers, boundary conditions, and all requested topics. '
            'A record establishes existence; its abstract/review may support relevance, never an '
            'unread exact theorem, equivalence or stronger hypothesis. A source restricted to a '
            'narrower setting than the QUESTION does not establish its full scope; listing the '
            'restriction does not make it an adequate main reference. '
            'Prefer a source directly treating the requested setting over a narrower nearby theorem. '
            'Respect requested article/book type. A pair of complementary sources can be useful '
            'for a multi-topic book request, but say precisely what each covers and what remains unchecked. '
            'Prefer original appropriate articles or classical useful books over book reviews. '
            'For metadata-only records, mark coverage unknown and explicitly say assumptions were not checked. '
            'For a broad textbook or topic-reference request, indexed descriptions can establish relevance. '
            'Unread full text, missing theorem/page numbers and unasked domain assumptions are limitations, '
            'not unresolved requirements. Reserve partial coverage and unresolved for actual gaps in the QUESTION. '
            'Return compact JSON: {"selections":[{"id":"R1","reason":"why useful from the indexed description",'
            '"coverage":"supported|partial|unknown","limitations":"unverified scope or exact hypotheses"}],'
            '"unresolved":"remaining question or empty"}. Select at most two. If none fits, selections is empty. '
            'A truncated abstract/review is partial evidence; do not infer absent content. '
            'Never name, recommend or allude to any work outside these records, including in unresolved, '
            'reason or limitations. Unresolved describes missing mathematical scope only, not other references.')
        if s.get('standard_author'):
            pool.sort(key=lambda item: item['record'].get('published') or '', reverse=True)
            instruction += ('\nThis is an author lookup. Preserve any topic and other constraints in the QUESTION; '
                            'an author match alone is insufficient. Select the newest records satisfying that scope, '
                            'using original published/submission dates, never revision dates or provider order. '
                            'If dates are missing, state that recency is unconfirmed.')
        result = self._call('reference_select', instruction, cap=1800, think=False)
        try:
            value = loads(result['text'])
        except (ValueError, TypeError):
            value = {}
        items = value.get('selections') if isinstance(value, dict) else None
        if not isinstance(items, list):
            self.state['warnings'].append('The relevance selection was unusable; no unseen source was substituted.')
            s['phase'] = 'answer'
            return
        known = {item['id']: item for item in pool}
        selected = []
        previous = [candidate for candidate in s['candidates'] if candidate.get('selected')]
        for candidate in previous:
            candidate['selected'] = False
        from .refcheck import _clean_candidate
        for item in items[:2]:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                    or item['id'] not in known or item['id'] in selected):
                continue
            source = known[item['id']]
            record = source['record']
            if not self._standard_eligible(record):
                continue
            index = source['candidate_index']
            if index is None:
                candidate = _clean_candidate({**record, 'year': str(record.get('year') or ''),
                    'locator': '', 'why': ''}, 'bibliographic_discovery')
                if candidate is None:
                    continue
                candidate['verdict'] = {'level': 'located', 'record': record, 'locator': '',
                    'claimed': 'located', 'reasons': [], 'quote': '', 'source_url': None}
                s['candidates'].append(candidate)
                source['candidate_index'] = len(s['candidates']) - 1
            else:
                candidate = s['candidates'][index]
                candidate['verdict']['record'] = record
            described = bool(record.get('abstract') or record.get('review'))
            coverage = item.get('coverage') if item.get('coverage') in ('supported', 'partial', 'unknown') else 'unknown'
            if not described:
                coverage = 'unknown'
            candidate['selected'] = True
            candidate['suitability'] = {
                'reason': str(item.get('reason') or '')[:800],
                'coverage': coverage,
                'limitations': str(item.get('limitations') or '')[:800],
                'basis': 'indexed_description' if described else 'metadata_only'}
            if not described:
                candidate['suitability']['limitations'] = 'Only bibliographic metadata was available; exact assumptions and coverage remain unchecked.'
            selected.append(item['id'])
        s['standard_selection_done'] = True
        s['standard_selection_usable'] = True
        s['standard_unresolved'] = str(value.get('unresolved') or '')[:1200] if isinstance(value, dict) else ''
        partial = any(candidate.get('selected') and candidate.get('suitability', {}).get('coverage') == 'partial'
                      for candidate in s['candidates'])
        needs_more = partial or bool(s['standard_unresolved'])
        if (not selected or needs_more) and s['standard_selection_calls'] < 3 and not s.get('standard_discovery_continued'):
            if s.get('standard_discovery_stage', 0):
                s['standard_discovery_continued'] = True
                retained = [source for source in pool if source.get('candidate_index') is not None
                            and s['candidates'][source['candidate_index']].get('selected')]
                s.setdefault('standard_record_archive', []).extend(source for source in pool if source not in retained)
                s['standard_pool'] = retained
            s['phase'] = 'standard_discover'
        else:
            s['phase'] = 'answer'


    def _compose_standard(self):
        from .refcheck import describe, _fold
        chosen = self.chosen()
        status = self.state.get('status', 'running')
        stopped = ('The check stopped early: ' + self.state.get('stop_reason', status) + '\n\n'
                   if status not in ('answered', 'partial', 'running') else '')
        if not chosen:
            out = ['No suitable scholarly reference was confirmed in this bounded search.']
            if self.state.get('standard_unresolved'):
                out.append('The returned records did not adequately support the requested scope.')
            failures = self.state.get('standard_provider_errors') or {}
            if failures:
                out.append('Unavailable sources: ' + '; '.join(provider + ': ' + error for provider, error in failures.items()) + '.')
            out.append('The search log retains the attempted candidates and queries; an unchecked title or Wikipedia page was not substituted.')
            return stopped + '\n\n'.join(out)
        author = self.state.get('standard_author')
        if author:
            chosen.sort(key=lambda candidate: candidate['verdict']['record'].get('published') or '', reverse=True)
        out = ['Matching records for ' + author + ' (ordered by first submission date where available):'] if author else []
        for index, candidate in enumerate(chosen, 1):
            verdict = candidate['verdict']
            record = verdict.get('record') or {}
            source = {**candidate, **{key: record.get(key) or '' for key in ('authors', 'title', 'year')}} if record else candidate
            suitability = candidate.get('suitability') or {}
            heading = ('Reference lead (scope unconfirmed): ' if suitability.get('coverage') in ('partial', 'unknown')
                       else 'Suggested reference: ')
            if len(chosen) > 1:
                heading = str(index) + '. ' + heading
            out.append(heading + '**' + describe(source) + '**.')
            if not record:
                out.append('Unverified suggestion from model recall; no bibliographic record was confirmed' + (' by the lookup.' if self.state['settings'].get('online') else ' (online search was disabled).'))
                continue
            if author:
                out.append('Identifier: ' + record['identifier'])
                if record.get('published'):
                    out.append('First submitted: ' + record['published'])
                if record.get('updated') and record['updated'] != record.get('published'):
                    out.append('Last revised: ' + record['updated'])
                if record.get('retrieved_at'):
                    out.append('Metadata retrieved: ' + record['retrieved_at']
                               + (' (cached)' if record.get('cached') else ''))
                if not (self.literature and self.literature.online) or record.get('stale_cache'):
                    out.append('Current recency was not checked online; cached metadata may be out of date.')
                if record.get('abstract'):
                    out.append('Indexed abstract' + (' (truncated)' if record.get('abstract_truncated') else '')
                               + ': ' + record['abstract'])
            normal = lambda title: re.findall(r'\w+', _fold(title))
            if normal(record['title']) != normal(candidate['title']):
                out.append('The title is taken from the matching bibliographic record and differs from model recall.')
            if record.get('publisher'):
                out.append('Publisher in the bibliographic record: ' + record['publisher'] + '.')
            elif record.get('venue'):
                out.append('Published in: ' + record['venue'] + '.')
            if suitability.get('basis') == 'indexed_description' and suitability.get('reason'):
                out.append('Relevance assessment from the indexed description: ' + suitability['reason'])
                if record.get('description_source_url') and record['description_source_url'] != record.get('url'):
                    out.append('Indexed description source: ' + record['description_source_url'])
            elif not suitability:
                out.append('The bibliographic record was located; relevance was not assessed before the check stopped.')
            elif suitability:
                out.append('Relevance is a lead based on the title and bibliographic metadata; detailed coverage remains unverified.')
            if suitability.get('limitations'):
                out.append('Scope limits: ' + suitability['limitations'])
            if record.get('year_kind') == 'catalog_first_publication':
                out.append('The year is the catalog work date; a particular edition was not identified.')
            if candidate.get('locator'):
                out.append('Possible place from model recall: **' + candidate['locator'] + '** — unconfirmed.')
                if record.get('year') and candidate.get('year') and str(record['year']) != str(candidate['year']):
                    out.append('The record is dated ' + str(record['year']) + ', whereas model recall suggested '
                               + str(candidate['year']) + '; the guessed place may refer to a different edition.')
            if record.get('url'):
                out.append('Book or paper record: ' + record['url'])
        if self.state.get('standard_unresolved'):
            out.append('Part of the requested scope remains unconfirmed; see the source-specific limits above.')
        failures = self.state.get('standard_provider_errors') or {}
        if failures:
            out.append('Unavailable sources: ' + '; '.join(provider + ': ' + error for provider, error in failures.items()) + '.')
        if any(candidate['verdict'].get('record') for candidate in chosen):
            out.append('Bibliographic identity was checked for the linked records. Exact theorem statements, locations and hypotheses were not verified in the full text.')
        return stopped + '\n\n'.join(out)
