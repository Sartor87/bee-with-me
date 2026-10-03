import { describe, it, expect, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('../api', () => ({ getMe: vi.fn(), login: vi.fn() }))

import AppLayout from './AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import { i18n } from '../i18n/index.js'

async function navFor(role) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { role, full_name: 'Test' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<i />' } }],
  })
  router.push('/')
  await router.isReady()
  const w = mount(AppLayout, {
    global: { plugins: [pinia, i18n, router], stubs: { SOSBanner: true, RouterView: true } },
  })
  return w.findAll('a.nav-item').map(a => a.attributes('href'))
}

describe('AppLayout navigation', () => {
  it('hides adminOnly items from non-admins [B38]', async () => {
    expect(await navFor('viewer')).not.toContain('/settings')
    expect(await navFor('rescuer')).not.toContain('/settings')
  })

  it('shows them to admins [B38]', async () => {
    expect(await navFor('admin')).toContain('/settings')
  })
})
