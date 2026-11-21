import { useEffect, useRef, useState } from 'react'
import { T } from '../i18n.js'
import { api, post, del } from '../api.js'
import Transcript from '../components/Transcript.jsx'
import TopicList from '../components/TopicList.jsx'
import { IcSearch, IcDownload, IcWave, IcChev, IcThumbUp, IcThumbDown } from '../components/Icons.jsx'
import SpeakerChip from '../components/SpeakerChip.jsx'
import { modal } from '../components/Modal.jsx'
import DocViewer from '../components/DocViewer.jsx'
import MeetingMemo from '../components/MeetingMemo.jsx'
import { Cites, laneLabel, pick, StanceReply } from '../components/InterventionCard.jsx'
import { useCardLang, LangToggle, useTranslateEnabled, pickText } from '../components/contentLang.jsx'
import PrioBadge from '../components/PrioBadge.jsx'
import PillSelect from '../components/PillSelect.jsx'
import SpeakerSheet from '../components/SpeakerSheet.jsx'
import { nextAnonNo } from './Live.jsx'
import { splitFlow, deleteUtt, memberTip } from '../components/splitFlow.js'

function fmtDur(sec) {
  const m = Math.round((sec || 0) / 60)
  if (!m) return '<1' + T('minUnit')
  return m >= 60 ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}` : `${m}${T('minUnit')}`
}

const fmtOff = (s) => { const n = Math.max(0, Math.floor(s || 0)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}` }

export default function History({ onRenamed, mode, histTick, openId, onOpened, openIv, onOpenedIv, listHidden }) {
  const [list, setList] = useState([])
  const [sel, setSel] = useState(null)
  const [detail, setDetail] = useState(null)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState(null)
  const [sort, setSort] = useState('newest')
  const [filter, setFilter] = useState('')
  const [split, setSplit] = useState(() => { try { const v = parseFloat(localStorage.getItem('mc.histSplit')); return v >= 0.25 && v <= 0.75 ? v : 0.58 } catch { return 0.58 } })
  const hdBodyRef = useRef(null)
  const startSplit = (e) => {
    e.preventDefault()
    const box = hdBodyRef.current ? hdBodyRef.current.getBoundingClientRect() : null
    if (!box) return
    const move = (ev) => {
      const v = Math.min(0.75, Math.max(0.25, (ev.clientX - box.left) / box.width))
      setSplit(v); try { localStorage.setItem('mc.histSplit', String(v)) } catch {}
    }
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  }
  const [activeUid, setActiveUid] = useState(null)
  const [docView, setDocView] = useState(null)
  const [roster, setRoster] = useState(null)
  const [audioSegs, setAudioSegs] = useState([])
  const [nearMisses, setNearMisses] = useState([])
  const segRefs = useRef({})

  const setDetailSafe = (d) => setDetail(d && !d.error ? d : null)
  const reloadDetail = () => { if (sel) api(`/meeting/${sel}`).then(setDetailSafe) }
  const reload = () => {
    api('/history').then((l) => setList(l || []))
  }
  useEffect(() => { reload() }, [])
  useEffect(() => { if (openId) { setSel(openId); onOpened && onOpened() } }, [openId])
  useEffect(() => {
    if (!histTick) return
    reload()
    if (sel) api(`/meeting/${sel}`).then(setDetailSafe)
  }, [histTick])

  useEffect(() => {
    if (!sel) { setDetail(null); setRoster(null); return }
    setFilter('')
    api(`/meeting/${sel}`).then(setDetailSafe)
    api(`/meeting/${sel}/roster`).then((r) => setRoster(r && !r.error ? r : null))
    setAudioSegs([]); segRefs.current = {}
    api(`/meeting/${sel}/audio_segments`).then((l) => setAudioSegs(l || []))
    setNearMisses([])
    api(`/meeting/${sel}/near_misses`).then((l) => setNearMisses(l || []))
  }, [sel])

  const [sheet, setSheet] = useState(null)
  const [ivOpen, setIvOpen] = useState({})
  const [cardLang, toggleCardLang] = useCardLang()
  const trEnabled = useTranslateEnabled()
  useEffect(() => {
    if (!openIv || !detail || !(detail.interventions || []).some((iv) => iv.id === openIv)) return
    setIvOpen((o) => ({ ...o, [openIv]: true }))
    setTimeout(() => {
      const el = document.getElementById(`iv-${openIv}`)
      if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1600) }
    }, 150)
    onOpenedIv && onOpenedIv()
  }, [openIv, detail])
  const refreshAll = () => {
    api(`/meeting/${sel}`).then(setDetailSafe)
    api(`/meeting/${sel}/roster`).then((r) => setRoster(r && !r.error ? r : null))
    reload()
  }

  const rosterEdit = async (action, sid) => {
    await post(`/meeting/${sel}/roster`, { action, speaker_id: sid })
    api(`/meeting/${sel}/roster`).then((r) => setRoster(r && !r.error ? r : null))
  }
  const addRosterPerson = async () => {
    const people = await api('/speakers')
    const inR = new Set((roster?.participants || []).map((p) => p.id))
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

  const sorted = [...list].sort((a, b) => {
    if ((b.starred || 0) !== (a.starred || 0)) return (b.starred || 0) - (a.starred || 0)
    if (sort === 'oldest') return a.started_at - b.started_at
    if (sort === 'longest') return (b.duration || 0) - (a.duration || 0)
    if (sort === 'utts') return (b.utterances || 0) - (a.utterances || 0)
    if (sort === 'responds') return (b.responds || 0) - (a.responds || 0)
    return b.started_at - a.started_at
  })

  const starMtg = async (m) => {
    await post(`/meeting/${m.id}/star`, { starred: !m.starred })
    reload()
    if (sel === m.id) api(`/meeting/${sel}`).then(setDetailSafe)
  }

  const [dlOpen, setDlOpen] = useState(false)

  useEffect(() => {
    if (!q.trim()) { setHits(null); return }
    const t = setTimeout(() => api(`/search?q=${encodeURIComponent(q)}`).then(setHits), 300)
    return () => clearTimeout(t)
  }, [q])

  const renameMtg = async (m) => {
    const t = await modal.prompt({ title: T('rename'), message: T('mtgName'), value: m.title, maxLength: 30 })
    if (t && t.trim()) { await post(`/meeting/${m.id}/rename`, { title: t.trim() }); reload() }
  }
  const deleteMtg = async (m) => {
    if (!await modal.confirm({ title: T('del'), message: T('delConfirm', m.title || `#${m.id}`), danger: true })) return
    const r = await del(`/meeting/${m.id}`)
    if (r && r.error) { await modal.confirm({ message: T('archBusy'), okText: T('ok') }); return }
    if (sel === m.id) setSel(null)
    reload()
  }
  const onReassign = async (u) => {
    const { pickSpeaker } = await import('../components/splitFlow.js')
    const v = await pickSpeaker(api, modal, T, u.spk, sel, post)
    if (v === null || v === undefined) return
    await post(`/utterance/${u.uid}/speaker`, { speaker_id: v })
    api(`/meeting/${sel}`).then(setDetailSafe)
    api(`/meeting/${sel}/roster`).then((r) => setRoster(r && !r.error ? r : null))
  }
  const onSplit = async (u) => {
    if (await splitFlow(u, utts, api, post, modal, T, sel)) api(`/meeting/${sel}`).then(setDetailSafe)
  }
  const onDelete = async (u) => {
    if (await deleteUtt(u, del, modal, T)) api(`/meeting/${sel}`).then(setDetailSafe)
  }
  const editUtt = async (u) => {
    const r = await modal.form({
      title: T('edit'),
      fields: [
        { key: 'text', label: T('editSrc'), value: u.text, multiline: true },
        { key: 'translation', label: T('editTrans'), value: u.zh || '', multiline: true },
      ],
    })
    if (!r) return
    await post(`/utterance/${u.uid}/edit`, { text: r.text, translation: r.translation })
    api(`/meeting/${sel}`).then(setDetailSafe)
  }

  const d = detail
  const meetingExportName = (suffix) => {
    const title = String(d?.meeting?.title || '')
      .replace(/[<>:"/\\|?*\x00-\x1f]/g, '-')
      .replace(/\s+/g, ' ')
      .replace(/^[. ]+|[. ]+$/g, '')
      .slice(0, 100)
    return `${title || `meeting-${d?.meeting?.id || ''}`}${suffix}`
  }
  const names = d ? d.speaker_names : {}
  const ivByUtt = {}
  if (d) for (const iv of d.interventions) if (iv.utterance_id) ivByUtt[iv.utterance_id] = iv
  const utts = d ? d.utterances.map((u) => ({
    uid: u.id, spk: u.speaker_id, off: u.start_time,
    name: u.speaker_id < 0 ? T('me') : (names[u.speaker_id] || `話者${u.speaker_id}`),
    named: u.speaker_id < 0 || !!names[u.speaker_id],
    lang: u.lang, ts: new Date(u.ts * 1000).toLocaleTimeString(),
    text: u.corrected_text || u.text, revised: !!u.corrected_text,
    zh: u.translation, pendingZh: false,
    marker: ivByUtt[u.id] ? (ivByUtt[u.id].type === 'RESPOND' ? '🔴' : '🟡') : '',
  })) : []
  const topics = d ? d.topics.map((t) => ({
    range: '', title: t.title, title_src: t.title_src || '', points: JSON.parse(t.points || '[]'),
  })) : []

  const segFor = (off) => {
    let seg = null
    for (const g of audioSegs) { if ((g.start_off || 0) <= off) seg = g; else break }
    return seg || audioSegs[0] || null
  }
  const seek = (off) => {
    const seg = segFor(off)
    if (!seg) return
    for (const [id, el] of Object.entries(segRefs.current)) {
      if (el && String(id) !== String(seg.id)) el.pause()
    }
    const a = segRefs.current[seg.id]
    if (a) { a.currentTime = Math.max(0, off - (seg.start_off || 0)); a.play() }
  }

  const onTimeUpdate = (seg) => () => {
    const a = segRefs.current[seg.id]
    if (!a || a.paused || !utts.length) return
    const t = (seg.start_off || 0) + a.currentTime
    let cur = null
    for (const u of utts) { if (u.off <= t) cur = u; else break }
    if (cur && cur.uid !== activeUid) {
      setActiveUid(cur.uid)
      document.getElementById(`utt-${cur.uid}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }

  const jumpTo = (uid) => {
    if (!uid) return
    setFilter('')
    const u = utts.find((x) => x.uid === uid)
    if (u) {
      const seg = segFor(u.off)
      const a = seg && segRefs.current[seg.id]
      if (a) a.currentTime = Math.max(0, u.off - (seg.start_off || 0))
    }
    setActiveUid(uid)
    requestAnimationFrame(() => {
      const el = document.getElementById(`utt-${uid}`)
      if (el) {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' })
        el.classList.add('flash')
        setTimeout(() => el.classList.remove('flash'), 1600)
      }
    })
  }
  const summary = d ? JSON.parse(d.meeting.summary || '[]') : []
  const todos = d ? JSON.parse(d.meeting.todos || '[]') : []
  const decisions = d ? JSON.parse(d.meeting.decisions || '[]') : []

  return (
    <div className={'history' + (listHidden ? ' list-hidden' : '')}>
      <div className="hist-list">
        <div className="hist-tools">
          <div className="searchwrap">
            <IcSearch size={15} />
            <input placeholder={T('searchPh')} value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {hits === null && (
            <PillSelect prefix="↓ " value={sort} onChange={setSort}
                        options={[
                          { value: 'newest', label: T('sortNewest') },
                          { value: 'oldest', label: T('sortOldest') },
                          { value: 'longest', label: T('sortLongest') },
                          { value: 'utts', label: T('sortUtts') },
                          { value: 'responds', label: T('sortResponds') },
                        ]} />
          )}
        </div>
        {hits !== null && hits.map((h, i) => (
          <div className="hist-item" key={'h' + i} onClick={() => { setQ(''); setSel(h.meeting_id) }}>
            <div className="t">{h.title || `#${h.meeting_id}`}</div>
            <div className="s">{h.snippet || new Date(h.started_at * 1000).toLocaleString()}</div>
          </div>
        ))}
        {hits === null && sorted.map((m) => (
          <div className={'hist-item' + (sel === m.id ? ' on' : '')} key={m.id} onClick={() => setSel(m.id)}>
            <div className="t" title={[...(m.me ? [m.me.name || T('me')] : []), ...(m.participants || [])].join('、')}>{m.starred ? <span className="fav">★ </span> : null}{m.title || `#${m.id}`}
              {m.status === 'live' ? <span className="arch-badge">{T('archBadge')}</span> : null}
            </div>
            <div className="s">
              {new Date(m.started_at * 1000).toLocaleString()}{m.duration ? ` · ${fmtDur(m.duration)}` : ''} · {m.utterances}{T('utt')}
              {m.has_audio ? <span className="s-mic"><IcWave size={11} /></span> : null}
              {m.responds ? ` · 🔴${m.responds}` : ''}
            </div>
            <div className="acts">
              <span className={m.starred ? 'star-on' : ''} title={m.starred ? T('unstar') : T('star')}
                    onClick={(e) => { e.stopPropagation(); starMtg(m) }}>{m.starred ? '★' : '☆'}</span>
              <span title={T('rename')} onClick={(e) => { e.stopPropagation(); renameMtg(m) }}>✎</span>
              {m.status !== 'live' && (
                <span title={T('del')} onClick={(e) => { e.stopPropagation(); deleteMtg(m) }}>✕</span>
              )}
            </div>
          </div>
        ))}
        {hits === null && !list.length && <div className="empty">{T('noMeetings')}</div>}
      </div>
      <div className={'hist-detail' + (d ? ' split' : '')}>
        {!d && (() => {
          const now = new Date()
          const sameDay = (t) => { const x = new Date(t * 1000); return x.toDateString() === now.toDateString() }
          const sameMonth = (t) => { const x = new Date(t * 1000); return x.getFullYear() === now.getFullYear() && x.getMonth() === now.getMonth() }
          const agg = (f) => {
            const ms = list.filter((m) => f(m.started_at))
            return [ms.length, Math.round(ms.reduce((a, m) => a + (m.duration || 0), 0) / 60)]
          }
          const [tn, tm] = agg(sameDay)
          const [mn, mm] = agg(sameMonth)
          return (
            <div className="hist-empty-stats">
              <div className="card stat">
                <div className="k">{T('statToday')}</div>
                <div className="v">{tn}<small>{T('mtgUnit')}</small></div>
                <div className="hint" style={{ margin: 0 }}>{tm} {T('minUnit')}</div>
              </div>
              <div className="card stat">
                <div className="k">{T('statThisMonth')}</div>
                <div className="v">{mn}<small>{T('mtgUnit')}</small></div>
                <div className="hint" style={{ margin: 0 }}>{mm} {T('minUnit')}</div>
              </div>
            </div>
          )
        })()}
        {d && (
          <>
            <div className="detail-head">
              <h2>{d.meeting.title || `#${d.meeting.id}`}</h2>
              {trEnabled ? <LangToggle lang={cardLang} onToggle={toggleCardLang} /> : null}
              <span className={'star-toggle' + (d.meeting.starred ? ' on' : '')}
                    title={d.meeting.starred ? T('unstar') : T('star')}
                    onClick={() => starMtg(d.meeting)}>{d.meeting.starred ? '★' : '☆'}</span>
              {d.prev_title ? (
                <span className="series-chip on" title={T('prevMeeting')} onClick={() => setSel(d.meeting.prev_meeting_id)}>
                  ↩ {T('prevMeeting')}: {d.prev_title}
                </span>
              ) : null}
              <span className="date">{new Date(d.meeting.started_at * 1000).toLocaleString()}{d.meeting.duration ? ` · ${fmtDur(d.meeting.duration)}` : ''}</span>
              <span style={{ flex: 1 }} />
                <div className="dl-menu-wrap">
                <button onClick={() => setDlOpen((o) => !o)}>
                  <IcDownload size={13} /> {T('download')} <IcChev size={12} />
                </button>
                {dlOpen && (
                  <>
                    <div className="menu-backdrop" onClick={() => setDlOpen(false)} />
                    <div className="dl-menu" onClick={() => setDlOpen(false)}>
                      <a href={`/api/export/${d.meeting.id}.md?filename=${encodeURIComponent(meetingExportName('.md'))}`} download={meetingExportName('.md')}>{T('exportMd')}</a>
                      <a href={`/api/export/${d.meeting.id}.srt?filename=${encodeURIComponent(meetingExportName('.srt'))}`} download={meetingExportName('.srt')}>{T('exportSrt')}</a>
                      {audioSegs.map((g, i) => (
                        <a key={g.id} href={g.url} download>
                          {T('exportAudio')}{audioSegs.length > 1 ? ` · ${T('audioPart', i + 1)}` : ''}
                        </a>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
            {d.meeting.status === 'live' && (
              <div className="arch-banner">{T('archDetailHint')}</div>
            )}
            {roster && (roster.me || roster.participants.length > 0 || (roster.anons || []).length > 0 || roster.unknown > 0) && (
              <div className="attached-row" style={{ marginBottom: 8 }}>
                <span className="rb-title" style={{ marginBottom: 0, alignSelf: 'center' }}>{T('participants')}</span>
                {roster.me && <span className="ctx-chip me" title={T('sheetCount', roster.me.count)}>{roster.me.name || T('me')}</span>}
                {[...roster.participants.map((p) => ({ ...p, _named: true })),
                  ...(roster.anons || []).map((a) => ({ ...a, _named: false }))].map((p) => (
                  <span key={p.id} className={'ctx-chip spk-chip' + (p._named ? '' : ' unknown')} title={T('editSpeaker')}
                        onClick={() => setSheet({ spk: p.id, name: p.name, named: !!p._named })}>
                    {p.name}{p._named && !p.has_voice ? ' ◌' : ''}
                  </span>
                ))}
                {Array.from({ length: roster.unknown }, (_, i) => (
                  <span key={'u' + i} className="ctx-chip unknown">{T('spkDefault', nextAnonNo(roster.anons) + i)}</span>
                ))}
                <span className="ctx-chip add" title={T('addParticipant')} onClick={addRosterPerson}>＋</span>
              </div>
            )}
            {(d.contexts || []).length > 0 && (
              <div className="attached-row" style={{ marginBottom: 10 }}>
                {d.contexts.map((c) => c.exists ? (
                  <span key={c.id} className="ctx-chip clickable" onClick={() => setDocView(c)}>📄 {c.name}
                    <PrioBadge value={c.priority} onChange={async (v) => {
                      await post(`/meeting/${d.meeting.id}/context/${c.id}/priority`, { priority: v })
                      api(`/meeting/${sel}`).then(setDetailSafe)
                    }} />
                  </span>
                ) : (
                  <span key={c.id} className="ctx-chip deleted" title={T('ctxDeleted')}>📄 {c.name} · {T('ctxDeleted')}</span>
                ))}
              </div>
            )}
            {audioSegs.length > 0 && <div className="audio-dock">
            {audioSegs.map((g, i) => (
              <div key={g.id} className="audio-seg">
                {audioSegs.length > 1 && (
                  <span className="as-label">{T('audioPart', i + 1)} · {`${String(Math.floor((g.start_off || 0) / 60)).padStart(2, '0')}:${String(Math.floor((g.start_off || 0) % 60)).padStart(2, '0')}`}〜</span>
                )}
                <audio ref={(el) => { segRefs.current[g.id] = el }} controls preload="metadata"
                       src={g.url} onTimeUpdate={onTimeUpdate(g)}
                       onPlay={() => { for (const [id, el] of Object.entries(segRefs.current)) if (el && String(id) !== String(g.id) && !el.paused) el.pause() }} />
              </div>
            ))}
            </div>}
            <div className="hd-body" ref={hdBodyRef}>
            <div className="hd-left" style={{ flex: `${split} 1 0%` }}>
            <div className="section-title">{T('transcript')}
              <input className="inline-filter" placeholder={T('filterPh')} value={filter}
                     onChange={(e) => setFilter(e.target.value)} />
            </div>
            <div className="hd-tr">
              <Transcript utterances={filter.trim()
                            ? utts.filter((u) => (u.text + (u.zh || '') + u.name).toLowerCase().includes(filter.toLowerCase()))
                            : utts}
                          onSeek={audioSegs.length ? seek : null} onEdit={editUtt} onReassign={onReassign} onSplit={onSplit} onDelete={onDelete} activeUid={activeUid} />
            </div>
            </div>
            <div className="col-resizer hd-resizer" onMouseDown={startSplit} />
            <div className="hd-right" style={{ flex: `${1 - split} 1 0%` }}>
            <div className="section-title">📝 {T('memo')}</div>
            <div className="card" style={{ padding: '10px 14px', marginBottom: 4 }}>
              <MeetingMemo meetingId={d.meeting.id} />
            </div>
            {summary.length > 0 && (
              <>
                <div className="section-title">{T('meetingSummary')}</div>
                <div className="card" style={{ padding: '12px 16px', marginBottom: 4 }}>
                  {summary.map((s, i) => (
                    <div className="ref-point" key={i}>
                      <span className="txt">{pickText(s, cardLang)}</span>
                      {s.uid ? <span className="jump" title={T('jumpTo')} onClick={() => jumpTo(s.uid)}>↗</span> : null}
                    </div>
                  ))}
                </div>
              </>
            )}
            {decisions.length > 0 && (
              <>
                <div className="section-title">{T('decisions')}</div>
                <div className="card" style={{ padding: '12px 16px', marginBottom: 4 }}>
                  {decisions.map((s, i) => (
                    <div className="ref-point" key={i}>
                      <span className="cb">✔</span>
                      <span className="txt">{pickText(s, cardLang)}</span>
                      {s.uid ? <span className="jump" title={T('jumpTo')} onClick={() => jumpTo(s.uid)}>↗</span> : null}
                    </div>
                  ))}
                </div>
              </>
            )}
            {todos.length > 0 && (
              <>
                <div className="section-title">{T('todoTitle')}</div>
                <div className="card" style={{ padding: '12px 16px', marginBottom: 4 }}>
                  {todos.map((s, i) => (
                    <div className="ref-point todo" key={i}>
                      <span className="cb">☐</span>
                      <span className="txt">
                        {s.owner ? <span className={'td-owner' + (s.owner === 'me' ? ' me' : '')}>{s.owner === 'me' ? T('me') : s.owner}</span> : null}
                        {pickText(s, cardLang)}
                      </span>
                      {s.uid ? <span className="jump" title={T('jumpTo')} onClick={() => jumpTo(s.uid)}>↗</span> : null}
                    </div>
                  ))}
                </div>
              </>
            )}
            {nearMisses.length > 0 && (
              <>
                <div className="section-title">{T('nearMiss')} · {nearMisses.length}</div>
                <div className="hint" style={{ marginTop: -4 }}>{T('nearMissHint')}</div>
                {nearMisses.map((nm) => (
                  <div key={nm.id} className={'nm-card' + (nm.verdict ? ' done ' + nm.verdict : '')}>
                    <div className="q" onClick={() => nm.utterance_id && jumpTo(nm.utterance_id)} style={{ cursor: nm.utterance_id ? 'pointer' : 'default' }}>
                      {nm.by_speaker}: {nm.text}
                    </div>
                    <div className="meta">
                      {nm.lane ? <span>{laneLabel(nm.lane)}</span> : null}
                      {nm.why ? <span> · {nm.why}</span> : null}
                      <span> · {T('nmLLM', nm.llm_type || 'NONE', Math.round((nm.confidence || 0) * 100) + '%')}</span>
                    </div>
                    {nm.reason ? (
                      <div className="meta nm-reason">{T('nmWhyNot')}: {T('nmr_' + nm.reason)}
                        {nm.detail && (nm.reason === 'threshold' || nm.reason === 'cap') ? ` (${nm.detail})` : ''}</div>
                    ) : null}
                    {!nm.verdict && <div className="acts thumbs">
                      <button className={'thumb' + (nm.verdict === 'should' ? ' on' : '')} title={T('nmShould')} aria-label={T('nmShould')}
                              onClick={async () => { await post(`/near_miss/${nm.id}/verdict`, { verdict: 'should' }); api(`/meeting/${sel}/near_misses`).then((l) => setNearMisses(l || [])) }}><IcThumbUp size={16} filled={nm.verdict === 'should'} /></button>
                      <button className={'thumb' + (nm.verdict === 'no' ? ' on' : '')} title={T('nmNo')} aria-label={T('nmNo')}
                              onClick={async () => { await post(`/near_miss/${nm.id}/verdict`, { verdict: 'no' }); api(`/meeting/${sel}/near_misses`).then((l) => setNearMisses(l || [])) }}><IcThumbDown size={16} filled={nm.verdict === 'no'} /></button>
                    </div>}
                  </div>
                ))}
              </>
            )}
            {d.interventions.length > 0 && (
              <>
                <div className="section-title">{T('interventions')}</div>
                {d.interventions.map((iv) => {
                  const open = !!ivOpen[iv.id]
                  const toggle = () => setIvOpen((o) => ({ ...o, [iv.id]: !open }))
                  const cls = { RESPOND: '', CONSIDER: 'consider', CONTRIBUTE: 'contribute', ASK: 'ask' }[iv.type] || 'consider'
                  const lbl = { RESPOND: T('yourTurn'), CONSIDER: T('consider'), CONTRIBUTE: T('kindCONTRIBUTE'), ASK: T('kindASK') }[iv.type] || T('consider')
                  const parse = (s, dflt) => { try { const v = JSON.parse(s || ''); return v == null ? dflt : v } catch { return dflt } }
                  const k = utts.findIndex((u) => u.uid === iv.utterance_id)
                  const ctxLines = k >= 0 ? utts.slice(Math.max(0, k - 2), k + 1) : []
                  const pk = pick(iv, cardLang)
                  const points = pk.points
                  return (
                    <div key={iv.id} id={`iv-${iv.id}`} className={'mini-iv hist ' + cls + (open ? ' open' : '')}>
                      <div className="h" onClick={toggle}>{lbl}{(() => { const u = utts.find((x) => x.uid === iv.utterance_id); return u && u.off != null ? <span className="at">{fmtOff(u.off)}</span> : null })()}
                        {iv.verdict ? <span className="rated" title={iv.verdict === 'good' ? T('ivGood') : T('ivBad')}>{iv.verdict === 'good' ? <IcThumbUp size={12} filled /> : <IcThumbDown size={12} filled />}</span> : null}
                        <span className="chev">{open ? '▴' : '▾'}</span></div>
                      <div className="q" onClick={toggle}>{iv.by_speaker}: {pk.question}</div>
                      {open && (
                        <div className="iv-detail">
                          {ctxLines.length > 0 && (
                            <div className="ctx-lines">
                              <div className="ctx-k">{T('ctxLbl')}</div>
                              {ctxLines.map((u) => (
                                <div key={u.uid} className={'cl' + (u.uid === iv.utterance_id ? ' trig' : '')} onClick={() => jumpTo(u.uid)}>
                                  <span className="who"><SpeakerChip spk={u.spk} name={u.name} named={u.named} /></span><span className="tx">{cardLang === 'src' ? u.text : (u.zh || u.text)}</span>
                                </div>
                              ))}
                            </div>
                          )}
                          {((pk.why || iv.why) || points.length > 0) && (
                            <div className="why iv-sec">
                              <div className="sec-k">{T('whySecLbl')}</div>
                              {(pk.why || iv.why) ? <div className="why-line">{pk.why || iv.why}</div> : null}
                              {points.length > 0 && <div className="ctx">{points.map((c, i) => <div key={i}>· {c}</div>)}</div>}
                            </div>
                          )}
                          <div className="a iv-sec">
                            <div className="sec-k">{T('replyLbl')}</div>
                            <StanceReply iv={iv} lang={cardLang} />
                            <Cites sources={parse(iv.sources, {})} onDoc={(m) => setDocView({ id: m.id, name: m.name })} onUtt={jumpTo} />
                          </div>
                        </div>
                      )}
                      {!iv.verdict && <div className="acts thumbs">
                        <button className={'thumb' + (iv.verdict === 'good' ? ' on' : '')} title={T('ivGood')} aria-label={T('ivGood')}
                                onClick={async () => { await post(`/intervention/${iv.id}/verdict`, { verdict: 'good' }); reloadDetail() }}><IcThumbUp size={16} filled={iv.verdict === 'good'} /></button>
                        <button className={'thumb' + (iv.verdict === 'bad' ? ' on' : '')} title={T('ivBad')} aria-label={T('ivBad')}
                                onClick={async () => { await post(`/intervention/${iv.id}/verdict`, { verdict: 'bad' }); reloadDetail() }}><IcThumbDown size={16} filled={iv.verdict === 'bad'} /></button>
                      </div>}
                    </div>
                  )
                })}
              </>
            )}
            {topics.length > 0 && (
              <>
                <div className="section-title">{T('topics')}</div>
                <TopicList topics={topics} onJump={jumpTo} lang={cardLang} />
              </>
            )}
            </div>
            </div>
            <SpeakerSheet open={!!sheet} spk={sheet?.spk} name={sheet?.name} named={!!sheet?.named}
                          meetingId={sel} mode="history" suggest={null}
                          count={sheet ? utts.filter((u) => u.spk === sheet.spk).length : 0}
                          onClose={() => setSheet(null)} onRenamed={onRenamed} onChanged={refreshAll} />
          </>
        )}
      </div>
      <DocViewer doc={docView} onClose={() => setDocView(null)} />
    </div>
  )
}
