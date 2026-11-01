"""Style profiles: exact extraction from .tex articles, the job through the interface, the saved file."""
import json
import unittest

from mathagent import style

try:
    from .test_gui import GuiCase, text
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import GuiCase, text

PREAMBLE = r"""\documentclass{amsart}
\newcommand{\R}{\mathbb{R}}
\newcommand\norm[1]{\left\lVert #1 \right\rVert}
\DeclareMathOperator{\dive}{div}
\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}
\def\eps#1{\varepsilon_{#1}}
\newcommand{\unused}{x}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
% \newcommand{\commented}{y}
"""

NUMERICS = PREAMBLE + r"""\title[Short]{A finite volume scheme for \R-valued flows}
\begin{document}
\begin{abstract} We design and analyse a scheme. \end{abstract}
\section{Introduction}
We now turn to the scheme. Let $u\in\R^d$ with $\norm{u}\le 1$ and $\abs{\dive u}\le\eps{0}$; 100\% of cells.
\section{Numerical experiments}
\begin{lemma} Stability. \end{lemma}
\begin{proof} The key observation is that the flux is monotone. We split the argument into three steps, first bounding
the discrete energy, then the time derivative, and finally passing to the limit in the weak formulation of the scheme,
which is long enough to serve as the representative proof shown to the model in the excerpts. \end{proof}
\end{document}
"""

GEOMETRY = r"""\documentclass{amsart}
\newcommand{\Ric}{\operatorname{Ric}}
\begin{document}
\title{Rigidity of Ricci flat metrics}
\section{Introduction}
In this note we prove rigidity. Throughout, $\Ric = 0$.
\end{document}
"""


class ExtractionTests(unittest.TestCase):
    def test_macros_environments_and_uses(self):
        macros = style.macro_definitions(NUMERICS)
        self.assertEqual(macros['R'], r'\newcommand{\R}{\mathbb{R}}')
        self.assertEqual(macros['norm'], r'\newcommand\norm[1]{\left\lVert #1 \right\rVert}')
        self.assertEqual(macros['abs'], r'\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}')
        self.assertEqual(macros['eps'], r'\def\eps#1{\varepsilon_{#1}}')
        self.assertNotIn('commented', macros)
        self.assertEqual(style.environment_definitions(NUMERICS)['lemma'], r'\newtheorem{lemma}[theorem]{Lemma}')
        body = style.document_body(NUMERICS)
        self.assertEqual((style.uses(body, 'R'), style.uses(body, 'unused'), style.uses(body, 'eps')), (1, 0, 1))
        self.assertIn('100\\%', style.strip_comments(NUMERICS))

    def test_scan_and_excerpts(self):
        found = style.scan(NUMERICS)
        self.assertEqual(found['title'], r'A finite volume scheme for \R-valued flows')
        self.assertEqual(found['abstract'], 'We design and analyse a scheme.')
        self.assertEqual(found['headings'], ['Introduction', 'Numerical experiments'])
        self.assertEqual(found['environments']['lemma'], 1)
        shown = style.excerpts(NUMERICS, 4000)
        self.assertIn('--- A proof ---', shown)
        self.assertLessEqual(len(style.excerpts(NUMERICS, 300)), 600)

    def test_only_verbatim_phrases_are_kept(self):
        cleaned, dropped = style.clean_style({'aim': 'A' * 900, 'structure': ['x'] * 30, 'habits': [],
                                              'phrases': ['We now   turn to', '"The key observation is that"', 'Invented signpost here']},
                                             [NUMERICS])
        self.assertEqual(cleaned['phrases'], ['We now turn to', 'The key observation is that'])
        self.assertEqual((dropped, len(cleaned['aim']), len(cleaned['structure'])), (1, 400, 10))


ARTICLE = {'aim': 'Design a scheme and prove it converges, then test it.',
           'structure': ['Introduction: model problem and contributions', 'Scheme', 'Analysis', 'Experiments'],
           'habits': ['Announces each lemma before proving it.'], 'phrases': ['We now turn to', 'Made-up phrase in no article']}


class StyleJobTests(GuiCase):
    def setUp(self):
        super().setUp()
        (self.root / 'scheme.tex').write_text(NUMERICS)
        (self.root / 'scheme2.tex').write_text(NUMERICS.replace('A finite volume', 'A second'))
        (self.root / 'ricci.tex').write_text(GEOMETRY)
        (self.root / 'mymacros.sty').write_text('\\newcommand{\\Ric}{\\operatorname{Ric}}\n')

    def test_style_is_sorted_by_domain_condensed_and_saved(self):
        domains = {'domains': [{'name': 'Numerical analysis', 'description': 'Schemes with proofs and tests.', 'articles': ['M1', 'M2']},
                               {'name': 'Geometry', 'description': 'Rigidity results.', 'articles': ['M3', 'M9']}]}
        geometry = {'aim': 'Prove a rigidity theorem.', 'structure': ['Introduction', 'Proof'], 'habits': ['Short.'],
                    'phrases': ['In this note we prove']}
        merged = {**ARTICLE, 'phrases': ['We now turn to', 'The key observation is that']}
        self.fake.replies = [text(json.dumps(domains)), text(json.dumps(ARTICLE)), text(json.dumps(ARTICLE)),
                             text(json.dumps(geometry)), text(json.dumps(merged))]
        started = self.ok('POST', '/api/style', {'name': 'My papers', 'source_files': ['scheme.tex', 'scheme2.tex', 'ricci.tex'],
                                                 'template_files': ['scheme.tex', 'mymacros.sty'],
                                                 'goal': 'Numerics and geometry.', 'tokens': 20000, 'seconds': 60})
        self.assertEqual(started['task']['label'], 'Writing style')
        self.assertEqual(self.wait_task(timeout=30)['state'], 'done')
        self.assertEqual(len(self.fake.requests), 5)  # one domain has one article: no merge call for it
        self.assertEqual(self.fake.requests[0]['format'], style.DOMAINS_SCHEMA)
        self.assertIn('Numerics and geometry.', self.fake.requests[0]['messages'][1]['content'])
        self.assertIn('--- A proof ---', self.fake.requests[1]['messages'][1]['content'])
        self.assertFalse(self.fake.requests[1]['think'])

        detail = self.ok('GET', f'/api/research/{started["id"]}')
        self.assertEqual((detail['kind'], detail['status'], detail['style_name']), ('style', 'saved', 'My papers'))
        self.assertFalse(detail['settings']['online'])
        self.assertIn('were not found verbatim', ' '.join(detail['warnings']))

        profile = json.loads((self.root / '.mathagent' / 'styles' / 'My papers.json').read_text())
        self.assertEqual(profile, detail['profile'])
        self.assertTrue(style.valid_profile(profile))
        numerics, geometry_domain = profile['domains']
        self.assertEqual(numerics['articles'], ['scheme.tex', 'scheme2.tex'])
        self.assertEqual(numerics['phrases'], ['We now turn to', 'The key observation is that'])
        self.assertEqual(geometry_domain['phrases'], ['In this note we prove'])
        names = [m['name'] for m in profile['macros']]
        self.assertNotIn('unused', names)  # defined but never used
        self.assertEqual(names[0], 'R')
        self.assertEqual(next(m for m in profile['macros'] if m['name'] == 'Ric')['articles'], ['ricci.tex'])
        self.assertEqual([a['domain'] for a in profile['articles']], ['Numerical analysis', 'Numerical analysis', 'Geometry'])

        prompt = style.style_prompt(profile, 'Geometry')
        self.assertIn(r'\newcommand{\Ric}{\operatorname{Ric}}', prompt)
        self.assertNotIn(r'\norm', prompt)  # only the macros that domain's articles use
        self.assertNotIn('Schemes with proofs', prompt)
        self.assertLessEqual(len(style.style_prompt(profile)), style.PROMPT_CHARS)
        self.assertIn(r'\newtheorem{lemma}[theorem]{Lemma}', style.style_sty(profile, 'Numerical analysis'))
        self.assertEqual([f['name'] for f in profile['template']], ['scheme.tex', 'mymacros.sty'])
        self.assertEqual(profile['template'][0]['content'], NUMERICS)  # kept whole: the style file is self-contained
        listed = self.ok('GET', '/api/styles')['styles']
        self.assertEqual([s['name'] for s in listed], ['My papers'])
        self.assertEqual(listed[0]['template'], [{'name': 'scheme.tex', 'size': len(NUMERICS)},
                                                 {'name': 'mymacros.sty', 'size': len('\\newcommand{\\Ric}{\\operatorname{Ric}}\n')}])

    def test_a_saved_styles_template_is_set_replaced_and_removed(self):
        style.save_profile(self.root, {'format': 'square-style', 'version': 1, 'name': 'Mine', 'created_at': 't', 'updated_at': 't',
                                       'articles': [], 'macros': [], 'environments': [], 'domains': []})
        changed = self.ok('POST', '/api/styles/template', {'name': 'Mine', 'template_files': ['mymacros.sty']})['style']
        self.assertEqual([f['name'] for f in changed['template']], ['mymacros.sty'])
        self.assertIn('content', style.load_profile(self.root, 'Mine')['template'][0])
        for body, status in (({'name': 'Mine', 'template_files': ['notes.md']}, 400), ({'name': 'Mine', 'template_files': ['../x.tex']}, 400),
                             ({'name': 'Other', 'template_files': ['mymacros.sty']}, 404)):
            self.assertEqual(self.request('POST', '/api/styles/template', body)[0], status, body)
        self.assertNotIn('template', self.ok('POST', '/api/styles/template', {'name': 'Mine', 'template_files': []})['style'])
        self.assertNotIn('template', style.load_profile(self.root, 'Mine'))

    def test_the_style_write_ups_use_is_a_saved_setting(self):
        style.save_profile(self.root, {'format': 'square-style', 'version': 1, 'name': 'Mine', 'created_at': 't', 'updated_at': 't',
                                       'articles': [], 'macros': [], 'environments': [],
                                       'domains': [{'name': 'Numerics', 'articles': []}, {'name': 'Geometry', 'articles': []}]})
        self.assertEqual(self.ok('GET', '/api/styles')['choice'], {'mode': 'auto'})
        for body in ({'mode': 'none'}, {'mode': 'style', 'style': 'Mine'}, {'mode': 'domain', 'style': 'Mine', 'domain': 'Geometry'},
                     {'mode': 'auto'}):
            self.assertEqual(self.ok('POST', '/api/styles/choice', body)['choice'], body)
            self.assertEqual(self.ok('GET', '/api/styles')['choice'], body)
        for body, status in (({'mode': 'style', 'style': 'Other'}, 404), ({'mode': 'domain', 'style': 'Mine', 'domain': 'Algebra'}, 400),
                             ({'mode': 'sometimes'}, 400), ({'mode': 'style', 'style': '../x'}, 400)):
            self.assertEqual(self.request('POST', '/api/styles/choice', body)[0], status, body)
        self.assertEqual(self.ok('GET', '/api/styles')['choice'], {'mode': 'auto'})

    def test_learning_a_style_again_keeps_its_template(self):
        style.save_profile(self.root, {'format': 'square-style', 'version': 1, 'name': 'Mine', 'created_at': 't', 'updated_at': 't',
                                       'articles': [], 'macros': [], 'environments': [], 'domains': [],
                                       'template': [{'name': 'mymacros.sty', 'content': 'x'}]})
        self.fake.replies = [text(json.dumps({'domains': [{'name': 'Geometry', 'description': 'd', 'articles': ['M1']}]})),
                             text(json.dumps(ARTICLE))]
        self.ok('POST', '/api/style', {'name': 'Mine', 'source_files': ['ricci.tex'], 'tokens': 20000, 'seconds': 60})
        self.assertEqual(self.wait_task(timeout=30)['state'], 'done')
        self.assertEqual(style.load_profile(self.root, 'Mine')['template'], [{'name': 'mymacros.sty', 'content': 'x'}])

    def test_requests_are_checked_before_starting(self):
        for body in ({'name': 'x'}, {'name': 'x', 'source_files': ['notes.md']}, {'name': '../evil', 'source_files': ['scheme.tex']},
                     {'source_files': ['scheme.tex']}):
            status, value, _ = self.request('POST', '/api/style', body)
            self.assertEqual(status, 400, (body, value))
        self.assertEqual(self.fake.requests, [])


if __name__ == '__main__':
    unittest.main()
