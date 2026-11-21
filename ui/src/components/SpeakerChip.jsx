import { T } from '../i18n.js'

const COLORS = ['#0f766e','#b45309','#6d28d9','#be185d','#1d4ed8','#15803d',
                '#a21caf','#b91c1c','#0e7490','#4d7c0f','#7c3aed','#c2410c']

export function spkColor(id) {
  return id < 0 ? '#64748b' : COLORS[id % COLORS.length]
}

export default function SpeakerChip({ spk, name, named }) {
  const label = spk < 0 ? (name || T('me')) : (named && name ? name : name || `#${spk}`)
  if (spk < 0) return <span className="spk static me">{label}</span>
  return <span className="spk static" style={{ background: spkColor(spk) }}>{label}</span>
}
