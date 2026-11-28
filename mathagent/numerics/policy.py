# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Per-skill limits of the Python sandbox, enforced by harness code.

The controller that binds the tool chooses the policy: the user's own command binds
python-lab, automatic contexts bind numerical-sanity-check. Tool arguments can only
lower limits, except that python-lab may ask for more, up to a ceiling, with approval.
"""
from dataclasses import asdict, dataclass, fields, replace
import json
import math
from pathlib import Path

LAB = 'python-lab'
SANITY = 'numerical-sanity-check'
REVIEW = 'review'  # the paper review's referees and judges: the sanity-check skill with more runs of 30 s


class PolicyError(ValueError):
    """A request that the bound policy does not allow (reported to the model, not raised to the user)."""


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float
    memory_mb: int
    cpus: float
    pids: int
    tmpfs_mb: int
    output_bytes: int      # per stream (stdout, stderr) returned from one run
    artifact_bytes: int    # figures and data files persisted from one run

    def as_dict(self):
        return asdict(self)


# Absolute bounds. No configuration file, flag or approval goes beyond these.
HARD_LIMITS = Limits(timeout_seconds=600, memory_mb=32768, cpus=4, pids=256, tmpfs_mb=512,
                     output_bytes=1_000_000, artifact_bytes=16_000_000)
FLOOR = Limits(timeout_seconds=0.5, memory_mb=128, cpus=0.25, pids=16, tmpfs_mb=8,
               output_bytes=1000, artifact_bytes=0)
# Automatic checks may be configured, but only within these bounds.
SANITY_BOUNDS = Limits(timeout_seconds=30, memory_mb=4096, cpus=2, pids=64, tmpfs_mb=128,
                       output_bytes=50_000, artifact_bytes=4_000_000)
MAX_SANITY_RUNS = 10
_DEFAULT_TURN_ALLOWANCE = object()

# Memory is a cap, not a reservation: 2 GB lets a modest simulation run without crowding the host.
SANITY_DEFAULTS = Limits(timeout_seconds=5, memory_mb=2048, cpus=1, pids=64, tmpfs_mb=64,
                         output_bytes=20_000, artifact_bytes=2_000_000)
LAB_DEFAULTS = Limits(timeout_seconds=30, memory_mb=4096, cpus=2, pids=128, tmpfs_mb=256,
                      output_bytes=200_000, artifact_bytes=8_000_000)
LAB_CEILING = Limits(timeout_seconds=300, memory_mb=16384, cpus=4, pids=256, tmpfs_mb=512,
                     output_bytes=1_000_000, artifact_bytes=16_000_000)

# The Review (v4): its referees recompute constants and test counterexamples, so they get more and longer runs
# than an automatic check, never more memory or CPU and never an escalation. Runs per referee or judge (one review).
REVIEW_DEFAULTS = Limits(timeout_seconds=30, memory_mb=2048, cpus=1, pids=64, tmpfs_mb=64,
                         output_bytes=20_000, artifact_bytes=2_000_000)
REVIEW_RUNS = {'lead': 20, 'co-referee': 8, 'judge': 3}

# The only resource fields a model may mention in a tool call.
REQUESTABLE = ('timeout_seconds', 'memory_mb', 'cpus')


@dataclass(frozen=True)
class Policy:
    name: str
    defaults: Limits
    ceiling: Limits          # equal to defaults when escalation is impossible
    max_runs: int | None     # optional operator allowance for a user-requested lab
    escalation: bool         # whether the user may approve more than the defaults

    explicit_max_runs: int | None = None  # optional allowance for a user-selected sanity check
    uses_chat_run_default: bool = False

    def describe(self):
        return {'policy': self.name, 'defaults': self.defaults.as_dict(), 'max_runs': self.max_runs,
                'escalation': self.escalation,
                **({'explicit_max_runs': self.explicit_max_runs} if self.name == SANITY else {}),
                **({'ceiling': self.ceiling.as_dict()} if self.escalation else {})}


def _bounded(limits, upper, label):
    for f in fields(Limits):
        value, low, high = getattr(limits, f.name), getattr(FLOOR, f.name), getattr(upper, f.name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{label}.{f.name} must be a finite number')
        if f.type is int and type(value) is not int:
            raise ValueError(f'{label}.{f.name} must be an integer')
        if not low <= value <= high:
            raise ValueError(f'{label}.{f.name} must be between {low:g} and {high:g}')
    return limits


def _limits(base, overrides, upper, label):
    if overrides is None:
        return _bounded(base, upper, label)
    if not isinstance(overrides, dict) or set(overrides) - {f.name for f in fields(Limits)}:
        raise ValueError(f'{label} accepts only: ' + ', '.join(f.name for f in fields(Limits)))
    return _bounded(replace(base, **overrides), upper, label)


def _explicit_runs(value):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError('An explicit sandbox run allowance must be a positive integer or null')
    return value


def sanity_policy(overrides=None, max_runs=_DEFAULT_TURN_ALLOWANCE, explicit_max_runs=None):
    """The automatic policy: never escalates, configurable only within SANITY_BOUNDS."""
    uses_chat_run_default = max_runs is _DEFAULT_TURN_ALLOWANCE
    if uses_chat_run_default:
        max_runs = 3
    if max_runs is not None and (type(max_runs) is not int or not 1 <= max_runs <= MAX_SANITY_RUNS):
        raise ValueError(f'sanity.max_runs must be null or an integer from 1 to {MAX_SANITY_RUNS}')
    limits = _limits(SANITY_DEFAULTS, overrides, SANITY_BOUNDS, 'sanity')
    return Policy(SANITY, limits, limits, max_runs, escalation=False, explicit_max_runs=_explicit_runs(explicit_max_runs),
                  uses_chat_run_default=uses_chat_run_default)


def review_policy(role):
    """The policy of one referee or judge of a paper review: REVIEW_RUNS[role] runs of at most 30 s, no escalation,
    within the bounds of the automatic checks."""
    if role not in REVIEW_RUNS:
        raise ValueError('review role must be one of ' + ', '.join(REVIEW_RUNS))
    limits = _bounded(REVIEW_DEFAULTS, SANITY_BOUNDS, 'review')
    return Policy(REVIEW, limits, limits, REVIEW_RUNS[role], escalation=False)


def lab_policy(defaults=None, ceiling=None, max_runs=None):
    """The explicit policy: user-approved escalation from defaults up to the ceiling."""
    top = _limits(LAB_CEILING, ceiling, HARD_LIMITS, 'lab.ceiling')
    base = _limits(LAB_DEFAULTS, defaults, top, 'lab.defaults')
    return Policy(LAB, base, top, _explicit_runs(max_runs), escalation=True)


def load_policies(path=None):
    """Policies from an optional JSON file::

        {"sanity": {"limits": {"timeout_seconds": 5}, "max_runs": 3},
         "lab": {"defaults": {"timeout_seconds": 30}, "ceiling": {"memory_mb": 4096}, "max_runs": 20}}
    """
    config = {}
    if path is not None:
        try:
            config = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'Cannot read Python sandbox limits {path}: {exc}') from exc
        if not isinstance(config, dict) or set(config) - {'sanity', 'lab'}:
            raise ValueError('Python sandbox limits must be a JSON object with "sanity" and/or "lab"')
    sanity = config.get('sanity') or {}
    lab = config.get('lab') or {}
    if set(sanity) - {'limits', 'max_runs', 'explicit_max_runs'} or set(lab) - {'defaults', 'ceiling', 'max_runs'}:
        raise ValueError('Unknown key in Python sandbox limits; see docs/guide.md')
    return {SANITY: sanity_policy(sanity.get('limits'), sanity.get('max_runs', _DEFAULT_TURN_ALLOWANCE), sanity.get('explicit_max_runs')),
            LAB: lab_policy(lab.get('defaults'), lab.get('ceiling'), lab.get('max_runs'))}


PREVIEW_CODE_LINES = 12


def step_limit(policy, key, reached=None):
    """The largest value one escalation request may ask for: the ceiling. The user approves each request and
    sees its purpose and code, so no smaller step is imposed (it only cost extra runs to reach a needed limit)."""
    allowed = getattr(policy.ceiling, key)
    return int(allowed) if key == 'memory_mb' else allowed


def _preview(policy, raised, purpose, code):
    lines = code.strip().splitlines()
    shown = '\n'.join('  ' + line[:160] for line in lines[:PREVIEW_CODE_LINES])
    if len(lines) > PREVIEW_CODE_LINES:
        shown += f'\n  … ({len(lines) - PREVIEW_CODE_LINES} more lines)'
    return ('RUN PYTHON IN THE SANDBOX WITH MORE RESOURCES (python-lab)\n'
            + '\n'.join(f'{k}: {v:g} (default {getattr(policy.defaults, k):g}, ceiling '
                        f'{getattr(policy.ceiling, k):g})' for k, v in raised.items())
            + f'\nPurpose: {" ".join(purpose.split())[:300] or "(none given)"}'
            + (f'\nCode ({len(lines)} lines):\n{shown}' if lines else '')
            + '\nNetwork stays disabled; the code still cannot see your files.')


def resolve(policy, requested, approve=None, *, purpose='', code='', reached=None):
    """Limits for one run. ``requested`` comes from a model and is untrusted.

    Lower values are always accepted (bounded below by FLOOR). Higher values are
    refused for a policy without escalation, refused above the ceiling or above
    the step allowed now (see step_limit; ``reached`` holds limits earlier runs ran
    into), refused without a stated purpose, and otherwise need ``approve(preview)``
    to return True: a user decision taken by the interface, not by the model. The
    preview shows the purpose and the start of the code.
    """
    requested = {k: v for k, v in (requested or {}).items() if v is not None}
    if set(requested) - set(REQUESTABLE):
        raise PolicyError('Only timeout_seconds, memory_mb and cpus can be requested')
    changes, raised = {}, {}
    for key, value in requested.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise PolicyError(f'{key} must be a positive number')
        if key == 'memory_mb':
            value = int(math.ceil(value))
        value = max(value, getattr(FLOOR, key))
        default = getattr(policy.defaults, key)
        if value > default:
            if not policy.escalation:
                raise PolicyError(f'{policy.name} runs are limited to {key}={default:g}; automatic checks '
                                  'cannot raise their limits. Simplify the experiment, or ask the user to '
                                  'run it with /python-lab.')
            ceiling = getattr(policy.ceiling, key)
            if value > ceiling:
                raise PolicyError(f'{key}={value:g} exceeds the configured ceiling {ceiling:g}')
            step = step_limit(policy, key, reached)
            if value > step:
                raise PolicyError(f'Ask for at most {key}={step:g} now. A larger value can be requested after a '
                                  'run reaches the current limit; first estimate the cost with a smaller case.')
            raised[key] = value
        changes[key] = value
    if raised:
        if not isinstance(purpose, str) or not purpose.strip():
            raise PolicyError('Larger limits need a purpose: say in one sentence why this run needs them.')
        preview = _preview(policy, raised, purpose, code if isinstance(code, str) else '')
        if approve is None or not approve(preview):
            raise PolicyError('The user did not approve the larger limits. Run within the defaults: '
                              + ', '.join(f'{k}={getattr(policy.defaults, k):g}' for k in raised))
    return replace(policy.defaults, **changes)
