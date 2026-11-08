"""Feedback's real HTTP flow and private filesystem/trace boundaries, without model calls."""
import base64
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import uuid
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

    def download(self, path, *, method='GET', body=None, auth=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': f'127.0.0.1:{self.port}'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def chat_with_workers(self):
        chat = store.create_chat(self.root, 'free')
        proof_id, research_id = str(uuid.uuid4()), str(uuid.uuid4())
        chat['history'] = [{'role': 'user', 'content': 'Prove a lemma. sk-di-private-api-key-fixture'}]
        chat['transcript'] = [{'role': 'user', 'content': 'Only selected chat'},
                              {'role': 'route', 'mode': 'prove', 'job_id': proof_id},
                              {'role': 'route', 'mode': 'literature', 'job_id': research_id},
                              {'role': 'route', 'mode': 'critic', 'job_id': 'child_' + uuid.uuid4().hex,
                               'outcome': 'Inline mathematical answer'}]
        chat['assistant_state'] = {'children': [{'job_id': proof_id, 'action': {'mode': 'prove'}},
                                   {'id': 'child_' + uuid.uuid4().hex, 'job_id': 'child_' + uuid.uuid4().hex,
                                    'action': {'mode': 'explore'}, 'result': {'answer': 'Inline exploration findings'}}],
                                   'token': 'private-gui-token', 'headers': {'Authorization': 'hidden-header'}}
        store._save_chat(self.root, chat)
        for folder, job_id in (('proofs', proof_id), ('research', research_id)):
            directory = self.root / '.mathagent' / folder / job_id
            (directory / 'artifacts').mkdir(parents=True)
            (directory / 'state.json').write_text(json.dumps({'id': job_id, 'calls': [],
                'sources': [{'content': 'Inline already-supplied source text'}],
                'api_key': 'hidden-key-value', 'budget': {'tokens_charged': 123}}))
            (directory / 'report.md').write_text('A complete worker report')
            (directory / 'artifacts' / '0001-solver-request.md').write_text(json.dumps({
                'messages': [{'content': 'Request context'}], 'authorization': 'private-authorization'}))
            (directory / 'artifacts' / '0002-solver.jsonl').write_text(json.dumps({
                'message': {'thinking': 'Worker thinking', 'content': 'Worker answer'},
                'cookie': 'private-cookie'}) + '\n')
            # Original workspace assets and arbitrary child files must never be opened.
            (directory / 'artifacts' / 'source.tex').write_text('DO NOT EXPORT ORIGINAL FILE')
        foreign = store.create_chat(self.root, 'free')
        foreign['transcript'] = [{'role': 'user', 'content': 'FOREIGN CHAT PRIVATE CONTENT'}]
        store._save_chat(self.root, foreign)
        (self.root / 'private-source.tex').write_text('ORIGINAL WORKSPACE SECRET')
        return chat, proof_id, research_id

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
        self.assertEqual(self.ok('GET', '/api/feedback/config')['max_images'], 3)
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

    def test_preview_export_and_submit_include_only_selected_workers_and_redact_credentials(self):
        chat, proof_id, research_id = self.chat_with_workers()
        manifest = self.ok('POST', '/api/feedback/preview', {'chat_id': chat['id']})
        names = [item['name'] for item in manifest['files']]
        self.assertIn('conversation.json', names)
        self.assertTrue(any(proof_id in name and name.endswith('jsonl') for name in names))
        self.assertTrue(any(research_id in name for name in names))
        self.assertEqual(manifest['size'], sum(item['size'] for item in manifest['files']))
        self.assertEqual(manifest['omissions'], [])
        status, data, _ = self.download('/api/feedback/export', method='POST', body={'chat_id': chat['id']})
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as exported:
            content = b'\n'.join(exported.read(name) for name in exported.namelist()).decode()
            self.assertIn('Worker thinking', content)
            self.assertIn('Inline mathematical answer', content)
            self.assertIn('Inline exploration findings', content)
            self.assertIn('Inline already-supplied source text', content)
            self.assertIn('tokens_charged', content)
            for secret in ('sk-di-private-api-key-fixture', 'hidden-key-value', 'private-gui-token',
                           'hidden-header', 'private-authorization', 'private-cookie',
                           'FOREIGN CHAT PRIVATE CONTENT', 'ORIGINAL WORKSPACE SECRET', 'DO NOT EXPORT ORIGINAL FILE'):
                self.assertNotIn(secret, content)
        with patch.object(feedback, '_notify', return_value=False):
            result = self.ok('POST', '/api/feedback', {'category': 'math', 'description': 'A proof issue',
                'chat_id': chat['id'], 'include_trace': True, 'images': []})
        status, data, _ = self.download(f'/api/feedback/{result["id"]}/archive')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as saved:
            self.assertIn('trace/conversation.json', saved.namelist())


if __name__ == '__main__':
    unittest.main()
