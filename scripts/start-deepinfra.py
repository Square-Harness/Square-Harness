#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 SquareHarness Team

"""Start the isolated GUI with DS4/DeepInfra or the existing local Qwen server."""
import argparse
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.local' / 'deepinfra'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('ds4', 'qwen'), default='ds4',
                        help='Model and budget setup (default: ds4)')
    parser.add_argument('--service-tier', choices=('standard', 'priority'),
                        help='DS4 API scheduling and pricing tier (default: standard)')
    parser.add_argument('--port', type=int, help='GUI port (default: 8780 for DS4, 8781 for Qwen)')
    parser.add_argument('--host', help='Qwen model server URL (default: http://127.0.0.1:18020)')
    parser.add_argument('--model', help='Qwen served model ID (default: qwen3.8-27b)')
    parser.add_argument('--ctx', type=int, help='Model context (default: 1048576 for DS4, 65536 for Qwen)')
    parser.add_argument('--proof-call-seconds', type=float,
                        help='DS4 common per-call working-time allowance (default: 10800 seconds)')
    parser.add_argument('--workspace', type=Path,
                        help='Research workspace (default: the existing private profile workspace)')
    parser.add_argument('--online', action='store_true',
                        help='Enable online literature tools for the local test (off by default)')
    parser.add_argument('--no-browser', action='store_true')
    options = parser.parse_args()
    ds4 = options.profile == 'ds4'
    if not ds4 and options.service_tier is not None:
        parser.error('--service-tier requires the DS4 profile')
    options.port = options.port if options.port is not None else (8780 if ds4 else 8781)
    if not 1024 <= options.port <= 65535:
        parser.error('choose a port between 1024 and 65535')
    if ds4 and (options.host is not None or options.model is not None):
        parser.error('--host and --model select the existing Qwen server; DS4 uses the pinned DeepInfra model')
    if options.proof_call_seconds is not None and (not ds4 or not math.isfinite(options.proof_call_seconds)
                                                  or options.proof_call_seconds <= 0):
        parser.error('--proof-call-seconds requires the DS4 profile and a finite positive value')
    if options.ctx is not None and options.ctx < 2048:
        parser.error('--ctx must be at least 2048')
    os.umask(0o077)
    runtime = RUNTIME if ds4 else ROOT / '.local' / 'qwen-test'
    credential = RUNTIME / 'credential'
    if ds4 and not credential.is_file():
        parser.error('run python3 scripts/setup-deepinfra.py first; never paste the key into a command')
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', options.port))
        except OSError:
            parser.error(f'port {options.port} is occupied; inspect it before starting another GUI')
    runtime.mkdir(parents=True, exist_ok=True)
    workspace = options.workspace.expanduser().resolve() if options.workspace else runtime / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(ROOT)
    for name in ('DEEPINFRA_API_KEY', 'DEEPINFRA_API_KEY_FILE', 'DEEPINFRA_ALLOW_LOOPBACK',
                 'SH_DEEPINFRA_SERVICE_TIER'):
        environment.pop(name, None)
    if ds4:
        environment['DEEPINFRA_API_KEY_FILE'] = str(credential)
        environment['SH_DEEPINFRA_SERVICE_TIER'] = options.service_tier or 'standard'
        for name in ('SQUARE_OPENAI_THINKING_FRACTION', 'SQUARE_OPENAI_STRUCTURED_THINKING_FRACTION'):
            environment.pop(name, None)
    else:
        environment.setdefault('SQUARE_OPENAI_THINKING_FRACTION', '0.75')
        environment.setdefault('SQUARE_OPENAI_STRUCTURED_THINKING_FRACTION', '0.75')
    feedback_key = ROOT / '.local' / 'feedback' / 'slack-webhook'
    if feedback_key.is_file():
        environment.setdefault('SH_FEEDBACK_WEBHOOK_FILE', str(feedback_key))
    feedback_bot = ROOT / '.local' / 'feedback' / 'slack-bot-token'
    feedback_channel = ROOT / '.local' / 'feedback' / 'slack-channel'
    if feedback_bot.is_file() and feedback_channel.is_file():
        environment.setdefault('SH_FEEDBACK_SLACK_TOKEN_FILE', str(feedback_bot))
        environment.setdefault('SH_FEEDBACK_SLACK_CHANNEL', feedback_channel.read_text().strip())
    environment.setdefault('SH_FEEDBACK_REPORT_BASE_URL', f'http://127.0.0.1:{options.port}/api/feedback')
    command = [sys.executable, '-m', 'mathagent', '--gui', '--no-browser',
               '--gui-host', '127.0.0.1', '--gui-port', str(options.port),
               '--workspace', str(workspace), '--backend', 'openai',
               '--api-profile', 'deepinfra' if ds4 else 'default', '--online' if options.online else '--offline',
               '--gui-concurrency', '5' if ds4 else '1',
               '--gui-model-concurrency', '5' if ds4 else '1',
               '--assistant-concurrency', '5' if ds4 else '1']
    model = 'deepseek-ai/DeepSeek-V4.1-Flash-1017' if ds4 else options.model or 'qwen3.8-27b'
    if ds4:
        command.append('--allow-remote-model')
        if options.proof_call_seconds is not None:
            command += ['--proof-call-seconds', str(options.proof_call_seconds)]
    else:
        command += ['--host', options.host or 'http://127.0.0.1:18020', '--model', model,
                    '--ctx', str(options.ctx or 65536), '--proof-solve-tokens', '32768',
                    '--proof-verify-tokens', '16384', '--request-timeout', '1800']
    if ds4 and options.ctx is not None:
        command += ['--ctx', str(options.ctx)]
    suffix = '' if options.port == (8780 if ds4 else 8781) else '-' + str(options.port)
    log = runtime / ('harness' + suffix + '.log')
    with log.open('ab', buffering=0) as output:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    (runtime / ('harness' + suffix + '.pid')).write_text(str(process.pid) + '\n')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        if process.poll() is not None:
            print('The GUI exited. Inspect the private log: ' + str(log), file=sys.stderr)
            return 1
        try:
            with opener.open(f'http://127.0.0.1:{options.port}/', timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.25)
    else:
        print('The GUI is still starting. Inspect the private log: ' + str(log), file=sys.stderr)
        return 1
    token_file = workspace / '.mathagent' / 'gui-token'
    token = token_file.read_text().strip()
    url = f'http://127.0.0.1:{options.port}/?token={token}'
    if not options.no_browser:
        import webbrowser
        webbrowser.open(url)
    print(f'Square Harness is running at http://127.0.0.1:{options.port}/ (PID {process.pid}).')
    print('Model: ' + model + '; web research is ' + ('on.' if options.online else 'off.'))
    if ds4:
        print('DeepInfra service tier: ' + environment['SH_DEEPINFRA_SERVICE_TIER'])
    print('Private workspace: ' + str(workspace))
    print('The authenticated Open address is in the private GUI log: ' + str(log))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
