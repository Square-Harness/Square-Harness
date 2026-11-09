# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Smoke test of real Docker isolation and resource limits."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from mathagent.numerics.executor import DockerExecutor, SandboxUnavailable
from mathagent.numerics.policy import SANITY_DEFAULTS


def docker_sandbox():
    executor = DockerExecutor()
    try:
        executor.probe()
    except (SandboxUnavailable, OSError):
        return None
    return executor


REAL = None if os.environ.get('SQUARE_SKIP_DOCKER_TESTS') else docker_sandbox()
NO_IMAGE = 'Docker or the sandbox image is unavailable (python -m mathagent.numerics build)'


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


if __name__ == '__main__':
    unittest.main()
