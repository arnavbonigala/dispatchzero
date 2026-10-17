import { useEffect, useMemo, useRef, useState } from 'react'

function useHls(ref, src) {
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const video = ref.current
    if (!video || !src) return
    setFailed(false)
    const play = () => video.play().catch(() => {})
    let hls
    let alive = true
    import('hls.js').then(({ default: Hls }) => {
      if (!alive) return
      if (Hls.isSupported()) {
        hls = new Hls()
        hls.on(Hls.Events.ERROR, (_, d) => d.fatal && setFailed(true))
        hls.on(Hls.Events.MANIFEST_PARSED, play)
        hls.loadSource(src)
        hls.attachMedia(video)
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = src
        video.onerror = () => setFailed(true)
        play()
      } else setFailed(true)
    })
    return () => {
      alive = false
      hls?.destroy()
      video.removeAttribute('src')
    }
  }, [ref, src])
  return failed
}

const FIXTURE_FRAMES = Object.fromEntries(
  Object.entries(import.meta.glob('../../fixtures/camera_frames/*.jpg', { query: '?url', import: 'default', eager: true }))
    .map(([path, url]) => [path.split('/').pop().replace('.jpg', ''), url]),
)

function Vision({ camera }) {
  const sources = useMemo(
    () => [`/api/camera_frame/${camera.camera_id}?t=${encodeURIComponent(camera.ts ?? '')}`, FIXTURE_FRAMES[camera.camera_id]].filter(Boolean),
    [camera.camera_id, camera.ts],
  )
  const [i, setI] = useState(0)
  useEffect(() => setI(0), [sources])
  if (i >= sources.length) return null
  return (
    <figure className="cam-vision">
      <img src={sources[i]} onError={() => setI(i + 1)}
        alt={`YOLOv8 vehicle tracking on camera ${camera.camera_id}: ${camera.vehicles} vehicles, ${Math.round(camera.moving_frac * 100)}% moving`} />
      <figcaption>Computer vision · YOLOv8 vehicle tracking{camera.ts && ` · read ${camera.ts.slice(11, 16)}`}</figcaption>
    </figure>
  )
}

export default function CameraCard({ camera, note, onClose }) {
  const ref = useRef(null)
  const failed = useHls(ref, camera.stream)
  const live = camera.stream && !failed
  return (
    <div className="cam-card glass" role="dialog" aria-label={camera.name}>
      <div className="cam-head">
        <b>{camera.name.split('--').pop().trim()}</b>
        <button className="dp-link" onClick={onClose}>Close</button>
      </div>
      <div className="cam-media">
        {live ? (
          <video ref={ref} autoPlay muted playsInline poster={camera.image}
            aria-label={`Live Caltrans video, camera ${camera.camera_id}`} />
        ) : (
          camera.image && <img src={camera.image} alt={`Caltrans camera ${camera.camera_id}`} />
        )}
        {live && <span className="cam-live">Live</span>}
      </div>
      <Vision camera={camera} />
      {note && <p className="cam-note">{note}</p>}
      <span className="cam-stats">
        {camera.vehicles} vehicles · {Math.round(camera.moving_frac * 100)}% moving · travel time ×{camera.slowdown.toFixed(1)}
      </span>
    </div>
  )
}
