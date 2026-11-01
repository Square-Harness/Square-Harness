"""@writeup and @summarize: write up the conversation's results as .tex, in a saved style when one fits."""
import json
import shutil
import unittest
from unittest.mock import patch

from mathagent import style
from mathagent.mode_calls import validated_mode_call
from mathagent.gui import store
from mathagent.writeup import SUMMARY_REQUEST, announces_only, named_definition, undefined_names

try:
    from .test_gui import APPROVAL, PROOF, GuiCase, text
except ImportError:  # unittest discover imports test modules as top-level names
    from test_gui import APPROVAL, PROOF, GuiCase, text


class ModeCallTests(unittest.TestCase):
    def test_writeup_alone_summarizes_and_instructions_narrow_it(self):
        self.assertEqual(validated_mode_call('@writeup', {'mode': 'writeup', 'start': 0}), (SUMMARY_REQUEST, 'summarize'))
        self.assertEqual(validated_mode_call('  @summarize ', {'mode': 'summarize', 'start': 2}), (SUMMARY_REQUEST, 'summarize'))
        self.assertEqual(validated_mode_call('@writeup a short note on the lemma', {'mode': 'writeup', 'start': 0}),
                         ('a short note on the lemma', 'writeup'))
        with self.assertRaises(ValueError):
            validated_mode_call('@prove', {'mode': 'prove', 'start': 0})


class LatexHelperTests(unittest.TestCase):
    def test_multi_token_macros_are_braced(self):
        self.assertEqual(style.grouped(r'\newcommand{\dt}{\Delta t}'), r'\newcommand{\dt}{{\Delta t}}')
        for kept in (r'\renewcommand{\leq}{\leqslant}', r'\newcommand\norm[1]{\lVert #1\rVert}', r'\newcommand{\x}{{a b}}'):
            self.assertEqual(style.grouped(kept), kept)

    def test_undefined_commands_are_read_from_the_log_and_named_by_convention(self):
        log = ('./document.tex:96: Undefined control sequence.\n<recently read> \\rmd \n\nl.96 ...such that $\\rmd\n  (x)\n'
               '! Undefined control sequence.\nl.12 $\\bbP\n   (A)$\n')
        self.assertEqual(undefined_names(log), ['rmd', 'bbP'])
        self.assertEqual([named_definition(n) for n in ('bbP', 'rmd', 'cL', 'bbone', 'frakg', 'cl', 'foo')],
                         [r'\newcommand{\bbP}{\mathbb{P}}', r'\newcommand{\rmd}{\mathrm{d}}', r'\newcommand{\cL}{\mathcal{L}}',
                          r'\newcommand{\bbone}{\mathbf{1}}', r'\newcommand{\frakg}{\mathfrak{g}}', None, None])

    def test_an_announcement_is_not_a_document(self):
        self.assertTrue(announces_only("I'll read the exact source lines first."))
        self.assertTrue(announces_only('% only a comment'))
        self.assertFalse(announces_only('\\section{A}\nLet $x$ be real.'))


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

    def call_compiled(self, chat, content, mode):
        self.ok('POST', f'/api/chats/{chat}/route', {'content': content, 'mode_call': {'mode': mode, 'start': 0}})
        self.assertEqual(self.wait_task(timeout=120)['state'], 'done')
        card = [item for item in self.ok('GET', f'/api/chats/{chat}')['transcript'] if item['role'] == 'route'][-1]
        return card, self.ok('GET', f'/api/research/{card["job_id"]}')

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

    @unittest.skipIf(shutil.which('latexmk') is None, 'latexmk is not installed')
    def test_commands_the_sources_never_define_are_supplied_and_it_compiles(self):
        chat = self.prove_first()
        self.fake.replies = [text('\\title{Bound}\n\\section{Bound}\nWe have $\\bbP(A) \\le \\int \\rmd x$.')]
        _, detail = self.call_compiled(chat, '@writeup the bound', 'writeup')
        self.assertTrue(detail['compile']['ok'], detail['compile'])
        self.assertEqual(len(self.fake.requests), 1)  # no repair call: the definitions come from the names
        self.assertIn(r'\newcommand{\bbP}{{\mathbb{P}}}', detail['document'])
        self.assertIn(r'\rmd as \mathrm{d}', ' '.join(detail['warnings']))
        self.assertEqual(detail['status'], 'complete')

    def test_a_styles_template_is_the_preamble_unless_the_write_up_has_its_own(self):
        article = ('\\documentclass[11pt]{article}\n\\usepackage{amsmath,amssymb,amsthm}\n\\newtheorem{lemma}{Lemma}\n'
                   '\\newcommand{\\R}{\\mathbb{R}}\n\\title{An old paper}\n\\author{The Author}\n'
                   '\\begin{document}\n\\maketitle\nOld text.\n\\end{document}\n')
        style.save_profile(self.root, {**PROFILE, 'template': [{'name': 'paper.tex', 'content': article}]})
        chat = self.prove_first()
        self.fake.replies = [text('My papers: Real analysis'), text(BODY)]
        _, detail = self.call(chat, '@writeup a note with \\newcommand{\\dt}{\\Delta t} and \\newcommand{\\bbone}{\\mathbf{1}}', 'writeup')
        self.assertIn('style-template/paper.tex', [source['path'] for source in detail['sources']])
        document = detail['document']
        self.assertTrue(document.startswith('\\documentclass[11pt]{article}'))
        self.assertIn('\\title{A note on reflexivity}', document)
        self.assertNotIn('An old paper', document)
        self.assertIn('\\author{The Author}', document)
        self.assertNotIn('Old text.', document)  # only the preamble of the article
        self.assertEqual(document.count('\\newcommand{\\R}'), 1)  # the template's own \\R, not the style's again
        writing = self.fake.requests[1]['messages'][1]['content']
        self.assertIn('TEMPLATE MACROS', writing)
        self.assertIn('Theorem environments: lemma, and proof.', writing)
        # A write-up given its own template keeps it.
        (self.root / 'notes.md').write_text('For real x, x = x.\n')
        (self.root / 'own.sty').write_text('\\newcommand{\\R}{\\mathbf{R}}\n')
        self.fake.replies = [text('My papers: Real analysis'), text(BODY)]
        with patch('mathagent.writeup.shutil.which', return_value=None):
            started = self.ok('POST', '/api/writeup', {'goal': 'Write up.', 'source_files': ['notes.md'],
                                                       'template_files': ['own.sty'], 'tokens': 30000})
            self.wait_task(timeout=30)
        detail = self.ok('GET', f'/api/research/{started["id"]}')
        self.assertEqual([s['path'] for s in detail['sources']], ['notes.md', 'own.sty'])
        self.assertIn('\\usepackage{own}', detail['document'])
        self.assertNotIn('article}', detail['document'])

    def test_the_style_the_user_chose_is_used_without_asking(self):
        two = {**PROFILE, 'domains': PROFILE['domains'] + [{**PROFILE['domains'][0], 'name': 'Geometry', 'description': 'Curvature.'}]}
        style.save_profile(self.root, two)
        style.save_profile(self.root, {**PROFILE, 'name': 'Other papers'})
        chat = self.prove_first()
        cases = [({'mode': 'none'}, [], None),
                 ({'mode': 'domain', 'style': 'My papers', 'domain': 'Geometry'}, [], 'My papers: Geometry'),
                 ({'mode': 'style', 'style': 'My papers'}, ['an answer naming no label'], 'My papers: Real analysis'),
                 ({'mode': 'domain', 'style': 'Gone', 'domain': 'X'}, ['Other papers: Real analysis'], 'Other papers: Real analysis')]
        for choice, asked, label in cases:
            with self.subTest(choice=choice):
                (self.root / '.mathagent' / 'style-choice.json').write_text(json.dumps(choice))
                self.fake.requests.clear()
                self.fake.replies = [text(answer) for answer in asked] + [text(BODY)]
                _, detail = self.call(chat, '@writeup the note', 'writeup')
                self.assertEqual(len(self.fake.requests), len(asked) + 1)
                self.assertEqual((detail['style'] or {}).get('label'), label)
                if choice['mode'] == 'style':  # the model picks a domain within the chosen style, never none
                    question = self.fake.requests[0]['messages'][1]['content']
                    self.assertIn('Which domain of the author', question)
                    self.assertNotIn('Other papers', question)
                    self.assertNotIn('or with: none', question)
                if choice.get('style') == 'Gone':
                    self.assertIn('is no longer saved; the style was chosen automatically', ' '.join(detail['warnings']))

    def test_writeup_alone_summarizes_without_a_style(self):
        chat = self.prove_first()
        self.fake.replies = [text('\\title{Summary}\n\\section{Results}\nEvery real $x$ satisfies $x=x$.')]
        card, detail = self.call(chat, '@writeup', 'writeup')
        self.assertEqual(card['mode'], 'summarize')
        self.assertEqual(card['request'], SUMMARY_REQUEST)
        self.assertEqual(len(self.fake.requests), 1)  # no saved style: no style choice
        request = self.fake.requests[0]
        self.assertIn('# Summary of results', request['messages'][0]['content'])  # the summarize skill
        self.assertIn('Write a LaTeX summary of the important results', request['messages'][1]['content'])
        self.assertIn('No saved author style fits this material', request['messages'][1]['content'])
        self.assertNotIn('EXACT REFERENCE CONTEXT', request['messages'][1]['content'])  # once, as the material
        self.assertEqual((detail['variant'], detail['style']), ('summary', None))
        self.assertEqual(card['outputs']['download'], 'summary.tex')
        self.assertFalse((self.root / 'summary.tex').exists())
        transcript = self.ok('GET', f'/api/chats/{chat}')['transcript']
        self.assertFalse(any('Delegating' in (item.get('content') or '') for item in transcript))  # progress is not kept
        # Deleting the conversation deletes the summary kept for download; the proof job stays.
        proofs = list((self.root / '.mathagent' / 'proofs').iterdir())
        self.assertTrue((self.root / '.mathagent' / 'research' / card['job_id']).is_dir())
        self.ok('DELETE', f'/api/chats/{chat}')
        self.assertFalse((self.root / '.mathagent' / 'research' / card['job_id']).exists())
        self.assertEqual(self.request('GET', f'/api/research/{card["job_id"]}/tex')[0], 404)
        self.assertEqual(list((self.root / '.mathagent' / 'proofs').iterdir()), proofs)

    def test_nothing_to_summarize_in_a_new_conversation(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [text('There is nothing to summarize yet.')]
        card, _ = self.call(chat, '@summarize', 'summarize')
        self.assertEqual(card['status'], 'failed')
        self.assertIn('Nothing to summarize yet', card['error'])


if __name__ == '__main__':
    unittest.main()
