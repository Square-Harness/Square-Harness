# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Cloudflare Access gateway for fixed workspaces or a shared authenticated backend.

The shared-backend mode forwards a short-lived signed principal assertion. The
backend remains private and verifies that assertion before selecting a workspace.
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
import sys
from urllib.parse import parse_qsl, urlsplit

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathagent.gui.identity import (AuthenticatedPrincipal, issue_assertion,
                                    validate_secret)

MAX_BODY = 32 * 1024 * 1024
REQUEST_HEADERS = {'accept', 'accept-encoding', 'content-type', 'last-event-id', 'range',
                   'if-none-match', 'if-modified-since'}
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}
RESPONSE_DROP = HOP_HEADERS | {'set-cookie', 'cache-control', 'expires', 'server', 'date',
                               'access-control-allow-origin', 'access-control-allow-credentials'}


def private_text(path, limit=65536, modes=(0o400, 0o600, 0o640)):
    """Read a private regular file without following symlinks."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in modes:
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
class SharedBackend:
    port: int
    identity_secret_file: Path


@dataclass(frozen=True)
class Settings:
    hostname: str
    issuer: str
    audience: str
    tenants: dict
    feedback_admins: tuple = ()
    shared_backend: SharedBackend | None = None
    allowed_emails: frozenset[str] | None = None

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
        shared = None
        tenants = {}
        allowed = None
        if 'shared_backend' in value:
            if 'tenants' in value:
                raise ValueError('Configure either a shared backend or fixed tenant backends, not both')
            config = value['shared_backend']
            if not isinstance(config, dict):
                raise ValueError('The shared backend configuration must be an object')
            port = config.get('port')
            secret_path = config.get('identity_secret_file')
            secret_file = Path(secret_path) if isinstance(secret_path, str) else Path()
            if (type(port) is not int or not 1024 <= port <= 65535
                    or not isinstance(secret_path, str) or not secret_file.is_absolute()):
                raise ValueError('Configure a loopback backend port and absolute identity secret file')
            shared = SharedBackend(port, secret_file)
            raw_allowed = value.get('allowed_emails')
            if raw_allowed is not None:
                if (not isinstance(raw_allowed, list) or not raw_allowed
                        or any(not isinstance(email, str) for email in raw_allowed)):
                    raise ValueError('Allowed identities must be a nonempty email list')
                normalized = [email.strip().casefold() for email in raw_allowed]
                if (len(normalized) > 1000 or len(set(normalized)) != len(normalized)
                        or any(not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)
                               for email in normalized)):
                    raise ValueError('Allowed identities must be distinct valid emails')
                allowed = frozenset(normalized)
        else:
            ports = set()
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
        if (len(set(admins)) != len(admins)
                or any(not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) for email in admins)
                or (not shared and any(email not in tenants for email in admins))
                or (shared and allowed is not None and any(email not in allowed for email in admins))):
            raise ValueError('Feedback administrators must be distinct invited identities')
        return cls(hostname, issuer, audience, tenants, admins, shared, allowed)


class AccessVerifier:
    def __init__(self, settings, key_client=None):
        self.settings = settings
        self.keys = key_client or jwt.PyJWKClient(settings.issuer + '/cdn-cgi/access/certs',
                                                cache_jwk_set=True, lifespan=300, timeout=10)

    def principal(self, token):
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
        return AuthenticatedPrincipal(claims['sub'], claims['email'].strip().casefold())

    def email(self, token):
        return self.principal(token).email


class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, settings, verifier=None):
        if address[0] != '127.0.0.1':
            raise ValueError('The tester gateway must listen on loopback')
        self.settings = settings
        self.verifier = verifier or AccessVerifier(settings)
        self.identity_secret = None
        if settings.shared_backend is not None:
            self.identity_secret = private_text(settings.shared_backend.identity_secret_file, 4096,
                                                modes=(0o400, 0o600, 0o640)).strip()
            validate_secret(self.identity_secret)
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
        principal = self.server.verifier.principal(assertions[0])
        if settings.shared_backend is not None:
            if settings.allowed_emails is not None and principal.email not in settings.allowed_emails:
                raise ValueError('Identity is not invited')
            return principal, None
        tenant = settings.tenants.get(principal.email)
        if tenant is None:
            raise ValueError('Identity is not invited')
        return principal, tenant

    def account(self, principal):
        body = json.dumps({'email': principal.email, 'logout_url': 'https://' + self.server.settings.hostname
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
                principal, tenant = self.identity()
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
                return self.account(principal) if self.command in {'GET', 'HEAD'} else self.refuse(405)
            backend_path = self.path
            if target.path.startswith('/api/admin/feedback'):
                if principal.email not in self.server.settings.feedback_admins:
                    return self.refuse(403)
                if self.server.settings.shared_backend is not None:
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
            headers = {name: value for name, value in self.headers.items() if name.lower() in REQUEST_HEADERS}
            if self.server.settings.shared_backend is not None:
                backend = self.server.settings.shared_backend
                assertion = issue_assertion(principal, self.server.identity_secret)
                headers.update(Host='127.0.0.1:' + str(backend.port),
                               Origin='http://127.0.0.1:' + str(backend.port), Connection='close',
                               **{'X-SH-Identity': assertion})
                port = backend.port
            else:
                token = tenant.token()
                port = tenant.port
                headers.update(Host='127.0.0.1:' + str(port), Authorization='Bearer ' + token,
                               Origin='http://127.0.0.1:' + str(port), Connection='close')
                # Never trust a browser-supplied reporter identity.
                headers['X-SH-User-Email'] = principal.email
            if self.command not in {'GET', 'HEAD'}:
                headers['Content-Length'] = str(length)
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=45)
            connection.request('GET' if self.command == 'HEAD' else self.command, backend_path, body, headers)
            response = connection.getresponse()
            self.send_response_only(response.status)
            connection_headers = {part.strip().lower() for part in response.getheader('Connection', '').split(',')}
            for name, value in response.getheaders():
                if (name.lower() in RESPONSE_DROP | connection_headers
                        or (self.server.settings.shared_backend is None and token in value)
                        or (self.server.settings.shared_backend is not None and assertion in value)):
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
