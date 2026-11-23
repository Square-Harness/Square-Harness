# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Provider settings and opt-in local credential persistence."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

from ..ledger import _atomic_write
from ..model_client import model_client_type, registered_model_clients


STORE_VERSION = 1
MAX_STORE_BYTES = 1_000_000
MAX_API_KEY_BYTES = 16_384


def _normalize_hostname(hostname: str) -> str:
    if not hostname or any(char.isspace() or ord(char) < 33 or char in '\\%' for char in hostname):
        raise ValueError('Provider endpoint hostname is invalid')
    try:
        return ipaddress.ip_address(hostname).compressed.lower()
    except ValueError:
        if re.fullmatch(r'[0-9.]+', hostname):
            raise ValueError('Provider endpoint IP address is invalid') from None
    try:
        normalized = hostname.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError as exc:
        raise ValueError('Provider endpoint hostname is invalid') from exc
    labels = normalized.split('.')
    if (len(normalized) > 253 or not normalized
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('Provider endpoint hostname is invalid')
    return normalized


def provider_id(backend: str, profile: str) -> str:
    return f'{backend}/{profile}'


def _client_id(client_type) -> str:
    return provider_id(client_type.backend, client_type.profile)


def _validate_model(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256 or value.strip() != value
            or any(ord(char) < 32 for char in value)):
        raise ValueError('Model name must be nonempty text of at most 256 characters')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError('Model name must be valid Unicode text') from exc
    return value


def _valid_host(value: object, allowed_origins: frozenset[tuple[str, str, int]] | None) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or value.strip() != value:
        raise ValueError('Provider endpoint must be a URL')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Provider endpoint must be a valid HTTP or HTTPS URL') from exc
    if (parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc or not hostname
            or parsed.username is not None or parsed.password is not None
            or '?' in value or '#' in value or any(char.isspace() or ord(char) < 33 for char in value)
            or '\\' in value):
        raise ValueError('Provider endpoint must be an HTTP or HTTPS URL without credentials or query parameters')
    origin = (parsed.scheme.lower(), _normalize_hostname(hostname),
              port or (443 if parsed.scheme.lower() == 'https' else 80))
    if allowed_origins is not None and origin not in allowed_origins:
        raise ValueError('Provider endpoint is not in the server operator allowlist')
    return value.rstrip('/')


def validate_provider_host(value: str, allowed_origins: frozenset[tuple[str, str, int]] | None) -> str:
    return _valid_host(value, allowed_origins)


def parse_host_allowlist(value: str | None) -> frozenset[tuple[str, str, int]]:
    entries = [item.strip() for item in (value or '').split(',') if item.strip()]
    origins = set()
    for item in entries:
        try:
            parsed = urlsplit(item)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ValueError('Provider host allowlist entries must be valid origins') from exc
        if (parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc or not hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {'', '/'} or '?' in item or '#' in item):
            raise ValueError('Provider host allowlist entries must be HTTP or HTTPS origins without a path')
        if any(char.isspace() or ord(char) < 33 for char in item) or '\\' in item:
            raise ValueError('Provider host allowlist entries must be valid HTTP or HTTPS origins')
        scheme = parsed.scheme.lower()
        origins.add((scheme, _normalize_hostname(hostname), port or (443 if scheme == 'https' else 80)))
    return frozenset(origins)


def default_provider_data(args) -> dict:
    selected_backend = getattr(args, 'backend', 'ollama')
    selected_profile = (getattr(args, 'api_profile', None) or 'default') if selected_backend == 'openai' else 'default'
    selected_id = provider_id(selected_backend, selected_profile)
    providers = {}
    for client_type in registered_model_clients():
        key = _client_id(client_type)
        providers[key] = {
            'backend': client_type.backend,
            'profile': client_type.profile,
            'host': (getattr(args, 'host', None) if key == selected_id else client_type.default_host)
                    or client_type.default_host,
            'model': (getattr(args, 'model', '') if key == selected_id
                      else client_type.default_model or getattr(args, 'model', '')),
            'api_key': None,
            'remembered': False,
        }
    if selected_id not in providers:
        raise ValueError(f'No registered provider for {selected_backend}/{selected_profile}')
    return {'selected': selected_id, 'providers': providers}


def _checked_config(provider_key: str, raw: object, *, persisted: bool = False) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('Provider settings entry must be an object')
    backend, profile = raw.get('backend'), raw.get('profile')
    if not isinstance(backend, str) or not isinstance(profile, str) or provider_id(backend, profile) != provider_key:
        raise ValueError('Provider settings identifier does not match its backend and profile')
    client_type = model_client_type(backend, profile)
    host = _valid_host(raw.get('host'), None)
    model = _validate_model(raw.get('model'))
    api_key = raw.get('api_key')
    remembered = raw.get('remembered', persisted and api_key is not None)
    if type(remembered) is not bool:
        raise ValueError('Remember must be true or false')
    if api_key is not None:
        if (not client_type.supports_api_key or not isinstance(api_key, str)
                or not api_key or api_key.strip() != api_key or len(api_key.encode('utf-8')) > MAX_API_KEY_BYTES
                or any(ord(char) <= 32 or ord(char) >= 127 for char in api_key)):
            raise ValueError('API key is invalid for this provider')
    if remembered and api_key is None:
        raise ValueError('Remembered provider settings must include an API key')
    return {'backend': backend, 'profile': profile, 'host': host, 'model': model,
            'api_key': api_key, 'remembered': remembered}


def merge_persisted(defaults: dict, saved: object) -> dict:
    if saved is None:
        return defaults
    if (not isinstance(saved, dict) or set(saved) != {'version', 'selected', 'providers'}
            or saved.get('version') != STORE_VERSION or not isinstance(saved.get('providers'), dict)):
        raise ValueError('Local provider settings file has an unsupported format')
    providers = copy.deepcopy(defaults['providers'])
    for key, raw in saved['providers'].items():
        if key not in providers:
            raise ValueError(f'Local provider settings contain an unknown provider: {key}')
        entry = _checked_config(key, raw, persisted=True)
        if not entry['remembered'] and entry['api_key'] is not None:
            raise ValueError('Session-only API keys must not appear in the credential file')
        providers[key] = entry
    selected = saved['selected']
    if not isinstance(selected, str) or selected not in providers:
        raise ValueError('Local provider settings select an unknown provider')
    return {'selected': selected, 'providers': providers}


def merge_server_credentials(defaults: dict, credential_store, account_id: str) -> dict:
    providers = copy.deepcopy(defaults['providers'])
    for key, config in providers.items():
        client_type = model_client_type(config['backend'], config['profile'])
        if not client_type.supports_api_key:
            continue
        api_key = credential_store.load_api_key(account_id, key)
        if api_key is not None:
            providers[key] = _checked_config(
                key, {**config, 'api_key': api_key, 'remembered': True})
    return {'selected': defaults['selected'], 'providers': providers}


def updated_provider_data(current: dict, body: dict, *, mode: str,
                          allowed_origins: frozenset[tuple[str, str, int]] | None,
                          managed_secret_storage: bool = False) -> dict:
    allowed_fields = {'provider_id', 'host', 'model', 'api_key', 'remember', 'clear_api_key'}
    if not isinstance(body, dict) or set(body) - allowed_fields:
        raise ValueError('Provider settings contain unknown fields')
    selected = body.get('provider_id', current['selected'])
    if not isinstance(selected, str) or selected not in current['providers']:
        raise ValueError('Choose a supported provider')
    previous = current['providers'][selected]
    client_type = model_client_type(previous['backend'], previous['profile'])
    host = _valid_host(body.get('host', previous['host']), allowed_origins)
    model = _validate_model(body.get('model', previous['model']))
    if type(body.get('clear_api_key', False)) is not bool:
        raise ValueError('clear_api_key must be true or false')
    clear_key = body.get('clear_api_key', False)
    api_key = None if clear_key else previous['api_key']
    if 'api_key' in body:
        incoming = body['api_key']
        if not isinstance(incoming, str):
            raise ValueError('API key must be text')
        if incoming:
            api_key = incoming
    if api_key is not None:
        if (not client_type.supports_api_key or api_key.strip() != api_key
                or len(api_key.encode('utf-8')) > MAX_API_KEY_BYTES
                or any(ord(char) <= 32 or ord(char) >= 127 for char in api_key)):
            raise ValueError('API key is invalid for this provider')
    remember = body.get('remember', previous['remembered'])
    if type(remember) is not bool:
        raise ValueError('remember must be true or false')
    if mode == 'server' and remember and not managed_secret_storage:
        raise ValueError('Server-mode API keys are session-only until managed secret storage is configured')
    if remember and (not client_type.supports_api_key or api_key is None):
        raise ValueError('Enter an API key before choosing Remember')
    if not client_type.supports_api_key and api_key is not None:
        raise ValueError('This provider does not accept an API key')
    entry = {'backend': previous['backend'], 'profile': previous['profile'], 'host': host,
             'model': model, 'api_key': api_key, 'remembered': remember}
    updated = copy.deepcopy(current)
    updated['selected'] = selected
    updated['providers'][selected] = entry
    return updated


def public_provider_settings(data: dict, *, mode: str,
                             allowed_hosts: tuple[str, ...] = (),
                             remember_store: str | None = None) -> dict:
    clients = {_client_id(client_type): client_type for client_type in registered_model_clients()}
    providers = []
    for key, client_type in clients.items():
        config = data['providers'][key]
        providers.append({
            'id': key,
            'backend': client_type.backend,
            'profile': client_type.profile,
            'name': client_type.display_name or client_type.__name__,
            'default_host': client_type.default_host,
            'host': config['host'],
            'model': config['model'],
            'supports_api_key': client_type.supports_api_key,
            'requires_api_key': client_type.requires_api_key,
            'has_api_key': config['api_key'] is not None,
            'remembered': config['remembered'],
            'capabilities': sorted(client_type.capabilities),
        })
    return {'mode': mode, 'selected': data['selected'], 'providers': providers,
            'allowed_hosts': list(allowed_hosts),
            'remember_available': mode == 'local' or remember_store is not None,
            'remember_store': remember_store}


def display_origins(origins: frozenset[tuple[str, str, int]]) -> tuple[str, ...]:
    result = []
    for scheme, hostname, port in sorted(origins):
        display_host = f'[{hostname}]' if ':' in hostname else hostname
        default_port = 443 if scheme == 'https' else 80
        result.append(f'{scheme}://{display_host}' + (f':{port}' if port != default_port else ''))
    return tuple(result)


def serializable_provider_data(data: dict) -> dict:
    providers = {}
    for key, config in data['providers'].items():
        entry = {field: config[field] for field in ('backend', 'profile', 'host', 'model')}
        if config['remembered'] and config['api_key'] is not None:
            entry['api_key'] = config['api_key']
        providers[key] = entry
    return {'version': STORE_VERSION, 'selected': data['selected'], 'providers': providers}


class LocalProviderStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def from_environment(cls):
        data_dir = os.environ.get('SQUARE_DATA_DIR')
        if data_dir:
            root = Path(data_dir).expanduser()
            if not root.is_absolute():
                raise ValueError('SQUARE_DATA_DIR must be an absolute path')
        elif os.name == 'nt':
            root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Square Harness'
        elif sys.platform == 'darwin':
            root = Path.home() / 'Library' / 'Application Support' / 'Square Harness'
        else:
            root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'square-harness'
        return cls(root / 'secrets' / 'provider-settings.json')

    def _ensure_directory(self):
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Provider credential storage must be a directory')
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(directory, 0o700)

    def load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError('Cannot open local provider credential file') from exc
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
                raise ValueError('Provider credential file must be a regular file with mode 0600')
            content = stream.read(MAX_STORE_BYTES + 1)
        if len(content) > MAX_STORE_BYTES:
            raise ValueError('Provider credential file is too large')
        try:
            return json.loads(content.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError('Local provider credential file is malformed') from exc

    def save(self, data: dict):
        self._ensure_directory()
        try:
            self.path.lstat()
        except FileNotFoundError:
            pass
        else:
            self.load()
        payload = json.dumps(serializable_provider_data(data), ensure_ascii=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
        _atomic_write(self.path, payload)


def allowed_origins_from_environment() -> frozenset[tuple[str, str, int]]:
    return parse_host_allowlist(os.environ.get('SQUARE_PROVIDER_HOST_ALLOWLIST'))
