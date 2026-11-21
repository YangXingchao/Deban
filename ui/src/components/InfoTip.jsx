import { useEffect, useRef, useState } from 'react'

export default function InfoTip({ children }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  return (
    <span className="infotip" ref={ref}>
      <span className={'info-icon' + (open ? ' on' : '')} onClick={() => setOpen(!open)}>?</span>
      {open && <span className="info-pop">{children}</span>}
    </span>
  )
}
