import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import Icon from 'ol/style/Icon'
import Circle from 'ol/style/Circle'
import { useMap, makeMarkerStyle } from './useMap'
import { ringFor, clearPhotoCache, markerZIndex, photoImage, photoCanvas } from '../lib/photoMarker'
import { LIVE, STALE, LOST } from '../lib/freshness'

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }

let loads
let calls
beforeEach(() => {
  // restyles wait for an animation frame (B56): a frame that fires on the next microtask here
  vi.stubGlobal('requestAnimationFrame', (fn) => { queueMicrotask(() => fn(0)); return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  clearPhotoCache()
  loads = []
  calls = []
  globalThis.Image = class {
    set src(v) { this._src = v; loads.push(this) }
    get src() { return this._src }
    get naturalWidth() { return 100 }
    get naturalHeight() { return 80 }
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function () {
    const rec = (name) => (...a) => calls.push([name, ...a])
    const ctx = {
      set globalAlpha(v) { calls.push(['alpha', v]) },
      set strokeStyle(v) { calls.push(['stroke', v]) },
      set lineWidth(v) { calls.push(['width', v]) },
    }
    for (const n of ['scale', 'save', 'restore', 'beginPath', 'arc', 'clip', 'drawImage', 'stroke', 'setLineDash']) ctx[n] = rec(n)
    return ctx
  })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function mountMap(list) {
  let api
  const Comp = defineComponent({
    setup() {
      api = useMap(ref(null), ref(list), ref({}), () => {}, () => {}, ref({}), () => {}, ref(true))
      return () => h('div')
    },
  })
  const w = mount(Comp)
  return { api, w }
}
const now = () => new Date().toISOString()
const pos = (id, extra = {}) => ({
  device_id: id, dev_sn: 1, latitude: 42.5, longitude: 24.5, received_at: now(), recorded_at: now(),
  full_name: 'T', photo_url: '/uploads/a.jpg', ...extra,
})
const marker = (api, id) => {
  let f
  api.map().getLayers().forEach(l => { f ??= l.getSource?.()?.getFeatureById?.(id) })
  return f
}
const old = (min) => new Date(Date.now() - min * 60_000).toISOString()
const strokeColours = () => calls.filter(c => c[0] === 'stroke' && typeof c[1] === 'string')

describe('marker draw order [B54]', () => {
  it('SOS beats live beats stale beats lost [B54]', () => {
    const z = (sos, f) => makeMarkerStyle('#fff', sos, 'x', false, f, false).getZIndex()
    expect(z(true, LOST)).toBeGreaterThan(z(false, LIVE))
    expect(z(false, LIVE)).toBeGreaterThan(z(false, STALE))
    expect(z(false, STALE)).toBeGreaterThan(z(false, LOST))
    expect(markerZIndex(true, LOST)).toBeGreaterThan(markerZIndex(false, LIVE))
  })

  it('a photo marker carries the same zIndex, an SOS photo is on top [B54]', async () => {
    const { api, w } = mountMap([])
    api.refreshMarkers([pos('live'), pos('sos', { sos_active: true, received_at: old(45) })])
    loads.forEach(l => l.onload())
    await Promise.resolve()
    const s = marker(api, 'sos').getStyle(), l = marker(api, 'live').getStyle()
    expect(s.getImage()).toBeInstanceOf(Icon)
    expect(s.getZIndex()).toBeGreaterThan(l.getZIndex())
    w.unmount()
  })
})

describe('stale SOS photo [B54]', () => {
  it('keeps the red ring, dims the photo and adds a grey inner ring when stale or lost [B54]', () => {
    expect(ringFor({ color: '#fff', isSOS: true, freshness: LIVE, noFix: false })).toMatchObject({ color: '#ef4444', photoAlpha: 1, innerColor: null })
    const stale = ringFor({ color: '#fff', isSOS: true, freshness: STALE, noFix: false })
    const lost = ringFor({ color: '#fff', isSOS: true, freshness: LOST, noFix: false })
    expect(stale).toMatchObject({ color: '#ef4444', photoAlpha: 0.55 })
    expect(lost).toMatchObject({ color: '#ef4444', photoAlpha: 0.3 })
    expect(stale.innerColor).toBeTruthy()
    expect(lost.innerColor).toBeTruthy()
  })

  it('draws the inner ring on the canvas only for a quiet SOS [B54]', () => {
    const img = { naturalWidth: 10, naturalHeight: 10 }
    photoCanvas('/u/a.jpg', img, ringFor({ color: '#fff', isSOS: true, freshness: STALE }), 1)
    expect(strokeColours()).toHaveLength(2)
    calls.length = 0
    photoCanvas('/u/b.jpg', img, ringFor({ color: '#fff', isSOS: true, freshness: LIVE }), 1)
    expect(strokeColours()).toHaveLength(1)
  })
})

describe('photo retry and shared notification [B54]', () => {
  it('a failed URL is retried after the backoff, not before [B54]', () => {
    const t0 = 1_000_000
    vi.spyOn(Date, 'now').mockReturnValue(t0)
    expect(photoImage('/u/a.jpg', t0)).toBeNull()
    loads[0].onerror()
    photoImage('/u/a.jpg', t0 + 30_000)
    expect(loads).toHaveLength(1)
    photoImage('/u/a.jpg', t0 + 60_000)
    expect(loads).toHaveLength(2)
    Date.now.mockReturnValue(t0 + 60_000)
    loads[1].onerror()   // second failure: the backoff doubles to 120 s
    photoImage('/u/a.jpg', t0 + 60_000 + 100_000)
    expect(loads).toHaveLength(2)
    photoImage('/u/a.jpg', t0 + 60_000 + 120_000)
    expect(loads).toHaveLength(3)
    loads[2].onload()
    expect(photoImage('/u/a.jpg', t0 + 999_999)).not.toBeNull()
    expect(loads).toHaveLength(3)
  })

  it('every open map restyles when an image loads [B54]', async () => {
    const a = mountMap([]), b = mountMap([])
    a.api.refreshMarkers([pos('d1')])
    b.api.refreshMarkers([pos('d1')])
    expect(marker(b.api, 'd1').getStyle().getImage()).toBeInstanceOf(Circle)
    loads[0].onload()
    await Promise.resolve()
    expect(marker(a.api, 'd1').getStyle().getImage()).toBeInstanceOf(Icon)
    expect(marker(b.api, 'd1').getStyle().getImage()).toBeInstanceOf(Icon)
    a.w.unmount(); b.w.unmount()
  })
})
