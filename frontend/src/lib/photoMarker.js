// Round photo markers for the live map (F1). Pure helpers plus a per-URL image cache; the
// OpenLayers style itself is assembled in useMap.js.
//
// The ring keeps every state the plain dot shows (BP-01): SOS red and thicker, stale and lost
// grey with a fainter photo, no GNSS fix yellow and dashed, otherwise the group colour.
// No glow anywhere: glow is an alarm signal.

import { LIVE, LOST } from './freshness'
import { hexToRgba } from './color'

export const PHOTO_SIZE = 34   // outer diameter in CSS px, ring included

const SOS_RED = '#ef4444'
const NO_FIX  = '#facc15'
const GREY    = '#9ca3af'

/** Same-origin photo URL or null. A photo is only ever loaded from this server (TP-01): an
 *  absolute URL to another host, a protocol-relative one and any data:/javascript: URL are
 *  refused, and the map keeps the dot. */
export function safePhotoUrl(raw, origin = globalThis.location?.origin) {
  if (typeof raw !== 'string' || !raw.trim() || !origin) return null
  try {
    const u = new URL(raw.trim(), origin)
    if (u.origin !== origin) return null
    return u.pathname + u.search
  } catch {
    return null
  }
}

/** Ring and photo opacity for one marker state. */
export function ringFor({ color, isSOS, freshness, noFix }) {
  const photoAlpha = freshness === LOST ? 0.3 : freshness === LIVE ? 1 : 0.55
  if (isSOS) {
    // Red stays reserved for SOS; a dashed red ring says "SOS, and no satellite fix".
    return { color: SOS_RED, width: 5, dash: noFix ? [5, 3] : null, photoAlpha: 1 }
  }
  const base = freshness === LOST ? hexToRgba(GREY, 0.4)
    : freshness !== LIVE ? hexToRgba(GREY, 0.8)
    : color
  return { color: noFix ? NO_FIX : base, width: 3, dash: noFix ? [5, 3] : null, photoAlpha }
}

// ---- image cache -------------------------------------------------------------------------

const MAX_CACHED_CANVASES = 400
const images = new Map()     // url -> { state: 'loading' | 'ready' | 'failed', img }
const canvases = new Map()   // ring/url/dpr key -> canvas

/** The decoded image for `url` when ready, else null. The first call starts the load and
 *  `onLoad` runs once it succeeds. A failure is remembered and silent (no console output,
 *  nothing with a name or URL in it): the marker just stays a dot. */
export function photoImage(url, onLoad) {
  let entry = images.get(url)
  if (!entry) {
    const img = new Image()
    entry = { state: 'loading', img }
    images.set(url, entry)
    img.onload  = () => { entry.state = 'ready'; onLoad?.() }
    img.onerror = () => { entry.state = 'failed' }
    img.src = url
  }
  return entry.state === 'ready' ? entry.img : null
}

export function clearPhotoCache() { images.clear(); canvases.clear() }

/** A circular canvas: clipped photo, ring on the outer edge. Null when no 2D context exists. */
export function photoCanvas(url, img, ring, dpr = 1) {
  const key = `${url}|${ring.color}|${ring.width}|${ring.dash ?? ''}|${ring.photoAlpha}|${dpr}`
  const hit = canvases.get(key)
  if (hit) return hit
  const px = Math.round(PHOTO_SIZE * dpr)
  const canvas = document.createElement('canvas')
  canvas.width = px
  canvas.height = px
  const ctx = canvas.getContext?.('2d')
  if (!ctx) return null
  ctx.scale(dpr, dpr)
  const r = PHOTO_SIZE / 2
  const inner = r - ring.width
  const iw = img.naturalWidth || img.width || 1
  const ih = img.naturalHeight || img.height || 1
  const side = Math.min(iw, ih)   // centre-crop to a square
  ctx.save()
  ctx.beginPath()
  ctx.arc(r, r, inner, 0, Math.PI * 2)
  ctx.clip()
  ctx.globalAlpha = ring.photoAlpha
  ctx.drawImage(img, (iw - side) / 2, (ih - side) / 2, side, side, r - inner, r - inner, inner * 2, inner * 2)
  ctx.restore()
  ctx.beginPath()
  ctx.arc(r, r, r - ring.width / 2, 0, Math.PI * 2)
  ctx.lineWidth = ring.width
  ctx.strokeStyle = ring.color
  ctx.setLineDash?.(ring.dash ?? [])
  ctx.stroke()
  if (canvases.size >= MAX_CACHED_CANVASES) canvases.clear()
  canvases.set(key, canvas)
  return canvas
}
