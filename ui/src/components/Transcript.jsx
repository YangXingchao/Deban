import { useEffect, useRef } from 'react'
import SpeakerChip from './SpeakerChip.jsx'
import { T } from '../i18n.js'

export default function Transcript({ utterances, onSeek, onEdit, onReassign, onSplit, onDelete, activeUid }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    const id = requestAnimationFrame(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight })
    return () => cancelAnimationFrame(id)
  }, [])
  useEffect(() => {
    const el = ref.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) {
      el.scrollTop = el.scrollHeight
    }
  }, [utterances])

  if (!utterances.length) return <div className="scroll" ref={ref}><div className="empty">{T('waiting')}</div></div>
  return (
    <div className="scroll" ref={ref}>
      {utterances.map((u) => (
        <div className={'cap' + (activeUid === u.uid ? ' active' : '')} id={`utt-${u.uid}`} key={u.uid}>
          <div className="meta">
            <span className="ts" style={onSeek ? { cursor: 'pointer' } : {}}
                  onClick={() => onSeek && onSeek(u.off)}>{u.ts}</span>
            <SpeakerChip spk={u.spk} name={u.name} named={u.named} />
            {u.lang ? <span className="lang-tag">{u.lang}</span> : null}
            {u.marker ? <span>{u.marker}</span> : null}
            {onReassign ? <span className="edit-btn" onClick={() => onReassign(u)}>⇄ {T('reassign')}</span> : null}
            {onSplit ? <span className="edit-btn" onClick={() => onSplit(u)}>✂ {T('split')}</span> : null}
            {onEdit ? <span className="edit-btn" onClick={() => onEdit(u)}>✎ {T('edit')}</span> : null}
            {onDelete ? <span className="edit-btn danger" onClick={() => onDelete(u)}>✕ {T('del')}</span> : null}
          </div>
          <div className={'jp' + (u.revised ? ' revised' : '')}>{u.text}</div>
          {u.zh === '' && u.pendingZh
            ? <div className="zh pending">{T('translating')}</div>
            : (u.zh ? <div className="zh">{u.zh}</div> : null)}
        </div>
      ))}
    </div>
  )
}
