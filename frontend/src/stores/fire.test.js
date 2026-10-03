import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../api', () => ({
  getFireHotspots:   vi.fn(),
  getFireBurntAreas: vi.fn(),
  getFireStatus:     vi.fn(),
}))

import { getFireHotspots, getFireBurntAreas } from '../api'
import { useFireStore } from './fire'

const FC = (n, extra = {}) => ({ type: 'FeatureCollection', features: Array.from({ length: n }, (_, i) => ({ id: String(i) })), ...extra })

describe('useFireStore', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    getFireHotspots.mockResolvedValue(FC(2, { fetched_at: '2026-10-02T10:00:00+00:00', upstream_state: 'live' }))
    getFireBurntAreas.mockResolvedValue(FC(1))
  })

  it('starts with every layer off and nothing loaded [T11]', () => {
    const store = useFireStore()
    expect(store.layers).toEqual({ burnt: false, hotspots: false, zones: false })
    expect(store.anyLayerOn).toBe(false)
    expect(store.hotspots.features).toEqual([])
  })

  it('turning a layer on fetches only that layer and remembers it [T11]', async () => {
    const store = useFireStore()
    await store.setLayer('hotspots', true)
    expect(getFireHotspots).toHaveBeenCalledTimes(1)
    expect(getFireBurntAreas).not.toHaveBeenCalled()
    expect(store.hotspots.features).toHaveLength(2)
    expect(store.fetchedAt).toBe('2026-10-02T10:00:00+00:00')
    expect(store.upstreamState).toBe('live')
    expect(JSON.parse(localStorage.getItem('bwm.fireLayers'))).toEqual({ burnt: false, hotspots: true, zones: false })
  })

  it('restores layer state from localStorage and survives garbage in it [T11]', () => {
    localStorage.setItem('bwm.fireLayers', JSON.stringify({ burnt: true }))
    expect(useFireStore().layers).toEqual({ burnt: true, hotspots: false, zones: false })
    setActivePinia(createPinia())
    localStorage.setItem('bwm.fireLayers', '{not json')
    expect(useFireStore().layers).toEqual({ burnt: false, hotspots: false, zones: false })
  })

  it('fire_data_updated updates status and refetches only visible layers [T11]', async () => {
    const store = useFireStore()
    await store.setLayer('burnt', true)
    vi.clearAllMocks()
    getFireBurntAreas.mockResolvedValue(FC(3))
    await store.applyFireDataUpdated({ fetched_at: '2026-10-02T11:00:00+00:00', upstream_state: 'error' })
    expect(store.fetchedAt).toBe('2026-10-02T11:00:00+00:00')
    expect(store.upstreamState).toBe('error')
    expect(getFireBurntAreas).toHaveBeenCalledTimes(1)
    expect(getFireHotspots).not.toHaveBeenCalled()
    expect(store.burntAreas.features).toHaveLength(3)
  })

  it('a failed fetch keeps the previous data [T11]', async () => {
    const store = useFireStore()
    await store.setLayer('hotspots', true)
    getFireHotspots.mockRejectedValueOnce('Network Error')
    await expect(store.refreshVisible()).rejects.toBe('Network Error')
    expect(store.hotspots.features).toHaveLength(2)
  })
})
