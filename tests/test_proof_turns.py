# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from mathagent.agent import Agent
from mathagent.literature import LiteratureTools
from mathagent.numerics.executor import ExperimentResult
from mathagent.numerics.session import PythonSandbox
from mathagent.proof import ProofRunner, not_a_proof
from mathagent.refcheck import CheckRunner
from mathagent.source_tasks import excludes_external_sources, scoped_literature
from mathagent.tools import Workspace
from test_gui import APPROVAL, PROOF
from test_research import FakeClient, response, tool


class ProofTurnTests(unittest.TestCase):
    def test_sandbox_count_across_solver_and_resumed_verifier(self):
        for configured, expected in [(None, 4), (3, 3)]:
            with self.subTest(configured=configured), TemporaryDirectory() as root:
                settings = Path(root) / 'limits.json'
                settings.write_text(json.dumps({'sanity': {'max_runs': configured}} if configured else {}))
                executor = Mock()
                executor.probe.return_value = {'image': 'sandbox-test'}
                executor.run.return_value = ExperimentResult('ok', exit_code=0, result=2)
                sandbox = PythonSandbox(executor=executor, limits_file=settings)
                chat = sandbox.automatic_session(root)
                self.assertEqual(chat.run_limit, 3)
                observed = []
                chat.on_run_change = observed.append
                runs = [tool('execute_python', code=f'RESULT = {i} + 1') for i in range(4)]
                client = FakeClient([response(calls=runs[:2]), response(PROOF), [KeyboardInterrupt()],
                                     response(calls=runs[2:]), response(calls=[tool('submit_review', **APPROVAL)])])
                agent = Agent(client, Workspace(root, numerics=chat), ctx=16384)
                runner = ProofRunner(agent)
                paused = runner.start('Show x=x.', max_rounds=1, max_tokens=16000, max_predict=900, verify_tokens=900)
                self.assertEqual(paused['status'], 'paused')
                self.assertEqual(runner.state['evidence_sandbox_runs'], 2)
                self.assertEqual(runner.state['settings'].get('sandbox_max_runs'), configured)
                self.assertEqual(chat.run_limit, 3)
                # The new host no longer specifies a cap. Resume respects the saved operator decision.
                fresh = PythonSandbox(executor=executor).automatic_session(root)
                fresh.on_run_change = observed.append
                agent.workspace.numerics = fresh
                runner = ProofRunner(agent)
                out = runner.resume(paused['id'])
                self.assertEqual((out['status'], out['rounds_started']), ('candidate_complete', 1), out['stop_reason'])
                self.assertEqual(executor.run.call_count, expected)
                self.assertEqual(runner.state['evidence_sandbox_runs'], expected)
                self.assertEqual(observed, list(range(1, expected + 1)))
                self.assertEqual(('budget_exhausted' in json.dumps(client.requests[-1])), configured is not None)
                self.assertEqual(fresh.run_limit, 3)
                self.assertEqual(fresh.runs, 0)
                for call in executor.run.call_args_list:
                    self.assertEqual(call.args[1], chat.policy.defaults)

    def test_truncated_surplus_turn_finishes_the_same_role_and_attempt(self):
        read = tool('search_papers', query='example')
        limited_solver = response(complete=False, count=450)
        limited_solver[0]['message']['thinking'] = 'solver-only unfinished calculation'
        limited_review = response('{"schema_version":', complete=False, count=400)
        client = FakeClient([response(calls=[read], count=750), limited_solver, response(PROOF, count=300),
                             response(calls=[read], count=500), limited_review, response(json.dumps(APPROVAL))])
        with TemporaryDirectory() as root:
            agent = Agent(client, Workspace(root), ctx=16384)
            runner = ProofRunner(agent)
            def pause_after_journal(kind, value):
                if kind == 'end' and len(runner.state['calls']) == 2:
                    raise KeyboardInterrupt
            runner.emit = pause_after_journal
            with patch.object(runner.turns.desk, 'execute', return_value='{"results": []}'):
                paused = runner.start('Show x=x.', max_predict=1200, verify_tokens=1200, max_tokens=3600, max_rounds=1)
            self.assertEqual(paused['status'], 'paused')
            self.assertIsNone(paused['initial_candidate'])
            runner = ProofRunner(agent)
            with patch.object(runner.turns.desk, 'execute', return_value='{"results": []}'):
                out = runner.resume(paused['id'])
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            self.assertEqual((out['rounds_started'], len(runner.state['candidates']), len(runner.state['reviews'])), (1, 1, 1))
            self.assertEqual([c['reserved_tokens'] for c in runner.state['calls']], [1200, 450, 1200, 1200, 400, 1200])
            for limited, final in [(1, 2), (4, 5)]:
                self.assertTrue(runner.state['calls'][limited]['evidence_surplus_limited'])
                self.assertNotIn('tools', client.requests[final])
                self.assertIn('-final', runner.state['calls'][final]['key'])
            self.assertIn('solver-only unfinished calculation', json.dumps(client.requests[2]))
            self.assertNotIn('solver-only unfinished calculation', json.dumps(client.requests[3:]))
            self.assertIn('task_status', client.requests[5]['format']['properties'])

    def test_tool_continuations_reserve_normal_final_outputs_and_then_return_the_verdict(self):
        read = tool('search_papers', query='example')
        client = FakeClient([response(calls=[read], count=750), response(calls=[read], count=450),
                             response(PROOF, count=900), response(calls=[read], count=340),
                             response(json.dumps(APPROVAL), count=30)])
        client.remaining_input_tokens = 25000
        with TemporaryDirectory() as root:
            runner = ProofRunner(Agent(client, Workspace(root), ctx=16384))
            with patch.object(runner.turns.desk, 'execute', return_value='{"results": []}'):
                out = runner.start('Prove x=x.', max_predict=1200, verify_tokens=1200, max_tokens=3600, max_rounds=1)
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            first, second, final_solve, review, final_review = client.requests
            self.assertTrue(all(call.get('tools') for call in [first, second, review]))
            self.assertEqual(second['options']['num_predict'], 450)
            self.assertNotIn('tools', final_solve)
            self.assertEqual(final_solve['options']['num_predict'], 1200)
            self.assertNotIn('tools', final_review)
            self.assertIn('task_status', final_review['format']['properties'])
            self.assertEqual(final_review['options']['num_predict'], 1160)
            for call in [second, final_solve, final_review]:
                self.assertNotIn('Remaining in this job:', call['messages'][0]['content'])
                self.assertEqual(call['messages'][-2]['role'], 'tool')
                self.assertEqual(call['messages'][-1]['role'], 'user')
                note = call['messages'][-1]['content']
                self.assertIn('shared output tokens', note)
                self.assertIn('25000 shared input tokens', note)
                self.assertIn('active seconds', note)

    def test_only_assigned_sources_survives_proof_resume_without_locking_other_jobs(self):
        with TemporaryDirectory() as root:
            library = LiteratureTools(root)
            files = []
            for name, content in [('claim.txt', 'Equality is reflexive.'), ('other.txt', 'Private unrelated lemma.')]:
                Path(root, name).write_text(content)
                files.append(library.import_local(name)['document_id'])
            client = FakeClient([response(calls=[tool('read_paper', document_id=files[1])]), [KeyboardInterrupt()],
                                 response(PROOF), response(calls=[tool('submit_review', **APPROVAL)])])
            agent = Agent(client, Workspace(root, literature=library), ctx=16384)
            runner = ProofRunner(agent, evidence_coordinator=True)
            query = 'Use only the attached manuscript. Show x=x.'
            paused = runner.start(query, source_files=[files[0]], max_tokens=9000, max_predict=800, verify_tokens=800)
            self.assertEqual(paused['status'], 'paused')
            resumed = ProofRunner(agent, evidence_coordinator=True)
            result = resumed.resume(paused['id'])
            self.assertEqual(result['status'], 'candidate_complete')
            for request in client.requests:
                names = {s['function']['name'] for s in request['tools']}
                self.assertIn('read_paper', names)
                self.assertFalse(names.intersection({'search_papers', 'open_paper', 'request_evidence'}))
                self.assertNotIn('Private unrelated lemma.', json.dumps(request['messages']))
            self.assertIn('limits evidence to the assigned sources', json.dumps(client.requests[2]['messages']))
            self.assertTrue(library.online)
            self.assertFalse(resumed.turns.desk.literature.online)
            self.assertTrue(excludes_external_sources('Use only pinned material.'))
            self.assertFalse(excludes_external_sources('Explain the phrase “use only pinned material”.'))
            # A subtask inherits the caller's restricted library even when its own goal omits the instruction.
            inherited = scoped_literature(library, query, sources=[{'path': 'claim.txt', 'document_id': files[0]}])
            child = ProofRunner(Agent(FakeClient([]), Workspace(root, literature=inherited)), evidence_coordinator=True)
            child.turns.desk.scope('Verify identity A.', None, None, [])
            self.assertEqual({s['function']['name'] for s in child.turns.available()}, {'search_paper', 'read_paper'})
            self.assertIn('Equality is reflexive.', child.turns.desk.execute('read_paper', {'document_id': files[0]}))
            self.assertIn('limits evidence', child.turns.desk.execute('read_paper', {'document_id': files[1]}))

    def test_native_check_restores_original_source_scope_instead_of_historical_network_switch(self):
        with TemporaryDirectory() as root:
            agent = Agent(FakeClient([]), Workspace(root, literature=LiteratureTools(root)))
            for original, online in [('Use only pinned material.', False), ('Check this reference.', True)]:
                first = CheckRunner(agent)
                with patch.object(first, '_run'):
                    first.start('Check the cited theorem.', reference_context={'query': original, 'files': [], 'messages': []})
                self.assertEqual(first.literature.online, online)
                first.state['settings']['online'] = False
                first.state['literature_snapshot']['online'] = False
                (first.directory/'state.json').write_text(json.dumps(first.state))
                fresh = CheckRunner(agent)
                with patch.object(fresh, '_run'):
                    fresh.resume(first.state['id'])
                fresh._restore_literature()
                self.assertEqual(fresh.literature.online, online)
                self.assertTrue(agent.workspace.literature.online)
                if not online:
                    args = {'query': 'unassigned reference'}
                    self.assertIn('assigned sources', fresh.literature.execute('search_papers', args))

    def test_large_receipts_are_paged_without_disclosing_the_solver_history(self):
        lookup = {'document_id': 'doc1', 'start_line': 1}
        client = FakeClient([response('private solver thought', [tool('read_paper', **lookup)]),
                             response(calls=[tool('read_proof_evidence', id='E1', chars=300)]),
                             response(PROOF), response(calls=[tool('read_proof_evidence', id='E1', chars=300)]),
                             response(calls=[tool('submit_review', **APPROVAL)])])
        with TemporaryDirectory() as root:
            runner = ProofRunner(Agent(client, Workspace(root, literature=LiteratureTools(root)), ctx=8192))
            source = {'document_id': 'doc1', 'text_sha256': 'version', 'citation': 'doc1:L1-L1',
                      'passage': 'Equality is reflexive. ' * 3000}
            with patch.object(runner.turns.desk, 'execute', return_value=json.dumps({**source, 'metadata': 'test'})):
                out = runner.start('Show that a=a for each a.', max_rounds=1, max_tokens=9000,
                                   max_predict=800, verify_tokens=800)
            self.assertEqual(out['status'], 'candidate_complete', out['stop_reason'])
            self.assertEqual((out['rounds_started'], len(client.requests)), (1, 5))
            for role in ('solver', 'verifier'):
                self.assertTrue(runner.state['pending'][role+'_indexed'])
            for request in client.requests[3:]:
                messages = json.dumps(request['messages'])
                self.assertIn('Show that a=a for each a.', messages)
                self.assertIn(PROOF, messages)
                self.assertNotIn('private solver thought', messages)
                self.assertNotIn(source['passage'], messages)
            self.assertIn('Equality is reflexive.', json.dumps(client.requests[-1]['messages']))
            visible = runner.turns.page('verifier', {'id': 'index'})
            self.assertNotIn('read_paper', json.loads(visible)['content'])  # only primary material is available

    def test_assistance_reserves_role_allowances_and_can_still_help_on_a_tight_job(self):
        def request(role, tokens):
            help_call = response(calls=[tool('request_evidence', question='Which result applies?', kind='check')])
            client = FakeClient([help_call] if role == 'solver' else [response(PROOF), help_call])
            client.remaining_seconds = 360 if role == 'solver' else 2000
            with TemporaryDirectory() as root:
                runner = ProofRunner(Agent(client, Workspace(root), ctx=1048576, predict=384000), evidence_coordinator=True)
                out = runner.start('Show x=x.', max_tokens=tokens, max_predict=4096, verify_tokens=4096)
                self.assertEqual(out['status'], 'waiting_for_evidence')
                seconds = out['evidence_request']['remaining_seconds']
                self.assertLessEqual(seconds, min(1800, client.remaining_seconds))
                self.assertGreaterEqual(seconds, min(1800 - out['seconds_used'], client.remaining_seconds))
                return out['evidence_request']['remaining_tokens'], tokens-out['tokens_charged']
        available, left = request('solver', 24000)
        self.assertEqual(left-available, 8192)
        available, left = request('verifier', 24000)
        self.assertEqual(left-available, 4096)
        available, left = request('solver', 2400)
        self.assertTrue(0 < available < left-256)

    def test_program_evidence_matches_the_observation_and_id(self):
        exp = 'exp-20261127-110000-abcdef'
        review = {**APPROVAL, 'explanation': f'{exp} was checked.'}
        record = {'kind': 'experiment', 'status': 'ok', 'experiment_id': exp, 'code': 'print(3)', 'stdout': '3\n'}
        self.assertTrue(not_a_proof(f'The calculation in {exp} establishes the claim.', review, []))
        for output in (3, 4):
            candidate = f'```python\nprint(3)\n```\nThe output is {output}.'
            self.assertEqual(not_a_proof(candidate, review, [record]) is None, output == 3)
