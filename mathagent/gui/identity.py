# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Provider-neutral authenticated principals passed by a trusted front proxy."""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import time


ASSERTION_TTL = 60
_B64 = re.compile(r'[A-Za-z0-9_-]+')
_EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject: str
    email: str

    def __post_init__(self):
        if (not isinstance(self.subject, str) or not self.subject or len(self.subject) > 512
                or self.subject.strip() != self.subject or not self.subject.isprintable()
                or not isinstance(self.email, str) or len(self.email) > 320
                or not _EMAIL.fullmatch(self.email.strip())):
            raise ValueError('Invalid authenticated principal')
        object.__setattr__(self, 'email', self.email.strip().casefold())

    @property
    def account_id(self) -> str:
        return hashlib.sha256(self.subject.encode('utf-8')).hexdigest()


def _secret_bytes(secret: str) -> bytes:
    if not isinstance(secret, str):
        raise ValueError('Trusted-proxy secret must be text')
    encoded = secret.encode('utf-8')
    if len(encoded) < 32 or secret.strip() != secret or any(char.isspace() for char in secret):
        raise ValueError('Trusted-proxy secret must contain at least 32 bytes')
    return encoded


def validate_secret(secret: str) -> None:
    _secret_bytes(secret)


def _principal(subject: object, email: object) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(subject, email)


def issue_assertion(principal: AuthenticatedPrincipal, secret: str, *, now: int | None = None) -> str:
    key = _secret_bytes(secret)
    issued = int(time.time()) if now is None else now
    payload = json.dumps({'sub': principal.subject, 'email': principal.email,
                          'iat': issued, 'exp': issued + ASSERTION_TTL},
                         ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')
    encoded = base64.urlsafe_b64encode(payload).rstrip(b'=').decode('ascii')
    message = 'v1.' + encoded
    signature = hmac.new(key, message.encode('ascii'), hashlib.sha256).digest()
    return message + '.' + base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')


def verify_assertion(assertion: str, secret: str, *, now: int | None = None) -> AuthenticatedPrincipal:
    key = _secret_bytes(secret)
    if not isinstance(assertion, str) or len(assertion) > 4096:
        raise ValueError('Invalid trusted identity assertion')
    parts = assertion.split('.')
    if len(parts) != 3 or parts[0] != 'v1' or any(not _B64.fullmatch(part) for part in parts[1:]):
        raise ValueError('Invalid trusted identity assertion')
    message = '.'.join(parts[:2])
    expected = hmac.new(key, message.encode('ascii'), hashlib.sha256).digest()
    try:
        supplied = base64.b64decode(parts[2] + '=' * (-len(parts[2]) % 4), altchars=b'-_', validate=True)
        payload = base64.b64decode(parts[1] + '=' * (-len(parts[1]) % 4), altchars=b'-_', validate=True)
        claims = json.loads(payload)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid trusted identity assertion') from exc
    if not hmac.compare_digest(supplied, expected) or not isinstance(claims, dict):
        raise ValueError('Invalid trusted identity assertion')
    current = int(time.time()) if now is None else now
    issued, expires = claims.get('iat'), claims.get('exp')
    if (type(issued) is not int or type(expires) is not int or issued > current + 5
            or expires <= current or expires - issued != ASSERTION_TTL
            or set(claims) != {'sub', 'email', 'iat', 'exp'}):
        raise ValueError('Expired or malformed trusted identity assertion')
    return _principal(claims['sub'], claims['email'])
