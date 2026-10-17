// Data comes from C's FastAPI (proxied at /api) when it is up, and from fixtures/ otherwise.
// loadFixtures() is instant so the page renders at once; loadLive() then swaps in API data
// endpoint by endpoint, keeping the fixture for any endpoint that fails.
import fxBaseline from '@fixtures/engines_baseline.json'
import fxAgent from '@fixtures/engines_agent.json'
import fxHeadline from '@fixtures/engines_headline.json'
import fxPosts from '@fixtures/posts.json'
import fxCameras from '@fixtures/cameras.json'
import cameraImages from '@fixtures/camera_images.json'
import fxSlow from '@fixtures/slow_nbhds.json'
import fxPolice from '@fixtures/police_tuesday.json'
import fxCrashes from '@fixtures/crash_hotspots.json'
import fxReports from '@fixtures/police_reports.json'
import neighborhoods from '@fixtures/neighborhoods.geojson?raw'

const SPLIT = 'frozen'
const MODE = 'engines'
const QUICK_MS = 4000
const SIM_MS = 200000 // first /results call for a version runs the simulator (api.py allows 180s)

async function api(path, ms = QUICK_MS) {
  const r = await fetch(`/api${path}`, { signal: AbortSignal.timeout(ms) })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

const withImages = (cams) => cams.map((c) => ({ ...c, image: cameraImages[c.camera_id] }))

export function loadFixtures() {
  return {
    headline: fxHeadline,
    source: 'fixtures',
    baseline: fxBaseline,
    agent: fxAgent,
    posts: fxPosts,
    slowNbhds: new Set(fxSlow.nbhds),
    neighborhoods: JSON.parse(neighborhoods),
    cameras: withImages(fxCameras),
    policeFixture: fxPolice,
    crashes: fxCrashes,
    reports: fxReports,
    liveIncidents: { fetched_at: null, police: [], chp: [] },
  }
}

async function replayOf(version) {
  const [summary, results] = await Promise.all([
    api(`/summary/${version}?split=${SPLIT}&mode=${MODE}`, SIM_MS),
    api(`/results/${version}?split=${SPLIT}&mode=${MODE}`, SIM_MS),
  ])
  return { version, summary, results }
}

function bestAgent(versions) {
  return [...versions].reverse().find((v) => v.accepted && v.version !== 'baseline')?.version
}

// Resolves to the fields the API could serve; the caller merges them over the fixtures.
export async function loadLive() {
  const live = {}
  const [posts, slow, cameras, versions, headline, crashes, incidents, reports] = await Promise.allSettled([
    api('/posts'), api('/slow'), api('/cameras'), api(`/versions?mode=${MODE}`), api(`/headline?mode=${MODE}`),
    api('/incidents/crashes'), api('/incidents/live'), api('/incidents/reports'),
  ])
  if (headline.status === 'fulfilled') live.headline = headline.value
  if (posts.status === 'fulfilled') live.posts = posts.value
  if (slow.status === 'fulfilled') live.slowNbhds = new Set(slow.value.nbhds)
  if (cameras.status === 'fulfilled') live.cameras = withImages(cameras.value)
  if (crashes.status === 'fulfilled') live.crashes = crashes.value
  if (incidents.status === 'fulfilled') live.liveIncidents = incidents.value
  if (reports.status === 'fulfilled') live.reports = reports.value

  const agentVersion = versions.status === 'fulfilled' ? bestAgent(versions.value) : null
  const [baseline, agent] = await Promise.allSettled([
    replayOf('baseline'),
    agentVersion ? replayOf(agentVersion) : Promise.reject(new Error('no accepted agent version')),
  ])
  if (baseline.status === 'fulfilled') live.baseline = baseline.value
  if (agent.status === 'fulfilled') live.agent = agent.value

  if (Object.keys(live).length) live.source = 'api'
  return live
}

// SFPD dispatched calls on one frozen-week day, and the latest live SFPD + CHP refresh. See backend/incidents.py.
export async function loadPolice(day) {
  return api(`/incidents/police?day=${day}`)
}

export async function loadLiveIncidents() {
  return api('/incidents/live')
}

export async function loadCameras() {
  return withImages(await api('/cameras'))
}

// One incident at a frozen-week moment, e.g. t = '2026-10-06T17:40:00'. See backend/dispatch.py.
export async function dispatchIncident(lat, lon, t, priority = '3') {
  const q = new URLSearchParams({ lat: lat.toFixed(5), lon: lon.toFixed(5), t, priority })
  return api(`/dispatch?${q}`, 30000)
}
