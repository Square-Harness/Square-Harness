"""python-lab: explicit invocation, user code, figures, errors and reproducibility."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from mathagent import cli, skill_catalog
from mathagent.numerics import __main__ as numerics_cli
from mathagent.numerics.executor import ExperimentResult
from mathagent.numerics.policy import LAB, LAB_DEFAULTS, SANITY, SANITY_DEFAULTS
from mathagent.numerics.session import PythonSandbox

from numerics_support import (REAL_SKIP_REASON, ScriptedClient, calls, dedent, fake_sandbox, final, python_blocks,
                              real_sandbox, tool_call)

SKILLS = Path(skill_catalog.__file__).parent / 'skills'
USER_CODE = 'import numpy as np\nA = np.array([[2, 1], [1, 2]])\nprint(np.linalg.eigvalsh(A))  # keep this comment\n'


def run_cli(root, prompt, responses, sandbox, *extra):
    """One-shot CLI turn with a scripted model and a given sandbox; returns (exit code, client, ui)."""
    client = ScriptedClient(responses)
    ui = Mock()
    ui.approve.return_value = False
    with patch('sys.argv', ['square-harness', '--workspace', str(root), '--model', 'test-model', '--mode', 'critic',
                            '--prompt', prompt, *extra]), \
            patch.object(cli, 'UI', return_value=ui), patch.object(cli, 'Ollama', return_value=client), \
            patch.object(cli.PythonSandbox, 'from_args', return_value=sandbox):
        code = cli.main()
    return code, client, ui


def tool_names(request):
    return [t['function']['name'] for t in request.get('tools', [])]


class PythonLabSkillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_skill_is_registered_as_explicit(self):
        skill = skill_catalog.load('python-lab')
        self.assertEqual(skill.invocation, 'explicit')
        self.assertIn('explicitly requests', skill.description)
        self.assertIn('examples', skill.references)
        self.assertIn('Never claim code ran', skill.section('Core rules'))

    def test_cli_command_binds_the_lab_policy_and_preserves_user_code(self):
        sandbox, executor = fake_sandbox()
        responses = [calls(tool_call('execute_python', {'code': USER_CODE, 'purpose': 'Run the user code unchanged'})),
                     final('Eigenvalues 1 and 3 (experiment saved).')]
        code, client, ui = run_cli(self.root, '/python-lab Run this code:\n' + USER_CODE, responses, sandbox)
        self.assertEqual(code, 0)
        self.assertEqual(executor.calls[0]['code'], USER_CODE)          # byte-for-byte
        self.assertEqual(executor.calls[0]['limits'], LAB_DEFAULTS)
        first = client.requests[0]
        self.assertIn('execute_python', tool_names(first))
        properties = first['tools'][tool_names(first).index('execute_python')]['function']['parameters']['properties']
        self.assertIn('memory_mb', properties)                            # lab-only resource requests
        self.assertIn('SKILL python-lab', first['tools'][tool_names(first).index('execute_python')]['function']['description'])
        self.assertIn('MODE: Carry out the computation', first['messages'][0]['content'])
        [record] = sandbox.bind(SANITY, self.root, explicit=False).store.list()
        saved = json.loads((self.root / '.mathagent' / 'experiments' / record['id'] / 'record.json').read_text())
        self.assertEqual((saved['skill'], saved['invocation'], saved['code']), (LAB, 'explicit', USER_CODE))
        self.assertEqual((self.root / '.mathagent' / 'experiments' / record['id'] / 'code.py').read_text(), USER_CODE)
        self.assertEqual(saved['image']['image'], 'square-harness/python-numerics:1')

    def test_hyphenated_and_underscored_commands_select_the_same_mode(self):
        for command in ('/python-lab', '/python_lab'):
            sandbox, executor = fake_sandbox()
            code, client, _ = run_cli(self.root, command + ' Compute 2+2.',
                                      [calls(tool_call('execute_python', {'code': 'RESULT = 2 + 2'})), final('4')],
                                      sandbox)
            self.assertEqual(code, 0)
            self.assertEqual(executor.calls[0]['limits'], LAB_DEFAULTS, command)

    def test_lab_is_not_offered_outside_its_command(self):
        sandbox, executor = fake_sandbox()
        code, client, _ = run_cli(self.root, 'Is x^2 >= 0?', [final('Yes: squares are nonnegative.')], sandbox,
                                  '--ctx', '40960')
        self.assertEqual(code, 0)
        description = client.requests[0]['tools'][tool_names(client.requests[0]).index('execute_python')]['function']
        self.assertIn('SKILL numerical-sanity-check', description['description'])
        self.assertNotIn('memory_mb', description['parameters']['properties'])

    def test_unavailable_sandbox_is_a_clear_error_for_the_explicit_command(self):
        sandbox, _ = fake_sandbox()
        sandbox.executor.unavailable = 'Docker is not installed (no docker command on PATH).'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code, client, ui = run_cli(self.root, '/python-lab plot sin', [], sandbox)
        self.assertEqual(code, 1)
        said = ' '.join(str(c.args[0]) for c in ui.say.call_args_list)
        self.assertIn('python-lab needs the Python sandbox', said)
        self.assertIn('Docker is not installed', said)
        self.assertEqual(client.requests, [])

    def test_resource_escalation_requires_user_approval(self):
        sandbox, executor = fake_sandbox()
        decisions = iter([False, True])
        previews = []
        session = sandbox.bind(LAB, self.root, explicit=True,
                               approve=lambda preview: previews.append(preview) or next(decisions))
        refused = json.loads(session.execute('execute_python', {'code': 'x = 1', 'timeout_seconds': 120}))
        self.assertEqual(refused['status'], 'refused')
        self.assertEqual(executor.calls, [])
        approved = json.loads(session.execute('execute_python', {'code': 'x = 1', 'timeout_seconds': 120,
                                                                 'memory_mb': 2048}))
        self.assertEqual(approved['status'], 'ok')
        self.assertEqual((executor.calls[0]['limits'].timeout_seconds, executor.calls[0]['limits'].memory_mb),
                         (120, 2048))
        self.assertEqual(len(previews), 2)
        self.assertIn('MORE RESOURCES', previews[0])
        self.assertEqual(json.loads(session.execute('execute_python', {'code': 'x', 'cpus': 64}))['status'], 'refused')

    def test_errors_are_returned_so_the_model_can_correct_its_code(self):
        failing = ExperimentResult('error', exit_code=0, stdout='',
                                   error={'type': 'NameError', 'message': "name 'np' is not defined",
                                          'traceback': '  File "<experiment>", line 1, in <module>\nNameError'})
        sandbox, executor = fake_sandbox()
        executor.results = [failing]
        session = sandbox.bind(LAB, self.root, explicit=True)
        value = json.loads(session.execute('execute_python', {'code': 'print(np.pi)'}))
        self.assertEqual(value['status'], 'error')
        self.assertEqual(value['error']['type'], 'NameError')
        self.assertIn('<experiment>', value['error']['traceback'])
        self.assertIn('verified nothing', value['note'])
        fixed = json.loads(session.execute('execute_python', {'code': 'import numpy as np\nprint(np.pi)'}))
        self.assertEqual(fixed['status'], 'ok')
        self.assertEqual(fixed['runs_left'], 18)


@unittest.skipUnless(real_sandbox(), REAL_SKIP_REASON)
class PythonLabSandboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sandbox = PythonSandbox(executor=real_sandbox())
        self.session = self.sandbox.bind(LAB, self.root, explicit=True)

    def tearDown(self):
        self.assertEqual(real_sandbox().leftovers(), [])

    def call(self, **arguments):
        return json.loads(self.session.execute('execute_python', arguments))

    def test_numerical_computation_and_figure_are_saved_and_readable(self):
        value = self.call(code=dedent('''
            import numpy as np, matplotlib.pyplot as plt
            t = np.linspace(0, 2 * np.pi, 200)
            plt.plot(t, np.sin(t), label='sin'); plt.title('Sine')
            RESULT = {'integral': float(np.trapezoid(np.sin(t) ** 2, t))}'''), purpose='Plot sin and integrate sin^2')
        self.assertEqual(value['status'], 'ok', value)
        self.assertAlmostEqual(value['result']['integral'], 3.14159, places=3)
        [figure] = value['figures']
        self.assertEqual(figure['summary']['axes'][0]['title'], 'Sine')
        path = self.root / figure['path']
        self.assertTrue(path.read_bytes().startswith(b'\x89PNG'))
        self.assertTrue(path.is_relative_to(self.root / '.mathagent' / 'experiments'))
        code = json.loads(self.session.execute('read_experiment', {'experiment_id': value['experiment_id'],
                                                                   'part': 'code'}))
        self.assertIn("plt.title('Sine')", code['text'])

    def test_errors_then_corrected_code(self):
        failed = self.call(code='import numpy as np\nnp.linalg.inv(np.zeros((2, 2)))')
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['error']['type'], 'LinAlgError')
        fixed = self.call(code='import numpy as np\nRESULT = np.linalg.pinv(np.zeros((2, 2))).tolist()')
        self.assertEqual((fixed['status'], fixed['result']), ('ok', [[0.0, 0.0], [0.0, 0.0]]))

    def test_seeded_experiment_is_reproducible_from_its_record(self):
        value = self.call(code='import numpy as np\nprint(np.random.default_rng(SEED).normal(size=3))', seed=2026)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = numerics_cli.main(['rerun', value['experiment_id'], '--workspace', str(self.root)])
        self.assertEqual(code, 0)
        rerun = json.loads(stdout.getvalue().split('\nSaved ')[0])
        self.assertEqual(rerun['stdout'], value['stdout'])
        self.assertEqual(len(self.session.store.list()), 2)

    def test_reference_examples_run_within_the_default_limits(self):
        blocks = python_blocks(SKILLS / 'python-lab' / 'references' / 'examples.md')
        self.assertGreaterEqual(len(blocks), 5)
        for index, code in enumerate(blocks, 1):
            with self.subTest(example=index):
                result = real_sandbox().run(code, LAB_DEFAULTS, seed=0)
                self.assertEqual(result.status, 'ok', result.as_dict())
                self.assertIsNotNone(result.result)
        eigen = real_sandbox().run(blocks[0], LAB_DEFAULTS).result
        self.assertLess(eigen['max_error_vs_exact'], 1e-12)
        symbolic = real_sandbox().run(blocks[4], LAB_DEFAULTS).result
        self.assertEqual(symbolic['minimum'], '-5/4')


if __name__ == '__main__':
    unittest.main()


from mathagent.agent import Agent
from mathagent.mode_calls import USER_MODES, ModeOrchestrator, validated_mode_call
from mathagent.orchestrator import _delegate_schema
from mathagent.tools import Workspace
from test_gui import GuiCase, text, tool


class InterfaceModeCallTests(GuiCase):
    """@python_lab / @sanity_check typed by the user in an Assistant conversation."""
    extra_args = ('--ctx', '40960')

    def setUp(self):
        super().setUp()
        self.hub.sandbox, self.executor = fake_sandbox()

    def call(self, mode, request, replies):
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = replies
        self.ok('POST', f'/api/chats/{chat}/route', {'content': f'@{mode} {request}',
                                                     'mode_call': {'mode': mode, 'start': 0}})
        final = replies[-1][0]['message']['content']
        self.wait_for(lambda: any(item.get('content') == final for item in self.ok('GET', f'/api/chats/{chat}')['transcript']),
                      timeout=20, message='the final answer')
        return self.ok('GET', f'/api/chats/{chat}')['transcript']

    def test_python_lab_mode_call_binds_the_lab_policy(self):
        transcript = self.call('python_lab', 'Run this code:\n' + USER_CODE,
                               [tool('execute_python', {'code': USER_CODE, 'purpose': 'run the user code'}),
                                text('Eigenvalues 1 and 3.'), text('Python Lab result: eigenvalues 1 and 3.')])
        self.assertEqual(self.executor.calls[0]['code'], USER_CODE)
        self.assertEqual(self.executor.calls[0]['limits'], LAB_DEFAULTS)
        worker_tools = self.fake.requests[0]['tools']
        execute = next(t for t in worker_tools if t['function']['name'] == 'execute_python')['function']
        self.assertIn('memory_mb', execute['parameters']['properties'])
        self.assertNotIn('run_python', [t['function']['name'] for t in worker_tools])
        [card] = [item for item in transcript if item.get('role') == 'route']
        self.assertEqual((card['mode'], card['status'], card.get('job_id')), ('python_lab', 'done', None))
        [record] = list((self.root / '.mathagent' / 'experiments').glob('exp-*/record.json'))
        saved = json.loads(record.read_text())
        self.assertEqual((saved['skill'], saved['invocation']), (LAB, 'explicit'))

    def test_sanity_check_mode_call_uses_the_strict_policy(self):
        self.call('sanity_check', 'Is x^4 - 3x^2 + 1 >= 0 for all real x?',
                  [tool('execute_python', {'code': 'RESULT = 1.5**2 * 1.5**2 - 3 * 1.5**2 + 1'}),
                   text('Counterexample near x = 1.22.'), text('False: f(sqrt(3/2)) = -5/4.')])
        self.assertEqual(self.executor.calls[0]['limits'], SANITY_DEFAULTS)
        [record] = list((self.root / '.mathagent' / 'experiments').glob('exp-*/record.json'))
        self.assertEqual(json.loads(record.read_text())['invocation'], 'explicit')

    def test_mode_call_without_a_sandbox_fails_with_an_explanation(self):
        self.executor.unavailable = 'Docker is not installed (no docker command on PATH).'
        self.hub.sandbox._failure = None
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [text('The Python Lab could not run: Docker is missing.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': '@python_lab plot sin',
                                                     'mode_call': {'mode': 'python_lab', 'start': 0}})
        self.wait_for(lambda: any(item.get('role') == 'route' and item.get('status') == 'failed'
                                  for item in self.ok('GET', f'/api/chats/{chat}')['transcript']),
                      timeout=20, message='the failed card')
        card = next(item for item in self.ok('GET', f'/api/chats/{chat}')['transcript'] if item.get('role') == 'route')
        self.assertIn('needs the Python sandbox', card['outcome'])
        self.assertIn('Docker is not installed', card['outcome'])
        self.assertEqual(self.executor.calls, [])


class UserOnlyModeTests(unittest.TestCase):
    """The Assistant can never start python_lab or sanity_check by itself."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_selection_is_validated_and_absent_from_the_delegate_tool(self):
        self.assertEqual(validated_mode_call('@python_lab plot sin', {'mode': 'python_lab', 'start': 0}),
                         ('plot sin', 'python_lab'))
        enum = _delegate_schema()['function']['parameters']['properties']['mode']['enum']
        for mode in USER_MODES:
            self.assertNotIn(mode, enum)

    def test_model_delegations_to_user_only_modes_are_refused(self):
        from numerics_support import ScriptedClient, calls, final, tool_call
        action = {'mode': 'python_lab', 'request': 'Run a heavy simulation.', 'files': [], 'effort': 'high',
                  'reason': 'More compute.'}
        delegated = []
        client = ScriptedClient([calls(tool_call('delegate', action, 'd1')), final('done')])
        agent = Agent(client, Workspace(self.root), ctx=40960, predict=4096, think=False)
        ModeOrchestrator(agent, lambda a, c, s: delegated.append(a['mode']) or {'status': 'complete'}).run(
            'Simulate this SDE with a lot of paths.')
        self.assertIn('Unknown worker mode', json.dumps(client.requests[-1]['messages']))
        self.assertEqual(delegated, [])
        # After a user's @sanity_check, the turn ends with a final answer (no further tools), and
        # that selection never authorizes python_lab.
        client = ScriptedClient([final('Sanity check reported.')])
        agent = Agent(client, Workspace(self.root), ctx=40960, predict=4096, think=False)
        runner = ModeOrchestrator(agent, lambda a, c, s: delegated.append(a['mode']) or {'status': 'complete'},
                                  required_mode='sanity_check')
        runner.run('Is AB+BA PSD for SPD A, B?')
        self.assertEqual(delegated, ['sanity_check'])
        self.assertNotIn('tools', client.requests[-1])
        with self.assertRaisesRegex(ValueError, 'Unknown worker mode'):
            runner._validate_delegate(dict(action, effort='low'))
