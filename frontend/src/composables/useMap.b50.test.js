import { describe, it, expect, vi, afterEach } from 'vitest'
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

const now = () => new Date().toISOString()
const pos = (id, extra = {}) => ({
  device_id: id, dev_sn: 1, latitude: 42.5, longitude: 24.5, received_at: now(), recorded_at: now(), ...extra,
})
const markerIds = (api) => {
  const ids = []
  api.map().getLayers().forEach(l => l.getSource?.()?.getFeatures?.().forEach(f => ids.push(f.getId())))
  return ids
}

describe('useMap marker guard', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('draws a marker when groups arrives as a JSON string [B50]', () => {
    const { api, w } = mountMap()
    api.refreshMarkers([pos('d1', { groups: '[{"id":"g","color":"#112233","is_leader":true}]' })])
    expect(markerIds(api)).toContain('d1')
    w.unmount()
  })

  it('one row that throws does not stop the others [B50]', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { api, w } = mountMap()
    const bad = pos('bad', { full_name: 'Ivan Petrov' })
    Object.defineProperty(bad, 'latitude', { get() { throw new Error('boom Ivan Petrov') } })
    api.refreshMarkers([pos('a'), bad, pos('c')])
    const ids = markerIds(api)
    expect(ids).toContain('a')
    expect(ids).toContain('c')
    expect(JSON.stringify(console.warn.mock.calls)).not.toContain('Ivan')
    w.unmount()
  })
})
