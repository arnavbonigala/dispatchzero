import { fmtTime } from './api'

// The agent's own words for every version, newest first.
export default function DiagnosisFeed({ versions }) {
  const byName = Object.fromEntries(versions.map((v) => [v.version, v]))
  const rows = [...versions].reverse()
  return (
    <ol className="gm-feed">
      {rows.map((v) => {
        const parent = byName[v.parent]
        const delta = parent && v.train_median != null ? v.train_median - parent.train_median : null
        return (
          <li key={v.version} className={v.accepted ? 'gm-feed-item' : 'gm-feed-item gm-rejected'}>
            <div className="gm-feed-head">
              <strong>{v.version}</strong>
              <span className={v.accepted ? 'gm-chip gm-chip-ok' : 'gm-chip'}>{v.accepted ? 'accepted' : v.reason || 'rejected'}</span>
              {v.train_median != null && (
                <span className="gm-muted">
                  train {fmtTime(v.train_median)}
                  {delta != null && ` (${delta <= 0 ? '−' : '+'}${Math.abs(Math.round(delta))}s vs ${v.parent})`}
                </span>
              )}
            </div>
            <p>{v.diagnosis}</p>
          </li>
        )
      })}
    </ol>
  )
}
