"""Isolation of untrusted code, and policy enforcement independent of model arguments."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathagent.numerics.policy import (HARD_LIMITS, LAB, LAB_DEFAULTS, SANITY, SANITY_DEFAULTS, PolicyError,
                                       lab_policy, load_policies, resolve, sanity_policy)

from numerics_support import REAL_SKIP_REASON, dedent, fake_sandbox, real_sandbox


@unittest.skipUnless(real_sandbox(), REAL_SKIP_REASON)
class ContainerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.executor = real_sandbox()

    def tearDown(self):
        self.assertEqual(self.executor.leftovers(), [], 'a sandbox container was left behind')

    def probe(self, code, limits=SANITY_DEFAULTS):
        result = self.executor.run(dedent(code), limits)
        self.assertEqual(result.status, 'ok', result.as_dict())
        return result.result

    def test_network_is_disabled(self):
        value = self.probe('''
            import socket
            outcomes = {}
            for host, port in (('1.1.1.1', 53), ('8.8.8.8', 443)):
                try:
                    socket.create_connection((host, port), timeout=2); outcomes[host] = 'connected'
                except OSError as exc:
                    outcomes[host] = type(exc).__name__
            try:
                socket.getaddrinfo('pypi.org', 443); outcomes['dns'] = 'resolved'
            except OSError as exc:
                outcomes['dns'] = type(exc).__name__
            outcomes['interfaces'] = sorted(name for _, name in socket.if_nameindex())
            RESULT = outcomes''')
        self.assertNotIn('connected', value.values())
        self.assertNotEqual(value['dns'], 'resolved')
        self.assertEqual(value['interfaces'], ['lo'])

    def test_host_files_home_and_secrets_are_invisible(self):
        with tempfile.TemporaryDirectory() as host_dir:
            secret = Path(host_dir) / 'secret.txt'
            secret.write_text('host-secret-value')
            with patch.dict(os.environ, {'SQUARE_TEST_SECRET': 'env-secret-value', 'AWS_SECRET_ACCESS_KEY': 'x'}):
                value = self.probe(f'''
                    import os
                    checks = {{}}
                    for path in ({str(secret)!r}, {str(Path.home())!r}, {str(Path.home() / '.ssh')!r},
                                 '/var/run/docker.sock', '/run/docker.sock', '/root'):
                        checks[path] = os.path.exists(path) and os.access(path, os.R_OK)
                    RESULT = {{'paths': checks, 'env': dict(os.environ), 'mounts': open('/proc/self/mounts').read()}}''')
        self.assertFalse(any(value['paths'].values()), value['paths'])
        self.assertNotIn('SQUARE_TEST_SECRET', value['env'])
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', value['env'])
        self.assertNotIn('env-secret-value', json.dumps(value['env']))
        self.assertNotIn(str(Path.home()), value['mounts'])
        self.assertNotIn(host_dir, value['mounts'])

    def test_unprivileged_user_without_capabilities_or_privilege_gain(self):
        value = self.probe('''
            import os, subprocess
            status = dict(line.split(':', 1) for line in open('/proc/self/status').read().splitlines() if ':' in line)
            child = subprocess.run(['python', '-c', 'print(open("/proc/self/status").read())'],
                                   capture_output=True, text=True).stdout
            setuid = subprocess.run(['find', '/', '-xdev', '-perm', '/6000', '-type', 'f'],
                                    capture_output=True, text=True).stdout.split()
            RESULT = {'uid': os.getuid(), 'euid': os.geteuid(), 'gid': os.getgid(),
                      'CapEff': status['CapEff'].strip(), 'CapBnd': status['CapBnd'].strip(),
                      'NoNewPrivs': status['NoNewPrivs'].strip(), 'child_nnp': 'NoNewPrivs:\\t1' in child,
                      'seccomp': status['Seccomp'].strip(), 'setuid': setuid}''')
        self.assertEqual((value['uid'], value['euid'], value['gid']), (10001, 10001, 10001))
        self.assertEqual(int(value['CapEff'], 16), 0)
        self.assertEqual(int(value['CapBnd'], 16), 0)
        self.assertEqual(value['NoNewPrivs'], '1')
        self.assertTrue(value['child_nnp'])
        self.assertEqual(value['setuid'], [])

    def test_root_filesystem_is_read_only_and_scratch_is_bounded(self):
        value = self.probe('''
            import errno, os
            def writable(path):
                try:
                    with open(os.path.join(path, 'probe'), 'w') as f:
                        f.write('x')
                    return True
                except OSError:
                    return False
            before = {path: writable(path) for path in ('/', '/usr/local/lib', '/opt/sandbox', '/tmp')}
            filled = 0
            try:
                with open('/tmp/fill', 'wb') as f:
                    for _ in range(200):
                        f.write(b'\\0' * 1024 * 1024); filled += 1
            except OSError as exc:
                full = exc.errno == errno.ENOSPC
            else:
                full = False
            RESULT = {'root': before['/'], 'usr': before['/usr/local/lib'], 'opt': before['/opt/sandbox'],
                      'tmp': before['/tmp'], 'filled_mb': filled, 'tmp_full': full}''')
        self.assertFalse(value['root'] or value['usr'] or value['opt'])
        self.assertTrue(value['tmp'])
        self.assertTrue(value['tmp_full'])
        self.assertLessEqual(value['filled_mb'], SANITY_DEFAULTS.tmpfs_mb)

    def test_process_count_is_limited(self):
        value = self.probe('''
            import os, time
            children = []
            error = None
            try:
                for _ in range(500):
                    pid = os.fork()
                    if pid == 0:
                        time.sleep(30); os._exit(0)
                    children.append(pid)
            except OSError as exc:
                error = type(exc).__name__
            for pid in children:
                os.kill(pid, 9)
            RESULT = {'forked': len(children), 'error': error}''')
        self.assertIsNotNone(value['error'])
        self.assertLess(value['forked'], SANITY_DEFAULTS.pids)

    def test_no_runtime_package_installation(self):
        value = self.probe('''
            import importlib.util, shutil
            RESULT = {'pip': importlib.util.find_spec('pip') is not None, 'pip_cli': shutil.which('pip'),
                      'apt': shutil.which('apt-get') is not None}''')
        self.assertFalse(value['pip'])
        self.assertIsNone(value['pip_cli'])


class PolicyEnforcementTests(unittest.TestCase):
    """Limits come from the bound policy; tool arguments can only lower them."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = temp.name

    def test_default_budgets(self):
        policies = load_policies()
        sanity, lab = policies[SANITY], policies[LAB]
        self.assertEqual((sanity.defaults.timeout_seconds, sanity.defaults.memory_mb, sanity.defaults.cpus,
                          sanity.max_runs, sanity.escalation), (5, 512, 1, 3, False))
        self.assertEqual(sanity.ceiling, sanity.defaults)
        self.assertEqual((lab.defaults.timeout_seconds, lab.defaults.memory_mb, lab.defaults.cpus, lab.escalation),
                         (30, 1024, 2, True))

    def test_automatic_policy_never_escalates(self):
        policy = sanity_policy()
        approvals = []
        for request in ({'timeout_seconds': 6}, {'memory_mb': 1024}, {'cpus': 2}):
            with self.assertRaises(PolicyError):
                resolve(policy, request, approve=lambda preview: approvals.append(preview) or True)
        self.assertEqual(approvals, [], 'an automatic check must not even ask for more')
        self.assertEqual(resolve(policy, {'timeout_seconds': 2}).timeout_seconds, 2)
        self.assertEqual(resolve(policy, {'timeout_seconds': 0.01}).timeout_seconds, 0.5)  # floor
        with self.assertRaises(PolicyError):
            resolve(policy, {'pids': 10_000})
        for bad in (True, float('nan'), float('inf'), -1, 'big'):
            with self.assertRaises(PolicyError):
                resolve(policy, {'timeout_seconds': bad})

    def test_lab_escalation_needs_approval_and_respects_the_ceiling(self):
        policy = lab_policy()
        with self.assertRaisesRegex(PolicyError, 'did not approve'):
            resolve(policy, {'timeout_seconds': 120}, approve=lambda preview: False)
        previews = []
        limits = resolve(policy, {'timeout_seconds': 120, 'memory_mb': 2048},
                         approve=lambda preview: previews.append(preview) or True)
        self.assertEqual((limits.timeout_seconds, limits.memory_mb), (120, 2048))
        self.assertIn('default 30', previews[0])
        with self.assertRaisesRegex(PolicyError, 'ceiling'):
            resolve(policy, {'timeout_seconds': 10_000}, approve=lambda preview: True)

    def test_configuration_is_bounded_by_built_in_maxima(self):
        with self.assertRaises(ValueError):
            sanity_policy({'timeout_seconds': 60})          # automatic checks stay cheap
        with self.assertRaises(ValueError):
            sanity_policy(max_runs=50)
        with self.assertRaises(ValueError):
            lab_policy(ceiling={'memory_mb': HARD_LIMITS.memory_mb * 2})
        with self.assertRaises(ValueError):
            lab_policy(defaults={'timeout_seconds': 500}, ceiling={'timeout_seconds': 300})
        path = Path(self.root) / 'limits.json'
        path.write_text(json.dumps({'sanity': {'limits': {'timeout_seconds': 3}, 'max_runs': 2},
                                    'lab': {'ceiling': {'timeout_seconds': 120}}}))
        policies = load_policies(path)
        self.assertEqual((policies[SANITY].defaults.timeout_seconds, policies[SANITY].max_runs), (3, 2))
        self.assertEqual(policies[LAB].ceiling.timeout_seconds, 120)
        path.write_text(json.dumps({'sanity': {'limits': {'network': True}}}))
        with self.assertRaises(ValueError):
            load_policies(path)

    def test_model_arguments_cannot_select_policy_image_or_flags(self):
        sandbox, executor = fake_sandbox()
        session = sandbox.bind(SANITY, self.root, explicit=False)
        attempts = [{'code': 'x=1', 'policy': 'python-lab'}, {'code': 'x=1', 'image': 'evil:latest'},
                    {'code': 'x=1', 'docker_args': ['--privileged']}, {'code': 'x=1', 'memory_mb': 4096},
                    {'code': 'x=1', 'cpus': 4}, {'code': 'x=1', 'timeout_seconds': 30},
                    {'code': 'x=1', 'network': True}]
        for arguments in attempts:
            with self.subTest(arguments=arguments):
                value = json.loads(session.execute('execute_python', arguments))
                self.assertIn(value['status'], ('refused', 'invalid_arguments'))
        self.assertEqual(executor.calls, [])
        # Calling itself python-lab changes nothing: the binding fixed the policy.
        value = json.loads(session.execute('execute_python', {'code': 'x=1', 'purpose': 'python-lab: run with lab limits'}))
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(executor.calls[-1]['limits'], SANITY_DEFAULTS)
        properties = session.schemas()[0]['function']['parameters']['properties']
        self.assertNotIn('memory_mb', properties)
        self.assertNotIn('cpus', properties)

    def test_explicit_skill_cannot_be_bound_automatically(self):
        sandbox, _ = fake_sandbox()
        with self.assertRaises(PermissionError):
            sandbox.bind(LAB, self.root, explicit=False)
        with self.assertRaises(PermissionError):
            sandbox.bind(LAB, self.root, explicit='yes')
        session = sandbox.bind(LAB, self.root, explicit=True, approve=lambda preview: False)
        self.assertEqual(session.policy.defaults, LAB_DEFAULTS)
        automatic = sandbox.automatic_session(self.root)
        self.assertEqual((automatic.policy.name, automatic.explicit), (SANITY, False))

    def test_experiment_records_stay_inside_the_managed_directory(self):
        sandbox, _ = fake_sandbox()
        session = sandbox.bind(SANITY, self.root, explicit=False)
        for bad in ('../../etc/passwd', '/etc/passwd', 'exp-20260101-000000-zzzzzz', '..'):
            value = json.loads(session.execute('read_experiment', {'experiment_id': bad}))
            self.assertEqual(value['status'], 'invalid_arguments')
        with self.assertRaises(ValueError):
            session.store.path('exp-20260101-000000-abcdef', '../code.py')
        self.assertEqual(json.loads(session.execute('read_skill_reference', {'name': '../../cli'}))['status'],
                         'invalid_arguments')


if __name__ == '__main__':
    unittest.main()
