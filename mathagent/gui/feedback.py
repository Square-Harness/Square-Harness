# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Private, explicit-consent feedback snapshots. Never follows workspace references."""
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from . import store

MAX_IMAGES = 3
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_TRACE_BYTES = 32 * 1024 * 1024
_CATEGORIES = {'bug', 'suggestion', 'math'}
_ARTIFACT = re.compile(r'^\d{4,}-[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.(?:md|json|jsonl)$')
_SECRET_KEYS = re.compile(r'(?:apikey|authorization|cookie|password|credential|webhook|privatekey|accesstoken|guitoken|cfaccessjwtassertion|secret)', re.I)
_PATTERNS = (
    (re.compile(r'https://hooks\.slack\.com/[^\s<>"\']+', re.I), '[REDACTED WEBHOOK]'),
    (re.compile(r'\bBearer\s+[A-Za-z0-9._~+/-]+=*', re.I), 'Bearer [REDACTED]'),
    (re.compile(r'\b(?:sk-(?:di-)?|hf_)[A-Za-z0-9._-]{8,}'), '[REDACTED KEY]'),
    (re.compile(r'\b(?:xox[bp]-|xapp-|xoxe\.xox[bp]-)[A-Za-z0-9.-]{8,}'), '[REDACTED SLACK TOKEN]'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'), '[REDACTED JWT]'),
    (re.compile(r'(?<![A-Fa-f0-9])[A-Fa-f0-9]{64}(?![A-Fa-f0-9])'), '[REDACTED TOKEN OR DIGEST]'),
    (re.compile(r'([?&#](?:token|key|api_key|access_token)=)[^\s&#"\']+', re.I), r'\1[REDACTED]'),
    (re.compile(r'''((?:["']?)(?:api[-_ ]?key|authorization|set-cookie|cookie|password|access[-_ ]?token|gui[-_ ]?token|webhook)(?:["']?)\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n,;]+)''', re.I), r'\1[REDACTED]'),
)


def _id(value):
    if not isinstance(value, str):
        raise ValueError('A saved UUID is required')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError('A saved UUID is required') from None
    if str(parsed) != value:
        raise ValueError('A canonical saved UUID is required')
    return value


def _directory(path, create=False, private=False):
    """Reject symlink ancestors as well as the final path (including dangling links)."""
    path = Path(os.path.abspath(path))
    missing = []
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            missing.append(part)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Unsafe feedback or trace directory')
    if create:
        for part in reversed(missing):
            part.mkdir(mode=0o700)
    elif missing:
        raise store.NotFound('Unknown feedback or trace directory')
    if private:
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Feedback storage must be owned by this user with mode 0700')
    return path


def _read(path, limit, *, private=False):
    _directory(Path(path).parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise store.NotFound('Unknown feedback or trace file') from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600))):
            raise ValueError('Unsafe feedback or trace file')
        if info.st_size > limit:
            raise ValueError('Full trace exceeds the 32 MiB export limit; send without the trace or choose a smaller conversation')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Full trace exceeds the 32 MiB export limit; send without the trace or choose a smaller conversation')
        return data
    finally:
        if fd is not None:
            os.close(fd)


def _storage(root, create=False):
    configured = os.environ.get('SH_FEEDBACK_DIR')
    if configured and not Path(configured).is_absolute():
        raise ValueError('SH_FEEDBACK_DIR must be an absolute private path')
    workspace = Path(os.path.abspath(root))
    fallback = workspace / '.mathagent' / 'feedback'
    path = Path(os.path.abspath(configured)) if configured else fallback
    if configured and path != fallback and (path == workspace or workspace in path.parents):
        raise ValueError('Feedback storage must be outside the served workspace or its protected .mathagent directory')
    return _directory(path, create=create, private=True)


def _text(value):
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    # In-memory credentials, if configured this way, need not match a known key syntax.
    for name, secret in os.environ.items():
        if _SECRET_KEYS.search(re.sub(r'[^a-z]', '', name.lower())) and len(secret) >= 8:
            value = value.replace(secret, '[REDACTED]')
    return value


def _redact(value):
    if isinstance(value, dict):
        return {key: ('[REDACTED]' if _SECRET_KEYS.search(re.sub(r'[^a-z]', '', key.lower()))
                       or key.lower() in {'token', 'headers', 'request_headers', 'response_headers'} else _redact(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        # Tool arguments and HTTP payloads can be nested serialized JSON.
        if value.lstrip().startswith(('{', '[')):
            try:
                return json.dumps(_redact(json.loads(value)), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        return _text(value)
    return value


def _json(value):
    return json.dumps(_redact(value), ensure_ascii=False, indent=2).encode('utf-8')


def _lines(text):
    result = []
    for line in text.splitlines(keepends=True):
        try:
            result.append(json.dumps(_redact(json.loads(line)), ensure_ascii=False) + ('\n' if line.endswith('\n') else ''))
        except ValueError:
            result.append(_text(line))  # Running streams may have a partial final JSON line.
    return ''.join(result).encode()


def _trace(root, chat_id):
    """Snapshot selected saved chat and linked jobs, never files mentioned in their text."""
    chat_id = _id(chat_id)
    base = _directory(Path(root) / '.mathagent')
    chat_path = _directory(base / 'chats') / (chat_id + '.json')
    raw = _read(chat_path, MAX_TRACE_BYTES)
    try:
        chat = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('Cannot export an invalid saved conversation') from None
    if not isinstance(chat, dict) or chat.get('id') != chat_id or not isinstance(chat.get('transcript'), list):
        raise ValueError('Cannot export an invalid saved conversation')
    remaining = MAX_TRACE_BYTES - len(raw)
    files = {'conversation.json': _json(chat)}
    omissions = []
    related = set()
    entries = list(chat['transcript'])
    state = chat.get('assistant_state') or {}
    entries += state.get('children', []) if isinstance(state, dict) else []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get('job_id'):
            continue
        mode = (entry.get('action') or {}).get('mode') if isinstance(entry.get('action'), dict) else entry.get('mode')
        if not isinstance(mode, str):
            continue
        folder = 'proofs' if mode == 'prove' else 'checks' if mode == 'check' else 'research' if mode in {'literature', 'referee', 'detailed_review', 'quick_review', 'explain', 'writeup', 'summarize'} else None
        if folder:
            related.add((folder, _id(entry['job_id'])))
    for folder, job_id in sorted(related):
        prefix = f'workers/{folder}/{job_id}'
        try:
            directory = _directory(base / folder / job_id)
        except store.NotFound:
            omissions.append({'name': prefix, 'reason': 'Linked job no longer exists'})
            continue
        paths = [(directory / name, name) for name in ('state.json', 'report.md', 'ledger.md', 'proof.md')]
        artifact_dir = directory / 'artifacts'
        if artifact_dir.exists() or artifact_dir.is_symlink():
            _directory(artifact_dir)
            paths += [(p, 'artifacts/' + p.name) for p in sorted(artifact_dir.iterdir()) if _ARTIFACT.fullmatch(p.name)]
        for path, name in paths:
            if not path.exists() and not path.is_symlink():
                if name == 'state.json':
                    omissions.append({'name': prefix + '/' + name, 'reason': 'Linked job snapshot is missing'})
                continue
            data = _read(path, remaining)
            remaining -= len(data)
            try:
                text = data.decode('utf-8')
            except UnicodeError:
                raise ValueError('A linked trace file is not valid UTF-8') from None
            if name.endswith('.json'):
                try:
                    data = _json(json.loads(text))
                except ValueError:
                    raise ValueError('A linked job snapshot is invalid JSON') from None
            elif name.endswith('.jsonl'):
                data = _lines(text)
            else:
                try:  # Research request/stream artifacts use .md even when their content is JSON.
                    data = _json(json.loads(text))
                except ValueError:
                    data = _lines(text)
            files[prefix + '/' + name] = data
    if sum(len(data) for data in files.values()) > MAX_TRACE_BYTES:
        raise ValueError('Redacted full trace exceeds the 32 MiB export limit; send without the trace')
    return files, omissions


def _manifest(files, omissions=()):
    return {'files': [{'name': name, 'size': len(data)} for name, data in files.items()],
            'size': sum(map(len, files.values())), 'omissions': list(omissions),
            'scope': 'Selected saved conversation and linked worker traces; no original workspace files',
            'snapshot': True, 'redacted': True}


def _zip(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive_file:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, time.gmtime()[:6])
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive_file.writestr(info, data)
    return output.getvalue()


def preview(root, chat_id):
    files, omissions = _trace(root, chat_id)
    return _manifest(files, omissions)


def archive(root, chat_id):
    files, omissions = _trace(root, chat_id)
    files['manifest.json'] = _json(_manifest(files, omissions))
    return _zip(files)


def _images(items):
    if not isinstance(items, list) or len(items) > MAX_IMAGES:
        raise ValueError('Attach at most three screenshots')
    result = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) - {'name', 'mime', 'data'}:
            raise ValueError('Invalid screenshot attachment')
        mime, encoded = item.get('mime'), item.get('data')
        if not isinstance(mime, str) or mime not in {'image/png', 'image/jpeg', 'image/webp'} or not isinstance(encoded, str):
            raise ValueError('Screenshots must be PNG, JPEG or WebP')
        if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError('Each screenshot must be at most 3 MiB')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid screenshot encoding') from None
        valid = (mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')
                 or mime == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')
                 or mime == 'image/webp' and len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP')
        if not valid or len(data) > MAX_IMAGE_BYTES:
            raise ValueError('Screenshot contents do not match its image type or exceed 3 MiB')
        suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[mime]
        result[f'screenshots/screenshot-{index}.{suffix}'] = data
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _webhook():
    filename = os.environ.get('SH_FEEDBACK_WEBHOOK_FILE')
    if not filename:
        return None
    if not Path(filename).is_absolute():
        raise ValueError('Feedback webhook secret file must be absolute')
    value = _read(Path(filename), 4096, private=True).decode('ascii').strip()
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'hooks.slack.com' or parsed.port is not None
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r'/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+', parsed.path)):
        raise ValueError('Feedback webhook must be an HTTPS Slack incoming webhook')
    return value


def config():
    from . import feedback_slack
    try:
        bot = feedback_slack.settings()
        mode = 'slack_files' if bot else 'webhook' if _webhook() else 'saved'
    except (OSError, ValueError, UnicodeError):
        mode = 'saved'  # A partial/invalid bot configuration never falls back to a webhook.
    return {'enabled': True, 'delivery_configured': mode != 'saved', 'delivery_mode': mode, 'max_images': MAX_IMAGES,
            'max_image_bytes': MAX_IMAGE_BYTES, 'max_description_chars': 8000,
            'max_trace_bytes': MAX_TRACE_BYTES}


def _write(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.feedback-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _notify(report):
    webhook = _webhook()
    if not webhook:
        return False
    text = f'SH feedback · {report["category"]} · report {report["id"]}'
    metadata = report['metadata']
    details = [f'{key}: {metadata[key]}' for key in ('reporter_email', 'version', 'build', 'model', 'service_tier') if metadata.get(key)]
    if details:
        text += '\n' + ' · '.join(details)
    summary = report['description'][:1200]
    if len(report['description']) > 1200:
        summary += '\n[Notification excerpt; complete description is in the private report.]'
    text += '\n' + summary
    # Plain Slack text must not turn user-provided strings into mentions or links.
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    if report.get('archive_url', '').startswith(('https://', 'http://')):
        text += '\nProtected report archive: ' + report['archive_url']
    request = urllib.request.Request(webhook, json.dumps({'text': text}).encode(), {'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise ValueError('Feedback delivery failed')
        if response.read(1024).strip() != b'ok':
            raise ValueError('Feedback delivery was not acknowledged')
    return True


def save(root, body, metadata=None):
    if not isinstance(body, dict) or set(body) - {'category', 'description', 'chat_id', 'include_trace', 'images'}:
        raise ValueError('Unknown feedback fields')
    category, description = body.get('category'), body.get('description')
    if not isinstance(category, str) or category not in _CATEGORIES or not isinstance(description, str) or not description.strip() or len(description) > 8000:
        raise ValueError('Choose bug, suggestion or math and describe the feedback in 1–8000 characters')
    include_trace = body.get('include_trace', False)
    if type(include_trace) is not bool:
        raise ValueError('Trace consent must be true or false')
    chat_id = body.get('chat_id')
    if chat_id is not None:
        _id(chat_id)  # Consent-off never loads or reads the conversation.
    if include_trace and chat_id is None:
        raise ValueError('Choose a conversation before including its trace')
    files = _images(body.get('images', []))
    omissions = []
    if include_trace:
        traces, omissions = _trace(root, chat_id)
        files.update({'trace/' + name: data for name, data in traces.items()})
    identifier = str(uuid.uuid4())
    safe_metadata = {key: value for key, value in (metadata or {}).items()
                     if key in {'version', 'build', 'model', 'tier', 'email', 'reporter_email', 'api_profile', 'service_tier'}
                     and isinstance(value, str) and len(value) <= 300}
    report = {'id': identifier, 'category': category, 'description': _text(description.strip()),
              'chat_id': chat_id, 'include_trace': include_trace, 'created_at': time.time(),
              'metadata': _redact(safe_metadata), 'status': 'saved',
              'archive_url': '/api/feedback/' + identifier + '/archive'}
    prefix = os.environ.get('SH_FEEDBACK_REPORT_BASE_URL', '')
    if prefix:
        if '{tenant}' in prefix:
            email = safe_metadata.get('reporter_email', '')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
                raise ValueError('Hosted feedback archive links require a verified reporter email')
            prefix = prefix.replace('{tenant}', hashlib.sha256(email.strip().casefold().encode()).hexdigest()[:16])
        parsed = urllib.parse.urlsplit(prefix)
        loopback_http = parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1', 'localhost'}
        if (not (parsed.scheme == 'https' or loopback_http) or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment or '{' in prefix or '}' in prefix):
            raise ValueError('Feedback report base URL must be HTTPS (HTTP is allowed only for localhost)')
        report['archive_url'] = prefix.rstrip('/') + '/' + identifier + '/archive'
    files['report.json'] = _json(report)
    manifest = _manifest(files, omissions)
    report.update(files=manifest['files'], size=manifest['size'], omissions=omissions)
    files['manifest.json'] = _json(manifest)
    storage = _storage(root, create=True)
    directory = Path(tempfile.mkdtemp(prefix='.pending-', dir=storage))
    # The report is durable before any notification is attempted.
    _write(directory / 'archive.zip', _zip(files))
    _write(directory / 'report.json', _json(report))
    published = storage / identifier
    os.replace(directory, published)
    directory = published
    _sync(storage)
    try:
        from . import feedback_slack
        delivered = feedback_slack.deliver(report,
            _read(directory / 'archive.zip', MAX_TRACE_BYTES + MAX_IMAGES * MAX_IMAGE_BYTES + 1024 * 1024, private=True),
            [(name, data) for name, data in files.items() if name.startswith('screenshots/')],
            lambda: _write(directory / 'report.json', _json(report)))
        if delivered is None:
            delivered = _notify(report)  # Existing webhook-only installations keep their summary delivery.
        if delivered:
            report['status'] = 'delivered'
    except Exception:  # Never return webhook URLs, HTTP bodies or provider exceptions to the browser.
        report['delivery_error'] = 'Notification failed; the private report is saved.'
    _write(directory / 'report.json', _json(report))
    return report


def list_reports(root):
    try:
        directory = _storage(root)
    except store.NotFound:
        return []
    result = []
    for child in directory.iterdir():
        try:
            _id(child.name)
        except ValueError:
            continue
        _directory(child, private=True)
        result.append(json.loads(_read(child / 'report.json', MAX_TRACE_BYTES, private=True)))
    return sorted(result, key=lambda item: item['created_at'], reverse=True)


def read_archive(root, report_id):
    directory = _directory(_storage(root) / _id(report_id), private=True)
    return _read(directory / 'archive.zip', MAX_TRACE_BYTES + MAX_IMAGES * MAX_IMAGE_BYTES + 1024 * 1024, private=True)
