# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Shared model-client contract and registry for provider adapters."""
from abc import ABC, abstractmethod
from collections.abc import Generator
from importlib import import_module
from typing import Any


class ModelClient(ABC):
    """Harness-facing interface implemented by each model transport."""

    backend = ''
    profile = 'default'
    display_name = ''
    default_host = ''
    default_model = ''
    supports_api_key = False
    requires_api_key = False
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    def models(self) -> list[str]:
        """Return models exposed by the configured endpoint."""

    @abstractmethod
    def stream(self, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
        """Yield native events, including terminal usage; closing cancels the request."""

    def count_input_tokens(self, payload: dict[str, Any]) -> int | None:
        """Return an exact count or supported estimate, else None for fallback estimation."""
        return None


_CLIENTS: dict[tuple[str, str], type[ModelClient]] = {}
_BUILTINS_LOADED = False


def register_model_client(backend: str, profile: str = 'default'):
    """Register a concrete client class for one backend/profile pair."""
    if not backend or not profile:
        raise ValueError('Model-client backend and profile must be nonempty')

    def register(client_type: type[ModelClient]) -> type[ModelClient]:
        key = backend, profile
        if not isinstance(client_type, type) or not issubclass(client_type, ModelClient):
            raise TypeError('Registered model clients must subclass ModelClient')
        if (client_type.backend, client_type.profile) != key:
            raise ValueError(f'Model client attributes must match registered key {backend}/{profile}')
        if key in _CLIENTS:
            raise ValueError(f'Model client already registered for {backend}/{profile}')
        _CLIENTS[key] = client_type
        return client_type

    return register


def _load_builtin_clients() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    for module in ('agent', 'backends', 'deepinfra', 'api_profiles'):
        import_module(f'.{module}', __package__)
    _BUILTINS_LOADED = True


def registered_profiles(backend: str) -> frozenset[str]:
    _load_builtin_clients()
    return frozenset(profile for name, profile in _CLIENTS if name == backend)


def registered_model_clients() -> tuple[type[ModelClient], ...]:
    _load_builtin_clients()
    return tuple(_CLIENTS[key] for key in sorted(_CLIENTS))


def model_client_type(backend: str, profile: str = 'default') -> type[ModelClient]:
    _load_builtin_clients()
    from .agent import AgentError

    client_type = _CLIENTS.get((backend, profile))
    if client_type is None:
        raise AgentError(f'Unknown model client: {backend}/{profile}')
    return client_type


def create_model_client(backend: str, host: str, timeout: float = 600, *,
                        profile: str = 'default') -> ModelClient:
    return model_client_type(backend, profile)(host, timeout=timeout)
