# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Compile a conversation write-up with figures, a table, citations and honest draft status."""
import base64
import json
import shutil
import unittest
from uuid import uuid4

from mathagent.gui import store

try:
    from .test_gui import GuiCase, text
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import GuiCase, text


class ConversationWriteupTests(GuiCase):
    extra_args = ('--ctx', '40960')

    @unittest.skipIf(shutil.which('latexmk') is None, 'latexmk is not installed')
    def test_a_draft_with_figures_tables_and_citations_compiles_and_shows_what_to_check(self):
        paper = self.root / 'paper'
        paper.mkdir()
        (paper / 'macros.sty').write_text('\\newcommand{\\E}{\\mathrm{E}}\n')
        (paper / 'refs.bib').write_text('@article{HS04,\n  author = {Holcman, D. and Schuss, Z.},\n'
                                        '  title = {Escape through a small opening},\n  year = {2004},\n}\n')
        (paper / 'fig.png').write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='))
        (paper / 'notes.md').write_text('E[tau] ~ |Omega|/(4 eps) for a small disk window [HS04].')
        self.fake.replies = [text(r"""\title{Narrow escape}
\section{Result}
The mean exit time satisfies
\begin{equation}\label{eq:exit}
\E[\tau]\sim|\Omega|/(4\varepsilon)
\end{equation}
by \cite{HS04}, see also \cite{Invented13}. By (\ref{eq:exit}) and \eqref{eq:missing}, the equation above holds.
\begin{equation}\label{eq:unused} a = b \end{equation}
% TODO: check the constant 4_x
%  against the reference.
\begin{enumerate}[(i)]
\item A window on the boundary.
\end{enumerate}
\begin{table}[h]\centering
\renewcommand{\arraystretch}{2.2}
\begin{tabular}{cc} a & b \\ \end{tabular}
\end{table}
\begin{figure}[h]\centering\includegraphics[width=1cm]{fig}\includegraphics{missing-plot}\end{figure}""")]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note. \\newcommand{\\E}{\\mathbb{E}}', 'seconds': 300,
                                                   'source_files': ['paper/notes.md'],
                                                   'template_files': ['paper/macros.sty', 'paper/refs.bib']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        prompt = self.fake.requests[0]['messages'][1]['content']
        self.assertIn('HS04: Holcman, D., Escape through a small opening (2004)', prompt)  # the author's .bib
        self.assertIn('never invent a bibliographic entry', prompt)
        self.assertIn('\\eqref{eq:short-name}', prompt)  # numbering and references are asked for
        job = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((job / 'state.json').read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertTrue((self.root / 'notes-writeup.pdf').exists() and (job / 'artifacts' / 'compiled.pdf').exists())
        document = state['document']
        table = document[document.index('\\begin{table}'):document.index('\\end{table}')]
        self.assertIn('\\renewcommand{\\arraystretch}{2.2}', table)  # stays in its table, unbraced
        self.assertNotIn('{{2.2}}', document)
        self.assertIn('\\renewcommand{\\E}{{\\mathbb{E}}}', document)  # the request's notation wins
        self.assertIn('\\usepackage[shortlabels]{enumitem}', document)
        self.assertEqual(state['missing_figures'], ['missing-plot'])  # a placeholder; fig.png was copied
        block = document[document.index('Points to check'):document.index('\\section{Result}')]
        self.assertIn('check the constant 4\\_x against the reference.', block)
        self.assertIn('Invented13', block)
        self.assertNotIn('HS04', block)
        self.assertIn('missing-plot', block)
        self.assertIn('By \\eqref{eq:exit} and', document)  # (\\ref{...}) normalised
        self.assertIn('eq:missing', block)
        self.assertNotIn('eq:exit', block)
        self.assertIn('the equation above', block)
        self.assertIn('\\begin{equation*} a = b \\end{equation*}', document)  # numbered only when referred to
        self.assertIn('\\begin{equation}\\label{eq:exit}', document)
        self.assertTrue(any('Invented13' in warning for warning in state['warnings']))
        self.assertEqual(state['status'], 'partial')
        self.assertIn('Invented13', state['compile']['references']['citations'])
        self.assertIn('eq:missing', state['compile']['references']['labels'])

        # Without a .bib, a bibliography is made from the identifiers the material gives; an entry without one is dropped.
        (paper / 'notes2.md').write_text('E[tau] ~ |Omega|/(4 eps) [HS04], '
                                       '[doi:10.1007/s10955-004-5712-8](https://doi.org/10.1007/s10955-004-5712-8).')
        self.fake.replies = [text(r"""\title{Without a bibliography}
\section{Result}
By \cite{HS04}, see also \cite{Fake99}.
% BEGIN BIBTEX
@article{HS04,
  author = {Holcman, D. and Schuss, Z.},
  title = {Escape through a small opening},
  journal = {J. Stat. Phys.},
  year = {2004},
  doi = {10.1007/s10955-004-5712-8},
}
@misc{Fake99, author = {Nobody}, title = {Invented}, year = {1999}}
% END BIBTEX""")]
        started = self.ok('POST', '/api/writeup', {'goal': 'A short note.', 'seconds': 300, 'source_files': ['paper/notes2.md']})
        made = self.root / '.mathagent' / 'research' / started['id'] / 'state.json'
        self.wait_for(lambda: made.exists() and json.loads(made.read_text())['status'] in ('complete', 'partial'), timeout=240)
        self.assertIn('There is no bibliography file', self.fake.requests[-1]['messages'][1]['content'])
        state = json.loads(made.read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['compile']['references']['citations'], ['Fake99'])
        document = state['document']
        self.assertTrue(document.startswith('% The bibliography made'))  # embedded: the .tex compiles on its own
        self.assertIn('doi = {10.1007/s10955-004-5712-8}', document)
        self.assertIn('\\bibliography{', document)
        self.assertNotIn('Invented', document)
        self.assertIn('Fake99', document[document.index('Points to check'):document.index('\\section{Result}')])

        # A conversation's retrieved record supplies the bibliography even when
        # the writer emits only a citation, without retyping the metadata.
        literature_id = str(uuid4())
        literature = {**state, 'id': literature_id, 'kind': 'literature', 'status': 'listed', 'document': '',
                      'draft': '[doi:10.1007/s10955-004-5712-8](https://doi.org/10.1007/s10955-004-5712-8)',
                      'pool': [{'n': 1, 'title': 'Escape through a small opening',
                                'authors': ['D. Holcman', 'Z. Schuss'], 'year': 2004, 'type': 'journal-article',
                                'doi': '10.1007/s10955-004-5712-8'}],
                      'organisation': {'entry': [1], 'themes': []}}
        directory = self.root / '.mathagent' / 'research' / literature_id
        directory.mkdir()
        (directory / 'artifacts').mkdir()
        (directory / 'state.json').write_text(json.dumps(literature))
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        store.append_items(self.root, chat, [{'role': 'route', 'mode': 'literature', 'status': 'done',
                                              'job_id': literature_id, 'request': 'Find a reference.'}])
        self.fake.replies = [text(r'\title{A cited note}\section{Reference}By \cite{Holcman2004}.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': '@writeup this report',
                                                   'mode_call': {'mode': 'writeup', 'start': 0}})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        saved = self.ok('GET', f'/api/chats/{chat}')
        card = [item for item in saved['transcript'] if item['role'] == 'route'][-1]
        self.assertIn('job_id', card)
        completed = self.ok('GET', f'/api/research/{card["job_id"]}')
        self.assertEqual(completed['status'], 'complete', completed['warnings'])
        self.assertFalse(completed['compile']['references']['unresolved'])
        self.assertTrue(completed['outputs']['pdf'])
        self.assertIn('Holcman2004: D. Holcman', self.fake.requests[-1]['messages'][1]['content'])

if __name__ == '__main__':
    unittest.main()
