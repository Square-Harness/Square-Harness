# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Exercise source reuse at the Investigate worker boundary, with scripted model and paper contents."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event, RLock
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from mathagent import briefs, cli, investigation_writing
from mathagent.agent import Agent
from mathagent.assistant_budget import AssistantAdmissionRefused, BudgetClient
from mathagent.deepinfra import DeepInfraGenerationError
from mathagent.gui import store
from mathagent.investigation import Investigation, _receipt
from mathagent.ledger import ProofStore
from mathagent.literature import LiteratureError, LiteratureTools
from mathagent.litreview import ReviewRunner
from mathagent.proof import ProofRunner
from mathagent.proof_support import complete_proof_evidence
from mathagent.tools import Workspace
from mathagent.workers import run_worker
from test_research import FakeClient, response, tool


GENERIC = 'For a residual set of parameters, consecutive distinct levels are at least one apart.'
SIMPLE = 'At parameters in class S, every level is simple.'
UNRELATED = 'A theorem from a different conversation with an incompatible family of levels.'
GOAL = 'Determine what the sources imply about gaps between levels in this family.'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ask(question):
    return response(calls=[tool('request_evidence', kind='literature', question=question)])


def shown_characters(packet):
    return sum(len('\n'.join(line.split(': ', 1)[1] for line in row['content'].splitlines()))
               for row in packet['passages'])


class InvestigationPassages(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.chat = store.create_chat(self.root, 'free')
        self.library = LiteratureTools(self.root, online=True)
        # No fixture can silently use a real source provider.
        wire = patch.object(LiteratureTools, '_request_once', side_effect=AssertionError('Unexpected network request'))
        wire.start()
        self.addCleanup(wire.stop)
        self.parent = Investigation.__new__(Investigation)
        self.parent._state_lock = RLock()
        self.parent._save = Mock()
        self.targets = [
            {'id': 't1', 'mode': 'literature', 'after': []},
            {'id': 't2', 'mode': 'literature', 'after': []},
            {'id': 't3', 'mode': 'prove', 'effort': 'high', 'claim': 'target',
             'objective': GOAL, 'after': ['t1']},
        ]
        self.limits = {'tokens': 60000, 'input_tokens': 1000000, 'seconds': 120, 'requests': 50, 'tries': 1}
        self.parent.state = {'id': 'assistant-example', 'actions': 0, 'query': GOAL, 'files': [],
                             'children': [], 'index_view': briefs.freeze_view(self.root, self.chat),
                             'investigation': {'id': 'plan-example', 'chat_id': self.chat['id'], 'phase': 'running',
                                               'target': {'statement': GOAL}, 'plan': {'tasks': self.targets},
                                               'allowances': {'t3': self.limits}, 'tasks': {}}}
        self.generic = self.paper('The gap theorem', GENERIC)
        self.simple = self.paper('Simple levels on S', SIMPLE)
        self.foreign = self.paper('Another family', UNRELATED)
        foreign_chat = store.create_chat(self.root, 'free')
        self.publish(self.chat, self.generic)
        self.publish(self.chat, self.simple)
        self.publish(foreign_chat, self.foreign)
        self.slots = self.parent.inv['tasks']
        self.slots.update(t1={'mode': 'literature', 'status': 'complete', 'job_id': self.generic['job']},
                          t2={'mode': 'literature', 'status': 'running', 'job_id': self.simple['job']},
                          t3={'mode': 'prove', 'status': 'pending'})
        self.approve = response(calls=[tool('submit_review', schema_version=3,
            explanation='The answer preserves the stated scope.', issues=[], notes=[], task_status='proved',
            critical_dependencies=[{'claim': 'The statement concerns distinct levels.', 'provenance': 'standard',
                                    'check': 'The conclusion keeps the same convention.'}])])

    def paper(self, title, passage, *, url=None, tail=('A line outside the recorded reading range.',)):
        doc = 'doc-' + digest(title)[:24]
        text = '\n'.join((title, passage, *tail))
        url = url or 'https://example.org/papers/' + doc
        self.library._write(('documents', doc + '.txt'), text)
        self.library._write(('documents', doc + '.json'), json.dumps(
            {'document_id': doc, 'text_sha256': digest(text), 'source_url': url,
             'extractor': 'text/plain', 'title': title}))
        return {'title': title, 'doc': doc, 'passage': passage, 'url': url}

    def supply_review(self, runner, paper):
        runner.state.update(status='partial', stop_reason='The supplied reading covers one theorem.',
                            references=[{'n': 1, 'title': paper['title'], 'read': 'full_text', 'url': paper['url']}],
                            cards={'1': {'mode': 'full_text', 'passages': {'L2-L2': paper['passage']}}},
                            documents={'1': {'document_id': paper['doc'], 'extractor': 'text/plain'}})
        runner._save()

    def publish(self, chat, paper):
        # Create the predecessor through the native review store; replace its external search with fixed evidence.
        agent = Agent(FakeClient([]), Workspace(self.root, literature=self.library), ctx=32768)
        review = ReviewRunner(agent)
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, paper)):
            review.start_companion('Locate the precise result from ' + paper['title'])
        paper['job'] = review.state['id']
        store.append_items(self.root, chat['id'], [{'role': 'route', 'mode': 'literature',
                           'status': 'done', 'job_id': paper['job']}])

    def assessment(self, paper, *, coverage='sufficient', gap='', locator='L2-L2', quote=None):
        citation = {'document_id': paper['doc'], 'locator': locator}
        if quote is not None:
            citation['quote'] = quote
        return response(json.dumps({'scope': 'specific', 'coverage': coverage,
            'answer': paper['passage'], 'hypotheses': 'The bound applies only on the stated class.',
            'missing': gap, 'citations': [citation]}))

    def prove(self, replies):
        client = FakeClient(replies)
        workspace = Workspace(self.root, literature=self.library)
        self.parent.agent = Agent(client, workspace, ctx=32768, predict=2048)
        budget = BudgetClient(client, concurrency=1, max_tokens=75000, max_seconds=120)
        host = Mock(root=self.root, chat_id=self.chat['id'], online=True, cancel=Event(), args=cli.parser().parse_args([]))
        host.args.proof_solve_tokens = host.args.proof_verify_tokens = 4096
        host.check_files.return_value = []
        host.project_context.return_value = ''
        host.engine.return_value = Agent(client, workspace, ctx=32768, predict=2048)
        child = self.parent._make_child(self.targets[-1], self.slots['t3'])
        result = run_worker(host, self.parent, budget, child['action'], child, self.parent._save)
        self.assertEqual(result['status'], 'candidate_complete', result)
        native = ProofStore.load(self.root, result['job_id'])
        retained = [(e, json.loads(native.read_artifact(e['result']))) for e in native.state['evidence']
                    if e['name'] == 'request_evidence']
        return native, retained, client.requests

    def test_solver_and_verifier_reuse_scoped_readings_including_a_newly_finished_sibling(self):
        unused = self.paper('A neighbouring model', 'A spacing law for the separate example is described.')
        self.publish(self.chat, unused)
        self.targets.insert(-1, {'id': 't4', 'mode': 'literature', 'after': []})
        self.slots['t4'] = {'mode': 'literature', 'status': 'complete', 'job_id': unused['job']}
        def replies():
            yield ask('Which separation estimate holds for neighbouring unequal levels on a generic parameter set?')
            yield self.assessment(self.generic)
            # This sibling was still running when the Investigate child and its solver sources were pinned.
            self.slots['t2']['status'] = 'complete'
            yield response('Only the distinction between unequal levels has a gap bound on the residual set.')
            yield ask('For parameters belonging to S, what does the paper say about multiplicity?')
            yield self.assessment(self.simple)
            yield self.approve
        with patch.object(ReviewRunner, 'start_companion', side_effect=AssertionError('Unexpected new literature job')):
            native, receipts, requests = self.prove(replies())
        self.assertEqual([e['role'] for e, _ in receipts], ['solver', 'verifier'])
        for (_, receipt), paper in zip(receipts, (self.generic, self.simple)):
            self.assertEqual(receipt['result']['status'], 'source_read')
            self.assertEqual([(s['document_id'], s['locator'], s['content']) for s in receipt['primary_sources']],
                             [(paper['doc'], 'L2-L2', paper['passage'])])
        self.assertIn(GENERIC, native.state['sources'][0]['content'])
        self.assertEqual(len(requests), 6)
        for (entry, _), request in zip(receipts, (requests[1], requests[4])):
            charged = json.loads(native.read_artifact(entry['receipt']))
            self.assertEqual((charged['tokens'], charged['measured_tokens'], charged['input_tokens']), (20, 20, 100))
            packet = json.loads(request['messages'][-1]['content'])
            for row in packet['passages']:
                first = int(row['locator'].split('-')[0][1:])
                self.assertTrue(row['content'].startswith(f'L{first}: '))
            delivered_chars = sum(len(s['content']) for s in charged['primary_sources'])
            self.assertEqual(charged['returned_chars'], shown_characters(packet) + delivered_chars)
        self.assertNotIn(SIMPLE, json.dumps(requests[:2]))
        self.assertNotIn(UNRELATED, json.dumps(requests))
        self.assertIn(unused['passage'], json.dumps(requests[1]))
        # The GUI registers the native route in the chat before the plan consumes its completed receipt.
        job = native.state['id']
        store.append_items(self.root, self.chat['id'], [{'role': 'route', 'mode': 'prove', 'status': 'done', 'job_id': job}])
        slot = self.slots['t3']
        _receipt(self.root, self.chat['id'], slot, {'job_id': job}, {'status': native.state['status']})
        finding = investigation_writing.evidence_excerpt(self.root, slot, store, self.chat['id'], complete=True)
        for paper in (self.generic, self.simple):
            self.assertIn(paper['title'], finding)
            self.assertIn(paper['passage'], finding)
        self.assertGreaterEqual(finding.count('L2-L2'), 2)
        for paper in (self.foreign, unused):
            self.assertNotIn(paper['title'], finding)
            self.assertNotIn(paper['passage'], finding)

    def test_stronger_solver_and_verifier_questions_keep_partial_support_and_launch_focused_reviews(self):
        universal = 'Does the distinct-level bound extend from a residual set to every parameter?'
        multiple = 'Does one still have this positive lower bound for the full list counted with multiplicity?'
        gaps = ['Determine the behavior at exceptional parameters.', 'Determine whether levels can be repeated.']
        new_example = self.paper('Exceptional levels', 'At parameter zero two distinct levels are less than one apart.')
        double = self.paper('Double levels', 'The full list at parameter one contains a double level.')
        fresh = iter(zip(gaps, (new_example, double)))
        def retrieve(runner):
            gap, paper = next(fresh)
            # Inspect the actual scoping prompt at the model boundary, without running any remote searches.
            with patch.object(runner, '_ask', side_effect=InterruptedError('Captured scope')) as ask_scope:
                with self.assertRaisesRegex(InterruptedError, 'Captured scope'):
                    runner._scope()
            scope_prompt = ask_scope.call_args.args[1]
            self.assertIn(gap, scope_prompt)
            self.assertIn(GENERIC, scope_prompt)
            self.assertEqual(runner._public_queries([gap, GENERIC, 'spectral gap']),
                             ['spectral gap'])
            for query in (GENERIC, self.generic['doc']):
                url = 'https://api.openalex.org/works?' + urlencode({'search': query})
                with self.assertRaises(LiteratureError):
                    runner.literature.outbound_guard(url)
            runner.literature.outbound_guard('https://api.openalex.org/works?search=spectral+gap')
            self.supply_review(runner, paper)
        replies = [ask(universal), self.assessment(self.generic, coverage='partial', gap=gaps[0]),
                   response('The generic lower bound need not extend to every parameter.'),
                   ask(multiple), self.assessment(self.generic, coverage='partial', gap=gaps[1]), self.approve]
        with patch.object(ReviewRunner, '_run', retrieve):
            native, receipts, requests = self.prove(replies)
        for (entry, receipt), question, gap, paper, call in zip(
                receipts, (universal, multiple), gaps, (new_example, double), (requests[1], requests[4])):
            result = receipt['result']
            self.assertEqual(result['passage_assessment']['coverage'], 'partial')
            self.assertEqual(result['kind'], 'literature')
            self.assertEqual(result['job_id'], entry['job_link'][1])
            nested = json.loads((self.root / '.mathagent' / 'research' / result['job_id'] / 'state.json').read_text())
            self.assertEqual(nested['goal'], question)
            private = nested['prior_passage_context']
            self.assertEqual(private['missing'], gap)
            self.assertEqual([(s['document_id'], s['locator'], s['source_url'], s['content'])
                              for s in private['passages']], [(self.generic['doc'], 'L2-L2', self.generic['url'], GENERIC)])
            self.assertEqual({s['content'] for s in receipt['primary_sources']}, {GENERIC, paper['passage']})
            judged = json.loads(call['messages'][-1]['content'])
            self.assertEqual(judged['question'], question)
            charge = json.loads(native.read_artifact(entry['receipt']))['returned_chars']
            self.assertEqual(charge, shown_characters(judged) + 2 * len(GENERIC) + nested['literature_snapshot']['stats']['returned_chars'])
        self.assertNotIn(UNRELATED, json.dumps(requests))

    def test_fabricated_locator_and_foreign_source_cannot_close_either_request(self):
        fresh = self.paper('Additional source', 'This additional construction resolves only the exceptional example.')
        replies = [ask('What lower bound is stated for generic parameters?'), self.assessment(self.generic, locator='L7-L8'),
                   response('The report describes a restricted example.'),
                   ask('Which inequality for the residual parameters does the source actually establish?'),
                   self.assessment(self.foreign), self.approve]
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, fresh)):
            _, receipts, _ = self.prove(replies)
        for _, receipt in receipts:
            result = receipt['result']
            self.assertEqual(result['kind'], 'literature')
            self.assertEqual(result['passage_assessment']['citations'], [])
            self.assertNotEqual(result['passage_assessment']['coverage'], 'sufficient')
            self.assertEqual([s['content'] for s in receipt['primary_sources']], [fresh['passage']])

    def test_priority_and_an_invented_quote_require_search_even_if_the_assessor_says_sufficient(self):
        fresh = self.paper('History of the family', 'The present construction predates the survey.')
        replies = [ask('Who first proved this assertion about distinct levels?'), self.assessment(self.generic),
                   response('The historical claim needs a source.'),
                   ask('Which lower bound for distinct levels is printed in the theorem?'),
                   self.assessment(self.generic, quote='A lower bound holds for every parameter.'), self.approve]
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, fresh)):
            _, receipts, _ = self.prove(replies)
        self.assertEqual([receipt['result']['kind'] for _, receipt in receipts], ['literature', 'literature'])
        self.assertNotEqual(receipts[0][1]['result']['passage_assessment']['coverage'], 'sufficient')
        self.assertEqual(receipts[1][1]['result']['passage_assessment']['citations'], [])

    def test_requested_article_version_and_real_lines_in_a_larger_displayed_passage(self):
        v1 = self.paper('First edition', GENERIC, url='https://arxiv.org/pdf/2101.23456v1')
        v2 = self.paper('Second edition', 'The revised paper only proves the bound for parameters in S.',
                        url='https://arxiv.org/pdf/2101.23456v2')
        # In early caches the recorded canonical ID and version could come from the requested
        # URL even after the download redirected to an unversioned paper.
        legacy = self.paper('Redirected extraction', GENERIC, url='https://arxiv.org/html/2101.23456')
        metadata, _ = self.library._document(legacy['doc'])
        metadata.update(canonical_identifier='arxiv:2101.23456v2', version='v2')
        self.library._write(('documents', legacy['doc'] + '.json'), json.dumps(metadata))
        self.publish(self.chat, legacy)
        self.publish(self.chat, v1)
        self.slots['t1']['job_id'] = legacy['job']
        self.slots['t2'].update(status='complete', job_id=v1['job'])
        replies = [ask('What bound on distinct levels does arXiv:2101.23456v2 prove?'), self.assessment(legacy),
                   response('The revised statement keeps its parameter restriction.'),
                   ask('Which generic bound on distinct levels appears in arXiv:2101.23456v1?'),
                   self.assessment(v1, locator='L1-L2'), self.approve]
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, v2)):
            _, receipts, requests = self.prove(replies)
        first, second = (receipt for _, receipt in receipts)
        self.assertEqual(first['result']['kind'], 'literature')
        self.assertNotEqual(first['result']['passage_assessment']['coverage'], 'sufficient')
        self.assertIn(v2['doc'], {s['document_id'] for s in first['primary_sources']})
        self.assertEqual(second['result']['status'], 'source_read')
        exact, = second['primary_sources']
        self.assertEqual((exact['document_id'], exact['locator'], exact['content']),
                         (v1['doc'], 'L1-L2', v1['title'] + '\n' + GENERIC))
        offered = json.loads(requests[4]['messages'][-1]['content'])['passages']
        self.assertIn((v1['doc'], 'L1-L3'), {(row['document_id'], row['locator']) for row in offered})
        self.assertNotIn((v1['doc'], 'L1-L2'), {(row['document_id'], row['locator']) for row in offered})

        from mathagent.passage_assessment import _named_sources_match
        versioned = 'Which claim does arXiv:2101.23456v2 prove?'
        for final, eligible in (
            ('https://arxiv.org/html/2101.23456', False),
            ('https://arxiv.org/html/2101.23456v1', False),
            ('https://arxiv.org/abs/2101.23456v2', False),
            ('https://wrong.example/html/2101.23456v2', False),
            ('https://arxiv.org/html/2101.23456?redirect=2101.23456v2', False),
            ('https://arxiv.org/html/2101.23456v2', True),
            ('https://export.arxiv.org/pdf/2101.23456v2.pdf', True),
        ):
            with self.subTest(final=final):
                row = {**metadata, 'source_url': final}
                self.assertEqual(_named_sources_match(versioned, [row]), eligible)
                self.assertTrue(_named_sources_match('What does arXiv:2101.23456 prove?', [row]))

    def test_verifier_finds_an_unread_theorem_in_the_extraction_pinned_by_the_solvers_own_read(self):
        theorem = 'Two coalescing eigenvalues differ quadratically in the perturbation.'
        paper = self.paper('Perturbation of a double eigenvalue', 'We introduce the family of operators.',
                           tail=('Section opening.',) * 7 + (theorem,))
        self.targets[-1]['objective'] += ' The public reference under discussion has document ID ' + paper['doc'] + '.'
        read = response(calls=[tool('read_paper', document_id=paper['doc'], start_line=2, end_line=2)])
        replies = [read, response('One must determine the separation order near coalescence.'),
                   ask('How rapidly do the two coalescing eigenvalues separate in the perturbation?'),
                   self.assessment({**paper, 'passage': theorem}, locator='L10-L10'), self.approve]
        with patch.object(ReviewRunner, 'start_companion', side_effect=AssertionError('Unexpected new survey')):
            native, receipts, requests = self.prove(replies)
        self.assertNotIn(paper['doc'], {s['document_id'] for s in native.state['sources'] if 'document_id' in s})
        reading = next(e for e in native.state['evidence'] if e['name'] == 'read_paper')
        first, = json.loads(native.read_artifact(reading['primary']))
        self.assertEqual((reading['role'], first['citation']), ('solver', paper['doc'] + ':L2-L2'))
        self.assertNotIn(theorem, first['passage'])
        self.assertNotIn(theorem, json.dumps(requests[:3]))
        (request, receipt), = receipts
        self.assertEqual(request['role'], 'verifier')
        found, = receipt['primary_sources']
        self.assertEqual((found['document_id'], found['locator'], found['content']), (paper['doc'], 'L10-L10', theorem))

    def test_legacy_pending_proof_uses_its_original_exact_source_without_an_assessment(self):
        question = 'Locate the precise result from ' + self.generic['title']
        client = FakeClient([ask(question), response('This lower bound concerns distinct levels.'), self.approve])
        agent = Agent(client, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        pending = ProofRunner(agent, evidence_coordinator=True).start(
            GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
        self.assertEqual(pending['status'], 'waiting_for_evidence')
        restored = ProofRunner(agent, evidence_coordinator=True)
        view = store.index_view(self.root, chat=self.chat['id'])
        with patch.object(ReviewRunner, 'start_companion', side_effect=AssertionError('Unexpected new literature job')):
            result = complete_proof_evidence(restored, restored.resume(pending['id']), view=view)
        self.assertEqual(result['status'], 'candidate_complete')
        entry = restored.state['evidence'][0]
        receipt = json.loads(restored.store.read_artifact(entry['result']))
        self.assertEqual(receipt['result']['status'], 'source_read')
        self.assertNotIn('passage_assessment', receipt['result'])
        self.assertEqual(len(client.requests), 3)

    def test_resumed_assessment_rechecks_the_cached_extraction_before_publishing_its_receipt(self):
        question = 'Which bound holds for distinct levels for generic parameters?'
        client = FakeClient([ask(question), self.assessment(self.generic),
                             response('Only the newly read example remains supported.'), self.approve])
        budget = BudgetClient(client, concurrency=1, max_tokens=60000, max_seconds=120)
        agent = Agent(budget, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        first = ProofRunner(agent, evidence_coordinator=True)
        pending = first.start(GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
        view = store.index_view(self.root, chat=self.chat['id'])
        initial = {'entries': [e for e in view['entries'] if e['job_id'] == self.generic['job']]}
        with patch.object(first, 'resume', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            complete_proof_evidence(first, pending, investigation_view=initial)
        self.assertEqual(len(client.requests), 2)
        self.library._write(('documents', self.generic['doc'] + '.txt'), 'A different source extraction.')
        fresh = self.paper('Rechecked estimate', 'The replacement passage covers a restricted example.')
        restored = ProofRunner(agent, evidence_coordinator=True)
        later_view = Mock(return_value=view)
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, fresh)):
            result = complete_proof_evidence(restored, restored.resume(pending['id']), investigation_view=later_view)
        later_view.assert_not_called()
        self.assertEqual(result['status'], 'candidate_complete')
        entry = next(e for e in restored.state['evidence'] if e['name'] == 'request_evidence')
        receipt = json.loads(restored.store.read_artifact(entry['receipt']))
        self.assertEqual(receipt['result']['passage_assessment']['citations'], [])
        self.assertEqual([s['content'] for s in receipt['primary_sources']], [fresh['passage']])
        self.assertEqual((len(client.requests), receipt['tokens'], receipt['input_tokens']), (4, 20, 100))

    def test_empty_request_snapshot_survives_interruption_before_native_review_creation(self):
        question = 'Which bound holds for generic parameters?'
        client = FakeClient([ask(question), response('Only the subsequently retrieved example is available.'), self.approve])
        agent = Agent(client, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        first = ProofRunner(agent, evidence_coordinator=True)
        pending = first.start(GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
        with patch.object(ReviewRunner, 'start_companion', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            complete_proof_evidence(first, pending, investigation_view={'entries': []})
        self.assertEqual(len(client.requests), 1)
        late = Mock(return_value=store.index_view(self.root, chat=self.chat['id']))
        fresh = self.paper('New counterexample', 'At parameter zero the first two distinct levels are unusually close.')
        restored = ProofRunner(agent, evidence_coordinator=True)
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, fresh)):
            result = complete_proof_evidence(restored, restored.resume(pending['id']), investigation_view=late)
        self.assertEqual(result['status'], 'candidate_complete')
        late.assert_not_called()
        entry = next(e for e in restored.state['evidence'] if e['name'] == 'request_evidence')
        receipt = json.loads(restored.store.read_artifact(entry['receipt']))
        self.assertEqual([s['document_id'] for s in receipt['primary_sources']], [fresh['doc']])
        self.assertEqual(len(client.requests), 3)

    def test_provider_interruption_resumes_the_same_assessment_and_charges_actual_reported_usage(self):
        question = 'Which separation theorem applies to consecutive distinct levels at generic parameters?'
        interruption = DeepInfraGenerationError('Interrupted after seven tokens', category='interrupted',
            stats={'eval_count': 7, 'prompt_eval_count': 31})
        client = FakeClient([ask(question), [interruption], self.assessment(self.generic),
                             response('The bound is stated on the residual set.'), self.approve])
        budget = BudgetClient(client, concurrency=1, max_tokens=60000, max_seconds=120)
        agent = Agent(budget, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        runner = ProofRunner(agent, evidence_coordinator=True)
        pending = runner.start(GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
        indexed = store.index_view(self.root, chat=self.chat['id'])
        view = {'entries': [e for e in indexed['entries'] if e['job_id'] == self.generic['job']]}
        with patch.object(ReviewRunner, 'start_companion', side_effect=AssertionError('Unexpected survey')):
            pause = complete_proof_evidence(runner, pending, investigation_view=view)
            self.assertEqual(pause['status'], 'paused')
            self.assertEqual(len(client.requests), 2)
            restored = ProofRunner(agent, evidence_coordinator=True)
            late = Mock(return_value=indexed)
            result = complete_proof_evidence(restored, restored.resume(pending['id']), investigation_view=late)
        late.assert_not_called()
        self.assertEqual(client.requests[1]['messages'], client.requests[2]['messages'])
        self.assertEqual(result['status'], 'candidate_complete')
        entry = next(e for e in restored.state['evidence'] if e['name'] == 'request_evidence')
        receipt = json.loads(restored.store.read_artifact(entry['receipt']))
        self.assertEqual((receipt['tokens'], receipt['measured_tokens'], receipt['input_tokens']), (27, 27, 131))

    def test_fixed_admission_refusal_returns_a_durable_receipt_and_the_proof_continues(self):
        question = 'Which theorem bounds the gap between distinct levels at generic parameters?'
        interruption = DeepInfraGenerationError('Stream interrupted', category='interrupted',
            stats={'eval_count': 7, 'prompt_eval_count': 31})
        client = FakeClient([ask(question), [interruption],
                             response('I retain the distinction and leave the literature question unresolved.'), self.approve])
        budget = BudgetClient(client, concurrency=1, max_tokens=60000, max_seconds=120)
        agent = Agent(budget, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        runner = ProofRunner(agent, evidence_coordinator=True)
        pending = runner.start(GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
        view = store.index_view(self.root, chat=self.chat['id'])
        reason = 'Worker input budget exhausted; no mathematical context was clipped.'
        with patch.object(ReviewRunner, 'start_companion', side_effect=AssertionError('Unexpected survey')):
            self.assertEqual(complete_proof_evidence(runner, pending, investigation_view=view)['status'], 'paused')
            restored = ProofRunner(agent, evidence_coordinator=True)
            waiting = restored.resume(pending['id'])
            with patch.object(budget, 'inference_slot', side_effect=AssistantAdmissionRefused(reason)):
                from mathagent.proof_support import _evidence
                receipt = _evidence(restored, waiting['evidence_request'], None, investigation_view=view)
            self.assertEqual(receipt['result']['status'], 'unavailable')
            self.assertEqual(receipt['result']['reason'], reason)
            self.assertEqual((receipt['tokens'], receipt['measured_tokens'], receipt['input_tokens']), (7, 7, 31))
            self.assertEqual(len(client.requests), 2)
            # Repeat after the native receipt has been produced but has not yet been accepted.
            late = Mock(side_effect=AssertionError('Unexpected re-evaluation'))
            result = complete_proof_evidence(restored, waiting, investigation_view=late)
        self.assertEqual(result['status'], 'candidate_complete')
        late.assert_not_called()
        self.assertEqual(len(client.requests), 4)
        entry = next(e for e in restored.state['evidence'] if e['name'] == 'request_evidence')
        self.assertEqual(json.loads(restored.store.read_artifact(entry['receipt'])), receipt)

    def test_local_reading_obeys_both_original_and_current_proof_character_limits(self):
        question = 'Which gap bound applies to distinct levels on generic parameters?'
        # The primary line fits; a second, surrounding window would exceed the smaller allowance.
        narrow, wide = 2 * len(GENERIC), 10000
        frozen = store.index_view(self.root, chat=self.chat['id'])
        view = {'entries': [e for e in frozen['entries'] if e['job_id'] == self.generic['job']]}
        for original, current in ((narrow, wide), (wide, narrow)):
            with self.subTest(original=original, current=current):
                library = LiteratureTools(self.root, online=True)
                library.reset_budget(max_requests=10, max_chars=original)
                client = FakeClient([ask(question), self.assessment(self.generic),
                    response('The generic bound applies only to distinct levels.'), self.approve])
                agent = Agent(client, Workspace(self.root, literature=library), ctx=32768, predict=2048)
                runner = ProofRunner(agent, evidence_coordinator=True)
                pending = runner.start(GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
                self.assertEqual(pending['evidence_request']['remaining_chars'], original)
                runner.turns.desk.literature.max_chars = current
                with patch.object(ReviewRunner, 'start_companion', side_effect=AssertionError('Unexpected native search')):
                    result = complete_proof_evidence(runner, pending, investigation_view=view)
                self.assertEqual(result['status'], 'candidate_complete')
                entry = next(e for e in runner.state['evidence'] if e['name'] == 'request_evidence')
                receipt = json.loads(runner.store.read_artifact(entry['receipt']))
                self.assertEqual(receipt['returned_chars'], narrow)
                self.assertEqual(runner.state['evidence_literature']['stats']['returned_chars'], narrow)

    def test_upgrade_with_an_existing_native_literature_job_finishes_that_same_job(self):
        question = 'Which published argument establishes an upper bound on the exceptional parameters?'
        client = FakeClient([ask(question), response('The source describes an exceptional parameter.'), self.approve])
        agent = Agent(client, Workspace(self.root, literature=self.library), ctx=32768, predict=2048)
        first = ProofRunner(agent, evidence_coordinator=True)
        pending = first.start(GOAL, max_predict=4096, verify_tokens=4096, max_rounds=1, max_tokens=60000)
        fresh = self.paper('New bound', 'The exceptional parameter violates the bound on distinct levels.')
        research = self.root / '.mathagent' / 'research'
        initial = set(research.iterdir())
        with patch.object(ReviewRunner, '_run', lambda runner: self.supply_review(runner, fresh)), \
                patch.object(first, 'resume', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            complete_proof_evidence(first, pending)
        created, = set(research.iterdir()) - initial
        restored = ProofRunner(agent, evidence_coordinator=True)
        result = complete_proof_evidence(restored, restored.resume(pending['id']),
                                         investigation_view=store.index_view(self.root, chat=self.chat['id']))
        self.assertEqual(result['status'], 'candidate_complete')
        entry = next(e for e in restored.state['evidence'] if e['name'] == 'request_evidence')
        receipt = json.loads(restored.store.read_artifact(entry['result']))
        self.assertEqual(receipt['result']['job_id'], created.name)
        self.assertNotIn('passage_assessment', receipt['result'])
        self.assertEqual(set(research.iterdir()), initial | {created})
        self.assertEqual(len(client.requests), 3)
