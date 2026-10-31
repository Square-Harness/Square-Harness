import assert from 'node:assert/strict'
import test from 'node:test'
import { parse, href, notebook } from '../src/router.ts'

const job = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const chat = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
test('each notebook has a reachable new page and saved-job route', () => {
  for (const mode of ['prove', 'literature', 'referee', 'writeup'] as const) {
    assert.deepEqual(parse(href(mode)), { mode, id: null, tab: null, from: null })
    assert.deepEqual(parse(href(mode, job, 'sources', chat)), { mode, id: job, tab: 'sources', from: chat })
  }
})
test('invalid routes are safe and legacy worker conversations keep their notebook', () => {
  assert.equal(parse('#/unknown').mode, 'free')
  assert.equal(parse('#/prove/not-a-uuid').id, null)
  assert.equal(parse(href('prove', job) + '?from=invalid').from, null)
  assert.equal(parse('#/critic').mode, 'free')
  assert.equal(notebook(parse(href('critic', chat)).mode), 'free')
})
