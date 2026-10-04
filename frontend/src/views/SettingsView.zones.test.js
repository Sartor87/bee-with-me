import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { ApiError } from '../api/client'

vi.mock('../api', () => ({
  getSettings: vi.fn(), putSettings: vi.fn(), putHQ: vi.fn(), putHQInitial: vi.fn(), getMe: vi.fn(),
  getFireStatus: vi.fn(), getSuppressionZones: vi.fn(), updateSuppressionZone: vi.fn(), disableSuppressionZone: vi.fn(),
}))

import * as api from '../api'
import SettingsView from './SettingsView.vue'
import { useAuthStore } from '../stores/auth'
import { i18n } from '../i18n/index.js'
import en from '../i18n/en.js'

const SETTINGS = {
  hq_latitude: 42.5, hq_longitude: 24.5, is_hq_alarm_enabled: true, is_rescuer_alarm_enabled: true,
  hq_radius_m: 10000, rescuer_radius_m: 3000, alarm_max_age_hours: 24, repeat_minutes: 5, updated_at: 't0',
}
const HOSTILE = '<img src=x onerror="window.__pwned = true">'
const ZONES = [
  { id: 'z1', label: 'Solar farm', latitude: 42.5, longitude: 24.5, radius_m: 1000, is_active: true, notes: HOSTILE },
  { id: 'z2', label: 'Old quarry', latitude: 42.6, longitude: 24.6, radius_m: 500, is_active: false, notes: null },
]

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { role: 'admin', full_name: 'Admin' }
  const w = mount(SettingsView, { global: { plugins: [pinia, i18n] } })
  await flushPromises()
  return w
}

describe('SettingsView: suppression zones and alarm targets', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'en'
    api.getSettings.mockResolvedValue({ ...SETTINGS })
    api.getFireStatus.mockResolvedValue({ targets: { hq: true, rescuers: 3 } })
    api.getSuppressionZones.mockResolvedValue(ZONES.map(z => ({ ...z })))
  })

  it('lists active and disabled zones, loaded with include_disabled [T19]', async () => {
    const w = await mountView()
    expect(api.getSuppressionZones).toHaveBeenCalledWith({ include_disabled: true })
    const items = w.findAll('[data-testid="zone-item"]')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('Solar farm')
    expect(items[0].text()).toContain('1000 m')
    expect(items[1].text()).toContain(en.settings.zones.disabled)
    expect(w.get('[data-testid="zones-disabled-note"]').text()).toContain('48 hours')
  })

  it('zone notes render as text, never as markup [T19]', async () => {
    const w = await mountView()
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).toContain(HOSTILE)
    expect(window.__pwned).toBeUndefined()
  })

  it('Disable calls the API and keeps the zone listed as disabled [T19]', async () => {
    api.disableSuppressionZone.mockResolvedValue({ ...ZONES[0], is_active: false })
    const w = await mountView()
    await w.get('[data-testid="zone-disable"]').trigger('click')
    await flushPromises()
    expect(api.disableSuppressionZone).toHaveBeenCalledWith('z1')
    expect(w.findAll('[data-testid="zone-enable"]')).toHaveLength(2)
  })

  it('Re-enable sends the whole zone with is_active true [T19]', async () => {
    api.updateSuppressionZone.mockResolvedValue({ ...ZONES[1], is_active: true })
    const w = await mountView()
    await w.get('[data-testid="zone-enable"]').trigger('click')
    await flushPromises()
    expect(api.updateSuppressionZone).toHaveBeenCalledWith('z2', {
      label: 'Old quarry', latitude: 42.6, longitude: 24.6, radius_m: 500, notes: null, is_active: true,
    })
  })

  it('Edit changes label and radius and keeps the centre and state [T19]', async () => {
    api.updateSuppressionZone.mockResolvedValue({ ...ZONES[0], label: 'Solar park', radius_m: 800 })
    const w = await mountView()
    await w.get('[data-testid="zone-edit"]').trigger('click')
    await w.get('[data-testid="zone-label"]').setValue('Solar park')
    await w.get('[data-testid="zone-radius"]').setValue('800')
    await w.get('[data-testid="zone-form"]').trigger('submit')
    await flushPromises()
    expect(api.updateSuppressionZone).toHaveBeenCalledWith('z1', {
      label: 'Solar park', latitude: 42.5, longitude: 24.5, radius_m: 800, notes: HOSTILE, is_active: true,
    })
    expect(w.find('[data-testid="zone-form"]').exists()).toBe(false)
    expect(w.text()).toContain('Solar park')
  })

  it('a failed disable shows the error on that zone and keeps it active [T19]', async () => {
    api.disableSuppressionZone.mockRejectedValue(new ApiError('Zone not found', 404))
    const w = await mountView()
    await w.get('[data-testid="zone-disable"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain(en.fire.errors.notFound)
    expect(w.findAll('[data-testid="zone-disable"]')).toHaveLength(1)
  })

  it('an empty list says so [T19]', async () => {
    api.getSuppressionZones.mockResolvedValue([])
    const w = await mountView()
    expect(w.get('[data-testid="zones-empty"]').text()).toBe(en.settings.zones.empty)
  })

  it('a failed load shows a retry that reloads [T19]', async () => {
    api.getSuppressionZones.mockRejectedValueOnce(new ApiError('boom', 500))
    const w = await mountView()
    expect(w.text()).toContain(en.settings.zones.loadFailed)
    await w.get('.zones-failed button').trigger('click')
    await flushPromises()
    expect(w.findAll('[data-testid="zone-item"]')).toHaveLength(2)
  })

  it('shows the alarm targets: HQ set and the rescuer count [T19]', async () => {
    const w = await mountView()
    expect(w.get('[data-testid="targets-hq"]').text()).toBe(en.settings.targets.hqSet)
    expect(w.get('[data-testid="targets-rescuers"]').text()).toBe('3')
    expect(w.find('[data-testid="targets-warning"]').exists()).toBe(false)
  })

  it('warns when the rescuer alarm is on and no rescuer has a recent position [T19]', async () => {
    api.getFireStatus.mockResolvedValue({ targets: { hq: false, rescuers: 0 } })
    const w = await mountView()
    expect(w.get('[data-testid="targets-hq"]').text()).toBe(en.settings.targets.hqNotSet)
    expect(w.get('[data-testid="targets-warning"]').text()).toBe(en.settings.targets.noRescuers)
  })

  it('no warning when the rescuer alarm is off [T19]', async () => {
    api.getSettings.mockResolvedValue({ ...SETTINGS, is_rescuer_alarm_enabled: false })
    api.getFireStatus.mockResolvedValue({ targets: { hq: true, rescuers: 0 } })
    const w = await mountView()
    expect(w.find('[data-testid="targets-warning"]').exists()).toBe(false)
  })

  it('targets null from the server reads as unavailable, not as zero [T19]', async () => {
    api.getFireStatus.mockResolvedValue({ targets: null })
    const w = await mountView()
    expect(w.get('[data-testid="targets-unavailable"]').text()).toBe(en.settings.targets.unavailable)
    expect(w.find('[data-testid="targets-rescuers"]').exists()).toBe(false)
  })

  it('the account card and logout are still there [T19]', async () => {
    const w = await mountView()
    expect(w.find('[data-test="logout"]').exists()).toBe(true)
  })
})
