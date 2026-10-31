"""Shared Docker executor: process handling (fake docker CLI) and real container runs."""
import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from mathagent.numerics import executor as executor_module
from mathagent.numerics.executor import DockerExecutor, SandboxUnavailable
from mathagent.numerics.policy import LAB_DEFAULTS, SANITY_DEFAULTS

from numerics_support import REAL_SKIP_REASON, dedent, fake_calls, fake_docker, live_containers, real_sandbox

POSIX = os.name == 'posix'


def png(width=3, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\x00' + b'\x00\x00\x00' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


@unittest.skipUnless(POSIX, 'the fake docker CLI is a POSIX script')
class FakeDockerTests(unittest.TestCase):
    """Container lifecycle against a scripted docker CLI: no Docker needed."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = temp.name
        grace = patch.object(executor_module, 'GRACE_SECONDS', 0.3)
        grace.start()
        self.addCleanup(grace.stop)

    def executor(self, mode='ok', probe_first=True):
        """A fake-CLI executor; by default probed (and calibrated) in a healthy mode first."""
        path, env = fake_docker(self.dir, 'ok' if probe_first else mode)
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = env
        executor = DockerExecutor(docker=path, startup_seconds=10)
        if probe_first:
            executor.probe()
            os.environ['DOCKER_FAKE_MODE'] = mode
        return executor

    def test_command_is_a_fixed_locked_down_argument_array(self):
        executor = self.executor()
        command = executor.command('sqh-py-test', SANITY_DEFAULTS, runtime_nnp=True)
        joined = ' '.join(command)
        for expected in ('run --rm -i', '--network none', '--user 10001:10001', '--read-only', '--cap-drop ALL',
                         '--security-opt no-new-privileges:true', '--memory 512m', '--memory-swap 512m',
                         '--cpus 1', '--pids-limit 64', 'size=64m', '--pull never', '--log-driver none',
                         'OMP_NUM_THREADS=1'):
            self.assertIn(expected, joined)
        for forbidden in ('-v', '--volume', '--mount', '--privileged', 'docker.sock', '--cap-add', '--network host',
                          '--pid', '--userns', '--device', '-e', '--env-file'):
            self.assertNotIn(forbidden, command)
        self.assertEqual(command[-1], executor.image)  # Nothing after the image: its entrypoint is fixed.
        self.assertTrue(all(v.split('=')[0] in ('HOME', 'MPLBACKEND', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                                                'MKL_NUM_THREADS')
                            for k, v in zip(command, command[1:]) if k == '--env'))
        lab = ' '.join(executor.command('sqh-py-test', LAB_DEFAULTS, runtime_nnp=False))
        self.assertIn('--memory 1024m', lab)
        self.assertIn('--cpus 2', lab)
        self.assertNotIn('no-new-privileges', lab)

    def test_round_trip_sends_code_on_stdin_never_in_arguments(self):
        executor = self.executor()
        result = executor.run('print("secret-code-marker")', SANITY_DEFAULTS, seed=7)
        self.assertEqual(result.status, 'ok', result.message)
        self.assertIn('secret-code-marker', result.stdout)
        self.assertEqual(result.result, {'seed': 7})
        runs = [c for c in fake_calls(self.env) if c[:1] == ['run']]
        self.assertTrue(runs)
        self.assertFalse(any('secret-code-marker' in arg for call in fake_calls(self.env) for arg in call))
        self.assertEqual(live_containers(self.env), [])

    def test_missing_docker_and_daemon_errors_are_explained(self):
        missing = DockerExecutor(docker=os.path.join(self.dir, 'no-such-docker'))
        with self.assertRaises(SandboxUnavailable) as caught:
            missing.probe()
        self.assertEqual(caught.exception.code, 'docker_missing')
        self.assertIn('Docker Desktop', str(caught.exception))
        for mode, code, text in (('daemon_down', 'daemon_unreachable', 'daemon is not reachable'),
                                 ('permission', 'permission', 'permission denied'),
                                 ('no_image', 'image_missing', 'python -m mathagent.numerics build'),
                                 ('old_image', 'image_outdated', 'Rebuild')):
            with self.subTest(mode=mode):
                with self.assertRaises(SandboxUnavailable) as caught:
                    self.executor(mode, probe_first=False).probe()
                self.assertEqual(caught.exception.code, code)
                self.assertIn(text, str(caught.exception))

    def test_snap_docker_no_new_privileges_failure_falls_back_to_in_process_guard(self):
        executor = self.executor('nnp_broken', probe_first=False)
        info = executor.probe()
        self.assertEqual(info['no_new_privileges'], 'process')
        self.assertFalse(executor.runtime_nnp)
        self.assertNotIn('no-new-privileges:true', executor.command('x', SANITY_DEFAULTS))
        self.assertEqual(executor.run('pass', SANITY_DEFAULTS).status, 'ok')

    def test_timeout_kills_and_removes_the_container(self):
        executor = self.executor('hang')
        started = time.monotonic()
        result = executor.run('while True: pass', replace(SANITY_DEFAULTS, timeout_seconds=0.5))
        self.assertEqual(result.status, 'timeout')
        self.assertLess(time.monotonic() - started, 8)
        self.assertIn(['rm', '--force', result.container], fake_calls(self.env))
        self.assertEqual(live_containers(self.env), [])

    def test_cancellation_removes_the_container(self):
        executor = self.executor('hang')
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        result = executor.run('while True: pass', SANITY_DEFAULTS, cancel=cancel)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(live_containers(self.env), [])

    def test_output_flood_is_capped_while_streaming(self):
        executor = self.executor('flood')
        result = executor.run('print("x" * 10**9)', SANITY_DEFAULTS)
        self.assertEqual(result.status, 'output_limit')
        self.assertEqual(live_containers(self.env), [])

    def test_interrupt_during_a_run_still_removes_the_container(self):
        executor = self.executor('hang')
        real_sleep = time.sleep
        ticks = []

        def interrupt(seconds):
            ticks.append(seconds)
            if len(ticks) > 20:
                raise KeyboardInterrupt
            real_sleep(seconds)
        with patch.object(executor_module.time, 'sleep', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                executor.run('while True: pass', SANITY_DEFAULTS)
        self.assertEqual(live_containers(self.env), [])
        self.assertTrue(any(c[:2] == ['rm', '--force'] for c in fake_calls(self.env)))

    def test_memory_kill_and_protocol_errors_are_reported(self):
        self.assertEqual(self.executor('oom').run('x = bytearray(10**10)', SANITY_DEFAULTS).status, 'memory')
        garbage = self.executor('garbage').run('pass', SANITY_DEFAULTS)
        self.assertEqual(garbage.status, 'error')
        self.assertIn('before reporting', garbage.error['message'])

    def test_orphans_of_a_killed_session_are_reaped(self):
        executor = self.executor('ok')
        state = Path(self.env['DOCKER_FAKE_DIR'])
        sleepers = {}
        for name, age in (('sqh-py-orphan', 3600), ('sqh-py-active', 5)):
            proc = subprocess.Popen(['sleep', '60'])
            self.addCleanup(proc.kill)
            pidfile = state / (name + '.pid')
            pidfile.write_text(str(proc.pid))
            os.utime(pidfile, (time.time() - age, time.time() - age))
            sleepers[name] = proc
        self.assertEqual(executor.reap_stale(), ['sqh-py-orphan'])
        self.assertEqual(live_containers(self.env), ['sqh-py-active'])
        self.assertIsNotNone(sleepers['sqh-py-orphan'].wait(timeout=5))

    def test_artifacts_are_validated_before_they_are_kept(self):
        good = png()
        items = [{'name': 'figure-1.png', 'b64': base64.b64encode(good).decode()},
                 {'name': '../escape.png', 'b64': base64.b64encode(good).decode()},
                 {'name': '.hidden.csv', 'b64': base64.b64encode(b'a,b').decode()},
                 {'name': 'fake.png', 'b64': base64.b64encode(b'<svg onload=alert(1)>').decode()},
                 {'name': 'data.csv', 'b64': base64.b64encode(b'\xff\xfe').decode()},
                 {'name': 'script.sh', 'b64': base64.b64encode(b'rm -rf /').decode()},
                 {'name': 'table.csv', 'b64': base64.b64encode(b'n,value\n1,2\n').decode()},
                 {'name': 'figure-1.png', 'b64': base64.b64encode(good).decode()}]
        warnings = []
        kept = DockerExecutor._artifacts(items, SANITY_DEFAULTS, warnings)
        self.assertEqual([(a.name, a.kind) for a in kept], [('figure-1.png', 'figure'), ('table.csv', 'data')])
        self.assertEqual((kept[0].width, kept[0].height), (3, 2))
        self.assertEqual(len(warnings), 6)
        tiny = replace(SANITY_DEFAULTS, artifact_bytes=10)
        self.assertEqual(DockerExecutor._artifacts(items[:1], tiny, []), [])


@unittest.skipUnless(real_sandbox(), REAL_SKIP_REASON)
class RealExecutorTests(unittest.TestCase):
    """Real containers from the pinned image."""

    def setUp(self):
        self.executor = real_sandbox()

    def tearDown(self):
        self.assertEqual(self.executor.leftovers(), [], 'a sandbox container was left behind')

    def run_code(self, code, limits=SANITY_DEFAULTS, **kwargs):
        return self.executor.run(dedent(code), limits, **kwargs)

    def test_numpy_calculation(self):
        result = self.run_code('''
            import numpy as np
            A = np.array([[2.0, 1.0], [1.0, 3.0]])
            RESULT = {'eigenvalues': np.linalg.eigvalsh(A), 'det': np.linalg.det(A)}
            print('trace', np.trace(A))''')
        self.assertEqual(result.status, 'ok', result.as_dict())
        self.assertAlmostEqual(result.result['det'], 5.0)
        self.assertEqual(len(result.result['eigenvalues']), 2)
        self.assertIn('trace 5.0', result.stdout)
        self.assertGreater(result.elapsed_seconds, 0)

    def test_scientific_stack_is_installed_and_pinned(self):
        result = self.run_code('''
            import numpy, scipy, sympy, mpmath, matplotlib
            from scipy.integrate import quad
            x = sympy.symbols('x')
            RESULT = {'versions': [m.__version__ for m in (numpy, scipy, sympy, mpmath, matplotlib)],
                      'integral': quad(lambda t: t**2, 0, 1)[0],
                      'symbolic': str(sympy.integrate(x**2, (x, 0, 1))),
                      'pi50': mpmath.nstr(mpmath.mp.pi, 30)}''')
        self.assertEqual(result.status, 'ok', result.as_dict())
        self.assertEqual(result.result['versions'], ['2.5.4', '1.18.1', '1.14.0', '1.3.0', '3.11.2'])
        self.assertEqual(result.result['symbolic'], '1/3')
        self.assertAlmostEqual(result.result['integral'], 1 / 3)

    def test_matplotlib_figures_are_returned_with_a_text_summary(self):
        result = self.run_code('''
            import numpy as np, matplotlib.pyplot as plt
            x = np.linspace(0, 1, 11)
            plt.plot(x, x**2, label='square'); plt.title('Parabola'); plt.xlabel('x')
            np.savetxt(OUTPUT_DIR + '/values.csv', np.c_[x, x**2], delimiter=',')''')
        self.assertEqual(result.status, 'ok', result.as_dict())
        figure, data = result.artifacts
        self.assertEqual((figure.name, figure.kind), ('figure-1.png', 'figure'))
        self.assertTrue(figure.data.startswith(b'\x89PNG'))
        self.assertGreater(figure.width, 100)
        axes = figure.summary['axes'][0]
        self.assertEqual((axes['title'], axes['xlabel']), ('Parabola', 'x'))
        self.assertEqual(axes['lines'][0]['label'], 'square')
        self.assertEqual(axes['lines'][0]['ymax'], 1.0)
        self.assertEqual(data.name, 'values.csv')
        self.assertIn(b',', data.data)

    def test_python_exceptions_propagate_with_the_failing_line(self):
        result = self.run_code('''
            def ratio(a, b):
                return a / b
            print('before')
            ratio(1, 0)''')
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.error['type'], 'ZeroDivisionError')
        self.assertIn('<experiment>", line 2', result.error['traceback'])
        self.assertIn('return a / b', result.error['traceback'])
        self.assertNotIn('runner.py', result.error['traceback'])
        self.assertIn('before', result.stdout)

    def test_timeout_is_enforced_gracefully_and_by_force(self):
        limits = replace(SANITY_DEFAULTS, timeout_seconds=1)
        soft = self.run_code('import time\nprint("start")\nwhile True: time.sleep(0.01)', limits)
        self.assertEqual(soft.status, 'timeout')
        self.assertIn('start', soft.stdout)  # interrupted cleanly: output kept
        started = time.monotonic()
        hard = self.run_code('''
            import signal
            signal.signal(signal.SIGALRM, signal.SIG_IGN)  # defeat the soft limit
            while True:
                pass''', limits)
        self.assertEqual(hard.status, 'timeout')
        self.assertLess(time.monotonic() - started, 1 + executor_module.GRACE_SECONDS + 15)

    def test_cpu_backstop_stops_code_even_without_the_host_deadline(self):
        limits = replace(SANITY_DEFAULTS, timeout_seconds=1)
        with patch.object(executor_module, 'GRACE_SECONDS', 60):   # as if the host never killed it
            started = time.monotonic()
            graceful = self.run_code('''
                import signal
                signal.signal(signal.SIGALRM, signal.SIG_IGN)
                print('running')
                while True:
                    pass''', limits)
            forced = self.run_code('''
                import signal
                for number in (signal.SIGALRM, signal.SIGXCPU):
                    signal.signal(number, signal.SIG_IGN)
                while True:
                    pass''', limits)
        self.assertEqual(graceful.status, 'timeout', graceful.as_dict())
        self.assertIn('running', graceful.stdout)              # SIGXCPU interrupted it cleanly
        self.assertEqual(forced.status, 'timeout', forced.as_dict())
        self.assertIn('CPU-time', forced.error['message'])     # SIGKILL at the hard limit, not "memory"
        self.assertLess(time.monotonic() - started, 30)

    def test_memory_limit(self):
        result = self.run_code('block = bytearray(900 * 1024 * 1024)\nprint(len(block))',
                               replace(SANITY_DEFAULTS, memory_mb=256))
        self.assertIn(result.status, ('memory', 'error'), result.as_dict())
        if result.status == 'error':
            self.assertEqual(result.error['type'], 'MemoryError')
        self.assertNotIn(str(900 * 1024 * 1024), result.stdout)

    def test_output_is_truncated_head_and_tail(self):
        result = self.run_code('''
            print('FIRST')
            for i in range(300000):
                print('line', i)
            print('LAST')''')
        self.assertEqual(result.status, 'ok')
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout), SANITY_DEFAULTS.output_bytes + 200)
        self.assertIn('FIRST', result.stdout)
        self.assertIn('LAST', result.stdout)
        self.assertIn('bytes omitted', result.stdout)

    def test_seeded_runs_are_reproducible(self):
        code = 'import numpy as np, random\nprint(np.random.rand(3), random.random(), hash("abc"))'
        first, second = self.run_code(code, seed=11), self.run_code(code, seed=11)
        self.assertEqual(first.stdout, second.stdout)
        self.assertNotEqual(first.stdout, self.run_code(code, seed=12).stdout)

    def test_parameters_are_data_only(self):
        result = self.run_code('RESULT = PARAMS["n"] ** 2', parameters={'n': 12})
        self.assertEqual(result.result, 144)


if __name__ == '__main__':
    unittest.main()
