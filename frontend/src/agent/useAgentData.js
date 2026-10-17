import { useEffect, useState } from 'react'
import { getJSON, liveSocket } from './api'

// Run log and headline from the API, kept current over the /live WebSocket.
export function useAgentData() {
  const [versions, setVersions] = useState([])
  const [headline, setHeadline] = useState(null)
  const [source, setSource] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let ws
    let closed = false
    const refreshHeadline = () => getJSON('/headline').then(setHeadline).catch(() => {})
    getJSON('/versions')
      .then((d) => {
        setVersions(d.versions)
        setSource(d.source)
        setError(null)
      })
      .catch((e) => setError(e.message))
    refreshHeadline()
    try {
      ws = liveSocket((msg) => {
        if (closed || msg.type !== 'versions') return
        setVersions(msg.versions)
        setSource(msg.source)
        refreshHeadline()
      })
    } catch {
      // the REST snapshot above still renders without live updates
    }
    return () => {
      closed = true
      ws?.close()
    }
  }, [])

  return { versions, headline, source, error }
}
