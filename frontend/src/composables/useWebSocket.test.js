import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const loc  = { setConnected: vi.fn(), fetchLive: vi.fn(async () => {}), fetchSOS: vi.fn(async () => {}), fetchTrail: vi.fn(async () => {}) }
const fire = { refreshVisible: vi.fn(async () => {}), applyFireDataUpdated: vi.fn(async () => {}) }
vi.mock('../stores/locations', () => ({ useLocationsStore: () => loc }))
vi.mock('../stores/fire', () => ({ useFireStore: () => fire }))
vi.mock('vue', async (orig) => ({ ...(await orig()), onUnmounted: vi.fn() }))

import { useWebSocket } from './useWebSocket'

describe('useWebSocket fire refetch', () => {
  let sockets
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    sockets = []
    globalThis.WebSocket = vi.fn(function () { sockets.push(this); this.close = vi.fn() })
  })
  afterEach(() => { vi.useRealTimers() })

  it('the 45 s resync tick does not refetch fire layers, a reconnect does [B33]', async () => {
    const { connect } = useWebSocket()
    connect()
    sockets[0].onopen()                       // first connect: nothing to catch up on
    await vi.advanceTimersByTimeAsync(45_000 * 3)
    expect(loc.fetchLive).toHaveBeenCalledTimes(3)
    expect(fire.refreshVisible).not.toHaveBeenCalled()

    sockets[0].onclose()                      // drop, then reconnect
    await vi.advanceTimersByTimeAsync(3_000)
    sockets[1].onopen()
    expect(fire.refreshVisible).toHaveBeenCalledTimes(1)
  })

  it('fire_data_updated applies the update [B33]', () => {
    const { connect } = useWebSocket()
    connect()
    sockets[sockets.length - 1].onmessage({ data: JSON.stringify({ type: 'fire_data_updated', fetched_at: null }) })
    expect(fire.applyFireDataUpdated).toHaveBeenCalledTimes(1)
  })
})
