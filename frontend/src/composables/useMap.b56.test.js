import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useMap } from './useMap'
import { clearPhotoCache, photoImage } from '../lib/photoMarker'

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }

let loads
beforeEach(() => {
  clearPhotoCache(); loads = []
  globalThis.Image = class {
    set src(v) { this._src = v; loads.push(this) }
    get src() { return this._src }
    get naturalWidth() { return 100 }
    get naturalHeight() { return 80 }
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => new Proxy({}, { get: () => () => {}, set: () => true }))
})
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

const now = () => new Date().toISOString()
const pos = (id) => ({ device_id: id, dev_sn: 1, latitude: 42.5, longitude: 24.5, received_at: now(), recorded_at: now(), photo_url: `/uploads/${id}.jpg` })
function mountMap(list) {
  const Comp = defineComponent({
    setup() { useMap(ref(null), ref(list), ref({}), () => {}, () => {}, ref({}), () => {}, ref(true)); return () => h('div') },
  })
  return mount(Comp)
}

describe('photo restyles [B56]', () => {
  it('photos finishing in separate tasks cause one restyle per frame, not one each [B56]', () => {
    vi.useFakeTimers()
    const frames = []
    vi.stubGlobal('requestAnimationFrame', (fn) => { frames.push(fn); return frames.length })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    mountMap([])
    const spy = vi.fn()
    for (let i = 0; i < 20; i++) photoImage(`/uploads/${i}.jpg`)
    loads.forEach(l => { l.onload(); spy() })
    expect(frames.length).toBe(1)   // twenty loads, one scheduled frame
    vi.unstubAllGlobals()
  })

  it('a pending restyle is cancelled and later loads do not touch an unmounted map [B56]', () => {
    const frames = []
    const cancel = vi.fn()
    vi.stubGlobal('requestAnimationFrame', (fn) => { frames.push(fn); return frames.length })
    vi.stubGlobal('cancelAnimationFrame', cancel)
    const w = mountMap([pos('a')])
    photoImage('/uploads/a.jpg'); loads[0].onload()
    expect(frames.length).toBe(1)
    w.unmount()
    expect(cancel).toHaveBeenCalledTimes(1)
    photoImage('/uploads/b.jpg'); loads[1].onload()
    expect(frames.length).toBe(1)   // unsubscribed: no new frame requested by the unmounted map
    vi.unstubAllGlobals()
  })
})
