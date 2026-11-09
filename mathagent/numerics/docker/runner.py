# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""In-container driver: run one untrusted experiment, report one framed JSON result.

stdin: ``{code, nonce, timeout, seed, parameters, limits}``. stdout (the protocol):
``<<<SQH-START nonce>>>`` before the code runs, then ``<<<SQH-RESULT nonce>>>``, one
JSON line and ``<<<SQH-END nonce>>>``. The code's own stdout/stderr go to files on
the size-limited tmpfs and come back as head+tail excerpts. This script is not the
security boundary; the container is, and the host kills it on overrun.
"""
import base64
import io
import json
import linecache
import math
import os
import random
import re
import resource
import shutil
import signal
import sys
import time
import traceback

MAX_INPUT = 512 * 1024
WORK = '/tmp/.sqh'
OUTPUT_DIR = '/tmp/outputs'
MPL_TEMPLATE = '/opt/sandbox/mplconfig'
# Files a run may return, validated again by the host. The code runs in OUTPUT_DIR;
# files saved there (one subfolder deep) or directly in /tmp are collected.
KINDS = {'.png': 'figure', '.svg': 'figure', '.pdf': 'document', '.html': 'page',
         '.csv': 'data', '.txt': 'data', '.json': 'data'}
MAX_FIGURES = 8
MAX_FILES = 16
MAX_PIXELS = 4_000_000
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
SOURCE = '<experiment>'


class ExperimentTimeout(BaseException):
    """A BaseException, so `except Exception` in user code cannot swallow it."""


def _alarm(signum, frame):
    raise ExperimentTimeout()


def jsonable(value, depth=0):
    """A JSON-safe rendering of a result value (NumPy, SymPy and mpmath included)."""
    if depth > 6:
        return repr(value)[:200]
    if value is None or isinstance(value, (bool, str)):
        return value[:20000] if isinstance(value, str) else value
    if isinstance(value, int):
        return value if abs(value) < 2 ** 63 else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, complex):
        return {'re': jsonable(value.real), 'im': jsonable(value.imag)}
    if isinstance(value, dict):
        return {str(k)[:200]: jsonable(v, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v, depth + 1) for v in list(value)[:1000]]
    module = type(value).__module__ or ''
    if module.startswith('numpy') and hasattr(value, 'tolist'):
        if getattr(value, 'size', 1) > 10000:
            return f'<array shape={getattr(value, "shape", "?")} dtype={getattr(value, "dtype", "?")}>'
        return jsonable(value.tolist(), depth + 1)
    return str(value)[:20000]


def read_capped(path, limit):
    """Head and tail of a captured stream so neither the start nor the end is lost."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as stream:
            if size <= limit:
                return stream.read().decode('utf-8', 'replace'), False
            half = max(limit // 2, 1)
            head = stream.read(half)
            stream.seek(size - half)
            tail = stream.read(half)
    except OSError:
        return '', False
    marker = f'\n[... {size - 2 * half} bytes omitted ...]\n'
    return head.decode('utf-8', 'replace') + marker + tail.decode('utf-8', 'replace'), True


def format_error(exc):
    frames = traceback.extract_tb(exc.__traceback__)
    # Keep the experiment's frames and anything it called; drop this driver's exec frame.
    while frames and frames[0].filename != SOURCE:
        frames.pop(0)
    lines = traceback.format_list(frames) + traceback.format_exception_only(type(exc), exc)
    text = ''.join(lines)
    return {'type': type(exc).__name__, 'message': str(exc)[:4000],
            'traceback': text[-6000:]}


def describe(figure):
    """A textual summary of a figure, so a text-only model can read what was plotted."""
    axes = []
    for ax in figure.get_axes()[:6]:
        item = {'title': ax.get_title()[:200], 'xlabel': ax.get_xlabel()[:200], 'ylabel': ax.get_ylabel()[:200],
                'xlim': [float(v) for v in ax.get_xlim()], 'ylim': [float(v) for v in ax.get_ylim()],
                'xscale': ax.get_xscale(), 'yscale': ax.get_yscale(), 'lines': []}
        for line in ax.get_lines()[:10]:
            entry = {'label': '' if str(line.get_label()).startswith('_') else str(line.get_label())[:100]}
            try:
                ys = [float(y) for y in line.get_ydata()]
                finite = [y for y in ys if math.isfinite(y)]
                entry.update(points=len(ys), ymin=min(finite) if finite else None, ymax=max(finite) if finite else None)
            except (TypeError, ValueError):
                pass
            item['lines'].append(entry)
        if ax.images:
            item['images'] = len(ax.images)
        if ax.collections:
            item['collections'] = len(ax.collections)
        axes.append(item)
    return jsonable({'axes': axes})


def track_saved_figures():
    """Remember where each Matplotlib figure was saved, so a saved figure is not captured twice."""
    try:
        from matplotlib.figure import Figure
    except ImportError:
        return
    original = Figure.savefig

    def savefig(self, fname, *args, **kwargs):
        if isinstance(fname, (str, os.PathLike)):
            self.__dict__.setdefault('_sqh_paths', []).append(os.path.abspath(os.fspath(fname)))
        return original(self, fname, *args, **kwargs)
    Figure.savefig = savefig


def _entries(folder):
    try:
        return sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return []


def output_files():
    """(name, entry) of the files in OUTPUT_DIR, its subfolders (as folder-name) and /tmp."""
    found = []
    for folder in (OUTPUT_DIR, '/tmp'):
        for entry in _entries(folder):
            if entry.name.startswith('.') or entry.path in (OUTPUT_DIR, WORK):
                continue
            if entry.is_file(follow_symlinks=False):
                found.append((entry.name, entry))
            elif folder == OUTPUT_DIR and entry.is_dir(follow_symlinks=False):
                found += [(f'{entry.name}-{inner.name}', inner) for inner in _entries(entry.path)
                          if inner.is_file(follow_symlinks=False)]
    return found


def collect(limits, warnings):
    artifacts, names, total = [], set(), 0
    budget = int(limits.get('artifact_bytes', 0))

    def add(name, kind, data, summary=None):
        nonlocal total
        if name in names:
            warnings.append(f'Skipped duplicate output name {name}')
            return
        if total + len(data) > budget:
            warnings.append(f'Skipped {name}: the artifact budget ({budget} bytes) is exhausted')
            return
        names.add(name)
        total += len(data)
        artifacts.append({'name': name, 'kind': kind, 'b64': base64.b64encode(data).decode('ascii'),
                          'summary': summary})

    candidates = output_files()
    files = [(name, entry) for name, entry in candidates if os.path.splitext(name)[1].lower() in KINDS]
    ignored = [name for name, _ in candidates if os.path.splitext(name)[1].lower() not in KINDS]
    if ignored:
        warnings.append('Ignored files of unsupported types: ' + ', '.join(ignored[:5])[:200])
    saved_paths = {os.path.abspath(entry.path) for _, entry in files}
    plt = sys.modules.get('matplotlib.pyplot')
    if plt is not None:
        # Figures already saved to a collected file are not captured again.
        numbers = [n for n in plt.get_fignums() if not set(getattr(plt.figure(n), '_sqh_paths', ())) & saved_paths]
        if len(numbers) > MAX_FIGURES:
            warnings.append(f'Only the first {MAX_FIGURES} of {len(numbers)} open figures were saved')
        for index, number in enumerate(numbers[:MAX_FIGURES], 1):
            try:
                figure = plt.figure(number)
                width, height = figure.get_size_inches()
                area = max(float(width) * float(height), 1e-6)
                dpi = max(10.0, min(100.0, math.sqrt(MAX_PIXELS / area)))
                buffer = io.BytesIO()
                figure.savefig(buffer, format='png', dpi=dpi)
                add(f'figure-{index}.png', 'figure', buffer.getvalue(), describe(figure))
            except Exception as exc:  # A broken figure must not hide the experiment's result.
                warnings.append(f'Figure {number} could not be saved: {type(exc).__name__}: {exc}'[:300])
    for name, entry in files[:MAX_FILES]:
        if not NAME.match(name):
            warnings.append(f'Ignored {name[:80]}: use simple file names (letters, digits, . _ -)')
            continue
        try:
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                data = stream.read(max(budget - total, 0) + 1)
        except OSError as exc:
            warnings.append(f'Could not read {name}: {exc}'[:300])
            continue
        add(name, KINDS[os.path.splitext(name)[1].lower()], data)
    if len(files) > MAX_FILES:
        warnings.append(f'Only the first {MAX_FILES} output files were collected')
    return artifacts


def forbid_privilege_gain():
    """prctl(PR_SET_NO_NEW_PRIVS) before any user code: irreversible and inherited.

    Docker's own no-new-privileges option breaks exec on some engines (snap Docker);
    this keeps the guarantee everywhere.
    """
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'cannot set no_new_privs')
    with open('/proc/self/status') as status:
        if 'NoNewPrivs:\t1' not in status.read():
            raise OSError('no_new_privs is not in effect')


def main():
    forbid_privilege_gain()  # Fails closed: no result, so the host reports a sandbox error.
    protocol = os.fdopen(os.dup(1), 'wb', buffering=0)  # Not inherited by subprocesses (O_CLOEXEC).
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    request = json.loads(raw[:MAX_INPUT].decode('utf-8'))
    nonce = str(request['nonce'])
    code = str(request['code'])
    timeout = float(request['timeout'])
    seed = request.get('seed')
    limits = request.get('limits') or {}
    # Backstop if the host session dies: busy code that ignores the alarm still gets SIGXCPU.
    cpu = int(math.ceil(timeout * max(1.0, float(limits.get('cpus', 1))))) + 2
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    output_bytes = int(limits.get('output_bytes', 20000))

    os.makedirs(WORK, mode=0o700, exist_ok=True)
    os.makedirs(OUTPUT_DIR, mode=0o700, exist_ok=True)
    if os.path.isdir(MPL_TEMPLATE):
        shutil.copytree(MPL_TEMPLATE, WORK + '/mpl', dirs_exist_ok=True)
        os.environ['MPLCONFIGDIR'] = WORK + '/mpl'
    streams = {}
    for fd, label in ((1, 'stdout'), (2, 'stderr')):
        path = f'{WORK}/{label}'
        target = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.dup2(target, fd)
        os.close(target)
        streams[label] = path
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = open(0, 'r', closefd=False)
    sys.stdout = io.TextIOWrapper(io.FileIO(1, 'w', closefd=False), encoding='utf-8',
                                  errors='backslashreplace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(io.FileIO(2, 'w', closefd=False), encoding='utf-8',
                                  errors='backslashreplace', line_buffering=True)

    namespace = {'__name__': '__main__', '__builtins__': __builtins__, 'OUTPUT_DIR': OUTPUT_DIR,
                 'SEED': seed, 'PARAMS': request.get('parameters') or {}}
    if seed is not None:
        random.seed(seed)
        try:
            import numpy
            numpy.random.seed(int(seed) % 2 ** 32)
        except ImportError:
            pass
    linecache.cache[SOURCE] = (len(code), None, code.splitlines(True), SOURCE)
    os.chdir(OUTPUT_DIR)  # plt.savefig('plot.png') lands where it is collected
    track_saved_figures()

    status, error, warnings = 'ok', None, []
    protocol.write(f'<<<SQH-START {nonce}>>>\n'.encode())
    started = time.perf_counter()
    signal.signal(signal.SIGALRM, _alarm)
    signal.signal(signal.SIGXCPU, _alarm)  # PID 1 ignores default-action signals: handle it explicitly
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        try:
            exec(compile(code, SOURCE, 'exec'), namespace)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
            signal.signal(signal.SIGXCPU, signal.SIG_IGN)
    except ExperimentTimeout as exc:
        status, error = 'timeout', format_error(exc)
        error.update(type='TimeoutError', message=f'Execution exceeded the {timeout:g} s limit')
    except SystemExit as exc:
        if exc.code not in (None, 0):
            status, error = 'error', {'type': 'SystemExit', 'message': f'exit status {exc.code!r}', 'traceback': ''}
    except BaseException as exc:  # Report every user failure, MemoryError included.
        status, error = 'error', format_error(exc)
    elapsed = time.perf_counter() - started

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    result = {'status': status, 'error': error, 'elapsed_seconds': round(elapsed, 4)}
    if 'RESULT' in namespace:
        value = jsonable(namespace['RESULT'])
        text = json.dumps(value, ensure_ascii=False)
        result['result'] = value if len(text) <= 20000 else {'truncated_repr': text[:20000]}
    try:
        result['artifacts'] = collect(limits, warnings)
    except Exception as exc:
        warnings.append(f'Output collection failed: {type(exc).__name__}: {exc}'[:300])
        result['artifacts'] = []
    for label, path in streams.items():
        result[label], result[label + '_truncated'] = read_capped(path, output_bytes)
    result['warnings'] = warnings[:20]
    body = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    protocol.write(f'<<<SQH-RESULT {nonce}>>>\n'.encode() + body.encode('utf-8')
                   + f'\n<<<SQH-END {nonce}>>>\n'.encode())
    protocol.flush()
    os._exit(0)  # Skip user atexit handlers and non-daemon threads.


if __name__ == '__main__':
    main()
