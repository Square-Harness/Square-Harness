# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Shared Docker sandbox for the python-lab and numerical-sanity-check skills.

* ``executor`` runs one untrusted script in a locked-down, throwaway container.
* ``policy`` holds the per-skill limits, enforced by harness code.
* ``artifacts`` keeps bounded experiment records in ``.mathagent/experiments/``.
* ``session`` binds a skill and its policy to the model-facing tools.

Trusted callers (scripts, tests) can use :func:`execute_python` directly.
"""
from .executor import IMAGE, DockerExecutor, ExperimentResult, SandboxUnavailable
from .policy import HARD_LIMITS, LAB, SANITY, SANITY_DEFAULTS, Limits, PolicyError
from .session import NumericsSession, PythonSandbox

__all__ = ['IMAGE', 'DockerExecutor', 'ExperimentResult', 'SandboxUnavailable', 'Limits', 'PolicyError',
           'NumericsSession', 'PythonSandbox', 'LAB', 'SANITY', 'execute_python']

_default = None


def execute_python(code, timeout_seconds=SANITY_DEFAULTS.timeout_seconds, *, memory_mb=SANITY_DEFAULTS.memory_mb,
                   cpus=SANITY_DEFAULTS.cpus, seed=None, parameters=None, image=IMAGE, cancel=None):
    """Run ``code`` in the sandbox and return an :class:`ExperimentResult`.

    For trusted callers only: limits are bounded by ``HARD_LIMITS`` but not by a
    skill policy. Model tool calls go through :class:`NumericsSession` instead.
    """
    global _default
    from dataclasses import replace
    limits = replace(SANITY_DEFAULTS, timeout_seconds=timeout_seconds, memory_mb=memory_mb, cpus=cpus)
    for name in ('timeout_seconds', 'memory_mb', 'cpus'):
        if not 0 < getattr(limits, name) <= getattr(HARD_LIMITS, name):
            raise ValueError(f'{name} must be positive and at most {getattr(HARD_LIMITS, name)}')
    if _default is None or _default.image != image:
        _default = DockerExecutor(image)
    return _default.run(code, limits, seed=seed, parameters=parameters, cancel=cancel)
