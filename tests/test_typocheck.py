# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Protected LaTeX spans never become proposed language corrections."""
import unittest

from mathagent import typocheck, typolint


def edits(old, new):
    source = typolint._Source([r'\begin{document}', old, r'\end{document}'])
    return typocheck.line_edits(7, old, new, source.prose.splitlines()[1])


class Helpers(unittest.TestCase):
    def test_masked_math_arguments_and_comments_are_left_alone(self):
        self.assertEqual(edits('Let $x_1$ be a point.', 'Let $x_2$ be a point.'), [])
        self.assertEqual(edits('As in \\cite{smiht} we argue.', 'As in \\cite{smith} we argue.'), [])
        self.assertEqual(edits('We argue. % teh comment', 'We argue. % the comment'), [])
        self.assertEqual(edits('We argue here.', 'We argue\nhere.'), [])
