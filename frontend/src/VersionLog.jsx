import { useEffect, useState } from 'react'
import fxVersions from '@fixtures/engines_versions.jsonl?raw'
import { mmss } from './format'
import './VersionLog.css'

const W = 360
const H = 150
const PAD = { l: 44, r: 10, t: 12, b: 12 }

const metric = (v) => v.train_mean ?? v.train_slow_median
const parse = (text) => text.trim().split('\n').map((l) => JSON.parse(l))

function Chart({ versions }) {
  const pts = versions.filter((v) => metric(v) != null)
  if (pts.length < 2) return null
  const ys = pts.map(metric)
  const lo = Math.min(...ys) - 10
  const hi = Math.max(...ys) + 10
  const x = (i) => PAD.l + (i * (W - PAD.l - PAD.r)) / (pts.length - 1)
  const y = (v) => PAD.t + ((hi - v) * (H - PAD.t - PAD.b)) / (hi - lo)
  let best = null
  const step = pts.map((v, i) => {
    if (v.accepted) best = metric(v)
    return [x(i), y(best ?? metric(v))]
  })
  const first = metric(pts[0])
  return (
    <svg className="vl-chart" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`On training weeks the best kept rule went from ${mmss(first)} to ${mmss(best)} over ${pts.length - 1} attempts.`}>
      {[...new Set([first, best])].map((t) => (
        <g key={t}>
          <line className="vl-grid" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
          <text className="vl-axis" x={PAD.l - 8} y={y(t) + 5} textAnchor="end">{mmss(t)}</text>
        </g>
      ))}
      <polyline className="vl-best" points={step.map((p) => p.join(',')).join(' ')} />
      {pts.map((v, i) => v.accepted && (
        <circle key={v.version} className="vl-dot vl-accepted" cx={x(i)} cy={y(metric(v))} r={5} />
      ))}
    </svg>
  )
}

export default function VersionLog() {
  const [versions, setVersions] = useState(() => parse(fxVersions))
  useEffect(() => {
    fetch('/api/versions?mode=engines')
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setVersions)
      .catch(() => {})
  }, [])
  const latest = [...versions].reverse().find((v) => v.accepted && v.version !== 'baseline')
  const kept = versions.filter((v) => v.accepted && v.version !== 'baseline').length
  const label = versions.some((v) => v.train_mean != null) ? 'Average time to first paramedic' : 'Median in the 10 slowest neighborhoods'
  return (
    <section className="vl" aria-labelledby="vl-title">
      <h2 id="vl-title">How the AI learned it</h2>
      <p className="vl-sub">{versions.length - 1} rules written and tested, {kept} kept. {label}, training weeks.</p>
      <Chart versions={versions} />
      {latest && <p className="vl-latest">{latest.diagnosis.split(/(?<=\.)\s/)[0]}</p>}
    </section>
  )
}
