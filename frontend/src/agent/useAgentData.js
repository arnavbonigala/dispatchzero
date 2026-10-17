import { useEffect, useState } from 'react'
import { getJSON, liveSocket } from './api'

// Run log and headline from the API, kept current over the /live WebSocket.
export function useAgentData() {
  const [versions, setVersions] = useState([])
  const [headline, setHeadline] = useState(null)
  const [source, setSource] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let closed = false
    const refreshHeadline = () => getJSON('/headline').then((h) => !closed && setHeadline(h)).catch(() => {})
    getJSON('/versions')
      .then((d) => {
        if (closed) return
        setVersions(d.versions)
        setSource(d.source)
        setError(null)
      })
      .catch((e) => !closed && setError(e.message))
    refreshHeadline()
    const live = liveSocket((msg) => {
      if (closed || msg.type !== 'versions') return
      setVersions(msg.versions)
      setSource(msg.source)
      setError(null)
      refreshHeadline()
    })
    return () => {
      closed = true
      live.close()
    }
  }, [])

  return { versions, headline, source, error }
}
