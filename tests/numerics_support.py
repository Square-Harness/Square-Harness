"""Shared fixtures for the Python sandbox tests.

* ``FakeExecutor``: records limits and code, returns scripted results (no Docker).
* ``fake_docker``: a stand-in ``docker`` program for executor process tests
  (hangs, floods, OOM exits, the snap AppArmor failure), POSIX only.
* ``real_sandbox()``: an executor when Docker and the image are usable, else None;
  integration tests skip without it (CI has no image).
"""
import copy
import functools
import json
import os
from pathlib import Path
import re
import stat
import sys
import textwrap

from mathagent.numerics.executor import DockerExecutor, ExperimentResult, SandboxUnavailable
from mathagent.numerics.policy import load_policies
from mathagent.numerics.session import PythonSandbox

IMAGE_INFO = {'image': 'square-harness/python-numerics:1', 'id': 'sha256:test', 'architecture': 'amd64',
              'os': 'linux', 'docker': 'fake', 'version': '1', 'no_new_privileges': 'docker+process'}


class FakeExecutor:
    """Stands in for DockerExecutor: no containers, every call recorded."""

    image = IMAGE_INFO['image']

    def __init__(self, results=None, unavailable=None):
        self.results = list(results or [])
        self.unavailable = unavailable
        self.calls = []

    def probe(self, refresh=False):
        if self.unavailable:
            raise SandboxUnavailable('docker_missing', self.unavailable)
        return dict(IMAGE_INFO)

    def run(self, code, limits, *, seed=None, parameters=None, cancel=None):
        self.calls.append({'code': code, 'limits': limits, 'seed': seed, 'parameters': parameters})
        if self.results:
            return copy.deepcopy(self.results.pop(0))
        return ExperimentResult('ok', elapsed_seconds=0.01, wall_seconds=0.5, exit_code=0, stdout='done\n',
                                result={'value': 1})


def fake_sandbox(executor=None, **kwargs):
    executor = executor or FakeExecutor()
    return PythonSandbox(executor=executor, policies=load_policies(), **kwargs), executor


@functools.lru_cache(maxsize=1)
def real_sandbox():
    """A probed DockerExecutor, or None when Docker or the image is missing."""
    if os.environ.get('SQUARE_SKIP_DOCKER_TESTS'):
        return None
    executor = DockerExecutor()
    try:
        executor.probe()
    except (SandboxUnavailable, OSError):
        return None
    return executor


REAL_SKIP_REASON = 'Docker sandbox image not available (python -m mathagent.numerics build)'

FAKE_DOCKER = r'''#!{python}
"""Fake docker CLI for executor tests. Behaviour from DOCKER_FAKE_MODE; state in DOCKER_FAKE_DIR."""
import json, os, signal, sys, time
state = os.environ['DOCKER_FAKE_DIR']
mode = os.environ.get('DOCKER_FAKE_MODE', 'ok')
args = sys.argv[1:]
with open(os.path.join(state, 'calls.jsonl'), 'a') as log:
    log.write(json.dumps(args) + '\n')
if args[:1] == ['version']:
    if mode == 'daemon_down':
        sys.stderr.write('Cannot connect to the Docker daemon at unix:///var/run/docker.sock\n'); sys.exit(1)
    if mode == 'permission':
        sys.stderr.write('permission denied while trying to connect to the Docker daemon socket\n'); sys.exit(1)
    print('29.0.0'); sys.exit(0)
if args[:2] == ['image', 'inspect']:
    if mode == 'no_image':
        sys.stderr.write('Error: No such image\n'); sys.exit(1)
    version = '0' if mode == 'old_image' else '1'
    print(json.dumps({{'org.squareharness.sandbox': 'python-numerics',
                       'org.squareharness.sandbox.version': version}}) + '|sha256:fake|amd64|linux')
    sys.exit(0)
if args[:2] == ['rm', '--force']:
    path = os.path.join(state, args[2] + '.pid')
    try:
        os.kill(int(open(path).read()), signal.SIGKILL)
    except (OSError, ValueError):
        pass
    try:
        os.unlink(path)
    except OSError:
        pass
    sys.exit(0)
if args[:1] == ['ps']:
    for name in os.listdir(state):
        if name.endswith('.pid'):
            created = time.strftime('%Y-%m-%d %H:%M:%S +0000 UTC', time.gmtime(os.path.getmtime(os.path.join(state, name))))
            print(name[:-4] + ('\t' + created if 'CreatedAt' in ' '.join(args) else ''))
    sys.exit(0)
if args[:1] == ['run']:
    name = args[args.index('--name') + 1]
    pidfile = os.path.join(state, name + '.pid')
    open(pidfile, 'w').write(str(os.getpid()))
    request = json.loads(sys.stdin.buffer.read())
    nonce = request['nonce']
    out = sys.stdout.buffer
    if mode == 'nnp_broken' and 'no-new-privileges:true' in args:
        os.unlink(pidfile)
        sys.stderr.write('docker: exec /usr/local/bin/python: operation not permitted\n'); sys.exit(126)
    if mode in ('ok', 'nnp_broken'):
        out.write(f'<<<SQH-START {{nonce}}>>>\n'.encode())
        body = {{'status': 'ok', 'error': None, 'elapsed_seconds': 0.01, 'stdout': 'echo:' + request['code'][:40],
                 'stderr': '', 'stdout_truncated': False, 'stderr_truncated': False, 'warnings': [],
                 'artifacts': [], 'result': {{'seed': request['seed']}}}}
        out.write(f'<<<SQH-RESULT {{nonce}}>>>\n'.encode() + json.dumps(body).encode() + f'\n<<<SQH-END {{nonce}}>>>\n'.encode())
        out.flush(); os.unlink(pidfile); sys.exit(0)
    out.write(f'<<<SQH-START {{nonce}}>>>\n'.encode()); out.flush()
    if mode == 'hang':
        while True:
            time.sleep(0.1)
    if mode == 'flood':
        chunk = b'x' * 65536
        while True:
            out.write(chunk)
    if mode == 'oom':
        os.unlink(pidfile); sys.exit(137)
    if mode == 'garbage':
        out.write(b'not a result\n'); out.flush(); os.unlink(pidfile); sys.exit(0)
sys.exit(2)
'''


def fake_docker(directory, mode='ok'):
    """Install the fake CLI in ``directory``; return (path, environment overrides)."""
    directory = Path(directory)
    state = directory / 'state'
    state.mkdir(exist_ok=True)
    script = directory / 'docker'
    script.write_text(FAKE_DOCKER.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), {'DOCKER_FAKE_DIR': str(state), 'DOCKER_FAKE_MODE': mode}


def fake_calls(environment):
    path = Path(environment['DOCKER_FAKE_DIR']) / 'calls.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def live_containers(environment):
    return sorted(p.stem for p in Path(environment['DOCKER_FAKE_DIR']).glob('*.pid'))


def python_blocks(path):
    """The executable ```python blocks of a reference Markdown file."""
    return re.findall(r'```python\n(.*?)```', Path(path).read_text(encoding='utf-8'), re.S)


# -- scripted model client (same shape as the orchestrator tests' client) --------

class ScriptedClient:
    backend = 'ollama'
    host = 'http://localhost:11434'
    timeout = 10

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def models(self):
        return ['test-model']

    def stream(self, payload):
        self.requests.append(copy.deepcopy(payload))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        yield from response


def final(text='Final answer'):
    return [{'message': {'content': text}, 'done': True, 'done_reason': 'stop', 'eval_count': 30,
             'prompt_eval_count': 100}]


def tool_call(name, arguments, call_id='call_1'):
    return {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


def calls(*items):
    return [{'message': {'tool_calls': list(items)}, 'done': True, 'done_reason': 'tool_calls',
             'eval_count': 60, 'prompt_eval_count': 100}]


def dedent(code):
    return textwrap.dedent(code).strip() + '\n'
