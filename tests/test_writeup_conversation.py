"""Write-ups end to end: a proved result written up in a saved style and deleted with its chat; a draft with
figures, a table, citations and TODOs compiled for real."""
import base64
import json
import shutil
import unittest
from unittest.mock import patch

from mathagent import style
from mathagent.gui import store

try:
    from .test_gui import APPROVAL, PROOF, GuiCase, text
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import APPROVAL, PROOF, GuiCase, text


PROFILE = {'format': 'square-style', 'version': 1, 'name': 'My papers', 'created_at': '', 'updated_at': '',
           'articles': [{'path': 'a.tex', 'sha256': '0', 'title': 'A', 'domain': 'Real analysis'}],
           'macros': [{'name': 'R', 'definition': r'\newcommand{\R}{\mathbb{R}}', 'uses': 9, 'articles': ['a.tex']},
                      {'name': 'unusedhere', 'definition': r'\newcommand{\unusedhere}{u}', 'uses': 2, 'articles': ['a.tex']},
                      {'name': 'Dt', 'definition': r'\newcommand\Dt{\Delta t}', 'uses': 30, 'articles': ['a.tex']}],
           'environments': [{'name': 'assumption', 'definition': r'\newtheorem{assumption}[theorem]{Assumption}', 'uses': 3}],
           'domains': [{'name': 'Real analysis', 'description': 'Elementary facts with full proofs.', 'articles': ['a.tex'],
                        'aim': 'State a fact and prove it in full.', 'structure': ['Statement', 'Proof', 'Lennard-Jones numerics'],
                        'habits': ['Announces the idea before the proof.'], 'phrases': ['We now turn to']}]}
BODY = '\\title{A note on reflexivity}\n\\section{Reflexivity}\nFor $x\\in\\R$ and $\\tau^\\dt$, $\\bbone_{x=x}=1$.'


class ConversationWriteupTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def prove_first(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [text(PROOF), text(json.dumps(APPROVAL)), text('Reviewed proof returned.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': '@prove For real x, show x=x.', 'mode_call': {'mode': 'prove', 'start': 0}})
        self.assertEqual(self.wait_task()['state'], 'done')
        self.fake.requests.clear()
        return chat

    def call(self, chat, content, mode):
        with patch('mathagent.writeup.shutil.which', return_value=None):
            self.ok('POST', f'/api/chats/{chat}/route', {'content': content, 'mode_call': {'mode': mode, 'start': 0}})
            self.assertEqual(self.wait_task(timeout=30)['state'], 'done')
        saved = self.ok('GET', f'/api/chats/{chat}')
        card = [item for item in saved['transcript'] if item['role'] == 'route'][-1]
        return card, (self.ok('GET', f'/api/research/{card["job_id"]}') if card.get('job_id') else None)

    def test_writeup_of_a_proof_in_the_saved_style_with_the_requests_notation(self):
        style.save_profile(self.root, PROFILE)
        chat = self.prove_first()
        self.fake.replies = [text('My papers: Real analysis'), text("I'll read the material first."), text(BODY)]
        card, detail = self.call(chat, '@writeup a one-section note with the reviewed proof, with '
                                        '\\newcommand{\\dt}{\\Delta t} and \\newcommand{\\bbone}{\\mathbf{1}}', 'writeup')
        self.assertEqual((card['mode'], card['status']), ('writeup', 'done'))
        self.assertEqual(len(self.fake.requests), 3)  # style choice, a non-answer asked again, the document
        choice, writing = self.fake.requests[0], self.fake.requests[2]
        self.assertIn('- My papers: Real analysis: Elementary facts with full proofs.', choice['messages'][1]['content'])
        self.assertFalse(choice['think'])
        self.assertEqual(choice['messages'][0]['content'], 'You match mathematical material to the saved styles of its author. '
                         'Answer with one label or with none.')
        prompt = writing['messages'][1]['content']
        self.assertTrue(writing['think'])
        self.assertIn('a one-section note with the reviewed proof', prompt)
        self.assertIn(PROOF, prompt)  # the proof job's full result is the material
        self.assertIn('## Proof result (the model review found no issue)', prompt)
        self.assertIn('NOTATION REQUIRED BY THE REQUEST', prompt)
        self.assertIn('Announces the idea before the proof.', prompt)  # the style digest...
        self.assertNotIn('Lennard-Jones', prompt)  # ...without the papers' own structure
        self.assertNotIn('\\Dt', prompt)  # the style's macro for the requested \\dt is withdrawn
        self.assertNotIn('% src', prompt)
        self.assertEqual(detail['style']['label'], 'My papers: Real analysis')
        self.assertFalse((self.root / 'writeup.tex').exists())  # kept for download
        self.assertEqual(card['outputs'], {'download': 'writeup-a-note-on-reflexivity.tex', 'pdf': False})
        status, document, headers = self.request('GET', f'/api/research/{card["job_id"]}/tex')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Disposition'], 'attachment; filename="writeup-a-note-on-reflexivity.tex"')
        self.assertIn('\\newcommand{\\dt}{{\\Delta t}}', document)  # so \\tau^\\dt is \\tau^{\\Delta t}
        self.assertIn('\\newcommand{\\bbone}{{\\mathbf{1}}}', document)
        self.assertIn(r'\newcommand{\R}{{\mathbb{R}}}', document)  # a style macro the text uses
        self.assertNotIn('unusedhere', document)
        reply = [i for i in self.ok('GET', f'/api/chats/{chat}')['transcript'] if i['role'] == 'assistant'][-1]['content']
        self.assertEqual(reply, 'Write-up ready: `writeup-a-note-on-reflexivity.tex` (download it from the card above).')
        record = store.conversation_record(self.root, chat)
        self.assertIn('## Write-up saved', record)
        self.assertNotIn('tau^', record)  # the document itself is not material for later write-ups
        # Deleting the conversation deletes the copy kept for download; the proof job stays.
        proofs = list((self.root / '.mathagent' / 'proofs').iterdir())
        self.ok('DELETE', f'/api/chats/{chat}')
        self.assertFalse((self.root / '.mathagent' / 'research' / card['job_id']).exists())
        self.assertEqual(self.request('GET', f'/api/research/{card["job_id"]}/tex')[0], 404)
        self.assertEqual(list((self.root / '.mathagent' / 'proofs').iterdir()), proofs)

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

        # Without a .bib, a bibliography is made from the identifiers the material gives; an entry without one is dropped.
        (paper / 'notes2.md').write_text('E[tau] ~ |Omega|/(4 eps) [HS04], doi:10.1007/s10955-004-5712-8.')
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
        document = state['document']
        self.assertTrue(document.startswith('% The bibliography made'))  # embedded: the .tex compiles on its own
        self.assertIn('doi = {10.1007/s10955-004-5712-8}', document)
        self.assertIn('\\bibliography{', document)
        self.assertNotIn('Invented', document)
        self.assertIn('Fake99', document[document.index('Points to check'):document.index('\\section{Result}')])

if __name__ == '__main__':
    unittest.main()
