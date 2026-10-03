import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getSettings, putHQ, putHQInitial, putSettings } from '../api'
import { useAuthStore } from './auth'

const LEGACY_HQ_KEY = 'bwm.hq'   // where MapView kept HQ before it moved to the database

function readLegacyHQ() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LEGACY_HQ_KEY) || 'null')
    if (typeof parsed?.lat !== 'number' || typeof parsed?.lon !== 'number') return null
    return parsed
  } catch {
    return null
  }
}

function dropLegacyHQ() {
  try { localStorage.removeItem(LEGACY_HQ_KEY) } catch { /* nothing to drop */ }
}

// The API client rejects with the response `detail` (string or validation array) or the axios
// message, so the status code is gone. A legacy copy is worth keeping only when the failure
// says "try again later": no connection, or a server-side error. Anything else (409, 422, 403)
// is a verdict, and retrying the same value forever helps nobody.
function isTransientFailure(err) {
  return typeof err === 'string' && /network error|timeout|status code 5\d\d|settings_missing/i.test(err)
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref(null)

  const hq = computed(() => {
    const s = settings.value
    return s && s.hq_latitude != null ? { lat: s.hq_latitude, lon: s.hq_longitude } : null
  })

  async function fetchSettings() {
    settings.value = await getSettings()
  }

  // Every write carries the version the client last saw, so a stale page can never overwrite
  // an alarm switch or a radius another admin changed since (BP-02). On a stale write the store
  // reloads the server state and rejects with 'settings_stale' so the page can say so.
  async function saveSettings(patch) {
    if (!settings.value) throw 'settings_missing'
    const { updated_at, ...current } = settings.value
    try {
      settings.value = await putSettings({ ...current, ...patch, expected_updated_at: updated_at })
    } catch (err) {
      if (err === 'settings_stale') {
        try { await fetchSettings() } catch { /* the page shows the stale message either way */ }
      }
      throw err
    }
  }

  // HQ writes touch only the HQ columns (PUT /settings/hq) and run one at a time, in click
  // order, so the last placement wins and the store ends up equal to what the server holds.
  let hqQueue = Promise.resolve()
  function writeHQ(lat, lon) {
    const run = async () => {
      settings.value = await putHQ({ hq_latitude: lat, hq_longitude: lon })
    }
    const result = hqQueue.then(run, run)
    hqQueue = result.catch(() => { /* the caller sees the failure; the queue goes on */ })
    return result
  }
  const setHQ   = (lat, lon) => writeHQ(lat, lon)
  const clearHQ = () => writeHQ(null, null)

  // One-time upload of the pre-database HQ from this browser. The database is the source of
  // truth: only an admin may seed it, and the local copy is dropped whenever it can no longer
  // be useful (non-admin, invalid, already set). It stays only for a transient failure.
  async function migrateLocalHQ() {
    const local = readLegacyHQ()
    if (!local) return 'skipped'
    const auth = useAuthStore()
    if (!auth.user) {
      try { await auth.fetchMe() } catch { return 'failed' }
    }
    if (auth.user?.role !== 'admin') {
      dropLegacyHQ()
      return 'dropped'
    }
    try {
      settings.value = await putHQInitial({ hq_latitude: local.lat, hq_longitude: local.lon })
      dropLegacyHQ()
      return 'uploaded'
    } catch (err) {
      if (err === 'hq_already_set') {
        dropLegacyHQ()
        return 'exists'
      }
      if (isTransientFailure(err)) return 'failed'
      dropLegacyHQ()
      return 'dropped'
    }
  }

  return { settings, hq, fetchSettings, saveSettings, setHQ, clearHQ, migrateLocalHQ }
})
