import { describe, it, expect } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useMap } from './useMap'

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} }

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

const zonesLayerOf = (api) => api.map().getLayers().getArray().find(l => l.getZIndex() === 3.4)

describe('useMap suppression zones', () => {
  it('draws one polygon per valid zone and skips unusable rows [T19]', () => {
    const { api, w } = mountMap()
    const n = api.setZones([
      { id: 'a', label: 'Solar farm', latitude: 42.5, longitude: 24.5, radius_m: 1000 },
      { id: 'b', label: 'broken', latitude: 'x', longitude: 24.5, radius_m: 1000 },
      { id: 'c', label: 'zero', latitude: 42.5, longitude: 24.5, radius_m: 0 },
    ])
    expect(n).toBe(1)
    expect(zonesLayerOf(api).getSource().getFeatures().map(f => f.getId())).toEqual(['a'])
    w.unmount()
  })

  it('a zone is a true 1000 m circle, not a Mercator-stretched one [T19]', () => {
    const { api, w } = mountMap()
    api.setZones([{ id: 'a', label: 'z', latitude: 42.5, longitude: 24.5, radius_m: 1000 }])
    const [x0, , x1] = zonesLayerOf(api).getSource().getFeatures()[0].getGeometry().getExtent()
    // At 42.5 N a projected metre is 1/cos(lat) of a real one: 2 km across is about 2.7 km projected.
    expect(x1 - x0).toBeGreaterThan(2600)
    expect(x1 - x0).toBeLessThan(2800)
    w.unmount()
  })

  it('setZones replaces the previous zones and clears on an empty list [T19]', () => {
    const { api, w } = mountMap()
    api.setZones([{ id: 'a', label: 'a', latitude: 42.5, longitude: 24.5, radius_m: 100 }])
    api.setZones([{ id: 'b', label: 'b', latitude: 42.5, longitude: 24.5, radius_m: 100 }])
    expect(zonesLayerOf(api).getSource().getFeatures().map(f => f.getId())).toEqual(['b'])
    api.setZones([])
    expect(zonesLayerOf(api).getSource().getFeatures()).toHaveLength(0)
    w.unmount()
  })

  it('the zones layer starts hidden and follows setFireLayerVisible [T19]', () => {
    const { api, w } = mountMap()
    const layer = zonesLayerOf(api)
    expect(layer.getVisible()).toBe(false)
    api.setFireLayerVisible('zones', true)
    expect(layer.getVisible()).toBe(true)
    api.setFireLayerVisible('zones', false)
    expect(layer.getVisible()).toBe(false)
    w.unmount()
  })

  it('a long label is clipped on the map [T19]', () => {
    const { api, w } = mountMap()
    api.setZones([{ id: 'a', label: 'x'.repeat(100), latitude: 42.5, longitude: 24.5, radius_m: 100 }])
    const style = zonesLayerOf(api).getSource().getFeatures()[0].getStyle()
    expect(style.getText().getText().length).toBeLessThanOrEqual(28)
    w.unmount()
  })
})
