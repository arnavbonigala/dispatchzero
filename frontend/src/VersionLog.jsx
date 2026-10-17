import { useEffect, useState } from 'react'
import './VersionLog.css'

const W = 640
const H = 220
const PAD = { l: 48, r: 16, t: 16, b: 32 }

function fmt(s) {
  if (s == null) return '–'
  const sign = s > 0 ? '+' : s < 0 ? '−' : ''
  return `${sign}${Math.abs(Math.round(s))}s`
}

function VersionChart({ versions }) {
  const pts = versions.filter((v) => v.train_slow_median != null)
  if (pts.length < 2) return <p className="vl-empty">Waiting for the first candidate.</p>
  const ys = pts.map((v) => v.train_slow_median)
  const lo = Math.min(...ys) - 10
  const hi = Math.max(...ys) + 10
  const x = (i) => PAD.l + (i * (W - PAD.l - PAD.r)) / (pts.length - 1)
  const y = (v) => PAD.t + ((hi - v) * (H - PAD.t - PAD.b)) / (hi - lo)
  let best = null
  const step = pts.map((v, i) => {
    if (v.accepted) best = v.train_slow_median
    return [x(i), y(best ?? v.train_slow_median)]
  })
  const ticks = [lo + 10, (lo + hi) / 2, hi - 10].map(Math.round)
  return (
    <svg className="vl-chart" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Slow-neighborhood median on training weeks across ${pts.length} versions. Best accepted: ${Math.round(best)} seconds.`}>
      {ticks.map((t) => (
        <g key={t}>
          <line className="vl-grid" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
          <text className="vl-axis" x={PAD.l - 6} y={y(t) + 4} textAnchor="end">{t}s</text>
        </g>
      ))}
      <polyline className="vl-best" points={step.map((p) => p.join(',')).join(' ')} />
      {pts.map((v, i) => (
        <g key={v.version}>
          <circle className={v.accepted ? 'vl-dot vl-accepted' : 'vl-dot vl-rejected'} cx={x(i)} cy={y(v.train_slow_median)} r={v.accepted ? 6 : 4.5}>
            <title>{`${v.version}: ${Math.round(v.train_slow_median)}s, ${v.accepted ? 'accepted' : 'rejected'}`}</title>
          </circle>
          <text className="vl-axis" x={x(i)} y={H - 10} textAnchor="middle">{v.version === 'baseline' ? 'base' : v.version.slice(1).replace(/^0+/, '')}</text>
        </g>
      ))}
    </svg>
  )
}

function Feed({ versions }) {
  const [all, setAll] = useState(false)
  const shown = [...versions].reverse().slice(0, all ? undefined : 4)
  return (
    <>
    <ol className="vl-feed" reversed>
      {shown.map((v) => (
        <li key={v.version} className={v.accepted ? 'vl-item' : 'vl-item vl-item-rejected'}>
          <div className="vl-head">
            <strong>{v.version}</strong>
            <span className={v.accepted ? 'vl-badge vl-badge-ok' : 'vl-badge'}>{v.accepted ? 'accepted' : 'rejected'}</span>
            {v.holdout_slow_diff != null && v.version !== 'baseline' && (
              <span className="vl-meta">holdout: slow {fmt(v.holdout_slow_diff)}, citywide {fmt(v.holdout_all_diff)}</span>
            )}
          </div>
          <p>{v.diagnosis}</p>
          {!v.accepted && v.reason && <p className="vl-reason">{v.reason}</p>}
        </li>
      ))}
    </ol>
    {versions.length > 4 && (
      <button className="vl-more" onClick={() => setAll(!all)} aria-expanded={all}>
        {all ? 'Show latest 4' : `Show all ${versions.length}`}
      </button>
    )}
    </>
  )
}

const FIXTURES = {
  '': () => import('@fixtures/versions.jsonl?raw'),
  engines: () => import('@fixtures/engines_versions.jsonl?raw'),
}

export default function VersionLog({ mode = '' }) {
  const [versions, setVersions] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    setVersions(null)
    const fixture = () => FIXTURES[mode]()
      .then((m) => setVersions(m.default.trim().split('\n').map((l) => JSON.parse(l))))
      .catch(() => setError('Versions unavailable'))
    const load = () => fetch(`/api/versions${mode ? `?mode=${mode}` : ''}`).then((r) => {
      if (!r.ok) throw new Error(r.status)
      return r.json()
    }).then(setVersions).catch(fixture)
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [mode])
  if (error && !versions) return <p className="vl-empty" role="alert">{error}</p>
  if (!versions) return <p className="vl-empty">Loading…</p>
  const accepted = versions.filter((v) => v.accepted).length - 1
  return (
    <section className="vl" aria-labelledby="vl-title">
      <h2 id="vl-title">Agent versions{mode ? ' · with fire engines' : ''}</h2>
      <p className="vl-sub">{versions.length - 1} candidates, {accepted} accepted. Dots: median response in the 10 slowest neighborhoods (training weeks). Line: best accepted so far.</p>
      <VersionChart versions={versions} />
      <h3>Diagnosis feed</h3>
      <Feed versions={versions} />
    </section>
  )
}
