import { clock, mmss } from './format'

function Unit({ kind, unit, first }) {
  const label = kind === 'engine' ? 'Engine' : 'Ambulance'
  return (
    <li className={first ? 'first' : ''}>
      <i className={`dot ${kind === 'engine' ? 'eng' : 'amb'}`} /> <b>{label}</b> <span>{mmss(unit.eta_s)}</span>
    </li>
  )
}

function Side({ title, side, gm, vs }) {
  if (!side) return null
  if (side.error) return <div className="dp-side"><h3>{title}</h3><p className="pending">{side.error}</p></div>
  const f = side.first
  const saved = gm && vs && !vs.error ? vs.first.eta_s - f.eta_s : null
  return (
    <div className={`dp-side ${gm ? 'gm' : ''}`}>
      <h3>{title}</h3>
      <strong className="dp-first">{mmss(f.eta_s)}</strong>
      <span className="dp-sub">until a paramedic arrives</span>
      {saved != null && <span className={saved >= 1 ? 'dp-gain' : 'dp-gain flat'}>{saved >= 1 ? `${mmss(saved)} sooner` : 'same as baseline'}</span>}
      <ul>
        <Unit kind="ambulance" unit={side.chosen} first={f.kind !== 'engine'} />
        {side.engine
          ? <Unit kind="engine" unit={side.engine} first={f.kind === 'engine'} />
          : <li className="none"><i className="dot eng off" /> No fire engine sent</li>}
      </ul>
    </div>
  )
}

const TRAFFIC = {
  google: 'Google live traffic',
  cameras: 'Caltrans freeway cameras',
  osm: 'learned road speeds (no Google key)',
}
const SOURCE = { cameras: 'Caltrans freeway cameras', chp: 'CHP incidents' }

function trafficLabel(r) {
  if (r.traffic !== 'cameras') return TRAFFIC[r.traffic] ?? TRAFFIC.osm
  const names = (r.traffic_sources ?? ['cameras']).map((s) => SOURCE[s]).filter(Boolean)
  return `${names.join(' + ')}${r.cameras_ts ? ` (readings ${r.cameras_ts.slice(11, 16)})` : ''}`
}

function Warnings({ items }) {
  if (!items?.length) return null
  return (
    <div className="dp-warn" role="note" aria-label="Nearby incidents">
      <h3>Nearby</h3>
      <ul>
        {items.map((w) => (
          <li key={w.id} className={w.safety ? 'safety' : w.source}>
            <b>{w.safety && 'Weapon or violence: '}{w.source === 'chp' ? 'CHP' : 'Police'} · {w.title}</b>
            <span>{w.place} · {w.distance_m} m away · {w.live ? 'live now' : `${w.minutes_before} min earlier`}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function DispatchPanel({ state, onArm, onClose, onReplay, onWatch, children }) {
  const { armed, loading, error, result } = state
  return (
    <section className="dispatch" aria-labelledby="dp-title">
      <div className="dp-head">
        <h2 id="dp-title">Live dispatch</h2>
        {(result || armed || error) && <button className="dp-link" onClick={onClose}>Close</button>}
      </div>

      {!result && !loading && (
        <>
          <p className="dp-hint">
            {armed
              ? 'Click anywhere in San Francisco to report an incident at the replay’s current time.'
              : 'Report an incident and see which ambulance and engine each dispatcher sends, given where every unit is at that moment.'}
          </p>
          {!armed && <button className="dp-btn" onClick={onArm}>Dispatch an incident</button>}
          {error && <p className="dp-error" role="alert">{error}</p>}
        </>
      )}

      {loading && <p className="dp-hint">Replaying the week up to this moment…</p>}

      {result && (
        <>
          <p className="dp-where">
            <b>{result.incident.nbhd}</b> · {clock(Number(result.incident.t.slice(11, 13)) * 3600 + Number(result.incident.t.slice(14, 16)) * 60)}
          </p>
          <Warnings items={result.warnings} />
          {children}
          <div className="dp-compare">
            <Side title="Baseline" side={result.baseline} />
            <Side title="DispatchZero" side={result.agent} gm vs={result.baseline} />
          </div>
          {result.reason && <p className="dp-reason">{result.reason}</p>}
          {result.traffic_reason && (
            <div className="dp-reason dp-traffic">
              <p>{result.traffic_reason}</p>
              {onWatch && <button className="dp-watch" onClick={onWatch}>Watch the camera</button>}
            </div>
          )}
          <div className="dp-hosp">
            <span className="dp-h">H</span>
            <div>
              <b>{result.hospital.name}</b>
              <span>{mmss(result.hospital.drive_s)} drive + ~{mmss(result.hospital.offload_s)} typical handoff</span>
            </div>
          </div>
          <p className="dp-foot">
            Traffic: {trafficLabel(result)} ·{' '}
            <button className="dp-link" onClick={onReplay}>Replay</button>
          </p>
        </>
      )}
    </section>
  )
}
