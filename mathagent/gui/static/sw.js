// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

const CACHE = 'square-harness-static-v1'
const ASSET_PREFIX = '/assets/'
const STATIC_FILES = new Set([
  '/manifest.webmanifest',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-maskable-512.png',
  '/apple-touch-icon.png',
])
const MAX_CACHED_ASSETS = 100

function isStatic(path) {
  return STATIC_FILES.has(path) || path.startsWith(ASSET_PREFIX)
}

function localAsset(reference, base) {
  try {
    const url = new URL(reference, new URL(base, self.location.origin))
    return url.origin === self.location.origin && isStatic(url.pathname) ? url.pathname : null
  } catch {
    return null
  }
}

async function cacheAsset(cache, path, cssAssets) {
  const response = await fetch(path, { cache: 'no-store' })
  if (!response.ok) throw new Error(`Could not cache app asset ${path}: ${response.status}`)
  await cache.put(path, response.clone())
  if (path.endsWith('.css')) {
    const css = await response.text()
    for (const match of css.matchAll(/url\(\s*(?:"([^"]+)"|'([^']+)'|([^'")]+))\s*\)/g)) {
      const asset = localAsset(match[1] || match[2] || match[3], path)
      if (asset) cssAssets.add(asset)
    }
  }
}

async function cacheShell(response) {
  if (!response.ok) throw new Error(`Could not cache app shell: ${response.status}`)
  const cache = await caches.open(CACHE)
  const html = await response.clone().text()
  await cache.put('/', response.clone())

  const assets = new Set(STATIC_FILES)
  for (const match of html.matchAll(/(?:src|href)=["']([^"']+)["']/g)) {
    const asset = localAsset(match[1], self.location.origin)
    if (asset) assets.add(asset)
  }
  const cssAssets = new Set()
  for (const path of assets) await cacheAsset(cache, path, cssAssets)
  for (const path of cssAssets) await cacheAsset(cache, path, new Set())

  const cached = await cache.keys()
  const appAssets = cached.filter((request) => new URL(request.url).pathname.startsWith(ASSET_PREFIX))
  for (const request of appAssets.slice(0, Math.max(0, appAssets.length - MAX_CACHED_ASSETS))) {
    await cache.delete(request)
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const response = await fetch('/', { cache: 'no-store' })
    await cacheShell(response)
    await self.skipWaiting()
  })())
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys()
    await Promise.all(names.filter((name) => name.startsWith('square-harness-static-') && name !== CACHE)
      .map((name) => caches.delete(name)))
    await self.clients.claim()
  })())
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  const isApi = url.pathname === '/api' || url.pathname.startsWith('/api/')
  if (request.method !== 'GET' || url.origin !== self.location.origin || isApi) return

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(async (response) => {
      if (response.ok) {
        const cache = await caches.open(CACHE)
        await cache.put('/', response.clone())
      }
      return response
    }).catch(async () => (await caches.match('/')) || Response.error()))
    return
  }

  if (!isStatic(url.pathname)) return
  event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then(async (response) => {
    if (response.ok) {
      const cache = await caches.open(CACHE)
      await cache.put(request, response.clone())
      const cachedAssets = (await cache.keys()).filter((item) =>
        new URL(item.url).pathname.startsWith(ASSET_PREFIX))
      for (const stale of cachedAssets.slice(0, Math.max(0, cachedAssets.length - MAX_CACHED_ASSETS))) {
        await cache.delete(stale)
      }
    }
    return response
  })))
})
