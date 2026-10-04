import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('../api', () => ({
  getFireHotspots: vi.fn(), getFireBurntAreas: vi.fn(), getFireStatus: vi.fn(),
  getFireAlerts: vi.fn(), acknowledgeFireAlert: vi.fn(), acknowledgeAllFireAlerts: vi.fn(),
}))
vi.mock('../lib/fireTone', async (orig) => ({
  ...(await orig()),
  playFireTone: vi.fn(() => 'running'),
  toneState: vi.fn(() => 'running'),
  unlockFireTone: vi.fn(async () => 'running'),
}))

import { acknowledgeFireAlert } from '../api'
import { playFireTone, toneState, unlockFireTone } from '../lib/fireTone'
import FireAlarmBanner from './FireAlarmBanner.vue'
import { useFireStore } from '../stores/fire'
import { i18n } from '../i18n/index.js'
import en from '../i18n/en.js'
import bg from '../i18n/bg.js'

const alert = (id, extra = {}) => ({
  id, target_type: 'device', device_id: 'd1', full_name: 'Ivan Petrov', rank: 'Sergeant', distance_m: 1234,
  hotspot: { id: `h-${id}`, latitude: 42.5, longitude: 24.5, acquired_at: new Date(Date.now() - 12.5 * 60_000).toISOString(), source: 'viirs' },
  triggered_at: new Date().toISOString(), acknowledged_at: null, resolved_at: null, resolve_reason: null, ...extra,
})

async function mountBanner() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const page = { template: '<i />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/map', component: page }, { path: '/', component: page }],
  })
  router.push('/'); await router.isReady()
  const w = mount(FireAlarmBanner, { global: { plugins: [pinia, i18n, router] } })
  return { w, fire: useFireStore(), router }
}

describe('FireAlarmBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'en'
    toneState.mockReturnValue('running')
    playFireTone.mockReturnValue('running')
  })

  it('renders nothing without alerts [T17]', async () => {
    const { w } = await mountBanner()
    expect(w.find('.fire-banner').exists()).toBe(false)
  })

  it('says who, how far and how old the detection is [T17]', async () => {
    const { w, fire } = await mountBanner()
    fire.applyFireAlert(alert('a1'))
    await flushPromises()
    const text = w.text()
    expect(text).toContain('Ivan Petrov')
    expect(text).toContain('Sergeant')
    expect(w.find('.fb-distance').text()).toBe('1.2 km')
    expect(text).toContain('12 min ago')
    expect(w.find('.fire-banner').attributes('role')).toBe('alert')
  })

  it('HQ alerts say HQ, and Bulgarian uses its own text and decimal comma [T17]', async () => {
    i18n.global.locale.value = 'bg'
    const { w, fire } = await mountBanner()
    fire.applyFireAlert(alert('a1', { target_type: 'hq', full_name: null, rank: null }))
    await flushPromises()
    expect(w.find('.fb-name').text()).toBe(bg.fireAlarm.hq)
    expect(w.find('.fb-distance').text()).toBe('1,2 км')
  })

  it('an acknowledged alert stays listed, quiet, and the banner leaves alarm state [T17]', async () => {
    const { w, fire } = await mountBanner()
    fire.applyFireAlert(alert('a1'))
    acknowledgeFireAlert.mockResolvedValue(alert('a1', { acknowledged_at: 'now' }))
    await flushPromises()
    await w.find('.fb-btn-main').trigger('click')
    await flushPromises()
    expect(w.findAll('.fb-row')).toHaveLength(1)
    expect(w.find('.fb-row-acked').exists()).toBe(true)
    expect(w.find('.fire-banner-live').exists()).toBe(false)
    expect(w.find('.fire-banner').attributes('role')).toBe('region')
    expect(w.text()).toContain(en.fireAlarm.acknowledged)
  })

  it('acknowledge all only appears with more than one open alert [T17]', async () => {
    const { w, fire } = await mountBanner()
    fire.applyFireAlert(alert('a1'))
    await flushPromises()
    expect(w.text()).not.toContain(en.fireAlarm.acknowledgeAll)
    fire.applyFireAlert(alert('a2'))
    await flushPromises()
    expect(w.text()).toContain(en.fireAlarm.acknowledgeAll)
  })

  it('a failed acknowledge says so and keeps the alarm [T17]', async () => {
    const { w, fire } = await mountBanner()
    fire.applyFireAlert(alert('a1'))
    acknowledgeFireAlert.mockRejectedValue(new Error('Network Error'))
    await flushPromises()
    await w.find('.fb-btn-main').trigger('click')
    await flushPromises()
    expect(w.find('.fb-error').text()).toContain('Network Error')
    expect(w.find('.fire-banner-live').exists()).toBe(true)
  })

  it('plays one tone per ring and none for restored alerts [T17]', async () => {
    const { w, fire } = await mountBanner()
    fire.alerts = [alert('a1')]            // restored: no ring
    await flushPromises()
    expect(playFireTone).not.toHaveBeenCalled()
    fire.applyFireAlertRepeat({ alert_ids: ['a1'] })
    await flushPromises()
    expect(playFireTone).toHaveBeenCalledTimes(1)
    expect(w.find('.fb-sound').exists()).toBe(false)
  })

  it('shows "Sound blocked" visibly and plays the missed ring after the click [T17]', async () => {
    toneState.mockReturnValue('blocked')
    playFireTone.mockReturnValueOnce('blocked')
    const { w, fire } = await mountBanner()
    fire.applyFireAlert(alert('a1'))
    await flushPromises()
    expect(w.find('.fb-sound').text()).toBe(en.fireAlarm.soundBlocked)
    await w.find('.fb-sound').trigger('click')
    await flushPromises()
    expect(unlockFireTone).toHaveBeenCalled()
    expect(playFireTone).toHaveBeenCalledTimes(2)   // the blocked attempt, then once after the click
    expect(w.find('.fb-sound').exists()).toBe(false)
  })

  it('show on map requests focus on the hotspot and goes to the map [T17]', async () => {
    const { w, fire, router } = await mountBanner()
    fire.applyFireAlert(alert('a1'))
    await flushPromises()
    await w.findAll('.fb-btn').find(b => b.text() === en.fireAlarm.showOnMap).trigger('click')
    await flushPromises()
    expect(fire.focusRequest).toMatchObject({ latitude: 42.5, longitude: 24.5 })
    expect(router.currentRoute.value.path).toBe('/map')
  })

  it('shows a notice when the alarms could not be loaded [T17]', async () => {
    const { w, fire } = await mountBanner()
    fire.alertsFailed = true
    await flushPromises()
    expect(w.find('.fb-loadfail').text()).toBe(en.fireAlarm.loadFailed)
  })
})
