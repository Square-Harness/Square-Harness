"""Manage the Python sandbox: ``python -m mathagent.numerics <command>``.

  build                 Build the sandbox image from the pinned Dockerfile
  doctor                Check Docker, the image and a tiny run
  run FILE              Run a script in the sandbox (sanity or --lab limits)
  rerun ID              Re-execute a saved experiment with its code, seed and limits
  list / show ID        Inspect saved experiments of a workspace
  clean                 Remove leftover sandbox containers
"""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import subprocess
import sys

from .artifacts import ExperimentStore
from .executor import CONTAINER_LABEL, IMAGE, DockerExecutor, SandboxUnavailable, docker_environment
from .policy import LAB, Limits, load_policies

DOCKER_DIR = Path(__file__).resolve().parent / 'docker'


def _print_result(result):
    shown = result.as_dict()
    print(json.dumps(shown, indent=1, ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m mathagent.numerics', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--image', default=IMAGE, help='Sandbox image tag (default: %(default)s)')
    p.add_argument('--limits', metavar='FILE', help='JSON limits file, as for --python-limits')
    sub = p.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build', help='Build the sandbox image')
    build.add_argument('--platform', help='e.g. linux/amd64 or linux/arm64 (default: this machine)')
    build.add_argument('--no-cache', action='store_true')
    sub.add_parser('doctor', help='Check Docker, the image and a tiny run')
    run = sub.add_parser('run', help='Run a Python file in the sandbox')
    run.add_argument('file', type=Path)
    run.add_argument('--lab', action='store_true', help='Use python-lab default limits instead of sanity-check limits')
    run.add_argument('--seed', type=int)
    run.add_argument('--workspace', type=Path, help='Also save an experiment record in this workspace')
    rerun = sub.add_parser('rerun', help='Re-execute a saved experiment')
    rerun.add_argument('experiment_id')
    rerun.add_argument('--workspace', type=Path, default=Path('.'))
    listing = sub.add_parser('list', help='List saved experiments')
    listing.add_argument('--workspace', type=Path, default=Path('.'))
    show = sub.add_parser('show', help='Show a saved experiment record')
    show.add_argument('experiment_id')
    show.add_argument('--workspace', type=Path, default=Path('.'))
    sub.add_parser('clean', help='Remove leftover sandbox containers')
    args = p.parse_args(argv)
    executor = DockerExecutor(args.image)

    try:
        if args.command == 'build':
            command = [executor._cli(), 'build', '--tag', args.image]
            if args.platform:
                command += ['--platform', args.platform]
            if args.no_cache:
                command.append('--no-cache')
            command.append(str(DOCKER_DIR))
            print('$ ' + ' '.join(command), flush=True)
            return subprocess.call(command, env=docker_environment())
        if args.command == 'doctor':
            info = executor.probe(refresh=True)
            print(json.dumps(info, indent=1))
            policies = load_policies(args.limits)
            result = executor.run('import numpy, scipy, sympy, mpmath, matplotlib\n'
                                  'print(numpy.__version__, scipy.__version__, sympy.__version__, '
                                  'mpmath.__version__, matplotlib.__version__)', policies['numerical-sanity-check'].defaults)
            print(f'Test run: {result.status} in {result.wall_seconds:.2f} s: {result.stdout.strip()}')
            return 0 if result.ok else 1
        if args.command in ('run', 'rerun'):
            policies = load_policies(args.limits)
            if args.command == 'run':
                code = args.file.read_text(encoding='utf-8')
                policy = policies[LAB if args.lab else 'numerical-sanity-check']
                limits, seed, parameters, purpose = policy.defaults, args.seed, {}, f'run {args.file.name}'
            else:
                record = ExperimentStore(args.workspace).load(args.experiment_id)
                code, seed, parameters = record['code'], record.get('seed'), record.get('parameters') or {}
                limits = Limits(**{f.name: record['limits'][f.name] for f in fields(Limits)})
                policy = policies[record['skill']]
                purpose = f'rerun of {args.experiment_id}'
            result = executor.run(code, limits, seed=seed, parameters=parameters)
            _print_result(result)
            workspace = args.workspace
            if workspace is not None:
                saved = ExperimentStore(workspace).save(code=code, result=result, limits=limits, policy=policy.name,
                                                        image=executor.probe(), seed=seed, parameters=parameters,
                                                        purpose=purpose, explicit=True)
                print(f'Saved {saved["id"]} in {ExperimentStore(workspace).directory}')
            return 0 if result.ok else 1
        if args.command == 'list':
            for item in ExperimentStore(args.workspace).list():
                print(f'{item["id"]}  {item["skill"]:<24} {item["status"]:<12} {item.get("purpose") or ""}')
            return 0
        if args.command == 'show':
            print(json.dumps(ExperimentStore(args.workspace).load(args.experiment_id), indent=1, ensure_ascii=False))
            return 0
        if args.command == 'clean':
            names = executor.leftovers(mine=False)
            for name in names:
                executor.remove(name)
            print(f'Removed {len(names)} leftover sandbox container(s) labelled {CONTAINER_LABEL}.')
            return 0
    except (SandboxUnavailable, ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
