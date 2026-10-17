import { useMemo } from 'react'
import L from 'leaflet'
import { CircleMarker, GeoJSON, MapContainer, Polyline, Popup, TileLayer, Tooltip } from 'react-leaflet'
import { C, mmss, responseColor, slowdownColor } from './format'
import 'leaflet/dist/leaflet.css'

const RECENT_S = 3600

export default function ReplayMap({ calls, now, posts, neighborhoods, slowNbhds, cameras }) {
  const bounds = useMemo(() => L.geoJSON(neighborhoods).getBounds().pad(0.02), [neighborhoods])
  const postAt = (i) => posts[i] && [posts[i].lat, posts[i].lon]

  const nbhdStyle = (f) =>
    slowNbhds.has(f.properties.nhood)
      ? { color: C.slow, weight: 1.5, fillColor: C.slow, fillOpacity: 0.12 }
      : { color: C.line, weight: 0.6, fillOpacity: 0 }

  return (
    <MapContainer bounds={bounds} zoomSnap={0.25} className="map">
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
        attribution="Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"
        maxZoom={16}
      />
      <GeoJSON
        data={neighborhoods}
        style={nbhdStyle}
        onEachFeature={(f, layer) => layer.bindTooltip(f.properties.nhood, { sticky: true })}
      />

      {posts.map((p) => (
        <CircleMarker key={`post-${p.post}`} center={[p.lat, p.lon]} radius={3}
          pathOptions={{ color: C.post, weight: 1, fillColor: C.surface, fillOpacity: 1 }}>
          <Tooltip>Post {p.post} · station area {p.station_area}</Tooltip>
        </CircleMarker>
      ))}

      {calls.map((c) => {
        if (now < c.start) return null
        const arrive = c.start + c.response_s
        const driveStart = arrive - c.eta_s
        const scene = [c.lat, c.lon]

        if (now >= arrive) {
          const recent = now - arrive < RECENT_S
          return (
            <CircleMarker key={c.call_id} center={scene} radius={recent ? 5 : 3.5}
              pathOptions={{ stroke: false, fillColor: responseColor(c.response_s), fillOpacity: recent ? 0.9 : 0.35 }}>
              <Popup>
                <b>{c.nbhd}</b><br />
                {c.unit} from post {c.from_post}<br />
                Response {mmss(c.response_s)} · drive {mmss(c.eta_s)}<br />
                Historical {mmss(c.historical_s)}
              </Popup>
            </CircleMarker>
          )
        }

        const from = postAt(c.from_post)
        const driving = now >= driveStart && from
        const k = driving ? (now - driveStart) / c.eta_s : 0
        const unitAt = driving && [from[0] + (scene[0] - from[0]) * k, from[1] + (scene[1] - from[1]) * k]
        return [
          <CircleMarker key={`${c.call_id}-ring`} center={scene} radius={9}
            pathOptions={{ color: C.bad, weight: 2, fillColor: C.bad, fillOpacity: 0.15, className: 'pulse' }} />,
          driving && <Polyline key={`${c.call_id}-line`} positions={[from, scene]}
            pathOptions={{ color: C.unit, weight: 1.5, dashArray: '4 4', opacity: 0.7 }} />,
          driving && <CircleMarker key={`${c.call_id}-unit`} center={unitAt} radius={5}
            pathOptions={{ color: C.surface, weight: 1.5, fillColor: C.unit, fillOpacity: 1 }}>
            <Tooltip>{c.unit} → {c.nbhd}</Tooltip>
          </CircleMarker>,
        ]
      })}

      {cameras.map((cam) => (
        <CircleMarker key={cam.camera_id} center={[cam.lat, cam.lon]} radius={7}
          pathOptions={{ color: C.ink, weight: 1.5, fillColor: slowdownColor(cam.slowdown), fillOpacity: 0.95 }}>
          <Popup minWidth={240}>
            <div className="cam-pop">
              <b>{cam.name}</b>
              {cam.image && <img src={cam.image} alt={`Caltrans camera ${cam.camera_id}`} />}
              <span>{cam.vehicles} vehicles · {Math.round(cam.moving_frac * 100)}% moving · slowdown ×{cam.slowdown.toFixed(1)}</span>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}
