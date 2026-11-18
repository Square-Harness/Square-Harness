# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Compile a conversation write-up with figures, a table, citations and honest draft status."""
import base64
import json
import re
import shutil
import unittest
from uuid import uuid4

from mathagent.gui import store

try:
    from .test_gui import GuiCase, text
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import GuiCase, text


# The outline call comes first; a plan with no sections leaves the writing to the request.
PLAN = json.dumps({'kind': 'notes', 'scope': 'document', 'language': 'English', 'title': 'Notes', 'short_title': 'Notes',
                   'instruction': 'A short note.', 'requirements': [], 'inventory': [], 'problems': [], 'notation': [],
                   'sections': [], 'needs': []})


NOTHING_FOUND = json.dumps({'issues': []})


def report_points(job):
    """The report's list of points to check."""
    report = (job / 'report.md').read_text()
    return report[report.index('## Points to check'):].split('\n## ', 1)[0]


def writing_prompt(fake):
    """The writing call's prompt: the outline call comes before it, the review calls after it."""
    return next(r['messages'][1]['content'] for r in reversed(fake.requests)
                if 'Output the LaTeX body only' in r['messages'][1]['content'])


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
        prompt = writing_prompt(self.fake)
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
        # The PDF's block only counts, in the document's language; the points are listed in the report.
        block = document[document.index('Points to check'):document.index('\\section{Result}')]
        self.assertIn('1 TODO comment in the source marks a point to settle.', block)
        self.assertNotIn('textbackslash', block)
        points = report_points(job)
        self.assertIn('check the constant 4_x against the reference.', points)
        self.assertIn('Invented13', points)
        self.assertNotIn('HS04', points)
        self.assertIn('missing-plot', points)
        self.assertIn('By \\eqref{eq:exit} and', document)  # (\\ref{...}) normalised
        self.assertIn('eq:missing', points)
        self.assertNotIn('eq:exit', points)
        self.assertIn('the equation above', points)
        self.assertIn('\\begin{equation*} a = b \\end{equation*}', document)  # numbered only when referred to
        self.assertIn('\\begin{equation}\\label{eq:exit}', document)
        self.assertTrue(any('Invented13' in warning for warning in state['warnings']))
        self.assertEqual(state['status'], 'partial')
        self.assertIn('Invented13', state['compile']['references']['citations'])
        self.assertIn('eq:missing', state['compile']['references']['labels'])

        # Without a .bib, a bibliography is made from the identifiers the material gives; an entry without one is dropped.
        (paper / 'notes2.md').write_text('E[tau] ~ |Omega|/(4 eps) [HS04], '
                                       '[doi:10.1007/s10955-004-5712-8](https://doi.org/10.1007/s10955-004-5712-8).')
        self.fake.replies = [text(PLAN), text(r"""\title{Without a bibliography}
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
        # The runner saves its state, then writes its report: wait for both.
        self.wait_for(lambda: made.exists() and json.loads(made.read_text())['status'] in ('complete', 'partial')
                      and (made.parent / 'report.md').exists(), timeout=240)
        self.assertIn('There is no bibliography file', writing_prompt(self.fake))
        state = json.loads(made.read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['compile']['references']['citations'], ['Fake99'])
        document = state['document']
        self.assertTrue(document.startswith('% The bibliography made'))  # embedded: the .tex compiles on its own
        self.assertIn('doi = {10.1007/s10955-004-5712-8}', document)
        self.assertIn('\\bibliography{', document)
        self.assertNotIn('Invented', document)
        self.assertIn('Fake99', report_points(made.parent))

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
        self.fake.replies = [text(PLAN), text(r'\title{A cited note}\section{Reference}By \cite{Holcman2004}.')]
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
        self.assertIn('Holcman2004: D. Holcman', writing_prompt(self.fake))

@unittest.skipIf(shutil.which('latexmk') is None, 'latexmk is not installed')
class WholePaperTests(GuiCase):
    """A whole paper is written section by section, its introduction and its abstract last."""
    extra_args = ('--ctx', '40960')

    def paper(self, intro_source):
        section = lambda heading, role, source, covers=(): {
            'heading': heading, 'role': role, 'source': source, 'covers': list(covers), 'parts': [],
            'requested_by': 'R1' if source == 'composed' else '', 'purpose': heading + '.', 'thread': []}
        plan = json.loads(PLAN)
        plan.update(kind='article', pages=8, requirements=['an abstract and an introduction'], sections=[
            section('Abstract', 'abstract', 'composed'), section('Introduction', 'introduction', intro_source),
            section('Stability', 'body', 'material', ['S1']), section('Convergence', 'body', 'material', ['S2']),
            section('Numerical results', 'body', 'material')])
        skeleton = {'sections': [{'heading': 'Abstract', 'label': 'sec:abstract', 'opens': '', 'closes': ''},
                                 {'heading': 'Introduction', 'label': 'sec:intro', 'opens': '', 'closes': ''},
                                 {'heading': 'Stability', 'label': 'sec:stability', 'opens': '', 'closes': ''},
                                 {'heading': 'Convergence', 'label': 'sec:convergence', 'opens': '', 'closes': ''},
                                 {'heading': 'Numerical results', 'label': 'sec:numerics', 'opens': '', 'closes': ''}],
                    'items': [{'id': 'S1', 'environment': 'lemma', 'label': 'lem:stability', 'name': 'stability',
                               'section': 'sec:stability', 'role': 'auxiliary', 'uses': []},
                              {'id': 'S2', 'environment': 'theorem', 'label': 'thm:convergence', 'name': 'convergence',
                               'section': 'sec:convergence', 'role': 'main', 'uses': ['S1']}],
                    'equations': [], 'floats': [], 'aliases': [{'from': 'sec:numerical', 'to': 'sec:numerics'}]}
        body = (r'\title{The theta-scheme}' '\n' r'\section{Stability}\label{sec:stability}' '\n'
                r'\begin{lemma}\label{lem:stability} The step is stable. \end{lemma}' '\n'
                r'\section{Convergence}\label{sec:convergence}' '\n'
                r'\begin{theorem}\label{thm:convergence} The scheme converges. \end{theorem}' '\n'
                r'\begin{proof} By Lemma~\ref{lem:stability}. \end{proof}' '\n'
                r'\section{Numerical results}\label{sec:numerics}' '\n' r'The rates agree with Theorem~\ref{thm:convergence}.')
        abstract = ('We prove that the theta-scheme for the heat equation converges for every theta between one half and '
                    'one, with no condition linking the time step and the mesh size, by a stability estimate of the '
                    'one-step operator and a bound of the truncation error, and we observe the predicted rates.')
        return json.dumps(plan), json.dumps(skeleton), body, abstract

    def run_paper(self, replies, sources, rounds=1):
        self.fake.replies = [text(reply) for reply in replies]
        started = self.ok('POST', '/api/writeup', {'goal': 'An article with an abstract and an introduction.', 'seconds': 300,
                                                   'rounds': rounds, 'source_files': sources})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        job = self.root / '.mathagent' / 'research' / started['id']
        return json.loads((job / 'state.json').read_text()), job

    def test_the_body_comes_first_then_the_introduction_and_the_abstract(self):
        (self.root / 'notes.md').write_text('S1: the step is stable. S2: the scheme converges.')
        plan, skeleton, body, abstract = self.paper('composed')
        intro = (r'\section{Introduction}' '\n' r'We prove Theorem~\ref{thm:convergence}, using Lemma~\ref{lem:stability}. '
                 r'Section~\ref{sec:stability} bounds the step, Section~\ref{sec:convergence} proves the theorem and '
                 r'Section~\ref{sec:numerics} reports the experiments.')
        state, job = self.run_paper([plan, skeleton, body, intro, abstract], ['notes.md'])
        self.assertEqual([c['role'] for c in state['calls']], ['plan', 'skeleton', 'writing', 'writing', 'abstract'])
        prompts = [r['messages'][1]['content'] for r in self.fake.requests if 'Output the LaTeX body only' in r['messages'][1]['content']]
        self.assertIn('This call writes the sections Stability; Convergence; Numerical results and nothing else', prompts[0])
        self.assertNotIn('I1 [required]', prompts[0])  # the body's rules only
        self.assertIn('This call writes the introduction only', prompts[1])
        self.assertIn('THE TEXT ALREADY WRITTEN (in full', prompts[1])
        self.assertIn('I12 [style]', prompts[1])
        self.assertLess(state['body'].index('\\section{Introduction}\\label{sec:intro}'), state['body'].index('\\section{Stability}'))
        self.assertEqual(state['title'], 'The theta-scheme')
        self.assertEqual(state['abstract'], abstract)
        self.assertLess(state['document'].index('\\begin{abstract}'), state['document'].index('\\maketitle'))
        self.assertNotIn('frame-links', [f['rule'] for f in state['checks']])
        self.assertIn('## Writing calls', (job / 'report.md').read_text())

    def test_the_authors_introduction_is_kept_and_only_its_links_are_edited(self):
        context = ' '.join(['The theta-scheme combines the implicit and the explicit Euler steps with a weight theta.'] * 4)
        (self.root / 'intro.tex').write_text(r'\section{Introduction}\label{sec:intro}' '\nWe study the theta-scheme.\n'
                                             'The experiments are in Section~\\ref{sec:numerical}.\n' + context + '\n')
        (self.root / 'notes.md').write_text('S1: the step is stable. S2: the scheme converges.')
        plan, skeleton, body, abstract = self.paper('material')
        edits = ('@@ issue 1 lines 2-2\nWe study the theta-scheme and prove Theorem~\\ref{thm:convergence}.\n'
                 '@@ issue 2 lines 3-3\nSection~\\ref{sec:stability} bounds the step, Section~\\ref{sec:convergence} proves '
                 'the theorem, and the experiments are in Section~\\ref{sec:numerics}.\n')
        state, _ = self.run_paper([plan, skeleton, body, edits, abstract], ['intro.tex', 'notes.md'])
        self.assertEqual([c['role'] for c in state['calls']], ['plan', 'skeleton', 'writing', 'revise_section', 'abstract'])
        asked = next(r['messages'][1]['content'] for r in self.fake.requests if 'in this introduction by edits' in r['messages'][1]['content'])
        self.assertIn('does not cite the main result', asked)
        self.assertNotIn('points to no label', asked)  # the skeleton's alias repaired sec:numerical by code
        self.assertIn('We study the theta-scheme and prove Theorem~\\ref{thm:convergence}.', state['body'])
        self.assertIn('the experiments are in Section~\\ref{sec:numerics}', state['body'])
        self.assertEqual(state['frame_edits']['applied'], 2, state['frame_edits'])
        self.assertIn('combines the implicit and the explicit Euler steps', state['body'])  # the author's text stays
        self.assertNotIn('frame-links', [f['rule'] for f in state['checks']])

    def test_the_parts_of_an_authors_introduction_are_read_not_asked_and_hold_nothing(self):
        context = ' '.join(['The theta-scheme combines the implicit and the explicit Euler steps with a weight theta.'] * 4)
        (self.root / 'intro.tex').write_text('\\section{Introduction}\\label{sec:intro}\nWe study the theta-scheme.\n' + context + '\n')
        (self.root / 'notes.md').write_text('S1: the step is stable. S2: the scheme converges.')
        plan, skeleton, body, abstract = self.paper('material')
        plan = json.loads(plan)
        plan['kind'] = 'paper_part'
        next(s for s in plan['sections'] if s['role'] == 'introduction')['parts'] = ['I1', 'I2', 'I3', 'I4', 'I5']
        parts = json.dumps({'items': [{'rule': r, 'provides': r != 'I2', 'quote': 'We study the theta-scheme.',
                                       'problem': 'No earlier work is cited.' if r == 'I2' else ''} for r in ('I1', 'I2', 'I3', 'I4', 'I5')]})
        agrees = json.dumps({'agrees': True, 'problem': ''})
        state, job = self.run_paper([json.dumps(plan), skeleton, body, 'No edits.', abstract, NOTHING_FOUND,
                                     json.dumps({'summary': '', 'items': []}), json.dumps({'items': []}), agrees, abstract, parts],
                                    ['intro.tex', 'notes.md'], rounds=2)
        self.assertNotIn('part-missing', [f['rule'] for f in state['checks']])  # no % PART: marker is asked of the author's text
        self.assertIn('intro_parts', [c['role'] for c in state['calls']])
        item = next(i for i in state['ledger'] if i['rule'] == 'I2')
        self.assertEqual((item['layer'], item['fixable'], item['status']), ('intro', 'author', 'open'))
        self.assertFalse([g for g in state['gates'] if 'required parts' in g['message']])  # the author's text: not held
        report = (job / 'report.md').read_text()
        self.assertIn('No earlier work is cited.', report[report.index('### For you to settle'):])

    def test_finished_texts_are_assembled_and_only_what_is_new_is_written(self):
        from mathagent import writeup
        a = ('\\newcommand{\\Op}{\\mathcal{A}}\n\\section{Setting}\\label{sec:setting}\nLet $h>0$ be the mesh size and $\\Op$ the operator.\n'
             '\\begin{equation}\\label{eq:scheme}\nU^{n+1}=U^n+h\\,D U^{n+1}.\n\\end{equation}\n'
             '\\begin{theorem}\\label{thm:main} The scheme \\eqref{eq:scheme} converges for every $h$. \\end{theorem}\n')
        b = ('\\newcommand{\\Op}{\\mathcal{B}}\n\\section{Experiments}\\label{sec:experiments}\nWe recall the scheme\n'
             '\\begin{equation}\\label{eq:scheme}\nU^{n+1}=U^n+dx\\,D U^{n+1}.\n\\end{equation}\n'
             'With $dx=1/10$ and $\\Op$ the rates agree with \\eqref{eq:scheme} and Theorem~\\ref{thm:main}.\n')
        (self.root / 'a.tex').write_text(a)
        (self.root / 'b.tex').write_text(b)
        section = lambda heading: {'heading': heading, 'role': 'body', 'source': 'material', 'covers': [], 'parts': [],
                                   'requested_by': '', 'purpose': '', 'thread': []}
        plan = json.loads(PLAN)
        plan.update(kind='article', assemble=True, sections=[section('Setting'), section('Experiments')],
                    notation=[{'object': 'mesh size', 'symbol': '\\Delta x', 'also_written': 'h'}])
        skeleton = {'sections': [{'heading': 'Setting', 'label': 'sec:setting', 'opens': '', 'closes': '', 'file': 'a.tex'},
                                 {'heading': 'Experiments', 'label': 'sec:experiments', 'opens': '', 'closes': '', 'file': 'b.tex'}],
                    'items': [{'id': 'S1', 'environment': 'theorem', 'label': 'thm:main', 'name': 'convergence',
                               'section': 'sec:setting', 'role': 'main', 'uses': []}],
                    'equations': [{'label': 'eq:scheme', 'what': 'the scheme', 'section': 'sec:setting'}], 'floats': [], 'aliases': []}
        # What the model is scripted to answer: the texts as spliced, with the one symbol, and the duplicate display cited.
        spliced, _, _, _ = writeup.splice_texts([(n, writeup.split_body(x)[2], writeup.split_body(x)[1]) for n, x in (('a.tex', a), ('b.tex', b))])
        # The table keeps the experiments' dx: code turns it round, since a.tex holds the theorem.
        table = json.dumps({'rows': [{'object': 'mesh size', 'keep': 'dx', 'replace': [{'file': 'a.tex', 'from': 'h'}]}]})
        frozen, _ = writeup.freeze(writeup.replace_symbol(spliced[1], 'dx', 'h')[0])
        self.assertEqual(frozen.split('\n')[1:3], ['We recall the scheme', '[[D1]]'])
        cited = '@@ issue 1 lines 2-3\nThe scheme is \\eqref{eq:scheme}.\n'  # an edit of two lines, not the whole text
        self.fake.replies = [text(reply) for reply in (json.dumps(plan), json.dumps(skeleton), table, cited, NOTHING_FOUND,
                                                       json.dumps({'summary': '', 'items': []}), json.dumps({'items': []}))]
        started = self.ok('POST', '/api/writeup', {'goal': 'Merge a.tex and b.tex into one article.', 'seconds': 300, 'rounds': 2,
                                                   'source_files': ['a.tex', 'b.tex']})
        self.assertEqual(self.wait_task(timeout=240)['state'], 'done')
        job = self.root / '.mathagent' / 'research' / started['id']
        state = json.loads((job / 'state.json').read_text())
        self.assertTrue(state['compile']['ok'], state['compile']['errors'])
        roles = [c['role'] for c in state['calls']]
        self.assertEqual(roles[:4], ['plan', 'skeleton', 'notation', 'revise_section'])
        self.assertNotIn('writing', roles)  # nothing new to write but the stitches
        body = state['body']
        self.assertEqual(body.count('\\label{eq:scheme}'), 1)  # stated once, where the skeleton places it
        self.assertIn('The scheme is \\eqref{eq:scheme}.', body)
        self.assertIn('rates agree with \\eqref{eq:scheme}', body)  # the removed display's references follow the kept one
        self.assertIn('With $h=1/10$ and $\\OpB$', body)  # one notation, the theorem's; the other \\Op renamed
        self.assertIn('\\newcommand{\\OpB}', state['document'])
        self.assertIn('notation-clash', [f['rule'] for f in state['checks']])
        fidelity = next(r['messages'][1]['content'] for r in self.fake.requests if 'CHANGED PASSAGES' in r['messages'][1]['content'])
        self.assertNotIn('MATERIAL:', fidelity)  # the changed passages, not the whole material
        report = (job / 'report.md').read_text()
        self.assertIn('## Assembly', report)
        self.assertIn('label eq:scheme renamed eq:scheme-b', report)

if __name__ == '__main__':
    unittest.main()
