import { useEffect, useMemo, useRef, useState } from 'react'
import ReplayMap from './ReplayMap'
import VersionLog from './VersionLog.jsx'
import { loadCameras, loadFixtures, loadLive } from './data'
import { C, TARGET_S, clock, median, mmss, responseColor, secOfDay } from './format'
import './App.css'

const DAY_S = 24 * 3600
const SPEEDS = [300, 600, 1800, 3600]
const CAMERA_POLL_MS = 30000

function useData(mode) {
  const [data, setData] = useState(loadFixtures)
  useEffect(() => {
    let alive = true
    setData((d) => ({ ...d, ...loadFixtures(mode), agent: null }))
    loadLive(mode).then((live) => alive && setData((d) => ({ ...d, ...live })))
    const id = setInterval(() => {
      loadCameras().then((cameras) => alive && setData((d) => ({ ...d, cameras }))).catch(() => {})
    }, CAMERA_POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [mode])
  return data
}

// Frozen results span a week; the replay plays one day of it. Prefer a Tuesday (the demo script's day).
function daysOf(results) {
  const days = [...new Set(results.map((c) => c.t.slice(0, 10)))].sort()
  const tuesday = days.find((d) => new Date(`${d}T12:00:00`).getDay() === 2)
  return { days, initial: tuesday ?? days[0] }
}

const dayLabel = (d) =>
  new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })

function usePlayback() {
  const [now, setNow] = useState(6 * 3600)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(600)
  const last = useRef(null)

  useEffect(() => {
    if (!playing) return
    let raf
    const tick = (ts) => {
      if (last.current != null) {
        const dt = (ts - last.current) / 1000
        setNow((n) => {
          const next = n + dt * speed
          if (next >= DAY_S) setPlaying(false)
          return Math.min(next, DAY_S)
        })
      }
      last.current = ts
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      last.current = null
    }
  }, [playing, speed])

  const toggle = () => {
    if (!playing && now >= DAY_S) setNow(0)
    setPlaying(!playing)
  }
  return { now, setNow, playing, toggle, speed, setSpeed }
}

function Stat({ label, value, sub, tone }) {
  return (
    <div className={`stat ${tone || ''}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  )
}

const faster = (diff) => `${mmss(Math.abs(diff))} ${diff <= 0 ? 'faster' : 'slower'}`

function Headline({ headline, mode }) {
  if (!headline?.vs_baseline) return null
  const g = headline.vs_baseline
  return (
    <p className={`headline ${mode ? 'engines' : ''}`}>
      <span>Agent {headline.version} on a week it never saw{mode ? ', first paramedic on scene' : ''}:</span>
      <span><b>{faster(g.slow_nbhds.median_diff_s)}</b> in the 10 slowest neighborhoods</span>
      <span><b>{faster(g.everyone.median_diff_s)}</b> citywide</span>
    </p>
  )
}

function Compare({ baseline, agent, stale }) {
  const b = baseline.summary
  const a = agent?.summary
  const delta = a && a.slow_median_s - b.slow_median_s
  if (stale) {
    return (
      <section className="compare" aria-label="Baseline versus agent">
        <p className="pending" role="status">Replaying the week with fire engines…</p>
      </section>
    )
  }
  return (
    <section className="compare" aria-label="Baseline versus agent">
      <div className="compare-col">
        <h2>Baseline</h2>
        <Stat label="10 slowest neighborhoods" value={mmss(b.slow_median_s)} sub={`median · ${b.slow_n} calls`} tone="slow" />
        <Stat label="Citywide" value={mmss(b.median_s)} sub={`median · ${b.n} calls`} />
      </div>
      <div className="compare-col">
        <h2>Agent</h2>
        {a ? (
          <>
            <Stat label="10 slowest neighborhoods" value={mmss(a.slow_median_s)}
              sub={`${faster(delta)} vs baseline`} tone="slow" />
            <Stat label="Citywide" value={mmss(a.median_s)} sub={`${faster(a.median_s - b.median_s)} vs baseline`} />
          </>
        ) : (
          <p className="pending">Waiting for an agent replay of this week.</p>
        )}
      </div>
    </section>
  )
}

const engineFirst = (c) => c.engine_unit && c.engine_s <= c.ambulance_s

function Feed({ calls, now, slowNbhds }) {
  const recent = calls.filter((c) => c.start <= now).slice(-8).reverse()
  return (
    <section className="feed" aria-label="Recent calls">
      <h2>Recent calls</h2>
      {!recent.length && <p className="pending">Press play to start the replay.</p>}
      <ol>
        {recent.map((c) => {
          const done = now >= c.start + c.response_s
          return (
            <li key={c.call_id}>
              <span className="feed-time">{clock(c.start)}</span>
              <span className="feed-place">
                {c.nbhd}
                {slowNbhds.has(c.nbhd) && <em className="tag">slow 10</em>}
                <small>{engineFirst(c) ? `Engine ${c.engine_unit} first · ${c.unit} following` : `${c.unit} from post ${c.from_post}`}</small>
              </span>
              <span className="feed-resp" style={{ color: done ? responseColor(c.response_s) : C.unit }}>
                {done ? mmss(c.response_s) : 'en route'}
              </span>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function Live({ calls, now, slowNbhds }) {
  const arrived = calls.filter((c) => c.start + c.response_s <= now)
  const all = median(arrived.map((c) => c.response_s))
  const slow = median(arrived.filter((c) => slowNbhds.has(c.nbhd)).map((c) => c.response_s))
  const under = arrived.length ? arrived.filter((c) => c.response_s <= TARGET_S).length / arrived.length : null
  return (
    <section className="live" aria-label="Replay so far">
      <h2>So far today</h2>
      <div className="live-row">
        <Stat label="Arrived" value={arrived.length} />
        <Stat label="Median" value={mmss(all)} />
        <Stat label="Slow 10" value={mmss(slow)} tone="slow" />
        <Stat label="Under 8 min" value={under == null ? '—' : `${Math.round(under * 100)}%`} />
      </div>
    </section>
  )
}

function Timeline({ now, setNow, playing, toggle, speed, setSpeed, day, days, setDay }) {
  return (
    <footer className="timeline">
      <button className="play" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
        {playing ? '❚❚' : '▶'}
      </button>
      <span className="clock">{clock(now)}</span>
      <input type="range" min={0} max={DAY_S} step={60} value={now}
        onChange={(e) => setNow(Number(e.target.value))} aria-label="Replay time" />
      {days.length > 1 ? (
        <select value={day} onChange={(e) => setDay(e.target.value)} aria-label="Replay day">
          {days.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
        </select>
      ) : (
        <span className="day">{dayLabel(day)}</span>
      )}
      <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Replay speed">
        {SPEEDS.map((s) => <option key={s} value={s}>{s / 60}× min/s</option>)}
      </select>
    </footer>
  )
}

function Legend({ mode }) {
  return (
    <ul className="legend">
      <li><i style={{ background: C.good }} /> ≤ 8 min</li>
      <li><i style={{ background: C.warn }} /> 8–12 min</li>
      <li><i style={{ background: C.bad }} /> &gt; 12 min</li>
      <li><i style={{ background: C.unit }} /> ambulance en route</li>
      {mode && <li><i style={{ background: C.engine }} /> fire engine en route</li>}
      <li><i className="sq" style={{ borderColor: C.slow, background: `${C.slow}22` }} /> slowest 10 neighborhoods</li>
      <li><i className="ring" style={{ borderColor: C.ink }} /> freeway camera</li>
    </ul>
  )
}

function ModeToggle({ mode, setMode }) {
  return (
    <div className="toggle" role="group" aria-label="Units the dispatcher can send">
      {[['', 'Ambulances'], ['engines', '+ Fire engines']].map(([m, label]) => (
        <button key={label} aria-pressed={mode === m} onClick={() => setMode(m)}>{label}</button>
      ))}
    </div>
  )
}

function ViewToggle({ view, setView, agent }) {
  return (
    <div className="toggle" role="group" aria-label="Policy shown on the map">
      {['baseline', 'agent'].map((v) => (
        <button key={v} aria-pressed={view === v} disabled={v === 'agent' && !agent} onClick={() => setView(v)}>
          {v === 'baseline' ? 'Baseline' : agent ? `Agent ${agent.version}` : 'Agent'}
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const [mode, setMode] = useState('')
  const data = useData(mode)
  const playback = usePlayback()
  const [view, setView] = useState('baseline')
  const [pickedDay, setDay] = useState(null)

  const shown = view === 'agent' && data.agent ? data.agent : data.baseline
  const { days, initial } = useMemo(() => daysOf(data.baseline.results), [data.baseline])
  const day = days.includes(pickedDay) ? pickedDay : initial

  const calls = useMemo(
    () =>
      shown.results
        .filter((c) => c.t.startsWith(day))
        .map((c) => ({ ...c, start: secOfDay(c.t) }))
        .sort((x, y) => x.start - y.start),
    [shown, day],
  )

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Golden Minute</h1>
          <p>
            Replay of real SF ambulance calls · {data.baseline.summary.split} week
            <span className={`source ${data.source}`}>{data.source === 'api' ? 'live API' : 'fixtures'}</span>
          </p>
          <div className="toggles">
            <ModeToggle mode={mode} setMode={setMode} />
            <ViewToggle view={view} setView={setView} agent={data.agent} />
          </div>
        </div>
        <Compare baseline={data.baseline} agent={data.agent} stale={data.stale} />
      </header>
      <Headline headline={data.headline} mode={mode} />
      <main className="body">
        <div className="map-wrap">
          <ReplayMap calls={calls} now={playback.now} cameras={data.cameras} posts={data.posts}
            neighborhoods={data.neighborhoods} slowNbhds={data.slowNbhds} />
          <Legend mode={mode} />
        </div>
        <aside className="side">
          <Live calls={calls} now={playback.now} slowNbhds={data.slowNbhds} />
          <Feed calls={calls} now={playback.now} slowNbhds={data.slowNbhds} />
          <VersionLog mode={mode} />
        </aside>
      </main>
      <Timeline {...playback} day={day} days={days} setDay={setDay} />
    </div>
  )
}
