"""Literature check: recall, per-guess verification and the controller's evidence levels."""
import copy
import json
from pathlib import Path
import sys
from urllib.parse import unquote
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.literature import LiteratureError, LiteratureTools, _digest, bibliography_keys, cites_place
from mathagent import refcheck
from mathagent.refcheck import CheckRunner
from mathagent.tools import Workspace
from tests.test_research import FakeClient, response, tool

ARXIV = '2412.14786v1'  # the citing paper, read through its arXiv HTML
PAPER = '\n'.join([
    'Admissibility theory in abstract Sobolev scales',
    'When the domain is bounded and smooth, the domain of the Neumann Laplacian is exactly the space of all w in H^2',
    'such that the normal derivative vanishes, with equivalent norms; see, e.g., [Bre11, Theorem 9.26].',
    'References',
    '[Bre11] Haim Brezis. Functional analysis, Sobolev spaces and partial differential equations. Springer, 2011.',
]).encode()
ZBMATH = {'result': [{'identifier': '1220.46002', 'title': {'title': 'Functional analysis, Sobolev spaces and partial differential equations'},
                      'contributors': {'authors': [{'name': 'Brezis, Haim'}]}, 'year': '2011',
                      'document_type': {'description': 'book / book article'},
                      'source': {'source': 'Universitext. New York, NY: Springer', 'book': [{'publisher': 'New York, NY: Springer'}]},
                      'zbmath_url': 'https://zbmath.org/5633610',
                      'editorial_contributions': [{'text': 'IX. Sobolev spaces and variational formulation of boundary value problems in dimension N.'}]}]}
BOOK = {'DOI': '10.1007/978-0-387-70914-7', 'title': ['Functional Analysis, Sobolev Spaces and Partial Differential Equations'],
        'author': [{'given': 'Haim', 'family': 'Brezis'}], 'issued': {'date-parts': [[2011]]}, 'type': 'book'}
CITING = {'DOI': '10.1000/ADMISS', 'title': ['Admissibility theory in abstract Sobolev scales'], 'issued': {'date-parts': [[2024]]},
          'author': [{'family': 'Author'}], 'reference': [{'unstructured': 'H. Brezis, Functional analysis, Springer, 2011'}]}
ATOM = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2412.14786v1</id>'
        b'<title>Admissibility theory in abstract Sobolev scales</title><summary>Neumann Laplacian.</summary>'
        b'<author><name>A. Author</name></author><published>2024-12-01</published></entry></feed>')


def page(paper=PAPER):
    return b'<html><body><article class="ltx_document">' + b''.join(b'<p>' + line + b'</p>' for line in paper.split(b'\n')) + b'</article></body></html>'


RECALL = {'statement': 'H^2 regularity for the Neumann problem on a smooth bounded domain.',
          'keywords': ['Neumann problem', 'H2 regularity'],
          'candidates': [{'authors': ['Brezis'], 'title': 'Functional analysis, Sobolev spaces and partial differential equations',
                          'year': '2011', 'locator': 'Theorem 9.26', 'why': 'Chapter 9 treats elliptic regularity.'}]}


def fetch(url, headers=None, max_bytes=None, paper=PAPER):
    """The citation chain's sources: Crossref (the work, then papers on the topic), OpenCitations, arXiv."""
    if 'zbmath' in url:
        return json.dumps(ZBMATH).encode(), 'application/json', url
    if 'api.crossref.org' in url:
        items = [BOOK] if 'Brezis' in unquote(url) and 'Functional' in unquote(url) else [CITING]
        return json.dumps({'message': {'items': items}}).encode(), 'application/json', url
    if 'api.opencitations.net' in url:
        return json.dumps([{'citing': 'omid:br/1 doi:10.1000/admiss'}]).encode(), 'application/json', url
    if 'export.arxiv.org' in url:
        return ATOM, 'application/atom+xml', url
    if url == f'https://arxiv.org/html/{ARXIV}':
        return page(paper), 'text/html', url
    raise AssertionError('unexpected fetch ' + url)


DOC = 'doc-' + _digest('arxiv:' + ARXIV)[:24]


def text(value):
    return response(json.dumps(value))


class StandardSelectionClient(FakeClient):
    """Supply deterministic source-ID selection when suitability is not under test.

    Identity-linked records are selected; independently discovered records need
    an explicit selector. This keeps identity tests from blessing every topic hit.
    """
    def __init__(self, replies, selector=None):
        super().__init__(replies)
        self.selector = selector

    def stream(self, payload):
        prompt = '\n'.join(m.get('content', '') for m in payload['messages'])
        marker = 'ACTUAL BIBLIOGRAPHIC RECORDS (select only R identifiers):\n'
        if marker not in prompt:
            yield from super().stream(payload)
            return
        self.requests.append(copy.deepcopy(payload))
        pool = json.loads(prompt.split(marker, 1)[1].split('\n', 1)[0])
        selected = (self.selector(pool) if self.selector else
                    [item['id'] for item in pool if item['candidate_index'] is not None][:1])
        answer = {'selections': [{'id': source_id, 'reason': 'Indexed description supplies the topic lead.',
                                 'coverage': 'unknown', 'limitations': 'Exact assumptions were not read.'}
                                for source_id in selected], 'unresolved': ''}
        yield from text(answer)




class CheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = patch('mathagent.literature.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)
        # A compact profile keeps scripted replies short: two guesses, no open-paper search.
        compact = patch.dict(refcheck.EFFORTS, {'low': {**refcheck.EFFORTS['low'], 'candidates': 2, 'discover': False}})
        compact.start()
        self.addCleanup(compact.stop)

    def run_check(self, replies, effort='medium', guesses=(), online=True, fetcher=None, lookup=None,
                  selector=None, question=None):
        lit = LiteratureTools(self.root, online=online)
        self.client = StandardSelectionClient(replies, selector=selector) if lookup == 'standard' else FakeClient(replies)
        runner = CheckRunner(Agent(self.client, Workspace(self.root, literature=lit), ctx=16384, predict=4096))
        with patch.object(lit, '_fetch', side_effect=fetcher or fetch):
            options = {'lookup': lookup} if lookup is not None else {}
            result = runner.start(question or 'Find a reference on the regularity of elliptic problems with Neumann boundary conditions.',
                                  effort=effort, guesses=guesses, **options)
        return runner, result

    def searches(self):
        return [response('Search.', [tool('search_papers', query='au:Brezis ti:Functional analysis', provider='zbmath'),
                                     tool('find_quotes', author='Brezis', locator='Theorem 9.26', topic='Neumann regularity',
                                          title='Functional analysis')]),
                response('Open the hit.', [tool('open_paper', identifier_or_url=ARXIV)]),
                response('Find the number.', [tool('search_paper', document_id=DOC, query='9.26')])]

    def verdict(self, **fields):
        value = {'level': 'cited', 'locator': 'Theorem 9.26', 'document_id': DOC, 'lines': 'L3-L3',
                 'record': 'zbl:1220.46002', 'statement_found': 'The Neumann Laplacian has domain H^2 with zero normal derivative.', 'note': ''}
        value.update(fields)
        return text(value)











    def test_cited_reference_with_record_quote_and_bibtex(self):
        runner, result = self.run_check([text(RECALL), *self.searches(), response('Enough evidence.'), self.verdict()])
        self.assertEqual(result['status'], 'answered')
        verdict = runner.state['candidates'][0]['verdict']
        self.assertEqual(verdict['level'], 'cited')
        self.assertIn('Theorem 9.26', verdict['quote'])
        self.assertEqual(verdict['record']['identifier'], 'zbl:1220.46002')
        answer = runner.state['answer']
        self.assertIn('Cited by another paper', answer)
        self.assertIn(f'[{DOC}:L3-L3]', answer)
        self.assertIn('zbl = {1220.46002}', answer)
        self.assertIn('@book{brezis2011', answer)
        self.assertIn('Cited there as: Haim Brezis. Functional analysis', answer)
        self.assertNotIn('Edition:', answer)
        compact = json.loads(runner.result_for_agent())
        self.assertEqual(compact['references'][0]['level'], 'cited')
        self.assertTrue((self.root / '.mathagent' / 'checks' / result['id'] / 'report.md').exists())
        self.assertFalse((self.root / '.mathagent' / 'research').exists())  # not listed as a literature report
        # The verifier never gets a tool to start another check, nor workspace file tools.
        names = {t['function']['name'] for r in self.client.requests for t in r.get('tools', [])}
        self.assertEqual(names, {'search_papers', 'find_quotes', 'open_paper', 'read_paper', 'search_paper'})
        # The controller looked up the record and the quotes before the model's first round.
        self.assertEqual([e['tool'] for e in runner.state['evidence'][:2]], ['search_papers', 'find_quotes'])

        # Seen in review: a passage naming Brezis but citing another work at 9.26 is not "cited" for this book.
        other = PAPER.replace(b'with equivalent norms; see, e.g., [Bre11, Theorem 9.26].',
                              b'by the Brezis-Lieb lemma [BL83] and [Eva10, Theorem 9.26].')
        other += (b'\n[BL83] Haim Brezis and Elliott Lieb. A relation between pointwise convergence of functions and '
                  b'convergence of functionals. 1983.\n[Eva10] Lawrence Evans. Partial differential equations. 2010.')
        fresh = tempfile.TemporaryDirectory()  # a new folder: the first run cached the original paper
        self.addCleanup(fresh.cleanup)
        self.root = Path(fresh.name)
        runner, result = self.run_check([text(RECALL), *self.searches(), response('Enough evidence.'), self.verdict()],
                                        fetcher=lambda url, headers=None, max_bytes=None: fetch(url, headers, max_bytes, paper=other))
        verdict = runner.state['candidates'][0]['verdict']
        self.assertEqual(verdict['level'], 'located')
        self.assertIn('do not cite this work at 9.26', ' '.join(verdict['reasons']))

















if __name__ == '__main__':
    unittest.main()
