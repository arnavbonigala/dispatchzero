export const TARGET_S = 8 * 60

// Map colors live here, not in CSS: Leaflet writes them into SVG attributes where var() is not reliable
export const C = {
  good: '#1a9e6f',
  warn: '#e09b1b',
  bad: '#d6453d',
  slow: '#6d4fd8',
  unit: '#1f5fd1',
  post: '#55606e',
  line: '#9aa3ad',
  ink: '#1c2430',
  surface: '#ffffff',
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
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
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
