import { T } from '../i18n.js'

const STARS = { 2: '★★★', 1: '★★☆', 0: '★☆☆' }
export default function PrioBadge({ value, onChange }) {
  const v = value === 0 ? 0 : (value === 2 ? 2 : 1)
  return (
    <span className={'prio-badge p' + v} title={T('prio' + v)}
          onClick={(e) => { e.stopPropagation(); onChange((v + 1) % 3) }}>
      {STARS[v]}
    </span>
  )
}
