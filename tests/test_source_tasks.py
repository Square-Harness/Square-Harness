# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Requests about an attached file are sent to the right skill without a model call."""
import unittest

from mathagent import proof_routing
from mathagent.source_tasks import automatic_source_action

FILE = ['manuscript.tex']


def mode(query, files=FILE):
    return (automatic_source_action(query, files) or {}).get('mode')


class TypoRequests(unittest.TestCase):
    def test_a_request_for_typos_is_a_typo_check(self):
        # Live: "Can you check the typos still on the same manuscript?" was sent to Exploration.
        for query in ('Can you check the typos still on the same manuscript?', 'Proofread my paper',
                      'Peux-tu corriger les coquilles de ce manuscrit ?', 'Are there spelling mistakes in this document?'):
            action = automatic_source_action(query, FILE)
            self.assertEqual(action['mode'], 'typo_check', query)
            self.assertEqual((action['files'], action['effort'], action['request']), (FILE, 'high', query))
        self.assertEqual(mode('List the typos in manuscript.tex', []), 'typo_check')

    def test_other_requests_keep_their_skill(self):
        self.assertEqual(mode('Review this manuscript'), 'referee')
        self.assertEqual(mode('Check the proof of Lemma 2.3 in this paper'), 'quick_review')
        self.assertEqual(mode('Explain Theorem 3.1 of this paper'), 'explain')

    def test_no_typo_check_without_a_clear_request(self):
        # A review with the typos is two tasks for the router; a typo inside one proof is a question about that proof.
        for query in ('Review this paper and check the typos', 'Is there a typo in the proof of Lemma 2.3?',
                      "Don't check the typos, just summarize this paper"):
            self.assertNotEqual(mode(query), 'typo_check', query)
        self.assertIsNone(mode('What is a typo?', []))

    def test_the_router_is_told_about_the_manuscript_skills(self):
        self.assertIn('typo_check lists only its typos', proof_routing.POLICY)
        self.assertIn('typo_check', proof_routing.SCHEMA['properties']['tasks']['items']['properties']['mode']['enum'])


if __name__ == '__main__':
    unittest.main()
