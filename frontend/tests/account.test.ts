import { test } from 'node:test'
import assert from 'node:assert/strict'
import { api, ApiError, onUnauthorized } from '../src/api.ts'

test('hosted identity is optional locally and preserves verified account data and authentication errors', async () => {
  const original = globalThis.fetch
  let unauthorized = 0
  const unsubscribe = onUnauthorized(() => { unauthorized += 1 })
  try {
    globalThis.fetch = async (input, init) => {
      assert.equal(input, '/api/account')
      assert.equal(init?.credentials, 'same-origin')
      return Response.json({ error: 'Unknown endpoint' }, { status: 404 })
    }
    assert.equal(await api.account(), null)
    const account = { email: 'person@example.test', logout_url: 'https://sh.example.test/cdn-cgi/access/logout' }
    globalThis.fetch = async () => Response.json(account)
    assert.deepEqual(await api.account(), account)
    globalThis.fetch = async () => new Response('<html>Sign in</html>', { headers: { 'Content-Type': 'text/html' } })
    assert.equal(await api.account(), null)
    globalThis.fetch = async () => Response.json({ error: 'Authentication required' }, { status: 401 })
    await assert.rejects(api.account(), (error: unknown) => error instanceof ApiError && error.status === 401)
    assert.equal(unauthorized, 1)
  } finally {
    globalThis.fetch = original
    unsubscribe()
  }
})
