import { describe, it, expect, vi, afterEach } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useMap } from './useMap'

// useMap needs a component context (onMounted). Mount a throwaway one and hand back the API.
function mountMap() {
  let api
  const Comp = defineComponent({
    setup() {
      api = useMap(ref(null), ref([]), ref({}), () => {}, () => {}, ref({}), () => {})
      return () => h('div')
    },
  })
  const w = mount(Comp)
  return { api, w }
}

const hotspotFC = (n) => ({
  type: 'FeatureCollection',
  features: Array.from({ length: n }, (_, i) => ({
    type: 'Feature', id: `h${i}`, properties: { source: 'viirs' },
    geometry: { type: 'Point', coordinates: [25 + i * 0.01, 42.5] },
  })),
})

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }

describe('useMap fire feature guard', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('setHotspots / setBurntAreas report success for readable GeoJSON [B34]', () => {
    const { api, w } = mountMap()
    expect(api.setHotspots(hotspotFC(2))).toBe(true)
    expect(api.setBurntAreas({ type: 'FeatureCollection', features: [] })).toBe(true)
    w.unmount()
  })

  it('unreadable GeoJSON keeps the previous features and returns false without logging content [B34]', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { api, w } = mountMap()
    api.setHotspots(hotspotFC(2))
    const layer = api.map()?.getLayers().getArray().find(l => l.getSource?.()?.getFeatures?.().some(f => f.getId() === 'h0'))
    expect(layer).toBeTruthy()
    expect(layer.getSource().getFeatures()).toHaveLength(2)

    const bad = { type: 'FeatureCollection', features: 'not-an-array', secret: 'Ivan Petrov' }
    expect(api.setHotspots(bad)).toBe(false)
    expect(layer.getSource().getFeatures()).toHaveLength(2)
    expect(api.setBurntAreas(bad)).toBe(false)
    expect(warn).toHaveBeenCalled()
    expect(JSON.stringify(warn.mock.calls)).not.toContain('Ivan')
    w.unmount()
  })
})
