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

  async function fetchHotspots() {
    const fc = await getFireHotspots()
    hotspots.value = fc
    fetchedAt.value = fc.fetched_at ?? null
    upstreamState.value = fc.upstream_state ?? 'unknown'
  }

  async function fetchBurntAreas() {
    const fc = await getFireBurntAreas()
    burntAreas.value = fc
    burntFetchedAt.value = fc.fetched_at ?? null
    burntUpstreamState.value = fc.upstream_state ?? 'unknown'
  }

  // True while the last attempt to reach OUR backend failed (distinct from upstream_state,
  // which is the backend's view of EFFIS). The pill shows both as "unavailable".
  const fetchFailed = ref(false)

  async function refreshVisible() {
    const jobs = []
    if (layers.value.hotspots) jobs.push(fetchHotspots())
    if (layers.value.burnt)    jobs.push(fetchBurntAreas())
    try {
      await Promise.all(jobs)
      fetchFailed.value = false
    } catch (err) {
      fetchFailed.value = true
      throw err
    }
  }

  function setLayer(name, on) {
    layers.value = { ...layers.value, [name]: !!on }
    saveLayers(layers.value)
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
    fetchHotspots, fetchBurntAreas, refreshVisible, setLayer, applyFireDataUpdated,
  }
})
