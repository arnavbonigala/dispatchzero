// Data comes from C's FastAPI (proxied at /api) when it is up, and from fixtures/ otherwise.
// loadFixtures() is instant so the page renders at once; loadLive() then swaps in API data
// endpoint by endpoint, keeping the fixture for any endpoint that fails.
import fxResults from '@fixtures/results.json'
import fxSummary from '@fixtures/summary.json'
import fxPosts from '@fixtures/posts.json'
import fxCameras from '@fixtures/cameras.json'
import cameraImages from '@fixtures/camera_images.json'
import fxSlow from '@fixtures/slow_nbhds.json'
import neighborhoods from '@fixtures/neighborhoods.geojson?raw'

const SPLIT = 'frozen'
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
    source: 'fixtures',
    baseline: { summary: fxSummary, results: fxResults },
    agent: null,
    posts: fxPosts,
    slowNbhds: new Set(fxSlow.nbhds),
    neighborhoods: JSON.parse(neighborhoods),
    cameras: withImages(fxCameras),
  }
}

async function replayOf(version) {
  const [summary, results] = await Promise.all([
    api(`/summary/${version}?split=${SPLIT}`, SIM_MS),
    api(`/results/${version}?split=${SPLIT}`, SIM_MS),
  ])
  return { version, summary, results }
}

function bestAgent(versions) {
  return [...versions].reverse().find((v) => v.accepted && v.version !== 'baseline')?.version
}

// Resolves to the fields the API could serve; the caller merges them over the fixtures.
export async function loadLive() {
  const live = {}
  const [posts, slow, cameras, versions] = await Promise.allSettled([
    api('/posts'), api('/slow'), api('/cameras'), api('/versions'),
  ])
  if (posts.status === 'fulfilled') live.posts = posts.value
  if (slow.status === 'fulfilled') live.slowNbhds = new Set(slow.value.nbhds)
  if (cameras.status === 'fulfilled') live.cameras = withImages(cameras.value)

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

export async function loadCameras() {
  return withImages(await api('/cameras'))
}
