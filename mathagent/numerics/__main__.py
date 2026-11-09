# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ENPC / SquareHarness

"""Manage the Python sandbox: ``python -m mathagent.numerics <command>``.

  build              Build the pinned sandbox image
  doctor             Check Docker, the image and a test run
  list / show ID     Inspect the saved experiments of a workspace
  rerun ID           Re-execute a saved experiment with its code, seed, parameters and limits
  clean              Remove leftover sandbox containers
"""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import subprocess
import sys

from .artifacts import ExperimentStore
from .executor import IMAGE, DockerExecutor, SandboxUnavailable, docker_environment
from .policy import SANITY_DEFAULTS, Limits

DOCKER_DIR = Path(__file__).resolve().parent / 'docker'


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m mathagent.numerics', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--image', default=IMAGE, help='Sandbox image (default: %(default)s)')
    sub = p.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--platform', help='e.g. linux/amd64 or linux/arm64 (default: this machine)')
    sub.add_parser('doctor')
    for name in ('list', 'show', 'rerun'):
        command = sub.add_parser(name)
        if name != 'list':
            command.add_argument('experiment_id')
        command.add_argument('--workspace', type=Path, default=Path('.'))
    sub.add_parser('clean')
    args = p.parse_args(argv)
    executor = DockerExecutor(args.image)
    try:
        if args.command == 'build':
            command = [executor._cli(), 'build', '--tag', args.image,
                       *(['--platform', args.platform] if args.platform else []), str(DOCKER_DIR)]
            print('$ ' + ' '.join(command), flush=True)
            return subprocess.call(command, env=docker_environment())
        if args.command == 'doctor':
            print(json.dumps(executor.probe(refresh=True), indent=1))
            result = executor.run('import numpy, scipy, sympy, mpmath, matplotlib as m\n'
                                  'print(*(x.__version__ for x in (numpy, scipy, sympy, mpmath, m)))', SANITY_DEFAULTS)
            print(f'Test run: {result.status} in {result.wall_seconds:.2f} s: {result.stdout.strip()}')
            return 0 if result.ok else 1
        experiments = ExperimentStore(getattr(args, 'workspace', '.'))
        if args.command == 'list':
            for item in experiments.list():
                print(f'{item["id"]}  {item["skill"]:<24} {item["status"]:<12} {item.get("purpose") or ""}')
        elif args.command == 'show':
            print(json.dumps(experiments.load(args.experiment_id), indent=1, ensure_ascii=False))
        elif args.command == 'rerun':
            record = experiments.load(args.experiment_id)
            limits = Limits(**{f.name: record['limits'][f.name] for f in fields(Limits)})
            result = executor.run(record['code'], limits, seed=record.get('seed'), parameters=record.get('parameters'))
            saved = experiments.save(code=record['code'], result=result, limits=limits, policy=record['skill'],
                                     image=executor.probe(), seed=record.get('seed'), parameters=record.get('parameters'),
                                     purpose=f'rerun of {args.experiment_id}', explicit=True)
            print(json.dumps(result.as_dict(), indent=1, ensure_ascii=False))
            print(f'Saved {saved["id"]}')
            return 0 if result.ok else 1
        elif args.command == 'clean':
            names = executor.leftovers(mine=False)
            for name in names:
                executor.remove(name)
            print(f'Removed {len(names)} leftover sandbox container(s).')
        return 0
    except (SandboxUnavailable, ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
