"""Regressions for interrupted reading lists and overclaimed reference evidence."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import ParseError

from mathagent.agent import Agent
from mathagent.literature import LiteratureTools, _digest, cites_place
from mathagent.litreview import ReviewRunner
from mathagent.refcheck import CheckRunner, _clean_candidate, precise_reference_request
from mathagent.tools import Workspace
from tests.test_litreview import SCOPE, text
from tests.test_research import FakeClient


class ReadingListAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lit = LiteratureTools(self.root, online=True)
        self.runner = ReviewRunner(Agent(FakeClient([]), Workspace(self.root, literature=self.lit)))
        self.runner.state = {**self.runner._extra_state(), 'warnings': [], 'notes': []}

    def record(self, title='Functional analysis', author='Evans', **extra):
        return self.runner._record({'title': title, 'authors': [author], 'year': '2010', **extra}, 'crossref', 'query 1')

    def test_same_title_books_with_distinct_authors_do_not_share_identifiers(self):
        first = self.record(author='Evans', doi='10.1000/evans')
        second = self.record(author='Jost', identifier='zbl:1234.56789')
        self.assertEqual(len(self.runner.state['pool']), 2)
        self.assertIsNone(first['zbl'])
        self.assertIsNone(second['doi'])
        merged = self.record(author='Evans', identifier='zbl:1111.22222')
        self.assertIs(merged, first)
        self.assertEqual(first['zbl'], '1111.22222')

    def test_provider_markup_and_zbl_links_are_normalised(self):
        record = self.record(title='<i>p</i>-Laplacian &amp; control', venue='<b>Journal</b>',
                             doi='10.1000/example', identifier='zbl:1234.56789', url='https://doi.org/10.1000/example')
        self.assertEqual(record['title'], 'p-Laplacian & control')
        self.assertEqual(record['venue'], 'Journal')
        self.assertIn('[Zbl 1234.56789](https://zbmath.org/?q=an:1234.56789)', self.runner._identifier(record))

    def test_malformed_provider_response_is_a_coverage_gap(self):
        for error in (ParseError('not XML'), ValueError('not JSON'), TypeError('unexpected payload')):
            with self.subTest(error=error):
                def fail():
                    raise error
                self.assertEqual(self.runner._fetch('arxiv', 'heat control', fail), [])
                self.assertIn(str(error), self.runner.state['sweep_log'][-1]['error'])

    def test_fenced_json_and_complete_leading_truncated_items_survive(self):
        self.assertEqual(self.runner._json({'text': 'Here is JSON:\n```json\n{"items":[{"n":1}]}\n```'}), {'items': [{'n': 1}]})
        parsed = self.runner._json({'text': '{"items":[{"n":1,"role":"core"},{"n":2,"role":"co', 'complete': False})
        self.assertEqual(parsed, {'items': [{'n': 1, 'role': 'core'}]})

    def test_fallback_never_reintroduces_excluded_candidates(self):
        included = self.record(title='Heat control')
        excluded = self.record(title='Cooking recipes', author='Cook')
        self.runner.state.update(goal='Heat control', scope={'topic': 'Heat control', 'queries': [], 'intent': 'both'})
        self.runner.state['screened'] = {str(excluded['n']): {'role': 'exclude', 'relevance': 0}}
        draft = self.runner._compose()
        self.assertIn('Heat control', draft)
        self.assertNotIn('Cooking recipes', draft)
        self.assertIn('not screened', draft)
        self.assertIsNotNone(included)

    def test_pause_during_sweep_resumes_after_completed_queries(self):
        cancel, calls = threading.Event(), []
        self.runner.cancel = cancel
        self.runner.agent.client = FakeClient([text(SCOPE)])
        def fetch(url, **kwargs):
            calls.append(url)
            # Complete all six calls for query 1, interrupt the first query-2 call.
            if len(calls) == 7:
                cancel.set()
            if 'arxiv' in url:
                return b'<feed xmlns="http://www.w3.org/2005/Atom"/>', 'application/atom+xml', url
            if 'crossref' in url:
                value = {'message': {'items': []}}
            elif 'semanticscholar' in url:
                value = {'data': []}
            else:
                value = {'result': []}
            return json.dumps(value).encode(), 'application/json', url
        with patch.object(self.lit, '_fetch', side_effect=fetch):
            result = self.runner.start('Heat control reading list', max_seconds=600)
            self.assertEqual(result['status'], 'paused')
            self.assertEqual(self.runner.state['sweep_queries_done'], 1)
            self.assertTrue(self.runner.state['draft'])
            saved = json.loads((self.runner.directory / 'state.json').read_text())
            self.assertEqual(saved['draft'], self.runner.state['draft'])
            completed_queries = calls[:6]
            cancel.clear()
            self.runner.resume(result['id'])
        self.assertEqual(self.runner.state['sweep_queries_done'], 2)
        self.assertEqual(self.runner.state['sweep_seeds_done'], 2)
        self.assertTrue(all(calls.count(url) == 1 for url in completed_queries))
        keys = [(row['source'], row['query']) for row in self.runner.state['sweep_log']]
        self.assertEqual(len(keys), len(set(keys)))

    def test_exhausted_reading_list_resumes_only_with_explicit_extra_time(self):
        self.runner.agent.client = FakeClient([text(SCOPE)])
        result = self.runner.start('Heat control reading list', max_seconds=.00001)
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertTrue(self.runner.state['draft'])
        original_limit = self.runner.state['settings']['max_seconds']
        self.assertEqual(self.runner.resume(result['id'])['status'], 'budget_exhausted')
        self.assertEqual(self.runner.state['settings']['max_seconds'], original_limit)
        self.lit.online = False
        with patch.object(self.lit, '_request_once', side_effect=AssertionError('No external fixture traffic')):
            resumed = self.runner.resume(result['id'], additional_seconds=600)
        self.assertEqual(resumed['status'], 'partial')
        self.assertEqual(self.runner.state['settings']['max_seconds'], original_limit + 600)
        self.assertEqual(self.runner.state['time_extensions'][0]['seconds'], 600)
        self.assertTrue(self.runner.state['draft'])

    def test_private_pinned_phrases_are_not_sent_unless_explicitly_requested(self):
        phrase = 'our secret estimate improves the private construction considerably'
        self.runner.state.update(goal='Make a heat control reading list', sources=[{'content': phrase}])
        self.assertEqual(self.runner._public_queries([phrase, 'heat equation observability']), ['heat equation observability'])
        self.runner.state['goal'] = 'Search for ' + phrase
        self.assertEqual(self.runner._public_queries([phrase]), [phrase])

    def test_monographs_are_books_and_comments_do_not_recommend_unlisted_works(self):
        listed = self.record(title='Control notes', type='monograph')
        omitted = self.record(title='An omitted work', author='Other')
        self.runner.state['organisation'] = {'entry': [listed['n']], 'themes': []}
        self.runner.state['annotations'][str(listed['n'])] = {'why': f'Read #{omitted["n"]} then #{listed["n"]}.'}
        rendered = self.runner._entry(listed['n'])
        self.assertIn('[book]', rendered)
        self.assertIn('[unlisted candidate]', rendered)
        self.assertNotIn(f'#{omitted["n"]}', rendered)
        self.assertNotIn('Other (2010)', rendered)

    def test_crossref_metadata_titles_are_plain_text_for_every_caller(self):
        body = {'message': {'items': [{'DOI': '10.1000/plain', 'title': ['<i>p</i>-Laplacian &amp; control'],
                                      'container-title': ['<b>Journal</b>'], 'issued': {'date-parts': [[2020]]}}]}}
        with patch.object(self.lit, '_fetch', return_value=(json.dumps(body).encode(), 'application/json', 'https://api.crossref.org/works')):
            record = self.lit.crossref(query='plain title')[0]
        self.assertEqual(record['title'], 'p-Laplacian & control')
        self.assertEqual(record['venue'], 'Journal')

    def test_recent_and_empty_search_cache_expire(self):
        for provider, records in (('arxiv-recent', [{'title': 'Old preprint'}]), ('crossref', [])):
            with self.subTest(provider=provider):
                key = _digest(json.dumps([provider, 'heat control', 2]))
                self.lit._write(('searches', key + '.json'), json.dumps({'results': records, 'retrieved_at': '2000-01-01T00:00:00+00:00'}))
                fetch = unittest.mock.Mock(return_value=[{'title': 'Fresh work'}])
                result = self.lit._cache_search(provider, 'heat control', 2, fetch)
                self.assertFalse(result['cached'])
                self.assertEqual(result['results'], [{'title': 'Fresh work'}])
                fetch.assert_called_once()


class ReferenceAuditTests(unittest.TestCase):
    def candidate(self):
        return _clean_candidate({'authors': ['Brezis'], 'title': 'Functional analysis and partial differential equations',
                                 'year': '2011', 'locator': 'Theorem 9.26', 'why': ''}, 'recall')

    def selector(self):
        runner = CheckRunner.__new__(CheckRunner)
        candidate = self.candidate()
        candidate['verdict'] = {'level': 'located', 'record': self.book(), 'locator': 'Theorem 9.26'}
        runner.state = {'goal': 'Give a reference for elliptic regularity', 'check': {'lookup': 'standard'},
                        'settings': {'online': True}, 'candidates': [candidate], 'warnings': [],
                        'status': 'budget_exhausted', 'stop_reason': 'Time exhausted'}
        runner._standard_pool_add(self.book(), 0)
        return runner

    def book(self):
        return {'identifier': 'doi:10.1000/book', 'title': 'Functional analysis and partial differential equations',
                'authors': ['Brezis'], 'year': '2011', 'type': 'monograph', 'url': 'https://doi.org/10.1000/book'}

    def test_failed_selection_preserves_located_record_and_qualifies_timeout(self):
        runner = self.selector()
        with patch.object(runner, '_call', return_value={'text': 'not JSON'}):
            runner._standard_select()
        self.assertEqual(len(runner.state['standard_pool']), 1)
        self.assertFalse(runner.state.get('standard_selection_done'))
        self.assertEqual(len(runner.chosen()), 1)
        answer = runner._compose_standard()
        self.assertIn('stopped early: Time exhausted', answer)
        self.assertIn('relevance was not assessed', answer)
        self.assertIn('Brezis', answer)

    def test_selection_accepts_fenced_json(self):
        runner = self.selector()
        with patch.object(runner, '_call', return_value={'text': '```json\n{"selections":[{"id":"R1","coverage":"unknown"}],"unresolved":""}\n```'}):
            runner._standard_select()
        self.assertEqual(len(runner.chosen()), 1)
        self.assertTrue(runner.state['standard_selection_done'])

    def test_equation_labels_with_suffixes_are_not_theorem_citations(self):
        for text in ('Brezis: the problem (9.26)', 'Brezis uses this approach (9.26)', 'Brezis treats each (9.26)'):
            self.assertFalse(cites_place(text, '9.26', 'Brezis'), text)
        self.assertTrue(cites_place('Brezis, Theorem 9.26', '9.26', 'Brezis'))

    def test_read_claim_on_citing_or_unrelated_paper_is_lowered(self):
        runner = CheckRunner.__new__(CheckRunner)
        doc = 'doc-' + 'a' * 24
        runner.agent = SimpleNamespace(workspace=SimpleNamespace(literature=SimpleNamespace(
            _document=lambda _: ({'title': 'An unrelated paper', 'canonical_identifier': 'arxiv:1234.56789'}, []))))
        runner.state = {'candidates': [self.candidate()], 'evidence': []}
        full = ['Theorem 9.26 in [Bre11] gives regularity.', 'References', '[Bre11] Brezis. Functional analysis and partial differential equations. 2011.']
        with patch.object(runner, '_record', return_value=self.book()), patch.object(runner, '_provenance', return_value=({}, {doc: [(1, 3)]}, {})), patch.object(runner, '_text', return_value=(full, [full[0]])):
            verdict = runner._gate(0, {'level': 'read', 'document_id': doc, 'lines': 'L1-L1', 'statement_found': 'A regularity theorem'})
            self.assertEqual(verdict['level'], 'cited')
            recovered = runner._gate(0, {'level': 'read', 'document_id': '', 'lines': ''})
            self.assertEqual(recovered['level'], 'cited')
        with patch.object(runner, '_record', return_value=self.book()), patch.object(runner, '_provenance', return_value=({}, {doc: [(1, 1)]}, {})), patch.object(runner, '_text', return_value=(['Theorem 9.26. Our result holds.'], ['Theorem 9.26. Our result holds.'])):
            verdict = runner._gate(0, {'level': 'read', 'document_id': doc, 'lines': 'L1-L1', 'statement_found': 'Unsupported model claim'})
        self.assertEqual(verdict['level'], 'located')
        self.assertEqual(verdict['statement_found'], '')

    def test_french_exact_locator_requests_and_nested_quote_url(self):
        for request in ('Donne une référence exacte.', 'Quel est le numéro du théorème ?', 'Trouve le théorème exact dans ce livre.'):
            self.assertTrue(precise_reference_request(request), request)
        self.assertFalse(precise_reference_request('Sans référence exacte, donne un livre classique.'))
        evidence = {'result': json.dumps({'quotes': [{'document_id': 'doc-1', 'source_url': 'https://arxiv.org/abs/1234.56789'}]})}
        self.assertEqual(CheckRunner._source_url(evidence, 'doc-1'), 'https://arxiv.org/abs/1234.56789')


if __name__ == '__main__':
    unittest.main()
