// Colour helpers for OpenLayers styles. OpenLayers paints on a canvas and needs rgba()
// strings, while the design tokens in style.css are hex.

const TRACKER_BLUE = '#3b82f6'
const warned = new Set()

/** `#rgb`, `#rrggbb` or `#rrggbbaa` (alpha ignored) to [r, g, b]; null for anything else. */
export function parseHex(hex) {
  if (typeof hex !== 'string') return null
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(hex.trim())
  if (!m) return null
  let h = m[1]
  if (h.length === 3) h = [...h].map(c => c + c).join('')
  return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16))
}

/** Hex to `rgba(r,g,b,a)`. A value that is not hex (a named colour, `rgb()`, an empty token)
 *  falls back to `fallback`, and says so once in the console instead of silently turning
 *  into tracker blue. The message carries the colour string only, never user data. */
export function hexToRgba(hex, alpha, fallback = TRACKER_BLUE) {
  let rgb = parseHex(hex)
  if (!rgb) {
    const key = String(hex)
    if (!warned.has(key)) {
      warned.add(key)
      console.warn('hexToRgba: expected a #rgb or #rrggbb colour, got %s; using %s', key, fallback)
    }
    rgb = parseHex(fallback) ?? parseHex(TRACKER_BLUE)
  }
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`
}
