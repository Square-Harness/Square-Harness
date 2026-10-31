"""Regressions for source identity, partial reports and review phase budgets."""
import copy
import json
from unittest.mock import patch

from mathagent import manuscript
from mathagent.review import ExplainRunner, JournalRunner, QuickReviewRunner, SummaryReviewRunner, loads, degenerate
from tests.test_review import FakeClient, PAPER, ReviewTestCase, WRONG


class AuditMappingTests(ReviewTestCase):
    def test_inline_and_french_proofs_are_mapped_on_their_original_line(self):
        for text in ('Lemma. x=x. Proof. Reflexivity.', 'Lemme 1. x=x. Démonstration. Réflexivité.'):
            mapping = manuscript.build(text, 'paste.md')
            self.assertEqual(len(manuscript.checkable(mapping)), 1)
            self.assertEqual(mapping['units'][0]['proofs'], [[1, 1]])
            runner = QuickReviewRunner(self.agent(FakeClient(), ctx=32768))
            result = runner.start(text, reference_context={'query': text, 'messages': [], 'files': []}, max_rounds=1)
            self.assertEqual(result['status'], 'reviewed', result)
            self.assertEqual(runner.state['sources'][0]['content'], text)

    def test_nested_tex_proof_retains_the_outer_range_and_late_steps(self):
        text = r'''\newtheorem{thm}{Théorème}
\begin{thm}An identity.\end{thm}
\begin{proof}
First step.
\begin{proof}Proof of a claim.
Claim step.\end{proof}
LATE_OUTER_STEP
\end{proof}'''
        mapping = manuscript.build(text, 'paper.tex')
        self.assertEqual(mapping['units'][0]['type'], 'theorem')
        self.assertEqual(mapping['units'][0]['proofs'], [[3, 8]])

    def test_nested_text_claim_does_not_end_outer_proof(self):
        text = 'Theorem 1. Main claim.\nProof. First step.\nClaim 2. Auxiliary.\nProof. Auxiliary proof. ∎\nLATE_OUTER_STEP\nThus the theorem follows. ∎'
        mapping = manuscript.build(text, 'paper.md')
        self.assertEqual(mapping['units'][0]['proofs'], [[2, 6]])

    def test_corrected_previous_assistant_proof_wins_over_original_user_proof(self):
        corrected = 'Corrected proof: Use compactness, rather than continuity.\n' + ('Retain every hypothesis.\n' * 350)
        history = [{'role': 'user', 'content': WRONG}, {'role': 'assistant', 'content': corrected}]
        context = {'query': 'Check the corrected proof above.', 'files': [], 'history': history,
                   'messages': [history[0], {'role': 'assistant', 'content': 'ARCHIVED long answer'}]}
        runner = QuickReviewRunner(self.agent(FakeClient(), ctx=65536))
        result = runner.start(context['query'], reference_context=context, max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(runner.state['sources'][0]['content'], corrected)
        self.assertEqual(runner.state['sources'][0]['role'], 'assistant')

    def test_json_fences_and_tex_commands_and_repetition(self):
        self.assertEqual(loads('Preface\n```json\n{"value":"\\nabla_x + \\nabla_{y}"}\n```')['value'], r'\nabla_x + \nabla_{y}')
        self.assertTrue(degenerate('abc' * 20))
        self.assertFalse(degenerate('A normal mathematical argument.'))


class AuditReviewTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        (self.root / 'paper.tex').write_text(PAPER)

    def test_multiple_sources_choose_manuscript_and_read_bibtex_with_explicit_input_gap(self):
        (self.root / 'paper.tex').write_text(PAPER.replace('\\begin{thebibliography}{9}', '\\input{proofs}\n\\bibliography{refs}\n\\begin{thebibliography}{9}'))
        (self.root / 'refs.bib').write_text('@book{other, title={Another reference}, author={A. Author}, year={2020}}')
        runner = JournalRunner(self.agent(FakeClient()))
        result = runner.start('Review the manuscript.', source_files=['paper.tex', 'refs.bib'], max_rounds=1)
        self.assertEqual(result['status'], 'partial', result)
        self.assertIn('other', runner.state['map']['bibliography'])
        self.assertIn('External TeX inputs were not expanded', result['report'])
        self.assertIn('(provisional)', result['report'])
        self.assertIn('Scope of this review', runner.state['draft'])
        saved = json.loads((runner.directory / 'state.json').read_text())
        self.assertIn('Scope of this review', saved['draft'])

    def test_partial_reading_can_resume_and_only_failed_reading_is_retried(self):
        bad = FakeClient({'read': lambda p, s: 'Unusable output'})
        runner = SummaryReviewRunner(self.agent(bad))
        result = runner.start('Review.', source_files=['paper.tex'], max_rounds=3)
        self.assertEqual(result['status'], 'partial')
        first = runner.state['read_parts'][0]
        for a, b in runner.state['read_parts'][1:]:
            runner.state['reading'][f'L{a}'].update(usable=True, complete=True, summary='Preserved reading.')
        runner._save()
        client = FakeClient()
        resumed = SummaryReviewRunner(self.agent(client))
        result = resumed.resume(result['id'])
        self.assertEqual(result['status'], 'reviewed', result)
        reads = [c for c in client.requests if 'comments' in c.get('format', {}).get('properties', {})]
        self.assertEqual(len(reads), 1)
        self.assertIn(f'L{first[0]}:', reads[0]['messages'][1]['content'])

    def test_resume_keeps_user_time_cap_unless_an_exact_extension_is_requested(self):
        for addition in (0, 275):
            with self.subTest(additional_seconds=addition):
                runner = SummaryReviewRunner(self.agent(FakeClient({'read': lambda p, s: 'Unusable output'})))
                result = runner.start('Review.', source_files=['paper.tex'], max_rounds=3, max_seconds=1000)
                runner.state.update(status='partial', phase='done', seconds_used=900)
                runner._save()
                resumed = SummaryReviewRunner(self.agent(FakeClient()))
                with patch.object(resumed, '_run', return_value={'status': 'paused'}) as run:
                    resumed.resume(result['id'], additional_seconds=addition)
                run.assert_called_once_with()
                saved = json.loads((runner.directory / 'state.json').read_text())
                self.assertEqual(saved['settings']['max_seconds'], 1000 + addition)
                self.assertEqual(saved['seconds_used'], 900)
                self.assertEqual(saved['status'], 'paused')
                self.assertEqual(saved['phase'], 'read')
                if addition:
                    self.assertEqual([entry['seconds'] for entry in saved['time_extensions']], [addition])
                    self.assertTrue(saved['time_extensions'][0]['time'])
                else:
                    self.assertEqual(saved.get('time_extensions', []), [])

    def test_explicit_time_extension_resumes_reading_and_preserves_report_status(self):
        runner = SummaryReviewRunner(self.agent(FakeClient({'read': lambda p, s: 'Unusable output'})))
        result = runner.start('Review.', source_files=['paper.tex'], max_rounds=3, max_seconds=1000)
        self.assertEqual(result['status'], 'partial')
        runner.state['seconds_used'] = 900
        runner._save()
        resumed = SummaryReviewRunner(self.agent(FakeClient()))
        result = resumed.resume(result['id'], additional_seconds=300)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(resumed.state['settings']['max_seconds'], 1300)
        self.assertEqual([entry['seconds'] for entry in resumed.state['time_extensions']], [300])
        self.assertGreaterEqual(resumed.state['seconds_used'], 900)
        self.assertIn('Status: `reviewed`', result['report'])
        saved = json.loads((resumed.directory / 'state.json').read_text())
        self.assertIn('Scope of this review', saved['draft'])

    def test_long_confirm_is_stopped_before_the_reserved_report_time(self):
        client = FakeClient()
        runner = JournalRunner(self.agent(client))
        clock = [0]
        original = client.stream
        def stream(payload):
            is_confirm = 'assessment' in payload.get('format', {}).get('properties', {}) and 'argument' in payload['format']['properties']
            for event in original(payload):
                clock[0] += 800 if is_confirm else 5
                yield event
        client.stream = stream
        with patch.object(runner, '_elapsed', side_effect=lambda: clock[0]):
            result = runner.start('Review.', source_files=['paper.tex'], max_rounds=3, max_seconds=1000)
        self.assertTrue(runner.state['write'], result)
        self.assertNotEqual(result['status'], 'budget_exhausted')
        self.assertTrue(any('stopped before report synthesis' in w for w in runner.state['warnings']))

    def test_unlocated_main_proof_makes_writer_and_recommendation_provisional(self):
        text = PAPER.replace('\\begin{proof}[Proof of Theorem~\\ref{thm:main}]', '\\begin{proof}[Proof of Lemma~\\ref{lem:embed}]')
        (self.root / 'paper.tex').write_text(text)
        client = FakeClient()
        runner = JournalRunner(self.agent(client))
        result = runner.start('Review.', source_files=['paper.tex'], max_rounds=3)
        self.assertIn('(provisional)', result['report'])
        write = next(p for p in client.requests if 'recommendation' in p.get('format', {}).get('properties', {}))
        self.assertIn('main_results_without_located_proof', write['messages'][1]['content'])

class AuditExplainTests(ReviewTestCase):
    def explanation(self, steps):
        return {'says': 'A compactness statement.', 'idea': 'Use compactness.',
                'steps': [{'start_line': a, 'end_line': b, 'what': text, 'why': text, 'uses': ''}
                          for a, b, text in steps], 'facts': [], 'hypotheses': '', 'gaps': ''}

    def test_accuracy_check_receives_the_steps_added_by_fill(self):
        seen = []
        def check(prompt, schema):
            seen.append(prompt)
            return {'problems': []}
        client = FakeClient({'explain': lambda p, s: self.explanation([(3, 3, 'First step')]),
                             'fill': lambda p, s: {'steps': self.explanation([(4, 5, 'FILLED_CORRECTNESS_MARKER')])['steps']},
                             'explain_check': check})
        runner = ExplainRunner(self.agent(client))
        result = runner.start(WRONG, max_rounds=3)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertIn('FILLED_CORRECTNESS_MARKER', seen[0])
        self.assertIn('Accuracy check', runner.state['draft'])

    def test_fix_with_larger_missing_line_count_is_rejected_without_claiming_correction(self):
        client = FakeClient({'explain': lambda p, s: self.explanation([(3, 5, 'Original step')])})
        runner = ExplainRunner(self.agent(client))
        runner.start(WRONG, max_rounds=1)
        runner.state['sources'][0]['content'] = '\n'.join(['Lemma. A statement.', '', 'Proof. Start.'] + ['A proof step.'] * 7)
        runner._lines_cache.clear()
        runner.state['unit']['proofs'] = [[3, 10]]
        original = self.explanation([(3, 3, 'Original step'), (5, 5, 'Original step'), (7, 10, 'Original step')])
        runner.state['explanation'] = original
        runner.state['explain_problems'] = [{'where': 'step', 'problem': 'Incorrect inference', 'fix': 'Repair it'}]
        fixed = self.explanation([(3, 3, 'CORRECTED_BUT_INCOMPLETE')])
        with patch.object(runner, '_ask', return_value={'value': fixed, 'complete': True}):
            runner._fix()
        self.assertEqual(runner.state['explanation'], original)
        self.assertFalse(runner.state.get('explain_fixed'))
        self.assertTrue(any('lost proof-line coverage' in w for w in runner.state['warnings']))

    def test_corrected_step_text_is_kept_when_coverage_is_preserved(self):
        def explanation(prompt, schema):
            return self.explanation([(3, 5, 'CORRECTED_STEP' if 'PROBLEMS:' in prompt else 'ORIGINAL_STEP')])
        client = FakeClient({'explain': explanation, 'explain_check': lambda p, s: {
            'problems': [{'where': 'step', 'problem': 'Use compactness', 'fix': 'Invoke Rellich'}]}})
        runner = ExplainRunner(self.agent(client))
        result = runner.start(WRONG, max_rounds=3)
        self.assertTrue(runner.state.get('explain_fixed'), result)
        self.assertIn('CORRECTED_STEP', runner.state['explanation']['steps'][0]['what'])

    def test_strategy_inside_proof_does_not_request_mechanism_mode(self):
        client = FakeClient({'explain': lambda p, s: self.explanation([(3, 5, 'Complete explanation')])})
        runner = ExplainRunner(self.agent(client))
        runner.start(WRONG.replace('Since H^1', 'Our strategy is to use compactness. Since H^1'), max_rounds=1)
        self.assertTrue(runner._fine())
        self.assertFalse(runner._mechanism())

class AuditReviewPacketTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        (self.root / 'paper.tex').write_text(PAPER)

    def test_oversized_secondary_proof_does_not_abort_main_check_or_prose(self):
        from mathagent.worker_errors import WorkerInputError
        runner = JournalRunner(self.agent(FakeClient()))
        original = runner._check_jobs
        def jobs(unit, *args, **kwargs):
            if unit['name'].startswith('Lemma'):
                raise WorkerInputError('Complete dependencies do not fit', code='context_overflow', scope='context')
            return original(unit, *args, **kwargs)
        with patch.object(runner, '_check_jobs', side_effect=jobs):
            result = runner.start('Review.', source_files=['paper.tex'], max_rounds=3)
        self.assertEqual(result['status'], 'partial', result)
        self.assertTrue(runner.state['write'])
        self.assertTrue(any(c.get('usable') for c in runner.state['checks'].values()))
        self.assertTrue(any('remains unchecked' in w for w in runner.state['warnings']))

    def test_scope_fallback_preserves_notes_ahead_of_optional_introduction(self):
        client = FakeClient()
        runner = SummaryReviewRunner(self.agent(client, ctx=65536))
        runner.start('Review.', source_files=['paper.tex'], max_rounds=3)
        lines = runner.state['sources'][0]['content'].splitlines()
        for index in range(8, 13):
            lines[index] = 'Introductory words. ' * 100
        runner.state['sources'][0]['content'] = '\n'.join(lines)
        runner._lines_cache.clear()
        runner.state['read_parts'] = [[i, i] for i in range(1, 16)]
        runner.state['reading'] = {f'L{i}': {'start_line': i, 'end_line': i, 'usable': True, 'complete': True,
                                                   'summary': f'NOTE_{i}_KEEP ' + 'understanding ' * 60}
                                   for i in range(1, 16)}
        runner.agent.ctx = 22000
        before = len(client.requests)
        runner._scope()
        request = client.requests[before]['messages'][1]['content']
        self.assertIn('THE PAPER PART BY PART', request)
        self.assertIn('NOTE_1_KEEP', request)
        self.assertIn('NOTE_15_KEEP', request)
        self.assertFalse(runner.state.get('scope_incomplete'))
