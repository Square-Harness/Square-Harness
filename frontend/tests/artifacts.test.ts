import assert from 'node:assert/strict'
import test from 'node:test'
import { artifactsOf, artifactUrl, displayOf, experimentImage } from '../src/artifacts.ts'

const run = 'exp-20261031-225206-9e3454'

test('only images of sandbox runs become image URLs', () => {
  assert.equal(experimentImage(`.mathagent/experiments/${run}/figure-1.png`), `/api/artifacts/${run}/figure-1.png`)
  assert.equal(experimentImage(`./.mathagent/experiments/${run}/plot.svg`), `/api/artifacts/${run}/plot.svg`)
  assert.equal(experimentImage(`/api/artifacts/${run}/figure-1.png`), `/api/artifacts/${run}/figure-1.png`)
  for (const url of ['https://example.org/x.png', '/etc/passwd', `.mathagent/experiments/${run}/../gui-token`,
    `.mathagent/experiments/${run}/values.csv`, `.mathagent/experiments/${run}/page.html`,
    `.mathagent/experiments/exp-1/figure-1.png`, `.mathagent/experiments/${run}/.hidden.png`,
    'javascript:alert(1)//.png', `/api/artifacts/${run}/page.html`]) {
    assert.equal(experimentImage(url), '', url)
  }
})

test('artifacts are shown by MIME type, and old figure items still render', () => {
  assert.deepEqual(['image/png', 'image/svg+xml', 'text/html', 'application/pdf', 'text/csv'].map(displayOf),
    ['image', 'image', 'page', 'document', 'data'])
  const legacy = artifactsOf({ role: 'experiment', content: '', time: '', experiment: run,
    figures: [{ name: 'figure-1.png', width: 800, height: 500 }] })
  assert.deepEqual(legacy, [{ artifact_id: `${run}/figure-1.png`, filename: 'figure-1.png', mime_type: 'image/png',
    url: artifactUrl(run, 'figure-1.png'), width: 800, height: 500 }])
})
