import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h, ref, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import Icon from 'ol/style/Icon'
import Circle from 'ol/style/Circle'
import { useMap } from './useMap'
import { ringFor, safePhotoUrl, clearPhotoCache } from '../lib/photoMarker'
import { LIVE, STALE, LOST } from '../lib/freshness'

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }

// jsdom has no canvas and never loads images: record the drawing, and let each test settle the load.
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

const settle = (i, ok = true) => { ok ? loads[i].onload() : loads[i].onerror() }

function mountMap(list, showPhotos = ref(true)) {
  let api
  const positions = ref(list)
  const Comp = defineComponent({
    setup() {
      api = useMap(ref(null), positions, ref({}), () => {}, () => {}, ref({}), () => {}, showPhotos)
      return () => h('div')
    },
  })
  const w = mount(Comp)
  return { api, w, positions }
}

const now = () => new Date().toISOString()
const pos = (id, extra = {}) => ({
  device_id: id, dev_sn: 1, latitude: 42.5, longitude: 24.5, received_at: now(), recorded_at: now(),
  full_name: 'Test Rescuer', photo_url: '/uploads/a.jpg', ...extra,
})
const marker = (api, id) => {
  let f
  api.map().getLayers().forEach(l => { f ??= l.getSource?.()?.getFeatureById?.(id) })
  return f
}
const imageOf = (api, id) => marker(api, id).getStyle().getImage()

describe('photo marker choice [F1]', () => {
  it('draws a round photo once the image has loaded, flag on and photo_url set [F1]', async () => {
    const { api, w } = mountMap([])
    api.refreshMarkers([pos('d1')])
    expect(imageOf(api, 'd1')).toBeInstanceOf(Circle)   // dot while loading
    settle(0)
    await Promise.resolve()
    expect(imageOf(api, 'd1')).toBeInstanceOf(Icon)
    expect(loads).toHaveLength(1)
    w.unmount()
  })

  it('keeps the dot with the flag off, without a photo, in the groups view and for an external URL [F1]', () => {
    const off = mountMap([], ref(false))
    off.api.refreshMarkers([pos('off')])
    const on = mountMap([])
    on.api.refreshMarkers([
      pos('none', { photo_url: null }),
      pos('team', { displayLabel: 'Team A' }),
      pos('ext', { photo_url: 'https://evil.example/p.jpg' }),
      pos('proto', { photo_url: '//evil.example/p.jpg' }),
      pos('data', { photo_url: 'data:image/png;base64,AAAA' }),
    ])
    expect(loads).toHaveLength(0)   // nothing was even requested
    for (const id of ['none', 'team', 'ext', 'proto', 'data']) expect(imageOf(on.api, id)).toBeInstanceOf(Circle)
    expect(imageOf(off.api, 'off')).toBeInstanceOf(Circle)
    off.w.unmount(); on.w.unmount()
  })

  it('a failed load falls back to the dot silently [F1]', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const log = vi.spyOn(console, 'log').mockImplementation(() => {})
    const { api, w } = mountMap([])
    api.refreshMarkers([pos('d1')])
    settle(0, false)
    api.refreshMarkers([pos('d1')])
    await Promise.resolve()
    expect(imageOf(api, 'd1')).toBeInstanceOf(Circle)
    expect(loads).toHaveLength(1)   // the failure is cached, no retry storm
    expect(warn).not.toHaveBeenCalled()
    expect(err).not.toHaveBeenCalled()
    expect(log).not.toHaveBeenCalled()
    w.unmount()
  })

  it('caches the image per URL across devices [F1]', () => {
    const { api, w } = mountMap([])
    api.refreshMarkers([pos('a'), pos('b'), pos('c', { photo_url: '/uploads/other.jpg' })])
    expect(loads.map(i => i.src).sort()).toEqual(['/uploads/a.jpg', '/uploads/other.jpg'])
    w.unmount()
  })

  it('redraws every marker as soon as the flag changes [F1]', async () => {
    const flag = ref(true)
    const { api, w, positions } = mountMap([], flag)
    positions.value = [pos('d1')]
    api.refreshMarkers(positions.value)
    settle(0)
    await Promise.resolve()
    expect(imageOf(api, 'd1')).toBeInstanceOf(Icon)
    flag.value = false
    await nextTick()
    expect(imageOf(api, 'd1')).toBeInstanceOf(Circle)
    flag.value = true
    await nextTick()
    expect(imageOf(api, 'd1')).toBeInstanceOf(Icon)
    w.unmount()
  })

  it('keeps the name label above the photo [F1]', async () => {
    const { api, w } = mountMap([])
    api.refreshMarkers([pos('d1')])
    settle(0)
    await Promise.resolve()
    const text = marker(api, 'd1').getStyle().getText()
    expect(text.getText()).toBe('Test Rescuer')
    expect(text.getOffsetY()).toBeLessThan(-17)
    w.unmount()
  })
})

describe('photo ring per state [F1]', () => {
  const base = { color: '#22aa66', isSOS: false, freshness: LIVE, noFix: false }
  const alphaOf = (c) => Number(c.match(/,([\d.]+)\)$/)[1])

  it('live uses the group colour, solid [F1]', () => {
    expect(ringFor(base)).toMatchObject({ color: '#22aa66', width: 3, dash: null, photoAlpha: 1 })
  })
  it('SOS is red and thicker than any other ring, photo at full strength while live (a quiet SOS is dimmed, see B54) [F1]', () => {
    const sos = ringFor({ ...base, isSOS: true, freshness: LIVE })
    expect(sos.color).toBe('#ef4444')
    expect(sos.width).toBeGreaterThan(ringFor(base).width)
    expect(sos.photoAlpha).toBe(1)
  })
  it('red is used for SOS only [F1]', () => {
    for (const f of [LIVE, STALE, LOST]) {
      for (const noFix of [true, false]) {
        expect(ringFor({ ...base, freshness: f, noFix }).color).not.toBe('#ef4444')
      }
    }
  })
  it('stale is a grey ring with a semi-transparent photo [F1]', () => {
    const r = ringFor({ ...base, freshness: STALE })
    expect(r.color).toMatch(/^rgba\(156,163,175/)
    expect(r.photoAlpha).toBeGreaterThan(0.3)
    expect(r.photoAlpha).toBeLessThan(1)
  })
  it('lost is fainter than stale [F1]', () => {
    const lost = ringFor({ ...base, freshness: LOST })
    const stale = ringFor({ ...base, freshness: STALE })
    expect(lost.photoAlpha).toBeLessThan(stale.photoAlpha)
    expect(alphaOf(lost.color)).toBeLessThan(alphaOf(stale.color))
  })
  it('no GNSS fix is a dashed yellow ring [F1]', () => {
    expect(ringFor({ ...base, noFix: true })).toMatchObject({ color: '#facc15', dash: [5, 3] })
  })
  it('the drawn canvas uses the ring state and clips the photo to a circle [F1]', async () => {
    const { api, w } = mountMap([])
    api.refreshMarkers([pos('d1', { sos_active: true })])
    settle(0)
    await Promise.resolve()
    expect(calls).toContainEqual(['stroke', '#ef4444'])
    expect(calls.some(c => c[0] === 'clip')).toBe(true)
    expect(calls.some(c => c[0] === 'drawImage')).toBe(true)
    w.unmount()
  })
})

describe('safePhotoUrl [F1]', () => {
  const origin = 'http://localhost:5173'
  it('accepts local upload paths and same-origin absolute URLs only [F1]', () => {
    expect(safePhotoUrl('/uploads/a.jpg', origin)).toBe('/uploads/a.jpg')
    expect(safePhotoUrl('http://localhost:5173/uploads/a.jpg', origin)).toBe('/uploads/a.jpg')
    expect(safePhotoUrl('http://localhost:8000/uploads/a.jpg', origin)).toBeNull()
    expect(safePhotoUrl('https://cdn.example/a.jpg', origin)).toBeNull()
    expect(safePhotoUrl('//cdn.example/a.jpg', origin)).toBeNull()
    expect(safePhotoUrl('javascript:alert(1)', origin)).toBeNull()
    expect(safePhotoUrl('', origin)).toBeNull()
    expect(safePhotoUrl(null, origin)).toBeNull()
  })
})
