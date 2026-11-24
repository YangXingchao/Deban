import { useEffect, useRef, useState } from 'react'
import { isImeKey } from '../keyboard.js'
import { T } from '../i18n.js'
import { api, post, del } from '../api.js'
import { modal } from '../components/Modal.jsx'
import { splitFlow, deleteUtt, memberTip } from '../components/splitFlow.js'
import Transcript from '../components/Transcript.jsx'
import TopicList from '../components/TopicList.jsx'
import { useCardLang, useTranslateEnabled } from '../components/contentLang.jsx'
import { pick } from '../components/InterventionCard.jsx'
import MeetingMemo from '../components/MeetingMemo.jsx'
import PrioBadge from '../components/PrioBadge.jsx'
import SpeakerSheet from '../components/SpeakerSheet.jsx'
import Home from './Home.jsx'
import { IcStop, IcUsers, IcPanelR, IcMic, IcMsg, IcX } from '../components/Icons.jsx'

export function nextAnonNo(anons) {
  const nums = (anons || []).map((a) => parseInt((a.name || '').replace(/\D/g, ''), 10)).filter((n) => !isNaN(n))
  return (nums.length ? Math.max(...nums) : 0) + 1
}

function fmt(sec) {
  const s = Math.max(0, Math.floor(sec))
  return `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const fmtOff = (s) => { const n = Math.max(0, Math.floor(s || 0)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}` }

export default function Live({ mode, startedAt, meetingId, ctl, utts, topics, ivs, onRenamed, showIv, goHistory, rosterTick, micOn, autoEnd, rosterLocked, suggests, llmOn }) {
  const [askOpen, setAskOpen] = useState(false)
  const [askPosition, setAskPosition] = useState(null)
  const askPanelRef = useRef(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState(false)
  const [lang] = useCardLang()
  useTranslateEnabled()
  const [, tick] = useState(0)
  const elapsedRef = useRef(0)
  if (mode === 'recording' && startedAt) elapsedRef.current = Date.now() / 1000 - startedAt
  const [attached, setAttached] = useState([])
  const [docView, setDocView] = useState(null)
  const [roster, setRoster] = useState({ participants: [], anons: [], unknown: 0 })
  const [rosterOpen, setRosterOpen] = useState(false)
  const toggleRoster = () => setRosterOpen((o) => !o)
  const myUtts = utts.filter((u) => u.spk === -1)
  const me = myUtts.length ? { name: (roster.me && roster.me.name) || myUtts[0].name || '', count: myUtts.length } : null
  const [layout, setLayout] = useState(() => {
    try { return { wTr: 3, wTp: 2, wDoc: 3, hideTr: false, hideTp: false, hMemo: 170, ...JSON.parse(localStorage.getItem('mc_cols') || '{}') } }
    catch { return { wTr: 3, wTp: 2, wDoc: 3, hideTr: false, hideTp: false, hMemo: 170 } }
  })
  const bodyRef = useRef(null)
  const tpColRef = useRef(null)
  const startMemoDrag = (e) => {
    e.preventDefault()
    const y0 = e.clientY, h0 = layout.hMemo || 170
    const colH = tpColRef.current ? tpColRef.current.getBoundingClientRect().height : 600
    const move = (ev) => saveLayout({ hMemo: Math.round(Math.min(Math.max(120, colH - 160), Math.max(80, h0 - (ev.clientY - y0)))) })
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  }
  const saveLayout = (patch) => setLayout((l) => {
    const next = { ...l, ...patch }
    try { localStorage.setItem('mc_cols', JSON.stringify(next)) } catch {}
    return next
  })
  const startDrag = (leftKey, rightKey) => (e) => {
    e.preventDefault()
    const startX = e.clientX
    const wL0 = layout[leftKey], wR0 = layout[rightKey]
    const total = wL0 + wR0
    const box = bodyRef.current ? bodyRef.current.getBoundingClientRect().width : 1200
    const pxPerW = box / (layout.wTr * !layout.hideTr + layout.wTp * !layout.hideTp + (docView ? layout.wDoc : 0) || 1)
    const move = (ev) => {
      const dW = (ev.clientX - startX) / pxPerW
      const wL = Math.min(total - 0.6, Math.max(0.6, wL0 + dW))
      saveLayout({ [leftKey]: wL, [rightKey]: total - wL })
    }
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  useEffect(() => {
    const t = setInterval(() => tick((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {
    if (meetingId) api(`/meeting/${meetingId}/contexts`).then((l) => setAttached(l || []))
    else setAttached([])
    setDocView(null)
    setQuestion(''); setAnswer(''); setAskError(false); setAskOpen(false); setAskPosition(null)
  }, [meetingId])
  useEffect(() => {
    if (meetingId) api(`/meeting/${meetingId}/roster`).then((r) => r && setRoster(r))
    else setRoster({ participants: [], anons: [], unknown: 0 })
  }, [meetingId, rosterTick])

  const rosterEdit = (action, sid) =>
    post(`/meeting/${meetingId}/roster`, { action, speaker_id: sid })
  const addRosterPerson = async () => {
    const people = await api('/speakers')
    const inR = new Set(roster.participants.map((p) => p.id))
    const cands = (people || []).filter((p) => !inR.has(p.id))
    const v = await modal.choose({
      title: T('pickPeople'),
      register: { placeholder: T('secNew'), button: T('registerBtn') },
      options: cands.map((p) => ({ label: p.name, value: p.id, hint: p.has_voice ? '●' : '◌', title: memberTip(p) })),
    })
    if (v === null || v === undefined) return
    if (v && v.register) {
      const m = await post('/speakers', { name: v.register })
      if (m && m.id) rosterEdit('add', m.id)
      return
    }
    rosterEdit('add', v)
  }
  const assignPlaceholder = async () => {
    const people = await api('/speakers')
    const inR = new Set(roster.participants.map((p) => p.id))
    const cands = (people || []).filter((p) => !inR.has(p.id))
    const v = await modal.choose({
      title: T('sheetTitle'),
      register: { placeholder: T('secNew'), button: T('registerBtn') },
      options: [
        ...cands.map((p) => ({ label: p.name, value: p.id, hint: p.has_voice ? '●' : '◌', title: memberTip(p) })),
        { label: T('removeAnonBtn'), value: '__remove__', hint: T('noUttsHint'), danger: true },
      ],
    })
    if (v === null || v === undefined) return
    if (v === '__remove__') { await rosterEdit('remove_unknown'); return }
    let sid = v
    if (v && v.register) {
      const m = await post('/speakers', { name: v.register })
      if (!m || !m.id) return
      sid = m.id
    }
    await rosterEdit('add', sid)
    await rosterEdit('remove_unknown')
  }

  const rightRef = useRef(null)
  useEffect(() => {
    const el = rightRef.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 200) {
      el.scrollTop = el.scrollHeight
    }
  }, [topics, ivs])

  const dragAsk = (e) => {
    if (e.button !== 0 || e.target.closest('button')) return
    e.preventDefault()
    const rect = askPanelRef.current.getBoundingClientRect()
    const startX = e.clientX, startY = e.clientY
    const move = (ev) => setAskPosition({
      left: Math.max(0, Math.min(window.innerWidth - rect.width, rect.left + ev.clientX - startX)),
      top: Math.max(0, Math.min(window.innerHeight - 48, rect.top + ev.clientY - startY)),
    })
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up)
  }

  const [sheet, setSheet] = useState(null)

  if (mode === 'idle') {
    return <Home mode={mode} ctl={ctl} goHistory={goHistory} llmOn={llmOn} />
  }
  const onReassign = async (u) => {
    const { pickSpeaker } = await import('../components/splitFlow.js')
    const v = await pickSpeaker(api, modal, T, u.spk, meetingId, post)
    if (v === null || v === undefined) return
    await post(`/utterance/${u.uid}/speaker`, { speaker_id: v })
  }

  const onSplit = (u) => splitFlow(u, utts, api, post, modal, T, meetingId)
  const onDelete = (u) => deleteUtt(u, del, modal, T)

  const jumpToUtt = (uid) => {
    const el = document.getElementById(`utt-${uid}`)
    if (!el) return
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    el.classList.add('flash')
    setTimeout(() => el.classList.remove('flash'), 1600)
  }

  return (
    <div className="live">
      {mode !== 'idle' && !llmOn && <div className="live-notice">{T('localAiOff')}</div>}
      <div className="live-body" ref={bodyRef}>
        <div className="col tr-col" style={{ flex: `${layout.wTr} 1 0%` }}>
          <div className="col-title live-head">
            <span className={'statuspill mini ' + (mode === 'recording' ? 'rec' : '')}>
              <span className="dot" />
              <span className="lh-timer">{startedAt ? fmt(elapsedRef.current) : '00:00:00'}</span>
            </span>
            <span className="lh-label">{T('transcript')}</span>
            <span style={{ flex: 1 }} />
            <span className={'col-act mic-toggle' + (micOn ? ' on' : '')} title={micOn ? T('micOnTip') : T('micOffTip')}
                  onClick={() => ctl.mic(!micOn)}>
              <IcMic size={13} />
            </span>
            <span className={'col-act' + (Object.keys(suggests || {}).length ? ' has-sug' : '')}
                  title={Object.keys(suggests || {}).length ? T('suggestPending') : T('participants')} onClick={toggleRoster}>
              <IcUsers size={15} /><span className="participant-count">{rosterLocked ? '🔒' : ''}{(me ? 1 : 0) + roster.participants.length + (roster.anons || []).length + roster.unknown || '∞'}</span>
              {Object.keys(suggests || {}).length ? <span className="sug-dot" /> : null}
            </span>
            <button className="ask-icon" type="button" disabled={!llmOn} title={llmOn ? T('askMeeting') : T('aiUnavailable')}
                    aria-label={T('askMeeting')} aria-expanded={askOpen} onClick={() => setAskOpen((open) => !open)}>
              <IcMsg size={16} />
            </button>
            <button className="end-btn" onClick={ctl.end}><IcStop size={11} /> {T('endBtn')}</button>
            {layout.hideTp && (
              <span className="col-act" title={T('topics')} onClick={() => saveLayout({ hideTp: false })}>
                <IcPanelR size={14} />
              </span>
            )}
            {rosterOpen && (
              <>
              <div className="menu-backdrop" onClick={() => setRosterOpen(false)} />
              <div className="roster-pop">
                <div className="attached-row" style={{ marginBottom: 0 }}>
                  {me && <span className="ctx-chip me" title={T('sheetCount', me.count)}>{me.name || T('me')}</span>}
                  {roster.participants.map((p) => (
                    <span key={p.id} className="ctx-chip clickable" title={T('editSpeaker')}
                          onClick={() => setSheet({ spk: p.id, name: p.name, named: true })}>
                      {p.name}{p.has_voice ? '' : ' ◌'}
                    </span>
                  ))}
                  {(roster.anons || []).map((a) => (
                    <span key={'a' + a.id} className={'ctx-chip unknown clickable' + (suggests && suggests[a.id] ? ' flagged' : '')}
                          title={suggests && suggests[a.id] ? T('suggestFlag') : T('editSpeaker')}
                          onClick={() => setSheet({ spk: a.id, name: a.name, named: false })}>
                      {a.name}{suggests && suggests[a.id] ? <span className="chip-flag">?</span> : null}
                    </span>
                  ))}
                  {Array.from({ length: roster.unknown }, (_, i) => (
                    <span key={'u' + i} className="ctx-chip unknown clickable" onClick={assignPlaceholder}>
                      {T('spkDefault', nextAnonNo(roster.anons) + i)} ◌
                    </span>
                  ))}
                  <span className="ctx-chip add" onClick={addRosterPerson}>＋ {T('pickPeople')}</span>
                </div>
                <div className={'roster-lock' + (rosterLocked ? ' on' : '')}
                     onClick={() => post('/meeting/roster_lock', { on: !rosterLocked })}>
                  <span className="lk">{rosterLocked ? '🔒' : '🔓'}</span>
                  <span>{T('rosterLock')}</span>
                  <span className="hint">{rosterLocked ? T('rosterLockOn') : T('rosterLockOff')}</span>
                </div>
              </div>
              </>
            )}
          </div>
          <Transcript utterances={utts} onReassign={onReassign} onSplit={onSplit} onDelete={onDelete} />
          <SpeakerSheet open={!!sheet} spk={sheet?.spk} name={sheet?.name} named={!!sheet?.named}
                        meetingId={meetingId} mode="live" suggest={sheet ? suggests[sheet.spk] : null}
                        count={sheet ? utts.filter((u) => u.spk === sheet.spk).length : 0}
                        onClose={() => setSheet(null)} onRenamed={onRenamed}
                        onChanged={() => api(`/meeting/${meetingId}/roster`).then((r) => r && setRoster(r))} />
        </div>
        {!layout.hideTp && <div className="col-resizer" onMouseDown={startDrag('wTr', 'wTp')} />}
        {!layout.hideTp && (
          <div className="col tp-col" ref={tpColRef} style={{ flex: `${layout.wTp} 1 0%` }}>
          <div className="col-title with-acts">{T('topics')}
            <span className="col-act" title={T('collapse')} onClick={() => saveLayout({ hideTp: true })}>
              <IcPanelR size={14} />
            </span>
          </div>
          <div className="scroll" ref={rightRef}>
            {attached.length > 0 && (
              <div className="attached-row">
                {attached.map((c) => (
                  <span key={c.id} className={'ctx-chip clickable' + (docView && docView.id === c.id ? ' on' : '')}
                        onClick={() => setDocView(docView && docView.id === c.id ? null : c)}>
                    📄 {c.name}
                    <PrioBadge value={c.priority} onChange={async (v) => {
                      await post(`/meeting/${meetingId}/context/${c.id}/priority`, { priority: v })
                      api(`/meeting/${meetingId}/contexts`).then(setAttached)
                    }} />
                  </span>
                ))}
              </div>
            )}
            {ivs.map((iv) => {
              const cls = { RESPOND: '', CONSIDER: 'consider', CONTRIBUTE: 'contribute', ASK: 'ask' }[iv.kind] || 'consider'
              const lbl = { RESPOND: T('yourTurn'), CONSIDER: T('consider'), CONTRIBUTE: T('kindCONTRIBUTE'), ASK: T('kindASK') }[iv.kind] || T('consider')
              return (
                <div key={iv.iv_id} className={'mini-iv ' + cls} onClick={() => showIv(iv)} title={T('reopenIv')}>
                  <div className="h">{lbl}{(() => { const u = utts.find((x) => x.uid === iv.uid); return u && u.off != null ? <span className="at">{fmtOff(u.off)}</span> : null })()}<span className="reopen">↗</span></div>
                  <div className="q">{iv.by}: {pick(iv, lang).question}</div>
                  {(iv.reply_guide || iv.reply_src) ? <div className="a">→ {(lang === 'src' ? (iv.reply_guide_src || iv.reply_guide) : iv.reply_guide) || iv.reply_src}</div> : null}
                </div>
              )
            })}
            <TopicList topics={topics} onJump={jumpToUtt} lang={lang} />
          </div>
          <MeetingMemo meetingId={meetingId} compact height={layout.hMemo} onResize={startMemoDrag} />
          </div>
        )}
        {docView && !layout.hideTp && <div className="col-resizer" onMouseDown={startDrag('wTp', 'wDoc')} />}
        {docView && (
          <div className="col doc-col" style={{ flex: `${layout.wDoc} 1 0%` }}>
            <div className="col-title doc-head">
              <span className="dt">📄 {docView.name}</span>
              <span className="dx" onClick={() => setDocView(null)}>✕</span>
            </div>
            <iframe title="doc" src={`/api/context/${docView.id}/view`} />
          </div>
        )}
      </div>
      {askOpen && (
        <section className="meeting-ask-panel" ref={askPanelRef} style={askPosition ? { left: askPosition.left, top: askPosition.top, right: 'auto', bottom: 'auto' } : undefined} aria-label={T('askMeeting')}>
          <div className="meeting-ask-head" onPointerDown={dragAsk}>
            <strong>{T('askMeeting')}</strong>
            <button type="button" onClick={() => setAskOpen(false)} aria-label={T('close')}><IcX size={15} /></button>
          </div>
          <form className="meeting-ask" onSubmit={async (e) => {
            e.preventDefault()
            if (!question.trim() || asking || !llmOn) return
            setAsking(true); setAskError(false); setAnswer('')
            try {
              const response = await fetch(`/api/meeting/${meetingId}/ask`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: question.trim() }),
              })
              if (response.status === 404) setAskError('askMeetingRestart')
              else if (!response.ok) setAskError('askMeetingError')
              else {
                const result = await response.json()
                if (result.answer) setAnswer(result.answer)
                else setAskError('askMeetingError')
              }
            } catch { setAskError('askMeetingError') }
            finally { setAsking(false) }
          }}>
            <label className="sr-only" htmlFor="meeting-question">{T('askMeeting')}</label>
            <textarea id="meeting-question" value={question} onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey && !isImeKey(e)) {
                          e.preventDefault()
                          e.currentTarget.form.requestSubmit()
                        }
                      }} placeholder={T('askMeetingPlaceholder')} maxLength={2000} disabled={!llmOn || asking} rows={2} />
            <button type="submit" disabled={!llmOn || asking || !question.trim()} title={!llmOn ? T('aiUnavailable') : undefined}>{asking ? T('askMeetingLoading') : T('askMeetingSend')}</button>
            {!llmOn && <div className="hint">{T('aiUnavailable')}</div>}
            {askError && <div role="alert">{T(askError)}</div>}
            {answer && <div className="meeting-answer">{answer}</div>}
          </form>
        </section>
      )}
    </div>
  )
}
