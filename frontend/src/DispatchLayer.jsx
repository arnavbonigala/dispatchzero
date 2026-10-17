import { Fragment, useMemo } from 'react'
import L from 'leaflet'
import { CircleMarker, Marker, Polyline, Tooltip, useMapEvents } from 'react-leaflet'
import { C } from './format'

// Position after `frac` (0..1) of the way along a [[lat, lon], ...] route, by planar length
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

const hospitalIcon = L.divIcon({ className: 'hosp-icon', html: 'H', iconSize: [20, 20] })

function ClickToPlace({ armed, onPick }) {
  useMapEvents({
    click: (e) => armed && onPick(e.latlng.lat, e.latlng.lng),
  })
  return null
}

// One unit driving its route over eta_s simulated seconds; solid for Golden Minute, hollow for baseline
function Unit({ unit, color, agent, elapsed, label }) {
  if (!unit?.route?.length) return null
  const done = elapsed >= unit.eta_s
  const at = along(unit.route, elapsed / unit.eta_s)
  return (
    <>
      <Polyline positions={unit.route}
        pathOptions={{ color, weight: agent ? 4 : 2.5, opacity: agent ? 0.85 : 0.55, dashArray: agent ? null : '6 6' }} />
      <CircleMarker center={at} radius={agent ? 7 : 6}
        pathOptions={{ color: agent ? C.surface : color, weight: 2, fillColor: agent ? color : C.surface, fillOpacity: 1 }}>
        <Tooltip direction="top" permanent={!done} offset={[0, -6]} className="unit-tip">{label}</Tooltip>
      </CircleMarker>
    </>
  )
}

export default function DispatchLayer({ armed, onPick, result, elapsed }) {
  const legs = useMemo(() => {
    if (!result) return null
    const side = (s, agent) => s && !s.error && {
      amb: { ...s.chosen, route: s.route },
      engine: s.engine,
      agent,
    }
    return [side(result.baseline, false), side(result.agent, true)].filter(Boolean)
  }, [result])

  const gm = result?.agent && !result.agent.error ? result.agent : null
  const hospital = result?.hospital
  const toHospital = gm && hospital && Math.max(0, elapsed - gm.chosen.eta_s)

  return (
    <>
      <ClickToPlace armed={armed} onPick={onPick} />
      {result && (
        <CircleMarker center={[result.incident.lat, result.incident.lon]} radius={11}
          pathOptions={{ color: C.bad, weight: 2.5, fillColor: C.bad, fillOpacity: 0.25, className: 'pulse' }}>
          <Tooltip direction="right" offset={[10, 0]}>Incident · {result.incident.nbhd}</Tooltip>
        </CircleMarker>
      )}
      {legs?.map((s) => (
        <Fragment key={s.agent ? 'gm' : 'base'}>
          <Unit unit={s.engine} color={C.engine} agent={s.agent} elapsed={elapsed}
            label={`${s.agent ? 'GM' : 'Base'} · ${s.engine?.unit_id}`} />
          <Unit unit={s.amb} color={C.unit} agent={s.agent} elapsed={elapsed}
            label={`${s.agent ? 'GM' : 'Base'} · ${s.amb.unit_id}`} />
        </Fragment>
      ))}
      {hospital && (
        <>
          <Marker position={[hospital.lat, hospital.lon]} icon={hospitalIcon}>
            <Tooltip direction="top" offset={[0, -10]}>{hospital.name}</Tooltip>
          </Marker>
          {gm && toHospital > 0 && (
            <>
              <Polyline positions={hospital.route} pathOptions={{ color: C.hospital, weight: 4, opacity: 0.8 }} />
              <CircleMarker center={along(hospital.route, toHospital / hospital.drive_s)} radius={7}
                pathOptions={{ color: C.surface, weight: 2, fillColor: C.unit, fillOpacity: 1 }} />
            </>
          )}
        </>
      )}
    </>
  )
}
