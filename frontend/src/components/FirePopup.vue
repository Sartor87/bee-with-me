<template>
  <section class="fire-popup" role="dialog" :aria-label="title">
    <header class="fp-head">
      <h3 class="fp-title">{{ title }}</h3>
      <button type="button" class="fp-close" :aria-label="t('fire.close')" @click="emit('close')">&times;</button>
    </header>

    <dl v-if="kind === 'hotspot'" class="fp-rows">
      <div class="fp-row">
        <dt>{{ t('fire.popup.detected') }}</dt>
        <dd>
          <span class="fp-mono">{{ relative(properties.acquired_at) }}</span>
          <span class="fp-sub fp-mono">{{ utc(properties.acquired_at) }}</span>
        </dd>
      </div>
      <div class="fp-row">
        <dt>{{ t('fire.popup.source') }}</dt>
        <dd>{{ sourceLabel }}</dd>
      </div>
      <div v-if="properties.state && properties.state !== 'active'" class="fp-row">
        <dt>{{ t('fire.popup.state') }}</dt>
        <dd><span class="fp-chip">{{ t(`fire.state.${properties.state}`) }}</span></dd>
      </div>
      <div v-if="properties.notes" class="fp-row fp-notes">
        <dt>{{ t('fire.popup.notes') }}</dt>
        <dd>{{ properties.notes }}</dd>
      </div>
      <div v-if="properties.dismiss_notes" class="fp-row fp-notes">
        <dt>{{ t('fire.popup.dismissNotes') }}</dt>
        <dd>{{ properties.dismiss_notes }}</dd>
      </div>
    </dl>

    <dl v-else class="fp-rows">
      <div class="fp-row">
        <dt>{{ t('fire.popup.area') }}</dt>
        <dd><span class="fp-mono">{{ areaLabel }}</span></dd>
      </div>
      <div class="fp-row">
        <dt>{{ t('fire.popup.started') }}</dt>
        <dd><span class="fp-mono">{{ day(properties.started_at) }}</span></dd>
      </div>
      <div class="fp-row">
        <dt>{{ t('fire.popup.ended') }}</dt>
        <dd>
          <span class="fp-mono">{{ day(properties.ended_at) }}</span>
          <span class="fp-sub">{{ burnedAgo }}</span>
        </dd>
      </div>
      <div v-if="properties.effis_fire_id" class="fp-row">
        <dt>{{ t('fire.popup.fireId') }}</dt>
        <dd><span class="fp-mono">{{ properties.effis_fire_id }}</span></dd>
      </div>
    </dl>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'

// Every property is rendered as text through Vue interpolation; field-report notes and
// dismiss notes are user text and must never reach innerHTML. `isAdmin` is accepted now so
// the later action buttons (dismiss, create zone, extinguish) need no prop change.
const props = defineProps({
  kind:       { type: String, required: true, validator: v => v === 'hotspot' || v === 'burnt_area' },
  properties: { type: Object, required: true },
  isAdmin:    { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const { t } = useI18n()

const MIN = 60_000, HOUR = 3_600_000, DAY = 86_400_000

const title = computed(() => t(props.kind === 'hotspot' ? 'fire.popup.hotspotTitle' : 'fire.popup.burntTitle'))

const sourceLabel = computed(() => {
  const s = props.properties.source
  return ['viirs', 'modis', 'field_report'].includes(s) ? t(`fire.source.${s}`) : (s ?? t('fire.unknown'))
})

const areaLabel = computed(() => {
  const a = Number(props.properties.area_ha)
  return Number.isFinite(a) ? `${a.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${t('fire.popup.ha')}` : t('fire.unknown')
})

function ageMsOf(iso) {
  const ms = Date.now() - Date.parse(iso)
  return Number.isFinite(ms) ? Math.max(0, ms) : null
}

function relative(iso) {
  const ms = ageMsOf(iso)
  if (ms == null) return t('fire.unknown')
  if (ms < MIN)  return t('fire.ago.now')
  if (ms < HOUR) return t('fire.ago.minutes', { n: Math.floor(ms / MIN) })
  if (ms < DAY)  return t('fire.ago.hours', { n: Math.floor(ms / HOUR) })
  return t('fire.ago.days', { n: Math.floor(ms / DAY) })
}

function utc(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}

function day(iso) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? t('fire.unknown') : d.toISOString().slice(0, 10)
}

const burnedAgo = computed(() => {
  const ms = ageMsOf(props.properties.ended_at ?? props.properties.started_at)
  if (ms == null) return ''
  return t('fire.popup.burnedDaysAgo', { n: Math.floor(ms / DAY) })
})

function onKey(e) { if (e.key === 'Escape') emit('close') }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.fire-popup {
  width: 260px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, .55);
  color: var(--text);
  font-size: 13px;
}
.fp-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; border-bottom: 1px solid var(--border);
}
.fp-title { font-size: 13px; font-weight: 700; }
.fp-close {
  background: transparent; color: var(--text-muted);
  padding: 0 6px; font-size: 20px; line-height: 1; border-radius: 4px;
}
.fp-close:hover { color: var(--text); opacity: 1; }
.fp-close:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

.fp-rows { padding: 6px 12px 10px; }
.fp-row { display: grid; grid-template-columns: 78px 1fr; gap: 8px; padding: 5px 0; }
.fp-row dt { color: var(--text-muted); font-size: 12px; }
.fp-row dd { min-width: 0; overflow-wrap: anywhere; }
.fp-mono { font-family: monospace; font-variant-numeric: tabular-nums; font-weight: 600; }
.fp-sub { display: block; color: var(--text-muted); font-size: 11px; font-weight: 400; }
.fp-chip {
  display: inline-block; padding: 1px 8px; border-radius: 99px;
  border: 1px solid var(--border); background: var(--bg-card);
  color: var(--text-muted); font-size: 11px; font-weight: 600;
}
.fp-notes dd { white-space: pre-wrap; max-height: 96px; overflow-y: auto; }
</style>
