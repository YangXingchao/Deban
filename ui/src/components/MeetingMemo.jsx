import { useEffect, useRef, useState } from 'react'
import { api, post } from '../api.js'
import { T } from '../i18n.js'
import { IcChev } from './Icons.jsx'

export default function MeetingMemo({ meetingId, compact, height, onResize }) {
  const [text, setText] = useState('')
  const [saved, setSaved] = useState(true)
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem('mc_memo_open') !== '0' } catch { return true }
  })
  const toggle = () => setOpen((o) => {
    try { localStorage.setItem('mc_memo_open', o ? '0' : '1') } catch {}
    return !o
  })
  const timer = useRef(null)
  const cur = useRef('')

  useEffect(() => {
    let on = true
    clearTimeout(timer.current)
    setText(''); setSaved(true); cur.current = ''
    if (meetingId) api(`/meeting/${meetingId}/memo`).then((r) => {
      if (on && r) { setText(r.memo || ''); cur.current = r.memo || '' }
    })
    return () => { on = false; clearTimeout(timer.current) }
  }, [meetingId])

  const save = async (v) => {
    await post(`/meeting/${meetingId}/memo`, { memo: v })
    setSaved(true)
  }
  const change = (v) => {
    setText(v); cur.current = v; setSaved(false)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => save(v), 700)
  }

  const fill = compact && open && height > 0
  const box = (
    <textarea value={text} placeholder={T('memoPh')} rows={compact ? 3 : 4} maxLength={5000}
              onChange={(e) => change(e.target.value)}
              onBlur={() => { if (!saved) { clearTimeout(timer.current); save(cur.current) } }} />
  )
  if (!compact) return <div className="meeting-memo">{box}</div>
  return (
    <div className={'meeting-memo compact' + (fill ? ' fill' : '')} style={fill ? { height } : undefined}>
      {fill && onResize ? <div className="row-resizer" onMouseDown={onResize} title="" /> : null}
      <div className={'mm-head' + (open ? ' open' : '')} onClick={toggle}>
        <span className="mm-chev"><IcChev size={13} /></span>
        📝 {T('memo')}
        {(!open && text.trim()) || !saved ? <span className="mm-dot">•</span> : null}
      </div>
      <div className={'mm-wrap' + (open ? ' open' : '')}>
        <div className="mm-inner">{box}</div>
      </div>
    </div>
  )
}
