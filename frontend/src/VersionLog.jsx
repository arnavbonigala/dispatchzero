import { useEffect, useMemo, useState } from 'react'
import fxVersions from '@fixtures/engines_versions.jsonl?raw'
import { mmss } from './format'
import './VersionLog.css'

const W = 360
const H = 110
const PAD = { l: 40, r: 8, t: 10, b: 10 }
const STEP_MS = 70
const RACE_MS = 2000
const BIG_GAIN_S = 20

const metric = (v) => v.train_mean ?? v.train_slow_median
const parse = (text) => text.trim().split('\n').map((l) => JSON.parse(l))

function why(reason = '') {
  if (reason.startsWith('vetoed by reviewer: ')) return `Reviewer vetoed it: ${reason.slice(20)}`
  if (reason.includes('neighborhood spread')) return 'Widened the gap between neighborhoods'
  if (reason.startsWith('beaten by')) return 'Another idea this round did better'
  if (reason.includes('engine conflicts')) return 'Tied up too many fire engines'
  if (reason.includes('citywide')) return 'Slowed down the rest of the city'
  if (reason.startsWith('holdout')) return 'Did not hold up on a new week'
  if (reason.includes('not below best')) return 'No better than the current best'
  return 'Broke the dispatch rules'
}

function short(text, max = 110) {
  const s = text.split(/(?<=\.)\s/)[0]
  return s.length <= max ? s : `${s.slice(0, s.lastIndexOf(' ', max))}…`
}

function steps(versions) {
  let best = metric(versions[0])
  let bestV = versions[0].version
  return versions.map((v, i) => {
    const m = metric(v)
    const gain = i > 0 && v.accepted && m != null ? m - best : null
    if (v.accepted && m != null) [best, bestV] = [m, v.version]
    return { ...v, n: i, m, best, bestV, gain }
  })
}

function Chart({ pts, sel, setSel }) {
  const ys = pts.map((p) => p.m).filter((m) => m != null)
  const lo = Math.min(...ys) - 8
  const hi = Math.max(...ys) + 8
  const x = (i) => PAD.l + (i * (W - PAD.l - PAD.r)) / Math.max(pts.length - 1, 1)
  const y = (v) => PAD.t + ((hi - v) * (H - PAD.t - PAD.b)) / (hi - lo)
  const first = pts[0].best
  const last = pts[pts.length - 1].best
  return (
    <svg className="vl-chart" viewBox={`0 0 ${W} ${H}`} role="group"
      aria-label={`Best kept rule went from ${mmss(first)} to ${mmss(last)} over ${pts.length - 1} attempts. Select a point to read that step.`}>
      {[first, last].map((t) => (
        <g key={t}>
          <line className="vl-grid" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
          <text className="vl-axis" x={PAD.l - 6} y={y(t) + 4} textAnchor="end">{mmss(t)}</text>
        </g>
      ))}
      <polyline className="vl-best" points={pts.slice(0, sel + 1).map((p, i) => `${x(i)},${y(p.best)}`).join(' ')} />
      {pts.map((p, i) => (
        <circle key={p.version} cx={x(i)} cy={y(p.accepted ? p.m : p.best)} r={i === sel ? 6.5 : p.accepted ? 4.5 : 2.5}
          className={`vl-dot ${p.accepted ? 'vl-accepted' : 'vl-rejected'} ${i === sel ? 'vl-sel' : ''} ${i > sel ? 'vl-future' : ''}`}
          tabIndex={0} role="button" aria-label={`Step ${p.n}, ${p.accepted ? 'kept' : 'rejected'}`} aria-pressed={i === sel}
          onClick={() => setSel(i)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setSel(i)} />
      ))}
    </svg>
  )
}

const fxDiffs = Object.values(import.meta.glob('../../fixtures/engines_diffs.json', { import: 'default', eager: true }))[0] ?? {}

function gates(s, parent) {
  const r = s.reason ?? ''
  const ran = s.train_mean != null
  const list = [
    { label: 'Parsed and checked', detail: 'allow-listed imports, no file access', ok: ran, note: ran ? null : r.replace(/^(rejected: )+/, '') },
    s.review && { label: 'Reviewer', detail: r.startsWith('vetoed') ? 'veto' : 'pass', ok: !r.startsWith('vetoed') },
    { label: 'Sandboxed run', detail: 'no network, 90 s limit', ok: ran },
    { label: 'Training weeks', detail: `${mmss(parent?.train_mean)} → ${mmss(s.train_mean)}`, ok: !r.startsWith('train'), note: r.startsWith('train') ? why(r) : null },
    { label: 'Held-out week', detail: r.startsWith('holdout') || s.holdout_passes === false ? 'failed' : 'passed', ok: !r.startsWith('holdout') && s.holdout_passes !== false, note: r.startsWith('holdout') ? why(r) : null },
    { label: r.startsWith('beaten') ? 'Another idea this round did better' : 'Kept as the new best', ok: s.accepted },
  ].filter(Boolean)
  const fail = list.findIndex((g) => !g.ok)
  return list.map((g, i) => ({ ...g, state: fail < 0 || i < fail ? 'ok' : i === fail ? 'fail' : 'skip' }))
}

function Checks({ s, parent }) {
  return (
    <ul className="vl-checks" key={s.version} aria-label="Checks this rule went through">
      {gates(s, parent).map((g, i) => (
        <li key={g.label} className={`vl-check vl-${g.state}`} style={{ animationDelay: `${i * 140}ms` }}>
          <span aria-hidden="true">{g.state === 'ok' ? '✓' : g.state === 'fail' ? '✗' : '·'}</span>
          <span className="sr-only">{g.state === 'ok' ? 'passed' : g.state === 'fail' ? 'failed' : 'not reached'}: </span>
          <b>{g.label}</b> {g.state !== 'skip' && <em>{g.detail}</em>}
          {g.state === 'fail' && g.note && <p>{g.note}</p>}
        </li>
      ))}
    </ul>
  )
}

function Diff({ s }) {
  const text = fxDiffs[s.version]
  if (!text) return null
  const lines = text.split('\n')
  const add = lines.filter((l) => l.startsWith('+')).length
  const del = lines.filter((l) => l.startsWith('-')).length
  return (
    <figure className="vl-diff">
      <figcaption>Code it wrote vs {s.parent} <span className="vl-add">+{add}</span> <span className="vl-del">−{del}</span></figcaption>
      <pre tabIndex={0} aria-label={`Code diff, ${add} lines added, ${del} removed`}>
        {lines.map((l, i) => (
          <span key={i} className={l.startsWith('@@') ? 'vl-hunk' : l[0] === '+' ? 'vl-add' : l[0] === '-' ? 'vl-del' : ''}>{l.startsWith('@@') ? '⋯' : l}{'\n'}</span>
        ))}
      </pre>
    </figure>
  )
}

function Detail({ s, parent }) {
  if (s.n === 0) return <p className="vl-detail"><strong>Start</strong> Every unit waits at its home post; the closest idle unit is sent.</p>
  return (
    <div className="vl-detail" aria-live="polite">
      <div className="vl-head">
        <strong>Step {s.n}</strong>
        <span className={s.accepted ? 'vl-badge vl-badge-ok' : 'vl-badge'}>{s.accepted ? 'kept' : 'rejected'}</span>
        {s.gain != null && Math.abs(s.gain) >= 1 && <span className="vl-meta">{mmss(Math.abs(s.gain))} {s.gain <= 0 ? 'faster' : 'slower'}</span>}
      </div>
      <p>{short(s.diagnosis)}</p>
      <Diff s={s} />
      <Checks s={s} parent={parent} />
    </div>
  )
}

const fxLessons = Object.values(import.meta.glob('../../fixtures/engines_lessons.md', { query: '?raw', import: 'default', eager: true }))[0] ?? ''
const bullets = (text) => text.split('\n').filter((l) => l.startsWith('- ')).map((l) => l.slice(2))

export default function VersionLog({ onLearn, trained, onTrained }) {
  const [versions, setVersions] = useState(() => parse(fxVersions))
  const [lessons, setLessons] = useState(() => bullets(fxLessons))
  const pts = useMemo(() => steps(versions), [versions])
  const [sel, setSel] = useState(null)
  const [playing, setPlaying] = useState(false)
  const latestKept = pts.findLastIndex((p) => p.accepted)
  const at = Math.min(sel ?? (trained ? latestKept : 0), pts.length - 1)
  const races = useMemo(() => new Set(pts.flatMap((p, i) => (i === 0 || i === latestKept || p.gain <= -BIG_GAIN_S ? [i] : []))), [pts, latestKept])

  useEffect(() => {
    fetch('/api/lessons?mode=engines')
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((l) => l.length && setLessons(l))
      .catch(() => {})
    fetch('/api/versions?mode=engines')
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setVersions)
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!playing) return
    const finish = () => {
      setPlaying(false)
      onTrained?.(true)
    }
    const id = setTimeout(() => (at >= latestKept ? finish() : setSel(at + 1)), races.has(at) ? RACE_MS : STEP_MS)
    return () => clearTimeout(id)
  }, [playing, at, latestKept, races, onTrained])

  const raceAt = playing ? pts.findLastIndex((p, i) => i <= at && races.has(i)) : -1
  const learnV = raceAt >= 0 ? pts[raceAt].bestV : null
  const learnN = raceAt >= 0 ? pts[raceAt].n : null
  useEffect(() => {
    onLearn?.(learnV && { version: learnV, n: learnN })
  }, [learnV, learnN, onLearn])

  if (pts.length < 2) return null
  const pick = (i) => {
    setPlaying(false)
    setSel(Math.max(0, Math.min(pts.length - 1, i)))
  }
  const play = () => {
    if (playing) return setPlaying(false)
    setSel(0)
    setPlaying(true)
  }
  const kept = pts.filter((p) => p.accepted && p.n > 0).length
  const idle = !trained && !playing && sel == null

  return (
    <section className="vl" aria-labelledby="vl-title"
      onKeyDown={(e) => (e.key === 'ArrowLeft' ? pick(at - 1) : e.key === 'ArrowRight' ? pick(at + 1) : null)}>
      <div className="vl-top">
        <h2 id="vl-title">{trained ? 'How the AI learned it' : 'Train the AI'}</h2>
        <button className={trained ? 'vl-play' : 'vl-play vl-train'} onClick={play} aria-pressed={playing}>
          {playing ? '❚❚ Pause' : trained ? '▶ Replay learning' : '▶ Train'}
        </button>
      </div>
      {idle ? (
        <>
          <p className="vl-intro">The AI writes and tests {versions.length - 1} dispatch rules on three weeks of real SF 911 calls, and keeps a rule only if it also wins on a fourth week it never trained on.</p>
          <Chart pts={pts} sel={at} setSel={pick} />
          <p className="vl-locked">Live dispatch unlocks when training finishes.</p>
        </>
      ) : (
        <>
        <p className="vl-sub">{versions.length - 1} rules tried · {kept} kept · <b>{mmss(pts[at].best)}</b> average to first paramedic</p>
        <Chart pts={pts} sel={at} setSel={pick} />
        <div className="vl-nav">
          <button onClick={() => pick(at - 1)} disabled={at === 0} aria-label="Previous step">◀</button>
          <Detail s={pts[at]} parent={versions.find((v) => v.version === pts[at].parent)} />
          <button onClick={() => pick(at + 1)} disabled={at === pts.length - 1} aria-label="Next step">▶</button>
        </div>
        {lessons.length > 0 && <p className="vl-lesson"><span>Lesson</span> {short(lessons[lessons.length - 1], 140)}</p>}
        </>
      )}
    </section>
  )
}
