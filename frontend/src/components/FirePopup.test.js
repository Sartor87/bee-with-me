import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import FirePopup from './FirePopup.vue'
import { i18n } from '../i18n/index.js'

const mountPopup = (kind, properties) =>
  mount(FirePopup, { props: { kind, properties }, global: { plugins: [i18n] } })

describe('FirePopup unknown values', () => {
  it('burnt area with null area and dates shows unknown, never 0 ha or 1970 [B32]', () => {
    const text = mountPopup('burnt_area', { id: 'x', area_ha: null, started_at: null, ended_at: null }).text()
    expect(text).not.toContain('1970')
    expect(text).not.toContain('0 ha')
    expect(text).not.toContain('burned')
    expect(text.match(/unknown/g)).toHaveLength(3)
  })

  it('empty string and undefined count as unknown too [B32]', () => {
    const text = mountPopup('burnt_area', { id: 'x', area_ha: '', started_at: undefined, ended_at: '' }).text()
    expect(text).not.toContain('1970')
    expect(text).not.toContain('0 ha')
  })

  it('a real zero area still reads as 0 ha [B32]', () => {
    const text = mountPopup('burnt_area', { id: 'x', area_ha: 0, started_at: '2026-10-01T00:00:00Z', ended_at: null }).text()
    expect(text).toContain('0 ha')
  })

  it('hotspot with null acquired_at shows unknown and no epoch time [B32]', () => {
    const text = mountPopup('hotspot', { id: 'x', source: 'viirs', acquired_at: null }).text()
    expect(text).not.toContain('1970')
    expect(text).toContain('unknown')
  })

  it('an unknown state renders the unknown text, not the raw i18n key [B32]', () => {
    const text = mountPopup('hotspot', { id: 'x', source: 'viirs', acquired_at: null, state: 'weird' }).text()
    expect(text).not.toContain('fire.state')
    expect(text).not.toContain('weird')
    expect(text.match(/unknown/g).length).toBeGreaterThanOrEqual(2)
  })

  it('known states still render their label [B32]', () => {
    const text = mountPopup('hotspot', { id: 'x', source: 'viirs', acquired_at: '2026-10-01T00:00:00Z', state: 'dismissed' }).text()
    expect(text).toContain('Dismissed')
  })
})
