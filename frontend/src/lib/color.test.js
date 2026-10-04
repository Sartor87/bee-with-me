import { describe, it, expect, vi } from 'vitest'
import { hexToRgba, parseHex } from './color'

describe('hexToRgba', () => {
  it('converts the fire-hotspot token #ad1e57 [B34]', () => {
    expect(hexToRgba('#ad1e57', 1)).toBe('rgba(173,30,87,1)')
    expect(hexToRgba('#ad1e57', 0.6)).toBe('rgba(173,30,87,0.6)')
  })

  it('handles 3-digit and 8-digit hex and is case-insensitive [B34]', () => {
    expect(parseHex('#fa0')).toEqual([255, 170, 0])
    expect(parseHex('#AD1E57cc')).toEqual([173, 30, 87])
  })

  it('does not silently turn other formats into tracker blue: warns and uses the given fallback [B34]', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(hexToRgba('rgb(173, 30, 87)', 1, '#ad1e57')).toBe('rgba(173,30,87,1)')
    expect(warn).toHaveBeenCalledTimes(1)
    hexToRgba('rgb(173, 30, 87)', 0.5, '#ad1e57')   // same bad value: warned once only
    expect(warn).toHaveBeenCalledTimes(1)
    expect(hexToRgba('', 1)).toBe('rgba(59,130,246,1)')
    warn.mockRestore()
  })
})
