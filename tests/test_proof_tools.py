# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mathagent.literature import LiteratureTools
from mathagent.numerics.executor import ExperimentResult
from mathagent.numerics.session import PythonSandbox
from mathagent.proof_tools import ProofDesk
from mathagent.tools import Workspace


class ProofDeskTests(unittest.TestCase):
    def test_open_exact_user_supplied_locator_with_a_long_public_title(self):
        url = 'https://example.org/papers/finite-groups-in-the-multiplicative-group-of-a-field.html'
        request = f'Read the source at [{url}]({url}) and verify the hypotheses.'
        desk = ProofDesk(self.agent, [request])
        desk.scope(request, None, None, [])
        def source(address, headers, size):
            if address.endswith('/robots.txt'):
                return 200, {}, b'User-agent: *\nAllow: /'
            self.assertEqual(address, url)
            return 200, {'content-type': 'text/plain'}, b'The exact public paper requested for this test.'
        with patch.object(self.library, '_request_once', side_effect=source) as wire:
            opened = json.loads(desk.execute('open_paper', {'identifier_or_url': url}))
            self.assertIn('document_id', opened, opened)
            calls = wire.call_count
            changed = desk.execute('open_paper', {'identifier_or_url': url + '?copy=extra'})
            self.assertIn('private source text', changed)
            self.assertEqual(wire.call_count, calls)

        # A URL quoted on its own is a usable locator. An excerpt pasted for analysis grants no exception.
        desk.scope(f'Please check `{url}`.', None, None, [])
        self.assertIn(url, desk.requested_urls)
        excerpt = f'Consider this manuscript excerpt: "The confidential reference is {url}."'
        desk.scope(excerpt, None, None, [])
        desk.protect(excerpt)
        self.assertNotIn(url, desk.requested_urls)
        self.assertIn('private source text', desk.execute('open_paper', {'identifier_or_url': url}))

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.library = LiteratureTools(self.root)
        self.agent = SimpleNamespace(workspace=Workspace(self.root, literature=self.library))

    def test_native_experiment_is_recovered_with_the_same_inputs(self):
        executor = Mock()
        executor.probe.return_value = {'image': 'sandbox-test'}
        executor.run.return_value = ExperimentResult('ok', exit_code=0, result={'det': -1})
        sandbox = PythonSandbox(executor=executor)
        self.agent.workspace.numerics = sandbox.automatic_session(self.root)
        desk = ProofDesk(self.agent)
        names = {s['function']['name'] for s in desk.schemas()}
        self.assertEqual(names, {'execute_python', 'read_experiment', 'search_papers', 'open_paper', 'read_paper', 'search_paper'})
        args = {'code': 'RESULT = {"det": -1}', 'purpose': 'a private lead', 'seed': 7}
        receipt = desk.new_receipt_id()
        self.assertIsNone(desk.recover('execute_python', args, receipt))
        observed = desk.execute('execute_python', args, receipt_id=receipt)
        self.assertEqual(json.loads(observed)['experiment_id'], receipt)
        restored = ProofDesk(self.agent).recover('execute_python', args, receipt)
        self.assertEqual(json.loads(restored)['result'], {'det': -1})
        evidence = desk.primary_evidence('execute_python', args, restored)
        self.assertEqual((evidence['code'], evidence['seed'], evidence['result']), (args['code'], 7, {'det': -1}))
        self.assertNotIn('purpose', evidence)
        self.assertIsNone(desk.primary_evidence('execute_python', {**args, 'seed': 8}, restored))
        self.assertIsNone(desk.primary_evidence('execute_python', args, '{"status":"error"}'))
        read_args = {'experiment_id': receipt, 'part': 'result'}
        raw_page = desk.execute('read_experiment', read_args)
        reopened = desk.primary_evidence('read_experiment', read_args, raw_page)
        self.assertEqual(json.loads(reopened['observed_page']['text']), {'det': -1})
        self.assertEqual(reopened['experiment_id'], receipt)
        self.assertEqual(reopened['code'], args['code'])
        self.assertNotIn('purpose', reopened)
        self.assertIsNone(desk.primary_evidence('read_experiment', {'experiment_id': desk.new_receipt_id()}, raw_page))
        self.assertEqual(executor.run.call_count, 1)
        forged = desk.execute('execute_python', {**args, '_experiment_id': receipt})
        self.assertEqual(json.loads(forged)['status'], 'invalid_arguments')
        self.assertEqual(executor.run.call_count, 1)
        self.agent.workspace.numerics = None
        resumed = ProofDesk(self.agent)
        self.assertEqual(json.loads(resumed.recover('execute_python', args, receipt))['status'], 'ok')

    def test_exact_paper_evidence_and_online_lookup_after_historical_cache_only_snapshot(self):
        source = 'This bound is uniform over all frequencies and all time horizons.'
        (self.root / 'note.tex').write_text(source)
        doc = self.library.import_local('note.tex')['document_id']
        args = {'document_id': doc}
        desk = ProofDesk(self.agent, [source])
        self.assertIn('private', json.loads(desk.execute('search_papers', {'query': source}))['error'])
        self.assertEqual(self.library.stats['requests'], 0)
        desk.literature.restore({**self.library.snapshot(), 'online': False, 'stats': {'requests': 2, 'returned_chars': 4}})
        self.assertTrue(self.library.online)
        from test_refcheck import ATOM
        with patch.object(self.library, '_request_once', return_value=(200, {}, ATOM)) as fetch:
            found = json.loads(desk.execute('search_papers', {'query': 'uniform estimate'}))
        self.assertEqual(found['results'][0]['title'], 'Admissibility theory in abstract Sobolev scales')
        self.assertFalse(found['cached'])
        fetch.assert_called_once()
        self.assertEqual(self.library.stats['requests'], 3)
        passage = desk.primary_evidence('read_paper', args, desk.execute('read_paper', args))
        self.assertIn(source, passage['passage'])
        self.assertEqual(passage['citation'], doc + ':L1-L1')
        self.assertEqual(passage['source_url'], 'workspace:note.tex')
        self.assertTrue(passage['text_sha256'])
        self.assertGreater(self.library.stats['returned_chars'], 0)
        self.assertIsNone(desk.primary_evidence('search_paper', args, '{"matches":[]}'))
        self.assertIn('error', json.loads(desk.execute('write_file', {})))
