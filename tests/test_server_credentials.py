# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Managed server credential-store behavior without requiring cloud credentials."""
from types import SimpleNamespace
import unittest

from mathagent.gui.google_secret_manager import GoogleSecretManagerStore
from mathagent.gui.server_credentials import (ServerCredentialStoreError,
                                              secret_id_for,
                                              server_credential_store_from_environment)


class AlreadyExists(Exception):
    pass


class NotFound(Exception):
    pass


class GoogleApiError(Exception):
    pass


class FakeSecretManagerClient:
    def __init__(self):
        self.secrets = {}
        self.fail_access = False

    def create_secret(self, *, request):
        name = f"{request['parent']}/secrets/{request['secret_id']}"
        if name in self.secrets:
            raise AlreadyExists()
        self.secrets[name] = None

    def add_secret_version(self, *, request):
        if request['parent'] not in self.secrets:
            raise NotFound()
        self.secrets[request['parent']] = request['payload']['data']

    def access_secret_version(self, *, request):
        if self.fail_access:
            raise GoogleApiError('synthetic provider key must not leak')
        name = request['name'].removesuffix('/versions/latest')
        if name not in self.secrets or self.secrets[name] is None:
            raise NotFound()
        return SimpleNamespace(payload=SimpleNamespace(data=self.secrets[name]))

    def delete_secret(self, *, request):
        if request['name'] not in self.secrets:
            raise NotFound()
        del self.secrets[request['name']]


class ServerCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeSecretManagerClient()
        self.store = GoogleSecretManagerStore(
            'sample-project-123',
            client=self.client,
            already_exists_errors=(AlreadyExists,),
            not_found_errors=(NotFound,),
            api_errors=(GoogleApiError,),
        )
        self.account_id = 'a' * 64
        self.provider_id = 'openai/deepinfra'

    def test_google_adapter_scopes_secret_ids_and_supports_create_rotate_and_delete(self):
        secret_id = secret_id_for(self.account_id, self.provider_id)
        self.assertRegex(secret_id, r'^square-harness-[a-f0-9]{64}-[a-f0-9]{16}$')
        self.assertIsNone(self.store.load_api_key(self.account_id, self.provider_id))

        self.store.save_api_key(self.account_id, self.provider_id, 'first-test-key')
        self.assertEqual(self.store.load_api_key(self.account_id, self.provider_id), 'first-test-key')
        self.store.save_api_key(self.account_id, self.provider_id, 'rotated-test-key')
        self.assertEqual(self.store.load_api_key(self.account_id, self.provider_id), 'rotated-test-key')

        self.store.delete_api_key(self.account_id, self.provider_id)
        self.assertIsNone(self.store.load_api_key(self.account_id, self.provider_id))
        self.store.delete_api_key(self.account_id, self.provider_id)

    def test_store_errors_are_reported_without_echoing_credential_values(self):
        self.client.fail_access = True
        with self.assertRaises(ServerCredentialStoreError) as caught:
            self.store.load_api_key(self.account_id, self.provider_id)
        self.assertNotIn('synthetic provider key', str(caught.exception))

    def test_registry_is_optional_and_rejects_unknown_managers(self):
        self.assertIsNone(server_credential_store_from_environment({}))
        with self.assertRaisesRegex(ValueError, 'supported: google'):
            server_credential_store_from_environment({'SQUARE_SERVER_SECRET_MANAGER': 'unknown'})
        with self.assertRaisesRegex(ValueError, 'GOOGLE_CLOUD_PROJECT'):
            server_credential_store_from_environment({'SQUARE_SERVER_SECRET_MANAGER': 'google'})

    def test_secret_id_rejects_untrusted_account_and_provider_identifiers(self):
        with self.assertRaises(ValueError):
            secret_id_for('alice@example.test', self.provider_id)
        with self.assertRaises(ValueError):
            secret_id_for(self.account_id, '../provider')


if __name__ == '__main__':
    unittest.main()
