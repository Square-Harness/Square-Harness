# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Run untrusted Python in a locked-down, throwaway Docker container.

Each run is a fresh container started from a fixed argument array: no network,
a non-root user without capabilities, no privilege gain, a read-only root, a
size-limited tmpfs as the only writable storage, cgroup limits, and no host
mounts, environment or Docker socket. The code goes in on stdin; one framed
JSON result comes back. The host enforces the deadline, caps what it reads,
and removes the container on every exit path.
"""
import base64
from dataclasses import dataclass, field, replace
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import socket
import shutil
import struct
import subprocess
import threading
import time
import uuid

from .admission import AdmissionCancelled, AdmissionUnavailable, slot as numerical_slot

IMAGE_VERSION = '3'
IMAGE = f'square-harness/python-numerics:{IMAGE_VERSION}'
IMAGE_LABEL = 'org.squareharness.sandbox'
CONTAINER_LABEL = 'org.squareharness.sandbox.run'
# The build context's digest, set by `python -m mathagent.numerics build`: an image built from an
# older Dockerfile, runner or lock file is refused even under the same tag and version.
CONTENT_LABEL = 'org.squareharness.sandbox.content'
# Which server process started a container, so `clean` never removes another server's live run.
PROCESS_LABEL = 'org.squareharness.sandbox.process'
DOCKER_DIR = Path(__file__).resolve().parent / 'docker'
BUILD_FILES = ('Dockerfile', 'LICENSE.md', 'NOTICE', 'requirements.in', 'requirements.lock', 'runner.py')
DEFAULT_CONCURRENCY = 2
PROTOCOL_BYTES = 512 * 1024
STARTUP_SECONDS = 60
GRACE_SECONDS = 3
MAX_ARTIFACTS = 20
ARTIFACT_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
# Returned file types: extension -> (kind, MIME type). Everything else is dropped.
ARTIFACT_TYPES = {'.png': ('figure', 'image/png'), '.svg': ('figure', 'image/svg+xml'),
                  '.pdf': ('document', 'application/pdf'), '.html': ('page', 'text/html'),
                  '.csv': ('data', 'text/csv'), '.txt': ('data', 'text/plain'), '.json': ('data', 'application/json')}
# Variables the Docker CLI may need; none of them reaches the container.
DOCKER_ENV = ('PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'SYSTEMROOT',
              'SYSTEMDRIVE', 'TEMP', 'TMP', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL')


class SandboxUnavailable(RuntimeError):
    """Docker or the sandbox image cannot be used."""


@dataclass
class Artifact:
    name: str
    kind: str
    data: bytes
    summary: dict = None
    width: int = None
    height: int = None

    @property
    def mime_type(self):
        return ARTIFACT_TYPES[os.path.splitext(self.name)[1].lower()][1]

    def describe(self):
        value = {'name': self.name, 'kind': self.kind, 'mime_type': self.mime_type, 'bytes': len(self.data)}
        if self.width:
            value.update(width=self.width, height=self.height)
        if self.summary:
            value['summary'] = self.summary
        return value


@dataclass
class ExperimentResult:
    """status: ok, error, timeout, memory, output_limit, cancelled or sandbox_error."""
    status: str
    elapsed_seconds: float = 0.0
    wall_seconds: float = 0.0
    exit_code: int = None
    stdout: str = ''
    stderr: str = ''
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: dict = None
    result: object = None
    artifacts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    message: str = ''
    container: str = ''

    @property
    def ok(self):
        return self.status == 'ok'

    def as_dict(self):
        value = {k: getattr(self, k) for k in ('status', 'elapsed_seconds', 'wall_seconds', 'exit_code', 'stdout',
                                               'stderr', 'stdout_truncated', 'stderr_truncated', 'error', 'result',
                                               'warnings', 'message', 'container')}
        value['artifacts'] = [a.describe() for a in self.artifacts]
        return value


def content_digest(directory=DOCKER_DIR):
    """SHA-256 of the image build context (line endings normalized, so a Windows checkout agrees)."""
    digest = hashlib.sha256()
    for name in BUILD_FILES:
        digest.update(name.encode() + b'\0' + (Path(directory) / name).read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return digest.hexdigest()


def process_id():
    """This server process on this machine (the label value of its containers)."""
    return f'{socket.gethostname()}:{os.getpid()}'


def docker_environment():
    env = {k: v for k, v in os.environ.items() if k in DOCKER_ENV or k.startswith('DOCKER_')}
    env.setdefault('PATH', os.defpath)
    return env


def _hint_daemon():
    system = platform.system()
    if system == 'Darwin':
        return 'Start Docker Desktop (or another engine such as Colima).'
    if system == 'Windows':
        return 'Start Docker Desktop (WSL 2 backend, Linux containers).'
    return ('Start the daemon (sudo systemctl start docker) or use rootless Docker; to use Docker '
            'without sudo, add your user to the docker group and log in again.')


def _png_size(data):
    if len(data) < 24 or not data.startswith(b'\x89PNG\r\n\x1a\n') or data[12:16] != b'IHDR':
        return None
    return struct.unpack('>II', data[16:24])


def _reader(stream, limit, sink, overflow, marker=None, seen=None):
    """Drain a pipe into ``sink`` up to ``limit`` bytes, then keep draining without storing."""
    try:
        while chunk := stream.read1(65536):
            room = limit - len(sink)
            if room > 0:
                sink.extend(chunk[:room])
            if len(chunk) > max(room, 0):
                overflow.set()
            if marker is not None and not seen.is_set() and marker in sink:
                seen.set()
    except (OSError, ValueError):
        pass


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # It exists but belongs to someone else.
    return True


class DockerExecutor:
    """The shared Docker backend; one instance per process, safe across threads."""

    def __init__(self, image=IMAGE, startup_seconds=STARTUP_SECONDS, concurrency=DEFAULT_CONCURRENCY):
        if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}', image):
            raise ValueError('Invalid sandbox image reference')
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            raise ValueError('Sandbox concurrency must be an integer from 1 to 8')
        self.image = image
        self.startup_seconds = startup_seconds
        self.owner = uuid.uuid4().hex[:12]
        self.runtime_nnp = True  # Docker's no-new-privileges option; see _calibrate.
        self._image_info = None
        self._lock = threading.Lock()
        # Each run may use gigabytes; more containers at once than this wait their turn, so
        # parallel workers do not exhaust the Docker VM's memory (whose OOM killer picks any run).
        self.concurrency = concurrency
        self._slots = threading.BoundedSemaphore(concurrency)

    def _cli(self):
        path = shutil.which(os.environ.get('SQUARE_DOCKER') or 'docker')
        if not path:
            raise SandboxUnavailable(
                'Docker is not installed (no docker command on PATH). Install Docker Engine on Linux or '
                'Docker Desktop on macOS/Windows, then build the image: python -m mathagent.numerics build')
        return path

    def _docker(self, *args, timeout=30):
        try:
            return subprocess.run([self._cli(), *args], capture_output=True, timeout=timeout, env=docker_environment())
        except subprocess.TimeoutExpired as exc:
            raise SandboxUnavailable(f'Docker did not answer within {timeout} s. {_hint_daemon()}') from exc
        except OSError as exc:
            raise SandboxUnavailable(f'Cannot run Docker: {exc}') from exc

    def probe(self, refresh=False):
        """Check the CLI, the daemon and the image once; return image facts or raise SandboxUnavailable."""
        with self._lock:
            if self._image_info is not None and not refresh:
                return dict(self._image_info)
        version = self._docker('version', '--format', '{{.Server.Version}}', timeout=20)
        if version.returncode != 0:
            detail = version.stderr.decode(errors='replace').strip()[-500:]
            problem = ('this account cannot use Docker (permission denied)' if 'permission denied' in detail.lower()
                       else 'the Docker daemon is not reachable')
            raise SandboxUnavailable(f'Docker is installed but {problem}. {_hint_daemon()} Docker said: {detail}')
        inspect = self._docker('image', 'inspect', '--format',
                               '{{json .Config.Labels}}|{{.Id}}|{{.Architecture}}|{{.Os}}', self.image, timeout=20)
        if inspect.returncode != 0:
            raise SandboxUnavailable(f'The sandbox image {self.image} is not built. Build it once with: '
                                     'python -m mathagent.numerics build (it is never pulled automatically).')
        labels, image_id, arch, osname = inspect.stdout.decode(errors='replace').strip().split('|', 3)
        try:
            labels = json.loads(labels) or {}
        except ValueError:
            labels = {}
        if labels.get(IMAGE_LABEL) != 'python-numerics' or labels.get(IMAGE_LABEL + '.version') != IMAGE_VERSION:
            raise SandboxUnavailable(f'{self.image} is not a version-{IMAGE_VERSION} sandbox image. '
                                     'Rebuild it with: python -m mathagent.numerics build')
        if labels.get(CONTENT_LABEL) != content_digest():
            raise SandboxUnavailable(f'{self.image} was built from another version of the sandbox files '
                                     '(Dockerfile, runner or package lock). Rebuild it with: '
                                     'python -m mathagent.numerics build')
        self.reap_stale()
        info = {'image': self.image, 'id': image_id, 'architecture': arch, 'os': osname, 'version': IMAGE_VERSION,
                'content': labels.get(CONTENT_LABEL), 'concurrency': self.concurrency,
                'docker': version.stdout.decode(errors='replace').strip(),
                'no_new_privileges': 'docker+process' if self._calibrate() else 'process'}
        with self._lock:
            self._image_info = info
        return dict(info)

    def _calibrate(self):
        """Run one trivial container; True when Docker's no-new-privileges option works here.

        Snap-packaged Docker refuses exec under that option (an AppArmor rule). The runner's
        own prctl(PR_SET_NO_NEW_PRIVS) still applies before any user code, so the guarantee holds.
        """
        from .policy import SANITY_DEFAULTS
        failure = ''
        for nnp in (True, False):
            result = self._execute('pass', SANITY_DEFAULTS, runtime_nnp=nnp)
            if result.ok:
                self.runtime_nnp = nnp
                return nnp
            failure = result.message or result.status
            if 'operation not permitted' not in failure.lower():
                break
        raise SandboxUnavailable(f'The sandbox container cannot start: {failure}')

    def command(self, name, limits, runtime_nnp=None):
        """The fixed `docker run` argument array for one experiment."""
        threads = str(max(1, int(limits.cpus + 0.999)))
        nnp = self.runtime_nnp if runtime_nnp is None else runtime_nnp
        return [self._cli(), 'run', '--rm', '-i', '--name', name, '--pull', 'never',
                '--label', f'{CONTAINER_LABEL}={self.owner}', '--label', f'{PROCESS_LABEL}={process_id()}',
                '--network', 'none', '--user', '10001:10001', '--read-only', '--cap-drop', 'ALL',
                *(['--security-opt', 'no-new-privileges:true'] if nnp else []),
                '--memory', f'{limits.memory_mb}m', '--memory-swap', f'{limits.memory_mb}m',
                '--cpus', f'{limits.cpus:g}', '--pids-limit', str(limits.pids),
                '--ulimit', 'nofile=256:256', '--ulimit', 'core=0',
                '--tmpfs', f'/tmp:rw,nosuid,nodev,noexec,size={limits.tmpfs_mb}m,mode=1777', '--shm-size', '16m',
                '--log-driver', 'none', '--hostname', 'sandbox', '--workdir', '/tmp',
                '--env', 'HOME=/tmp', '--env', 'MPLBACKEND=Agg', '--env', f'OMP_NUM_THREADS={threads}',
                '--env', f'OPENBLAS_NUM_THREADS={threads}', '--env', f'MKL_NUM_THREADS={threads}',
                self.image]

    def remove(self, name):
        """Kill and remove one container; quiet when it is already gone."""
        try:
            subprocess.run([self._cli(), 'rm', '--force', name], capture_output=True, timeout=30,
                           env=docker_environment())
        except (OSError, subprocess.SubprocessError, SandboxUnavailable):
            pass

    def reap_stale(self):
        """Remove sandbox containers older than any run can last (orphans of a killed session)."""
        from .policy import HARD_LIMITS
        limit = HARD_LIMITS.timeout_seconds + self.startup_seconds + 120
        found = self._docker('ps', '--all', '--filter', f'label={CONTAINER_LABEL}', '--format', '{{.Names}}\t{{.CreatedAt}}')
        now = datetime.now(timezone.utc)
        for line in found.stdout.decode(errors='replace').splitlines():
            name, _, created = line.partition('\t')
            try:
                age = (now - datetime.strptime(created[:25], '%Y-%m-%d %H:%M:%S %z')).total_seconds()
            except ValueError:
                continue
            if name.startswith('sqh-py-') and age > limit:
                self.remove(name)

    def leftovers(self, mine=True):
        """Names of sandbox containers that can be removed safely.

        mine: this executor's own containers. Otherwise only stopped containers, containers whose
        server process on this machine is gone, and containers older than any run can last; a live
        run of another server (this machine or one sharing the Docker engine) is never listed."""
        if mine:
            found = self._docker('ps', '--all', '--filter', f'label={CONTAINER_LABEL}={self.owner}', '--format', '{{.Names}}')
            return found.stdout.decode(errors='replace').split()
        from .policy import HARD_LIMITS
        limit = HARD_LIMITS.timeout_seconds + self.startup_seconds + 120
        found = self._docker('ps', '--all', '--filter', f'label={CONTAINER_LABEL}', '--format',
                             '{{.Names}}\t{{.State}}\t{{.CreatedAt}}\t{{.Label "' + PROCESS_LABEL + '"}}')
        now, host, names = datetime.now(timezone.utc), socket.gethostname(), []
        for line in found.stdout.decode(errors='replace').splitlines():
            name, state, created, process = (line.split('\t') + ['', '', ''])[:4]
            try:
                age = (now - datetime.strptime(created[:25], '%Y-%m-%d %H:%M:%S %z')).total_seconds()
            except ValueError:
                age = 0
            owner_host, _, pid = process.rpartition(':')
            gone = owner_host == host and pid.isdigit() and not _alive(int(pid))
            if name.startswith('sqh-py-') and (state not in ('running', 'paused', 'restarting') or gone or age > limit):
                names.append(name)
        return names

    def _wait_for_slot(self, cancel):
        """Queue behind the runs already in progress in this process; False when cancelled."""
        while not self._slots.acquire(timeout=0.1):
            if cancel is not None and cancel.is_set():
                return False
        return True

    def run(self, code, limits, *, seed=None, parameters=None, cancel=None):
        """Execute ``code`` under ``limits``; user errors come back in the result, never as exceptions."""
        if not isinstance(code, str) or not code.strip():
            raise ValueError('Provide nonempty Python source code')
        if len(code.encode('utf-8')) > PROTOCOL_BYTES:
            raise ValueError(f'Python source is limited to {PROTOCOL_BYTES} bytes')
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2 ** 63):
            raise ValueError('seed must be a nonnegative integer')
        parameters = {} if parameters is None else parameters
        if not isinstance(parameters, dict):
            raise ValueError('parameters must be a JSON object')
        try:
            with numerical_slot(cancel):
                self.probe()
                if not self._wait_for_slot(cancel):
                    raise AdmissionCancelled
                try:
                    return self._execute(code, limits, seed=seed, parameters=parameters, cancel=cancel)
                finally:
                    self._slots.release()
        except AdmissionCancelled:
            return ExperimentResult('cancelled', message='Cancelled while waiting for a numerical worker.')
        except AdmissionUnavailable as exc:
            raise SandboxUnavailable(str(exc)) from None

    def _execute(self, code, limits, *, seed=None, parameters=None, cancel=None, runtime_nnp=None):
        nonce = uuid.uuid4().hex
        name = f'sqh-py-{uuid.uuid4().hex[:16]}'
        request = json.dumps({'code': code, 'nonce': nonce, 'timeout': limits.timeout_seconds, 'seed': seed,
                              'parameters': parameters or {},
                              'limits': {'output_bytes': limits.output_bytes, 'cpus': limits.cpus,
                                         'artifact_bytes': limits.artifact_bytes}}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(request) > PROTOCOL_BYTES:
            raise ValueError(f'The encoded Python request exceeds the {PROTOCOL_BYTES}-byte sandbox protocol limit')
        marker = f'<<<SQH-START {nonce}>>>'.encode()
        out, err = bytearray(), bytearray()
        overflow, started = threading.Event(), threading.Event()
        detach = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        t0 = time.monotonic()
        begin = outcome = proc = None
        clean = False
        try:
            proc = subprocess.Popen(self.command(name, limits, runtime_nnp), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=docker_environment(), **detach)
            cap = limits.output_bytes * 3 + limits.artifact_bytes * 4 // 3 + 200_000
            readers = [threading.Thread(target=_reader, args=(proc.stdout, cap, out, overflow, marker, started), daemon=True),
                       threading.Thread(target=_reader, args=(proc.stderr, 65536, err, threading.Event()), daemon=True)]
            for reader in readers:
                reader.start()

            def feed():
                try:
                    proc.stdin.write(request)
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            threading.Thread(target=feed, daemon=True).start()
            while proc.poll() is None:
                now = time.monotonic()
                if begin is None and started.is_set():
                    begin = now
                if cancel is not None and cancel.is_set():
                    outcome = 'cancelled'
                elif overflow.is_set():
                    outcome = 'output_limit'
                elif begin is None and now - t0 > self.startup_seconds:
                    outcome = 'startup'
                elif begin is not None and now - begin > limits.timeout_seconds + GRACE_SECONDS:
                    outcome = 'timeout'
                if outcome:
                    self.remove(name)
                    break
                time.sleep(0.02)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            for reader in readers:
                reader.join(timeout=5)
            clean = outcome is None
        finally:
            if not clean:  # Exceptions, Ctrl+C and kills: nothing may keep running.
                self.remove(name)
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
            if proc is not None:
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
        end = time.monotonic()
        result = self._result(bytes(out), bytes(err), nonce, proc.returncode, outcome, limits,
                              None if begin is None else end - begin)
        result.container = name
        result.wall_seconds = round(end - t0, 3)
        if begin is not None and not result.elapsed_seconds:
            result.elapsed_seconds = round(end - begin, 3)
        return result

    def _result(self, out, err, nonce, returncode, outcome, limits, ran):
        stderr = err.decode('utf-8', 'replace').strip()[-4000:]
        timeout = {'type': 'TimeoutError', 'message': f'Execution exceeded the {limits.timeout_seconds:g} s limit',
                   'traceback': ''}
        if outcome == 'cancelled':
            return ExperimentResult('cancelled', exit_code=returncode, message='Cancelled; the container was removed.')
        if outcome == 'output_limit':
            return ExperimentResult('output_limit', exit_code=returncode,
                                    message='The run wrote more output than the sandbox returns; it was stopped.')
        if outcome == 'startup':
            return ExperimentResult('sandbox_error', exit_code=returncode,
                                    message=f'The container did not start within {self.startup_seconds} s. {stderr}'.strip())
        if outcome == 'timeout':
            return ExperimentResult('timeout', exit_code=returncode, error=timeout,
                                    message='Killed at the time limit; the code ignored the interruption, output was lost.')
        head, tail = f'<<<SQH-RESULT {nonce}>>>\n'.encode(), f'\n<<<SQH-END {nonce}>>>'.encode()
        start = out.find(head)
        stop = out.find(tail, start + len(head)) if start >= 0 else -1
        if start < 0 or stop < 0:
            # 152 = SIGXCPU; 137 (SIGKILL) after the time limit = the hard CPU-time limit.
            if returncode == 152 or (returncode == 137 and ran is not None and ran >= limits.timeout_seconds):
                return ExperimentResult('timeout', exit_code=returncode, error=timeout,
                                        message='Killed at the CPU-time limit; output was lost.')
            if returncode == 137:
                return ExperimentResult('memory', exit_code=returncode, message='Killed at the memory limit; output was lost.',
                                        error={'type': 'MemoryError', 'traceback': '',
                                               'message': f'Killed at the {limits.memory_mb} MB memory limit'})
            if returncode in (125, 126, 127) or f'<<<SQH-START {nonce}>>>'.encode() not in out:
                return ExperimentResult('sandbox_error', exit_code=returncode,
                                        message=f'Docker could not run the sandbox container. {stderr}'.strip())
            return ExperimentResult('error', exit_code=returncode, message=stderr,
                                    error={'type': 'ProcessExit', 'traceback': '',
                                           'message': f'The interpreter exited with status {returncode} before reporting'})
        try:
            data = json.loads(out[start + len(head):stop].decode('utf-8'))
            if not isinstance(data, dict) or data.get('status') not in ('ok', 'error', 'timeout', 'memory'):
                raise ValueError('unexpected result shape')
        except ValueError as exc:
            return ExperimentResult('sandbox_error', exit_code=returncode, message=f'Unreadable sandbox result: {exc}')
        result = ExperimentResult(data['status'], exit_code=returncode, elapsed_seconds=float(data.get('elapsed_seconds') or 0))
        if data['status'] == 'memory':  # The runner survived its code's kill and kept the output.
            result.exit_code = 137
            result.message = f'Killed at the {limits.memory_mb} MB memory limit; what it printed before is kept.'
        elif data['status'] == 'timeout' and (data.get('error') or {}).get('message', '').endswith('was killed'):
            result.message = 'Killed at the time limit; what it printed before is kept.'
        for key in ('stdout', 'stderr'):
            text = data.get(key) if isinstance(data.get(key), str) else ''
            setattr(result, key, text[:limits.output_bytes + 200])
            setattr(result, key + '_truncated', bool(data.get(key + '_truncated')) or len(text) > limits.output_bytes + 200)
        if isinstance(data.get('error'), dict):
            result.error = {k: str(data['error'].get(k, ''))[:limits.output_bytes] for k in ('type', 'message', 'traceback')}
        result.result = data.get('result')
        remaining = limits.output_bytes
        for warning in data.get('warnings') or []:
            message = str(warning)
            size = len(message.encode('utf-8'))
            if size > remaining:
                result.warnings.append('Further diagnostics exceeded the configured output allowance.')
                break
            result.warnings.append(message)
            remaining -= size
        size = len(json.dumps(result.result, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        if size > max(limits.output_bytes, limits.artifact_bytes):
            result.result = {'unavailable': True, 'reason': 'Structured result exceeded the configured return bytes.'}
        elif size > limits.output_bytes:
            limits = replace(limits, artifact_bytes=limits.artifact_bytes - size)
        result.artifacts = self._artifacts(data.get('artifacts') or [], limits, result.warnings)
        return result

    @staticmethod
    def _artifacts(items, limits, warnings):
        """Keep only well-named, well-formed files of the allowed types, within the artifact budget."""
        accepted, names, total = [], set(), 0
        if len(items) > MAX_ARTIFACTS:
            warnings.append(f'Only the first {MAX_ARTIFACTS} artifacts fit the sandbox file-count limit.')
        for item in items[:MAX_ARTIFACTS]:
            name = item.get('name') if isinstance(item, dict) else None
            if not isinstance(name, str) or not ARTIFACT_NAME.match(name) or name in names:
                warnings.append('Rejected an output with an invalid or duplicate name')
                continue
            suffix = os.path.splitext(name)[1].lower()
            try:
                data = base64.b64decode(item.get('b64', ''), validate=True)
            except (ValueError, TypeError):
                warnings.append(f'Rejected {name}: invalid encoding')
                continue
            if suffix not in ARTIFACT_TYPES or total + len(data) > limits.artifact_bytes:
                warnings.append(f'Rejected {name}: unsupported type or over the artifact budget')
                continue
            width = height = None
            if suffix == '.png':
                if (size := _png_size(data)) is None:
                    warnings.append(f'Rejected {name}: not a PNG image')
                    continue
                width, height = size
            elif suffix == '.pdf':
                if not data.startswith(b'%PDF-'):
                    warnings.append(f'Rejected {name}: not a PDF document')
                    continue
            else:
                try:
                    text = data.decode('utf-8')
                except UnicodeDecodeError:
                    warnings.append(f'Rejected {name}: text files must be UTF-8')
                    continue
                if suffix == '.svg' and '<svg' not in text[:4096]:
                    warnings.append(f'Rejected {name}: not an SVG image')
                    continue
            summary = item.get('summary') if isinstance(item.get('summary'), dict) else None
            total += len(data)
            if summary is not None:
                size = len(json.dumps(summary, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                if total + size <= limits.artifact_bytes:
                    total += size
                else:
                    warnings.append(f'The full metadata for {name} exceeded the artifact allowance; its file was kept.')
                    summary = None
            names.add(name)
            accepted.append(Artifact(name, ARTIFACT_TYPES[suffix][0], data, summary, width, height))
        return accepted
