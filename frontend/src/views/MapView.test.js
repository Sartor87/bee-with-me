import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../api', () => ({
  getSettings: vi.fn(), putSettings: vi.fn(), putHQ: vi.fn(), putHQInitial: vi.fn(), getMe: vi.fn(),
  getGroupsWithMembers: vi.fn(), getSerialStatus: vi.fn(),
  getLive: vi.fn(), getSOS: vi.fn(), getTrail: vi.fn(),
}))
vi.mock('../composables/useWebSocket', () => ({ useWebSocket: () => ({ connect: vi.fn() }) }))
const mapArgs = vi.hoisted(() => ({ onHQPlace: null }))
vi.mock('../composables/useMap', () => ({
  BASEMAPS: [],
  useMap: (...args) => {
    mapArgs.onHQPlace = args[6]
    return new Proxy({ map: () => null }, {
      get: (t, k) => (k in t ? t[k] : vi.fn()),
    })
  },
}))
vi.mock('ol/Overlay', () => ({ default: class {} }))
vi.mock('ol/proj', () => ({ fromLonLat: (c) => c, toLonLat: (c) => c }))

import { getSettings, putHQ, getGroupsWithMembers, getSerialStatus } from '../api'
import MapView from './MapView.vue'
import { useAuthStore } from '../stores/auth'
import { useLocationsStore } from '../stores/locations'
import { useSettingsStore } from '../stores/settings'
import { i18n } from '../i18n/index.js'
import en from '../i18n/en.js'

const SETTINGS = {
  hq_latitude: 42.5, hq_longitude: 24.5, is_hq_alarm_enabled: true, is_rescuer_alarm_enabled: true,
  hq_radius_m: 10000, rescuer_radius_m: 3000, alarm_max_age_hours: 24, repeat_minutes: 5, updated_at: 't0',
}

async function mountMap(role) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { role }
  const loc = useLocationsStore()
  loc.fetchLive = vi.fn(); loc.fetchSOS = vi.fn(); loc.fetchTrail = vi.fn()
  const w = mount(MapView, { global: { plugins: [pinia, i18n], stubs: { SOSToast: true, FirePopup: true } } })
  await flushPromises()
  return w
}
const texts = (w) => w.findAll('button').map(b => b.text())

describe('MapView HQ controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    i18n.global.locale.value = 'en'
    getSettings.mockResolvedValue({ ...SETTINGS })
    putHQ.mockImplementation(async (b) => ({ ...SETTINGS, ...b, updated_at: 't1' }))
    getGroupsWithMembers.mockResolvedValue({ items: [] })
    getSerialStatus.mockResolvedValue(null)
  })

  it('HQ buttons are hidden for non-admins [B38]', async () => {
    const w = await mountMap('viewer')
    expect(texts(w)).not.toContain(en.map.hqMove)
    expect(texts(w)).not.toContain(en.map.hqClear)
    expect(texts(w)).not.toContain(en.map.hqSet)
  })

  it('Clear HQ asks for confirmation before writing [B38]', async () => {
    const w = await mountMap('admin')
    const clear = w.findAll('button').find(b => b.text() === en.map.hqClear)
    await clear.trigger('click')
    expect(putHQ).not.toHaveBeenCalled()
    expect(w.text()).toContain('The HQ fire alarm cannot work without HQ')
    await w.findAll('button').find(b => b.text() === en.map.hqClearCancel).trigger('click')
    expect(putHQ).not.toHaveBeenCalled()
    await w.findAll('button').find(b => b.text() === en.map.hqClear).trigger('click')
    await w.findAll('button').find(b => b.text() === en.map.hqClearYes).trigger('click')
    await flushPromises()
    expect(putHQ).toHaveBeenCalledWith({ hq_latitude: null, hq_longitude: null })
    expect(useSettingsStore().hq).toBeNull()
  })

  it('a slow failing HQ click followed by a successful one leaves no error [B39]', async () => {
    const w = await mountMap('admin')
    let failFirst
    putHQ.mockImplementationOnce(() => new Promise((_, reject) => { failFirst = () => reject(new Error('Network Error')) }))
    mapArgs.onHQPlace({ lat: 1, lon: 1 })     // slow, will fail
    mapArgs.onHQPlace({ lat: 2, lon: 2 })     // queued behind it, will succeed
    await flushPromises()
    failFirst()
    await flushPromises()
    expect(putHQ).toHaveBeenCalledTimes(2)
    expect(w.text()).not.toContain(en.map.hqSaveFailed)
    expect(useSettingsStore().hq).toEqual({ lat: 2, lon: 2 })
  })

  it('the latest HQ click failing still shows the error [B39]', async () => {
    const w = await mountMap('admin')
    putHQ.mockRejectedValueOnce(new Error('Network Error'))
    mapArgs.onHQPlace({ lat: 3, lon: 3 })
    await flushPromises()
    expect(w.text()).toContain(en.map.hqSaveFailed)
  })

  it('a failed settings load shows an amber note, disables Set HQ, and Retry recovers [B38]', async () => {
    getSettings.mockRejectedValueOnce('Network Error')
    const w = await mountMap('admin')
    expect(w.text()).toContain('Settings unavailable: HQ not shown')
    const setBtn = w.findAll('button').find(b => b.text() === en.map.hqSet)
    expect(setBtn.attributes('disabled')).toBeDefined()
    await w.findAll('button').find(b => b.text() === en.map.settingsRetry).trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('Settings unavailable')
    const moveBtn = w.findAll('button').find(b => b.text() === en.map.hqMove)
    expect(moveBtn.attributes('disabled')).toBeUndefined()
  })
})
