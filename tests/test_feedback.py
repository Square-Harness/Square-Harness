# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Feedback's real HTTP flow and private filesystem/trace boundaries, without model calls."""
import base64
import http.client
import io
import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch
import zipfile

from mathagent.gui import feedback, store
from tests.test_gui import GuiCase

PNG = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'screenshot fixture').decode()


class FeedbackHTTPTests(GuiCase):
    def setUp(self):
        super().setUp()
        report_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(report_temporary.cleanup)
        self.reports = Path(report_temporary.name).resolve() / 'reports'
        self.environment = patch.dict(os.environ, {'SH_FEEDBACK_DIR': str(self.reports),
                                                   'SH_FEEDBACK_WEBHOOK_FILE': '', 'SH_FEEDBACK_REPORT_BASE_URL': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def download(self, path, *, auth=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': f'127.0.0.1:{self.port}'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_consent_off_does_not_read_chat_and_saved_report_is_private(self):
        chat = store.create_chat(self.root, 'free')
        body = {'category': 'bug', 'description': 'The pause button is confusing',
                'chat_id': chat['id'], 'include_trace': False,
                'images': [{'name': '../../private.png', 'mime': 'image/png', 'data': PNG}]}
        with patch.object(feedback, '_trace', side_effect=AssertionError('No trace consent')), \
                patch.object(store, 'load_chat', side_effect=AssertionError('Chat must not be loaded')):
            result = self.ok('POST', '/api/feedback', body)
        self.assertEqual(result['status'], 'saved')
        self.assertFalse(result['include_trace'])
        report_dir = self.reports / result['id']
        self.assertEqual(stat.S_IMODE(report_dir.stat().st_mode), 0o700)
        for path in report_dir.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        status, data, headers = self.download(f'/api/feedback/{result["id"]}/archive')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(data)) as exported:
            self.assertEqual(set(exported.namelist()), {'report.json', 'manifest.json', 'screenshots/screenshot-1.png'})
        self.assertEqual(len(feedback.list_reports(self.root)), 1)
        self.assertEqual(self.download(f'/api/feedback/{result["id"]}/archive', auth=False)[0], 401)
