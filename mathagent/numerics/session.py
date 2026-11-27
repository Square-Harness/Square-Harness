# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Bind the shared sandbox to one skill and policy, and expose it as model tools.

Only controller code calls ``PythonSandbox.bind``. The skill, and therefore the
policy, is fixed for the session: a model sends code and may lower the limits,
but cannot choose a skill, policy, image or container flag. ``python-lab`` is a
user-requested skill, bound by a selected command or a validated routing decision.
"""
import json
import sys
import threading
import time
import warnings

from .. import __version__
from .. import skill_catalog
from ..tools import result_budget, schema
from .artifacts import ExperimentStore
from .executor import DEFAULT_CONCURRENCY, IMAGE, PROTOCOL_BYTES, DockerExecutor, SandboxUnavailable
from .policy import LAB, REVIEW, SANITY, PolicyError, load_policies, resolve, review_policy

TOOL_NAMES = ('execute_python', 'read_experiment', 'read_skill_reference')
NOTES = {
    SANITY: ('Numerical evidence only. Passing cases never prove a universal claim; a violation is a candidate '
             'counterexample until admissibility, conditioning and precision are checked (SymPy, mpmath).'),
    LAB: 'Report only what this output shows. The code, output and files are saved under experiment_id.',
    REVIEW: ('Numerical evidence only. Passing cases never prove a universal claim; a violation is a candidate '
             'counterexample until admissibility, conditioning and precision are checked. Cite the experiment_id '
             'when a report rests on this run.'),
    'failed': ('This run did not complete (status {status}): it verified nothing. Do not cite it as evidence; '
               'fix the code and rerun, or say the check is inconclusive.'),
    'memory': (' It reached the {memory_mb} MB memory limit: estimate array sizes first (n x m x 8 bytes), '
               'simulate step by step instead of storing every path, accumulate statistics, or use float32.'),
    'files': ' The files are shown to the user with this run; describe them instead of pasting links.',
}


def normalize_code(code):
    """Undo a common tool-argument encoding slip: the whole script sent with literal "\\n" escapes.

    Only when the text has no real line break, does not compile as sent, and compiles once the
    escapes become line breaks (a one-line script printing "a\\nb" is left alone)."""
    if '\n' in code or '\\n' not in code or _syntax_error(code) is None:
        return code
    fixed = code.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t')
    return fixed if _syntax_error(fixed) is None else code


def _syntax_error(code):
    """The SyntaxError of ``code`` on this host, or None. Compiling never runs the code.

    The sandbox runs Python 3.12: an older host cannot judge newer syntax and checks nothing."""
    if sys.version_info < (3, 12):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')  # e.g. invalid escape sequences: the sandbox reports them
            compile(code, '<experiment>', 'exec', dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        return exc
    except (RecursionError, MemoryError):
        return None  # Let the sandbox, with its own limits, decide.
    return None


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':'))


def _page(text, offset, budget, **fields):
    """A legal JSON response with a cursor into the original string, accounting for escaped characters."""
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a nonnegative integer')
    def page(count):
        end = offset + count
        value = {**fields, 'offset': offset, 'length': len(text), 'text': text[offset:end]}
        if end < len(text):
            value['next_offset'] = end
        return value
    low, high = 0, min(max(0, len(text) - offset), budget)
    if len(_json(page(high))) > budget:
        while low < high:
            mid = (low + high + 1) // 2
            if len(_json(page(mid))) <= budget:
                low = mid
            else:
                high = mid - 1
        high = low
    if not high and offset < len(text):
        raise ValueError('The context cannot hold this result page and its cursor')
    return page(high)


class NumericsSession:
    """A controller-chosen binding: one skill, its policy, a workspace store and a run counter."""

    tool_names = TOOL_NAMES

    def __init__(self, executor, store, policy, skill, *, explicit, routed=False, approve=None, cancel=None):
        self.executor, self.store, self.policy, self.skill = executor, store, policy, skill
        self.explicit, self.approve, self.cancel = explicit, approve, cancel
        self.invocation = 'routed' if routed else 'explicit' if explicit else 'automatic'
        self.run_limit = policy.explicit_max_runs if explicit and policy.name == SANITY else policy.max_runs
        self.result_chars = result_budget(None)
        self.runs = 0
        self.reached = {}  # Limits a run of this session ran into: a reason to approve a larger one.
        self.on_run_change = None
        self.on_record = None  # Set by the interface to show each saved run in the conversation.
        self._lock = threading.Lock()

    def for_proof(self, saved_limit=None):
        """Bind a whole proof job to this automatic sandbox without changing the caller's turn."""
        if self.invocation != 'automatic' or self.policy.name != SANITY:
            return None
        job = NumericsSession(self.executor, self.store, self.policy, self.skill, explicit=False, cancel=self.cancel)
        configured = self.run_limit
        if self.policy.uses_chat_run_default and configured == self.policy.max_runs:
            configured = None
        job.run_limit = min((n for n in (configured, saved_limit) if n is not None), default=None)
        job.runs, job.result_chars = self.runs, self.result_chars
        job.on_run_change, job.on_record = self.on_run_change, self.on_record
        return job

    def new_turn(self):
        with self._lock:
            self.runs = 0

    @property
    def runs_left(self):
        return None if self.run_limit is None else max(0, self.run_limit - self.runs)

    def instructions(self):
        """The skill's description, core rules and budget; the full skill is one tool call away."""
        d = self.policy.defaults
        allowance = (f'{self.run_limit} runs this turn' if self.run_limit is not None
                     else 'as many useful runs as the job allows')
        budget = (f'Sandbox budget: {allowance}, each at most {d.timeout_seconds:g} s, '
                  f'{d.memory_mb} MB, {d.cpus:g} CPU; no network, no files, no installs.')
        if self.policy.escalation:
            c = self.policy.ceiling
            budget += f' More (up to {c.timeout_seconds:g} s, {c.memory_mb} MB, {c.cpus:g} CPU) needs the user\'s approval.'
        how = {'automatic': 'Decide whether a check is worthwhile.', 'routed': 'The user requested this calculation.',
               'explicit': 'The user invoked this skill.'}[self.invocation]
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
                        'part': {'type': 'string', 'enum': ['summary', 'code', 'stdout', 'stderr', 'result', 'record']},
                        'offset': {'type': 'integer'}}, ['experiment_id']),
                schema('read_skill_reference', f'Read the {self.skill.name} skill ("skill") or a reference.',
                       {'name': {'type': 'string', 'enum': ['skill', *self.skill.references]},
                        'offset': {'type': 'integer'}}, ['name'])]

    def execute(self, name, arguments, *, experiment_id=None):
        """One tool call as bounded JSON text; model errors are returned, never raised."""
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict) or name not in TOOL_NAMES:
                raise ValueError('Unknown sandbox tool or arguments')
            if name == 'execute_python':
                # Only the controller may preallocate the receipt used to recover an interrupted job.
                value = self.execute_python(**arguments, _experiment_id=experiment_id)
            else:
                value = getattr(self, name)(**arguments)
        except PolicyError as exc:
            value = {'status': 'refused', 'message': str(exc)}
        except (TypeError, ValueError, OSError) as exc:
            value = {'status': 'invalid_arguments', 'message': str(exc)}
        return self._render(value)

    def execute_python(self, code, purpose='', seed=None, parameters=None, timeout_seconds=None,
                       memory_mb=None, cpus=None, *, _experiment_id=None):
        if not isinstance(purpose, str) or len(purpose.encode('utf-8')) > PROTOCOL_BYTES:
            raise ValueError(f'purpose must be text within {PROTOCOL_BYTES} bytes')
        if not isinstance(code, str) or not code.strip():
            raise ValueError('Provide nonempty Python source code')
        if not self.policy.escalation and (memory_mb is not None or cpus is not None):
            raise PolicyError('Automatic checks use fixed memory and CPU limits; heavier computations are for '
                              'the user\'s /python-lab.')
        code = normalize_code(code)
        if (error := _syntax_error(code)) is not None:
            # Checked before any approval or container: a typo costs neither a run nor a sandbox start.
            detail = {'type': type(error).__name__, 'message': str(getattr(error, 'msg', error))[:500]}
            if isinstance(error, SyntaxError):
                detail.update(line=error.lineno, text=(error.text or '').rstrip()[:300])
            return {'status': 'syntax_error', 'error': detail, 'runs_left': self.runs_left,
                    'message': 'The code does not compile, so it was not run (no sandbox run was used). Fix it and '
                               'send the complete script again, with real line breaks.'}
        limits = resolve(self.policy, {'timeout_seconds': timeout_seconds, 'memory_mb': memory_mb, 'cpus': cpus},
                         self.approve, purpose=purpose, code=code, reached=self.reached)
        with self._lock:
            if self.run_limit is not None and self.runs >= self.run_limit:
                return {'status': 'budget_exhausted', 'runs_left': 0,
                        'message': f'This check has used its {self.run_limit} runs. Reason from the results '
                                   'obtained, or report the check as inconclusive.'}
            self.runs += 1
            if self.on_run_change:
                self.on_run_change(self.runs)
        try:
            image = self.executor.probe()
            from ..explore_tasks import active_check
            background = active_check.get()
            cancel = background[0].cancellations[background[1]] if background else self.cancel
            result = self.executor.run(code, limits, seed=seed, parameters=parameters, cancel=cancel)
        except (SandboxUnavailable, OSError, ValueError) as exc:
            with self._lock:
                self.runs -= 1
                if self.on_run_change:
                    self.on_run_change(self.runs)
            if isinstance(exc, ValueError):
                raise
            return {'status': 'sandbox_unavailable',
                    'message': f'{exc} Continue without numerical evidence and say that no computation was run.'}
        record = self.store.save(code=code, result=result, limits=limits, policy=self.policy.name, image=image,
                                 seed=seed, parameters=parameters, purpose=purpose, invocation=self.invocation,
                                 harness_version=__version__, experiment_id=_experiment_id)
        hit = {'timeout': 'timeout_seconds', 'memory': 'memory_mb'}.get(result.status)
        if hit:
            with self._lock:
                self.reached[hit] = max(self.reached.get(hit, 0), getattr(limits, hit))
        if self.on_record is not None:
            try:
                self.on_record(record)
            except Exception:  # Display is best effort; the run and its record stand.
                pass
        return self._summary(record, limits)

    def _summary(self, record, limits):
        value = {'experiment_id': record['id'], 'status': record['status'], 'elapsed_seconds': record['elapsed_seconds'],
                 'limits': {'timeout_seconds': limits.timeout_seconds, 'memory_mb': limits.memory_mb, 'cpus': limits.cpus},
                 'runs_left': self.runs_left}
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
        record = self.store.read_saved(experiment_id)
        if part == 'summary':
            value = {k: record.get(k) for k in ('id', 'created', 'skill', 'invocation', 'purpose', 'status', 'seed',
                                                'parameters', 'limits', 'elapsed_seconds', 'error', 'warnings', 'message')}
            value['image'] = {k: (record.get('image') or {}).get(k) for k in ('image', 'id', 'architecture')}
            value.update(artifacts=record.get('artifacts', []), stdout_truncated=record.get('stdout_truncated'),
                         stderr_truncated=record.get('stderr_truncated'), truncated=record.get('truncated', False))
            if not offset and len(_json(value)) <= self.result_chars:
                return value
            text = _json(value)
        elif part == 'record':
            text = _json(record)
        elif part in ('code', 'stdout', 'stderr', 'result'):
            text = record.get(part)
            text = text if isinstance(text, str) else _json(text)
        else:
            raise ValueError('part must be summary, code, stdout, stderr, result or record')
        return _page(text, offset, self.result_chars, experiment_id=experiment_id, part=part,
                     **({'record_truncated': True} if record.get('truncated') else {}))

    def read_skill_reference(self, name, offset=0):
        return _page(self.skill.reference(name), offset, self.result_chars, skill=self.skill.name, reference=name)

    def _render(self, value):
        text = _json(value)
        if len(text) <= self.result_chars:
            return text
        if value.get('experiment_id'):
            return _json({key: value[key] for key in ('experiment_id', 'status', 'runs_left') if key in value}
                         | {'message': 'Saved experiment. Use read_experiment with part result, stdout, stderr, '
                                       'code, summary or record. Follow next_offset for further pages.'})
        return _json({'status': value.get('status', 'error'), 'message': str(value.get('message', ''))[:self.result_chars // 4]})


class PythonSandbox:
    """Process-wide sandbox configuration: one executor, the policies and availability."""

    def __init__(self, *, enabled=True, automatic=True, image=IMAGE, limits_file=None, executor=None, policies=None,
                 concurrency=DEFAULT_CONCURRENCY):
        self.enabled, self.automatic = enabled, automatic
        self.policies = policies or load_policies(limits_file)
        self.executor = executor or DockerExecutor(image, concurrency=concurrency)
        self._failure = None
        self._lock = threading.Lock()

    @classmethod
    def from_args(cls, args):
        return cls(enabled=not getattr(args, 'no_python_sandbox', False),
                   automatic=not getattr(args, 'no_numerical_checks', False),
                   image=getattr(args, 'python_image', None) or IMAGE,
                   limits_file=getattr(args, 'python_limits', None),
                   concurrency=getattr(args, 'python_concurrency', DEFAULT_CONCURRENCY))

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

    def bind(self, skill_name, workspace_root, *, explicit, routed=False, approve=None, cancel=None):
        """A session for ``skill_name``; only code reacting to the user's own command passes explicit=True."""
        skill = skill_catalog.load(skill_name)
        if skill.name not in self.policies:
            raise ValueError(f'{skill.name} does not use the Python sandbox')
        if routed and (skill.name != LAB or explicit):
            raise ValueError('Only a validated Python Lab assignment can be routed')
        if skill.invocation == 'explicit' and explicit is not True and routed is not True:
            raise PermissionError(f'{skill.name} runs only when the user invokes it')
        if reason := self.unavailable():
            raise SandboxUnavailable(reason)
        return NumericsSession(self.executor, ExperimentStore(workspace_root), self.policies[skill.name], skill,
                               explicit=bool(explicit), routed=routed, approve=approve if skill.name == LAB else None, cancel=cancel)

    def review_session(self, workspace_root, role, cancel=None):
        """The binding of one referee or judge of a paper review (policy "review": its own run count, 30 s a run),
        or None when the sandbox is off, automatic checks are off (--no-numerical-checks) or Docker is unavailable.
        The review's controller binds it; the model cannot choose another skill or policy."""
        if not (self.enabled and self.automatic):
            return None
        try:
            if self.unavailable():
                return None
            skill = skill_catalog.load(SANITY)
        except (SandboxUnavailable, OSError, ValueError):
            return None
        return NumericsSession(self.executor, ExperimentStore(workspace_root), review_policy(role), skill,
                               explicit=False, cancel=cancel)

    def automatic_session(self, workspace_root, cancel=None, ctx=None):
        """The sanity-check binding for ordinary chat, or None when off or unavailable."""
        if not (self.enabled and self.automatic):
            return None
        try:
            session = self.bind(SANITY, workspace_root, explicit=False, cancel=cancel)
            session.result_chars = result_budget(ctx)
            return session
        except (SandboxUnavailable, OSError, ValueError):
            return None  # Optional: a missing engine, image or skill file never stops a chat.
