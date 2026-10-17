import { fmtTime } from './api'

const W = 640
const H = 260
const PAD = { l: 52, r: 16, t: 16, b: 44 }

// Train and holdout medians per version; rejected candidates greyed out, frozen-week results as diamonds.
export default function VersionChart({ versions }) {
  if (!versions.length) return <p className="gm-muted">No versions yet.</p>
  const vals = versions.flatMap((v) => [v.train_median, v.holdout_median, v.frozen_median]).filter((x) => x != null)
  if (!vals.length) return <p className="gm-muted">No scored versions yet.</p>
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const span = Math.max(hi - lo, 20)
  const y0 = lo - span * 0.15
  const y1 = hi + span * 0.15
  const n = versions.length
  const x = (i) => PAD.l + (n === 1 ? (W - PAD.l - PAD.r) / 2 : (i * (W - PAD.l - PAD.r)) / (n - 1))
  const y = (s) => PAD.t + ((y1 - s) * (H - PAD.t - PAD.b)) / (y1 - y0)
  const accepted = versions.map((v, i) => ({ v, i })).filter(({ v }) => v.accepted && v.train_median != null)
  const line = (key) => accepted.filter(({ v }) => v[key] != null).map(({ v, i }) => `${x(i)},${y(v[key])}`).join(' ')
  const ticks = Array.from({ length: 5 }, (_, k) => y0 + ((k + 0.5) * (y1 - y0)) / 5)
  const every = Math.ceil(n / 12)

  return (
    <figure className="gm-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Median response time per policy version">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} className="gm-grid" />
            <text x={PAD.l - 8} y={y(t) + 4} textAnchor="end" className="gm-axis">
              {fmtTime(t)}
            </text>
          </g>
        ))}
        {versions.map((v, i) =>
          i % every === 0 || i === n - 1 ? (
            <text key={v.version} x={x(i)} y={H - PAD.b + 18} textAnchor="middle" className="gm-axis">
              {v.version}
            </text>
          ) : null,
        )}
        <polyline points={line('train_median')} className="gm-line-train" />
        <polyline points={line('holdout_median')} className="gm-line-holdout" />
        {versions.map((v, i) => (
          <g key={v.version}>
            {v.train_median != null && (
              <circle cx={x(i)} cy={y(v.train_median)} r={v.accepted ? 5 : 4} className={v.accepted ? 'gm-pt-train' : 'gm-pt-rejected'}>
                <title>{`${v.version} train ${fmtTime(v.train_median)}${v.accepted ? '' : ` (rejected: ${v.reason})`}`}</title>
              </circle>
            )}
            {v.accepted && v.holdout_median != null && (
              <circle cx={x(i)} cy={y(v.holdout_median)} r={4} className="gm-pt-holdout">
                <title>{`${v.version} holdout ${fmtTime(v.holdout_median)}`}</title>
              </circle>
            )}
            {v.frozen_median != null && (
              <rect x={x(i) - 5} y={y(v.frozen_median) - 5} width={10} height={10} transform={`rotate(45 ${x(i)} ${y(v.frozen_median)})`} className="gm-pt-frozen">
                <title>{`${v.version} frozen week ${fmtTime(v.frozen_median)}`}</title>
              </rect>
            )}
          </g>
        ))}
      </svg>
      <figcaption className="gm-legend">
        <span><i className="gm-key gm-key-train" /> train weeks</span>
        <span><i className="gm-key gm-key-holdout" /> holdout week</span>
        <span><i className="gm-key gm-key-frozen" /> frozen week</span>
        <span><i className="gm-key gm-key-rejected" /> rejected</span>
      </figcaption>
    </figure>
  )
}
