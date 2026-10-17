import DiagnosisFeed from './DiagnosisFeed'
import VersionChart from './VersionChart'
import { fmtTime } from './api'
import { useAgentData } from './useAgentData'
import './agent.css'

const secs = (x) => `${Math.abs(Math.round(x))}s`

function Headline({ headline }) {
  if (!headline?.version || headline.version === 'baseline' || headline.agent_median_s == null) return null
  const slow = headline.agent_slow_median_s != null
  const base = slow ? headline.baseline_slow_median_s : headline.baseline_median_s
  const agent = slow ? headline.agent_slow_median_s : headline.agent_median_s
  const ci = (slow ? headline.vs_baseline?.slow_nbhds : headline.vs_baseline)?.ci95
  const saved = Math.round(base - agent)
  const city = Math.round(headline.agent_median_s - headline.baseline_median_s)
  const scope = slow ? `in the 10 slowest neighborhoods (${headline.slow_n} calls)` : `on ${headline.n} calls`
  return (
    <div className="gm-headline">
      <div className="gm-counter">
        <span className="gm-muted">Baseline{slow ? ', slowest neighborhoods' : ''}</span>
        <b>{fmtTime(base)}</b>
      </div>
      <div className="gm-counter gm-counter-agent">
        <span className="gm-muted">Golden Minute {headline.version}</span>
        <b>{fmtTime(agent)}</b>
      </div>
      <p className="gm-headline-note">
        {saved > 0 ? `${secs(saved)} faster` : saved < 0 ? `${secs(saved)} slower` : 'No change'} at the median {scope} in a week the agent never saw
        {ci && ` (95% CI ${Math.round(-ci[1])} to ${Math.round(-ci[0])}s saved)`}.
        {slow && ` Citywide median ${city > 0 ? `${secs(city)} slower` : city < 0 ? `${secs(city)} faster` : 'unchanged'}.`}
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
        <span className="gm-muted">slowest-neighborhood median per version</span>
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
