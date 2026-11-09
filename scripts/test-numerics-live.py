#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Opt-in live check of the numerical skills with a real model and the real sandbox.

Each case runs in its own temporary workspace through the same path as a terminal
critic chat (automatic numerical-sanity-check binding), or as /python-lab for the
lab case. It records whether the model ran experiments, their statuses and its
answer, and applies light expectations (a check for false claims, none for the
trivial identity, no "proved" wording for numerical evidence). Model behaviour
varies: treat the summary as smoke evidence, not a benchmark.

  python scripts/test-numerics-live.py --host http://127.0.0.1:8000 --model square-qwen
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathagent.agent import Agent, Ollama
from mathagent.backends import OpenAICompatible
from mathagent.numerics import PythonSandbox
from mathagent.tools import Workspace

CASES = {
    'A': ('critic', 'Is it true that x^4 - 3x^2 + 1 >= 0 for every real x?', 'counterexample'),
    'B': ('critic', 'Claim: for all symmetric positive definite matrices A and B, the matrix AB + BA is positive '
                    'semidefinite. Is this correct?', 'counterexample'),
    'C': ('critic', 'Is the volume of the unit ball in R^n an increasing function of the dimension n?',
          'counterexample'),
    'D': ('critic', 'In a proof I use that g(x) = (1 - cos x)/x^2 - 1/2 + x^2/24 is strictly positive for '
                    '0 < x <= 0.01. My float64 evaluation gives negative values near x = 1e-4. Is the claim false?',
          'holds'),
    'F': ('critic', 'Is (x - y)^2 >= 0 for all real x and y?', 'skip'),
    'G': ('critic', 'Is (a + b + c)/3 >= (abc)^(1/3) for all positive reals a, b, c?', 'holds'),
    'LAB': ('python_lab', 'Simulate geometric Brownian motion dX = 0.5 X dt + 0.3 X dW with X0 = 1 up to T = 1 using '
                          'Euler-Maruyama with 10000 paths and seed 1, report the mean of X_T with its standard '
                          'error against exp(0.5), and plot five sample paths.', 'lab'),
}
OVERCLAIM = re.compile(r'\b(numerically (proved|proven|verified that it holds)|this proves|proof is complete)\b', re.I)


def run_case(name, args):
    mode, prompt, expectation = CASES[name]
    with tempfile.TemporaryDirectory() as root:
        sandbox = PythonSandbox()
        session = (sandbox.bind('python-lab', root, explicit=True, approve=lambda preview: False)
                   if mode == 'python_lab' else sandbox.automatic_session(root, ctx=args.ctx))
        if session is None:
            raise SystemExit('Sandbox unavailable: ' + str(sandbox.unavailable()))
        client = (Ollama(args.host, timeout=args.timeout) if args.backend == 'ollama'
                  else OpenAICompatible(args.host, timeout=args.timeout))
        agent = Agent(client, Workspace(root, numerics=session, read_types=()), args.model, args.ctx, args.predict,
                      think=not args.no_think, mode=mode, max_rounds=6, seed=args.seed)
        events = []
        started = time.monotonic()
        answer = agent.run(prompt, lambda kind, value: events.append((kind, value)) if kind in ('tool', 'result') else None)
        records = [session.store.load(r['id']) for r in session.store.list(limit=20)]
        tools = [v.split(' ', 1)[0] for k, v in events if k == 'tool']
    executed = len(records)
    # A check is optional when an exact argument is immediate (the skill says so); what matters is
    # a correct, nonempty verdict that never cites a failed run or calls numerics a proof.
    failed_cited = any(r['status'] != 'ok' for r in records) and bool(re.search(r'verified numerically|numerically verified|confirmed numerically', answer, re.I)) and not any(r['status'] == 'ok' for r in records)
    checks = {
        'counterexample': bool(re.search(r'counterexample|is false|claim is false|fails|not (true|correct)|\bno\b', answer[:600], re.I)),
        'holds': bool(answer.strip()) and not re.search(r'claim is false|is false\.', answer[:600], re.I),
        'skip': executed == 0 and bool(answer.strip()),
        'lab': executed >= 1 and any(r['status'] == 'ok' for r in records),
    }
    return {'case': name, 'mode': mode, 'seconds': round(time.monotonic() - started, 1), 'tools': tools,
            'experiments': [{'id': r['id'], 'status': r['status'], 'purpose': r['purpose'],
                             'error': (r.get('error') or {}).get('message'), 'code': r['code']} for r in records],
            'expectation': expectation, 'overclaim': bool(OVERCLAIM.search(answer)) or failed_cited,
            'met': checks[expectation] and bool(answer.strip()) and not failed_cited, 'answer': answer.strip()}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='http://127.0.0.1:8000')
    p.add_argument('--model', default='square-qwen')
    p.add_argument('--backend', choices=('openai', 'ollama'), default='openai')
    p.add_argument('--ctx', type=int, default=40960)
    p.add_argument('--predict', type=int, default=8192)
    p.add_argument('--timeout', type=float, default=900)
    p.add_argument('--seed', type=int, default=20261031)
    p.add_argument('--no-think', action='store_true')
    p.add_argument('--cases', default=','.join(CASES), help='Comma-separated subset of ' + ','.join(CASES))
    p.add_argument('--parallel', type=int, default=3, help='Cases run at once (the server batches them)')
    p.add_argument('--output', type=Path, help='Append one JSON line per finished case here')
    args = p.parse_args()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {pool.submit(run_case, name.strip(), args): name for name in args.cases.split(',')}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:  # Report the case and keep the others running.
                result = {'case': futures[future], 'met': False, 'error': f'{type(exc).__name__}: {exc}',
                          'seconds': 0, 'experiments': [], 'tools': [], 'overclaim': False}
            results.append(result)
            print(f"{result['case']:>3} {'met ' if result['met'] else 'MISS'} {result['seconds']:6.1f}s "
                  f"runs={len(result['experiments'])} tools={result['tools']} overclaim={result['overclaim']}"
                  + (f" error={result['error']}" if result.get('error') else ''), flush=True)
            if args.output:
                with args.output.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + '\n')
    print(f"{sum(r['met'] for r in results)}/{len(results)} expectations met")
    return 0 if all(r['met'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
