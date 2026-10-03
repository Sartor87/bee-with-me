import { onMounted, onUnmounted, watch } from 'vue'
import Overlay from 'ol/Overlay'
import Map from 'ol/Map'
import View from 'ol/View'
import TileLayer from 'ol/layer/Tile'
import OSM from 'ol/source/OSM'
import XYZ from 'ol/source/XYZ'
import VectorLayer from 'ol/layer/Vector'
import VectorSource from 'ol/source/Vector'
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import LineString from 'ol/geom/LineString'
import { fromLonLat, toLonLat } from 'ol/proj'
import GeoJSON from 'ol/format/GeoJSON'
import { Circle, Fill, Stroke, Style, Text } from 'ol/style'
import RegularShape from 'ol/style/RegularShape'
import Graticule from 'ol/layer/Graticule'
import ScaleLine from 'ol/control/ScaleLine'
import { forward as toMGRS } from 'mgrs'
import { useSettings } from './useSettings'
import { freshnessOf, LIVE, LOST } from '../lib/freshness'
import { hotspotStyleKey } from '../lib/fireStyle'
import { hexToRgba } from '../lib/color'

const DEFAULT_COLOR = '#3b82f6'

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180
  const Δφ = (lat2 - lat1) * Math.PI / 180, Δλ = (lon2 - lon1) * Math.PI / 180
  const a = Math.sin(Δφ/2)**2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ/2)**2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function bearingDeg(lat1, lon1, lat2, lon2) {
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180
  const Δλ = (lon2 - lon1) * Math.PI / 180
  const y = Math.sin(Δλ) * Math.cos(φ2)
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ)
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360
}

export const BASEMAPS = [
  { id: 'osm',          label: 'Street' },
  { id: 'dark',         label: 'Dark' },
  { id: 'satellite',    label: 'Satellite' },
  { id: 'topo',         label: 'Topo' },
  { id: 'bgmountains',  label: 'BG Mountains', local: true },
]

const TILE_SERVER = 'http://localhost:8080'

const { bgMountainsOffline } = useSettings()

function makeBgMountainsSource() {
  if (bgMountainsOffline.value) {
    return new XYZ({ url: '/tiles/bgmountains/{z}/{x}/{y}.png', attributions: '© BGMountains', maxZoom: 18 })
  }
  return new XYZ({ url: 'https://bgmtile.kade.si/{z}/{x}/{y}.png', attributions: '© BGMountains', crossOrigin: 'anonymous', maxZoom: 18 })
}

function makeBasemapLayer(id) {
  const sources = {
    osm: new OSM(),
    dark: new XYZ({
      url: 'https://{a-d}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
      attributions: '© CartoDB',
    }),
    satellite: new XYZ({
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      attributions: '© Esri',
    }),
    topo: new XYZ({
      url: 'https://{a-c}.tile.opentopomap.org/{z}/{x}/{y}.png',
      attributions: '© OpenTopoMap',
    }),
    bgmountains: makeBgMountainsSource(),
  }
  return new TileLayer({ source: sources[id] ?? sources.osm })
}

function gridInterval(zoom) {
  if (zoom >= 14) return 0.01
  if (zoom >= 11) return 0.1
  if (zoom >= 8)  return 1
  return 5
}

function makeMGRSLabel(lon, lat) {
  try {
    const mgrs = toMGRS([lon, lat], 0)
    return mgrs.slice(0, 7)
  } catch {
    return ''
  }
}

// Fire colours live as tokens in style.css (--fire-*). OpenLayers paints on a canvas and
// cannot read CSS variables, so resolve them when a style is built. Fallbacks only matter
// without a DOM.
function fireToken(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return v || fallback
  } catch { return fallback }
}

// Hotspot look per style key. Age fades fill and ring so the newest detections read first;
// state (dismissed / suppressed / extinguished) goes hollow and grey; a field report is a
// diamond, never a circle, so it cannot pass for a satellite detection.
function makeHotspotStyle(key, fieldReportLabel) {
  const ember    = fireToken('--fire-hotspot', '#ad1e57')
  const inactive = fireToken('--fire-inactive', '#8892aa')
  const ring     = fireToken('--text', '#e2e8f0')
  const circle = (radius, fill, stroke) => new Style({
    image: new Circle({ radius, fill: fill ? new Fill({ color: fill }) : undefined, stroke }),
  })
  // The ember is dark on a dark map (about 2.8:1 on --bg), so every hotspot carries a thin
  // light ring: newest is solid, older ones fade, but the ring never drops below what keeps
  // the dot findable on dark and satellite basemaps. No glow: glow is an alarm signal.
  const E = (alpha) => hexToRgba(ember, alpha, '#ad1e57')
  const R = (alpha) => hexToRgba(ring, alpha, '#e2e8f0')
  switch (key) {
    case 'age_24h': return circle(6, E(1),   new Stroke({ color: R(1),    width: 1.75 }))
    case 'age_3d':  return circle(5, E(0.7), new Stroke({ color: R(0.8),  width: 1.5 }))
    case 'age_7d':  return circle(4, E(0.45), new Stroke({ color: R(0.6), width: 1.25 }))
    case 'field_report':
      return new Style({
        image: new RegularShape({
          points: 4, radius: 9, angle: 0,
          fill: new Fill({ color: ember }), stroke: new Stroke({ color: ring, width: 2 }),
        }),
        text: fieldReportLabel ? new Text({
          text: fieldReportLabel, offsetY: 20,
          font: 'bold 12px system-ui',
          fill: new Fill({ color: ring }), stroke: new Stroke({ color: '#000', width: 3 }),
        }) : undefined,
      })
    case 'suppressed':
      return circle(5, null, new Stroke({ color: hexToRgba(inactive, 0.9, '#8892aa'), width: 1.5, lineDash: [3, 3] }))
    default: // dismissed, extinguished
      return circle(4, null, new Stroke({ color: hexToRgba(inactive, 0.8, '#8892aa'), width: 1.5 }))
  }
}

// Burnt areas are history, not alarm: neutral ash, never red.
function makeBurntStyle() {
  const ash = fireToken('--fire-burnt', '#a8a29e')
  return new Style({
    fill:   new Fill({ color: hexToRgba(ash, 0.28, '#a8a29e') }),
    stroke: new Stroke({ color: hexToRgba(ash, 0.9, '#a8a29e'), width: 1.5 }),
  })
}

function makeMarkerStyle(color, isSOS, name, isTeam, freshness, noFix) {
  const radius   = isSOS ? 10 : isTeam ? 10 : 7
  const isStale  = freshness !== LIVE
  const isLost   = freshness === LOST
  // Lost contact fades further than merely stale, so "we haven't heard from them in half an
  // hour" is distinguishable from "they're a few minutes overdue" at a glance.
  const fillColor = isSOS ? '#ef4444'
    : isLost  ? 'rgba(156,163,175,0.22)'
    : isStale ? 'rgba(156,163,175,0.45)'
    : color
  const strokeCol = isStale ? 'rgba(255,255,255,0.35)' : '#fff'

  const style = new Style({
    image: new Circle({
      radius,
      fill:   new Fill({ color: fillColor }),
      // Dashed ring = in radio contact but no satellite fix: the position shown is the last
      // known one, not where they are now.
      stroke: new Stroke({
        color:    noFix ? '#facc15' : strokeCol,
        width:    isTeam ? 3 : 2,
        lineDash: noFix ? [3, 2] : undefined,
      }),
    }),
    text: new Text({
      text:    name || '',
      offsetY: -(radius + 10),
      fill:    new Fill({ color: '#fff' }),
      stroke:  new Stroke({ color: '#000', width: 3 }),
      font:    isTeam ? 'bold 14px system-ui' : 'bold 13px system-ui',
    }),
  })
  return style
}

function makeTrailStyle(color) {
  return new Style({
    stroke: new Stroke({
      color:    hexToRgba(color, 0.65),
      width:    2.5,
      lineDash: [6, 3],
    }),
  })
}

function formatCheckpointTime(recordedAt) {
  if (!recordedAt) return ''
  const d = new Date(recordedAt)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function makeCheckpointStyle(color, index, isLast, showLabels, recordedAt) {
  const label = showLabels
    ? (formatCheckpointTime(recordedAt) || String(index + 1))
    : ''
  return new Style({
    image: new Circle({
      radius: isLast ? 6 : 4,
      fill:   new Fill({ color: isLast ? color : hexToRgba(color, 0.6) }),
      stroke: new Stroke({ color: '#fff', width: isLast ? 2 : 1 }),
    }),
    text: showLabels ? new Text({
      text:         label,
      font:         `bold ${isLast ? 10 : 9}px system-ui`,
      fill:         new Fill({ color: '#fff' }),
      stroke:       new Stroke({ color: '#000', width: 2 }),
      offsetY:      isLast ? -14 : -12,
      textBaseline: 'bottom',
    }) : undefined,
  })
}

export function useMap(mapRef, positionList, trails, onCursorMGRS, onMeasure, groupsMap, onHQPlaced) {
  let map = null
  let basemapLayer = makeBasemapLayer('osm')
  let checkpointNumbersVisible = false
  let mgrsLabelMode = false
  let latLonGridVisible = false
  let measureMode   = false
  let measurePoints = []
  let staleTimer    = null
  let hqPlacementMode = false
  let hqLabel = 'ЩАБ'

  // Marker layer
  const source      = new VectorSource()
  const vectorLayer = new VectorLayer({ source, zIndex: 10 })

  // Trail line layer
  const trailSource = new VectorSource()
  const trailLayer  = new VectorLayer({ source: trailSource, zIndex: 5, visible: false })

  // Checkpoint dot layer
  const checkpointSource = new VectorSource()
  const checkpointLayer  = new VectorLayer({ source: checkpointSource, zIndex: 6, visible: false })

  // Fire layers: history (burnt areas) and detections (hotspots) sit under the trackers and
  // trails, hidden until the operator turns them on.
  const geojson       = new GeoJSON()
  const burntSource   = new VectorSource()
  const burntLayer    = new VectorLayer({ source: burntSource, zIndex: 3, visible: false })
  const hotspotSource = new VectorSource()
  const hotspotStyles = {}
  let   fieldReportLabel = ''
  const hotspotLayer  = new VectorLayer({
    source: hotspotSource, zIndex: 3.5, visible: false,
    style: (feature) => {
      const key = hotspotStyleKey(feature.getProperties(), Date.now())
      return (hotspotStyles[key] ??= makeHotspotStyle(key, fieldReportLabel))
    },
  })
  let burntStyleCache = null
  burntLayer.setStyle(() => (burntStyleCache ??= makeBurntStyle()))
  let fireTimer = null
  let fireClickCb = null

  // Measure layer
  const measureSource = new VectorSource()
  const measureLayer  = new VectorLayer({ source: measureSource, zIndex: 20 })

  // HQ layer
  const hqSource = new VectorSource()
  const hqLayer  = new VectorLayer({ source: hqSource, zIndex: 15 })

  // Weather overlay layer — created/destroyed on demand
  let activeWeatherLayer = null

  // MGRS latitude band letters C–X (8° bands starting at −80°)
  const MGRS_BANDS = 'CDEFGHJKLMNPQRSTUVWX'
  function mgrsLatBand(lat) {
    const idx = Math.floor((lat + 80) / 8)
    return MGRS_BANDS[Math.max(0, Math.min(idx, MGRS_BANDS.length - 1))] ?? ''
  }

  const graticule = new Graticule({
    strokeStyle: new Stroke({ color: 'rgba(0,0,0,0.85)', width: 2 }),
    showLabels: true,
    lonLabelStyle: new Text({
      font: 'bold 13px monospace',
      fill: new Fill({ color: '#000' }),
      stroke: new Stroke({ color: '#fff', width: 4 }),
      textBaseline: 'bottom',
      textAlign: 'center',
      offsetY: -4,
    }),
    latLabelStyle: new Text({
      font: 'bold 13px monospace',
      fill: new Fill({ color: '#000' }),
      stroke: new Stroke({ color: '#fff', width: 4 }),
      textAlign: 'right',
      textBaseline: 'bottom',
      offsetX: -6,
      offsetY: -4,
    }),
    lonLabelFormatter: (lon) => {
      if (mgrsLabelMode) {
        try {
          const center = map?.getView().getCenter()
          if (center) {
            const [, lat] = toLonLat(center)
            const zoom = map.getView().getZoom() ?? 7
            const acc = zoom >= 14 ? 2 : zoom >= 11 ? 1 : 0
            return toMGRS([lon, lat], acc)
          }
        } catch { /* ignore */ }
        return ''
      }
      const abs = parseFloat(Math.abs(lon).toFixed(5)).toString()
      return `${abs}°${lon >= 0 ? 'E' : 'W'}`
    },
    latLabelFormatter: (lat) => {
      if (mgrsLabelMode) {
        try {
          const center = map?.getView().getCenter()
          if (center) {
            const [lon] = toLonLat(center)
            const zoom = map.getView().getZoom() ?? 7
            const acc = zoom >= 14 ? 2 : zoom >= 11 ? 1 : 0
            return toMGRS([lon, lat], acc)
          }
        } catch { /* ignore */ }
        return mgrsLatBand(lat)
      }
      const abs = parseFloat(Math.abs(lat).toFixed(5)).toString()
      return `${abs}°${lat >= 0 ? 'N' : 'S'}`
    },
    visible: false,
    zIndex: 4,
  })

  let activeBasemapId = 'osm'

  function setWeatherLayer(url) {
    if (activeWeatherLayer) {
      map.removeLayer(activeWeatherLayer)
      activeWeatherLayer = null
    }
    if (!url || !map) return
    activeWeatherLayer = new TileLayer({
      source: new XYZ({ url, crossOrigin: 'anonymous' }),
      opacity: 1.0,
      zIndex: 2,
    })
    map.addLayer(activeWeatherLayer)
  }

  function setBasemap(id) {
    if (!map) return
    activeBasemapId = id
    map.getLayers().removeAt(0)
    basemapLayer = makeBasemapLayer(id)
    map.getLayers().insertAt(0, basemapLayer)
  }

  watch(bgMountainsOffline, () => {
    if (activeBasemapId === 'bgmountains') setBasemap('bgmountains')
  })

  function forceGraticuleRedraw() {
    graticule.renderedExtent_ = null
    map?.render()
  }

  function setMGRSGrid(visible) {
    mgrsLabelMode = visible
    graticule.setVisible(visible || latLonGridVisible)
    forceGraticuleRedraw()
  }

  function setLatLonGrid(visible) {
    latLonGridVisible = visible
    graticule.setVisible(visible || mgrsLabelMode)
    forceGraticuleRedraw()
  }

  function upsertFeature(pos) {
    const id          = pos.device_id
    const leaderGroup = pos.groups?.find(g => g.is_leader)
    const color       = leaderGroup?.color ?? pos.groups?.[0]?.color ?? DEFAULT_COLOR
    const isSOS       = pos.sos_active
    const freshness   = freshnessOf(pos)
    const label = pos.displayLabel || pos.full_name || pos.device_name || String(pos.dev_sn ?? '')

    let feature = source.getFeatureById(id)
    if (!feature) {
      feature = new Feature({ geometry: new Point([0, 0]) })
      feature.setId(id)
      source.addFeature(feature)
    }
    feature.getGeometry().setCoordinates(fromLonLat([pos.longitude, pos.latitude]))
    feature.setStyle(makeMarkerStyle(color, isSOS, label, !!pos.displayLabel, freshness, pos.gnss_valid === false))
    feature.setProperties({ pos }, true)
  }

  function removeStaleFeatures(currentIds) {
    const set = new Set(currentIds)
    source.getFeatures().forEach(f => {
      if (!set.has(f.getId())) source.removeFeature(f)
    })
  }

  function upsertTrail(deviceId, points) {
    const fid = `trail-${deviceId}`
    if (!points || points.length < 2) {
      const f = trailSource.getFeatureById(fid)
      if (f) trailSource.removeFeature(f)
      return
    }

    const coords = points.map(p => fromLonLat([p.lon, p.lat]))
    // Look up the device color from its marker
    const markerFeature = source.getFeatureById(deviceId)
    const color = markerFeature?.get('pos')?.groups?.[0]?.color ?? DEFAULT_COLOR

    let feature = trailSource.getFeatureById(fid)
    if (!feature) {
      feature = new Feature({ geometry: new LineString(coords) })
      feature.setId(fid)
      trailSource.addFeature(feature)
    } else {
      feature.getGeometry().setCoordinates(coords)
    }
    feature.setStyle(makeTrailStyle(color))
  }

  function removeStaleTrails(currentIds) {
    const set = new Set(currentIds.map(id => `trail-${id}`))
    trailSource.getFeatures().forEach(f => {
      if (!set.has(f.getId())) trailSource.removeFeature(f)
    })
  }

  function upsertCheckpoints(deviceId, points) {
    // Remove old checkpoints for this device
    checkpointSource.getFeatures()
      .filter(f => f.get('deviceId') === deviceId)
      .forEach(f => checkpointSource.removeFeature(f))

    if (!points || points.length === 0) return

    const markerFeature = source.getFeatureById(deviceId)
    const color = markerFeature?.get('pos')?.groups?.[0]?.color ?? DEFAULT_COLOR

    points.forEach((p, i) => {
      const isLast = i === points.length - 1
      const f = new Feature({ geometry: new Point(fromLonLat([p.lon, p.lat])) })
      // Label with the server clock, same as every other time the operator sees — a device
      // with a skewed GNSS clock would otherwise stamp checkpoints with times that never were.
      f.setStyle(makeCheckpointStyle(color, i, isLast, checkpointNumbersVisible, p.received_at ?? p.recorded_at))
      f.set('deviceId', deviceId)
      f.set('recordedAt', p.recorded_at)
      f.set('cpIndex', i)
      f.set('cpIsLast', isLast)
      f.set('cpColor', color)
      checkpointSource.addFeature(f)
    })
  }

  function removeStaleCheckpoints(currentIds) {
    const set = new Set(currentIds)
    const toRemove = checkpointSource.getFeatures()
      .filter(f => !set.has(f.get('deviceId')))
    toRemove.forEach(f => checkpointSource.removeFeature(f))
  }

  function setTrailVisible(visible) {
    trailLayer.setVisible(visible)
    checkpointLayer.setVisible(visible)
  }

  function setCheckpointNumbers(visible) {
    checkpointNumbersVisible = visible
    checkpointSource.getFeatures().forEach(f => {
      f.setStyle(makeCheckpointStyle(
        f.get('cpColor'), f.get('cpIndex'), f.get('cpIsLast'), visible, f.get('recordedAt'),
      ))
    })
  }

  onMounted(() => {
    // Hover tooltip element — created programmatically so it lives inside OL's viewport
    const tooltipEl = document.createElement('div')
    Object.assign(tooltipEl.style, {
      position:        'absolute',
      background:      'rgba(15,15,20,0.88)',
      color:           '#fff',
      padding:         '6px 10px',
      borderRadius:    '6px',
      fontSize:        '12px',
      pointerEvents:   'none',
      whiteSpace:      'nowrap',
      boxShadow:       '0 2px 8px rgba(0,0,0,0.5)',
      border:          '1px solid rgba(255,255,255,0.1)',
      lineHeight:      '1.5',
      display:         'none',
    })

    const tooltip = new Overlay({
      element:     tooltipEl,
      offset:      [14, 0],
      positioning: 'center-left',
      stopEvent:   false,
    })

    map = new Map({
      target:   mapRef.value,
      layers:   [basemapLayer, burntLayer, hotspotLayer, graticule, trailLayer, checkpointLayer, hqLayer, vectorLayer, measureLayer],
      view:     new View({ center: fromLonLat([25.0, 42.5]), zoom: 7 }),
      overlays: [tooltip],
      controls: [new ScaleLine({ units: 'metric', bar: false, minWidth: 100 })],
    })

    map.on('pointermove', (evt) => {
      if (evt.dragging) return
      const feature = map.forEachFeatureAtPixel(evt.pixel, f => f, {
        layerFilter: l => l === vectorLayer,
        hitTolerance: 5,
      })
      if (feature) {
        const stored = feature.get('pos')
        const pos    = positionList.value?.find(p => p.device_id === feature.getId()) ?? stored
        const isTeam = !!pos.displayLabel
        const sos    = pos.sos_active    ? '<span style="color:#ef4444;font-weight:700"> 🚨 SOS</span>' : ''
        const bat    = pos.battery_voltage != null ? `<br>🔋 ${pos.battery_voltage.toFixed(1)} V` : ''
        const time   = pos.recorded_at   ? `<br>🕐 ${new Date(pos.recorded_at).toLocaleTimeString()}` : ''

        if (isTeam) {
          const leaderGroup = pos.groups?.find(g => g.is_leader)
          const gDetail     = leaderGroup ? (groupsMap?.value ?? {})[leaderGroup.id] : null
          const members     = gDetail?.members ?? []
          const desc        = gDetail?.description
            ? `<br><span style="font-size:11px;color:#aaa;font-style:italic">${gDetail.description}</span>`
            : ''
          const memberList  = members.length
            ? '<br><span style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.05em">Members</span><br>' +
              members.map(m =>
                `<span style="color:${m.is_leader ? '#ffc900' : '#ccc'}">`+
                `${m.is_leader ? '★ ' : '· '}${m.full_name}${m.rank ? ` (${m.rank})` : ''}</span>`
              ).join('<br>')
            : ''
          tooltipEl.innerHTML =
            `<strong>${pos.displayLabel}</strong>${sos}` +
            desc +
            (pos.full_name ? `<br><span style="font-size:11px;color:#aaa">${pos.full_name}${pos.rank ? ` · ${pos.rank}` : ''}</span>` : '') +
            `<br><span style="font-family:monospace;font-size:11px">${pos.mgrs ?? ''}</span>` +
            bat + time + memberList
        } else {
          const name  = pos.full_name || pos.device_name || `SN:${pos.dev_sn}`
          const rep   = pos.repeater_mode ? '<span style="color:#a78bfa"> ↩ Repeater</span>' : ''
          const phone = pos.phone ? `<br>📞 ${pos.phone}` : ''
          const alt   = pos.altitude_m != null ? `<br>⛰ ${pos.altitude_m} m` : ''
          tooltipEl.innerHTML =
            `<strong>${name}</strong>${sos}${rep}` +
            `<br><span style="font-family:monospace;font-size:11px">${pos.mgrs ?? ''}</span>` +
            alt + bat + time + phone
        }
        tooltipEl.style.display = 'block'
        tooltip.setPosition(evt.coordinate)
        map.getTargetElement().style.cursor = 'pointer'
      } else {
        tooltipEl.style.display = 'none'
        tooltip.setPosition(undefined)
        map.getTargetElement().style.cursor = ''
      }

      // cursor coordinate readout
      if (onCursorMGRS) {
        try {
          const [lon, lat] = toLonLat(evt.coordinate)
          onCursorMGRS({ mgrs: toMGRS([lon, lat], 5), lat, lon })
        } catch {
          onCursorMGRS(null)
        }
      }
    })

    // Measure click handler
    const ptStyle = (label) => new Style({
      image: new Circle({
        radius: 6,
        fill:   new Fill({ color: '#1d4ed8' }),
        stroke: new Stroke({ color: '#fff', width: 2 }),
      }),
      text: new Text({
        text: label, offsetY: -16,
        fill:   new Fill({ color: '#1d4ed8' }),
        stroke: new Stroke({ color: '#000', width: 3 }),
        font: 'bold 12px system-ui',
      }),
    })

    map.on('click', (evt) => {
      if (hqPlacementMode) {
        const [lon, lat] = toLonLat(evt.coordinate)
        onHQPlaced?.({ lat, lon })
        return
      }
      if (!measureMode) return
      const [lon, lat] = toLonLat(evt.coordinate)

      if (measurePoints.length >= 2) {
        measurePoints = []
        measureSource.clear()
        onMeasure?.(null)
      }

      measurePoints.push({ lat, lon, coord: evt.coordinate })

      const f = new Feature({ geometry: new Point(evt.coordinate) })
      f.setStyle(ptStyle(measurePoints.length === 1 ? 'A' : 'B'))
      measureSource.addFeature(f)

      if (measurePoints.length === 2) {
        const [a, b] = measurePoints
        const line = new Feature({ geometry: new LineString([a.coord, b.coord]) })
        line.setStyle(new Style({ stroke: new Stroke({ color: '#1d4ed8', width: 2, lineDash: [6, 4] }) }))
        measureSource.addFeature(line)
        onMeasure?.({ distKm: haversineKm(a.lat, a.lon, b.lat, b.lon), bearing: bearingDeg(a.lat, a.lon, b.lat, b.lon) })
      }
    })

    // Fire features: a tracker wins when both are hit; clicking bare map closes the popup.
    const fireHit = (pixel) => {
      const hotspot = map.forEachFeatureAtPixel(pixel, f => f, { layerFilter: l => l === hotspotLayer, hitTolerance: 6 })
      if (hotspot) return { kind: 'hotspot', feature: hotspot }
      const burnt = map.forEachFeatureAtPixel(pixel, f => f, { layerFilter: l => l === burntLayer })
      return burnt ? { kind: 'burnt_area', feature: burnt } : null
    }
    const trackerHit = (pixel) => map.hasFeatureAtPixel(pixel, { layerFilter: l => l === vectorLayer, hitTolerance: 5 })

    map.on('click', (evt) => {
      if (hqPlacementMode || measureMode || !fireClickCb) return
      // A tracker click opens the tracker popup and closes any fire popup.
      if (trackerHit(evt.pixel)) { fireClickCb(null); return }
      const hit = fireHit(evt.pixel)
      if (!hit) { fireClickCb(null); return }
      const props = { ...hit.feature.getProperties() }
      delete props.geometry
      fireClickCb({
        kind: hit.kind,
        properties: props,
        coordinate: hit.kind === 'hotspot' ? hit.feature.getGeometry().getCoordinates() : evt.coordinate,
      })
    })

    map.on('pointermove', (evt) => {
      if (evt.dragging || hqPlacementMode || measureMode) return
      if (trackerHit(evt.pixel)) return
      if (fireHit(evt.pixel)) map.getTargetElement().style.cursor = 'pointer'
    })

    // Hotspots fade with age; repaint each minute so they do so without a refetch.
    fireTimer = setInterval(() => { if (hotspotLayer.getVisible()) hotspotLayer.changed() }, 60_000)

    // Re-evaluate stale state every minute without needing a new WS frame
    staleTimer = setInterval(() => {
      source.getFeatures().forEach(f => {
        const pos = f.get('pos')
        if (!pos) return
        const leaderGroup = pos.groups?.find(g => g.is_leader)
        const color   = leaderGroup?.color ?? pos.groups?.[0]?.color ?? DEFAULT_COLOR
        const label = pos.displayLabel || pos.full_name || pos.device_name || String(pos.dev_sn ?? '')
        f.setStyle(makeMarkerStyle(
          color, pos.sos_active, label, !!pos.displayLabel,
          freshnessOf(pos), pos.gnss_valid === false,
        ))
      })
    }, 60_000)

  })

  watch(positionList, (list) => {
    list.forEach(upsertFeature)
    removeStaleFeatures(list.map(p => p.device_id))
  }, { deep: true })

  watch(trails, (trailMap) => {
    Object.entries(trailMap).forEach(([deviceId, points]) => {
      upsertTrail(deviceId, points)
      upsertCheckpoints(deviceId, points)
    })
    removeStaleTrails(Object.keys(trailMap))
    removeStaleCheckpoints(Object.keys(trailMap))
  }, { deep: true })

  onUnmounted(() => {
    map?.setTarget(null)
    clearInterval(staleTimer)
    clearInterval(fireTimer)
  })

  function setMeasureMode(on) {
    measureMode = on
    if (!on) {
      measurePoints = []
      measureSource.clear()
      onMeasure?.(null)
    }
    if (map) map.getTargetElement().style.cursor = on ? 'crosshair' : ''
  }

  function makeHQStyle(label) {
    return new Style({
      image: new RegularShape({
        points:  4,
        radius:  15,
        radius2: 4,
        angle:   0,
        fill:    new Fill({ color: '#fbbf24' }),
        stroke:  new Stroke({ color: '#000', width: 2 }),
      }),
      text: new Text({
        text:    label || 'ЩАБ',
        offsetY: -22,
        font:    'bold 13px system-ui',
        fill:    new Fill({ color: '#fbbf24' }),
        stroke:  new Stroke({ color: '#000', width: 3 }),
      }),
    })
  }

  function setHQ(loc, label) {
    hqLabel = label || hqLabel
    hqSource.clear()
    if (!loc) return
    const f = new Feature({ geometry: new Point(fromLonLat([loc.lon, loc.lat])) })
    f.setId('hq')
    f.setStyle(makeHQStyle(hqLabel))
    hqSource.addFeature(f)
  }

  function setHQPlacementMode(on) {
    hqPlacementMode = on
    if (map) map.getTargetElement().style.cursor = on ? 'crosshair' : ''
  }

  // Read before clearing: a payload OpenLayers cannot parse keeps the previous features on
  // screen. Returns false so the caller can show the feed as failed (BP-03). The log line
  // carries no feature content.
  function replaceFeatures(source, fc, what) {
    let features
    try {
      features = geojson.readFeatures(fc, { featureProjection: 'EPSG:3857' })
    } catch {
      console.warn('fire layer: unreadable %s GeoJSON, keeping previous features', what)
      return false
    }
    source.clear(true)
    source.addFeatures(features)
    return true
  }

  function setBurntAreas(fc)  { return replaceFeatures(burntSource, fc, 'burnt-area') }
  function setHotspots(fc)    { return replaceFeatures(hotspotSource, fc, 'hotspot') }

  function setFireLayerVisible(name, visible) {
    if (name === 'burnt')    burntLayer.setVisible(visible)
    if (name === 'hotspots') hotspotLayer.setVisible(visible)
  }

  function setFireLabels({ fieldReport }) {
    fieldReportLabel = fieldReport || ''
    for (const k of Object.keys(hotspotStyles)) delete hotspotStyles[k]
    hotspotLayer.changed()
  }

  function onFireFeatureClick(cb) { fireClickCb = cb }

  function refreshMarkers(list) {
    list.forEach(upsertFeature)
    removeStaleFeatures(list.map(p => p.device_id))
  }

  return { map: () => map, setBasemap, setMGRSGrid, setLatLonGrid, setTrailVisible, setCheckpointNumbers, setMeasureMode, setWeatherLayer, refreshMarkers, setHQ, setHQPlacementMode, setBurntAreas, setHotspots, setFireLayerVisible, setFireLabels, onFireFeatureClick }
}
