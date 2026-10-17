import state from '@fixtures/engines_state.json'
import { clock, mmss } from './format'

const W = 300
const H = 44
const DAY_S = 86400

function at(rows, now) {
  let lo = 0
  let hi = rows.length - 1
  let hit = null
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (rows[mid][0] <= now) {
      hit = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  return hit
}

function Spark({ rows, upto, max }) {
  const pts = rows.slice(0, upto + 1).map((r) => `${((r[0] / DAY_S) * W).toFixed(1)},${(H - (r[1] / max) * H).toFixed(1)}`)
  const last = rows[upto]
  return (
    <svg className="live-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`Free ambulances through the day, now ${last[1]}`}>
      <polyline points={pts.join(' ')} />
      <circle cx={(last[0] / DAY_S) * W} cy={H - (last[1] / max) * H} r="2.5" />
    </svg>
  )
}

export default function SystemBar({ day, now, version, dispatchMs }) {
  const rows = state[version === 'best' ? state.best : 'baseline']?.[day]
  const i = rows ? at(rows, now) : null
  const r = i == null ? null : rows[i]
  const hour = state.hourly[day]?.[Math.floor(now / 3600) % 24]
  const max = rows ? Math.max(...rows.map((x) => x[2])) : 1
  return (
    <section className="live glass" aria-label="Live city state">
      <div className="live-head">
        <h2><i className="sys-live" aria-hidden="true" />Live city state</h2>
        <span>{clock(now)}</span>
      </div>
      {r ? (
        <>
          <div className="live-row">
            <span>Ambulances free</span>
            <b>{r[1]} <small>of {r[2]} on duty</small></b>
          </div>
          <Spark rows={rows} upto={i} max={max} />
          <div className="live-grid">
            <div><span>Paramedic engines free</span><b>{r[3]} <small>of 34</small></b></div>
            <div><span>If a call came in now</span><b>{mmss(r[5])} <small>closest free paramedic's drive</small></b></div>
            {hour && <div><span>Calls this hour</span><b>{hour[1]} <small>forecast {hour[0].toFixed(1)}</small></b></div>}
            {hour && hour[3] != null && <div><span>Weather</span><b>{Math.round(hour[3])}°C <small>{hour[2] > 0.1 ? `rain ${hour[2]} mm` : 'dry'}</small></b></div>}
          </div>
        </>
      ) : (
        <p className="live-empty">State starts with the first call of the day.</p>
      )}
      {dispatchMs != null && <p className="live-foot">Last dispatch: week replayed and decided in {(dispatchMs / 1000).toFixed(1)} s</p>}
    </section>
  )
}
