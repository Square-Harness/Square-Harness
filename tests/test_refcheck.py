# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Reference Check traces a source verdict back to the lines actually read."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathagent import refcheck
from mathagent.agent import Agent
from mathagent.literature import LiteratureTools
from mathagent.refcheck import CheckRunner
from mathagent.tools import Workspace
from tests.test_research import FakeClient, response


def text(value):
    return response(json.dumps(value))


class CheckTests(unittest.TestCase):
    def test_proof_check_reads_one_supplied_work_before_model_verification(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        url = 'https://example.org/math/(groups)'
        body = b'Notes\nLemma 3.4. A subgroup of a group contains its identity.\n'
        for valid, broader in [(True, False), (False, False), (True, True)]:
            with self.subTest(valid=valid, broader=broader):
                library = LiteratureTools(root)
                def replies():
                    yield response('The statement in the supplied source is sufficient.')
                    observed = next(e for e in runner.state['evidence'] if e['tool'] == 'read_paper')
                    doc = json.loads(observed['result'])['document_id']
                    yield text({'level': 'read', 'locator': 'Lemma 3.4', 'lines': 'L2-L2' if valid else 'L87-L87',
                                'document_id': doc, 'record': url, 'statement_found': 'Every subgroup contains the identity.',
                                'note': '', 'needs_other_sources': broader})
                    if broader:
                        yield response('No further source established.')
                        yield text({'found': False, 'candidate': {}})
                runner = CheckRunner(Agent(FakeClient(replies()), Workspace(root, literature=library), ctx=32768))
                with patch.object(library, '_fetch', return_value=(body, 'text/plain', url)):
                    runner.start(f'Check Lemma 3.4 in [the source]({url}).' + (' Find other references too.' if broader else ''),
                                 asker='proof')
                self.assertEqual([e['tool'] for e in runner.state['evidence']], ['open_paper', 'read_paper'])
                self.assertEqual([c['role'] for c in runner.state['calls']],
                                 ['verify', 'verdict'] + (['discover', 'discover'] if broader else []))
                self.assertIn('A subgroup of a group contains its identity.', json.dumps(runner.agent.client.requests[0]['messages']))
                verdict = runner.state['candidates'][0]['verdict']
                docs = refcheck.export_evidence(runner.state)[0]['documents']
                if valid:
                    self.assertEqual((verdict['level'], verdict['source_url'], verdict['lines']), ('read', url, 'L2-L2'))
                    self.assertEqual(docs[0]['passages'][0]['range'], 'L2-L2')
                else:
                    self.assertNotEqual(verdict['level'], 'read')
                    self.assertFalse(docs)
