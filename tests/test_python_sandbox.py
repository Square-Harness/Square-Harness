"""End-to-end tests of the Docker Python sandbox and its two skills."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import zlib

from mathagent import skill_catalog
from mathagent.agent import Agent
from mathagent.mode_calls import ModeOrchestrator
from mathagent.numerics.executor import Artifact, DockerExecutor, ExperimentResult, SandboxUnavailable
from mathagent.numerics.policy import LAB_DEFAULTS, SANITY, SANITY_DEFAULTS, load_policies
from mathagent.numerics.session import PythonSandbox
from mathagent.tools import Workspace

from tests.test_gui import GuiCase, text, tool

SKILLS = Path(skill_catalog.__file__).parent / 'skills'


def docker_sandbox():
    executor = DockerExecutor()
    try:
        executor.probe()
    except (SandboxUnavailable, OSError):
        return None
    return executor


REAL = None if os.environ.get('SQUARE_SKIP_DOCKER_TESTS') else docker_sandbox()
NO_IMAGE = 'Docker or the sandbox image is unavailable (python -m mathagent.numerics build)'


def png(width=4, height=3):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\x00' + b'\x00\x00\x00' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class FakeExecutor:
    """Records each run's code and limits; returns scripted results without Docker."""

    def __init__(self, results=()):
        self.results, self.calls = list(results), []

    def probe(self, refresh=False):
        return {'image': 'square-harness/python-numerics:2', 'id': 'sha256:test', 'architecture': 'amd64'}

    def run(self, code, limits, *, seed=None, parameters=None, cancel=None):
        self.calls.append({'code': code, 'limits': limits})
        return self.results.pop(0) if self.results else ExperimentResult('ok', exit_code=0, stdout='done\n')


def blocks(path):
    return re.findall(r'```python\n(.*?)```', path.read_text(encoding='utf-8'), re.S)


@unittest.skipUnless(REAL, NO_IMAGE)
class ContainerTests(unittest.TestCase):
    def test_untrusted_code_is_isolated_bounded_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as host, patch.dict(os.environ, {'SQUARE_TEST_SECRET': 'env-secret'}):
            secret = Path(host) / 'secret.txt'
            secret.write_text('host-secret')
            probe = REAL.run(textwrap.dedent(f'''
                import os, socket
                status = dict(l.split(':', 1) for l in open('/proc/self/status').read().splitlines() if ':' in l)
                try:
                    socket.create_connection(('1.1.1.1', 53), timeout=2); network = True
                except OSError:
                    network = False
                def writable(path):
                    try:
                        open(path + '/probe', 'w').close(); return True
                    except OSError:
                        return False
                RESULT = {{'network': network, 'uid': os.getuid(), 'caps': int(status['CapEff'], 16),
                          'nnp': status['NoNewPrivs'].strip(), 'root_writable': writable('/usr/local/lib'),
                          'tmp_writable': writable('/tmp'), 'env': sorted(os.environ),
                          'host_paths': [p for p in ({str(secret)!r}, {str(Path.home())!r}, '/var/run/docker.sock')
                                         if os.path.exists(p)]}}'''), SANITY_DEFAULTS)
        self.assertEqual(probe.status, 'ok', probe.as_dict())
        self.assertEqual(probe.result, {**probe.result, 'network': False, 'uid': 10001, 'caps': 0, 'nnp': '1',
                                        'root_writable': False, 'tmp_writable': True, 'host_paths': []})
        self.assertNotIn('SQUARE_TEST_SECRET', probe.result['env'])
        stubborn = REAL.run('import signal\nsignal.signal(signal.SIGALRM, signal.SIG_IGN)\nwhile True: pass',
                            replace(SANITY_DEFAULTS, timeout_seconds=1))
        self.assertEqual(stubborn.status, 'timeout')
        hungry = REAL.run('block = bytearray(900 * 2**20)',
                          replace(SANITY_DEFAULTS, memory_mb=256))
        self.assertIn(hungry.status, ('memory', 'error'))
        flood = REAL.run('for i in range(300000): print(i)', SANITY_DEFAULTS)
        self.assertTrue(flood.stdout_truncated and len(flood.stdout) <= SANITY_DEFAULTS.output_bytes + 200)
        self.assertEqual(REAL.leftovers(), [])

    def test_skill_examples_run_and_conclude_correctly(self):
        for skill, limits in (('python-lab', LAB_DEFAULTS), ('numerical-sanity-check', SANITY_DEFAULTS)):
            for path in sorted((SKILLS / skill / 'references').glob('*.md')):
                for index, code in enumerate(blocks(path)):
                    with self.subTest(skill=skill, file=path.name, block=index):
                        self.assertEqual(REAL.run(code, limits, seed=0).status, 'ok')
        a, b, c, d, e, g = (REAL.run(code, SANITY_DEFAULTS, seed=0).result
                            for code in blocks(SKILLS / 'numerical-sanity-check' / 'references' / 'examples.md'))
        self.assertEqual((a['exact_value'], a['classification']), ('-5/4', 'exact counterexample'))
        self.assertEqual((b['det'], b['classification']), (-9401, 'exact counterexample'))
        self.assertEqual((c['first_failing_n'], c['classification']), (6, 'exact counterexample'))
        self.assertTrue(any(d['float64_negative']) and d['mp50_positive'])  # roundoff, not a counterexample
        self.assertAlmostEqual(e['observed_orders'][-1], 2.0, places=2)
        self.assertEqual(g['classification'], 'no counterexample detected')


class InterfaceTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def setUp(self):
        super().setUp()
        self.executor = FakeExecutor()
        self.hub.sandbox = PythonSandbox(executor=self.executor, policies=load_policies())

    def transcript(self, chat):
        return self.ok('GET', f'/api/chats/{chat}')['transcript']

    def wait_answer(self, chat, answer):
        self.wait_for(lambda: any(item.get('content') == answer for item in self.transcript(chat)),
                      timeout=20, message=answer)
        return self.transcript(chat)

    def test_python_lab_is_user_only_and_its_outputs_are_served_as_artifacts(self):
        code = 'import matplotlib.pyplot as plt\nplt.plot([0, 1])'
        self.executor.results = [ExperimentResult('ok', exit_code=0, artifacts=[
            Artifact('figure-1.png', 'figure', png(), {'axes': []}, 4, 3),
            Artifact('report.html', 'page', b'<h1>Report</h1><script>1</script>')])]
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.fake.replies = [tool('execute_python', {'code': code, 'purpose': 'Plot'}), text('Plotted.'),
                             text('The plot is shown above.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': '@python_lab plot a line',
                                                     'mode_call': {'mode': 'python_lab', 'start': 0}})
        roles = [item['role'] for item in self.wait_answer(chat, 'The plot is shown above.')]
        self.assertEqual((self.executor.calls[0]['code'], self.executor.calls[0]['limits']), (code, LAB_DEFAULTS))
        self.assertLess(roles.index('experiment'), roles.index('assistant'))
        [item] = [i for i in self.transcript(chat) if i['role'] == 'experiment']
        record = json.loads((self.root / '.mathagent' / 'experiments' / item['experiment'] / 'record.json').read_text())
        self.assertEqual((record['skill'], record['invocation']), ('python-lab', 'explicit'))
        figure, page = item['artifacts']
        self.assertEqual((figure['mime_type'], page['mime_type']), ('image/png', 'text/html'))
        status, _, headers = self.request('GET', figure['url'])
        self.assertEqual((status, headers['Content-Type']), (200, 'image/png'))
        status, _, headers = self.request('GET', page['url'])
        self.assertEqual((status, headers['X-Frame-Options']), (200, 'SAMEORIGIN'))
        self.assertIn('sandbox allow-scripts', headers['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', headers['Content-Security-Policy'])
        self.assertEqual(self.request('GET', f"/api/artifacts/{item['experiment']}/record.json")[0], 404)
        self.assertEqual(self.request('GET', figure['url'], auth=False)[0], 401)
        # The Assistant's own delegate tool can never choose python_lab.
        orchestrator = ModeOrchestrator(Agent(None, Workspace(self.root)), lambda *a: {})
        with self.assertRaisesRegex(ValueError, 'Unknown worker mode'):
            orchestrator._validate_delegate({'mode': 'python_lab', 'request': 'Run.', 'files': [],
                                             'effort': 'low', 'reason': 'More compute.'})

    def test_automatic_checks_keep_the_strict_policy_and_budget(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        calls = [{'function': {'name': 'execute_python', 'arguments': {'code': f'RESULT = {n}'}}} for n in range(4)]
        escalate = tool('execute_python', {'code': 'x = 1', 'timeout_seconds': 60})
        self.fake.replies = [escalate, [{'message': {'content': '', 'tool_calls': calls}, 'done': True,
                                         'done_reason': 'stop', 'eval_count': 8, 'prompt_eval_count': 200}],
                             text('No counterexample was found.')]
        self.ok('POST', f'/api/chats/{chat}/route', {'content': 'Is x^4 - 3x^2 + 1 >= 0 for all real x?'})
        self.wait_answer(chat, 'No counterexample was found.')
        self.assertEqual([call['limits'] for call in self.executor.calls], [SANITY_DEFAULTS] * 3)
        results = [json.loads(m['content']) for m in self.fake.requests[-1]['messages'] if m.get('role') == 'tool']
        statuses = [r.get('sandbox_result', {}).get('status') for r in results]
        self.assertEqual(statuses, ['refused', 'ok', 'ok', 'ok', 'budget_exhausted'])
        self.assertEqual(self.hub.sandbox.policies[SANITY].defaults, SANITY_DEFAULTS)


if __name__ == '__main__':
    unittest.main()
