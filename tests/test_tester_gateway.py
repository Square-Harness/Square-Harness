# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""The optional external gateway authenticates and isolates real HTTP traffic."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from deployment import tester_gateway as gateway
except ImportError:
    jwt = None


class Backend:
    def __init__(self, token):
        self.token, self.files, self.seen, self.paths = token, {}, [], []
        self.stream_continue = threading.Event()
        backend = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def send(self, body, content_type='application/json'):
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Security-Policy', "default-src 'self'")
                self.send_header('Cache-Control', 'public, max-age=1000')
                self.send_header('Set-Cookie', 'pairing=' + backend.token)
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                backend.seen.append(dict(self.headers))
                backend.paths.append(self.path)
                if self.headers.get('Authorization') != 'Bearer ' + backend.token:
                    self.send_error(401)
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                target = urlsplit(self.path)
                if target.path == '/api/events':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    self.wfile.write(b'data: first\n\n')
                    self.wfile.flush()
                    backend.stream_continue.wait(3)
                    self.wfile.write(b'data: second\n\n')
                    self.wfile.flush()
                    return
                if target.path == '/download':
                    return self.send(backend.files.get('note.txt', b''), 'application/octet-stream')
                if target.path == '/redirect':
                    self.send_response(303)
                    self.send_header('Location', '/?token=' + backend.token)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send(json.dumps({'files': sorted(backend.files)}).encode())

            def do_POST(self):
                if not self.authorized():
                    return
                name = parse_qs(urlsplit(self.path).query).get('name', ['note.txt'])[0]
                backend.files[name] = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.send(b'{"ok":true}')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.stream_continue.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


@unittest.skipUnless(jwt is not None, 'Install deployment/requirements-testers.txt for gateway tests')
class TesterGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.backends = [Backend('a' * 43), Backend('b' * 43)]
        for backend in self.backends:
            self.addCleanup(backend.close)
        tenants = {}
        for email, backend in zip(('alice@example.test', 'bob@example.test'), self.backends):
            token_file = root / (email.split('@')[0] + '.token')
            token_file.write_text(backend.token + '\n')
            token_file.chmod(0o600)
            tenants[email] = {'port': backend.server.server_port, 'token_file': str(token_file)}
        config = root / 'config.json'
        config.write_text(json.dumps({'hostname': 'sh.example.test',
            'issuer': 'https://sh-test.cloudflareaccess.com', 'audience': 'test-app-audience', 'tenants': tenants}))
        config.chmod(0o600)
        settings = gateway.Settings.load(config)
        public_key = self.private_key.public_key()

        class SigningKey:
            key = public_key

        class Keys:
            def get_signing_key_from_jwt(self, token):
                if jwt.get_unverified_header(token).get('kid') != 'test-signing-key':
                    raise jwt.PyJWTError('Unknown key')
                return SigningKey()

        verifier = gateway.AccessVerifier(settings, key_client=Keys())
        self.server = gateway.Gateway(('127.0.0.1', 0), settings, verifier=verifier)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_gateway)

    def close_gateway(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def token(self, email='alice@example.test', key=None, **overrides):
        now = int(time.time())
        claims = {'iss': 'https://sh-test.cloudflareaccess.com', 'aud': ['test-app-audience'],
                  'iat': now, 'exp': now + 300, 'sub': 'test-identity', 'email': email}
        claims.update(overrides)
        return jwt.encode(claims, key or self.private_key, algorithm='RS256', headers={'kid': 'test-signing-key'})

    def request(self, method='GET', path='/api/files', token=None, headers=None, body=None):
        values = {'Host': 'sh.example.test', 'Origin': 'https://sh.example.test',
                  'Cf-Access-Jwt-Assertion': token or self.token()}
        values.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def test_signed_identity_is_required_before_private_workspaces_are_selected(self):
        bad = [self.token(key=self.other_key), self.token(exp=int(time.time()) - 100),
               self.token(aud='other-app'), self.token(iss='https://other.cloudflareaccess.com'),
               self.token(email='uninvited@example.test'), 'not-a-jwt']
        for token in bad:
            with self.subTest(token_kind=bad.index(token)):
                self.assertEqual(self.request(token=token)[0], 403)
                self.assertEqual(self.request(path='/api/account', token=token)[0], 403)
        self.assertEqual(self.request(headers={'Cf-Access-Jwt-Assertion': ''})[0], 403)
        self.assertEqual(self.request(headers={'Host': 'evil.example.test'})[0], 403)
        self.assertEqual(self.request('POST', headers={'Origin': 'https://evil.example.test'}, body=b'x')[0], 403)
        self.assertEqual(self.request('POST', headers={'Sec-Fetch-Site': 'cross-site'}, body=b'x')[0], 403)
        self.assertEqual(self.request(path='/?token=private')[0], 403)
        self.assertEqual(self.request('POST', path='/api/login', body=b'{}')[0], 403)
        for method in ('GET', 'HEAD'):
            code, headers, body = self.request(method, '/files?from=1',
                headers={'Host': 'www.sh.example.test', 'Cf-Access-Jwt-Assertion': ''})
            self.assertEqual(code, 308)
            self.assertEqual(headers['Location'], 'https://sh.example.test/files?from=1')
            self.assertEqual(body, b'')
        self.assertEqual(self.request(path='/?token=private', headers={'Host': 'www.sh.example.test'})[0], 403)
        self.assertEqual(self.request('POST', headers={'Host': 'www.sh.example.test'}, body=b'x')[0], 403)
        token_file = self.server.settings.tenants['alice@example.test'].token_file
        token_file.chmod(0o660)
        try:
            self.assertEqual(self.request()[0], 503)
        finally:
            token_file.chmod(0o600)
        self.assertFalse(any(backend.seen for backend in self.backends))

        code, headers, body = self.request(path='/api/account',
            headers={'Cf-Access-Authenticated-User-Email': 'bob@example.test'})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {'email': 'alice@example.test',
            'logout_url': 'https://sh.example.test/cdn-cgi/access/logout'})
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        self.assertNotIn('Set-Cookie', headers)
        self.assertNotIn(self.backends[0].token.encode(), body)
        self.assertEqual(json.loads(self.request(path='/api/account', token=self.token('bob@example.test'))[2])['email'],
                         'bob@example.test')
        self.assertEqual(self.request('POST', path='/api/account', body=b'{}')[0], 405)
        self.assertFalse(any(backend.seen for backend in self.backends))

        code, headers, body = self.request('POST', '/api/files/upload?name=note.txt', body=b'alice only',
            headers={'Authorization': 'Bearer supplied-by-browser', 'Cookie': 'pairing=another-workspace',
                     'X-Forwarded-User': 'bob@example.test', 'Content-Type': 'application/octet-stream'})
        self.assertEqual(code, 200)
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        self.assertEqual(headers['Content-Security-Policy'], "default-src 'self'")
        self.assertNotIn(self.backends[0].token.encode(), body)
        seen = self.backends[0].seen[-1]
        self.assertEqual(seen['Authorization'], 'Bearer ' + self.backends[0].token)
        self.assertEqual(seen['Host'], '127.0.0.1:' + str(self.backends[0].server.server_port))
        self.assertNotIn('Cookie', seen)
        self.assertNotIn('Cf-Access-Jwt-Assertion', seen)
        self.assertNotIn('X-Forwarded-User', seen)
        self.assertEqual(json.loads(self.request()[2])['files'], ['note.txt'])
        self.assertEqual(json.loads(self.request(token=self.token('bob@example.test'))[2])['files'], [])
        self.assertEqual(self.request(path='/download')[2], b'alice only')
        code, headers, body = self.request(path='/redirect')
        self.assertEqual(code, 303)
        self.assertNotIn('Location', headers)
        self.assertNotIn(self.backends[0].token.encode(), body)
