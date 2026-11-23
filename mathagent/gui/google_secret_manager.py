# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Google Cloud Secret Manager adapter for remembered server provider keys."""
from collections.abc import Mapping
import re

from .server_credentials import (MAX_CREDENTIAL_BYTES, ServerCredentialStore,
                                 ServerCredentialStoreError, register_server_credential_store,
                                 secret_id_for)


@register_server_credential_store('google')
class GoogleSecretManagerStore(ServerCredentialStore):
    name = 'google'
    display_name = 'Google Cloud Secret Manager'

    def __init__(self, project_id: str, *, client=None, already_exists_errors=(),
                 not_found_errors=(), api_errors=()):
        project_id_is_valid = (isinstance(project_id, str)
                               and (re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project_id)
                                    or re.fullmatch(r'[0-9]{6,20}', project_id)))
        if not project_id_is_valid:
            raise ValueError('GOOGLE_CLOUD_PROJECT must be a valid project ID or number')
        self.project_id = project_id
        if client is None:
            try:
                from google.api_core.exceptions import AlreadyExists, GoogleAPICallError, NotFound
                from google.cloud import secretmanager
            except ImportError as exc:
                raise ValueError(
                    'Google Secret Manager requires the square-harness[gcp-secrets] extra'
                ) from exc
            client = secretmanager.SecretManagerServiceClient()
            already_exists_errors = (AlreadyExists,)
            not_found_errors = (NotFound,)
            api_errors = (GoogleAPICallError,)
        self.client = client
        self.already_exists_errors = tuple(already_exists_errors)
        self.not_found_errors = tuple(not_found_errors)
        self.api_errors = tuple(api_errors)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> 'GoogleSecretManagerStore':
        project_id = environ.get('GOOGLE_CLOUD_PROJECT', '').strip()
        if not project_id:
            raise ValueError('Google Secret Manager requires GOOGLE_CLOUD_PROJECT')
        return cls(project_id)

    def _resource_name(self, account_id: str, provider_id: str) -> str:
        return f'projects/{self.project_id}/secrets/{secret_id_for(account_id, provider_id)}'

    def load_api_key(self, account_id: str, provider_id: str) -> str | None:
        name = self._resource_name(account_id, provider_id) + '/versions/latest'
        try:
            response = self.client.access_secret_version(request={'name': name})
        except self.not_found_errors:
            return None
        except self.api_errors as exc:
            raise ServerCredentialStoreError(
                'Google Secret Manager could not access the remembered provider key'
            ) from exc
        payload = response.payload.data
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CREDENTIAL_BYTES:
            raise ServerCredentialStoreError('Google Secret Manager returned an invalid provider key')
        try:
            return payload.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ServerCredentialStoreError('Google Secret Manager returned an invalid provider key') from exc

    def save_api_key(self, account_id: str, provider_id: str, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key or len(api_key.encode('utf-8')) > MAX_CREDENTIAL_BYTES:
            raise ValueError('Provider API key must contain 1 to 16384 bytes')
        name = self._resource_name(account_id, provider_id)
        parent = f'projects/{self.project_id}'
        try:
            self.client.create_secret(request={
                'parent': parent,
                'secret_id': name.rsplit('/', 1)[1],
                'secret': {'replication': {'automatic': {}}},
            })
        except self.already_exists_errors:
            pass
        except self.api_errors as exc:
            raise ServerCredentialStoreError(
                'Google Secret Manager could not create the provider key resource'
            ) from exc
        try:
            self.client.add_secret_version(request={
                'parent': name,
                'payload': {'data': api_key.encode('utf-8')},
            })
        except self.api_errors as exc:
            raise ServerCredentialStoreError(
                'Google Secret Manager could not save the provider key'
            ) from exc

    def delete_api_key(self, account_id: str, provider_id: str) -> None:
        name = self._resource_name(account_id, provider_id)
        try:
            self.client.delete_secret(request={'name': name})
        except self.not_found_errors:
            return
        except self.api_errors as exc:
            raise ServerCredentialStoreError(
                'Google Secret Manager could not delete the remembered provider key'
            ) from exc
