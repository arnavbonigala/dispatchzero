import { useEffect, useMemo, useRef, useState } from 'react'
import CityMap from './CityMap'
import DispatchPanel from './DispatchPanel'
import VersionLog from './VersionLog.jsx'
import CallFlow from './CallFlow.jsx'
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

const LEARNING = Object.values(import.meta.glob('../../fixtures/engines_learning.json', { import: 'default', eager: true }))[0]
const LEARN_HOLD_MS = 2500
const RACE_S = 900
const RACE_MS = 1600

function useLearning(playback) {
  const [step, setStep] = useState(null)
  const [shown, setShown] = useState(null)
  useEffect(() => {
    if (step) return setShown(step)
    const id = setTimeout(() => setShown(null), LEARN_HOLD_MS)
    return () => clearTimeout(id)
  }, [step])
  const { playing, toggle } = playback
  useEffect(() => {
    if (step && playing) toggle()
  }, [step, playing, toggle])
  const version = shown?.version
  const calls = useMemo(() => {
    const rows = version && LEARNING?.versions[version]
    return rows && LEARNING.calls.map((c, i) => ({ ...c, start: 0, ...Object.fromEntries(LEARNING.fields.map((f, k) => [f, rows[i][k]])) }))
  }, [version])
  const [now, setNow] = useState(0)
  useEffect(() => {
    if (!version) return
    let raf
    const t0 = performance.now()
    const tick = (ts) => {
      const k = Math.min(1, (ts - t0) / RACE_MS)
      setNow(k * RACE_S)
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [version])
  return { onLearn: setStep, calls, step: shown, now }
}

function LearnChip({ step, calls, now }) {
  const fast = calls.filter((c) => c.response_s <= 8 * 60 && c.response_s <= now).length
  return (
    <p className="learn-chip glass" role="status">
      {step.n === 0 ? 'Before learning' : `After step ${step.n}`} · <b className="learn-clock">{mmss(now)}</b> · <b>{fast}</b> of {calls.length} calls reached in 8 min
    </p>
  )
}

const learned = Object.values(import.meta.glob('../../fixtures/learned.json', { import: 'default', eager: true }))[0]

const still = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

function useCountUp(target, ms = 1600) {
  const [v, setV] = useState(still ? target : 0)
  useEffect(() => {
    if (still) return setV(target)
    let raf
    const t0 = performance.now()
    const tick = (ts) => {
      const k = Math.min(1, (ts - t0) / ms)
      setV(target * (1 - (1 - k) ** 3))
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

function Hero({ headline }) {
  const f = headline.frozen
  const b = headline.baseline
  const mean = f.mean_s - b.mean_s
  const shown = useCountUp(Math.abs(mean))
  const stats = [
    [headline.vs_baseline.slow_nbhds.median_diff_s, 'faster in the 10 slowest neighborhoods'],
    [f.p90_s - b.p90_s, 'faster for the slowest 10% of calls'],
  ]
  return (
    <section className="hero glass" aria-label="Result">
      <p className="eyebrow">First paramedic on scene · unseen week</p>
      <p className="hero-main">
        <b className="big" aria-label={`${gain(mean)} ${mean <= 0 ? 'faster' : 'slower'} on average`}>{mmss(shown)}</b>
        <span aria-hidden="true">{mean <= 0 ? 'faster' : 'slower'} on average</span>
      </p>
      <div className="hero-compare">
        <div><span>Baseline</span><strong>{mmss(b.mean_s)}</strong></div>
        <i aria-hidden="true">→</i>
        <div className="gm"><span>DispatchZero</span><strong>{mmss(f.mean_s)}</strong></div>
      </div>
      <ul className="hero-sub">
        {stats.map(([d, label]) => <li key={label}><b>{gain(d)}</b> {label}</li>)}
        {f.nbhd_spread_s != null && b.nbhd_spread_s != null && (
          <li>Gap between neighborhoods <b>{mmss(b.nbhd_spread_s)} → {mmss(f.nbhd_spread_s)}</b></li>
        )}
      </ul>
    </section>
  )
}

function Timeline({ now, setNow, playing, toggle, speed, setSpeed, day, days, setDay }) {
  return (
    <footer className="timeline glass">
      <button className="play" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
        {playing ? '❚❚' : '▶'}
      </button>
      <span className="clock">{clock(now)}</span>
      <input style={{ '--pct': `${(now / DAY_S) * 100}%` }} type="range" min={0} max={DAY_S} step={60} value={now}
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
    <ul className="legend glass">
      <li><i style={{ background: C.good }} /> ≤ 8 min</li>
      <li><i style={{ background: C.warn }} /> 8–12 min</li>
      <li><i style={{ background: C.bad }} /> &gt; 12 min</li>
      <li><i style={{ background: C.unit }} /> ambulance</li>
      <li><i style={{ background: C.engine }} /> fire engine</li>
      <li><i className="sq" style={{ borderColor: C.slow, background: `${C.slow}33` }} /> slowest 10 areas</li>
      <li><i className="ring" style={{ borderColor: C.ink, background: C.good }} /> freeway camera</li>
      <li><i className="bar" /> column height = response time</li>
    </ul>
  )
}

function ViewToggle({ view, setView, agent }) {
  return (
    <div className="toggle" role="group" aria-label="Dispatcher shown on the map">
      {['baseline', 'agent'].map((v) => (
        <button key={v} aria-pressed={view === v} disabled={v === 'agent' && !agent} onClick={() => setView(v)}>
          {v === 'baseline' ? 'Baseline' : 'DispatchZero'}
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
  const learning = useLearning(playback)
  const [camera, setCamera] = useState(null)
  const jam = useMemo(() => {
    const r = dispatch.state.result
    const note = r && [r.agent, r.baseline].find((s) => s && !s.error && s.camera_note)?.camera_note
    const cam = note && data.cameras.find((c) => c.name === note.camera)
    return cam && { camera_id: cam.camera_id, text: r.traffic_reason }
  }, [dispatch.state.result, data.cameras])
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
      <div className="stage">
        <CityMap calls={learning.calls ?? (dispatching ? [] : calls)} now={learning.calls ? learning.now : playback.now} race={!!learning.calls} cameras={data.cameras} posts={data.posts}
          neighborhoods={data.neighborhoods} slowNbhds={data.slowNbhds} armed={dispatch.state.armed}
          onPick={dispatch.pick} result={dispatch.state.result} elapsed={dispatch.elapsed}
          camera={camera} onCamera={setCamera} jam={jam} />
        {learning.calls && <LearnChip step={learning.step} calls={learning.calls} now={learning.now} />}
      </div>
      <div className="left">
        <header className="top glass">
          <div className="brand">
            <span className="logo" aria-hidden="true" />
            <div>
              <h1>DispatchZero</h1>
              <p>
                Real SF 911 calls from a week the AI never saw
                {learned && <> · last learned {dayLabel(learned.last_learned)}</>}
              </p>
            </div>
          </div>
          <ViewToggle view={view} setView={setView} agent={data.agent} />
        </header>
        <Hero headline={data.headline} />
      </div>
      <Legend />
      <aside className="side glass">
        <DispatchPanel state={dispatch.state} onArm={dispatch.arm} onClose={dispatch.close} onReplay={dispatch.replay}
          onWatch={jam && (() => setCamera(data.cameras.find((c) => c.camera_id === jam.camera_id)))}>
          <CallFlow calls={[]} now={0} result={dispatch.state.result} elapsed={dispatch.elapsed} />
        </DispatchPanel>
        <CallFlow calls={dispatching ? [] : calls} now={playback.now} playing={playback.playing} result={null} elapsed={0} />
        <VersionLog onLearn={learning.onLearn} />
      </aside>
      <Timeline {...playback} day={day} days={days} setDay={setDay} />
    </div>
  )
}
