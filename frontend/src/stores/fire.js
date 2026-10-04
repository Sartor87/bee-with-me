import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { acknowledgeAllFireAlerts, acknowledgeFireAlert, getFireAlerts, getFireBurntAreas, getFireHotspots } from '../api'
import { oldestFetchedAt, worstUpstreamState } from '../lib/fireStyle'

const LAYERS_KEY = 'bwm.fireLayers'
const EMPTY = () => ({ type: 'FeatureCollection', features: [] })

function loadLayers() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAYERS_KEY) || '{}')
    return { burnt: !!saved.burnt, hotspots: !!saved.hotspots, zones: !!saved.zones }
  } catch {
    return { burnt: false, hotspots: false, zones: false }
  }
}

function saveLayers(layers) {
  try { localStorage.setItem(LAYERS_KEY, JSON.stringify(layers)) } catch { /* private mode: per-session only */ }
}

export const useFireStore = defineStore('fire', () => {
  const hotspots      = ref(EMPTY())
  const burntAreas    = ref(EMPTY())
  // Hotspot feed: also what the WebSocket `fire_data_updated` message reports.
  const fetchedAt     = ref(null)
  const upstreamState = ref('unknown')
  // Burnt-area feed keeps its own as-of time so the pill never claims more than it knows.
  const burntFetchedAt     = ref(null)
  const burntUpstreamState = ref('unknown')
  const layers        = ref(loadLayers())

  const anyLayerOn = computed(() => layers.value.burnt || layers.value.hotspots || layers.value.zones)

  // What the freshness pill shows: only the feeds the operator is looking at, combined
  // pessimistically (oldest time, worst state).
  const shownFeeds = computed(() => {
    const feeds = []
    if (layers.value.hotspots) feeds.push({ at: fetchedAt.value, state: upstreamState.value })
    if (layers.value.burnt)    feeds.push({ at: burntFetchedAt.value, state: burntUpstreamState.value })
    return feeds
  })
  const shownFetchedAt = computed(() => oldestFetchedAt(shownFeeds.value.map(f => f.at)))
  const shownUpstreamState = computed(() => worstUpstreamState(shownFeeds.value.map(f => f.state)))

  // Each feed numbers its requests. The resync tick, `fire_data_updated` and a layer toggle
  // can overlap, and only the newest request may write data or a failure flag: otherwise
  // 08:30 data can replace 09:00 data, or a late timeout can mark current data as failed.
  const hotspotsFailed = ref(false)
  const burntFailed    = ref(false)
  let hotspotsSeq = 0
  let burntSeq = 0
  // The as-of time of the last payload the MAP accepted, per feed. A fetch sets the time
  // optimistically; if the map then rejects the payload (markFeedFailed) the time goes back
  // here, so the pill never claims an age for data that is not drawn (BP-01).
  const accepted = { hotspots: { at: null, state: 'unknown' }, burnt: { at: null, state: 'unknown' } }

  async function fetchHotspots() {
    const seq = ++hotspotsSeq
    try {
      const fc = await getFireHotspots()
      if (seq !== hotspotsSeq) return
      hotspots.value = fc
      accepted.hotspots = { at: fetchedAt.value, state: upstreamState.value }
      fetchedAt.value = fc.fetched_at ?? null
      upstreamState.value = fc.upstream_state ?? 'unknown'
      hotspotsFailed.value = false
    } catch (err) {
      if (seq === hotspotsSeq) hotspotsFailed.value = true
      throw err
    }
  }

  async function fetchBurntAreas() {
    const seq = ++burntSeq
    try {
      const fc = await getFireBurntAreas()
      if (seq !== burntSeq) return
      burntAreas.value = fc
      accepted.burnt = { at: burntFetchedAt.value, state: burntUpstreamState.value }
      burntFetchedAt.value = fc.fetched_at ?? null
      burntUpstreamState.value = fc.upstream_state ?? 'unknown'
      burntFailed.value = false
    } catch (err) {
      if (seq === burntSeq) burntFailed.value = true
      throw err
    }
  }

  // True while the last attempt to reach OUR backend failed for a feed the operator is
  // looking at (distinct from upstream_state, which is the backend's view of EFFIS). The
  // pill shows both as "unavailable".
  const fetchFailed = computed(() =>
    (layers.value.hotspots && hotspotsFailed.value) || (layers.value.burnt && burntFailed.value))

  // The map could not draw a feed it received (unreadable GeoJSON): show it as failed, the
  // same as a failed fetch. The next successful fetch clears it.
  function markFeedFailed(name) {
    if (name === 'hotspots') {
      hotspotsFailed.value = true
      fetchedAt.value = accepted.hotspots.at
      upstreamState.value = accepted.hotspots.state
    }
    if (name === 'burnt') {
      burntFailed.value = true
      burntFetchedAt.value = accepted.burnt.at
      burntUpstreamState.value = accepted.burnt.state
    }
  }

  // Resync tick: re-pull only the shown feeds that are in the failed state. Healthy feeds
  // are left alone (the backend polls EFFIS every 30 min), failed ones recover on their own.
  function retryFailed() {
    const jobs = []
    if (layers.value.hotspots && hotspotsFailed.value) jobs.push(fetchHotspots())
    if (layers.value.burnt && burntFailed.value)       jobs.push(fetchBurntAreas())
    return Promise.all(jobs).then(() => undefined)
  }

  function refreshVisible() {
    const jobs = []
    if (layers.value.hotspots) jobs.push(fetchHotspots())
    if (layers.value.burnt)    jobs.push(fetchBurntAreas())
    return Promise.all(jobs).then(() => undefined)
  }

  function setLayer(name, on) {
    layers.value = { ...layers.value, [name]: !!on }
    saveLayers(layers.value)
    // Turning a layer off cancels its in-flight request: a response (or failure) that lands
    // after the operator hid the layer must not touch the store.
    if (!on && name === 'hotspots') { hotspotsSeq++; hotspotsFailed.value = false }
    if (!on && name === 'burnt')    { burntSeq++;    burntFailed.value = false }
    return on ? refreshVisible() : Promise.resolve()
  }

  function applyFireDataUpdated(msg) {
    // The message only says "new data exists": the pill time comes from a fetch the map
    // accepted, never from this message (a failed refetch would otherwise show the new
    // time over old data, or over an empty map).
    void msg
    return refreshVisible()
  }

  // ---- Fire alarms (alerts near HQ or a rescuer) --------------------------------------------
  // Open alerts, acknowledged ones included: an acknowledged alert stays listed (quiet) until
  // it is resolved (BP-02). `ringToken` goes up exactly when the tone must play once.
  const alerts       = ref([])
  const ringToken    = ref(0)
  const alertsFailed = ref(false)   // the last load of the open alerts failed: the list may be incomplete (BP-01)
  const unacknowledged = computed(() => alerts.value.filter(a => !a.acknowledged_at))

  // "Show on map": MapView consumes it (centres, then clears it).
  const focusRequest = ref(null)
  let focusSeq = 0
  function requestFocus(latitude, longitude) {
    focusRequest.value = { latitude, longitude, n: ++focusSeq }
  }
  function clearFocusRequest() { focusRequest.value = null }

  // A load that was in flight while a WebSocket message or an acknowledge changed the list must
  // not undo that change when it lands (an older snapshot would drop a new alert or bring an
  // acknowledged one back). Changes made meanwhile are logged and replayed over the snapshot.
  let alertsSeq = 0
  let alertsInflight = 0
  let alertsLog = []

  const ring = () => { ringToken.value += 1 }
  const clean = ({ type, ...rest }) => { void type; return rest }   // drop the WebSocket envelope key

  function upsertAlert(next) {
    next = clean(next)
    if (alertsInflight) alertsLog.push({ id: next.id, remove: false })
    const idx = alerts.value.findIndex(a => a.id === next.id)
    if (idx === -1) {
      alerts.value = [...alerts.value, next]
      return true
    }
    // Acknowledged stays acknowledged until resolved: a late or reordered message never re-arms it.
    const keepAck = alerts.value[idx].acknowledged_at && !next.acknowledged_at
    alerts.value = alerts.value.map(a => (a.id === next.id
      ? { ...a, ...next, ...(keepAck ? { acknowledged_at: a.acknowledged_at } : {}) }
      : a))
    return false
  }

  function removeAlert(id) {
    if (alertsInflight) alertsLog.push({ id, remove: true })
    alerts.value = alerts.value.filter(a => a.id !== id)
  }

  async function fetchOpenAlerts() {
    const seq = ++alertsSeq
    const from = alertsLog.length
    alertsInflight += 1
    try {
      const fetched = await getFireAlerts({ state: 'open' })
      if (seq !== alertsSeq) return
      const local = new Map(alerts.value.map(a => [a.id, a]))
      let next = fetched.map(a => {
        const mine = local.get(a.id)
        return mine?.acknowledged_at && !a.acknowledged_at ? { ...a, acknowledged_at: mine.acknowledged_at } : a
      })
      for (const change of alertsLog.slice(from)) {
        next = next.filter(a => a.id !== change.id)
        const mine = alerts.value.find(a => a.id === change.id)
        if (!change.remove && mine) next.push(mine)
      }
      alerts.value = next
      alertsFailed.value = false
    } catch (err) {
      if (seq === alertsSeq) alertsFailed.value = true
      throw err
    } finally {
      alertsInflight -= 1
      if (!alertsInflight) alertsLog = []
    }
  }

  function applyFireAlert(msg) {
    if (msg.resolved_at) return
    const isNew = upsertAlert(msg)
    if (isNew && !msg.acknowledged_at) ring()
  }

  // One repeat message lists every alert that is due: one tone, however many ids it holds.
  async function applyFireAlertRepeat({ alert_ids: ids = [] }) {
    const known = new Map(alerts.value.map(a => [a.id, a]))
    if (ids.some(id => !known.has(id))) {
      await fetchOpenAlerts()
      if (ids.some(id => alerts.value.find(a => a.id === id && !a.acknowledged_at))) ring()
      return
    }
    if (ids.some(id => !known.get(id).acknowledged_at)) ring()
  }

  function applyFireAlertUpdated(msg) {
    if (msg.resolved_at) { removeAlert(msg.id); return }
    upsertAlert(msg)
  }

  async function acknowledge(id) {
    try {
      upsertAlert(await acknowledgeFireAlert(id))
    } catch (err) {
      // Resolved meanwhile (or deleted): nothing left to acknowledge, so it leaves the list.
      if (err?.status === 404) { removeAlert(id); return }
      throw err
    }
  }

  // Only what the operator could see is marked: an alert that arrived while the request was in
  // flight stays unacknowledged and keeps ringing.
  async function acknowledgeAll() {
    const ids = new Set(unacknowledged.value.map(a => a.id))
    await acknowledgeAllFireAlerts()
    const now = new Date().toISOString()
    alerts.value = alerts.value.map(a => (ids.has(a.id) && !a.acknowledged_at ? { ...a, acknowledged_at: now } : a))
  }

  return {
    hotspots, burntAreas, fetchedAt, upstreamState, burntFetchedAt, burntUpstreamState, layers, anyLayerOn,
    shownFetchedAt, shownUpstreamState, fetchFailed,
    fetchHotspots, fetchBurntAreas, refreshVisible, retryFailed, markFeedFailed, setLayer, applyFireDataUpdated,
    alerts, ringToken, alertsFailed, unacknowledged, focusRequest, requestFocus, clearFocusRequest,
    fetchOpenAlerts, applyFireAlert, applyFireAlertRepeat, applyFireAlertUpdated, acknowledge, acknowledgeAll,
  }
})
