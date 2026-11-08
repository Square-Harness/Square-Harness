"""Separate harness processes share numerical capacity before launching work."""
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mathagent.numerics.executor import DockerExecutor, ExperimentResult, SandboxUnavailable
from mathagent.numerics.policy import SANITY_DEFAULTS

REPO = Path(__file__).resolve().parents[1]
WORKER = r'''
import json, os, pathlib, subprocess, sys, threading, time
from mathagent.numerics import admission
from mathagent.numerics.executor import DockerExecutor, ExperimentResult, SandboxUnavailable
from mathagent.numerics.policy import SANITY_DEFAULTS
root, name, hold, cancel_after, wait = pathlib.Path(sys.argv[1]), sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
admission.WAIT_SECONDS = wait
def record(kind, **extra):
    data = json.dumps(dict(name=name, kind=kind, at=time.monotonic(), pid=os.getpid(), **extra)) + '\n'
    fd = os.open(root / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try: os.write(fd, data.encode())
    finally: os.close(fd)
class SimulatedDocker(DockerExecutor):
    def probe(self, refresh=False):
        record('probe')
        return {}
    def _execute(self, code, limits, **kwargs):
        record('start')
        # A separate child stands in for the Docker CLI/container. No child may
        # be launched while this harness process is still waiting for a slot.
        subprocess.run([sys.executable, '-c', 'import time; time.sleep(' + repr(hold) + ')'], check=True)
        record('end')
        return ExperimentResult('ok')
(root / (name + '.ready')).touch()
deadline = time.monotonic() + 10
while not (root / 'go').exists():
    if time.monotonic() > deadline: raise SystemExit('Test start barrier timed out')
    time.sleep(0.01)
cancel = threading.Event()
if cancel_after >= 0:
    threading.Timer(cancel_after, cancel.set).start()
try:
    result = SimulatedDocker().run('print(1)', SANITY_DEFAULTS, cancel=cancel)
    record('result', status=result.status)
except SandboxUnavailable as error:
    record('result', status='unavailable', message=str(error))
'''


class NumericalAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.slots = self.root / 'slots'
        self.slots.mkdir(mode=0o700)
        (self.slots / 'slot-0.lock').touch(mode=0o600)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.temporary.cleanup()

    def worker(self, name, *, hold=0.35, cancel=-1, wait=5):
        environment = {**os.environ, 'PYTHONPATH': str(REPO),
                       'SH_NUMERICS_SLOTS_DIR': str(self.slots), 'SH_NUMERICS_SLOTS': '1'}
        process = subprocess.Popen([sys.executable, '-c', WORKER, str(self.root), name,
                                    str(hold), str(cancel), str(wait)],
                                   cwd=REPO, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        return process

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while not condition():
            if time.monotonic() >= deadline:
                self.fail('Timed out waiting for the numerical admission fixture')
            time.sleep(0.01)

    def events(self):
        path = self.root / 'events.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def finish(self, *processes):
        for process in processes:
            out, error = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, (out + error).decode())

    def test_independent_harness_processes_do_not_probe_or_execute_together(self):
        first, second = self.worker('first'), self.worker('second')
        self.wait_for(lambda: all((self.root / (name + '.ready')).exists() for name in ('first', 'second')))
        (self.root / 'go').touch()
        self.finish(first, second)
        events = self.events()
        self.assertEqual({event['status'] for event in events if event['kind'] == 'result'}, {'ok'})
        intervals = sorted((next(event['at'] for event in events if event['name'] == name and event['kind'] == 'probe'),
                            next(event['at'] for event in events if event['name'] == name and event['kind'] == 'end'))
                           for name in ('first', 'second'))
        self.assertLessEqual(intervals[0][1], intervals[1][0])
        self.assertEqual(len({event['pid'] for event in events}), 2)

    def test_cancelled_and_busy_waiters_never_launch_work_and_capacity_is_released(self):
        (self.root / 'go').touch()
        holder = self.worker('holder', hold=1.5)
        self.wait_for(lambda: any(event['kind'] == 'start' for event in self.events()))
        cancelled = self.worker('cancelled', cancel=0.15)
        busy = self.worker('busy', wait=0.2)
        self.finish(cancelled, busy)
        events = self.events()
        self.assertEqual(next(event['status'] for event in events if event['name'] == 'cancelled'), 'cancelled')
        result = next(event for event in events if event['name'] == 'busy')
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('workers are busy', result['message'])
        self.assertFalse(any(event['name'] in {'cancelled', 'busy'} and event['kind'] != 'result' for event in events))
        self.finish(holder)
        self.finish(self.worker('next', hold=0.01))
        self.assertTrue(any(event['name'] == 'next' and event.get('status') == 'ok' for event in self.events()))

    def test_unsafe_shared_lock_configuration_fails_before_docker(self):
        class NeverDocker(DockerExecutor):
            def probe(self, refresh=False):
                raise AssertionError('Docker was probed before validating shared admission')
        for kind in ('count', 'missing', 'symlink-directory', 'symlink-file', 'hardlink', 'writable-directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                directory = Path(temporary)
                lock = directory / 'slot-0.lock'
                lock.touch(mode=0o600)
                count = '1'
                if kind == 'count':
                    count = '0'
                elif kind == 'missing':
                    lock.unlink()
                elif kind == 'symlink-directory':
                    alias = directory.parent / (directory.name + '-alias')
                    alias.symlink_to(directory, target_is_directory=True)
                    directory = alias
                elif kind == 'symlink-file':
                    target = directory / 'other.lock'
                    target.touch(mode=0o600)
                    lock.unlink()
                    lock.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(lock, directory / 'other.lock')
                elif kind == 'writable-directory':
                    directory.chmod(0o770)
                with patch.dict(os.environ, {'SH_NUMERICS_SLOTS_DIR': str(directory), 'SH_NUMERICS_SLOTS': count}):
                    with self.assertRaises(SandboxUnavailable):
                        NeverDocker().run('print(1)', SANITY_DEFAULTS)

    def test_single_user_default_does_not_require_posix_locking(self):
        class InlineDocker(DockerExecutor):
            def probe(self, refresh=False):
                return {}

            def _execute(self, code, limits, **kwargs):
                return ExperimentResult('ok')
        original_import = builtins.__import__

        def without_fcntl(name, *args, **kwargs):
            if name == 'fcntl':
                raise ImportError('This platform has no POSIX file locking')
            return original_import(name, *args, **kwargs)
        with patch.dict(os.environ), patch('builtins.__import__', side_effect=without_fcntl):
            os.environ.pop('SH_NUMERICS_SLOTS_DIR', None)
            os.environ.pop('SH_NUMERICS_SLOTS', None)
            self.assertEqual(InlineDocker().run('print(1)', SANITY_DEFAULTS).status, 'ok')
            os.environ.update(SH_NUMERICS_SLOTS_DIR=str(self.slots), SH_NUMERICS_SLOTS='1')
            with self.assertRaisesRegex(SandboxUnavailable, 'POSIX file locking'):
                InlineDocker().run('print(1)', SANITY_DEFAULTS)
