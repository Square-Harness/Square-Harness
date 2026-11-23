# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Pluggable persistence for explicitly remembered server provider keys."""
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
import hashlib
import os
import re


MAX_CREDENTIAL_BYTES = 16_384
_SECRET_STORES: dict[str, Callable[[Mapping[str, str]], 'ServerCredentialStore']] = {}


class ServerCredentialStoreError(RuntimeError):
    """A managed secret operation failed without exposing its value."""


class ServerCredentialStore(ABC):
    name: str
    display_name: str

    @classmethod
    @abstractmethod
    def from_environment(cls, environ: Mapping[str, str]) -> 'ServerCredentialStore':
        raise NotImplementedError

    @abstractmethod
    def load_api_key(self, account_id: str, provider_id: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def save_api_key(self, account_id: str, provider_id: str, api_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_api_key(self, account_id: str, provider_id: str) -> None:
        raise NotImplementedError


def register_server_credential_store(name: str):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name):
        raise ValueError('Server credential-store names must be lowercase identifiers')

    def register(store_type):
        if name in _SECRET_STORES:
            raise ValueError(f'Server credential store {name!r} is already registered')
        _SECRET_STORES[name] = store_type.from_environment
        return store_type

    return register


def secret_id_for(account_id: str, provider_id: str) -> str:
    if not isinstance(account_id, str) or not re.fullmatch(r'[a-f0-9]{64}', account_id):
        raise ValueError('Account ID is invalid for managed provider credentials')
    provider_parts = provider_id.split('/') if isinstance(provider_id, str) else []
    if (not isinstance(provider_id, str)
            or len(provider_id) > 256 or len(provider_parts) != 2
            or any(part in {'', '.', '..'} for part in provider_parts)
            or not re.fullmatch(r'[a-z0-9._-]+/[a-z0-9._-]+', provider_id)):
        raise ValueError('Provider ID is invalid for managed provider credentials')
    provider_hash = hashlib.sha256(provider_id.encode('ascii')).hexdigest()[:16]
    return f'square-harness-{account_id}-{provider_hash}'


def server_credential_store_from_environment(environ: Mapping[str, str] | None = None
                                             ) -> ServerCredentialStore | None:
    values = os.environ if environ is None else environ
    name = values.get('SQUARE_SERVER_SECRET_MANAGER', '').strip().lower()
    if not name:
        return None
    from . import google_secret_manager  # Register built-in stores without loading their SDKs.
    del google_secret_manager
    try:
        factory = _SECRET_STORES[name]
    except KeyError as exc:
        supported = ', '.join(sorted(_SECRET_STORES))
        raise ValueError(f'Unsupported server secret manager {name!r}; supported: {supported}') from exc
    return factory(values)
