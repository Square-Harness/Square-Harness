# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Local web server for the visual interface (standard library only).

The security model matches the terminal interface:
- loopback only unless --gui-host explicitly selects another address;
- every /api request needs the workspace's access token (cookie or bearer);
- loopback mode also checks the Host header, against DNS rebinding;
- state-changing requests must be same-origin JSON and get no CORS answer;
- a strict Content-Security-Policy stops rendered model or source text from
  loading anything outside this server, so a hostile document cannot leak
  data through an image or script address;
- network and Python permissions stay fixed at launch; proof jobs use
  pinned text and no tools, as in the terminal.
"""
import errno
import hmac
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import stat
import sys
import threading
import unicodedata
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import webbrowser

from .. import __version__
from ..agent import AgentError
from ..mode_calls import validated_mode_call
from ..ledger import _directory
from ..proof import MAX_ADD_ROUNDS, MAX_ADD_TOKENS
from ..model_limits import HOSTED_DS4
from .. import images
from . import effort, personalization, projects, store
from .hub import Busy, Hub
from .watch import Watcher

VERSION = __version__
COOKIE = 'square_token'  # suffixed with the port: cookies ignore ports, workspaces must not collide
UUID = r'(?P<id>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})'
STATIC = Path(__file__).with_name('static')


def build_id():
    """The interface bundle being served; an open page with another one reloads itself."""
    try:
        match = re.search(r'assets/(index-[\w-]+\.js)', (STATIC / 'index.html').read_text(encoding='utf-8'))
    except OSError:
        return ''
    return match[1] if match else ''
MAX_BODY = 1_000_000
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.css': 'text/css; charset=utf-8', '.json': 'application/json',
         '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml', '.png': 'image/png',
         '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf',
         '.txt': 'text/plain; charset=utf-8', '.map': 'application/json'}
FALLBACK = b'''<!doctype html><meta charset="utf-8"><title>Square Harness</title>
<body style="font:16px system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>Interface assets are missing</h1><p>This checkout has no built interface. Build it with
<code>npm install &amp;&amp; npm run build</code> inside <code>frontend/</code>, then reload.</p>'''

ROUTES = []


def route(method, pattern, auth=True, raw=False, body_limit=MAX_BODY):
    def register(function):
        function.raw_body = raw
        function.body_limit = body_limit
        ROUTES.append((method, re.compile(pattern + r'\Z'), function, auth))
        return function
    return register


class Streamed:
    """Marker: the handler already wrote a streaming response."""


class Unauthorized(Exception):
    """A login attempt used the wrong access token."""


# How a limit is named in the forms, for messages about a request's fields.
LABELS = {'ctx': 'Context', 'rounds': 'Attempts', 'tokens': 'Total output', 'solve_tokens': 'Solve ceiling',
          'verify_tokens': 'Review ceiling', 'input_tokens': 'Total input', 'requests': 'Search requests',
          'chars': 'Source characters', 'add_tokens': 'Extra output tokens', 'add_rounds': 'Extra attempts'}


def _int(body, key, low=None, high=None):
    value = body.get(key)
    if value is None:
        return None
    if type(value) is not int or (low is not None and value < low) or (high is not None and value > high):
        unit = ' tokens' if key == 'ctx' or key.endswith('tokens') else ''
        rule = (f' from {low:,} to {high:,}{unit}' if low is not None and high is not None else
                f' at least {low:,}{unit}' if low is not None else f' at most {high:,}{unit}' if high is not None else '')
        raise ValueError(f'{LABELS.get(key, key)} must be ' + ('a whole number' + (',' + rule if rule else '')
                                                             if type(value) is not int else rule.strip()))
    return value


def _added_seconds(body, key):
    """An explicit extra working time on resume: 0 (none) up to one day."""
    value = body.get(key)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 86400:
        raise ValueError(f'{key} must be a number of seconds from 0 to 86400')
    return value


def _seconds(body, key):
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{key} must be a positive number of seconds')
    return float(value)


def _flag(body, key):
    value = body.get(key)
    if value is not None and type(value) is not bool:
        raise ValueError(f'{key} must be true or false')
    return value


def _files(body, key='source_files'):
    value = body.get(key) or []
    if not isinstance(value, list) or len(value) > 20 or any(not isinstance(v, str) or not v or len(v) > 1000 for v in value):
        raise ValueError('Pinned files must be a list of at most 20 workspace-relative paths')
    return value


def _text(body, key, limit):
    value = body.get(key)
    # Text made only of control characters and spaces is as empty as whitespace: refuse it before any model call.
    if not isinstance(value, str) or not ''.join(ch for ch in value if unicodedata.category(ch) != 'Cc').strip():
        raise ValueError(f'Provide {key}')
    if len(value) > limit:
        raise ValueError(f'{key} is limited to {limit} characters')
    return value


# -- routes -------------------------------------------------------------------

@route('GET', '/api/projects')
def project_list(h, body):
    return {'projects': h.server.hub.list_projects()}


@route('POST', '/api/projects')
def project_create(h, body):
    hub = h.server.hub
    result = hub.create_project(body.get('name'), body.get('notes'))
    hub.bus.publish('projects')
    return {'project': result}


@route('GET', '/api/projects/' + UUID)
def project_read(h, body, id):
    try:
        return {'project': projects.read_project(h.server.hub.root, id)}
    except FileNotFoundError:
        raise store.NotFound('Unknown project') from None


@route('DELETE', '/api/projects/' + UUID)
def project_delete(h, body, id):
    try:
        return h.server.hub.delete_project(id)
    except FileNotFoundError:
        raise store.NotFound('Unknown project') from None


@route('GET', '/api/projects/' + UUID + '/files')
def project_files(h, body, id):
    try:
        return projects.list_files(h.server.hub.root, id)
    except FileNotFoundError:
        raise store.NotFound('Unknown project') from None


@route('POST', '/api/projects/' + UUID)
def project_update(h, body, id):
    try:
        hub = h.server.hub
        result = hub.update_project(id, revision=body.get('revision'),
                                    name=body.get('name'), notes=body.get('notes'))
        hub.bus.publish('projects')
        return {'project': result}
    except FileNotFoundError:
        raise store.NotFound('Unknown project') from None


@route('GET', '/api/feedback/config')
def feedback_config(h, body):
    from . import feedback
    return feedback.config()


@route('POST', '/api/feedback/preview')
def feedback_preview(h, body):
    from . import feedback
    return feedback.preview(h.server.hub.root, body.get('chat_id'), (h.server.token,))


@route('POST', '/api/feedback/export')
def feedback_export(h, body):
    from . import feedback
    data = feedback.archive(h.server.hub.root, body.get('chat_id'), (h.server.token,))
    h.send_bytes(200, data, 'application/zip', [('Content-Disposition', 'attachment; filename="conversation-diagnostics.zip"'),
                                              ('Cache-Control', 'private, no-store')])
    return Streamed


@route('POST', '/api/feedback', body_limit=16 * 1024 * 1024)
def feedback_submit(h, body):
    from . import feedback
    hub = h.server.hub
    metadata = {'version': VERSION, 'build': build_id(), 'model': hub.args.model,
                'api_profile': getattr(hub.args, 'api_profile', 'default'),
                'service_tier': (os.environ.get('SH_DEEPINFRA_SERVICE_TIER', 'standard')
                                 if getattr(hub.args, 'api_profile', 'default') == 'deepinfra' else 'standard')}
    # Hosted gateways overwrite this header with the verified Access identity.
    # A local backend is a single trusted workspace and has no account identity.
    email = h.headers.get('X-SH-User-Email', '')
    if email and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) and len(email) <= 254:
        metadata['reporter_email'] = email
    return feedback.save(hub.root, body, metadata, (h.server.token,))


@route('GET', '/api/feedback/' + UUID + '/archive')
def feedback_archive(h, body, id):
    from . import feedback
    data = feedback.read_archive(h.server.hub.root, id)
    h.send_bytes(200, data, 'application/zip', [('Content-Disposition', f'attachment; filename="feedback-{id}.zip"'),
                                              ('Cache-Control', 'private, no-store')])
    return Streamed


@route('GET', '/api/account')
def account(h, body):
    """No account locally. The hosted gateway answers this path itself with the signed-in email."""
    return {}


@route('GET', '/api/status')
def status(h, body):
    hub, args = h.server.hub, h.server.hub.args
    from ..model_limits import PROFILE, effort_presets
    api_profile = getattr(args, 'api_profile', 'default')
    out = {'version': VERSION, 'build': build_id(), 'instance': hub.bus.instance, 'seq': hub.bus.seq,
           'workspace': str(hub.root), 'model': args.model, 'host': args.host, 'backend': args.backend,
           'model_default': bool(getattr(args, 'model_default', False)),
           # The default for new work in the interface: online unless launched --offline.
           'online': hub.online(), 'online_locked': hub.online_locked,
           'image_support': hub.image_support(),
           'allow_python': bool(args.allow_python), 'lan': not h.server.loopback,
           'concurrency': hub.limits(),
           'defaults': {'ctx': args.ctx, 'predict': args.predict, 'think': not args.no_think,
                        'api_profile': api_profile,
                        'budget_profile': PROFILE if api_profile in HOSTED_DS4 else 'default',
                        'effort_presets': effort_presets(args) if api_profile in HOSTED_DS4 else None,
                        'literature_presets': {'high': effort.limits('literature', 'high', args)},
                        'proof_rounds': args.proof_rounds, 'proof_tokens': args.proof_tokens,
                        'proof_seconds': args.proof_seconds, 'proof_solve_tokens': args.proof_solve_tokens,
                        'proof_call_seconds': getattr(args, 'proof_call_seconds', None),
                        'proof_verify_tokens': args.proof_verify_tokens,
                        'proof_repair_tokens': args.proof_repair_tokens,
                        'proof_min_solve_tokens': args.proof_min_solve_tokens,
                        'research_rounds': args.research_rounds, 'research_tokens': args.research_tokens,
                        'research_input_tokens': args.research_input_tokens,
                        'research_seconds': args.research_seconds, 'research_requests': args.research_requests,
                        'research_chars': args.research_chars},
           'ollama': hub.models(), 'root': str(hub.base),
           'workspace_path': '' if hub.root == hub.base else hub.root.relative_to(hub.base).as_posix(),
           'read': store.load_settings(hub.root)['read'], 'latex': shutil.which('latexmk') is not None,
           'recent': store.recent_folders(hub.base)}
    if os.environ.get('SH_DEEPINFRA_QUOTA_FILE'):
        from ..hosted_quota import quota_status
        out['tester_allowance'] = quota_status()
    if not h.server.loopback:
        # Only an authenticated device can read the pairing addresses.
        out['pair_urls'] = [url + '/?' + urlencode({'token': h.server.token}) for url in h.server.urls]
    return out


@route('POST', '/api/login', auth=False)
def login(h, body):
    token = body.get('token')
    if not isinstance(token, str) or not hmac.compare_digest(token.strip().encode(), h.server.token.encode()):
        raise Unauthorized('That access token is not valid for this workspace')
    h.cookie = h.server.token
    return {'ok': True}


@route('GET', '/api/events')
def events(h, body):
    bus = h.server.hub.bus
    after, resync = bus.seq, False
    last = h.headers.get('Last-Event-ID', '')
    if ':' in last:
        instance, _, number = last.partition(':')
        if instance == bus.instance and number.isdigit():
            after = int(number)
        else:
            resync = True
    h.send_response(200)
    h.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    h.send_header('Cache-Control', 'no-store')
    h.send_header('X-Accel-Buffering', 'no')
    h.security_headers()
    h.end_headers()
    h.close_connection = True

    def send(event):
        data = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        h.wfile.write(f'id: {bus.instance}:{event.get("seq", after)}\ndata: {data}\n\n'.encode())

    h.wfile.write(b'retry: 2000\n\n')
    send({'type': 'hello', 'instance': bus.instance, 'build': build_id(), 'seq': after, 'resync': resync})
    h.wfile.flush()
    while not h.server.stopping.is_set():
        batch, missed = bus.wait(after, 15)
        if missed:
            after = bus.seq
            send({'type': 'hello', 'instance': bus.instance, 'build': build_id(), 'seq': after, 'resync': True})
        elif not batch:
            h.wfile.write(b': ping\n\n')
        for event in batch:
            send(event)
            after = event['seq']
        h.wfile.flush()
    return Streamed


@route('GET', '/api/task')
def task(h, body):
    return h.server.hub.snapshot()


@route('POST', '/api/task/pause')
def pause(h, body):
    task = body.get('task')
    if task is not None and (not isinstance(task, str) or not re.fullmatch(r'[0-9a-f]{12}', task)):
        raise ValueError('Give a valid task ID to pause.')
    investigation, chat = body.get('investigation_id'), body.get('chat_id')
    if investigation is not None or chat is not None:
        if (not isinstance(investigation, str) or not re.fullmatch('[0-9a-f]{32}', investigation)
                or not isinstance(chat, str) or not store._ID.fullmatch(chat) or not task):
            raise ValueError('A guarded pause needs the task, conversation and investigation IDs')
    return {'task': h.server.hub.pause(task, investigation_id=investigation, chat_id=chat).summary()}


@route('POST', '/api/task/kill')
def kill(h, body):
    task = body.get('task')
    if not isinstance(task, str) or not re.fullmatch(r'[0-9a-f]{12}', task):
        raise ValueError('Give a valid task ID to stop.')
    return {'task': h.server.hub.kill(task).summary()}


@route('POST', '/api/concurrency')
def concurrency(h, body):
    if not body or set(body) - {'chats', 'requests'}:
        raise ValueError('Give chats or requests as whole numbers.')
    if any(type(value) is not int for value in body.values()):
        raise ValueError('Concurrency limits must be whole numbers.')
    return {'concurrency': h.server.hub.set_concurrency(**body)}


@route('POST', '/api/approvals/(?P<approval>[0-9a-f]{32})')
def approval(h, body, approval):
    decision = _flag(body, 'approve')
    if decision is None:
        raise ValueError('approve must be true or false')
    h.server.hub.decide(approval, decision)
    return {'ok': True}


@route('GET', '/api/files')
def files(h, body):
    records = store.list_files(h.server.hub.root, limit=2001)
    return {'files': records[:2000], 'truncated': len(records) > 2000}


@route('POST', '/api/images/preview')
def image_preview(h, body):
    paths = _files(body, 'files')
    if any(not images.is_image(path) for path in paths):
        raise ValueError('Image previews accept only PNG, JPEG or WebP files')
    return {'images': images.snapshot(h.server.hub.root, paths)}


@route('GET', r'/api/images/(?P<sha>[a-f0-9]{64})')
def image_snapshot(h, body, sha):
    data, mime = images.read(h.server.hub.root, sha)
    h.send_bytes(200, data, mime, [('Cache-Control', 'private, no-store'),
                                  ('Content-Disposition', 'inline')])
    return Streamed


_RECORD_FIELDS = ('id', 'job_id', 'chat_id', 'scope', 'folder', 'mode', 'kind', 'status', 'direction', 'qualifiers',
                  'summary', 'created_at', 'evidence')


def _record_page(view):
    """The fields the interface reads. The question of a literature review is sent in full with its first
    source; its other sources carry the opening words, which is all the interface shows beside a source."""
    told, entries = set(), []
    for entry in view['entries']:
        job, text = (entry['folder'], entry['job_id']), entry['task']['text']
        room = 200 if entry['kind'] == 'source' and job in told else 2000
        told.add(job)
        task = {'text': text[:room], 'text_truncated': True} if len(text) > room else {'text': text}
        documents = [{'document_id': doc['document_id'], 'title': doc.get('title') or '',
                      'passages': [{'range': passage['range'], 'label': passage.get('label') or ''}
                                   for passage in doc['passages']]} for doc in entry['documents']]
        entries.append({**{field: entry[field] for field in _RECORD_FIELDS}, 'task': task, 'documents': documents})
    return {'scope': view['scope'], 'entries': entries, 'scopes': view['scopes']}


@route('GET', '/api/index')
def research_index(h, body):
    from ..project_index import digest
    if set(h.query) - {'chat', 'project'} or any(len(values) != 1 for values in h.query.values()):
        raise ValueError('Use one chat or project identifier')
    page = _record_page(store.index_view(h.server.hub.root, findings=True,
                                         **{key: values[0] for key, values in h.query.items()}))
    revision = digest(json.dumps(page, ensure_ascii=False, sort_keys=True))[:32]
    headers = [('Cache-Control', 'no-store'), ('ETag', f'"{revision}"')]
    known = [tag.strip().removeprefix('W/') for tag in h.headers.get('If-None-Match', '').split(',')]
    if f'"{revision}"' in known:
        # The record the client holds is current: no body.
        h.send_response(304)
        h.security_headers()
        for key, value in headers:
            h.send_header(key, value)
        h.end_headers()
    else:
        h.send_bytes(200, json.dumps({'revision': revision, **page}, ensure_ascii=False).encode('utf-8'),
                     'application/json; charset=utf-8', headers)
    return Streamed


@route('GET', '/api/proofs')
def proofs(h, body):
    return {'jobs': store.list_proofs(h.server.hub.root, h.active('proof'))}


@route('POST', '/api/proofs')
def proof_start(h, body):
    unknown = set(body) - {'goal', 'source_files', 'rounds', 'tokens', 'seconds', 'solve_tokens', 'verify_tokens', 'ctx', 'queue', 'literature'}
    if unknown:
        raise ValueError('Unknown proof options: ' + ', '.join(sorted(unknown)))
    if _flag(body, 'literature'):
        raise ValueError('Proof jobs use the pinned text only, with no tools: pin the sources the proof needs')
    task = h.server.hub.start_proof(
        _text(body, 'goal', 60000), source_files=_files(body),
        rounds=_int(body, 'rounds', 1, 100), tokens=_int(body, 'tokens', 512),
        seconds=_seconds(body, 'seconds'), solve_tokens=_int(body, 'solve_tokens', 128, 1_000_000),
        verify_tokens=_int(body, 'verify_tokens', 128, 1_000_000), ctx=_int(body, 'ctx', 2048),
        queue=bool(_flag(body, 'queue')))
    return {'task': task.summary(), 'id': task.target}


def _owner_task(hub, job_id):
    with hub._lock:
        for task in hub._tasks.values():
            if task.kind != 'chat' or task.state not in ('starting', 'running', 'pausing'):
                continue
            try:
                chat = store.load_chat(hub.root, task.target)
            except (ValueError, OSError, store.NotFound):
                continue
            if any(c.get('job_id') == job_id and c.get('status') == 'running' for c in (chat.get('assistant_state') or {}).get('children', [])):
                return {**task.summary(), 'chat': task.target, 'title': chat.get('title') or task.title}
    return None


@route('GET', '/api/proofs/' + UUID)
def proof(h, body, id):
    result = store.proof_detail(h.server.hub.root, id, h.active('proof'))
    result['owner_task'] = _owner_task(h.server.hub, id)
    return result


@route('POST', '/api/proofs/' + UUID + '/resume')
def proof_resume(h, body, id):
    unknown = set(body) - {'queue', 'add_tokens', 'add_seconds', 'add_rounds'}
    if unknown:
        raise ValueError('Unknown resume options: ' + ', '.join(sorted(unknown)))
    task = h.server.hub.resume_proof(id, queue=bool(_flag(body, 'queue')),
                                     add_tokens=_int(body, 'add_tokens', 0, MAX_ADD_TOKENS) or 0,
                                     add_seconds=_added_seconds(body, 'add_seconds'),
                                     add_rounds=_int(body, 'add_rounds', 0, MAX_ADD_ROUNDS) or 0)
    return {'task': task.summary(), 'id': id}


@route('DELETE', '/api/proofs/' + UUID)
def proof_delete(h, body, id):
    return h.server.hub.delete_proof(id)


@route('GET', '/api/proofs/' + UUID + '/report')
def proof_report(h, body, id):
    return store.proof_report(h.server.hub.root, id)


@route('GET', '/api/proofs/' + UUID + '/sources')
def proof_sources(h, body, id):
    return {'sources': store.proof_sources(h.server.hub.root, id)}


@route('GET', '/api/proofs/' + UUID + '/artifacts/(?P<name>[A-Za-z0-9_.-]+)')
def proof_artifact(h, body, id, name):
    return store.proof_artifact(h.server.hub.root, id, name)


@route('GET', '/api/proofs/' + UUID + '/stream/(?P<name>[A-Za-z0-9_.-]+)')
def proof_stream(h, body, id, name):
    return store.proof_stream(h.server.hub.root, id, name, h.offset())


@route('GET', '/api/research')
def researches(h, body):
    return {'jobs': store.list_research(h.server.hub.root, h.active('research'))}


@route('POST', '/api/research')
def research_start(h, body):
    variant = body.get('variant')
    if variant is not None and variant not in ('quick', 'review', 'journal', 'typos', 'explain'):
        raise ValueError('variant must be quick, review, typos or explain (journal, the former detailed review, is the review)')
    target = body.get('target') or ''
    if not isinstance(target, str) or len(target) > 200:
        raise ValueError('target is a short text, at most 200 characters')
    task = h.server.hub.start_research(
        body.get('kind'), _text(body, 'goal', 20000), source_files=_files(body), variant=variant, target=target,
        rounds=_int(body, 'rounds', 1, 100), tokens=_int(body, 'tokens', 1024),
        input_tokens=_int(body, 'input_tokens', 2048), seconds=_seconds(body, 'seconds'),
        requests=_int(body, 'requests', 0, 1000), chars=_int(body, 'chars', 1000, 1_000_000),
        online=_flag(body, 'online'), queue=bool(_flag(body, 'queue')))
    return {'task': task.summary(), 'id': task.target}


@route('GET', '/api/research/' + UUID)
def research(h, body, id):
    result = store.research_detail(h.server.hub.root, id, h.active('research'))
    result['owner_task'] = _owner_task(h.server.hub, id)
    return result


@route('POST', '/api/research/' + UUID + '/resume')
def research_resume(h, body, id):
    if body.get('add_rounds') is not None:
        raise ValueError('add_rounds applies to proofs; a research job takes add_tokens and add_seconds')
    if body.get('add_seconds') is not None and body.get('additional_seconds') is not None:
        raise ValueError('Give add_seconds or additional_seconds, not both')
    seconds = _added_seconds(body, 'add_seconds') or _seconds(body, 'additional_seconds') or 0
    task = h.server.hub.resume_research(id, queue=bool(_flag(body, 'queue')), additional_seconds=seconds,
                                        additional_tokens=_int(body, 'add_tokens', 0, MAX_ADD_TOKENS) or 0)
    return {'task': task.summary(), 'id': id}


@route('GET', '/api/research/' + UUID + '/review-evidence')
def review_evidence(h, body, id):
    return {'items': store.review_evidence(h.server.hub.root, id)}


@route('GET', '/api/research/' + UUID + '/sources')
def research_sources(h, body, id):
    return {'sources': store.research_sources(h.server.hub.root, id)}


@route('GET', '/api/research/' + UUID + '/artifacts/(?P<name>[A-Za-z0-9_.-]+)')
def research_artifact(h, body, id, name):
    return store.research_artifact(h.server.hub.root, id, name)


@route('GET', '/api/research/' + UUID + '/stream/(?P<name>[A-Za-z0-9_.-]+)')
def research_stream(h, body, id, name):
    return store.research_artifact(h.server.hub.root, id, name, h.offset())


@route('GET', '/api/chats')
def chats(h, body):
    hub = h.server.hub
    query = (h.query.get('q') or [''])[0].strip()
    if len(query) > 200:
        raise ValueError('Search for at most 200 characters')
    search = {} if query else None
    rows = store.list_chats(hub.root, hub.base, query=query or None, search=search)
    live = {t.target for t in hub.active_tasks() if t.kind == 'chat'}
    for row in rows:
        if row['id'] not in live:
            row['agent_statuses'] = []
    return {'chats': rows, **({'truncated': True} if search and search.get('truncated') else {})}


@route('POST', '/api/chats')
def chat_create(h, body):
    hub = h.server.hub
    think, online = _flag(body, 'think'), _flag(body, 'online')
    chat = store.create_chat(hub.root, body.get('mode'), (not hub.args.no_think) if think is None else think,
                             hub.args.model, hub.online(online), project_id=body.get('project_id'))
    hub.bus.publish('chat_saved', chat=chat['id'])
    return _chat_view(chat, hub.root)


@route('GET', '/api/chats/' + UUID + r'/experiments/(?P<experiment>exp-\d{8}-\d{6}-[0-9a-f]{6})')
def chat_experiment(h, body, id, experiment):
    return store.saved_chat_experiment(h.server.hub.root, id, experiment)


@route('GET', '/api/chats/' + UUID + '/checks/' + UUID.replace('P<id>', 'P<job_id>'))
def chat_check_report(h, body, id, job_id):
    return store.saved_chat_check(h.server.hub.root, id, job_id)


@route('GET', '/api/chats/' + UUID + '/answers/' + UUID.replace('P<id>', 'P<answer_id>'))
def chat_answer(h, body, id, answer_id):
    return store.saved_chat_answer(h.server.hub.root, id, answer_id)


@route('GET', '/api/chats/' + UUID)
def chat(h, body, id):
    hub = h.server.hub
    saved = store.load_chat(hub.root, id)
    result = _chat_view(saved, hub.root)
    result['math_macros'] = store.chat_macros(hub.root, saved, hub.base)
    snapshot = hub.snapshot()
    result['queued_followup'] = next(({'task': task['id'], 'content': task['title']} for task in snapshot['queue']
                                      if task.get('followup') and task['target'] == id), None)
    if any(task['target'] == id and task['state'] == 'queued' and not task.get('followup') for task in snapshot['queue']):
        # A queued turn has not saved its state yet: the previous turn's status would show (B8).
        result['assistant_status'], result['assistant_resumable'], result['assistant_progress'] = 'queued', False, None
    elif result['assistant_status'] == 'running':
        tasks = [entry['task'] for entry in snapshot['tasks']] + snapshot['queue']
        if not any(task['target'] == id and task['state'] in ('starting', 'running', 'pausing', 'queued') for task in tasks):
            result['assistant_status'] = 'paused'
            result['waiting_for_results'] = []
            turn = (saved.get('assistant_state') or {}).get('id')
            for lane in result['agents']:
                if lane['turn_id'] == turn and lane['status'] in ('running', 'waiting'):
                    lane['status'] = 'stopped'
            if result['investigation'] and (saved.get('assistant_state') or {}).get('investigation'):
                from ..investigation import TERMINAL
                result['investigation']['can_stop'] = result['investigation']['phase'] not in TERMINAL
                for step in result['investigation']['tasks']:
                    if step['status'] == 'running':  # its worker died with the process (a graceful stop says so itself)
                        step['status'] = 'interrupted'
            tokens = result['assistant_budget']['tokens']
            tokens['uncertain'] += tokens['reserved']
            tokens['reserved'] = 0
    return result


@route('DELETE', '/api/chats/' + UUID)
def chat_delete(h, body, id):
    return h.server.hub.delete_chat(id)


@route('POST', '/api/chats/' + UUID + '/title')
def chat_title(h, body, id):
    hub = h.server.hub
    saved = store.set_chat_title(hub.root, id, _text(body, 'title', 120))
    hub.bus.publish('chat_saved', chat=id)
    return _chat_view(saved, hub.root)


@route('POST', '/api/chats/' + UUID + '/project')
def chat_project(h, body, id):
    hub = h.server.hub
    if 'project_id' not in body:
        raise ValueError('Give project_id: a project ID, or null to remove the conversation from its project')
    return _chat_view(hub.set_chat_project(id, body['project_id']), hub.root)


@route('POST', '/api/chats/' + UUID + '/settings')
def chat_settings(h, body, id):
    hub = h.server.hub
    think, online = _flag(body, 'think'), _flag(body, 'online')
    if think is None and online is None:
        raise ValueError('Give think or online as true or false')
    if online:
        hub.online(True)  # refuses when launched with --offline
    view = _chat_view(store.update_chat_settings(hub.root, id, think=think, online=online), hub.root)
    if online is False:
        # Running work of this conversation stops web access at its next request, not only later turns.
        view['online_stopped_jobs'] = hub.stop_online(id)
    return view


@route('POST', '/api/chats/' + UUID + '/messages')
def chat_message(h, body, id):
    return {'task': h.server.hub.chat(id, _text(body, 'content', 60000), files=_files(body, 'files'),
                                     queue=bool(_flag(body, 'queue'))).summary()}


@route('POST', '/api/chats/' + UUID + '/route')
def chat_route(h, body, id):
    content, mode = validated_mode_call(_text(body, 'content', 60000), body.get('mode_call'), _files(body, 'files'))
    task = h.server.hub.route(id, content, _files(body, 'files'), required_mode=mode,
                              required_effort=(body.get('mode_call') or {}).get('effort'), queue=bool(_flag(body, 'queue')))
    # A follow-up queued behind the running turn has state 'queued'; cancel it with /api/queue/<task>/cancel.
    return {'ok': True, 'task': task.summary()}


@route('POST', '/api/chats/' + UUID + '/investigation/stop')
def investigation_stop(h, body, id):
    expected = body.get('investigation_id')
    if expected is not None and not isinstance(expected, str):
        raise ValueError('Invalid investigation ID')
    return h.server.hub.stop_investigation(id, expected)


@route('POST', '/api/chats/' + UUID + '/assistant/stop')
def assistant_stop(h, body, id):
    return h.server.hub.stop_assistant(id)


@route('POST', '/api/chats/' + UUID + '/assistant/resume')
def assistant_resume(h, body, id):
    return {'task': h.server.hub.resume_assistant(id).summary()}


@route('POST', '/api/chats/' + UUID + r'/routes/(?P<index>\d{1,6})/start')
def route_start(h, body, id, index):
    raw = body.get('limits') or {}
    if not isinstance(raw, dict):
        raise ValueError('limits must be an object')
    limits = {key: value for key, value in (
        ('rounds', _int(raw, 'rounds', 1, 100)), ('tokens', _int(raw, 'tokens', 512)),
        ('solve_tokens', _int(raw, 'solve_tokens', 128, 1_000_000)),
        ('verify_tokens', _int(raw, 'verify_tokens', 128, 1_000_000)),
        ('input_tokens', _int(raw, 'input_tokens', 2048)), ('seconds', _seconds(raw, 'seconds')),
        ('requests', _int(raw, 'requests', 0, 1000)), ('chars', _int(raw, 'chars', 1000, 1_000_000))) if value is not None}
    task = h.server.hub.start_route(id, int(index), body.get('mode'), _text(body, 'request', 60000),
                                    _files(body, 'files'), limits)
    return {'task': task.summary()}


@route('POST', '/api/chats/' + UUID + r'/routes/(?P<index>\d{1,6})/cancel')
def route_cancel(h, body, id, index):
    return {'task': h.server.hub.cancel_route(id, int(index)).summary()}


@route('POST', '/api/chats/' + UUID + r'/routes/(?P<index>\d{1,6})/dismiss')
def route_dismiss(h, body, id, index):
    return {'item': h.server.hub.dismiss_route(id, int(index))}


@route('POST', '/api/queue/(?P<task>[0-9a-f]{12})/cancel')
def queue_cancel(h, body, task):
    return {'task': h.server.hub.cancel_queued(task).summary()}


@route('GET', '/api/folders')
def folders(h, body):
    hub = h.server.hub
    return {**store.list_folders(hub.base, (h.query.get('path') or [''])[0]),
            'current': hub.root.relative_to(hub.base).as_posix() if hub.root != hub.base else '',
            'recent': store.recent_folders(hub.base)}


@route('POST', '/api/folders')
def folder_create(h, body):
    path = body.get('path') or ''
    return {'path': store.create_folder(h.server.hub.base, path, body.get('name'))}


@route('POST', '/api/workspace')
def workspace_open(h, body):
    path = body.get('path')
    if not isinstance(path, str):
        raise ValueError('Give the folder path relative to the interface root')
    target = store.resolve_folder(h.server.hub.base, path)
    if projects.holds_projects(target):
        raise ValueError('This folder holds the files of your projects. Open the project from the conversation list instead.')
    project = projects.folder_label(target)
    if project:
        raise ValueError(f'This folder holds the files of the project “{project["name"]}”. '
                         'Open the project from the conversation list instead; opening it as a folder would start an empty workspace inside it.')
    h.server.switch(path)
    return {'workspace': str(h.server.hub.root)}


@route('GET', '/api/settings')
def settings(h, body):
    return store.load_settings(h.server.hub.root)


@route('POST', '/api/settings')
def settings_save(h, body):
    return store.save_settings(h.server.hub.root, body.get('read'))


@route('GET', '/api/personalization')
def preferences(h, body):
    return personalization.load(h.server.hub.base)


@route('POST', '/api/personalization')
def preferences_save(h, body):
    hub = h.server.hub
    value = personalization.save(hub.base, instructions=body.get('instructions'),
                                 macros=body.get('macros'), revision=body.get('revision'))
    hub.bus.publish('personalization')
    return value


@route('POST', '/api/files/upload', raw=True)
def upload(h, body):
    name = (h.query.get('name') or [''])[0]
    root = h.server.hub.root
    project_id = (h.query.get('project_id') or [''])[0]
    try:
        folder = projects.read_project(root, project_id)['folder'] if project_id else ''
    except FileNotFoundError:
        raise store.NotFound('Unknown project') from None
    saved = store.save_upload(root / folder if folder else root, name, body)
    if folder:
        saved = folder + '/' + saved
    result = {'path': saved}
    if encoding := store.upload_encoding(body):
        result['note'] = f'Read as {encoding} text (the file starts with a byte-order mark); it is kept unchanged.'
    if images.is_image(saved):
        try:
            result['image'] = images.snapshot(root, [saved])[0]
        except (AgentError, ValueError, OSError):
            # Invalid image uploads must not accumulate as ordinary source files.
            (root / saved).unlink(missing_ok=True)
            raise
    h.server.hub.bus.publish('files', path=saved)
    return result


@route('GET', '/api/templates')
def templates(h, body):
    return {'templates': store.list_templates(h.server.hub.base)}


_WRITEUP_KEYS = {'goal', 'source_files', 'files', 'template_files', 'template', 'save_template', 'notes', 'output',
                 'rounds', 'tokens', 'input_tokens', 'seconds', 'queue'}


@route('POST', '/api/writeup')
def writeup_start(h, body):
    unknown = sorted(set(body) - _WRITEUP_KEYS)
    if unknown:
        raise ValueError('Unknown field' + ('s' if len(unknown) > 1 else '') + ' for a write-up: ' + ', '.join(unknown)
                         + '. Use source_files for the notes, template_files for the template.')
    if body.get('files') is not None and body.get('source_files') is not None:
        raise ValueError('Give the notes once: source_files (files is the same field)')
    template = body.get('template')
    save_as = body.get('save_template')
    notes = body.get('notes') or ''
    if not isinstance(notes, str) or not isinstance(body.get('output') or '', str):
        raise ValueError('notes and output must be text')
    task = h.server.hub.start_writeup(
        _text(body, 'goal', 20000), source_files=_files(body, 'files' if body.get('files') is not None else 'source_files'),
        template_files=_files(body, 'template_files'),
        template=template if isinstance(template, str) and template else None,
        save_template=save_as if isinstance(save_as, str) and save_as.strip() else None,
        notes=notes, output=body.get('output') or '',
        rounds=_int(body, 'rounds', 1, 100), tokens=_int(body, 'tokens', 1024),
        input_tokens=_int(body, 'input_tokens', 2048), seconds=_seconds(body, 'seconds'),
        queue=bool(_flag(body, 'queue')))
    return {'task': task.summary(), 'id': task.target}


# Untrusted files from sandbox runs. Each type gets headers that keep its content inert:
# SVG and HTML never run in this origin (CSP sandbox); HTML may run its own scripts in an
# opaque origin only, framed by this interface; data files are plain text.
ARTIFACT_HEADERS = {
    '.png': ('image/png', "default-src 'none'", False),
    '.svg': ('image/svg+xml', "default-src 'none'; style-src 'unsafe-inline'; sandbox", False),
    '.pdf': ('application/pdf', None, True),
    '.html': ('text/html; charset=utf-8', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
              "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; frame-ancestors 'self'", True),
    '.csv': ('text/plain; charset=utf-8', "default-src 'none'; sandbox", False),
    '.txt': ('text/plain; charset=utf-8', "default-src 'none'; sandbox", False),
    '.json': ('text/plain; charset=utf-8', "default-src 'none'; sandbox", False),
}


@route('GET', r'/api/artifacts/(?P<experiment>exp-\d{8}-\d{6}-[0-9a-f]{6})/(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,63})')
def experiment_artifact(h, body, experiment, name):
    from ..numerics.artifacts import ExperimentStore
    suffix = Path(name).suffix.lower()
    if suffix not in ARTIFACT_HEADERS:
        raise store.NotFound('No such artifact in this folder')
    experiments = ExperimentStore(h.server.hub.root)
    try:
        declared = {a.get('name') for a in experiments.load(experiment).get('artifacts', [])}
    except (ValueError, OSError):
        declared = set()
    if name not in declared:  # only the run's own outputs: never record.json or code.py
        raise store.NotFound('No such artifact in this folder')
    path = experiments.path(experiment, name)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_000_000:
            raise FileNotFoundError
        data = path.read_bytes()
    except OSError:
        raise store.NotFound('No such artifact in this folder') from None
    content_type, policy, frame = ARTIFACT_HEADERS[suffix]
    headers = [('Cache-Control', 'private, max-age=86400'), ('Content-Disposition', f'inline; filename="{name}"')]
    if policy:
        headers.append(('Content-Security-Policy', policy))
    h.send_bytes(200, data, content_type, headers, csp=False, frame=frame)
    return Streamed


@route('GET', '/api/styles')
def styles(h, body):
    return {'styles': store.list_styles(h.server.hub.base), 'choice': store.style_choice(h.server.hub.base)}


@route('POST', '/api/styles/choice')
def styles_choice(h, body):
    values = [body.get(key) for key in ('mode', 'style', 'domain')]
    if any(value is not None and not isinstance(value, str) for value in values):
        raise ValueError('mode, style and domain must be text')
    return {'choice': store.set_style_choice(h.server.hub.base, *values)}


@route('POST', '/api/styles/template')
def style_template(h, body):
    name = body.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Name the style')
    return {'style': store.set_style_template(h.server.hub.base, h.server.hub.root, name.strip(), _files(body, 'template_files'))}


# After /api/styles/choice and /api/styles/template: the first matching route wins.
@route('POST', r'/api/styles/(?P<name>[^/]{1,200})')
def style_edit(h, body, name):
    name = unquote(name)
    if set(body) - {'rename', 'items'} or len(body) != 1:
        raise ValueError('Send either {"rename": "new name"} or {"items": {...}}')
    if 'rename' in body:
        if not isinstance(body['rename'], str) or not body['rename'].strip():
            raise ValueError('Give the new name of the style')
        return {'style': store.edit_style(h.server.hub.base, name, rename=body['rename'])}
    return {'style': store.edit_style(h.server.hub.base, name, items=body['items'])}


@route('DELETE', r'/api/styles/(?P<name>[^/]{1,200})')
def style_delete(h, body, name):
    store.delete_style(h.server.hub.base, unquote(name))
    return {'ok': True}


@route('POST', '/api/style')
def style_start(h, body):
    name, goal = body.get('name'), body.get('goal') or ''
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Name the style')
    if not isinstance(goal, str) or len(goal) > 20000:
        raise ValueError('goal must be text of at most 20000 characters')
    task = h.server.hub.start_style(
        goal, name=name.strip(), source_files=_files(body), template_files=_files(body, 'template_files'),
        rounds=_int(body, 'rounds', 1, 100), tokens=_int(body, 'tokens', 1024),
        input_tokens=_int(body, 'input_tokens', 2048), seconds=_seconds(body, 'seconds'),
        queue=bool(_flag(body, 'queue')))
    return {'task': task.summary(), 'id': task.target}
@route('GET', '/api/research/' + UUID + '/pdf')
def research_pdf(h, body, id):
    data = store.research_pdf(h.server.hub.root, id)
    download = (h.query.get('download') or [''])[0] == '1'
    name = store.research_tex(h.server.hub.root, id)[0][:-4] + '.pdf' if download else 'writeup.pdf'
    # Native browser PDF preview, including the app's authenticated same-origin work panel.
    h.send_bytes(200, data, 'application/pdf', [('Content-Disposition', f'{"attachment" if download else "inline"}; filename="{name}"'),
                                                ('Cache-Control', 'no-store')], csp=False, frame=not download)
    return Streamed


@route('GET', '/api/research/' + UUID + '/tex')
def research_tex(h, body, id):
    name, data = store.research_tex(h.server.hub.root, id)
    h.send_bytes(200, data, 'application/x-tex; charset=utf-8', [('Content-Disposition', f'attachment; filename="{name}"'),
                                                                 ('Cache-Control', 'no-store')])
    return Streamed


@route('POST', '/api/research/' + UUID + '/needs')
def research_needs(h, body, id):
    action = body.get('action')
    if action not in ('waive', 'stop'):
        raise ValueError('action must be waive or stop')
    task = h.server.hub.writeup_needs(id, action)
    return {'task': task.summary() if task else None, 'id': id}


@route('GET', '/api/research/' + UUID + '/body')
def research_body(h, body, id):
    name, data = store.research_body(h.server.hub.root, id)
    h.send_bytes(200, data, 'application/x-tex; charset=utf-8', [('Content-Disposition', f'attachment; filename="{name}"'),
                                                                 ('Cache-Control', 'no-store')])
    return Streamed


@route('GET', '/api/research/' + UUID + '/source')
def research_source(h, body, id):
    name, data = store.research_source(h.server.hub.root, id)
    h.send_bytes(200, data, 'application/zip', [('Content-Disposition', f'attachment; filename="{name}"'),
                                             ('Cache-Control', 'no-store')])
    return Streamed


@route('GET', '/api/research/' + UUID + '/review')
def research_review(h, body, id):
    return _review_file(h, id, 'review', 'text/markdown; charset=utf-8')


@route('GET', '/api/research/' + UUID + '/bib')
def research_bib(h, body, id):
    return _review_file(h, id, 'bib', 'application/x-bibtex; charset=utf-8')


@route('GET', '/api/research/' + UUID + r'/download\.md')
def research_download_md(h, body, id):
    return _review_download(h, id, 'md', 'text/markdown; charset=utf-8')


@route('GET', '/api/research/' + UUID + r'/download\.bib')
def research_download_bib(h, body, id):
    return _review_download(h, id, 'bib', 'application/x-bibtex; charset=utf-8')


def _review_download(h, id, output, content_type):
    """A literature review rendered for use outside the app: standalone Markdown or the BibTeX of its cited works."""
    name, data = store.research_review_download(h.server.hub.root, id, output)
    h.send_bytes(200, data, content_type, [('Content-Disposition', f'attachment; filename="{name}"'),
                                           ('Cache-Control', 'no-store')])
    return Streamed


def _review_file(h, id, output, content_type):
    """A literature review's Markdown or BibTeX file, shown or (with ?download=1) saved."""
    name, data = store.research_review_file(h.server.hub.root, id, output)
    download = (h.query.get('download') or [''])[0] == '1'
    h.send_bytes(200, data, content_type, [('Content-Disposition', f'{"attachment" if download else "inline"}; filename="{name}"'),
                                           ('Cache-Control', 'no-store')])
    return Streamed


@route('POST', '/api/chats/' + UUID + '/review')
def chat_review(h, body, id):
    return {'task': h.server.hub.review(id, queue=bool(_flag(body, 'queue'))).summary()}


def _investigation_view(record, can_stop=False, *, current=False, usage=None):
    if not record:
        return None
    from ..investigation import TERMINAL
    from ..investigation_writing import exact_review
    tasks = []
    for task in (record.get('plan') or {}).get('tasks', []):
        result = record.get('tasks', {}).get(task['id'], {})
        tasks.append({'id': task['id'], 'mode': task['mode'], 'objective': task['objective'],
                      'after': task['after'], 'claim': task.get('claim'), 'status': result.get('status', 'pending'), 'effort': task.get('effort'),
                      'job_id': result.get('job_id'), 'child_id': result.get('child_id'), 'reason': result.get('reason')})
    budget = usage if current else record.get('usage')
    totals = store.budget_view(budget or {}, current and record['phase'] == 'running')
    return {'id': record['id'], 'phase': record['phase'], 'tasks': tasks, 'approved': bool(record.get('approved')),
            'synthesis': record.get('synthesis', ''), 'synthesis_steps': record.get('synthesis_steps', []),
            'target_reviews': sorted({v for s in record.get('tasks', {}).values()
                                      if record.get('target') and (v := exact_review(s, record['target']))}),
            'current': current, 'usage': totals, 'reading': (record.get('plan') or {}).get('reading', ''),
            'statement': (record.get('target') or {}).get('statement', ''), 'ceiling': record.get('ceiling') or {},
            'can_stop': bool(can_stop and record['phase'] not in TERMINAL)}


def _chat_view(chat, root):
    state = chat.get('assistant_state') or {}
    investigation = state.get('investigation')
    can_stop = bool(investigation and state.get('status') in ('paused', 'interrupted', 'incomplete'))
    if not investigation:
        from ..investigation import load_record
        for item in reversed(chat['transcript']):
            if item.get('role') != 'assistant' or not item.get('investigation_id'):
                continue
            try:
                prior = load_record(root, item['investigation_id'])
                if prior.get('chat_id') == chat['id']:
                    investigation = prior
                    break
            except (OSError, ValueError, TypeError, KeyError):
                continue
    transcript = chat['transcript']
    if investigation and investigation['phase'] == 'clarification':
        # Older releases saved the framing question as the body of an investigation report.
        heading = '## Investigation report\n\n'
        transcript = [({**{k: v for k, v in item.items() if k != 'investigation_id'},
                        'clarification_id': investigation['id'], 'content': item['content'][len(heading):]}
                       if item.get('investigation_id') == investigation['id'] and item.get('content', '').startswith(heading)
                       else item) for item in transcript]
    return {'id': chat['id'], 'mode': chat['mode'], 'title': chat['title'], 'settings': chat['settings'],
            'investigation': _investigation_view(investigation, can_stop, current=bool(state.get('investigation')),
                                                 usage=state.get('budget')),
            'project_id': chat.get('project_id'), 'title_source': chat.get('title_source'),
            'images': store.chat_images(chat),
            'created_at': chat['created_at'], 'updated_at': chat['updated_at'],
            'transcript': transcript, 'context_messages': len(chat['history']), **store.assistant_view(chat, root)}


# -- HTTP plumbing ------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'SquareHarness/' + VERSION
    sys_version = ''

    timeout = 30

    def log_message(self, format, *args):
        pass  # request logs would print job IDs and paths on every poll

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')

    def do_PUT(self):
        self._handle('PUT')

    def do_DELETE(self):
        self._handle('DELETE')

    def _handle(self, method):
        self.cookie = None
        self.body_read = method == 'GET'
        try:
            url = urlsplit(self.path)
            self.query = parse_qs(url.query)
            if not self._host_allowed():
                return self.error(403, 'Unexpected Host header; open the address printed in the terminal')
            if url.path.startswith('/api/'):
                return self._api(method, url.path)
            if method != 'GET':
                return self.error(405, 'Method not allowed')
            return self._static(url.path)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def active(self, kind):
        return next((t.target for t in self.server.hub.active_tasks() if t.kind == kind), None)

    def offset(self):
        value = (self.query.get('from') or ['0'])[0]
        if not value.isdigit():
            raise ValueError('from must be a nonnegative byte offset')
        return int(value)

    def _host_allowed(self):
        if not self.server.loopback:
            return True  # LAN mode: phones use an IP address; the token is required
        host = self.headers.get('Host', '').lower()
        name = host.split(']')[0] + ']' if host.startswith('[') else host.rsplit(':', 1)[0]
        return name in ('localhost', '127.0.0.1', '[::1]')

    def _authorized(self):
        token = self.server.token.encode()
        header = self.headers.get('Authorization', '')
        if header.startswith('Bearer ') and hmac.compare_digest(header[7:].strip().encode(), token):
            return True
        try:
            morsel = http.cookies.SimpleCookie(self.headers.get('Cookie', '')).get(self.server.cookie_name)
        except http.cookies.CookieError:
            return False
        return morsel is not None and hmac.compare_digest(morsel.value.encode(), token)

    def _same_origin(self):
        site = self.headers.get('Sec-Fetch-Site')
        if site is not None and site not in ('same-origin', 'none'):
            return False
        origin = self.headers.get('Origin')
        return origin is None or urlsplit(origin).netloc.lower() == self.headers.get('Host', '').lower()

    def _body(self, limit=MAX_BODY):
        length = self.headers.get('Content-Length', '0')
        if not length.isdigit() or int(length) > limit:
            raise ValueError('Request body is missing or too large')
        self.body_read = int(length) == 0
        if int(length) == 0:
            return {}
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise TypeError('Requests must be JSON')
        data = self.rfile.read(int(length))
        self.body_read = True
        value = json.loads(data.decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Request body must be a JSON object')
        try:
            # JSON escapes can encode a lone surrogate, which no saved UTF-8 file can hold.
            json.dumps(value, ensure_ascii=False).encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('The request contains an invalid character (an unpaired UTF-16 surrogate). '
                             'Remove it and try again.') from None
        return value

    def _raw_body(self):
        length = self.headers.get('Content-Length', '')
        if not length.isdigit() or int(length) > store.UPLOAD_BYTES:
            # Read a moderately oversized body before refusing it, so the client receives the
            # message instead of a broken connection; a much larger one is not read at all.
            if length.isdigit() and int(length) <= 4 * store.UPLOAD_BYTES:
                remaining = int(length)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.body_read = True
            raise ValueError('Files are limited to 20 MB')
        data = self.rfile.read(int(length))
        self.body_read = True
        return data

    def _api(self, method, path):
        allowed = [(fn, match, auth) for m, pattern, fn, auth in ROUTES
                   if m == method and (match := pattern.match(path))]
        if not allowed:
            known = any(pattern.match(path) for _, pattern, _, _ in ROUTES)
            return self.error(405 if known else 404, 'Method not allowed' if known else 'Unknown endpoint')
        function, match, auth = allowed[0]
        if auth and not self._authorized():
            return self.error(401, 'Open the address printed in the terminal, or enter its access token')
        body = {}
        if method != 'GET':
            if not self._same_origin():
                return self.error(403, 'Cross-origin request refused')
            try:
                body = self._raw_body() if getattr(function, 'raw_body', False) else self._body(function.body_limit)
            except TypeError as exc:
                return self.error(415, str(exc))
            except (ValueError, UnicodeError, RecursionError) as exc:
                return self.error(400, 'Invalid JSON body' if isinstance(exc, (json.JSONDecodeError, RecursionError)) else str(exc))
        try:
            result = function(self, body, **match.groupdict())
        except Busy as exc:
            return self.error(409, str(exc))
        except (projects.StaleProject, personalization.StalePersonalization) as exc:
            return self.error(409, str(exc))
        except FileExistsError as exc:
            return self.error(409, _os_message(exc))
        except store.NotFound as exc:
            return self.error(404, str(exc))
        except Unauthorized as exc:
            return self.error(401, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            raise
        except OSError as exc:
            return self.error(400, _os_message(exc))
        except (ValueError, AgentError) as exc:
            return self.error(400, str(exc))
        except Exception as exc:  # report, never crash the handler thread silently
            print(f'Interface error on {method} {path}: {type(exc).__name__}: {exc}', file=sys.stderr)
            return self.error(500, 'Internal error; see the terminal for details')
        if result is not Streamed:
            self.json(200, result)

    def _static(self, path):
        token = (self.query.get('token') or [''])[0]
        if token:
            if hmac.compare_digest(token.encode(), self.server.token.encode()):
                self.cookie = self.server.token
            # Never leave the token in the address bar, history or screenshots.
            return self.send_bytes(303, b'', 'text/plain', [('Location', '/')])
        relative = (path or '/').lstrip('/') or 'index.html'
        parts = relative.split('/')
        if any(not part or part.startswith('.') or part == '..' for part in parts):
            return self.error(404, 'Not found', html=True)
        base = STATIC.resolve()
        target = (base / relative).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            if relative == 'index.html':
                return self.send_bytes(200, FALLBACK, TYPES['.html'], [('Cache-Control', 'no-store')])
            return self.error(404, 'Not found', html=True)
        cache = 'public, max-age=31536000, immutable' if parts[0] == 'assets' else 'no-cache'
        self.send_bytes(200, target.read_bytes(), TYPES.get(target.suffix.lower(), 'application/octet-stream'),
                        [('Cache-Control', cache)])

    def security_headers(self, csp=True, frame=False):
        if csp:
            self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        # Only sandboxed artifacts (HTML, PDF) may be framed, and only by this interface.
        self.send_header('X-Frame-Options', 'SAMEORIGIN' if frame else 'DENY')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        if self.cookie:
            # SameSite=Strict and HttpOnly: other sites can neither send nor read it.
            self.send_header('Set-Cookie', f'{self.server.cookie_name}={self.cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000')

    def send_bytes(self, status, body, content_type, headers=(), csp=True, frame=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.security_headers(csp, frame)
        for key, value in headers:
            self.send_header(key, value)
        if not getattr(self, 'body_read', True):
            # Unread body bytes must never be parsed as the next keep-alive request.
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_bytes(status, body, 'application/json; charset=utf-8', [('Cache-Control', 'no-store')])

    def error(self, status, message, html=False):
        if html:
            return self.send_bytes(status, message.encode(), 'text/plain; charset=utf-8')
        self.json(status, {'error': message})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, hub, token, loopback):
        if ':' in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)
        self.hub, self.token, self.loopback = hub, token, loopback
        self.cookie_name = f'{COOKIE}_{self.server_address[1]}'
        self.stopping = threading.Event()
        self.urls = []

    def shutdown(self):
        self.stopping.set()
        super().shutdown()

    def switch(self, relative):
        """Open another folder below the root and follow its saved jobs instead."""
        target = self.hub.set_workspace(relative)
        old = getattr(self, 'watcher', None)
        if old is not None:
            old.stop()
            self.watcher = Watcher(target, self.hub.bus, old.interval)
            self.watcher.start()
        self.hub.bus.publish('workspace', path=target.relative_to(self.hub.base).as_posix())


def _os_message(exc):
    """An OS error without server paths or temporary names; harness-raised messages are kept."""
    if exc.errno is None and exc.filename is None:
        return str(exc)
    return {errno.EEXIST: 'A file or folder with that name already exists',
            errno.ENOENT: 'No such file or folder',
            errno.ENAMETOOLONG: 'That name is too long',
            errno.EACCES: 'Permission denied by the file system',
            errno.EPERM: 'Permission denied by the file system',
            errno.ENOSPC: 'The disk is full',
            errno.EROFS: 'The folder is read-only'}.get(exc.errno, f'File system error ({exc.strerror or "unknown"})')


# -- launch ------------------------------------------------------------------

def workspace_token(root):
    """A per-workspace access token kept with the saved jobs it protects (mode 0600)."""
    base = Path(root) / '.mathagent'
    _directory(base, create=True)
    path = base / 'gui-token'
    try:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, encoding='ascii') as stream:
                value = stream.read(200).strip()
            if re.fullmatch(r'[A-Za-z0-9_-]{32,128}', value):
                return value
        path.unlink()  # unreadable, malformed or readable by others: replace it
    except FileNotFoundError:
        pass
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as stream:
        stream.write(value + '\n')
    return value


def _loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _lan_addresses():
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))  # TEST-NET-1: a UDP connect sends no packet
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if not ipaddress.ip_address(a).is_loopback)


def folder_lock(root):
    """Hold the folder for this interface: two interfaces writing one folder's jobs and chats corrupt them."""
    import fcntl
    path = Path(root) / '.mathagent' / 'gui.lock'
    _directory(path.parent, create=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    handle = os.fdopen(fd, 'r+')
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        handle.close()
        raise ValueError('The workspace lock must be a regular file.')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.seek(0)
        owner = handle.read(40).strip()
        handle.close()
        raise ValueError(f'Another Square Harness interface already serves {root}'
                         + (f' (process {owner})' if owner else '') + '; use it, or stop it first.') from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def bind(host, port, hub, token):
    error = None
    for candidate in range(port, port + 10) if port else [0]:
        try:
            return Server((host, candidate), hub, token, _loopback(host))
        except OSError as exc:
            error = exc
    raise OSError(f'Cannot listen on {host} ports {port}–{port + 9}: {error}. Choose another --gui-port.')


def serve(args):
    root = Path(args.workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Workspace must be a directory')
    if not _loopback(args.gui_host):
        try:
            ipaddress.ip_address(args.gui_host)
        except ValueError:
            raise ValueError('--gui-host must be an IP address such as 127.0.0.1 or 0.0.0.0') from None
    base = Path(getattr(args, 'gui_root', None) or root).expanduser().resolve(strict=True)
    if not base.is_dir() or not root.is_relative_to(base):
        raise ValueError('--gui-root must be a folder that contains the --workspace folder')
    args.workspace, args.gui_root = root, base
    token = workspace_token(base)
    hub = Hub(args)
    hub.folder_lock = folder_lock(root)
    server = bind(args.gui_host, args.gui_port, hub, token)
    port = server.server_address[1]
    if args.gui_port and port != args.gui_port:
        print(f'Port {args.gui_port} is busy; this interface uses {port}.', file=sys.stderr)
    local = f'http://127.0.0.1:{port}' if server.loopback or args.gui_host in ('0.0.0.0', '::') else f'http://{args.gui_host}:{port}'
    if not server.loopback:
        hosts = _lan_addresses() if args.gui_host in ('0.0.0.0', '::') else [args.gui_host]
        server.urls = [f'http://{h}:{port}' for h in hosts]
    watcher = Watcher(root, hub.bus)
    watcher.start()
    server.watcher = watcher
    query = '/?' + urlencode({'token': token})
    lines = [f'╭─ SQUARE HARNESS · v{VERSION} · visual interface',
             f'│ {args.model} · {args.backend} at {args.host}',
             f'│ Workspace: {root}' + (f' (folders below {base} can be opened)' if base != root else ''),
             f'│ Research: {"offline, locked by --offline (local/cache only)" if hub.online_locked else "online by default, switchable per job"}'
             ' · proof: solve → verify → repair',
             f'│ Open: {local}{query}']
    for url in server.urls:
        lines.append(f'│ Phone / other device: {url}{query}')
    if server.urls:
        lines.append('│ Network mode: traffic is plain HTTP on your local network. Use it only on a trusted')
        lines.append('│ network or through a VPN/SSH tunnel; anyone with the token can control this harness.')
    lines.append('╰─ Ctrl+C stops the interface; running work pauses at its saved checkpoint')
    print('\n'.join(lines), flush=True)
    if args.allow_python:
        print('Python enabled: each snippet requires approval in the interface and runs with your account permissions.',
              flush=True)
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, [local + query]).start()
    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        def stop_signal(signum, frame):
            raise KeyboardInterrupt
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous_handlers[signum] = signal.signal(signum, stop_signal)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print('\nStopping. Running work is pausing at its checkpoint…', flush=True)
    finally:
        server.stopping.set()
        hub.shutdown()
        server.watcher.stop()
        server.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0
