"""Writing style end to end: articles sorted by domain, condensed, saved with their template and listed."""
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
        self.assertEqual([f['name'] for f in profile['template']], ['scheme.tex', 'mymacros.sty'])
        self.assertEqual(profile['template'][0]['content'], NUMERICS)  # kept whole: the style file is self-contained
        listed = self.ok('GET', '/api/styles')['styles']
        self.assertEqual([s['name'] for s in listed], ['My papers'])
        self.assertEqual(listed[0]['template'], [{'name': 'scheme.tex', 'size': len(NUMERICS)},
                                                 {'name': 'mymacros.sty', 'size': len('\\newcommand{\\Ric}{\\operatorname{Ric}}\n')}])

if __name__ == '__main__':
    unittest.main()
