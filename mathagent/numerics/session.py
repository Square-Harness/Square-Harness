"""Bind the shared sandbox to one skill and policy, and expose it as model tools.

Trust boundary: ``PythonSandbox.bind`` is called by controller code only. The
skill (and therefore the policy) is fixed for the life of a session; a model
can call ``execute_python`` with code and optional *lower* limits, but cannot
choose another skill, policy, image or flag. ``python-lab`` is an explicit skill:
binding it without ``explicit=True`` (the user's own command) is refused.
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
# Automatic checks add tool schemas (~1k tokens) and results to every call: offer them
# only when the model context has room to spare. Explicit commands are not gated.
MIN_AUTOMATIC_CONTEXT = 32768
SANITY_NOTE = ('Numerical evidence only. Passing cases never prove a universal claim. A violation is a '
               'candidate counterexample until admissibility, conditioning and precision are checked '
               '(exactly with SymPy, or at higher precision with mpmath).')
LAB_NOTE = 'Report only what this output shows. The code, output and files are saved under experiment_id.'
FAILED_NOTE = ('This run did not complete (status {status}): it verified nothing. Do not cite it as evidence; '
               'fix the code and rerun, or say the check is inconclusive.')
MEMORY_HINT = (' It reached the {memory_mb} MB memory limit: estimate array sizes first (n x m x 8 bytes for '
               'float64), keep only what you need (simulate step by step instead of storing every path, '
               'accumulate statistics, use float32), or use fewer samples.')
FIGURE_NOTE = (' The figures are shown to the user below this run; refer to them by name and describe what '
               'they show instead of pasting image links.')


def _excerpt(text, limit):
    if len(text) <= limit:
        return text
    if limit <= 40:
        return '[omitted]'
    head = limit // 3
    return text[:head] + f'\n[... {len(text) - limit} characters omitted ...]\n' + text[-(limit - head):]


class NumericsSession:
    """One controller-chosen binding: a skill, its policy, a workspace store and a run counter."""

    tool_names = TOOL_NAMES

    def __init__(self, executor, store, policy, skill, *, explicit, approve=None, cancel=None):
        self.executor, self.store, self.policy, self.skill = executor, store, policy, skill
        self.explicit = explicit
        self.approve = approve
        self.cancel = cancel
        self.runs = 0
        self._lock = threading.Lock()
        # Set by an interface to show a saved run (its figures) in the conversation.
        self.on_record = None

    def new_turn(self):
        """A new user turn starts a new check with a fresh run allowance."""
        with self._lock:
            self.runs = 0

    def instructions(self):
        """Level 1 of the skill (description, core rules, budget), sent with the execute_python tool.

        The full SKILL.md body and its references stay one read_skill_reference call away,
        so ordinary chats pay for a few hundred tokens, not the whole skill.
        """
        d = self.policy.defaults
        budget = (f'Sandbox budget: {self.policy.max_runs} runs for this turn; each run at most '
                  f'{d.timeout_seconds:g} s, {d.memory_mb} MB, {d.cpus:g} CPU; no network, no files, no installs.')
        if self.policy.escalation:
            c = self.policy.ceiling
            budget += (f' Larger requests (up to {c.timeout_seconds:g} s, {c.memory_mb} MB, {c.cpus:g} CPU) '
                       'ask the user for approval.')
        how = ('The user invoked this skill.' if self.explicit else
               'This skill is available automatically: decide whether a check is worthwhile.')
        core = self.skill.section('Core rules') or self.skill.body.strip()
        more = ', '.join(('skill',) + self.skill.references)
        return (f'SKILL {self.skill.name}: {self.skill.description} {how}\n{core}\n{budget}\n'
                f'Full procedure and worked examples: read_skill_reference with name {more}.')

    def schemas(self):
        d = self.policy.defaults
        timeout = (f'Lower time limit in seconds (default and maximum {d.timeout_seconds:g}).'
                   if not self.policy.escalation else
                   f'Time limit in seconds (default {d.timeout_seconds:g}; more needs the user\'s approval).')
        properties = {
            'code': {'type': 'string', 'description': 'Complete Python 3.12 script (NumPy, SciPy, SymPy, mpmath, '
                     'Matplotlib). Print results or assign a JSON-friendly RESULT. Open figures are saved; '
                     'extra .png/.csv/.txt/.json files go in OUTPUT_DIR.'},
            'purpose': {'type': 'string', 'description': 'One sentence: the claim tested or the computation requested.'},
            'seed': {'type': 'integer', 'description': 'Seeds random and numpy.random before the run (also SEED).'},
            'parameters': {'type': 'object', 'description': 'Small JSON object available to the code as PARAMS.'},
            'timeout_seconds': {'type': 'number', 'description': timeout},
        }
        if self.policy.escalation:
            properties['memory_mb'] = {'type': 'integer', 'description': f'Memory in MB (default {d.memory_mb}; '
                                       'more needs approval).'}
            properties['cpus'] = {'type': 'number', 'description': f'CPU cores (default {d.cpus:g}; more needs approval).'}
        # The skill travels with its tool, so every model loop (terminal chat, interface
        # workers, the Assistant) receives the same instructions without a prompt hook.
        description = ('Run Python in an isolated local sandbox (no network, no access to your files) for '
                       + ('the computation the user requested.' if self.skill.name == LAB else
                          'a cheap numerical check that could falsify a mathematical claim.')
                       + ' Returns stdout, errors, RESULT, figure summaries and an experiment_id.\n\n'
                       + self.instructions())
        tools = [schema('execute_python', description, properties, ['code']),
                 schema('read_experiment', 'Read more of a saved sandbox experiment.',
                        {'experiment_id': {'type': 'string'},
                         'part': {'type': 'string', 'enum': ['summary', 'code', 'stdout', 'stderr', 'result']},
                         'offset': {'type': 'integer'}},
                        ['experiment_id'])]
        tools.append(schema('read_skill_reference', f'Read the {self.skill.name} skill ("skill") or a reference.',
                            {'name': {'type': 'string', 'enum': ['skill', *self.skill.references]},
                             'offset': {'type': 'integer'}}, ['name']))
        return tools

    # -- tool calls ----------------------------------------------------------

    def execute(self, name, arguments):
        """Run one tool call and return bounded JSON text (never raises for model errors)."""
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError('Tool arguments must be an object')
            if name == 'execute_python':
                value = self.execute_python(**arguments)
            elif name == 'read_experiment':
                value = self.read_experiment(**arguments)
            elif name == 'read_skill_reference':
                value = self.read_skill_reference(**arguments)
            else:
                raise ValueError(f'Unknown sandbox tool {name}')
        except PolicyError as exc:
            value = {'status': 'refused', 'message': str(exc)}
        except TypeError as exc:
            value = {'status': 'invalid_arguments', 'message': str(exc)}
        except (ValueError, OSError) as exc:
            value = {'status': 'invalid_arguments', 'message': str(exc)}
        return self._render(value)

    def execute_python(self, code, purpose='', seed=None, parameters=None, timeout_seconds=None,
                       memory_mb=None, cpus=None):
        if not isinstance(purpose, str) or len(purpose) > 600:
            raise ValueError('purpose must be a sentence of at most 600 characters')
        if not self.policy.escalation and (memory_mb is not None or cpus is not None):
            raise PolicyError(f'{self.policy.name} runs use fixed memory and CPU limits; automatic checks cannot '
                              'request more. Ask the user to use /python-lab for heavier computations.')
        if not isinstance(code, str) or not code.strip():
            raise ValueError('Provide nonempty Python source code')
        limits = resolve(self.policy, {'timeout_seconds': timeout_seconds, 'memory_mb': memory_mb, 'cpus': cpus},
                         self.approve)
        with self._lock:
            if self.runs >= self.policy.max_runs:
                return {'status': 'budget_exhausted', 'runs_left': 0,
                        'message': f'This check has used its {self.policy.max_runs} sandbox runs. Reason from the '
                                   'results obtained, or report the check as inconclusive.'}
            self.runs += 1
        try:
            image = self.executor.probe()
            result = self.executor.run(code, limits, seed=seed, parameters=parameters, cancel=self.cancel)
        except (SandboxUnavailable, OSError) as exc:
            with self._lock:
                self.runs -= 1
            return {'status': 'sandbox_unavailable', 'message': str(exc) +
                    ' Continue without numerical evidence and say that no computation was run.'}
        except ValueError:
            with self._lock:
                self.runs -= 1
            raise
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
        value = {'experiment_id': record['id'], 'status': record['status'],
                 'elapsed_seconds': record['elapsed_seconds'],
                 'limits': {'timeout_seconds': limits.timeout_seconds, 'memory_mb': limits.memory_mb,
                            'cpus': limits.cpus},
                 'runs_left': max(self.policy.max_runs - self.runs, 0)}
        for key in ('stdout', 'stderr'):
            if record[key]:
                value[key] = record[key]
        if record['error']:
            value['error'] = dict(record['error'])
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
        if record['warnings']:
            value['warnings'] = record['warnings']
        if record['message']:
            value['message'] = record['message']
        if record['status'] != 'ok':
            value['note'] = FAILED_NOTE.format(status=record['status'])
            if record['status'] == 'memory':
                value['note'] += MEMORY_HINT.format(memory_mb=limits.memory_mb)
        else:
            value['note'] = LAB_NOTE if self.policy.name == LAB else SANITY_NOTE
        if figures:
            value['note'] += FIGURE_NOTE
        return value

    def read_experiment(self, experiment_id, part='summary', offset=0):
        record = self.store.load(experiment_id)
        if type(offset) is not int or offset < 0:
            raise ValueError('offset must be a nonnegative integer')
        if part == 'summary':
            value = {k: record.get(k) for k in ('id', 'created', 'skill', 'invocation', 'purpose', 'status',
                                                'seed', 'parameters', 'limits', 'elapsed_seconds', 'error',
                                                'warnings', 'message')}
            value['image'] = {k: (record.get('image') or {}).get(k) for k in ('image', 'id', 'architecture')}
            value['artifacts'] = [{k: a.get(k) for k in ('path', 'kind', 'bytes', 'width', 'height')}
                                  for a in record.get('artifacts', [])]
            return value
        if part not in ('code', 'stdout', 'stderr', 'result'):
            raise ValueError('part must be summary, code, stdout, stderr or result')
        text = record.get(part)
        text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
        return {'experiment_id': experiment_id, 'part': part, 'offset': offset, 'length': len(text),
                'text': text[offset:offset + PAGE_CHARS],
                **({'next_offset': offset + PAGE_CHARS} if offset + PAGE_CHARS < len(text) else {})}

    def read_skill_reference(self, name, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('offset must be a nonnegative integer')
        text = self.skill.reference(name)
        return {'skill': self.skill.name, 'reference': name, 'offset': offset, 'length': len(text),
                'text': text[offset:offset + PAGE_CHARS],
                **({'next_offset': offset + PAGE_CHARS} if offset + PAGE_CHARS < len(text) else {})}

    @staticmethod
    def _render(value):
        """Compact JSON within RESULT_CHARS, shrinking the long fields first instead of cutting the JSON."""
        text = json.dumps(value, ensure_ascii=False, default=str)
        budget = 3000
        while len(text) > RESULT_CHARS and budget >= 0:
            shrunk = dict(value)
            for key in ('stdout', 'stderr', 'text'):
                if isinstance(shrunk.get(key), str):
                    shrunk[key] = _excerpt(shrunk[key], budget if key != 'stderr' else budget // 2)
            if isinstance(shrunk.get('error'), dict):
                shrunk['error'] = {**shrunk['error'], 'traceback': _excerpt(shrunk['error'].get('traceback', ''),
                                                                            budget // 2)}
            if 'result' in shrunk and len(json.dumps(shrunk['result'], default=str)) > budget:
                shrunk['result'] = _excerpt(json.dumps(shrunk['result'], default=str), budget)
            for figure in shrunk.get('figures', []):
                if budget < 1500:
                    figure.pop('summary', None)
            text = json.dumps(shrunk, ensure_ascii=False, default=str)
            budget = budget // 2 if budget > 50 else -1
        if len(text) > RESULT_CHARS:
            text = json.dumps({k: value[k] for k in ('experiment_id', 'status') if k in value}
                              | {'message': 'Result too large to show; use read_experiment.'})
        return text


class PythonSandbox:
    """Process-wide configuration of the sandbox: one executor, the policies, availability."""

    def __init__(self, *, enabled=True, automatic=True, image=IMAGE, limits_file=None, executor=None,
                 policies=None):
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
        """None when the sandbox can run, else the reason (rechecked after 30 s)."""
        if not self.enabled:
            return 'The Python sandbox is disabled (--no-python-sandbox).'
        with self._lock:
            if self._failure and time.monotonic() - self._failure[0] < 30:
                return self._failure[1]
        try:
            self.executor.probe()
        except SandboxUnavailable as exc:
            with self._lock:
                self._failure = (time.monotonic(), str(exc))
            return str(exc)
        with self._lock:
            self._failure = None
        return None

    def bind(self, skill_name, workspace_root, *, explicit, approve=None, cancel=None):
        """A session for ``skill_name``; only controller code that reacts to the user passes explicit=True."""
        skill = skill_catalog.load(skill_name)
        if skill.name not in self.policies:
            raise ValueError(f'{skill.name} does not use the Python sandbox')
        if skill.invocation == 'explicit' and explicit is not True:
            raise PermissionError(f'{skill.name} runs only when the user invokes it explicitly')
        reason = self.unavailable()
        if reason:
            raise SandboxUnavailable('unavailable', reason)
        return NumericsSession(self.executor, ExperimentStore(workspace_root), self.policies[skill.name], skill,
                               explicit=bool(explicit), approve=approve if skill.name == LAB else None,
                               cancel=cancel)

    def automatic_session(self, workspace_root, cancel=None, ctx=None):
        """The sanity-check binding for ordinary chat, or None when disabled, unavailable or too small."""
        if not (self.enabled and self.automatic):
            return None
        if ctx is not None and ctx < MIN_AUTOMATIC_CONTEXT:
            return None
        try:
            return self.bind(SANITY, workspace_root, explicit=False, cancel=cancel)
        except (SandboxUnavailable, OSError, ValueError):
            # Automatic checks are optional: a missing engine, image or skill file must not stop a chat.
            return None
