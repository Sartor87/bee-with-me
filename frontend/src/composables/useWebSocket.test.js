import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const loc  = { setConnected: vi.fn(), fetchLive: vi.fn(async () => {}), fetchSOS: vi.fn(async () => {}), fetchTrail: vi.fn(async () => {}) }
const fire = { retryFailed: vi.fn(async () => {}), refreshVisible: vi.fn(async () => {}), applyFireDataUpdated: vi.fn(async () => {}),
  applyFireZonesUpdated: vi.fn(async () => {}), fetchOpenAlerts: vi.fn(async () => {}), applyFireAlert: vi.fn(), applyFireAlertRepeat: vi.fn(async () => {}), applyFireAlertUpdated: vi.fn() }
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

  it('fire_zones_updated refetches zones through the store [B52]', () => {
    const { connect } = useWebSocket()
    connect()
    sockets[sockets.length - 1].onmessage({ data: JSON.stringify({ type: 'fire_zones_updated' }) })
    expect(fire.applyFireZonesUpdated).toHaveBeenCalledTimes(1)
  })

  it('every resync tick retries failed fire feeds, also after the first connect [B35]', async () => {
    const { connect } = useWebSocket()
    connect()
    sockets[0].onopen()
    await vi.advanceTimersByTimeAsync(45_000 * 2)
    expect(loc.fetchLive).toHaveBeenCalled()
    expect(fire.retryFailed).toHaveBeenCalledTimes(loc.fetchLive.mock.calls.length)
  })

  it('fire alarm messages reach the store [T17]', () => {
    const { connect } = useWebSocket()
    connect()
    const send = (m) => sockets[sockets.length - 1].onmessage({ data: JSON.stringify(m) })
    send({ type: 'fire_alert', id: 'a1' })
    send({ type: 'fire_alert_repeat', alert_ids: ['a1'] })
    send({ type: 'fire_alert_updated', id: 'a1', acknowledged_at: 'x' })
    expect(fire.applyFireAlert).toHaveBeenCalledTimes(1)
    expect(fire.applyFireAlertRepeat).toHaveBeenCalledTimes(1)
    expect(fire.applyFireAlertUpdated).toHaveBeenCalledTimes(1)
  })

  it('every resync tick re-pulls the open fire alarms, a missed push must not hide one [T17]', async () => {
    const { connect } = useWebSocket()
    connect()
    sockets[0].onopen()
    await vi.advanceTimersByTimeAsync(45_000 * 2)
    expect(fire.fetchOpenAlerts).toHaveBeenCalledTimes(loc.fetchLive.mock.calls.length)
    expect(fire.fetchOpenAlerts).toHaveBeenCalled()
  })
  it('after disconnect() a late onclose schedules no reconnect and no resync [B58]', async () => {
    const { connect, disconnect } = useWebSocket()
    connect()
    sockets[0].onopen()
    disconnect()
    vi.clearAllMocks()                        // drop the catch-up pull a reconnect onopen does
    sockets[0].onclose?.()                    // the browser fires close after socket.close()
    await vi.advanceTimersByTimeAsync(3_000 + 45_000 * 2)
    expect(sockets).toHaveLength(1)
    expect(loc.fetchLive).not.toHaveBeenCalled()
  })

  it('connect() after disconnect() works again and leaves one live socket [B58]', async () => {
    const { connect, disconnect } = useWebSocket()
    connect()
    disconnect()
    connect()
    sockets[0].onclose?.()                    // stale close from the first socket
    await vi.advanceTimersByTimeAsync(3_000)
    expect(sockets).toHaveLength(2)
    sockets[1].onclose()                      // unexpected drop still reconnects
    await vi.advanceTimersByTimeAsync(3_000)
    expect(sockets).toHaveLength(3)
  })
})
