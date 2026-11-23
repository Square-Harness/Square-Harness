# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The model-free typo rules: each finds its slip and stays silent on correct text."""

import unittest

from mathagent import typolint

HEAD = ['\\documentclass{article}', '\\newtheorem{thm}{Theorem}', '\\newtheorem{lem}[thm]{Lemma}', '\\begin{document}']
FILLER = ['We study the problem and we show that the solution is unique for this class of data and of domains.'] * 3


def lint(body, **options):
    options.setdefault('spelling', False)
    return typolint.lint(HEAD + FILLER + body + ['\\end{document}'], **options)


def rules(body, **options):
    return [f['rule'] for f in lint(body, **options)]


class Slips(unittest.TestCase):
    def test_phrases(self):
        found = lint(['This allows to conclude the proof.', 'The bound holds component wise, at the first order.',
                      'The map is non negative and there exists two constants.'])
        self.assertEqual([f['rule'] for f in found], ['phrase'] * 5)
        self.assertEqual(found[0]['suggestion'], 'allows us to conclude')
        self.assertEqual(found[0]['start_line'], len(HEAD) + len(FILLER) + 1)

    def test_correct_phrases_are_silent(self):
        self.assertEqual(rules(['The parameter is allowed to vary, which allows us to conclude.',
                                'This holds at first order. There exist an open set $U$ and a map $f$.',
                                'We use an Euler scheme, a unique solution, an hour, a uniform bound and an $L^2$ norm.',
                                'The proof is the same as before, i.e. the bound holds. Hence the claim.']), [])

    def test_articles_doubled_words_and_sentence_starts(self):
        self.assertEqual(rules(['There is an unique solution. A analogous bound holds for the the gradient.',
                                'This ends the proof. the next step is easier (See the appendix).']),
                         ['article', 'article', 'doubled-word', 'capital-in-parenthesis', 'lowercase-sentence-start'])

    def test_math_is_not_prose(self):
        self.assertEqual(rules(['Let $a an other non x$ and $\\text{the the}$ be given.',
                                '% a comment with the the doubled word', '\\label{eq:the the}']), [])

    def test_display_endings(self):
        body = ['We have', '\\begin{equation}\\label{eq:a}', '  a = b \\cdot', '\\end{equation}', 'Hence the claim.',
                'Then', '\\[', '  c = d,', '\\]', 'The proof is complete.',
                'Moreover', '\\begin{align}', '  e &= f.', '\\end{align}', 'where $f$ is the function above.',
                'Finally', '\\[ g = h, \\]', 'where $h$ is fixed, and', '\\[ g = h. \\]', 'This is the end.']
        self.assertEqual(rules(body), ['display-cdot', 'display-comma', 'display-period'])

    def test_cref_and_references(self):
        body = ['\\begin{lem}\\label{lem:a} The claim holds for all data. \\end{lem}',
                '\\begin{thm}\\label{thm:b} The claim holds for all data. \\end{thm}', '\\label{lem:a}',
                'By the \\cref{lem:a} and Theorem~\\ref{lem:a}, see Theorem~\\ref{thm:b} and \\cref{thm:missing}.']
        found = lint(body)
        self.assertEqual(sorted(f['rule'] for f in found),
                         ['article-before-cref', 'duplicate-label', 'reference-kind', 'undefined-reference'])
        self.assertIn('the Lemma 1', next(f for f in found if f['rule'] == 'article-before-cref')['comment'])
        self.assertNotIn('undefined-reference', rules(body + ['\\input{more}']))

    def test_variants_are_one_remark_with_its_places(self):
        body = ['We normalise the quasi-mode by the Walk on Spheres method.', 'The normalization of the quasimode is fixed.',
                'The quasi-mode is normalized, and the Walk on Spheres method is used.', 'The Walk on Sphere method is fast.',
                'Recall that $D(\\mathcal{L})$ is the domain, and $\\mathcal{D}(\\mathcal{L})$ is dense, like $\\mathcal{D}(\\mathcal{L})$.']
        found = [f for f in lint(body) if f['rule'] == 'variant']
        self.assertEqual(len(found), 4)
        self.assertTrue(all(f['importance'] == 'suggestion' and f['lines'] for f in found))

    def test_repeated_slip_is_grouped(self):
        found = lint(['This allows to conclude.'] * 4)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]['lines']), 4)

    def test_spelling_needs_a_near_dictionary_word(self):
        known = {'Explicitely': ['Explicitly'], 'subsingular': ['sub singular'], 'pratice': ['practice', 'pirate'],
                 'Neumann': ['Newman']}
        src = typolint._Source(HEAD + ['Explicitely, in pratice the subsingular term obeys the Neumann condition.'])
        found = list(typolint._spelling(src, run=lambda words: {w: g for w, g in known.items() if w in words}))
        self.assertEqual([(f['quote'], f['suggestion']) for f in found], [('Explicitely', 'Explicitly'), ('pratice', 'practice')])

    def test_other_languages_and_plain_text(self):
        self.assertEqual(typolint.lint(['Nous montrons que le non lineaire est une des questions pour la suite et que les bornes']), [])
        self.assertEqual([f['rule'] for f in typolint.lint(FILLER + ['This allows to conclude the the proof.'], tex=False, spelling=False)],
                         ['doubled-word', 'phrase'])


if __name__ == '__main__':
    unittest.main()
