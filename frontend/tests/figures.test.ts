import assert from 'node:assert/strict'
import test from 'node:test'
import { experimentFigure, figureUrl } from '../src/figures.ts'

test('only figures of sandbox runs become image URLs', () => {
  const id = 'exp-20261031-225206-9e3454'
  assert.equal(experimentFigure(`.mathagent/experiments/${id}/figure-1.png`), `/api/experiments/${id}/figure-1.png`)
  assert.equal(experimentFigure(`./.mathagent/experiments/${id}/plot.png`), `/api/experiments/${id}/plot.png`)
  for (const url of [`https://example.org/x.png`, `/etc/passwd`, `.mathagent/experiments/${id}/../gui-token`,
    `.mathagent/experiments/${id}/values.csv`, `.mathagent/experiments/exp-1/figure-1.png`,
    `.mathagent/experiments/${id}/.hidden.png`, `javascript:alert(1)//.png`]) {
    assert.equal(experimentFigure(url), '', url)
  }
  assert.equal(figureUrl(id, 'figure-1.png'), `/api/experiments/${id}/figure-1.png`)
})
