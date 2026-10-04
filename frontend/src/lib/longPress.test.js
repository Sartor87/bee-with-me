import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createLongPress } from './longPress'

const touch = (over = {}) => ({ pointerType: 'touch', isPrimary: true, clientX: 100, clientY: 100, ...over })

describe('long press on touch', () => {
  let fired
  let lp
  beforeEach(() => {
    vi.useFakeTimers()
    fired = vi.fn()
    lp = createLongPress({ onLongPress: fired, ms: 600, slopPx: 10 })
  })
  afterEach(() => { vi.useRealTimers() })

  it('fires once after the hold and then swallows the release click [B52]', () => {
    lp.down(touch())
    vi.advanceTimersByTime(599)
    expect(fired).not.toHaveBeenCalled()
    vi.advanceTimersByTime(2)
    expect(fired).toHaveBeenCalledTimes(1)
    expect(fired).toHaveBeenCalledWith(100, 100)
    expect(lp.shouldSwallowClick()).toBe(true)
  })

  it('the next gesture clicks normally again [B52]', () => {
    lp.down(touch())
    vi.advanceTimersByTime(700)
    expect(lp.shouldSwallowClick()).toBe(true)
    lp.down(touch())
    expect(lp.shouldSwallowClick()).toBe(false)
  })

  it('a short tap never swallows its click [B52]', () => {
    lp.down(touch())
    vi.advanceTimersByTime(200)
    lp.end()
    vi.advanceTimersByTime(1000)
    expect(fired).not.toHaveBeenCalled()
    expect(lp.shouldSwallowClick()).toBe(false)
  })

  it('a second finger (pinch) cancels the press and nothing is swallowed [B52]', () => {
    lp.down(touch())
    vi.advanceTimersByTime(300)
    lp.down(touch({ isPrimary: false, clientX: 160 }))
    vi.advanceTimersByTime(1000)
    expect(fired).not.toHaveBeenCalled()
    expect(lp.shouldSwallowClick()).toBe(false)
  })

  it('moving past the slop cancels, moving within it does not [B52]', () => {
    lp.down(touch())
    lp.move({ clientX: 105, clientY: 104 })
    vi.advanceTimersByTime(650)
    expect(fired).toHaveBeenCalledTimes(1)
    lp.down(touch())
    lp.move({ clientX: 130, clientY: 100 })
    vi.advanceTimersByTime(650)
    expect(fired).toHaveBeenCalledTimes(1)
  })

  it('a mouse never starts a long press [B52]', () => {
    lp.down(touch({ pointerType: 'mouse' }))
    vi.advanceTimersByTime(1000)
    expect(fired).not.toHaveBeenCalled()
  })
})
