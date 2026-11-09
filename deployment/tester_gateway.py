# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Cloudflare Access entry point for fixed, separately isolated SH workspaces.

Only this optional deployment layer needs PyJWT. SH remains a loopback service
with its ordinary workspace token; the gateway keeps those tokens server-side.
"""
import argparse
from dataclasses import dataclass
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qsl, urlsplit

import jwt

MAX_BODY = 32 * 1024 * 1024
REQUEST_HEADERS = {'accept', 'accept-encoding', 'content-type', 'last-event-id', 'range',
                   'if-none-match', 'if-modified-since'}
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}
RESPONSE_DROP = HOP_HEADERS | {'set-cookie', 'cache-control', 'expires', 'server', 'date',
                               'access-control-allow-origin', 'access-control-allow-credentials'}


def private_text(path, limit=65536):
    """Read a private regular file, allowing only modes 0600 and 0640."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in {0o600, 0o640}:
            raise ValueError('Gateway files must be private regular files')
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Gateway file is too large')
    return data.decode('utf-8')


@dataclass(frozen=True)
class Tenant:
    port: int
    token_file: Path

    def token(self):
        value = private_text(self.token_file, 4096).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value):
            raise ValueError('Invalid private pairing credential')
        return value


@dataclass(frozen=True)
class Settings:
    hostname: str
    issuer: str
    audience: str
    tenants: dict
    feedback_admins: tuple = ()

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(private_text(path)))

    @classmethod
    def from_dict(cls, value):
        hostname = value['hostname'].lower()
        issuer, audience = value['issuer'], value['audience']
        if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', hostname)
                or '..' in hostname or '.' not in hostname):
            raise ValueError('Configure one public DNS hostname without a port')
        if not re.fullmatch(r'https://[a-z0-9][a-z0-9-]{0,62}\.cloudflareaccess\.com', issuer):
            raise ValueError('Configure the exact HTTPS Cloudflare Access issuer')
        if not isinstance(audience, str) or not audience or len(audience) > 256 or audience.strip() != audience:
            raise ValueError('Configure the Access application audience')
        tenants, ports = {}, set()
        for email, entry in value['tenants'].items():
            email = email.strip().casefold()
            port, token_file = entry['port'], Path(entry['token_file'])
            if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or email in tenants
                    or type(port) is not int or not 1024 <= port <= 65535 or port in ports
                    or not token_file.is_absolute()):
                raise ValueError('Invalid or duplicate tenant mapping')
            tenants[email] = Tenant(port, token_file)
            ports.add(port)
        if not tenants or len(tenants) > 50:
            raise ValueError('Configure between one and fifty tenant identities')
        admins = value.get('feedback_admins', [])
        if (not isinstance(admins, list) or any(not isinstance(email, str) for email in admins)
                or len(admins) > 10):
            raise ValueError('Feedback administrators must be a list of invited emails')
        admins = tuple(email.strip().casefold() for email in admins)
        if len(set(admins)) != len(admins) or any(email not in tenants for email in admins):
            raise ValueError('Feedback administrators must be distinct invited identities')
        return cls(hostname, issuer, audience, tenants, admins)


class AccessVerifier:
    def __init__(self, settings, key_client=None):
        self.settings = settings
        self.keys = key_client or jwt.PyJWKClient(settings.issuer + '/cdn-cgi/access/certs',
                                                cache_jwk_set=True, lifespan=300, timeout=10)

    def email(self, token):
        if not isinstance(token, str) or not token or len(token) > 32768:
            raise ValueError('Missing identity assertion')
        if jwt.get_unverified_header(token).get('alg') != 'RS256':
            raise ValueError('Unsupported identity signature')
        key = self.keys.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=self.settings.audience,
                            issuer=self.settings.issuer, leeway=5,
                            options={'require': ['exp', 'iat', 'aud', 'iss', 'sub', 'email']})
        if not isinstance(claims['email'], str) or not isinstance(claims['sub'], str) or not claims['sub']:
            raise ValueError('Invalid identity claims')
        return claims['email'].strip().casefold()


class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, settings, verifier=None):
        if address[0] != '127.0.0.1':
            raise ValueError('The tester gateway must listen on loopback')
        self.settings = settings
        self.verifier = verifier or AccessVerifier(settings)
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        pass  # Never write request headers or credential-bearing exceptions to logs.


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    timeout = 30

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.proxy()

    do_POST = do_PUT = do_DELETE = do_HEAD = do_GET

    def do_OPTIONS(self):
        self.refuse(405)

    def refuse(self, code):
        body = json.dumps({'error': 'Access refused' if code == 403 else
                                  'Service unavailable' if code == 503 else 'Invalid request'}).encode()
        self.send_response_only(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(body)

    def identity(self):
        settings = self.server.settings
        hosts = self.headers.get_all('Host', [])
        assertions = self.headers.get_all('Cf-Access-Jwt-Assertion', [])
        if len(hosts) != 1 or hosts[0].lower() not in {settings.hostname, settings.hostname + ':443'}:
            raise ValueError('Unexpected public hostname')
        if self.command not in {'GET', 'HEAD'}:
            origins = self.headers.get_all('Origin', [])
            if len(origins) != 1 or origins[0] != 'https://' + settings.hostname:
                raise ValueError('Cross-origin request')
            if self.headers.get('Sec-Fetch-Site') not in {None, 'same-origin', 'none'}:
                raise ValueError('Cross-site request')
        if len(assertions) != 1:
            raise ValueError('Missing identity assertion')
        email = self.server.verifier.email(assertions[0])
        if email not in settings.tenants:
            raise ValueError('Identity is not invited')
        return email, settings.tenants[email]

    def account(self, email):
        body = json.dumps({'email': email, 'logout_url': 'https://' + self.server.settings.hostname
                           + '/cdn-cgi/access/logout'}).encode()
        self.send_response_only(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def proxy(self):
        connection = None
        headers_sent = False
        try:
            target = urlsplit(self.path)
            if (target.scheme or target.netloc or target.fragment or not target.path.startswith('/')
                    or target.path.startswith('//') or target.path.rstrip('/') == '/api/login'
                    or any(key.casefold() == 'token' for key, _ in parse_qsl(target.query, keep_blank_values=True))):
                return self.refuse(403)
            hosts = self.headers.get_all('Host', [])
            hostname = self.server.settings.hostname
            if (self.command in {'GET', 'HEAD'} and len(hosts) == 1
                    and hosts[0].lower() in {'www.' + hostname, 'www.' + hostname + ':443'}):
                self.send_response_only(308)
                self.send_header('Location', 'https://' + hostname + self.path)
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('Content-Length', '0')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                return
            try:
                email, tenant = self.identity()
            except (ValueError, KeyError, jwt.PyJWTError, OSError):
                return self.refuse(403)
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get_all('Transfer-Encoding') or len(lengths) > 1
                    or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit()))):
                return self.refuse(400)
            length = int(lengths[0]) if lengths else 0
            if length > MAX_BODY or (self.command in {'GET', 'HEAD'} and length):
                return self.refuse(413 if length > MAX_BODY else 400)
            body = self.rfile.read(length) if length else None
            if body is not None and len(body) != length:
                return self.refuse(400)
            if target.path == '/api/account':
                return self.account(email) if self.command in {'GET', 'HEAD'} else self.refuse(405)
            backend_path = self.path
            if target.path.startswith('/api/admin/feedback'):
                if email not in self.server.settings.feedback_admins:
                    return self.refuse(403)
                match = re.fullmatch(r'/api/admin/feedback/([0-9a-f]{16})/'
                                     r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/archive', target.path)
                if self.command not in {'GET', 'HEAD'} or target.query or match is None:
                    return self.refuse(403)
                # The URL selects only a configured identity, never a client-supplied port.
                tenant = next((entry for address, entry in self.server.settings.tenants.items()
                               if hashlib.sha256(address.encode()).hexdigest()[:16] == match[1]), None)
                if tenant is None:
                    return self.refuse(404)
                backend_path = '/api/feedback/' + match[2] + '/archive'
            token = tenant.token()
            origin = 'http://127.0.0.1:' + str(tenant.port)
            headers = {name: value for name, value in self.headers.items() if name.lower() in REQUEST_HEADERS}
            headers.update(Host='127.0.0.1:' + str(tenant.port), Authorization='Bearer ' + token,
                           Origin=origin, Connection='close')
            # Never trust a browser-supplied reporter identity.
            headers['X-SH-User-Email'] = email
            if self.command not in {'GET', 'HEAD'}:
                headers['Content-Length'] = str(length)
            connection = http.client.HTTPConnection('127.0.0.1', tenant.port, timeout=45)
            connection.request('GET' if self.command == 'HEAD' else self.command, backend_path, body, headers)
            response = connection.getresponse()
            self.send_response_only(response.status)
            connection_headers = {part.strip().lower() for part in response.getheader('Connection', '').split(',')}
            for name, value in response.getheaders():
                if name.lower() in RESPONSE_DROP | connection_headers or token in value:
                    continue
                if name.lower() == 'location':
                    redirect = urlsplit(value)
                    if (redirect.scheme or redirect.netloc or not value.startswith('/') or value.startswith('//')
                            or any(k.casefold() == 'token' for k, _ in parse_qsl(redirect.query, keep_blank_values=True))):
                        continue
                self.send_header(name, value)
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('X-Accel-Buffering', 'no')
            chunked = response.getheader('Content-Length') is None and self.command != 'HEAD'
            if chunked:
                self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            headers_sent = True
            if self.command != 'HEAD':
                while data := response.read1(65536):
                    self.wfile.write((f'{len(data):X}\r\n'.encode() + data + b'\r\n') if chunked else data)
                    self.wfile.flush()
                if chunked:
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException):
            if not headers_sent:
                self.refuse(503)
            self.close_connection = True
        finally:
            if connection is not None:
                connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('The gateway port must be between 1024 and 65535')
    server = Gateway(('127.0.0.1', args.port), Settings.load(args.config))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
