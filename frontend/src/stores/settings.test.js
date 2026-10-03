import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../api', () => ({
  getSettings:  vi.fn(),
  putSettings:  vi.fn(),
  putHQInitial: vi.fn(),
}))

import { getSettings, putSettings, putHQInitial } from '../api'
import { useSettingsStore } from './settings'

const DEFAULTS = {
  hq_latitude: null, hq_longitude: null, is_hq_alarm_enabled: true, is_rescuer_alarm_enabled: true,
  hq_radius_m: 10000, rescuer_radius_m: 3000, alarm_max_age_hours: 24, repeat_minutes: 5,
  updated_at: '2026-10-02T10:00:00+00:00',
}

describe('useSettingsStore', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    getSettings.mockResolvedValue({ ...DEFAULTS })
    putSettings.mockImplementation(async (body) => ({ ...body, updated_at: 'later' }))
  })

  it('exposes HQ as {lat, lon} or null [T13]', async () => {
    const store = useSettingsStore()
    await store.fetchSettings()
    expect(store.hq).toBeNull()
    await store.setHQ(42.5, 24.5)
    expect(store.hq).toEqual({ lat: 42.5, lon: 24.5 })
  })

  it('saveSettings sends the full object without updated_at [T13]', async () => {
    const store = useSettingsStore()
    await store.fetchSettings()
    await store.saveSettings({ hq_radius_m: 5000 })
    const sent = putSettings.mock.calls[0][0]
    expect(sent.hq_radius_m).toBe(5000)
    expect(sent.rescuer_radius_m).toBe(3000)
    expect(sent).not.toHaveProperty('updated_at')
  })

  it('clearHQ empties both coordinates [T13]', async () => {
    const store = useSettingsStore()
    await store.fetchSettings()
    await store.clearHQ()
    expect(putSettings.mock.calls[0][0]).toMatchObject({ hq_latitude: null, hq_longitude: null })
  })

  it('migrateLocalHQ skips non-admins and keeps their local copy [T13]', async () => {
    localStorage.setItem('bwm.hq', JSON.stringify({ lat: 42.5, lon: 24.5 }))
    expect(await useSettingsStore().migrateLocalHQ(false)).toBe('skipped')
    expect(putHQInitial).not.toHaveBeenCalled()
    expect(localStorage.getItem('bwm.hq')).not.toBeNull()
  })

  it('migrateLocalHQ uploads once and clears the local copy [T13]', async () => {
    localStorage.setItem('bwm.hq', JSON.stringify({ lat: 42.5, lon: 24.5 }))
    putHQInitial.mockResolvedValue({ ...DEFAULTS, hq_latitude: 42.5, hq_longitude: 24.5 })
    const store = useSettingsStore()
    expect(await store.migrateLocalHQ(true)).toBe('uploaded')
    expect(putHQInitial).toHaveBeenCalledWith({ hq_latitude: 42.5, hq_longitude: 24.5 })
    expect(store.hq).toEqual({ lat: 42.5, lon: 24.5 })
    expect(localStorage.getItem('bwm.hq')).toBeNull()
  })

  it('migrateLocalHQ: another admin already set HQ, local copy dropped [T13]', async () => {
    localStorage.setItem('bwm.hq', JSON.stringify({ lat: 42.5, lon: 24.5 }))
    putHQInitial.mockRejectedValue('hq_already_set')
    expect(await useSettingsStore().migrateLocalHQ(true)).toBe('exists')
    expect(localStorage.getItem('bwm.hq')).toBeNull()
  })

  it('migrateLocalHQ: network failure keeps the local copy for next time [T13]', async () => {
    localStorage.setItem('bwm.hq', JSON.stringify({ lat: 42.5, lon: 24.5 }))
    putHQInitial.mockRejectedValue('Network Error')
    expect(await useSettingsStore().migrateLocalHQ(true)).toBe('failed')
    expect(localStorage.getItem('bwm.hq')).not.toBeNull()
  })

  it('migrateLocalHQ ignores missing or malformed local values [T13]', async () => {
    expect(await useSettingsStore().migrateLocalHQ(true)).toBe('skipped')
    localStorage.setItem('bwm.hq', '{"lat":"x"}')
    expect(await useSettingsStore().migrateLocalHQ(true)).toBe('skipped')
    expect(putHQInitial).not.toHaveBeenCalled()
  })
})
