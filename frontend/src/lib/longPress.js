// Long press on touch (the map's "Report fire here"). Plain functions over pointer events so the
// rules can be tested without a map:
// - only a primary touch/pen pointer starts a press; a mouse uses the context menu instead;
// - a second pointer going down (a pinch) cancels the press, and moving past the slop cancels it;
// - after a press fired, the click that the release produces must not reach the map (it would open a
//   popup or select a tracker under the menu): shouldSwallowClick() is true until the next pointerdown.
export function createLongPress({ onLongPress, ms = 600, slopPx = 10 }) {
  let press = null
  let swallow = false

  function end() {
    if (press) { clearTimeout(press.timer); press = null }
  }

  function down(e) {
    swallow = false
    if (e.pointerType === 'mouse') return
    if (!e.isPrimary) { end(); return }   // pinch: another finger joined
    end()
    press = {
      x: e.clientX, y: e.clientY,
      timer: setTimeout(() => {
        const p = press
        press = null
        if (!p) return
        swallow = true
        onLongPress(p.x, p.y)
      }, ms),
    }
  }

  function move(e) {
    if (press && Math.hypot(e.clientX - press.x, e.clientY - press.y) > slopPx) end()
  }

  return { down, move, end, shouldSwallowClick: () => swallow }
}
