// Fixture loaders. Each returns the same shape C's api.py will serve, so swapping to
// fetch('/api/...') later only touches this file.
import results from '@fixtures/results.json'
import summary from '@fixtures/summary.json'
import posts from '@fixtures/posts.json'
import cameras from '@fixtures/cameras.json'
import cameraImages from '@fixtures/camera_images.json'
import slow from '@fixtures/slow_nbhds.json'
import neighborhoods from '@fixtures/neighborhoods.geojson?raw'

export async function loadReplay() {
  return {
    baseline: { summary, results },
    agent: null, // no agent replay fixture yet
  }
}

export async function loadMap() {
  return {
    posts,
    neighborhoods: JSON.parse(neighborhoods),
    slowNbhds: new Set(slow.nbhds),
  }
}

export async function loadCameras() {
  return cameras.map((c) => ({ ...c, image: cameraImages[c.camera_id] }))
}
