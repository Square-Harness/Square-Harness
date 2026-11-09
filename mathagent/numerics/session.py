# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Bind the shared sandbox to one skill and policy, and expose it as model tools.

Only controller code calls ``PythonSandbox.bind``. The skill, and therefore the
policy, is fixed for the session: a model sends code and may lower the limits,
but cannot choose a skill, policy, image or container flag. ``python-lab`` is an
explicit skill, bound only with ``explicit=True`` (the user's own command).
"""
import json
import threading
import time

from .. import __version__
from .. import skill_catalog
from ..tools import schema
from .artifacts import ExperimentStore
from .executor import IMAGE, DockerExecutor, SandboxUnavailable
from .policy import LAB, SANITY, PolicyError, load_policies, resolve

TOOL_NAMES = ('execute_python', 'read_experiment', 'read_skill_reference')
RESULT_CHARS = 5600
PAGE_CHARS = 5000
# Automatic checks add ~1k tokens of tool schema to every call: only offer them with room to spare.
MIN_AUTOMATIC_CONTEXT = 32768
NOTES = {
    SANITY: ('Numerical evidence only. Passing cases never prove a universal claim; a violation is a candidate '
             'counterexample until admissibility, conditioning and precision are checked (SymPy, mpmath).'),
    LAB: 'Report only what this output shows. The code, output and files are saved under experiment_id.',
    'failed': ('This run did not complete (status {status}): it verified nothing. Do not cite it as evidence; '
               'fix the code and rerun, or say the check is inconclusive.'),
    'memory': (' It reached the {memory_mb} MB memory limit: estimate array sizes first (n x m x 8 bytes), '
               'simulate step by step instead of storing every path, accumulate statistics, or use float32.'),
    'files': ' The files are shown to the user with this run; describe them instead of pasting links.',
}


def _excerpt(text, limit):
    if len(text) <= limit:
        return text
    head = limit // 3
    return text[:head] + f'\n[... {len(text) - limit} characters omitted ...]\n' + text[-(limit - head):]


def _page(text, offset, **fields):
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a nonnegative integer')
    value = {**fields, 'offset': offset, 'length': len(text), 'text': text[offset:offset + PAGE_CHARS]}
    if offset + PAGE_CHARS < len(text):
        value['next_offset'] = offset + PAGE_CHARS
    return value


class NumericsSession:
    """A controller-chosen binding: one skill, its policy, a workspace store and a run counter."""

    tool_names = TOOL_NAMES

    def __init__(self, executor, store, policy, skill, *, explicit, approve=None, cancel=None):
        self.executor, self.store, self.policy, self.skill = executor, store, policy, skill
        self.explicit, self.approve, self.cancel = explicit, approve, cancel
        self.runs = 0
        self.on_record = None  # Set by the interface to show each saved run in the conversation.
        self._lock = threading.Lock()

    def new_turn(self):
        with self._lock:
            self.runs = 0

    def instructions(self):
        """The skill's description, core rules and budget; the full skill is one tool call away."""
        d = self.policy.defaults
        budget = (f'Sandbox budget: {self.policy.max_runs} runs this turn, each at most {d.timeout_seconds:g} s, '
                  f'{d.memory_mb} MB, {d.cpus:g} CPU; no network, no files, no installs.')
        if self.policy.escalation:
            c = self.policy.ceiling
            budget += f' More (up to {c.timeout_seconds:g} s, {c.memory_mb} MB, {c.cpus:g} CPU) needs the user\'s approval.'
        how = 'The user invoked this skill.' if self.explicit else 'Decide whether a check is worthwhile.'
        core = self.skill.section('Core rules') or self.skill.body.strip()
        refs = ', '.join(('skill',) + self.skill.references)
        return (f'SKILL {self.skill.name}: {self.skill.description} {how}\n{core}\n{budget}\n'
                f'Full procedure and worked examples: read_skill_reference with name {refs}.')

    def schemas(self):
        d = self.policy.defaults
        properties = {
            'code': {'type': 'string', 'description': 'Complete Python 3.12 script (NumPy, SciPy, SymPy, mpmath, '
                     'Matplotlib). Print results or assign a JSON-friendly RESULT. Saved files and open figures '
                     'are returned.'},
            'purpose': {'type': 'string', 'description': 'One sentence: the claim tested or the computation requested.'},
            'seed': {'type': 'integer', 'description': 'Seeds random and numpy.random (also SEED).'},
            'parameters': {'type': 'object', 'description': 'Small JSON object available to the code as PARAMS.'},
            'timeout_seconds': {'type': 'number', 'description': f'Time limit in seconds (default {d.timeout_seconds:g}'
                                + ('; more needs the user\'s approval).' if self.policy.escalation else ', the maximum).')},
        }
        if self.policy.escalation:
            properties['memory_mb'] = {'type': 'integer', 'description': f'Memory in MB (default {d.memory_mb}).'}
            properties['cpus'] = {'type': 'number', 'description': f'CPU cores (default {d.cpus:g}).'}
        purpose = ('the computation the user requested' if self.skill.name == LAB
                   else 'a cheap numerical check that could falsify a mathematical claim')
        # The skill travels with its tool, so every model loop gets the same instructions.
        description = (f'Run Python in an isolated local sandbox (no network, no access to your files) for {purpose}. '
                       'Returns stdout, errors, RESULT, file summaries and an experiment_id.\n\n' + self.instructions())
        return [schema('execute_python', description, properties, ['code']),
                schema('read_experiment', 'Read more of a saved sandbox experiment.',
                       {'experiment_id': {'type': 'string'},
                        'part': {'type': 'string', 'enum': ['summary', 'code', 'stdout', 'stderr', 'result']},
                        'offset': {'type': 'integer'}}, ['experiment_id']),
                schema('read_skill_reference', f'Read the {self.skill.name} skill ("skill") or a reference.',
                       {'name': {'type': 'string', 'enum': ['skill', *self.skill.references]},
                        'offset': {'type': 'integer'}}, ['name'])]

    def execute(self, name, arguments):
        """One tool call as bounded JSON text; model errors are returned, never raised."""
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict) or name not in TOOL_NAMES:
                raise ValueError('Unknown sandbox tool or arguments')
            value = getattr(self, name)(**arguments)
        except PolicyError as exc:
            value = {'status': 'refused', 'message': str(exc)}
        except (TypeError, ValueError, OSError) as exc:
            value = {'status': 'invalid_arguments', 'message': str(exc)}
        return self._render(value)

    def execute_python(self, code, purpose='', seed=None, parameters=None, timeout_seconds=None,
                       memory_mb=None, cpus=None):
        if not isinstance(purpose, str) or len(purpose) > 600:
            raise ValueError('purpose must be a sentence of at most 600 characters')
        if not isinstance(code, str) or not code.strip():
            raise ValueError('Provide nonempty Python source code')
        if not self.policy.escalation and (memory_mb is not None or cpus is not None):
            raise PolicyError('Automatic checks use fixed memory and CPU limits; heavier computations are for '
                              'the user\'s /python-lab.')
        limits = resolve(self.policy, {'timeout_seconds': timeout_seconds, 'memory_mb': memory_mb, 'cpus': cpus},
                         self.approve)
        with self._lock:
            if self.runs >= self.policy.max_runs:
                return {'status': 'budget_exhausted', 'runs_left': 0,
                        'message': f'This check has used its {self.policy.max_runs} runs. Reason from the results '
                                   'obtained, or report the check as inconclusive.'}
            self.runs += 1
        try:
            image = self.executor.probe()
            result = self.executor.run(code, limits, seed=seed, parameters=parameters, cancel=self.cancel)
        except (SandboxUnavailable, OSError, ValueError) as exc:
            with self._lock:
                self.runs -= 1
            if isinstance(exc, ValueError):
                raise
            return {'status': 'sandbox_unavailable',
                    'message': f'{exc} Continue without numerical evidence and say that no computation was run.'}
        record = self.store.save(code=code, result=result, limits=limits, policy=self.policy.name, image=image,
                                 seed=seed, parameters=parameters, purpose=purpose, explicit=self.explicit,
                                 harness_version=__version__)
        if self.on_record is not None:
            try:
                self.on_record(record)
            except Exception:  # Display is best effort; the run and its record stand.
                pass
        return self._summary(record, limits)

    def _summary(self, record, limits):
        value = {'experiment_id': record['id'], 'status': record['status'], 'elapsed_seconds': record['elapsed_seconds'],
                 'limits': {'timeout_seconds': limits.timeout_seconds, 'memory_mb': limits.memory_mb, 'cpus': limits.cpus},
                 'runs_left': max(self.policy.max_runs - self.runs, 0)}
        value.update({key: record[key] for key in ('stdout', 'stderr', 'error', 'warnings', 'message') if record[key]})
        if record['result'] is not None:
            value['result'] = record['result']
        figures = [{'path': a['path'], 'width': a.get('width'), 'height': a.get('height'),
                    **({'summary': a['summary']} if a.get('summary') else {})}
                   for a in record['artifacts'] if a['kind'] == 'figure']
        files = [{'path': a['path'], 'bytes': a['bytes']} for a in record['artifacts'] if a['kind'] != 'figure']
        if figures:
            value['figures'] = figures
        if files:
            value['files'] = files
        if record['status'] == 'ok':
            value['note'] = NOTES[self.policy.name]
        else:
            value['note'] = NOTES['failed'].format(status=record['status'])
            if record['status'] == 'memory':
                value['note'] += NOTES['memory'].format(memory_mb=limits.memory_mb)
        if record['artifacts']:
            value['note'] += NOTES['files']
        return value

    def read_experiment(self, experiment_id, part='summary', offset=0):
        record = self.store.load(experiment_id)
        if part == 'summary':
            value = {k: record.get(k) for k in ('id', 'created', 'skill', 'invocation', 'purpose', 'status', 'seed',
                                                'parameters', 'limits', 'elapsed_seconds', 'error', 'warnings', 'message')}
            value['image'] = {k: (record.get('image') or {}).get(k) for k in ('image', 'id', 'architecture')}
            value['artifacts'] = [{k: a.get(k) for k in ('path', 'kind', 'bytes', 'width', 'height')}
                                  for a in record.get('artifacts', [])]
            return value
        if part not in ('code', 'stdout', 'stderr', 'result'):
            raise ValueError('part must be summary, code, stdout, stderr or result')
        text = record.get(part)
        text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
        return _page(text, offset, experiment_id=experiment_id, part=part)

    def read_skill_reference(self, name, offset=0):
        return _page(self.skill.reference(name), offset, skill=self.skill.name, reference=name)

    @staticmethod
    def _render(value):
        """Compact JSON within RESULT_CHARS: long fields are shortened, the JSON is never cut."""
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) <= RESULT_CHARS:
            return text
        value = dict(value)
        for key, limit in (('stdout', 1500), ('stderr', 600), ('text', 3000)):
            if isinstance(value.get(key), str):
                value[key] = _excerpt(value[key], limit)
        if isinstance(value.get('error'), dict):
            value['error'] = {**value['error'], 'traceback': _excerpt(value['error'].get('traceback', ''), 800)}
        if 'result' in value and len(json.dumps(value['result'], default=str)) > 1200:
            value['result'] = _excerpt(json.dumps(value['result'], default=str), 1200)
        value['figures'] = [{k: v for k, v in f.items() if k != 'summary'} for f in value.get('figures', [])] or None
        text = json.dumps({k: v for k, v in value.items() if v is not None}, ensure_ascii=False, default=str)
        if len(text) > RESULT_CHARS:
            text = json.dumps({k: value[k] for k in ('experiment_id', 'status') if k in value}
                              | {'message': 'Result too large to show; use read_experiment.'})
        return text


class PythonSandbox:
    """Process-wide sandbox configuration: one executor, the policies and availability."""

    def __init__(self, *, enabled=True, automatic=True, image=IMAGE, limits_file=None, executor=None, policies=None):
        self.enabled, self.automatic = enabled, automatic
        self.policies = policies or load_policies(limits_file)
        self.executor = executor or DockerExecutor(image)
        self._failure = None
        self._lock = threading.Lock()

    @classmethod
    def from_args(cls, args):
        return cls(enabled=not getattr(args, 'no_python_sandbox', False),
                   automatic=not getattr(args, 'no_numerical_checks', False),
                   image=getattr(args, 'python_image', None) or IMAGE,
                   limits_file=getattr(args, 'python_limits', None))

    def unavailable(self):
        """None when the sandbox can run, else why (a failure is rechecked after 30 s)."""
        if not self.enabled:
            return 'The Python sandbox is disabled (--no-python-sandbox).'
        with self._lock:
            if self._failure and time.monotonic() - self._failure[0] < 30:
                return self._failure[1]
        try:
            self.executor.probe()
            failure = None
        except SandboxUnavailable as exc:
            failure = (time.monotonic(), str(exc))
        with self._lock:
            self._failure = failure
        return failure and failure[1]

    def bind(self, skill_name, workspace_root, *, explicit, approve=None, cancel=None):
        """A session for ``skill_name``; only code reacting to the user's own command passes explicit=True."""
        skill = skill_catalog.load(skill_name)
        if skill.name not in self.policies:
            raise ValueError(f'{skill.name} does not use the Python sandbox')
        if skill.invocation == 'explicit' and explicit is not True:
            raise PermissionError(f'{skill.name} runs only when the user invokes it')
        if reason := self.unavailable():
            raise SandboxUnavailable(reason)
        return NumericsSession(self.executor, ExperimentStore(workspace_root), self.policies[skill.name], skill,
                               explicit=bool(explicit), approve=approve if skill.name == LAB else None, cancel=cancel)

    def automatic_session(self, workspace_root, cancel=None, ctx=None):
        """The sanity-check binding for ordinary chat, or None when off, unavailable or the context is small."""
        if not (self.enabled and self.automatic) or (ctx is not None and ctx < MIN_AUTOMATIC_CONTEXT):
            return None
        try:
            return self.bind(SANITY, workspace_root, explicit=False, cancel=cancel)
        except (SandboxUnavailable, OSError, ValueError):
            return None  # Optional: a missing engine, image or skill file never stops a chat.
