import { useEffect, useState } from 'react'
import { T } from '../i18n.js'
import { IcX } from './Icons.jsx'
import { api, post } from '../api.js'
import { modal } from './Modal.jsx'
import { memberTip } from './splitFlow.js'

export default function SpeakerSheet({ open, spk, name, named, meetingId, mode, suggest, count,
                                       onClose, onRenamed, onChanged }) {
  const [people, setPeople] = useState([])
  const [parts, setParts] = useState([])
  const [anons, setAnons] = useState([])
  const [newName, setNewName] = useState('')
  const [more, setMore] = useState(true)
  const [busy, setBusy] = useState(false)
  const live = mode === 'live'
  const [q, setQ] = useState('')
  useEffect(() => { setQ('') }, [spk, open])

  useEffect(() => {
    if (!open) return
    setNewName(''); setMore(true); setBusy(false)
    api('/speakers').then((l) => setPeople(l || []))
    if (meetingId) {
      api(`/meeting/${meetingId}/participants`).then((l) => setParts(l || []))
      api(`/meeting/${meetingId}/roster`).then((r) => setAnons((r && r.anons) || []))
    }
  }, [open, spk])
  if (!open) return null

  const done = () => { onChanged && onChanged(); onClose() }
  const run = async (fn) => { if (busy) return; setBusy(true); try { await fn() } finally { setBusy(false) } }
  const partIds = new Set(parts.map((p) => p.id))
  const members = people.filter((m) => !q.trim() || m.name.toLowerCase().includes(q.trim().toLowerCase()))
    .filter((p) => p.id !== spk && p.name && !(suggest && p.id === suggest.member))
    .sort((a, b) => (partIds.has(b.id) - partIds.has(a.id)) || ((b.has_voice ? 1 : 0) - (a.has_voice ? 1 : 0)))
  const otherAnons = anons.filter((a) => a.id !== spk)
  const hint = (p) => partIds.has(p.id) && !p.has_voice ? T('preRegistered') : (p.has_voice ? '●' : '◌')

  const bindTo = (m) => run(async () => {
    if (!await modal.confirm({ title: T('sheetTitle'), message: T('cfBind', name, m.name, count, live) })) return
    if (live) {
      const r = await post(`/speaker/${m.id}/bind`, { from_spk: spk })
      if (suggest) post('/meeting/spk_suggest', { spk, member: suggest.member, accept: false })
      if (r && r.ok) onRenamed && onRenamed(spk, r.name || m.name)
    } else {
      await post(`/meeting/${meetingId}/speaker_edit`, { action: 'bind', speaker_id: spk, target_id: m.id })
    }
    done()
  })
  const acceptSuggest = () => run(async () => { await post('/meeting/spk_suggest', { spk, member: suggest.member, accept: true }); done() })
  const rejectSuggest = () => run(async () => { await post('/meeting/spk_suggest', { spk, member: suggest.member, accept: false }); done() })
  const register = () => run(async () => {
    const n = newName.trim()
    if (!n) return
    if (named) {
      const m = await post('/speakers', { name: n })
      if (!m || !m.id || m.id === spk) return
      if (live) await post('/meeting/reassign_speaker', { from_spk: spk, to_spk: m.id })
      else await post(`/meeting/${meetingId}/speaker_edit`, { action: 'merge', speaker_id: spk, target_id: m.id })
      done(); return
    }
    await post(`/speaker/${spk}/rename`, { name: n })
    if (suggest) post('/meeting/spk_suggest', { spk, member: suggest.member, accept: false })
    onRenamed && onRenamed(spk, n)
    done()
  })
  const changeMember = (m) => run(async () => {
    if (!await modal.confirm({ title: T('sheetTitle'), message: T('reassignAllConfirm', name, m.name, count) })) return
    if (live) await post('/meeting/reassign_speaker', { from_spk: spk, to_spk: m.id })
    else await post(`/meeting/${meetingId}/speaker_edit`, { action: 'merge', speaker_id: spk, target_id: m.id })
    done()
  })
  const mergeInto = (a) => run(async () => {
    if (!await modal.confirm({ title: T('sheetTitle'), message: T('cfMerge', name, a.name, count) })) return
    if (live) await post('/meeting/reassign_speaker', { from_spk: spk, to_spk: a.id })
    else await post(`/meeting/${meetingId}/speaker_edit`, { action: 'merge', speaker_id: spk, target_id: a.id })
    done()
  })
  const delAll = () => run(async () => {
    if (!await modal.confirm({ title: T('del'), message: T('spkDelUttsConfirm', name, count), danger: true })) return
    await post(`/meeting/${meetingId}/speaker_edit`, { action: 'delete_utts', speaker_id: spk })
    done()
  })
  const removeOnly = () => run(async () => { await post(`/meeting/${meetingId}/roster`, { action: 'remove', speaker_id: spk }); done() })
  const removeAnon = () => run(async () => {
    if (live) await post(`/meeting/${meetingId}/roster`, { action: 'remove', speaker_id: spk })
    else await post(`/meeting/${meetingId}/speaker_edit`, { action: 'delete_utts', speaker_id: spk })
    done()
  })

  const Row = ({ label, hint, onClick, danger, title }) => (
    <div className={'m-choice' + (danger ? ' danger' : '')} title={title} onClick={onClick}>
      <span className="l">{label}</span>{hint ? <span className="h">{hint}</span> : null}
    </div>
  )

  return (
    <>
      <div className="drawer-backdrop" style={{ zIndex: 120 }} onClick={onClose} />
      <div className="drawer spk-sheet" style={{ zIndex: 121 }} onKeyDown={(e) => { if (e.key === 'Escape') onClose() }}>
        <div className="drawer-head">{T('sheetTitle')}<button className="drawer-x" onClick={onClose} aria-label={T('close')}><IcX size={15} /></button></div>
        <div className="ss-who"><span className="nm">{name}</span><span className="cnt">{T('sheetCount', count)}</span></div>
        <div className="m-body">
          {!named && suggest && (
            <div className="ss-sec suggest">
              <div className="ss-h">{T('secSuggest')}</div>
              <div className="ss-sug">
                <span className="nm">{suggest.name}</span>
                <button className="primary" disabled={busy} onClick={acceptSuggest}>{T('sugYes')}</button>
                <button className="ghost" disabled={busy} onClick={rejectSuggest}>{T('sugNo')}</button>
              </div>
            </div>
          )}
          <div className="ss-sec members">
            <div className="ss-h">{named ? T('secChangeMember') : T('secPick')}</div>
            {people.length > 5 && (
              <input type="text" className="ss-filter" value={q} placeholder={T('searchPeoplePh')} autoFocus
                     onChange={(e) => setQ(e.target.value)} />
            )}
            <div className="ss-list">
              {members.length === 0 && <div className="empty" style={{ padding: '8px 0' }}>{T('noMembers')}</div>}
              {members.map((m) => (
                <Row key={m.id} label={m.name} hint={hint(m)} title={memberTip(m)} onClick={() => (named ? changeMember(m) : bindTo(m))} />
              ))}
              {otherAnons.length > 0 && (
                <div className="ss-more">
                  <div className="ss-h more" onClick={() => setMore((v) => !v)}>{more ? '▾' : '▸'} {T('secOther')}</div>
                  {more && otherAnons.map((a) => (
                    <Row key={a.id} label={T('mergeIntoX', a.name)} onClick={() => mergeInto(a)} />
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="drawer-foot pick-foot ss-other">
            <div className="ss-new">
              <input type="text" value={newName} placeholder={named ? T('secNewChange') : T('secNew')} maxLength={30}
                     onChange={(e) => setNewName(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) register() }} />
              <button className="primary" disabled={!newName.trim() || busy} onClick={register}>{T('registerBtn')}</button>
            </div>
            {count === 0 ? (
              <Row label={T('removeAnonBtn')} hint={T('noUttsHint')} danger onClick={named ? removeOnly : removeAnon} />
            ) : (!live && <Row label={T('delAllUtts')} danger onClick={delAll} />)}
          </div>
        </div>
      </div>
    </>
  )
}
