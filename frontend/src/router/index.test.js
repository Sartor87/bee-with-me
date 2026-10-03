import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({ getMe: vi.fn(), login: vi.fn() }))

import { getMe } from '../api'
import { guard } from './index.js'
import { useAuthStore } from '../stores/auth'

const adminRoute = { meta: { adminOnly: true } }

describe('router guard', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('token', 'x')
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('redirects a viewer from /settings to the map [B38]', async () => {
    useAuthStore().user = { role: 'viewer' }
    expect(await guard(adminRoute)).toBe('/map')
  })

  it('loads the user once, then lets an admin in [B38]', async () => {
    getMe.mockResolvedValue({ role: 'admin' })
    expect(await guard(adminRoute)).toBeUndefined()
    expect(getMe).toHaveBeenCalledTimes(1)
  })

  it('redirects to the map when the role cannot be confirmed [B38]', async () => {
    getMe.mockRejectedValue('boom')
    expect(await guard(adminRoute)).toBe('/map')
  })
})
