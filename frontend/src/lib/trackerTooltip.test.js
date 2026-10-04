import { describe, it, expect } from 'vitest'
import { renderTrackerTooltip } from './trackerTooltip'

const EVIL = '<img src=x onerror=alert(1)>'

describe('tracker tooltip', () => {
  it('team tooltip shows names, rank, description and members as literal text [B52]', () => {
    const el = document.createElement('div')
    renderTrackerTooltip(el, {
      displayLabel: EVIL, full_name: EVIL, rank: '<b>cpt</b>', mgrs: '35TLG1', sos_active: true,
    }, {
      description: '<script>alert(2)</script><img src=y onerror=alert(3)>',
      members: [{ full_name: EVIL, rank: EVIL, is_leader: true }, { full_name: '<svg onload=alert(4)>', is_leader: false }],
    })
    expect(el.querySelector('img, script, svg, b')).toBeNull()
    expect(el.textContent).toContain(EVIL)
    expect(el.textContent).toContain('<script>alert(2)</script>')
    expect(el.textContent).toContain('<svg onload=alert(4)>')
    expect(el.textContent).toContain('SOS')
    expect(el.querySelector('strong').textContent).toBe(EVIL)
  })

  it('individual tooltip renders name, phone and device name as text and keeps the facts [B52]', () => {
    const el = document.createElement('div')
    renderTrackerTooltip(el, {
      device_name: EVIL, dev_sn: 7, phone: EVIL, mgrs: '35TLG2', altitude_m: 812, battery_voltage: 3.94,
      repeater_mode: true, recorded_at: '2026-10-04T10:00:00Z',
    }, null)
    expect(el.querySelector('img')).toBeNull()
    expect(el.querySelector('strong').textContent).toBe(EVIL)
    const t = el.textContent
    expect(t).toContain(`📞 ${EVIL}`)
    expect(t).toContain('⛰ 812 m')
    expect(t).toContain('🔋 3.9 V')
    expect(t).toContain('Repeater')
    expect(t).toContain('35TLG2')
  })

  it('a second render replaces the first [B52]', () => {
    const el = document.createElement('div')
    renderTrackerTooltip(el, { full_name: 'A', dev_sn: 1 }, null)
    renderTrackerTooltip(el, { full_name: 'B', dev_sn: 2 }, null)
    expect(el.textContent).not.toContain('A')
    expect(el.textContent).toContain('B')
  })
})
