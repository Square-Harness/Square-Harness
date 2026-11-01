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
MAX_LAB_RUNS = 50

# Memory is a cap, not a reservation: 2 GB lets a modest simulation run without crowding the host.
SANITY_DEFAULTS = Limits(timeout_seconds=5, memory_mb=2048, cpus=1, pids=64, tmpfs_mb=64,
                         output_bytes=20_000, artifact_bytes=2_000_000)
LAB_DEFAULTS = Limits(timeout_seconds=30, memory_mb=4096, cpus=2, pids=128, tmpfs_mb=256,
                      output_bytes=200_000, artifact_bytes=8_000_000)
LAB_CEILING = Limits(timeout_seconds=300, memory_mb=16384, cpus=4, pids=256, tmpfs_mb=512,
                     output_bytes=1_000_000, artifact_bytes=16_000_000)

# The only resource fields a model may mention in a tool call.
REQUESTABLE = ('timeout_seconds', 'memory_mb', 'cpus')


@dataclass(frozen=True)
class Policy:
    name: str
    defaults: Limits
    ceiling: Limits          # equal to defaults when escalation is impossible
    max_runs: int            # sandbox runs per check (one user turn)
    escalation: bool         # whether the user may approve more than the defaults

    def describe(self):
        return {'policy': self.name, 'defaults': self.defaults.as_dict(), 'max_runs': self.max_runs,
                'escalation': self.escalation,
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


def sanity_policy(overrides=None, max_runs=3):
    """The automatic policy: never escalates, configurable only within SANITY_BOUNDS."""
    if type(max_runs) is not int or not 1 <= max_runs <= MAX_SANITY_RUNS:
        raise ValueError(f'sanity.max_runs must be an integer from 1 to {MAX_SANITY_RUNS}')
    limits = _limits(SANITY_DEFAULTS, overrides, SANITY_BOUNDS, 'sanity')
    return Policy(SANITY, limits, limits, max_runs, escalation=False)


def lab_policy(defaults=None, ceiling=None, max_runs=20):
    """The explicit policy: user-approved escalation from defaults up to the ceiling."""
    if type(max_runs) is not int or not 1 <= max_runs <= MAX_LAB_RUNS:
        raise ValueError(f'lab.max_runs must be an integer from 1 to {MAX_LAB_RUNS}')
    top = _limits(LAB_CEILING, ceiling, HARD_LIMITS, 'lab.ceiling')
    base = _limits(LAB_DEFAULTS, defaults, top, 'lab.defaults')
    return Policy(LAB, base, top, max_runs, escalation=True)


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
    if set(sanity) - {'limits', 'max_runs'} or set(lab) - {'defaults', 'ceiling', 'max_runs'}:
        raise ValueError('Unknown key in Python sandbox limits; see docs/guide.md')
    return {SANITY: sanity_policy(sanity.get('limits'), sanity.get('max_runs', 3)),
            LAB: lab_policy(lab.get('defaults'), lab.get('ceiling'), lab.get('max_runs', 20))}


def resolve(policy, requested, approve=None):
    """Limits for one run. ``requested`` comes from a model and is untrusted.

    Lower values are always accepted (bounded below by FLOOR). Higher values are
    refused for a policy without escalation, refused above the ceiling, and
    otherwise need ``approve(preview)`` to return True: a user decision taken
    by the interface, not by the model.
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
            raised[key] = value
        changes[key] = value
    if raised:
        preview = ('RUN PYTHON IN THE SANDBOX WITH MORE RESOURCES (python-lab)\n'
                   + '\n'.join(f'{k}: {v:g} (default {getattr(policy.defaults, k):g}, ceiling '
                               f'{getattr(policy.ceiling, k):g})' for k, v in raised.items())
                   + '\nNetwork stays disabled; the code still cannot see your files.')
        if approve is None or not approve(preview):
            raise PolicyError('The user did not approve the larger limits. Run within the defaults: '
                              + ', '.join(f'{k}={getattr(policy.defaults, k):g}' for k in raised))
    return replace(policy.defaults, **changes)
