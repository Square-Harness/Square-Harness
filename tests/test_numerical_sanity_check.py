"""numerical-sanity-check: discovery, automatic routing, budgets, and the worked mathematics."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest

from mathagent import cli, skill_catalog
from mathagent.agent import Agent, AgentError
from mathagent.gui.hub import Hub
from mathagent.numerics.policy import LAB, SANITY, SANITY_DEFAULTS
from mathagent.numerics.session import MIN_AUTOMATIC_CONTEXT, SANITY_NOTE, PythonSandbox
from mathagent.orchestrator import Orchestrator
from mathagent.tools import Workspace

from numerics_support import (REAL_SKIP_REASON, ScriptedClient, calls, fake_sandbox, final, python_blocks,
                              real_sandbox, tool_call)
from test_gui import GuiCase, text, tool
from test_python_lab import run_cli, tool_names

SKILL_DIR = Path(skill_catalog.__file__).parent / 'skills' / 'numerical-sanity-check'


def check_call(code='RESULT = 1', call_id='call_1', **extra):
    return tool_call('execute_python', {'code': code, 'purpose': 'test the claim', **extra}, call_id)


class DiscoveryTests(unittest.TestCase):
    def test_both_skills_are_discoverable_with_their_invocation_modes(self):
        found = {skill.name: skill for skill in skill_catalog.catalog()}
        self.assertEqual(found['numerical-sanity-check'].invocation, 'automatic')
        self.assertEqual(found['python-lab'].invocation, 'explicit')
        sanity = found['numerical-sanity-check']
        self.assertIn('Proactively test mathematical conjectures', sanity.description)
        self.assertEqual(sanity.references, ('examples', 'strategies'))
        core = sanity.section('Core rules')
        for phrase in ('exact counterexample', 'numerically verified candidate', 'no counterexample detected',
                       'inconclusive', 'never prove', 'Skip it when an exact argument is immediate'):
            self.assertIn(phrase, core)

    def test_frontmatter_parser(self):
        meta, body = skill_catalog.parse_frontmatter('---\nname: x\ndescription: >\n  one\n  two\n'
                                                     'metadata:\n  invocation: explicit  # note\n---\n\n# Body\n')
        self.assertEqual(meta, {'name': 'x', 'description': 'one two', 'metadata': {'invocation': 'explicit'}})
        self.assertEqual(body, '# Body\n')
        with self.assertRaises(ValueError):
            skill_catalog.load('../skills')

    def test_report_skills_without_frontmatter_are_unaffected(self):
        names = [skill.name for skill in skill_catalog.catalog()]
        self.assertEqual(names, ['numerical-sanity-check', 'python-lab'])


class AutomaticRoutingTests(unittest.TestCase):
    """Who gets which binding: decided by controller code, never by the model."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def hub(self, *extra):
        args = cli.parser().parse_args(['--workspace', str(self.root), '--model', 'test-model', '--ctx', '40960',
                                        '--host', 'http://127.0.0.1:9',
                                        *extra])
        args.workspace = self.root
        hub = Hub(args)
        self.addCleanup(hub.shutdown, 2)
        hub.sandbox, self.executor = fake_sandbox(automatic=not args.no_numerical_checks)
        return hub

    def engine(self, hub, mode, ctx=None):
        task = types.SimpleNamespace(cancel=threading.Event(), id='task-1')
        agent, _ = hub._engine(task, mode=mode, ctx=ctx)
        return agent.workspace.numerics

    def test_interface_binds_sanity_checks_only_to_conversational_engines(self):
        hub = self.hub()
        for mode in ('critic', 'explore'):        # chats, the Assistant's main loop, critic/explore workers
            session = self.engine(hub, mode)
            self.assertEqual((session.policy.name, session.explicit), (SANITY, False), mode)
        for mode in ('prove', 'literature', 'referee', 'writeup', 'check'):
            self.assertIsNone(self.engine(hub, mode), mode)
        self.assertIsNone(self.engine(hub, 'critic', ctx=MIN_AUTOMATIC_CONTEXT - 1))

    def test_interface_option_disables_automatic_checks(self):
        self.assertIsNone(self.engine(self.hub('--no-numerical-checks'), 'critic'))

    def test_terminal_chat_runs_at_most_three_checks_per_turn(self):
        sandbox, executor = fake_sandbox()
        responses = [calls(*(check_call(call_id=f'c{i}') for i in range(4))), final('No counterexample found.'),
                     calls(check_call(call_id='c9')), final('Second turn.')]
        code, client, _ = run_cli(self.root, 'Is x^4-3x^2+1 >= 0 for all real x?', responses[:2], sandbox,
                                  '--ctx', '40960')
        self.assertEqual(code, 0)
        self.assertEqual(len(executor.calls), 3)
        results = [json.loads(m['content']) for m in client.requests[1]['messages'] if m.get('role') == 'tool']
        self.assertEqual([r['status'] for r in results], ['ok', 'ok', 'ok', 'budget_exhausted'])
        self.assertEqual(executor.calls[0]['limits'], SANITY_DEFAULTS)
        self.assertIn(SANITY_NOTE, json.dumps(results[0]))
        # A new turn is a new check: the CLI binds a fresh session.
        code, client, _ = run_cli(self.root, 'Another claim?', responses[2:], sandbox, '--ctx', '40960')
        self.assertEqual(len(executor.calls), 4)

    def test_terminal_options_and_context_gate(self):
        sandbox, executor = fake_sandbox(automatic=False)
        _, client, _ = run_cli(self.root, 'Claim?', [final('ok')], sandbox, '--ctx', '40960', '--no-numerical-checks')
        self.assertNotIn('execute_python', tool_names(client.requests[0]))
        sandbox, executor = fake_sandbox()
        _, client, ui = run_cli(self.root, 'Claim?', [final('ok')], sandbox, '--ctx', '16384')
        self.assertNotIn('execute_python', tool_names(client.requests[0]))
        self.assertIn('--ctx 32768', ' '.join(str(c.args[0]) for c in ui.say.call_args_list))

    def test_explicit_sanity_check_command_uses_the_strict_policy(self):
        sandbox, executor = fake_sandbox(automatic=False)
        code, client, _ = run_cli(self.root, '/sanity-check Is AB+BA PSD for SPD A, B?',
                                  [calls(check_call(timeout_seconds=60)), calls(check_call(call_id='c2')),
                                   final('Counterexample in dimension 2.')], sandbox, '--ctx', '16384')
        self.assertEqual(code, 0)
        refused = json.loads(next(m['content'] for m in client.requests[1]['messages'] if m.get('role') == 'tool'))
        self.assertEqual(refused['status'], 'refused')
        self.assertEqual(executor.calls[0]['limits'], SANITY_DEFAULTS)
        [record] = PythonSandbox(executor=executor).bind(SANITY, self.root, explicit=False).store.list()
        saved = json.loads((self.root / '.mathagent' / 'experiments' / record['id'] / 'record.json').read_text())
        self.assertEqual((saved['skill'], saved['invocation']), (SANITY, 'explicit'))


class AssistantTests(unittest.TestCase):
    """The main assistant (orchestrator) gets the automatic binding and persists its runs."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sandbox, self.executor = fake_sandbox()

    def assistant(self, responses, explicit=False):
        session = self.sandbox.bind(LAB if explicit else SANITY, self.root, explicit=explicit)
        client = ScriptedClient(responses)
        agent = Agent(client, Workspace(self.root, numerics=session), ctx=40960, predict=4096, think=False)
        states = []
        runner = Orchestrator(agent, lambda *a: {'status': 'complete'}, checkpoint=states.append)
        return runner, client, states

    def test_main_agent_is_encouraged_and_can_run_a_check(self):
        runner, client, _ = self.assistant([calls(check_call()), final('f(sqrt(3/2)) = -5/4 < 0.')])
        result = runner.run('Is x^4 - 3x^2 + 1 >= 0 for all real x?')
        self.assertEqual(result['status'], 'complete')
        first = client.requests[0]
        self.assertIn('execute_python', tool_names(first))
        self.assertIn('cheap falsification', first['messages'][0]['content'])
        self.assertEqual(len(self.executor.calls), 1)
        tool_message = json.loads(next(m['content'] for m in client.requests[1]['messages'] if m.get('role') == 'tool'))
        self.assertEqual(tool_message['status'], 'complete')
        self.assertEqual(tool_message['sandbox_result']['status'], 'ok')

    def test_resumed_assistant_never_reruns_completed_code(self):
        runner, client, states = self.assistant([calls(check_call()), RuntimeError('model server lost')])
        with self.assertRaises(RuntimeError):
            runner.run('Is the claim true?')
        self.assertEqual(len(self.executor.calls), 1)
        saved = copy.deepcopy(states[-1])
        resumed, client, _ = self.assistant([final('Answer from the saved experiment.')])
        result = resumed.run('Is the claim true?', state=saved)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.executor.calls), 1)

    def test_assistant_refuses_a_python_lab_binding(self):
        runner, client, _ = self.assistant([final('unused')], explicit=True)
        with self.assertRaisesRegex(AgentError, 'automatic sandbox binding'):
            runner.run('Run heavy code.')
        self.assertEqual(client.requests, [])


class InterfaceEndToEndTests(GuiCase):
    """A real interface session: the Assistant runs a check through the fake sandbox."""
    extra_args = ('--ctx', '40960')

    def setUp(self):
        super().setUp()
        self.hub.sandbox, self.executor = fake_sandbox()

    def test_assistant_conversation_runs_an_automatic_check(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [tool('execute_python', {'code': 'RESULT = min(x**4 - 3*x**2 + 1 for x in [1.2247])',
                                                     'purpose': 'look for a negative value'}),
                             text('The claim is false: f(sqrt(3/2)) = -5/4.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'Is x^4-3x^2+1 >= 0 on R?'})
        self.wait_for(lambda: any(item.get('content') == 'The claim is false: f(sqrt(3/2)) = -5/4.'
                                  for item in self.ok('GET', f'/api/chats/{chat}')['transcript']),
                      timeout=20, message='the answer')
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(self.executor.calls[0]['limits'], SANITY_DEFAULTS)
        self.assertIn('execute_python', tool_names(self.fake.requests[0]))
        self.assertTrue(any(p.name == 'record.json' for p in (self.root / '.mathagent' / 'experiments').rglob('*')))


@unittest.skipUnless(real_sandbox(), REAL_SKIP_REASON)
class WorkedMathematicsTests(unittest.TestCase):
    """The reference examples, executed under the automatic policy, report what they should."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.blocks = python_blocks(SKILL_DIR / 'references' / 'examples.md')
        cls.text = (SKILL_DIR / 'references' / 'examples.md').read_text()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, index):
        session = PythonSandbox(executor=real_sandbox()).bind(SANITY, self.temp.name, explicit=False)
        value = json.loads(session.execute('execute_python', {'code': self.blocks[index], 'seed': 0}))
        self.assertEqual(value['status'], 'ok', value)
        self.assertIn('never prove', value['note'])
        return value['result']

    def test_a_false_scalar_inequality_has_an_exact_counterexample(self):
        result = self.check(0)
        self.assertEqual(result['classification'], 'exact counterexample')
        self.assertEqual(result['exact_value'], '-5/4')
        self.assertIn('sqrt(6)/2', result['exact_point'])
        self.assertAlmostEqual(result['numerical_min'], -1.25, places=9)

    def test_b_matrix_claim_fails_in_dimension_two(self):
        result = self.check(1)
        self.assertEqual(result['classification'], 'exact counterexample')
        self.assertTrue(result['A_spd'] and result['B_spd'])
        self.assertEqual(result['det'], -9401)
        self.assertIn('201 - sqrt(49802)', result['exact_eigenvalues'])
        self.assertLess(result['random_search_min_eig'], 0)

    def test_c_first_failing_dimension(self):
        result = self.check(2)
        self.assertEqual((result['first_failing_n'], result['classification']), (6, 'exact counterexample'))
        self.assertEqual((result['V_prev'], result['V_n']), ('8*pi**2/15', 'pi**3/6'))

    def test_d_roundoff_is_not_reported_as_a_violation(self):
        result = self.check(3)
        self.assertTrue(any(result['float64_negative']), 'float64 should show spurious negatives')
        self.assertTrue(result['mp50_positive'])
        self.assertEqual(result['classification'], 'no counterexample detected')

    def test_e_spectral_convergence_is_suggested_not_proved(self):
        result = self.check(4)
        for order in result['observed_orders']:
            self.assertAlmostEqual(order, 2.0, places=2)
        self.assertLess(max(result['residuals']), 1e-9)
        self.assertEqual(result['classification'], 'no counterexample detected')
        self.assertIn('**suggests**', self.text)

    def test_f_trivial_identity_needs_no_execution(self):
        section = self.text.split('## F.')[1].split('## G.')[0]
        self.assertNotIn('```python', section)
        self.assertIn('Do **not** run anything', section)
        self.assertEqual(len(self.blocks), 6)  # A–E and G only

    def test_g_valid_inequality_is_not_reported_as_proved(self):
        result = self.check(5)
        self.assertEqual(result['classification'], 'no counterexample detected')
        self.assertGreaterEqual(result['min_scaled_margin'], -1e-12)
        self.assertGreaterEqual(result['min_sample_margin_relative'], 0)
        self.assertIn('not a\nproof', self.text.split('## G.')[1])

    def test_strategy_scripts_fit_the_automatic_budget(self):
        for index, code in enumerate(python_blocks(SKILL_DIR / 'references' / 'strategies.md'), 1):
            with self.subTest(strategy=index):
                result = real_sandbox().run(code, SANITY_DEFAULTS, seed=0)
                self.assertEqual(result.status, 'ok', result.as_dict())
                self.assertLess(result.elapsed_seconds, SANITY_DEFAULTS.timeout_seconds)


if __name__ == '__main__':
    unittest.main()


# -- figures in the interface ------------------------------------------------------

from mathagent.gui import store as gui_store
from mathagent.numerics.executor import Artifact, ExperimentResult
from test_python_executor import png


def figure_run(status='ok'):
    figure = Artifact('figure-1.png', 'figure', png(4, 3), {'axes': [{'title': 'Brownian paths'}]}, 4, 3)
    return ExperimentResult(status, elapsed_seconds=0.2, wall_seconds=0.9, exit_code=0, stdout='mean 0.01\n',
                            artifacts=[figure])


class FigureTranscriptTests(unittest.TestCase):
    def test_saved_turn_places_figures_after_the_tool_block(self):
        figure = {'path': '.mathagent/experiments/exp-20261101-101010-abcdef/figure-1.png', 'width': 4, 'height': 3}
        result = json.dumps({'experiment_id': 'exp-20261101-101010-abcdef', 'status': 'ok', 'figures': [figure]})
        with tempfile.TemporaryDirectory() as root:
            chat = gui_store.create_chat(root, 'critic')
            history = [{'role': 'user', 'content': 'Simulate Brownian motion.'},
                       {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'execute_python'}},
                                                                            {'function': {'name': 'read_experiment'}}]},
                       {'role': 'tool', 'tool_name': 'execute_python', 'content': result},
                       {'role': 'tool', 'tool_name': 'read_experiment', 'content': '{}'},
                       {'role': 'assistant', 'content': 'The paths are shown above.'}]
            gui_store.commit_turn(root, chat['id'], history, [{'thinking': '', 'stats': {}}] * 2, [])
            roles = [item['role'] for item in gui_store.load_chat(root, chat['id'])['transcript']]
        self.assertEqual(roles, ['user', 'assistant', 'tool', 'tool', 'experiment', 'assistant'])

    def test_experiment_items_accept_only_valid_ids_and_png_names(self):
        self.assertIsNone(gui_store.experiment_item('../exp', 'ok', [{'name': 'figure-1.png'}]))
        self.assertIsNone(gui_store.experiment_item('exp-20261101-101010-abcdef', 'ok', [{'name': 'data.csv'}]))
        item = gui_store.experiment_item('exp-20261101-101010-abcdef', 'ok',
                                         [{'name': 'figure-1.png'}, {'name': '../x.png'}, {'path': 'a/b/plot.png'}])
        self.assertEqual([f['name'] for f in item['figures']], ['figure-1.png', 'plot.png'])

    def test_memory_failures_tell_the_model_how_to_shrink_the_experiment(self):
        sandbox, executor = fake_sandbox()
        executor.results = [ExperimentResult('memory', exit_code=137, error={'type': 'MemoryError',
                                              'message': 'Killed at the 2048 MB memory limit', 'traceback': ''})]
        with tempfile.TemporaryDirectory() as root:
            value = json.loads(sandbox.bind(SANITY, root, explicit=False).execute(
                'execute_python', {'code': 'import numpy as np\nX = np.zeros((20000, 20000))'}))
        self.assertIn('verified nothing', value['note'])
        self.assertIn('2048 MB memory limit', value['note'])
        self.assertIn('simulate step by step', value['note'])


class InterfaceFigureTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def setUp(self):
        super().setUp()
        self.hub.sandbox, self.executor = fake_sandbox()

    def figure_items(self, chat):
        return [item for item in self.ok('GET', f'/api/chats/{chat}')['transcript'] if item.get('role') == 'experiment']

    def test_assistant_shows_figures_above_its_answer_and_serves_them(self):
        self.executor.results = [figure_run()]
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [tool('execute_python', {'code': 'plot()', 'purpose': 'Simulate Brownian paths'}),
                             text('Five Brownian paths are shown above.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'Simulate and plot Brownian motion.'})
        self.wait_for(lambda: any(item.get('content') == 'Five Brownian paths are shown above.'
                                  for item in self.ok('GET', f'/api/chats/{chat}')['transcript']), timeout=20,
                      message='the answer')
        transcript = self.ok('GET', f'/api/chats/{chat}')['transcript']
        roles = [item['role'] for item in transcript]
        self.assertLess(roles.index('experiment'), len(roles) - 1 - roles[::-1].index('assistant'))
        [item] = self.figure_items(chat)
        self.assertEqual((item['purpose'], item['run_status'], item['figures'][0]['name']),
                         ('Simulate Brownian paths', 'ok', 'figure-1.png'))
        path = f"/api/experiments/{item['experiment']}/figure-1.png"
        status, body, headers = self.request('GET', path)
        self.assertEqual((status, headers['Content-Type']), (200, 'image/png'))
        self.assertTrue(body.startswith('\ufffdPNG'))  # the helper decodes bodies as text
        self.assertEqual(self.request('GET', path, auth=False)[0], 401)
        for bad in (f"/api/experiments/{item['experiment']}/missing.png", '/api/experiments/exp-1/figure-1.png',
                    f"/api/experiments/{item['experiment']}/record.json", f"/api/experiments/{item['experiment']}/code.py"):
            self.assertIn(self.request('GET', bad)[0], (404,), bad)

    def test_critique_chat_saves_the_figures_with_the_turn(self):
        self.executor.results = [figure_run()]
        chat = self.ok('POST', '/api/chats', {'mode': 'critic'})['id']
        self.fake.replies = [tool('execute_python', {'code': 'plot()'}), text('See the figure.')]
        self.ok('POST', f'/api/chats/{chat}/messages', {'content': 'Do Brownian paths look rough?'})
        self.wait_task(timeout=30)
        roles = [item['role'] for item in self.ok('GET', f'/api/chats/{chat}')['transcript']]
        self.assertEqual(roles, ['user', 'assistant', 'tool', 'experiment', 'assistant'])
