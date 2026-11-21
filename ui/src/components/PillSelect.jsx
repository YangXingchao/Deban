import { useState } from 'react'
import { IcChev } from './Icons.jsx'

export default function PillSelect({ prefix, value, options, onChange }) {
  const [open, setOpen] = useState(false)
  const cur = options.find((o) => String(o.value) === String(value))
  return (
    <div className="pillselect">
      <button className="ps-btn" onClick={() => setOpen((o) => !o)}>
        {prefix}{cur ? cur.label : ''} <IcChev size={10} />
      </button>
      {open && (
        <>
          <div className="menu-backdrop" onClick={() => setOpen(false)} />
          <div className="dl-menu ps-menu" onClick={() => setOpen(false)}>
            {options.map((o) => (
              <a key={String(o.value)} className={String(o.value) === String(value) ? 'on' : ''}
                 onClick={() => onChange(o.value)}>{o.label}</a>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
