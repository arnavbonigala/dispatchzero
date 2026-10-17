import { useState } from 'react'

// One Caltrans camera, large: the frame cameras.py analysed with a box on every car it counted
// (green moving, red stopped), or the latest live snapshot from Caltrans.
export default function CameraView({ camera, onClose }) {
  const frame = camera.frame
  const analysed = Boolean(frame && camera.frameUrl)
  const [view, setView] = useState(analysed ? 'analysed' : 'live')
  const showBoxes = analysed && view === 'analysed'
  const boxes = frame?.boxes ?? []
  const stopped = boxes.filter((b) => !b[4]).length
  const src = showBoxes ? camera.frameUrl : camera.image

  return (
    <div className="cam-card glass" role="dialog" aria-label={camera.name}>
      <div className="cam-head">
        <b>{camera.name}</b>
        <div className="cam-actions">
          {analysed && camera.image && (
            <div className="toggle" role="group" aria-label="Camera view">
              <button aria-pressed={view === 'analysed'} onClick={() => setView('analysed')}>Detected cars</button>
              <button aria-pressed={view === 'live'} onClick={() => setView('live')}>Live image</button>
            </div>
          )}
          <button className="dp-link" onClick={onClose} aria-label="Close camera">Close</button>
        </div>
      </div>
      <div className="cam-view" style={showBoxes ? { aspectRatio: `${frame.w} / ${frame.h}` } : undefined}>
        {src ? <img src={src} alt={`Caltrans camera ${camera.camera_id}`} /> : <p className="cam-empty">No image from this camera right now.</p>}
        {showBoxes && (
          <svg className="cam-boxes" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
            {boxes.map(([x1, y1, x2, y2, moving], i) => (
              <rect key={i} x={x1} y={y1} width={x2 - x1} height={y2 - y1} className={moving ? 'moving' : 'stopped'} vectorEffect="non-scaling-stroke" />
            ))}
          </svg>
        )}
      </div>
      <div className="cam-foot">
        <span>
          {camera.vehicles} vehicles · {Math.round(camera.moving_frac * 100)}% moving · slowdown ×{camera.slowdown.toFixed(1)}
          {camera.ts && ` · analysed ${camera.ts.slice(11, 16)}`}
          {camera.source === 'clip' && ' · recorded clip'}
        </span>
        {showBoxes && (
          <span className="cam-legend">
            <i className="moving" /> moving <i className="stopped" /> stopped{stopped ? ` (${stopped})` : ''}
          </span>
        )}
      </div>
    </div>
  )
}
