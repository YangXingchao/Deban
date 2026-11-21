import { useEffect, useRef, useState } from 'react'
import { T } from '../i18n.js'
import { api, post, upload } from '../api.js'
import { memberTip } from './splitFlow.js'
import { IcUpload, IcDoc, IcX, IcUsers, IcLive, IcLanguage, IcMic, IcSpark, IcTranslate } from './Icons.jsx'
import { modal } from './Modal.jsx'
import InfoTip from './InfoTip.jsx'
import PrioBadge from './PrioBadge.jsx'

export default function StartPanel({ onStart, starting, llmOn }) {
  const [selected, setSelected] = useState([])
  const [prios, setPrios] = useState({})
  const [brief, setBrief] = useState('')
  const [record, setRecord] = useState(false)
  const [mic, setMic] = useState(false)
  const [translate, setTranslate] = useState(true)
  const [srcLang, setSrcLang] = useState('ja')
  const [outLang, setOutLang] = useState('zh')
  useEffect(() => { api('/settings').then((c) => {
    if (!c) return
    setTranslate(c.translate_default !== false)
    setSrcLang(c.src_lang)
    setOutLang(c.out_lang)
    setMic(c.mic_enabled !== false)
  }) }, [])
  const saveLanguage = (patch) => {
    if (patch.src_lang) setSrcLang(patch.src_lang)
    if (patch.out_lang) setOutLang(patch.out_lang)
    post('/settings', patch)
  }
  const saveRecordDefault = (value) => {
    setRecord(value)
    post('/settings', { record_audio: value })
  }
  const saveTranslateDefault = (value) => {
    setTranslate(value)
    post('/settings', { translate_default: value })
  }
  const [people, setPeople] = useState([])
  const [chosen, setChosen] = useState([])
  const [nameInput, setNameInput] = useState('')
  const [newName, setNewName] = useState('')
  const [unknown, setUnknown] = useState(0)
  const [drawer, setDrawer] = useState(null)
  const [lib, setLib] = useState([])
  const [libQ, setLibQ] = useState('')
  const [prevId, setPrevId] = useState(null)
  const [prev, setPrev] = useState(null)
  const [suggested, setSuggested] = useState(null)
  const fileRef = useRef(null)
  useEffect(() => {
    api('/settings').then((c) => setRecord(!!(c || {}).record_audio))
    api('/speakers').then((l) => setPeople(l || []))
  }, [])

  const prefilled = useRef({ ctx: [], parts: [] })
  const pickPrev = async (mid) => {
    setPrevId(mid)
    if (!mid) {
      const { ctx, parts } = prefilled.current
      setSelected((s) => s.filter((c) => !ctx.includes(c.id)))
      setPrios((p) => Object.fromEntries(Object.entries(p).filter(([k]) => !ctx.includes(Number(k)))))
      setChosen((s) => s.filter((id) => !parts.includes(id)))
      prefilled.current = { ctx: [], parts: [] }
      setPrev(null)
      return
    }
    const p = await api(`/meeting/${mid}/prefill`)
    if (!p || !p.last) return
    setPrev(p.last); setSuggested(null)
    prefilled.current = { ctx: (p.contexts || []).map((c) => c.id), parts: p.participants || [] }
    if (p.participants?.length) setChosen((s) => [...new Set([...s, ...p.participants])])
    if (p.contexts?.length) {
      setSelected((s) => [...s, ...p.contexts.filter((c) => !s.some((x) => x.id === c.id)).map((c) => ({ id: c.id, name: c.name }))].slice(0, 5))
      setPrios((pr) => ({ ...pr, ...Object.fromEntries(p.contexts.map((c) => [c.id, c.priority])) }))
    }
  }
  const choosePrev = async () => {
    const list = await api('/history')
    const done = (list || []).filter((m) => m.status === 'done')
    const pids = new Set(done.flatMap((m) => String(m.participant_ids || '').split(',').filter(Boolean)))
    const pOpts = people.filter((p) => pids.has(String(p.id))).map((p) => ({ value: p.id, label: p.name }))
    const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const opts = done.map((m) => {
      const d = new Date(m.started_at * 1000)
      return {
        value: m.id, label: `${m.title || `#${m.id}`}`, hint: d.toLocaleDateString() + (m.utterances ? ` · ${m.utterances}${T('utt')}` : ''),
        meta: { day: ymd(d), parts: String(m.participant_ids || '').split(',') },
      }
    })
    const v = await modal.choose({
      title: T('prevPick'), options: opts, searchPh: T('searchMeetingsPh'), searchable: true,
      filters: [
        { key: 'day', type: 'date', label: T('filterDate'), match: (meta, v) => meta.day === v },
        ...(pOpts.length ? [{ key: 'p', type: 'multi', label: T('filterParticipant'), options: pOpts, match: (meta, vs) => vs.every((v) => meta.parts.includes(String(v))) }] : []),
      ],
    })
    if (v != null) pickPrev(v)
  }
  useEffect(() => {
    if (prevId || chosen.length === 0) { setSuggested(null); return }
    api(`/meeting/suggest_prev?participants=${chosen.join(',')}`).then((r) => setSuggested(r && r.id ? r : null))
  }, [chosen, prevId])

  const togglePerson = (id) => setChosen((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id])
  const addPerson = async () => {
    const v = newName.trim()
    if (!v) return
    const exact = people.find((p) => p.name === v)
    if (exact) {
      setChosen((s) => s.includes(exact.id) ? s : [...s, exact.id])
    } else {
      const row = await post('/speakers', { name: v })
      if (row && row.id) {
        setPeople((p) => p.some((x) => x.id === row.id) ? p : [...p, row])
        setChosen((s) => s.includes(row.id) ? s : [...s, row.id])
      }
    }
    setNewName('')
  }

  const MAX_CTX = 5
  const addSel = (row) => setSelected((s) => s.some((x) => x.id === row.id) || s.length >= MAX_CTX ? s : [...s, row])
  const rmSel = (id) => setSelected((s) => s.filter((x) => x.id !== id))

  const doUpload = async (f) => {
    if (!f) return
    if (f.size > 10 * 1024 * 1024) { await modal.confirm({ message: T('uploadTooLarge'), okText: T('ok') }); return }
    const row = await upload('/context', f)
    if (row && row.error) { await modal.confirm({ message: T(row.error === 'too large' ? 'uploadTooLarge' : 'uploadNoText'), okText: T('ok') }); return }
    if (row && row.id) addSel(row)
  }
  const openLib = async () => {
    setLib(await api('/context') || [])
    setDrawer('lib')
  }
  const openPeople = async () => {
    setPeople(await api('/speakers') || [])
    setDrawer('people')
  }

  return (
    <div className="start-panel">
      <div className="form-row">
        <label>{T('prevMeeting')}</label>
        <div style={{ flex: 1 }}>
          {prevId ? (
            <span className="ctx-chip series on">↩ {prev?.title || `#${prevId}`}
              <span className="chip-x" onClick={() => pickPrev(null)}><IcX size={11} /></span>
            </span>
          ) : (
            <button className="ghost" style={{ padding: '5px 10px' }} onClick={choosePrev}>↩ {T('prevPick')}</button>
          )}
          {prevId ? <button className="ghost" style={{ padding: '5px 8px', marginLeft: 4 }} onClick={choosePrev}>…</button> : null}
          {!prevId && suggested && (
            <div className="prev-suggest">
              <span>{T('prevSuggest', suggested.title || `#${suggested.id}`, new Date(suggested.started_at * 1000).toLocaleDateString())}</span>
              <button className="ghost" style={{ padding: '3px 10px' }} onClick={() => pickPrev(suggested.id)}>{T('yes')}</button>
            </div>
          )}
          {prev && (
            <div className="prev-brief">
              <span className="pb-t">{new Date(prev.started_at * 1000).toLocaleDateString()}</span>
              <span className="pb-s">
                {prev.decisions.length > 0 && `✔${prev.decisions.length} `}
                {prev.todos.length > 0 && `☐${prev.todos.length} `}
              </span>
              <div className="hint" style={{ margin: '3px 0 0' }}>{T('prevBriefHint')}</div>
            </div>
          )}
        </div>
      </div>
      <div className="form-row" style={{ alignItems: 'flex-start' }}>
        <label style={{ paddingTop: 7 }}>{T('participants')}
          <InfoTip>
            <b>{T('ptHelpT1')}</b><br />{T('ptHelp1')}<br /><br />
            <b>{T('ptHelpT2')}</b><br />{T('ptHelp2')}<br /><br />
            <b>{T('ptHelpT3')}</b><br />{T('ptHelp3')}
          </InfoTip>
        </label>
        <div style={{ flex: 1 }}>
          {(chosen.length > 0 || unknown > 0) && (
            <div className="attached-row">
              {chosen.map((id) => {
                const p = people.find((x) => x.id === id)
                return (
                  <span key={id} className="ctx-chip">{p ? p.name : `#${id}`}{p && !p.has_voice ? ' ◌' : ''}
                    <span className="chip-x" onClick={() => togglePerson(id)}><IcX size={11} /></span>
                  </span>
                )
              })}
              {Array.from({ length: unknown }, (_, i) => (
                <span key={'u' + i} className="ctx-chip unknown">{T('spkDefault', i + 1)}
                  <span className="chip-x" onClick={() => setUnknown((n) => n - 1)}><IcX size={11} /></span>
                </span>
              ))}
            </div>
          )}
          <div className="ctx-actions">
            <button className="ghost" onClick={openPeople}><IcUsers size={13} /> {T('pickPeople')}</button>
          </div>

        </div>
      </div>
      <div className="form-row" style={{ alignItems: 'flex-start' }}>
        <label style={{ paddingTop: 8 }}>{T('brief')}</label>
        <textarea rows={4} value={brief} placeholder={T('briefPh')} maxLength={2000}
                  onChange={(e) => setBrief(e.target.value)} style={{ flex: 1 }} />
      </div>
      <div className="form-row" style={{ alignItems: 'flex-start' }}>
        <label style={{ paddingTop: 6 }}>{T('attachContext')}{selected.length > 0 ? <span className="ctx-count">{selected.length}/{MAX_CTX}</span> : null}
          <InfoTip>
            <b>{T('ctxHelpT1')}</b><br />{T('ctxHelp1')}<br /><br />
            <b>{T('ctxHelpT2')}</b><br />{T('ctxHelp2')}<br /><br />
            <b>{T('ctxHelpT3')}</b><br />{T('ctxHelp3')}
          </InfoTip>
        </label>
        <div style={{ flex: 1 }}>
          {selected.length > 0 && (
            <div className="attached-row">
              {selected.map((c) => (
                <span key={c.id} className="ctx-chip"><IcDoc size={12} /> {c.name}
                  <PrioBadge value={prios[c.id]} onChange={(v) => setPrios((p) => ({ ...p, [c.id]: v }))} />
                  <span className="chip-x" onClick={() => rmSel(c.id)}><IcX size={11} /></span>
                </span>
              ))}
            </div>
          )}
          <div className="ctx-actions">
            <input ref={fileRef} type="file" style={{ display: 'none' }}
                   accept=".pdf,.md,.markdown,.txt,.docx,.pptx"
                   onChange={(e) => { doUpload(e.target.files[0]); e.target.value = '' }} />
            <button className="ghost" disabled={selected.length >= MAX_CTX} title={selected.length >= MAX_CTX ? T('ctxLimit') : undefined}
                    onClick={() => fileRef.current.click()}><IcUpload size={13} /> {T('uploadFile').replace('＋ ', '')}</button>
            <button className="ghost" disabled={selected.length >= MAX_CTX} title={selected.length >= MAX_CTX ? T('ctxLimit') : undefined}
                    onClick={openLib}><IcDoc size={13} /> {T('importLib')}</button>
            {selected.length >= MAX_CTX ? <span className="hint" style={{ margin: 0, alignSelf: 'center' }}>{T('ctxLimit')}</span> : null}
          </div>
        </div>
      </div>
      {drawer === 'people' && (
        <>
          <div className="drawer-backdrop" onClick={() => setDrawer(null)} />
          <div className="drawer">
            <div className="drawer-head">
              <span>{T('pickPeople')}</span>
              <button className="drawer-x" onClick={() => setDrawer(null)} aria-label={T('close')}><IcX size={15} /></button>
            </div>
            <div style={{ padding: '10px 12px 0', display: 'flex', gap: 8 }}>
              <input type="text" value={nameInput} placeholder={T('searchPeoplePh')} style={{ flex: 1 }} autoFocus
                     onChange={(e) => setNameInput(e.target.value)} />
            </div>
            <div className="drawer-body">
              {people.length === 0 && <div className="empty">{T('peopleEmpty')}</div>}
              {people.filter((p) => !nameInput.trim() || p.name.toLowerCase().includes(nameInput.trim().toLowerCase())).map((p) => {
                const on = chosen.includes(p.id)
                return (
                  <div key={p.id} className={'lib-pick-row' + (on ? ' on' : '')} title={memberTip(p)}
                       onClick={() => togglePerson(p.id)}>
                    <IcUsers size={13} /> <span className="n">{p.name}</span>
                    <span className="s">{p.has_voice ? '●' : '◌'}</span>
                    {on ? <span className="ok">✓</span> : null}
                  </div>
                )
              })}
            </div>
            <div className="drawer-foot pick-foot">
              <div className="pick-reg">
                <input type="text" value={newName} placeholder={T('secNew')} maxLength={30}
                       onChange={(e) => setNewName(e.target.value)}
                       onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); addPerson() } }} />
                <button className="primary" disabled={!newName.trim()} onClick={addPerson}>{T('registerBtn')}</button>
              </div>
            </div>
          </div>
        </>
      )}
      {drawer === 'lib' && (
        <>
          <div className="drawer-backdrop" onClick={() => setDrawer(null)} />
          <div className="drawer">
            <div className="drawer-head">
              <span>{T('importLib')}</span>
              <button className="drawer-x" onClick={() => setDrawer(null)} aria-label={T('close')}><IcX size={15} /></button>
            </div>
            <div style={{ padding: '10px 12px 0' }}>
              <input type="text" value={libQ} placeholder={T('libFilterPh')} style={{ width: '100%' }}
                     onChange={(e) => setLibQ(e.target.value)} />
            </div>
            <div className="drawer-body">
              {lib.length === 0 && <div className="empty">{T('libEmpty')}</div>}
              {lib.filter((c) => !libQ.trim() || c.name.toLowerCase().includes(libQ.trim().toLowerCase())).map((c) => {
                const on = selected.some((x) => x.id === c.id)
                return (
                  <div key={c.id} className={'lib-pick-row' + (on ? ' on' : '')}
                       onClick={() => on ? rmSel(c.id) : addSel(c)}>
                    <IcDoc size={13} /> <span className="n">{c.name}</span>
                    <span className="s">{c.type}</span>
                    {on ? <span className="ok">✓</span> : null}
                  </div>
                )
              })}
            </div>
          </div>
        </>
      )}
      <div className="form-row meeting-record-row">
        <label><IcMic size={15} /> {T('recordThis')}</label>
        <label className="switch">
          <input type="checkbox" checked={record} onChange={(e) => saveRecordDefault(e.target.checked)} />
          <span className="tr" />
        </label>
      </div>
      <section className="translation-setup">
        <div className="translation-setup-head"><IcSpark size={16} /> {T('translateThis')}</div>
        <div className="form-row">
          <label><IcMic size={15} /> {T('micEnabled')}</label>
          <label className="switch">
            <input type="checkbox" aria-label={T('micEnabled')} checked={mic} onChange={(e) => setMic(e.target.checked)} />
            <span className="tr" />
          </label>
        </div>
        <div className="form-row" title={!llmOn ? T('aiUnavailable') : undefined}>
          <label>{T('translateThis')}</label>
          <label className="switch">
            <input type="checkbox" checked={llmOn && translate} disabled={!llmOn} onChange={(e) => saveTranslateDefault(e.target.checked)} />
            <span className="tr" />
          </label>
        </div>
        {!llmOn && <div className="hint">{T('aiUnavailable')}</div>}
        <div className="form-row">
          <label><IcLanguage size={15} /> {T('langSrc')}</label>
          <select value={srcLang} onChange={(e) => saveLanguage({ src_lang: e.target.value })}>
            <option value="en">English</option><option value="ja">日本語</option>
            <option value="zh">{T('langChinese')}</option>
          </select>
        </div>
        {llmOn && (
        <div className="form-row">
          <label style={{ opacity: !translate ? .5 : 1 }}><IcTranslate size={15} /> {T('langOut')}</label>
          <select value={outLang} disabled={!translate}
                  onChange={(e) => saveLanguage({ out_lang: e.target.value })}>
            {[['en', 'English'], ['ja', '日本語'], ['zh-Hant', '繁體中文'], ['zh', '简体中文']].map(([v, lb]) => (
              <option key={v} value={v}>{lb}</option>
            ))}
          </select>
        </div>
        )}
      </section>
      <div style={{ marginTop: 16 }}>
        <button className="primary start-btn" style={{ width: '100%', padding: '11px 0', fontSize: 14 }}
                disabled={starting} onClick={() => onStart({ context_ids: selected.map((c) => c.id), context_prios: prios, brief, participants: chosen, record, unknown,
                                          translate: llmOn && translate, mic, prev_meeting_id: prevId })}>
          <IcLive size={15} /> {T('startMeeting')}
        </button>
        <p className="hint" style={{ marginTop: 8 }}>{T('captureChooserHint')}</p>
      </div>
    </div>
  )
}
