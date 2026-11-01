import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { TaskEntry, TaskSnapshot, TaskSummary } from '../src/api.ts'
import { chatWork, reduceActivity, reduceChat } from '../src/concurrency.ts'

const task = (id: string, target: string): TaskSummary => ({
  id, target, kind: 'chat', label: 'Assistant', title: target, state: 'running',
  started: 1, finished: null, error: null, result_status: null,
})
const entry = (id: string, chat: string): TaskEntry => ({
  task: task(id, chat), activity: [], live: { chat, kind: 'message', user: chat, steps: [] },
})
const first = entry('first', 'chat-a')
const second = entry('second', 'chat-b')
const snapshot: TaskSnapshot = {
  seq: 1, task: first.task, activity: [], live: first.live, tasks: [first, second],
  approvals: [], queue: [], routing: [], concurrency: { chats: 2, requests: 2, active: 2 },
}

test('interleaved chat deltas and activity stay attached to their own task after reconnect', () => {
  let current: TaskSnapshot = { ...snapshot, tasks: JSON.parse(JSON.stringify(snapshot.tasks)) }
  current = reduceChat(current, { task: 'first', chat: 'chat-a', seq: 2, kind: 'start' })
  current = reduceChat(current, { task: 'second', chat: 'chat-b', seq: 3, kind: 'start' })
  current = reduceChat(current, { task: 'first', chat: 'chat-a', seq: 4, kind: 'delta', text: 'Alpha' })
  current = reduceChat(current, { task: 'second', chat: 'chat-b', seq: 5, kind: 'delta', thinking: 'Beta thought', text: 'Beta' })
  current = reduceActivity(current, 'second', { kind: 'tool', text: 'Read b.tex', time: 1 }, 6)
  // Reconnect replaces state with a server snapshot before later events continue.
  current = JSON.parse(JSON.stringify(current))
  current = reduceChat(current, { task: 'first', chat: 'chat-a', seq: 7, kind: 'delta', text: ' continued' })
  assert.deepEqual(chatWork(current, 'chat-a').active?.live?.steps, [{ type: 'call', text: 'Alpha continued', thinking: '' }])
  assert.deepEqual(chatWork(current, 'chat-b').active?.live?.steps, [{ type: 'call', text: 'Beta', thinking: 'Beta thought' }])
  assert.deepEqual(chatWork(current, 'chat-a').active?.activity, [])
  assert.equal(chatWork(current, 'chat-b').active?.activity[0].text, 'Read b.tex')
  assert.deepEqual(snapshot.tasks?.[0].live?.steps, [])
})
