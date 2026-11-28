# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Compile a conversation write-up with figures, a table, citations and honest draft status."""
import base64
import io
import json
import shutil
import unittest
import zipfile
from urllib.request import Request, urlopen

from test_gui import GuiCase, text


# The outline call comes first; a plan with no sections leaves the writing to the request.
PLAN = json.dumps({'kind': 'notes', 'scope': 'document', 'language': 'English', 'title': 'Notes', 'short_title': 'Notes',
                   'instruction': 'A short note.', 'requirements': [], 'inventory': [], 'problems': [], 'notation': [],
                   'sections': [], 'needs': []})


class ConversationWriteupTests(GuiCase):
    extra_args = ('--ctx', '40960')

    @unittest.skipIf(shutil.which('latexmk') is None, 'latexmk is not installed')
    def test_compiled_draft_and_its_sources_can_be_downloaded(self):
        paper = self.root / 'paper'
        paper.mkdir()
        (paper / 'macros.sty').write_text('\\newcommand{\\E}{\\mathrm{E}}\n')
        (paper / 'refs.bib').write_text('@article{HS04,\n  author = {Holcman, D. and Schuss, Z.},\n'
                                        '  title = {Escape through a small opening},\n  year = {2004},\n}\n')
        (paper / 'fig.png').write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='))
        (paper / 'notes.md').write_text('E[tau] ~ |Omega|/(4 eps) for a small disk window [HS04].')
        self.fake.replies = [text(PLAN), text(r"""\title{Narrow escape}
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
        job_id = started['id']
        state = self.ok('GET', f'/api/research/{job_id}')
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertEqual(state['status'], 'partial')
        self.assertIn('Invented13', state['compile']['references']['citations'])
        self.assertIn('eq:missing', state['compile']['references']['labels'])

        def download(path):
            request = Request(f'http://127.0.0.1:{self.port}/api/research/{job_id}/{path}',
                              headers={'Authorization': 'Bearer ' + self.token})
            with urlopen(request, timeout=10) as reply:
                self.assertEqual(reply.status, 200)
                self.assertIn('attachment', reply.headers['Content-Disposition'])
                return reply.read()

        self.assertTrue(download('pdf?download=1').startswith(b'%PDF-'))
        with zipfile.ZipFile(io.BytesIO(download('source'))) as bundle:
            self.assertEqual(bundle.read('document.tex'), state['document'].encode())
            self.assertEqual(bundle.read('document-body.tex'), download('body'))
            figures = [name for name in bundle.namelist() if name.endswith('.png')]
            self.assertEqual(len(figures), 1)
            self.assertIn('{' + figures[0] + '}', state['document'])
            self.assertEqual(bundle.read(figures[0]), (paper / 'fig.png').read_bytes())
