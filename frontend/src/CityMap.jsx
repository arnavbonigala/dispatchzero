import { useMemo, useRef, useState } from 'react'
import { Map, NavigationControl, useControl } from 'react-map-gl/maplibre'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { HeatmapLayer } from '@deck.gl/aggregation-layers'
import { ArcLayer, ColumnLayer, GeoJsonLayer, PathLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import 'maplibre-gl/dist/maplibre-gl.css'
import CameraCard from './CameraCard'
import { C, clock, mmss, responseColor, rgb, secOfDay, slowdownColor } from './format'

const STYLE = 'https://tiles.openfreemap.org/styles/dark'
const RECENT_S = 3600
const GROW_S = 240
const POLICE_S = 1800 // SFPD calls stay on the map for 30 replay minutes, fading
const narrow = () => typeof window !== 'undefined' && window.innerWidth < 900
const VIEW = { longitude: -122.447, latitude: 37.757, zoom: narrow() ? 11 : 11.7, pitch: 0, bearing: 0 }
const TILT = { pitch: 55, bearing: -17 }
// Heat ramps, faint to hot: police reports warm, injury crashes magenta so the two read apart when both are on
const WARM = [[242, 211, 107, 0], [242, 211, 107, 120], [236, 160, 70, 170], [232, 120, 58, 200], [214, 74, 52, 220], [200, 50, 58, 235]]
const MAGENTA = [[240, 182, 228, 0], [240, 182, 228, 110], [226, 130, 210, 160], [214, 92, 192, 195], [176, 52, 170, 220], [143, 31, 158, 235]]
const HIT = [255, 255, 255, 1] // invisible but pickable, so a heat cell can be hovered
const DPR = typeof window !== 'undefined' ? Math.min(window.devicePixelRatio || 1, 1.5) : 1

function Overlay(props) {
  const overlay = useControl(() => new MapboxOverlay({ interleaved: false, useDevicePixels: DPR, ...props }))
  overlay.setProps(props)
  return null
}

function along(route, frac) {
  if (route.length < 2) return route[0]
  const seg = route.slice(1).map((p, i) => Math.hypot(p[0] - route[i][0], (p[1] - route[i][1]) * 0.79))
  let left = Math.max(0, Math.min(1, frac)) * seg.reduce((a, b) => a + b, 0)
  for (let i = 0; i < seg.length; i++) {
    if (left <= seg[i]) {
      const k = seg[i] ? left / seg[i] : 0
      return [route[i][0] + (route[i + 1][0] - route[i][0]) * k, route[i][1] + (route[i + 1][1] - route[i][1]) * k]
    }
    left -= seg[i]
  }
  return route[route.length - 1]
}

const ll = ([lat, lon]) => [lon, lat]

function addBuildings(e) {
  const map = e.target
  if (map.getLayer('gm-buildings') || !map.getSource('openmaptiles')) return
  const label = map.getStyle().layers.find((l) => l.type === 'symbol')?.id
  const height = ['coalesce', ['get', 'render_height'], 8]
  map.addLayer({
    id: 'gm-buildings',
    type: 'fill-extrusion',
    source: 'openmaptiles',
    'source-layer': 'building',
    minzoom: 12,
    layout: { visibility: 'none' },
    paint: {
      'fill-extrusion-color': ['interpolate', ['linear'], height, 0, '#1c1f24', 80, '#2a2e35', 250, '#3a3f47'],
      'fill-extrusion-height': height,
      'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
      'fill-extrusion-opacity': 0.88,
    },
  }, label)
}

function enRoute(c, now, posts) {
  const engine = c.engine_unit && c.engine_s <= c.ambulance_s
  const eta = engine ? c.engine_eta_s : c.eta_s
  const arrive = c.start + c.response_s
  const from = posts[engine ? c.engine_post : c.from_post]
  if (!from || now < arrive - eta) return null
  const k = Math.min(1, (now - (arrive - eta)) / eta)
  const src = [from.lon, from.lat]
  const at = [src[0] + (c.lon - src[0]) * k, src[1] + (c.lat - src[1]) * k]
  return { c, engine, unit: engine ? c.engine_unit : c.unit, color: engine ? C.engine : C.unit, src, at, dst: [c.lon, c.lat] }
}

function dispatchLegs(result, elapsed) {
  if (!result) return []
  const legs = []
  for (const [key, s] of [['base', result.baseline], ['gm', result.agent]]) {
    if (!s || s.error) continue
    const gm = key === 'gm'
    for (const [u, color] of [[s.engine, C.engine], [s.chosen && { ...s.chosen, route: s.route }, C.unit]]) {
      if (!u?.route?.length) continue
      const engine = color === C.engine
      legs.push({ id: `${key}-${u.unit_id}`, gm, color, offset: [0, (gm ? -18 : 18) * (engine ? 2.2 : 1)], path: u.route.map(ll), at: ll(along(u.route, elapsed / u.eta_s)),
        done: elapsed >= u.eta_s, label: `${gm ? 'DispatchZero' : 'Baseline'} · ${u.unit_id}` })
    }
  }
  return legs
}

function tooltip({ layer, object }) {
  if (!object || !layer) return null
  const style = { background: '#16191e', color: '#ecebe7', fontSize: '12px', borderRadius: '10px',
    padding: '7px 10px', border: '1px solid #2c3037', fontFamily: '"IBM Plex Sans", system-ui, sans-serif' }
  const id = layer.id
  let text = null
  if (id === 'nbhd') text = object.properties.nhood
  else if (id === 'posts') text = `Ambulance post ${object.post} · station area ${object.station_area}`
  else if (id === 'done') {
    const engine = object.engine_unit && object.engine_s <= object.ambulance_s
    text = `${object.nbhd}\nFirst paramedic ${mmss(object.response_s)} · ${engine ? `engine ${object.engine_unit}` : `ambulance ${object.unit}`}\nHistory: ${mmss(object.historical_s)}`
  } else if (id === 'units') text = `${object.engine ? 'Engine' : 'Ambulance'} ${object.unit} → ${object.c.nbhd}`
  else if (id === 'cameras') text = `${object.name}\nClick for the live camera`
  else if (id === 'd-units') text = object.label
  else if (id === 'crashes') text = `${object.n} injury crashes since 2021\n${object.severe} severe or fatal`
  else if (id === 'reports') text = `${object.n} police reports in 90 days · mostly ${object.top}\n${object.violent} violent (assault, robbery, weapons…)`
  else if (id === 'police' || id === 'd-warn') {
    const src = object.source === 'chp' ? 'CHP' : 'SFPD'
    const when = object.live ? 'live now' : object.source === 'chp' ? `live · ${object.t.slice(11, 16)}` : clock(secOfDay(object.t))
    text = `${object.safety ? 'Weapon or violence · ' : ''}${src} · ${object.title}${object.priority ? ` (priority ${object.priority})` : ''}\n${object.place} · ${when}`
  } else if (id === 'chp') text = `CHP live · ${object.title}\n${object.place} · ${object.t.slice(11, 16)}`
  return text && { text, style }
}

export default function CityMap({ calls, now, race, posts, neighborhoods, slowNbhds, cameras, armed, onPick, result, elapsed, camera, onCamera, jam, incidents, show }) {
  const mapRef = useRef(null)
  const [flat, setFlat] = useState(true)
  const wave = 0.5 + 0.5 * Math.sin(elapsed / 8)

  const done = calls.filter((c) => now >= c.start + c.response_s)
  const active = calls.filter((c) => now >= c.start && now < c.start + c.response_s)
  const moving = active.map((c) => enRoute(c, now, posts)).filter(Boolean)
  const legs = useMemo(() => dispatchLegs(result, elapsed), [result, elapsed])
  const gm = result?.agent && !result.agent.error ? result.agent : null
  const hospital = result?.hospital
  const toHospital = gm && hospital ? Math.max(0, elapsed - gm.chosen.eta_s) : 0
  const incident = result && [result.incident.lon, result.incident.lat]
  const police = useMemo(() => (show.police
    ? incidents.police.map((p) => ({ ...p, s: secOfDay(p.t) })).filter((p) => p.s <= now && p.s > now - POLICE_S)
    : []), [incidents.police, show.police, now])
  const warnings = result?.warnings ?? []

  const layers = [
    new GeoJsonLayer({
      id: 'nbhd', data: neighborhoods, pickable: true, filled: true, stroked: true, lineWidthUnits: 'pixels',
      getFillColor: (f) => (slowNbhds.has(f.properties.nhood) ? rgb(C.slow, 38) : [0, 0, 0, 0]),
      getLineColor: (f) => (slowNbhds.has(f.properties.nhood) ? rgb(C.slow, 230) : [148, 163, 184, 46]),
      getLineWidth: (f) => (slowNbhds.has(f.properties.nhood) ? 1.6 : 0.8),
      updateTriggers: { getFillColor: [slowNbhds], getLineColor: [slowNbhds], getLineWidth: [slowNbhds] },
    }),
    new HeatmapLayer({
      id: 'reports-heat', data: incidents.reports, visible: show.reports, getPosition: (c) => [c.lon, c.lat],
      // square root, so a few downtown cells with hundreds of reports don't wash out the rest of the city
      getWeight: (c) => Math.sqrt(c.n), radiusPixels: 38, intensity: 1, threshold: 0.03, colorRange: WARM, opacity: 0.7,
    }),
    new HeatmapLayer({
      id: 'crashes-heat', data: incidents.crashes, visible: show.crashes, getPosition: (h) => [h.lon, h.lat],
      getWeight: (h) => Math.sqrt(h.n), radiusPixels: 32, intensity: 1, threshold: 0.03, colorRange: MAGENTA, opacity: 0.75,
    }),
    new ScatterplotLayer({
      id: 'reports', data: show.reports ? incidents.reports : [], pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 50],
      radiusUnits: 'meters', getPosition: (c) => [c.lon, c.lat], getRadius: 75, getFillColor: HIT,
    }),
    new ScatterplotLayer({
      id: 'crashes', data: show.crashes ? incidents.crashes : [], pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 50],
      radiusUnits: 'meters', getPosition: (h) => [h.lon, h.lat], getRadius: 110, getFillColor: HIT,
    }),
    new ColumnLayer({
      id: 'posts', data: posts, pickable: true, extruded: true, diskResolution: 6, radius: 38,
      getPosition: (p) => [p.lon, p.lat], getElevation: 140, getFillColor: [226, 232, 240, 170],
    }),
    new ColumnLayer({
      id: 'done', data: done, pickable: true, extruded: true, diskResolution: 12, radius: 46,
      getPosition: (c) => [c.lon, c.lat],
      getElevation: (c) => c.response_s * 0.5 * Math.min(1, (now - c.start - c.response_s) / GROW_S),
      getFillColor: (c) => rgb(responseColor(c.response_s), now - c.start - c.response_s < RECENT_S ? 235 : 85),
      updateTriggers: { getElevation: [Math.floor(now / 15)], getFillColor: [Math.floor(now / 60)] },
      material: { ambient: 0.55, diffuse: 0.6, shininess: 40 },
    }),
    new ScatterplotLayer({
      id: 'calls-ring', data: active, stroked: true, filled: true, radiusUnits: 'meters', lineWidthUnits: 'pixels',
      getPosition: (c) => [c.lon, c.lat], getRadius: race ? 90 : 160,
      getFillColor: rgb(C.bad, 40), getLineColor: rgb(C.bad, 220), getLineWidth: 1.5,
    }),
    new ColumnLayer({
      id: 'calls-beacon', data: race ? [] : active, extruded: true, radius: 14, diskResolution: 8,
      getPosition: (c) => [c.lon, c.lat], getElevation: 520, getFillColor: rgb(C.bad, 170),
    }),
    new ArcLayer({
      id: 'units-arc', data: race ? [] : moving, getSourcePosition: (m) => m.src, getTargetPosition: (m) => m.dst,
      getSourceColor: (m) => rgb(m.color, 30), getTargetColor: (m) => rgb(m.color, 200), getWidth: 2, getHeight: 0.35, greatCircle: false,
    }),
    new PathLayer({
      id: 'units-trail', data: moving, getPath: (m) => [m.src, m.at], getColor: (m) => rgb(m.color, 220),
      getWidth: 3, widthUnits: 'pixels', capRounded: true,
    }),
    new ScatterplotLayer({
      id: 'units', data: moving, pickable: true, radiusUnits: 'pixels', stroked: true, lineWidthUnits: 'pixels',
      getPosition: (m) => m.at, getRadius: 5.5, getFillColor: (m) => rgb(m.color), getLineColor: [255, 255, 255, 230], getLineWidth: 1.5,
    }),
    new ScatterplotLayer({
      id: 'cameras', data: cameras, pickable: true, stroked: true, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
      getPosition: (c) => [c.lon, c.lat], getRadius: 6, getFillColor: (c) => rgb(slowdownColor(c.slowdown), 235),
      getLineColor: [15, 23, 42, 255], getLineWidth: 2,
    }),
    new ScatterplotLayer({
      id: 'police', data: police, pickable: true, stroked: true, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
      getPosition: (p) => [p.lon, p.lat], getRadius: (p) => (p.safety ? 6 : 4),
      getFillColor: (p) => rgb(p.crash ? C.warn : C.police, Math.round(70 + 170 * (1 - (now - p.s) / POLICE_S))),
      getLineColor: (p) => (p.safety ? rgb(C.bad) : [15, 23, 42, 200]), getLineWidth: (p) => (p.safety ? 2 : 1),
      updateTriggers: { getFillColor: [Math.floor(now / 60)] },
    }),
    new ScatterplotLayer({
      id: 'chp', data: show.chp ? incidents.chp : [], pickable: true, stroked: true, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
      getPosition: (c) => [c.lon, c.lat], getRadius: (c) => (c.crash ? 8 : 6), getFillColor: rgb(C.gold),
      getLineColor: [15, 23, 42, 255], getLineWidth: 2,
    }),
    new ScatterplotLayer({
      id: 'd-warn', data: warnings, pickable: true, stroked: true, filled: false, radiusUnits: 'pixels', lineWidthUnits: 'pixels',
      getPosition: (w) => [w.lon, w.lat], getRadius: 11 + 3 * wave, getLineColor: (w) => (w.safety ? rgb(C.bad) : rgb(w.source === 'chp' ? C.gold : C.police)),
      getLineWidth: 2, updateTriggers: { getRadius: [wave] },
    }),
    new ScatterplotLayer({
      id: 'jam', data: jam ? cameras.filter((c) => c.camera_id === jam.camera_id) : [], stroked: true, filled: false,
      radiusUnits: 'pixels', lineWidthUnits: 'pixels', getPosition: (c) => [c.lon, c.lat],
      getRadius: 13 + 5 * wave, getLineColor: rgb(C.bad), getLineWidth: 2, updateTriggers: { getRadius: [wave] },
    }),
    ...(incident ? [
      new ScatterplotLayer({
        id: 'd-ring', data: [incident], stroked: true, filled: true, radiusUnits: 'meters', lineWidthUnits: 'pixels',
        getPosition: (p) => p, getRadius: 150 + 220 * wave, getFillColor: rgb(C.bad, 45), getLineColor: rgb(C.bad, 230), getLineWidth: 2,
        updateTriggers: { getRadius: [wave] },
      }),
      new ColumnLayer({
        id: 'd-beacon', data: [incident], extruded: true, radius: 22, diskResolution: 10,
        getPosition: (p) => p, getElevation: 900, getFillColor: rgb(C.bad, 200),
      }),
    ] : []),
    new PathLayer({
      id: 'd-path', data: legs, getPath: (l) => l.path, getColor: (l) => rgb(l.gm ? l.color : C.line, l.gm ? 245 : 150),
      getWidth: (l) => (l.gm ? 4 : 2.5), widthUnits: 'pixels', capRounded: true, jointRounded: true,
    }),
    new ScatterplotLayer({
      id: 'd-units', data: legs, pickable: true, radiusUnits: 'pixels', stroked: true, lineWidthUnits: 'pixels',
      getPosition: (l) => l.at, getRadius: (l) => (l.gm ? 8 : 6), getFillColor: (l) => (l.gm ? rgb(l.color) : rgb(C.surface)),
      getLineColor: (l) => (l.gm ? [255, 255, 255, 255] : rgb(l.color)), getLineWidth: 2,
    }),
    new TextLayer({
      id: 'd-labels', data: legs.filter((l) => !l.done), getPosition: (l) => l.at, getText: (l) => l.label,
      getSize: 12, getColor: [241, 245, 249, 255], getPixelOffset: (l) => l.offset, fontFamily: '"IBM Plex Sans", system-ui, sans-serif', fontWeight: 600,
      background: true, getBackgroundColor: [22, 25, 30, 235], backgroundPadding: [6, 3], characterSet: 'auto',
    }),
    ...(hospital ? [
      new PathLayer({
        id: 'h-path', data: toHospital > 0 ? [hospital] : [], getPath: (h) => h.route.map(ll), getColor: rgb(C.hospital, 230),
        getWidth: 4, widthUnits: 'pixels', capRounded: true,
      }),
      new ScatterplotLayer({
        id: 'h-unit', data: toHospital > 0 ? [hospital] : [], radiusUnits: 'pixels', stroked: true, lineWidthUnits: 'pixels',
        getPosition: (h) => ll(along(h.route, toHospital / h.drive_s)), getRadius: 7, getFillColor: rgb(C.unit), getLineColor: [255, 255, 255, 255], getLineWidth: 2,
      }),
      new ScatterplotLayer({
        id: 'h-icon', data: [hospital], radiusUnits: 'pixels', getPosition: (h) => [h.lon, h.lat], getRadius: 11, getFillColor: rgb(C.hospital),
      }),
      new TextLayer({
        id: 'h-text', data: [hospital], getPosition: (h) => [h.lon, h.lat], getText: () => 'H', getSize: 13, getColor: [255, 255, 255, 255],
        fontWeight: 800, fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
      }),
    ] : []),
  ]

  const click = (info) => {
    if (info.layer?.id === 'cameras' && info.object) return onCamera(info.object)
    if (armed && info.coordinate) onPick(info.coordinate[1], info.coordinate[0])
  }

  const toggle = () => {
    const next = !flat
    setFlat(next)
    mapRef.current?.getMap().setLayoutProperty('gm-buildings', 'visibility', next ? 'none' : 'visible')
    mapRef.current?.easeTo({ pitch: next ? 0 : TILT.pitch, bearing: next ? 0 : TILT.bearing, duration: 1200 })
  }

  return (
    <div className={`city ${armed ? 'armed' : ''}`}>
      <Map ref={mapRef} pixelRatio={DPR} initialViewState={VIEW} mapStyle={STYLE} onLoad={addBuildings} onStyleData={addBuildings} maxPitch={70}
        cooperativeGestures={narrow()} attributionControl={{ compact: true }} style={{ position: 'absolute', inset: 0 }}>
        <Overlay layers={layers} getTooltip={tooltip} onClick={click} />
        <NavigationControl position="top-right" visualizePitch />
      </Map>
      <button className="view-toggle" onClick={toggle} aria-pressed={!flat} aria-label={flat ? 'Switch to 3D view' : 'Switch to 2D view'}>
        {flat ? '3D' : '2D'}
      </button>
      {armed && <p className="armed-hint" role="status">Click anywhere in San Francisco to report an incident</p>}
      {camera && <CameraCard camera={camera} note={jam?.camera_id === camera.camera_id ? jam.text : null} onClose={() => onCamera(null)} />}
    </div>
  )
}
