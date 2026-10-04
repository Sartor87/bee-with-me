<template>
  <div class="page">
    <div class="page-header">
      <h2>{{ t('nav.about') }}</h2>
    </div>

    <div class="card about-card">
      <div class="about-logo">
        <img src="../assets/asp-logo-1.png" class="about-logo-img" alt="ASP logo" />
        <h1 class="app-name">Bee With Me</h1>
        <span class="version">v1.0.0</span>
      </div>

      <div class="about-section">
        <h3>{{ t('about.description') }}</h3>
        <p>Offline people-tracking application for LoRaWAN-based rescue operations.</p>
      </div>

      <div class="about-section">
        <h3>{{ t('about.contact') }}</h3>
        <ul class="contact-list">
          <li v-for="c in contacts" :key="c.email" class="contact">
            <span class="contact-name">{{ c.name }}</span>
            <dl class="contact-rows">
              <dt>{{ t('about.phone') }}</dt>
              <dd>
                <a class="contact-link" :href="'tel:' + c.e164" :aria-label="t('about.callPerson', { name: c.name, phone: c.phone })">{{ c.phone }}</a>
              </dd>
              <dt>{{ t('about.email') }}</dt>
              <dd>
                <a class="contact-link" :href="'mailto:' + c.email" :aria-label="t('about.emailPerson', { name: c.name, email: c.email })">{{ c.email }}</a>
              </dd>
            </dl>
          </li>
        </ul>
      </div>

      <div class="about-section">
        <h3>{{ t('about.team') }}</h3>
        <p>ASP RESCUER TEAM<br><a href="https://rescuer.team" target="_blank" rel="noopener">https://rescuer.team</a></p>
      </div>

      <div class="about-section">
        <h3>{{ t('about.license') }}</h3>
        <p class="placeholder-text">—</p>
      </div>

      <div class="about-section" v-if="authStore.user?.role === 'admin'">
        <h3>{{ t('about.offlineMaps') }}</h3>
        <p class="maps-desc">{{ t('about.offlineMapsDesc') }}</p>

        <div class="mode-row">
          <span class="mode-label">{{ t('about.offlineMapsMode') }}</span>
          <div class="mode-pills">
            <button
              type="button"
              :class="['mode-pill', !bgMountainsOffline && 'mode-pill-active']"
              @click="bgMountainsOffline = false"
            >{{ t('about.modeOnline') }}</button>
            <button
              type="button"
              :class="['mode-pill', bgMountainsOffline && 'mode-pill-active']"
              @click="bgMountainsOffline = true"
            >{{ t('about.modeOffline') }}</button>
          </div>
        </div>

        <div v-if="tileStatus" class="tile-status">
          <div class="tile-progress-bar">
            <div class="tile-progress-fill" :style="{ transform: 'scaleX(' + progressPct / 100 + ')' }"></div>
          </div>
          <div class="tile-progress-label">
            <span v-if="tileStatus.running">
              {{ tileStatus.done.toLocaleString() }} / {{ tileStatus.total.toLocaleString() }} &nbsp;·&nbsp; {{ progressPct }}%
            </span>
            <span v-else-if="tileStatus.total > 0" class="tile-done">
              ✓ {{ t('about.offlineMapsComplete') }} &nbsp;·&nbsp; {{ tileStatus.skipped.toLocaleString() }} {{ t('about.offlineMapsSkipped') }}
              <span v-if="tileStatus.errors > 0" class="tile-errors">&nbsp;·&nbsp; {{ tileStatus.errors }} {{ t('about.offlineMapsErrors') }}</span>
            </span>
          </div>
        </div>

        <button class="download-btn" :disabled="tileStatus?.running" @click="openPasswordPrompt">
          {{ tileStatus?.running ? t('about.offlineMapsDownloading') : t('about.offlineMapsDownload') }}
        </button>
      </div>
    </div>

    <div v-if="passwordPromptOpen" class="modal-backdrop" @click.self="closePasswordPrompt">
      <div class="modal-card">
        <h3 class="modal-title">{{ t('about.offlineMapsPasswordTitle') }}</h3>
        <p class="modal-desc">{{ t('about.offlineMapsPasswordDesc') }}</p>
        <input
          ref="passwordInputEl"
          v-model="passwordInput"
          type="password"
          class="modal-input"
          :placeholder="t('about.offlineMapsPasswordPlaceholder')"
          @keyup.enter="submitPassword"
        />
        <div v-if="passwordError" class="modal-error">{{ passwordError }}</div>
        <div class="modal-actions">
          <button type="button" class="secondary" @click="closePasswordPrompt">
            {{ t('common.cancel') }}
          </button>
          <button type="button" @click="submitPassword" :disabled="!passwordInput">
            {{ t('about.offlineMapsDownload') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { useSettings } from '../composables/useSettings'
import { startTileDownload, getTileStatus } from '../api'
import { errorText } from '../api/client'

const { t } = useI18n()
const authStore = useAuthStore()
const { bgMountainsOffline } = useSettings()

onMounted(async () => {
  if (!authStore.user) await authStore.fetchMe()
})

// Contacts as data: the visible number is formatted +359 XXX XXX XXX, tel: gets it without spaces.
const CONTACTS = [
  { name: 'Konstantin Velev', phone: '+359 877 389 417', email: 'konsvelev@gmail.com' },
  { name: 'Kiril Penev',      phone: '+359 883 545 571', email: 'k.penev@outlook.com' },
  { name: 'Kiril Iliev',      phone: '+359 889 396 793', email: 'office@hemussoftware.com' },
]
const contacts = CONTACTS.map(c => ({ ...c, e164: c.phone.replace(/\s/g, '') }))

const tileStatus = ref(null)
let pollTimer = null

const passwordPromptOpen = ref(false)
const passwordInput      = ref('')
const passwordError      = ref('')
const passwordInputEl    = ref(null)

const progressPct = computed(() => {
  if (!tileStatus.value?.total) return 0
  return Math.round(tileStatus.value.done / tileStatus.value.total * 100)
})

function openPasswordPrompt() {
  passwordInput.value = ''
  passwordError.value = ''
  passwordPromptOpen.value = true
  nextTick(() => passwordInputEl.value?.focus())
}

function closePasswordPrompt() {
  passwordPromptOpen.value = false
}

async function submitPassword() {
  if (!passwordInput.value) return
  passwordError.value = ''
  try {
    await startTileDownload(passwordInput.value)
    passwordPromptOpen.value = false
    pollStatus()
  } catch (e) {
    if (e?.status === 403) {
      passwordError.value = t('about.offlineMapsPasswordWrong')
    } else {
      passwordError.value = errorText(e)
    }
  }
}

async function pollStatus() {
  tileStatus.value = await getTileStatus()
  if (tileStatus.value.running) {
    pollTimer = setTimeout(pollStatus, 1500)
  }
}

onUnmounted(() => clearTimeout(pollTimer))
</script>

<style scoped>
.page { padding: 24px; flex: 1; }

.about-card {
  max-width: 640px;
  padding: 36px;
}

.about-logo {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.about-logo-img { width: 52px; height: 52px; object-fit: contain; }
.app-name   { font-size: 22px; font-weight: 700; margin: 0; }
.version    { font-size: 12px; color: var(--text-muted); margin-left: auto; }

.about-section { margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }
.about-section:last-child { margin-bottom: 0; padding-bottom: 0; border-bottom: none; }
.about-section h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 10px;
}
.maps-desc { margin-bottom: 12px; }
.download-btn { margin-top: 12px; }
.download-btn:focus-visible, .mode-pill:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.contact-list { list-style: none; display: grid; gap: 16px; }
.contact { display: grid; gap: 4px; }
.contact-name { font-size: 14px; font-weight: 600; color: var(--text); }
.contact-rows {
  display: grid;
  grid-template-columns: 5.5rem 1fr;
  gap: 2px 12px;
  align-items: baseline;
}
.contact-rows dt { font-size: 13px; color: var(--text-muted); }
.contact-rows dd { min-width: 0; }
.contact-link {
  font-family: monospace;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
  text-decoration: underline;
  text-decoration-color: transparent;
  text-underline-offset: 3px;
  overflow-wrap: anywhere;
  transition: text-decoration-color .15s;
}
@media (hover: hover) and (pointer: fine) { .contact-link:hover { text-decoration-color: currentColor; } }
.contact-link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
@media (prefers-reduced-motion: reduce) { .contact-link { transition: none; } }
.about-section p {
  font-size: 14px;
  color: var(--text);
  line-height: 1.6;
  white-space: pre-line;
}
.placeholder-text { color: var(--text-muted); }

.tile-status { margin-top: 8px; }
.tile-progress-bar { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.tile-progress-fill { height: 100%; width: 100%; background: var(--accent); border-radius: 3px; transform-origin: left; transition: transform .4s; }
@media (prefers-reduced-motion: reduce) { .tile-progress-fill { transition: none; } }
.tile-progress-label { font-size: 13px; color: var(--text-muted); margin-top: 6px; }
.tile-done  { color: var(--success); }
.tile-errors { color: var(--danger); }

.mode-row   { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.mode-label { font-size: 14px; color: var(--text); white-space: nowrap; }
.mode-pills { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; flex-shrink: 0; }
.mode-pill  { padding: 5px 16px; font-size: 13px; background: transparent; border: none; color: var(--text-muted); cursor: pointer; }
.mode-pill-active { background: var(--primary, #3b82f6); color: #fff; font-weight: 600; }

.modal-backdrop {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(0,0,0,0.55); backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center;
}
.modal-card {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 24px; width: min(420px, 90vw);
  box-shadow: 0 12px 40px rgba(0,0,0,0.5);
}
.modal-title { margin: 0 0 8px; font-size: 16px; font-weight: 600; }
.modal-desc  { margin: 0 0 14px; font-size: 13px; color: var(--text-muted); }
.modal-input {
  width: 100%; padding: 8px 10px; font-size: 14px;
  background: var(--bg-card); color: var(--text);
  border: 1px solid var(--border); border-radius: 4px;
  box-sizing: border-box;
}
.modal-input:focus { outline: none; border-color: var(--accent); }
.modal-error {
  margin-top: 8px; font-size: 12px; color: var(--danger);
}
.modal-actions {
  margin-top: 16px; display: flex; justify-content: flex-end; gap: 8px;
}
</style>
