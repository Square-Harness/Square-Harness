# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Literature Review publishes source-checked prose after interrupted model calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.deepinfra import DeepInfraGenerationError
from mathagent.litreview import ReviewRunner
from mathagent.tools import Workspace
from tests.test_research import FakeClient, response


class LiteratureReviewTests(unittest.TestCase):
    def test_outline_failure_and_interrupted_check_still_deliver_only_source_checked_prose(self):
        fact = 'The observation time is strictly positive for an exterior strip at positive distance from the singular line.'
        unsupported = 'All bounded domains have the same numerical threshold.'
        located = {'verdicts': [
            {'unit': 1, 'reading': 'Positive time for an exterior strip.', 'verdict': 'supported',
             'support': [{'source': 1, 'condition': 'E1', 'conclusion': 'E1'}]},
            {'unit': 2, 'reading': 'nothing', 'verdict': 'unsupported', 'support': [],
             'problem': 'No numerical statement in the paper.'}]}
        compared = {'checks': [{'unit': 1, 'faithful': True}]}
        measured = DeepInfraGenerationError('Stream interrupted.', category='interrupted',
                                            stats={'eval_count': 12, 'prompt_eval_count': 130})
        empty = DeepInfraGenerationError('Model generation failed.', category='server_error', confirmed_terminal=True,
                                        stats={'eval_count': 0, 'prompt_eval_count': 130})
        transport = FakeClient([[measured], response(fact[:-1] + ' [@1].\n\n' + unsupported[:-1] + ' [@1].'),
                                [empty], response(json.dumps(located)), response(json.dumps(compared))])
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        runner = ReviewRunner(Agent(transport, Workspace(Path(folder.name)), temperature=0.75))
        default_step = runner._step
        def step(phase):
            if phase == 'plan':
                runner._record({'title': 'Time for exterior strips', 'authors': ['A. Author'],
                                'year': 2020, 'doi': '10.5555/strip'}, 'crossref', 'fixture')
                runner.state.update(phase='outline', selected=[1], outline_read_done=True,
                                    scope={'topic': 'Observation timing', 'title': 'Observation timing',
                                           'facets': [], 'terms': [], 'keywords': []},
                                    cards={'1': {'mode': 'primary_passages', 'passages': {'L4-L4': fact}}},
                                    screened={'1': {'relevance': 3, 'kind': 'core', 'facet': -1}})
            else:
                default_step(phase)
        with patch.object(runner, '_step', side_effect=step):
            result = runner.start('Survey observation timing.', max_tokens=40000)
        self.assertEqual(result['status'], 'partial', result.get('stop_reason'))
        self.assertTrue(runner.state['outline_fallback'])
        self.assertTrue(runner.state['checks']['0']['ran'])
        markdown = (runner.directory / 'review.md').read_text()
        self.assertIn(fact[:-1] + ' [1].', markdown)
        self.assertNotIn(unsupported[:-1], markdown)
        self.assertEqual([(row['role'], row['status']) for row in runner.state['calls']],
                         [('outline', 'generation_failed'), ('write', 'complete'), ('check', 'generation_failed'),
                          ('check', 'complete'), ('verify', 'complete')])
        self.assertEqual({request['options']['temperature'] for request in transport.requests}, {0.75})
        self.assertEqual((runner.state['tokens_charged'], runner.state['input_tokens_charged']), (72, 560))
