export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export async function getJSON(path) {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

export function liveSocket(onMessage) {
  const ws = new WebSocket(API.replace(/^http/, 'ws') + '/live')
  ws.onmessage = (e) => onMessage(JSON.parse(e.data))
  return ws
}

export const fmtTime = (s) => {
  if (s == null) return '–'
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.round(s % 60)).padStart(2, '0')}`
}
