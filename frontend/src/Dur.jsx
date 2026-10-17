import { mmss } from './format'

export default function Dur({ s }) {
  return mmss(s).split(/(?<=[ms])/).map((part, i) => {
    const m = part.trim().match(/^(\d+)([ms])$/)
    return m ? <span key={i} className="dur">{m[1]}<span className="dur-u">{m[2]}</span></span> : part
  })
}
