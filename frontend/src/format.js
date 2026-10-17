export const TARGET_S = 8 * 60

export const C = {
  good: '#6cc08b',
  warn: '#d9a54a',
  bad: '#e0604f',
  slow: '#9d93d6',
  unit: '#7aa7e8',
  engine: '#e08a5a',
  hospital: '#d97fa6',
  post: '#8b9098',
  line: '#8b9098',
  ink: '#ecebe7',
  surface: '#16191e',
  gold: '#e3b04b',
  police: '#5fb6c9',
}

export function rgb(hex, a = 255) {
  const n = parseInt(hex.slice(1), 16)
  return [n >> 16, (n >> 8) & 255, n & 255, a]
}

// "2026-10-06T00:04:17" -> seconds since midnight, read from the string so the browser time zone never shifts it
export function secOfDay(t) {
  const [h, m, s] = t.slice(11, 19).split(':').map(Number)
  return h * 3600 + m * 60 + s
}

export function clock(sec) {
  const h = Math.floor(sec / 3600) % 24
  const m = Math.floor((sec % 3600) / 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

export function mmss(sec) {
  if (sec == null || Number.isNaN(sec)) return '—'
  const s = Math.round(sec)
  const m = Math.floor(s / 60)
  return m ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

export function median(xs) {
  if (!xs.length) return null
  const a = [...xs].sort((x, y) => x - y)
  const mid = a.length >> 1
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2
}

export function responseColor(sec) {
  if (sec <= TARGET_S) return C.good
  if (sec <= 12 * 60) return C.warn
  return C.bad
}

export function slowdownColor(f) {
  if (f < 1.3) return C.good
  if (f < 2) return C.warn
  return C.bad
}
