import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getFireBurntAreas, getFireHotspots } from '../api'
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

  async function fetchHotspots() {
    const seq = ++hotspotsSeq
    try {
      const fc = await getFireHotspots()
      if (seq !== hotspotsSeq) return
      hotspots.value = fc
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
    if (name === 'hotspots') hotspotsFailed.value = true
    if (name === 'burnt')    burntFailed.value = true
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
    // fetched_at is null when the backend has never fetched successfully: keep what we
    // know (possibly nothing) rather than inventing a time.
    fetchedAt.value = msg.fetched_at ?? fetchedAt.value
    upstreamState.value = msg.upstream_state ?? upstreamState.value
    return refreshVisible()
  }

  return {
    hotspots, burntAreas, fetchedAt, upstreamState, burntFetchedAt, burntUpstreamState, layers, anyLayerOn,
    shownFetchedAt, shownUpstreamState, fetchFailed,
    fetchHotspots, fetchBurntAreas, refreshVisible, markFeedFailed, setLayer, applyFireDataUpdated,
  }
})
