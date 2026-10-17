import DiagnosisFeed from './DiagnosisFeed'
import VersionChart from './VersionChart'
import { fmtTime } from './api'
import { useAgentData } from './useAgentData'
import './agent.css'

function Headline({ headline }) {
  if (!headline?.agent_median_s) return null
  const ci = headline.vs_baseline?.ci95
  const saved = headline.baseline_median_s - headline.agent_median_s
  return (
    <div className="gm-headline">
      <div className="gm-counter">
        <span className="gm-muted">Baseline, frozen week</span>
        <b>{fmtTime(headline.baseline_median_s)}</b>
      </div>
      <div className="gm-counter gm-counter-agent">
        <span className="gm-muted">Golden Minute {headline.version}</span>
        <b>{fmtTime(headline.agent_median_s)}</b>
      </div>
      <p className="gm-headline-note">
        {Math.round(saved)}s faster at the median on {headline.n} calls the agent never saw
        {ci && ` (95% CI ${Math.round(-ci[1])} to ${Math.round(-ci[0])}s)`}.
        {headline.calibration_error_pct != null && ` Simulator calibrated to SF's real median within ${Math.abs(headline.calibration_error_pct)}%.`}
      </p>
    </div>
  )
}

export default function AgentPanel() {
  const { versions, headline, source, error } = useAgentData()
  return (
    <section className="gm-agent">
      <header className="gm-agent-head">
        <h2>The playbook gets better</h2>
        {source === 'fixtures' && <span className="gm-chip">sample data</span>}
      </header>
      {error && <p className="gm-error">Can't reach the API ({error}). Start it with `uvicorn api:app --port 8000` in backend/.</p>}
      <Headline headline={headline} />
      <VersionChart versions={versions} />
      <h3>What the agent changed</h3>
      <DiagnosisFeed versions={versions} />
    </section>
  )
}
