import assert from 'node:assert/strict'
import test from 'node:test'
import { api } from '../src/api.ts'
import { resumeAdditionalSeconds } from '../src/review.ts'

test('near-exhausted time is offered as an explicit extension, with an 80 percent boundary', () => {
  assert.equal(resumeAdditionalSeconds({ used: 719, limit: 900 }), 0)
  assert.equal(resumeAdditionalSeconds({ used: 720, limit: 900 }), 900)
  assert.equal(resumeAdditionalSeconds({ used: 900, limit: 900 }), 900)
  assert.equal(resumeAdditionalSeconds({ used: 900, limit: null }), 0)
  assert.equal(resumeAdditionalSeconds({ used: 0, limit: 0 }), 0)
})

test('ordinary research resume does not raise a cap; only explicit extra time is sent', async () => {
  const original = globalThis.fetch
  const bodies: Record<string, unknown>[] = []
  globalThis.fetch = async (_input, options) => {
    bodies.push(JSON.parse(String(options?.body)))
    return new Response(JSON.stringify({ task: {}, id: 'saved-job' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    await api.resumeResearch('saved-job')
    await api.resumeResearch('saved-job', true, 900)
    await api.resumeResearch('saved-job', true, 0)
    assert.deepEqual(bodies, [{ queue: false }, { queue: true, additional_seconds: 900 }, { queue: true }])
  } finally { globalThis.fetch = original }
})
