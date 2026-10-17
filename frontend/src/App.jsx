import { useEffect, useMemo, useRef, useState } from 'react'
import ReplayMap from './ReplayMap'
import DispatchLayer from './DispatchLayer'
import DispatchPanel from './DispatchPanel'
import VersionLog from './VersionLog.jsx'
import { dispatchIncident, loadCameras, loadFixtures, loadLive } from './data'
import { C, clock, mmss, secOfDay } from './format'
import './App.css'

const DAY_S = 24 * 3600
const SPEEDS = [300, 600, 1800, 3600]
const CAMERA_POLL_MS = 30000

function useData() {
  const [data, setData] = useState(loadFixtures)
  useEffect(() => {
    let alive = true
    loadLive().then((live) => alive && setData((d) => ({ ...d, ...live })))
    const id = setInterval(() => {
      loadCameras().then((cameras) => alive && setData((d) => ({ ...d, cameras }))).catch(() => {})
    }, CAMERA_POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])
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

const DISPATCH_SPEED = 60 // simulated seconds per real second in the dispatch animation

// Arm, click the map, fetch the decision, then run a clock for the animation
function useDispatch(day, playback) {
  const [state, setState] = useState({ armed: false, loading: false, error: null, result: null })
  const [elapsed, setElapsed] = useState(0)
  const [runId, setRunId] = useState(0)

  useEffect(() => {
    if (!state.result) return
    const r = state.result
    const legs = [r.baseline, r.agent].filter((s) => s && !s.error)
    const end = Math.max(...legs.flatMap((s) => [s.chosen.eta_s, s.engine?.eta_s ?? 0])) + (r.hospital?.drive_s ?? 0)
    let raf
    const t0 = performance.now()
    const tick = (ts) => {
      const e = ((ts - t0) / 1000) * DISPATCH_SPEED
      setElapsed(Math.min(e, end))
      if (e < end) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [state.result, runId])

  const arm = () => {
    if (playback.playing) playback.toggle()
    setState({ armed: true, loading: false, error: null, result: null })
  }
  const close = () => setState({ armed: false, loading: false, error: null, result: null })
  const pick = (lat, lon) => {
    const s = Math.floor(playback.now)
    const pad = (n) => String(n).padStart(2, '0')
    const t = `${day}T${pad(Math.floor(s / 3600) % 24)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`
    setState({ armed: false, loading: true, error: null, result: null })
    dispatchIncident(lat, lon, t, '2')
      .then((result) => {
        setElapsed(0)
        setState({ armed: false, loading: false, error: null, result })
      })
      .catch((e) => setState({ armed: false, loading: false, result: null,
        error: `Live dispatch needs the API running (backend/api.py). ${e.message}` }))
  }
  const replay = () => {
    setElapsed(0)
    setRunId((n) => n + 1)
  }
  return { state, elapsed, arm, close, pick, replay }
}

const gain = (diff) => mmss(Math.abs(diff))

const learned = Object.values(import.meta.glob('../../fixtures/learned.json', { import: 'default', eager: true }))[0]

function Hero({ headline }) {
  const f = headline.frozen
  const b = headline.baseline
  const mean = f.mean_s - b.mean_s
  return (
    <section className="hero" aria-label="Result">
      <p className="hero-main">
        First paramedic on scene, <b>{gain(mean)}</b> {mean <= 0 ? 'faster' : 'slower'} on average
      </p>
      <div className="hero-row">
        <div className="hero-compare">
          <div><span>Baseline</span><strong>{mmss(b.mean_s)}</strong></div>
          <div className="gm"><span>Golden Minute</span><strong>{mmss(f.mean_s)}</strong></div>
        </div>
        <ul className="hero-sub">
          <li><b>{gain(headline.vs_baseline.slow_nbhds.median_diff_s)}</b> faster in the 10 slowest neighborhoods</li>
          <li><b>{gain(f.p90_s - b.p90_s)}</b> faster for the slowest 10% of calls</li>
          {f.nbhd_spread_s != null && b.nbhd_spread_s != null && (
            <li>Gap between neighborhoods <b>{mmss(b.nbhd_spread_s)} → {mmss(f.nbhd_spread_s)}</b></li>
          )}
        </ul>
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

function Legend() {
  return (
    <ul className="legend">
      <li><i style={{ background: C.good }} /> ≤ 8 min</li>
      <li><i style={{ background: C.warn }} /> 8–12 min</li>
      <li><i style={{ background: C.bad }} /> &gt; 12 min</li>
      <li><i style={{ background: C.unit }} /> ambulance en route</li>
      <li><i style={{ background: C.engine }} /> fire engine en route</li>
      <li><i className="sq" style={{ borderColor: C.slow, background: `${C.slow}22` }} /> slowest 10 neighborhoods</li>
      <li><i className="ring" style={{ borderColor: C.ink }} /> freeway camera</li>
    </ul>
  )
}

const SOURCES = [
  ['8,607', 'SF 911 ambulance calls over 5 weeks'],
  ['10,668', 'dispatches by 57 ambulances'],
  ['9,998', 'dispatches by 34 paramedic fire engines, with locations'],
  ['62%', 'of calls flagged life-threatening'],
  ['10,033', 'intersections in the road network'],
  ['5,165', 'real drives train its drive-time model (error 253 s → 197 s)'],
  ['34,440', 'neighborhood-hour demand predictions from time, weather and demographics'],
  ['', 'Lookahead: coverage of the next hour\'s expected calls before each dispatch'],
  ['11', 'hospitals with real handoff times'],
  ['33', 'live Caltrans freeway cameras'],
  ['840', 'hours of weather, rain and temperature'],
  ['41', 'neighborhoods with population and share 65+'],
  ['', 'Each engine\'s hourly workload, and demand by hour and area'],
]

function Sources() {
  return (
    <section className="sources" aria-labelledby="sources-title">
      <h2 id="sources-title">Data it learns from</h2>
      <ul>
        {SOURCES.map(([n, label]) => (
          <li key={label}>{n && <b>{n}</b>} {label}</li>
        ))}
      </ul>
    </section>
  )
}

function ViewToggle({ view, setView, agent }) {
  return (
    <div className="toggle" role="group" aria-label="Dispatcher shown on the map">
      {['baseline', 'agent'].map((v) => (
        <button key={v} aria-pressed={view === v} disabled={v === 'agent' && !agent} onClick={() => setView(v)}>
          {v === 'baseline' ? 'Baseline' : 'Golden Minute'}
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const data = useData()
  const playback = usePlayback()
  const [view, setView] = useState('agent')
  const [pickedDay, setDay] = useState(null)

  const shown = view === 'agent' && data.agent ? data.agent : data.baseline
  const { days, initial } = useMemo(() => daysOf(data.baseline.results), [data.baseline])
  const day = days.includes(pickedDay) ? pickedDay : initial

  const dispatch = useDispatch(day, playback)
  const dispatching = dispatch.state.result || dispatch.state.loading

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
            Real San Francisco 911 calls from a week the AI never saw
            {learned && <> · last learned {dayLabel(learned.last_learned)}</>}
          </p>
        </div>
        <ViewToggle view={view} setView={setView} agent={data.agent} />
      </header>
      <Hero headline={data.headline} />
      <main className="body">
        <div className="map-wrap">
          <ReplayMap calls={dispatching ? [] : calls} now={playback.now} cameras={data.cameras} posts={data.posts}
            neighborhoods={data.neighborhoods} slowNbhds={data.slowNbhds} armed={dispatch.state.armed}>
            <DispatchLayer armed={dispatch.state.armed} onPick={dispatch.pick}
              result={dispatch.state.result} elapsed={dispatch.elapsed} />
          </ReplayMap>
          <Legend />
        </div>
        <aside className="side">
          <DispatchPanel state={dispatch.state} onArm={dispatch.arm} onClose={dispatch.close} onReplay={dispatch.replay} />
          <VersionLog />
          <Sources />
        </aside>
      </main>
      <Timeline {...playback} day={day} days={days} setDay={setDay} />
    </div>
  )
}
