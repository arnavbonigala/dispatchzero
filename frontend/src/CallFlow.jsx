import { useEffect, useRef, useState } from 'react'
import { TARGET_S, mmss } from './format'
import './CallFlow.css'

const WALK_SPEED = 120
const HOLD_S = 240
const INTRO_MS = 450
const RECENT_S = 1800

const clamp = (x) => Math.max(0, Math.min(1, x))

function stage(key, label, detail, at, from, to = at, ok = true) {
  return { key, label, detail, at, from, to, ok }
}

function replayStages(c) {
  const ambOut = c.ambulance_s - c.eta_s
  const engOut = c.engine_unit ? c.engine_s - c.engine_eta_s : null
  const out = Math.min(ambOut, engOut ?? ambOut)
  const firstBy = c.engine_unit && c.engine_s <= c.ambulance_s ? `Engine ${c.engine_unit}` : `Ambulance ${c.unit}`
  const vsTarget = c.response_s <= TARGET_S ? 'inside' : 'over'
  return [
    stage('call', '911 call received', `${c.nbhd}${c.life_threatening ? ' · life-threatening' : ''}`, 0, 0),
    stage('decide', 'Units dispatched', `Golden Minute sent ${c.unit} from post ${c.from_post}${c.engine_unit ? ` + engine ${c.engine_unit}` : ', no engine'}`, out, 0, out),
    c.engine_unit && stage('engine', `Engine ${c.engine_unit} en route`, `${mmss(c.engine_eta_s)} drive`, c.engine_s, engOut, c.engine_s),
    stage('amb', `Ambulance ${c.unit} en route`, `${mmss(c.eta_s)} drive`, c.ambulance_s, ambOut, c.ambulance_s),
    stage('first', 'First paramedic on scene', `${firstBy} · ${mmss(c.response_s)}, ${vsTarget} the 8:00 target`, c.response_s, c.response_s, c.response_s, c.response_s <= TARGET_S),
  ].filter(Boolean).sort((a, b) => a.at - b.at)
}

function dispatchStages(r) {
  const s = r.agent
  const base = r.baseline && !r.baseline.error ? r.baseline.first.eta_s : null
  const saved = base != null ? base - s.first.eta_s : null
  const top = s.candidates.slice(0, 3).map((c) => `${c.unit_id} ${mmss(c.eta_s)}`).join(' · ')
  const hosp = r.hospital
  return [
    stage('call', 'Incident reported', r.incident.nbhd, 0, 0),
    stage('scan', 'Scan the fleet', `${s.n_idle} ambulances and ${s.engine_idle_ids?.length ?? 0} engines free`, 0, 0),
    stage('rank', 'Rank by drive time', top, 0, 0),
    stage('decide', 'Send units', `${s.chosen.unit_id} from post ${s.chosen.post}${s.engine ? ` + engine ${s.engine.unit_id}` : ', no engine'}`, 0, 0),
    s.engine && stage('engine', `Engine ${s.engine.unit_id} en route`, `${mmss(s.engine.eta_s)} drive`, s.engine.eta_s, 0, s.engine.eta_s),
    stage('amb', `Ambulance ${s.chosen.unit_id} en route`, `${mmss(s.chosen.eta_s)} drive`, s.chosen.eta_s, 0, s.chosen.eta_s),
    stage('first', 'First paramedic on scene', `${s.first.kind[0].toUpperCase()}${s.first.kind.slice(1)} ${s.first.unit_id} · ${mmss(s.first.eta_s)}${saved > 0 ? `, ${mmss(saved)} sooner than baseline` : ''}`, s.first.eta_s, s.first.eta_s),
    hosp && stage('hosp', `To ${hosp.name}`, `${mmss(hosp.drive_s)} drive + ~${mmss(hosp.offload_s)} handoff`, s.chosen.eta_s + hosp.drive_s, s.chosen.eta_s, s.chosen.eta_s + hosp.drive_s),
  ].filter(Boolean).sort((a, b) => a.at - b.at)
}

function useWalk(calls, now, playing) {
  const [call, setCall] = useState(null)
  const [t, setT] = useState(0)
  const live = useRef({ calls, now, playing })
  live.current = { calls, now, playing }

  useEffect(() => {
    let raf
    let last = performance.now()
    let cur = null
    let rel = 0
    const pick = () => {
      const { calls: cs, now: n, playing: on } = live.current
      if (!on) return
      const started = cs.filter((c) => c.start <= n && c.start > n - RECENT_S)
      const next = started.filter((c) => !cur || c.start > cur.start).at(-1) ?? cur
      if (next && next !== cur) {
        cur = next
        rel = 0
        setCall(next)
      }
    }
    const tick = (ts) => {
      const dt = (ts - last) / 1000
      last = ts
      if (!cur || !live.current.calls.includes(cur)) {
        cur = null
        pick()
      } else {
        rel += dt * WALK_SPEED
        if (rel > Math.max(cur.ambulance_s, cur.response_s) + HOLD_S) pick()
        setT(rel)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  return { call: call && calls.includes(call) ? call : null, t }
}

function useIntro(result) {
  const [n, setN] = useState(0)
  useEffect(() => {
    setN(0)
    if (!result) return
    const id = setInterval(() => setN((k) => k + 1), INTRO_MS)
    return () => clearInterval(id)
  }, [result])
  return n
}

function Flow({ title, stages, t, revealed = Infinity }) {
  const done = (s, i) => i < revealed && t >= s.at
  const activeIdx = stages.findIndex((s, i) => !done(s, i))
  return (
    <ol className="cf-list">
      {stages.map((s, i) => {
        const state = done(s, i) ? 'done' : i === activeIdx ? 'active' : 'todo'
        const travel = s.to > s.from ? clamp((t - s.from) / (s.to - s.from)) : null
        return (
          <li key={`${title}-${s.key}`} className={`cf-step cf-${state} cf-k-${s.key} ${s.ok ? 'cf-ok' : 'cf-late'}`} aria-current={state === 'active' ? 'step' : undefined}>
            <span className="cf-node" aria-hidden="true" />
            <div className="cf-body">
              <div className="cf-row">
                <span className="cf-label">{s.label}</span>
                {state === 'done' && s.at > 0 && <span className="cf-time">+{mmss(s.at)}</span>}
              </div>
              {state !== 'todo' && <span className="cf-detail">{s.detail}</span>}
              {travel != null && state !== 'todo' && (
                <span className="cf-bar" aria-hidden="true"><span style={{ transform: `scaleX(${travel})` }} /></span>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export default function CallFlow({ calls, now, playing, result, elapsed }) {
  const walk = useWalk(calls, now, playing)
  const intro = useIntro(result)
  const live = result?.agent && !result.agent.error

  if (live) {
    return (
      <section className="cf cf-live" aria-labelledby="cf-title">
        <h2 id="cf-title">Your incident, step by step</h2>
        <Flow title="live" stages={dispatchStages(result)} t={intro >= 4 ? elapsed : 0} revealed={intro} />
      </section>
    )
  }
  if (!walk.call) return null
  return (
    <section className="cf" aria-labelledby="cf-title">
      <div className="cf-top">
        <h2 id="cf-title">Following a call</h2>
        <span className="cf-id">#{walk.call.call_id}</span>
      </div>
      <Flow title={walk.call.call_id} stages={replayStages(walk.call)} t={walk.t} />
    </section>
  )
}
