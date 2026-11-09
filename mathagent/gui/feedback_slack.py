# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ENPC / SquareHarness

"""Private Slack report delivery using chat:write and files:write.

Slack's upload flow: getUploadURLExternal -> raw bytes -> completeUploadExternal.
https://docs.slack.dev/messaging/working-with-files/
"""
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request


class DeliveryError(ValueError):
    """A safe error whose text never contains a credential or Slack response body."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def settings():
    """No public input can choose a token, recipient channel or upload destination."""
    filename = os.environ.get('SH_FEEDBACK_SLACK_TOKEN_FILE', '')
    channel = os.environ.get('SH_FEEDBACK_SLACK_CHANNEL', '')
    if not filename and not channel:
        return None
    if not filename or not Path(filename).is_absolute() or not re.fullmatch(r'C[A-Z0-9]{8,32}', channel):
        raise DeliveryError('Slack report delivery is not configured correctly')
    from .feedback import _read
    try:
        token = _read(Path(filename), 4096, private=True).decode('ascii').strip()
    except (OSError, ValueError, UnicodeError):
        raise DeliveryError('Slack report delivery credential is unavailable') from None
    if not re.fullmatch(r'(?:xoxb-|xoxe\.xoxb-)[A-Za-z0-9.-]{20,}', token):
        raise DeliveryError('Slack report delivery requires a bot credential')
    return token, channel


def _request(request, limit=1024 * 1024):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise DeliveryError('Slack report delivery was not acknowledged')
            body = response.read(limit + 1)
            if len(body) > limit:
                raise DeliveryError('Slack report delivery response was too large')
            return body
    except Exception:
        raise DeliveryError('Slack report delivery was not acknowledged') from None


def _api(token, method, body):
    if method not in {'chat.postMessage', 'files.getUploadURLExternal', 'files.completeUploadExternal'}:
        raise DeliveryError('Unsupported Slack report operation')
    if method == 'files.getUploadURLExternal':
        data = urllib.parse.urlencode(body).encode('ascii')
        content_type = 'application/x-www-form-urlencoded'
    else:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        content_type = 'application/json; charset=utf-8'
    request = urllib.request.Request('https://slack.com/api/' + method, data,
        {'Authorization': 'Bearer ' + token, 'Content-Type': content_type}, method='POST')
    try:
        result = json.loads(_request(request))
    except (ValueError, UnicodeError):
        raise DeliveryError('Slack report delivery was not acknowledged') from None
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise DeliveryError('Slack report delivery was not acknowledged')
    return result


def _upload(url, data):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'files.slack.com' or parsed.port is not None
            or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith('/upload/v1/')):
        raise DeliveryError('Slack returned an unsafe file upload destination')
    # The signed upload URL authorizes these bytes; never send the bot token here.
    request = urllib.request.Request(url, data, {'Content-Type': 'application/octet-stream'}, method='POST')
    _request(request, limit=4096)


def _message(report, channel):
    label = {'bug': 'Bug report', 'suggestion': 'Suggestion', 'math': 'Mathematical feedback'}[report['category']]
    title = 'Square Harness · ' + label
    details = [f'Report: {report["id"]}']
    metadata = report.get('metadata') or {}
    details += [f'{name}: {metadata[name]}' for name in
        ('reporter_email', 'version', 'build', 'model', 'api_profile', 'service_tier') if metadata.get(name)]
    details.append('Trace: ' + ('included by explicit consent' if report['include_trace'] else 'not included'))
    if report.get('omissions'):
        details.append(f'Trace snapshot notices: {len(report["omissions"])}; see manifest in the attached report ZIP.')
    blocks = [{'type': 'header', 'text': {'type': 'plain_text', 'text': title}},
              {'type': 'section', 'text': {'type': 'plain_text', 'text': '\n'.join(details)}},
              {'type': 'divider'}]
    # Slack section text has a 3000-character limit. Keep the entire <=8000-char description.
    description = report['description']
    for start in range(0, len(description), 2800):
        blocks.append({'type': 'section', 'text': {'type': 'plain_text',
                       'text': description[start:start + 2800], 'emoji': False}})
    fallback = title + '\n' + '\n'.join(details) + '\n\n' + description
    fallback = fallback.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    message = {'channel': channel, 'blocks': blocks, 'mrkdwn': False,
               'parse': 'none', 'link_names': False, 'unfurl_links': False, 'unfurl_media': False}
    # Slack can derive accessible text from plain-text blocks. Avoid its 40k
    # fallback truncation if escaping an ampersand-heavy description expands it.
    if len(fallback) < 39000:
        message['text'] = fallback
    return message


def deliver(report, archive_bytes, screenshots, checkpoint):
    """Return None when unconfigured, otherwise True only after message and all file acks.

    ``checkpoint`` persists the mutable report after each acknowledged step. A
    partial failure keeps its parent message/file IDs without claiming delivery.
    Screenshots are validated in feedback.py; filenames here are fixed by that module.
    """
    configured = settings()
    if configured is None:
        return None
    token, channel = configured
    progress = {'channel_id': channel, 'stage': 'posting_report', 'files': []}
    report['slack'] = progress
    checkpoint()
    parent = _api(token, 'chat.postMessage', _message(report, channel))
    timestamp = parent.get('ts')
    if parent.get('channel') != channel or not isinstance(timestamp, str) or not re.fullmatch(r'\d+\.\d+', timestamp):
        raise DeliveryError('Slack report message was not acknowledged')
    progress.update(message_ts=timestamp, stage='uploading_files')
    checkpoint()
    attachments = [(name.rsplit('/', 1)[-1], data) for name, data in screenshots]
    attachments.append((f'sh-report-{report["id"]}.zip', archive_bytes))
    for filename, data in attachments:
        result = _api(token, 'files.getUploadURLExternal', {'filename': filename, 'length': len(data)})
        identifier, url = result.get('file_id'), result.get('upload_url')
        if not isinstance(identifier, str) or not re.fullmatch(r'F[A-Z0-9]{8,32}', identifier) or not isinstance(url, str):
            raise DeliveryError('Slack report upload was not acknowledged')
        item = {'id': identifier, 'name': filename, 'stage': 'allocated'}
        progress['files'].append(item)
        checkpoint()
        _upload(url, data)
        item['stage'] = 'uploaded'
        checkpoint()
    progress['stage'] = 'sharing_files'
    checkpoint()
    files = [{'id': item['id'], 'title': item['name']} for item in progress['files']]
    result = _api(token, 'files.completeUploadExternal', {'files': files,
        'channel_id': channel, 'thread_ts': timestamp})
    acknowledged = result.get('files')
    if (not isinstance(acknowledged, list) or any(not isinstance(item, dict) for item in acknowledged)
            or {item.get('id') for item in acknowledged} != {item['id'] for item in files}):
        raise DeliveryError('Slack report files were not acknowledged')
    for item in progress['files']:
        item['stage'] = 'shared'
    progress['stage'] = 'delivered'
    checkpoint()
    return True
