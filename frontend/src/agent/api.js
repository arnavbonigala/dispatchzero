export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export async function getJSON(path) {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

// Opens /live and reconnects with backoff until close() is called.
export function liveSocket(onMessage) {
  let ws
  let delay = 1000
  let timer
  let stopped = false
  const open = () => {
    ws = new WebSocket(API.replace(/^http/, 'ws') + '/live')
    ws.onopen = () => {
      delay = 1000
    }
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data))
      } catch {
        // ignore a malformed frame and keep listening
      }
    }
    ws.onclose = () => {
      if (stopped) return
      timer = setTimeout(open, delay)
      delay = Math.min(delay * 2, 30000)
    }
  }
  open()
  return {
    close() {
      stopped = true
      clearTimeout(timer)
      ws?.close()
    },
  }
}

export const fmtTime = (s) => {
  if (s == null || Number.isNaN(s)) return '–'
  const t = Math.round(Math.abs(s))
  return `${s < 0 ? '−' : ''}${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`
}

// Slow-neighborhood medians are the goal; older run logs and fixtures only have citywide medians.
export const goalMedian = (v, split) => v[`${split}_slow_median`] ?? v[`${split}_median`] ?? null
