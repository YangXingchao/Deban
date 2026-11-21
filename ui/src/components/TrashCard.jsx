import { useEffect, useState } from 'react'
import { T } from '../i18n.js'
import { api, post, del } from '../api.js'
import { modal } from './Modal.jsx'
import { IcTrash, IcUsers, IcDoc, IcHistory } from './Icons.jsx'

const KINDS = [
  ['meeting', 'history', IcHistory],
  ['speaker', 'people', IcUsers],
  ['context', 'library', IcDoc],
]

export default function TrashCard() {
  const [items, setItems] = useState([])
  const [tab, setTab] = useState('meeting')
  const reload = () => api('/trash').then((l) => setItems(l || []))
  useEffect(() => { reload() }, [])

  const restore = async (it) => {
    const r = await post(`/trash/${it.id}/restore`)
    if (r && r.ok) reload()
  }
  const purgeAll = async () => {
    const n = items.filter((it) => it.kind === tab).length
    if (!n) return
    if (!await modal.confirm({ title: T('trashPurgeAll'), message: T('trashPurgeAllConfirm', n), danger: true, okText: T('trashPurgeAll') })) return
    await del(`/trash?kind=${tab}`)
    reload()
  }
  const purge = async (it) => {
    if (!await modal.confirm({ title: T('trashPurge'), message: T('trashPurgeConfirm', it.label), danger: true })) return
    await del(`/trash/${it.id}`)
    reload()
  }

  const group = items.filter((it) => it.kind === tab)
  return (
    <div className="card set-card">
      <h3><IcTrash size={15} /> {T('trash')}</h3>
      <div className="hint">{T('trashHint')}</div>
      <div className="subtabs trash-tabs">
        {KINDS.map(([kind, navKey, Ic]) => {
          const n = items.filter((it) => it.kind === kind).length
          return (
            <button key={kind} className={'subtab' + (tab === kind ? ' on' : '')} onClick={() => setTab(kind)}>
              <Ic size={13} /> {T(navKey)}{n > 0 && <span className="tg-n">{n}</span>}
            </button>
          )
        })}
      </div>
      {group.length > 0 && (
        <div className="trash-bulk">
          <span className="hint" style={{ margin: 0 }}>{T('trashCount', group.length)}</span>
          <button className="ghost tr-del" onClick={purgeAll}>{T('trashPurgeAll')}</button>
        </div>
      )}
      {group.length === 0 && <div className="empty" style={{ padding: '14px 0' }}>{T('trashEmpty')}</div>}
      {group.map((it) => (
        <div key={it.id} className="trash-row">
          <span className="tr-label">{it.label}</span>
          <span className="tr-meta">
            {new Date(it.deleted_at * 1000).toLocaleString()} · {T('trashDaysLeft', Math.ceil(it.days_left))}
          </span>
          <button className="ghost" onClick={() => restore(it)}>{T('trashRestore')}</button>
          <button className="ghost tr-del" onClick={() => purge(it)}>{T('trashPurge')}</button>
        </div>
      ))}
    </div>
  )
}
