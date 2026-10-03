import { describe, it, expect } from 'vitest'
import { hotspotStyleKey, freshnessKind, isFireDataStale, worstUpstreamState, oldestFetchedAt } from './fireStyle'

const NOW = Date.parse('2026-10-02T12:00:00Z')
const hoursAgo = (h) => new Date(NOW - h * 3600e3).toISOString()

describe('hotspotStyleKey', () => {
  it('fades EFFIS points by age: 24 h / 3 d / 7 d [T11]', () => {
    expect(hotspotStyleKey({ source: 'viirs', state: 'active', acquired_at: hoursAgo(2) }, NOW)).toBe('age_24h')
    expect(hotspotStyleKey({ source: 'viirs', state: 'active', acquired_at: hoursAgo(30) }, NOW)).toBe('age_3d')
    expect(hotspotStyleKey({ source: 'viirs', state: 'active', acquired_at: hoursAgo(100) }, NOW)).toBe('age_7d')
  })

  it('treats a future or unparseable timestamp as the freshest band [T11]', () => {
    expect(hotspotStyleKey({ source: 'viirs', state: 'active', acquired_at: hoursAgo(-3) }, NOW)).toBe('age_24h')
    expect(hotspotStyleKey({ source: 'viirs', state: 'active', acquired_at: 'garbage' }, NOW)).toBe('age_24h')
  })

  it('field reports have their own style regardless of age [T11]', () => {
    expect(hotspotStyleKey({ source: 'field_report', state: 'active', acquired_at: hoursAgo(100) }, NOW)).toBe('field_report')
  })

  it('state wins over source and age [T11]', () => {
    for (const state of ['dismissed', 'extinguished', 'suppressed']) {
      expect(hotspotStyleKey({ source: 'field_report', state, acquired_at: hoursAgo(1) }, NOW)).toBe(state)
    }
  })
})

describe('freshnessKind', () => {
  it('maps upstream states to pill kinds [T11]', () => {
    expect(freshnessKind('live')).toBe('live')
    expect(freshnessKind('no_recent_detections')).toBe('quiet')
    expect(freshnessKind('error')).toBe('error')
    expect(freshnessKind(undefined)).toBe('unknown')
  })
})

describe('feed freshness helpers', () => {
  it('flags an as-of time older than three poll cycles, never a null one [T11]', () => {
    expect(isFireDataStale(hoursAgo(0.5), NOW)).toBe(false)
    expect(isFireDataStale(hoursAgo(2), NOW)).toBe(true)
    expect(isFireDataStale(null, NOW)).toBe(false)
  })

  it('combines feeds pessimistically [T11]', () => {
    expect(worstUpstreamState(['live', 'error'])).toBe('error')
    expect(worstUpstreamState(['live', 'no_recent_detections'])).toBe('no_recent_detections')
    expect(worstUpstreamState([])).toBe('unknown')
    expect(oldestFetchedAt([hoursAgo(1), hoursAgo(3)])).toBe(hoursAgo(3))
    expect(oldestFetchedAt([hoursAgo(1), null])).toBe(null)
  })
})
