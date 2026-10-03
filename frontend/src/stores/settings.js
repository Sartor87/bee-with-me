import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getSettings, putHQInitial, putSettings } from '../api'

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

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref(null)

  const hq = computed(() => {
    const s = settings.value
    return s && s.hq_latitude != null ? { lat: s.hq_latitude, lon: s.hq_longitude } : null
  })

  async function fetchSettings() {
    settings.value = await getSettings()
  }

  async function saveSettings(patch) {
    const { updated_at, ...current } = settings.value ?? {}
    settings.value = await putSettings({ ...current, ...patch })
  }

  const setHQ   = (lat, lon) => saveSettings({ hq_latitude: lat, hq_longitude: lon })
  const clearHQ = () => saveSettings({ hq_latitude: null, hq_longitude: null })

  async function migrateLocalHQ(isAdmin) {
    const local = readLegacyHQ()
    if (!isAdmin || !local) return 'skipped'
    try {
      settings.value = await putHQInitial({ hq_latitude: local.lat, hq_longitude: local.lon })
      dropLegacyHQ()
      return 'uploaded'
    } catch (err) {
      if (err === 'hq_already_set') {
        dropLegacyHQ()
        return 'exists'
      }
      return 'failed'
    }
  }

  return { settings, hq, fetchSettings, saveSettings, setHQ, clearHQ, migrateLocalHQ }
})
