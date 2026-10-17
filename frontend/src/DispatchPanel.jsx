import { clock, mmss } from './format'

function Side({ title, side, gm }) {
  if (!side) return null
  if (side.error) return <div className="dp-side"><h3>{title}</h3><p className="pending">{side.error}</p></div>
  const f = side.first
  return (
    <div className={`dp-side ${gm ? 'gm' : ''}`}>
      <h3>{title}</h3>
      <strong className="dp-first">{mmss(f.eta_s)}</strong>
      <span className="dp-sub">first paramedic · {f.kind} {f.unit_id}</span>
      <ul>
        <li><i className="dot amb" /> {side.chosen.unit_id} from post {side.chosen.post} · {mmss(side.chosen.eta_s)}</li>
        {side.engine
          ? <li><i className="dot eng" /> {side.engine.unit_id} from post {side.engine.post} · {mmss(side.engine.eta_s)}</li>
          : <li><i className="dot eng off" /> no engine sent</li>}
      </ul>
    </div>
  )
}

const PRIORITIES = [
  ['3', 'Priority 3', 'lights and sirens'],
  ['2', 'Priority 2', 'urgent, no sirens'],
]

export default function DispatchPanel({ state, onArm, onClose, onReplay, priority, setPriority }) {
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
          <div className="dp-prio" role="radiogroup" aria-label="Call priority">
            {PRIORITIES.map(([p, name, sub]) => (
              <button key={p} role="radio" aria-checked={priority === p} onClick={() => setPriority(p)}>
                <b>{name}</b><span>{sub}</span>
              </button>
            ))}
          </div>
          {!armed && <button className="dp-btn" onClick={onArm}>Dispatch an incident</button>}
          {error && <p className="dp-error" role="alert">{error}</p>}
        </>
      )}

      {loading && <p className="dp-hint">Replaying the week up to this moment…</p>}

      {result && (
        <>
          <p className="dp-where">
            <b>{result.incident.nbhd}</b> · Priority {result.incident.priority} · {clock(Number(result.incident.t.slice(11, 13)) * 3600 + Number(result.incident.t.slice(14, 16)) * 60)}
          </p>
          <div className="dp-compare">
            <Side title="Baseline" side={result.baseline} />
            <Side title="Golden Minute" side={result.agent} gm />
          </div>
          {result.reason && <p className="dp-reason">{result.reason}</p>}
          <div className="dp-hosp">
            <span className="dp-h">H</span>
            <div>
              <b>{result.hospital.name}</b>
              <span>{mmss(result.hospital.drive_s)} drive + ~{mmss(result.hospital.offload_s)} typical handoff</span>
            </div>
          </div>
          <p className="dp-foot">
            Traffic: {result.traffic === 'google' ? 'Google live traffic' : 'learned road speeds (no Google key)'} ·{' '}
            <button className="dp-link" onClick={onReplay}>Replay</button>
          </p>
        </>
      )}
    </section>
  )
}
