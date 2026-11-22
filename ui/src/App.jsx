import { useEffect, useRef, useState, useCallback } from 'react'
import { T, onLangChange } from './i18n.js'
import { api, post } from './api.js'
import { prepareCapture } from './capture.js'
import Live from './views/Live.jsx'
import History from './views/History.jsx'
import Library from './views/Library.jsx'
import People from './views/People.jsx'
import Settings from './views/Settings.jsx'
import DesktopOnboarding from './components/DesktopOnboarding.jsx'
import InterventionCard from './components/InterventionCard.jsx'
import { ModalHost, modal } from './components/Modal.jsx'
import TooltipHost from './components/TooltipHost.jsx'
import { IcLive, IcHistory, IcDoc, IcUsers, IcPanelL, IcX, IcSettings } from './components/Icons.jsx'

function beep(times) {
  try {
    const ac = new (window.AudioContext || window.webkitAudioContext)()
    for (let i = 0; i < times; i++) {
      const o = ac.createOscillator(), g = ac.createGain()
      o.connect(g); g.connect(ac.destination)
      o.frequency.value = 880; g.gain.value = 0.12
      o.start(ac.currentTime + i * 0.25); o.stop(ac.currentTime + i * 0.25 + 0.15)
    }
  } catch {}
}

const NAV = [
  ['live', IcLive], ['history', IcHistory], ['people', IcUsers], ['library', IcDoc],
]

function IvDock({ children, resetKey }) {
  const [pos, setPos] = useState(null)
  const drag = useRef(null)
  useEffect(() => { setPos(null) }, [resetKey])
  const onDown = (e) => {
    if (!e.target.closest('.iv-card .head') || e.target.closest('.x, .lang-toggle')) return
    const box = e.currentTarget.getBoundingClientRect()
    drag.current = { dx: e.clientX - box.left, dy: e.clientY - box.top, w: box.width }
    const move = (ev) => setPos({
      x: Math.max(0, Math.min(window.innerWidth - drag.current.w, ev.clientX - drag.current.dx)),
      y: Math.max(0, Math.min(window.innerHeight - 48, ev.clientY - drag.current.dy)),
    })
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
    e.preventDefault()
  }
  return (
    <div className={'iv-dock' + (pos ? ' moved' : '')} style={pos ? { left: pos.x, top: pos.y, right: 'auto', maxHeight: `calc(100vh - ${pos.y + 16}px)` } : undefined} onMouseDown={onDown}>
      {children}
    </div>
  )
}

export default function App() {
  const [desktopOnboarding, setDesktopOnboarding] = useState(window.__DEBAN_DESKTOP__ === true ? null : false)
  useEffect(() => {
    if (window.__DEBAN_DESKTOP__ === true) api('/desktop/onboarding').then(value => setDesktopOnboarding(!value?.completed))
  }, [])
  const [view, setView] = useState('live')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [bootError, setBootError] = useState(null)
  const [asrError, setAsrError] = useState('')
  const [starting, setStarting] = useState(false)
  const capture = useRef(null)
  const startingRef = useRef(false)
  const startMeetingRef = useRef(null)
  const toastTimer = useRef(null)
  const flash = (key) => {
    setToast(T(key))
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(''), 6000)
  }
  const [conn, setConn] = useState('connecting')
  const [mode, setMode] = useState('idle')
  const [llmOn, setLlmOn] = useState(false)
  const [ready, setReady] = useState(false)
  const [modelProg, setModelProg] = useState(null)
  useEffect(() => {
    if (ready) { setModelProg(null); return }
    const tick = () => api('/models/status').then((m) => { if (m) { setModelProg(m.total ? { done: m.done, total: m.total } : null); setReady(m.pipeline_ready); setAsrError(m.asr_error || '') } })
    tick(); const t = setInterval(tick, 2000); return () => clearInterval(t)
  }, [ready])
  const [micOn, setMicOn] = useState(true)
  const [autoEnd, setAutoEnd] = useState(true)
  const [rosterLocked, setRosterLocked] = useState(false)
  const [meetingId, setMeetingId] = useState(null)
  const [startedAt, setStartedAt] = useState(null)
  const [liveTitle, setLiveTitle] = useState('')
  const [titleEdit, setTitleEdit] = useState(null)
  const commitTitle = async () => {
    const v = (titleEdit || '').trim()
    setTitleEdit(null)
    if (!v || !meetingId) return
    await post(`/meeting/${meetingId}/rename`, { title: v })
    setLiveTitle(v)
  }
  const [toast, setToast] = useState('')
  const [rosterTick, setRosterTick] = useState(0)
  const [histTick, setHistTick] = useState(0)
  const [histOpenId, setHistOpenId] = useState(null)
  const [histOpenIv, setHistOpenIv] = useState(null)
  const [utts, setUtts] = useState([])
  const [suggests, setSuggests] = useState({})
  const [topics, setTopics] = useState([])
  const [ivs, setIvs] = useState([])
  const [currentIv, setCurrentIv] = useState(null)
  const [, forceRender] = useState(0)
  const liveRef = useRef({ armed: false })
  const preRef = useRef(new Set())

  const rerender = useCallback(() => forceRender((x) => x + 1), [])
  useEffect(() => onLangChange(rerender), [rerender])
  useEffect(() => {
    let timer
    const h = () => {
      setToast(T('apiError'))
      clearTimeout(timer)
      timer = setTimeout(() => setToast(''), 4000)
    }
    window.addEventListener('api-error', h)
    return () => { window.removeEventListener('api-error', h); clearTimeout(timer) }
  }, [])

  useEffect(() => {
    const es = new EventSource('/events')
    es.onopen = () => {
      setConn('ok')
      setUtts([]); setTopics([]); setIvs([])
      liveRef.current.armed = false
      setTimeout(() => { liveRef.current.armed = true }, 1500)
    }
    es.onerror = () => setConn('dead')
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data)
      switch (ev.type) {
        case 'fatal':
          setBootError({ code: ev.code, detail: ev.detail })
          break
        case 'models':
          setModelProg(ev.total ? { done: ev.done, total: ev.total } : null)
          break
        case 'state':
          if ('mode' in ev) setMode(ev.mode)
          if ('meeting_id' in ev) setMeetingId(ev.meeting_id || null)
          if ('started_at' in ev) setStartedAt(ev.started_at || null)
          if ('title' in ev) setLiveTitle(ev.title || '')
          if ('llm_on' in ev) setLlmOn(ev.llm_on !== false)
          if ('ready' in ev) setReady(ev.ready !== false && !ev.asr_loading)
          if (ev.boot_error) setBootError(ev.boot_error)
          if ('asr_error' in ev) setAsrError(ev.asr_error || '')
          if (ev.mode === 'idle' && capture.current) {
            capture.current.close()
            capture.current = null
            startMeetingRef.current = null
          }
          if ('mic' in ev) setMicOn(ev.mic !== false)
          if ('auto_end' in ev) setAutoEnd(ev.auto_end !== false)
          if ('roster_locked' in ev) setRosterLocked(!!ev.roster_locked)
          if (ev.mode === 'recording') setView((v) => (v === 'history' ? 'live' : v))
          break
        case 'meeting_done':
          setToast(T('archivedDone'))
          setTimeout(() => setToast(''), 4000)
          setHistTick((t) => t + 1)
          break
        case 'notice':
          if (ev.text === 'archiving') { setToast(T('archiving')); setTimeout(() => setToast(''), 5000) }
          if (ev.text === 'auto_ended') { setToast(T('autoEnded')); setTimeout(() => setToast(''), 8000) }
          if (ev.text === 'audio_lost') flash(window.__DEBAN_DESKTOP__ === true ? 'desktopAudioLost' : 'captureLost')
          break
        case 'reset':
          setUtts([]); setTopics([]); setIvs([]); setCurrentIv(null); setSuggests({})
          break
        case 'spk_suggest':
          setSuggests((m) => ({ ...m, [ev.spk]: { member: ev.member, name: ev.name } }))
          break
        case 'spk_suggest_clear':
          setSuggests((m) => { const n = { ...m }; delete n[ev.spk]; return n })
          break
        case 'utterance':
          setUtts((u) => [...u.slice(-400), { ...ev, uid: ev.uid, zh: '', pendingZh: ev.pending_tr !== false }])
          break
        case 'translation':
          setUtts((u) => u.map((x) => x.uid === ev.uid ? { ...x, zh: ev.text, pendingZh: false } : x))
          break
        case 'revise':
          setUtts((u) => u.map((x) => {
            const f = ev.fixes.find((f) => f.uid === x.uid)
            return f ? { ...x, text: f.text, zh: f.translation || x.zh, revised: true } : x
          }))
          break
        case 'topic':
          setTopics((t) => {
            const i = t.findIndex((x) => x.topic_id === ev.topic_id)
            if (i < 0) return [...t, ev]
            const next = [...t]; next[i] = ev; return next
          })
          break
        case 'intervention_pre': {
          if (ev.replay || !liveRef.current.armed) break
          const pre = { ...ev, iv_id: 'pre-' + ev.uid, pending: true }
          preRef.current.add(ev.uid)
          setIvs((l) => [pre, ...l.filter((x) => x.uid !== ev.uid)])
          setCurrentIv(pre)
          beep(3)
          break
        }
        case 'intervention': {
          const hadPre = preRef.current.delete(ev.uid)
          setIvs((l) => [ev, ...l.filter((x) => !(x.pending && x.uid === ev.uid))])
          setCurrentIv((c) => (c && c.pending && c.uid === ev.uid) ? ev : c)
          if (!hadPre && !ev.replay && ev.kind === 'RESPOND' && (ev.id === -1 || liveRef.current.armed)) {
            setCurrentIv(ev)
            beep(3)
          }
          break
        }
        case 'intervention_update':
          setIvs((l) => l.map((x) => x.iv_id === ev.iv_id ? { ...x, replies: ev.replies } : x))
          setCurrentIv((c) => (c && c.iv_id === ev.iv_id) ? { ...c, replies: ev.replies } : c)
          break
        case 'intervention_cancel':
          preRef.current.delete(ev.uid)
          setIvs((l) => l.filter((x) => !(x.pending && x.uid === ev.uid)))
          setCurrentIv((c) => (c && c.pending && c.uid === ev.uid) ? { ...c, cancelled: true, cancelReason: ev.reason || 'none' } : c)
          break
        case 'roster':
          setRosterTick((t) => t + 1)
          break
        case 'speaker':
          setUtts((u) => u.map((x) => x.spk === ev.spk ? { ...x, name: ev.name, named: true } : x))
          setSuggests((m) => { const n = { ...m }; delete n[ev.spk]; return n })
          break
        case 'utterance_delete':
          setUtts((u) => u.filter((x) => x.uid !== ev.uid))
          break
        case 'utterance_speaker':
          setUtts((u) => u.map((x) => x.uid === ev.uid ? { ...x, spk: ev.spk, name: ev.name, named: ev.named } : x))
          break
        case 'utterance_split':
          setUtts((u) => {
            const i = u.findIndex((x) => x.uid === ev.orig.uid)
            if (i < 0) return u
            const next = [...u]
            next[i] = { ...next[i], text: ev.orig.text, zh: ev.orig.zh, revised: false, pendingZh: false }
            next.splice(i + 1, 0, { ...ev.new, pendingZh: false })
            return next
          })
          break
        case 'speaker_merge':
          setUtts((u) => u.map((x) => x.spk === ev.from ? { ...x, spk: ev.to, name: ev.name, named: ev.named !== false } : x))
          setSuggests((m) => { const n = { ...m }; delete n[ev.from]; return n })
          break
        default:
          break
      }
    }
    return () => es.close()
  }, [])

  const onRenamed = (spk, name) =>
    setUtts((u) => u.map((x) => x.spk === spk ? { ...x, name, named: true } : x))

  const ctl = {
    starting,
    start: async (opts) => {
      if (startingRef.current || capture.current) return
      if (!ready) { flash(asrError ? 'modelFailed' : 'modelLoadingToast'); return }
      startingRef.current = true
      setStarting(true)
      let candidate
      try {
        candidate = window.__DEBAN_DESKTOP__ === true
          ? { enableMic: async () => {}, disableMic: () => {}, close: () => {},
              start: () => ({ enableMic: async () => {}, disableMic: () => {}, close: () => {}, stop: async () => {} }) }
          : await prepareCapture()
        const mic = !!opts.mic
        if (mic) {
          await candidate.enableMic(() => {
            if (capture.current) void post('/meeting/mic', { on: false })
          })
        }
        const result = await post('/meeting/start', opts)
        if (!result?.meeting_id) {
          candidate.close()
          if (result?.error === 'not_ready') setReady(false)
          flash(result?.error === 'busy' ? 'busyMeeting' : result?.error === 'not_ready' ? 'modelLoadingToast' : 'apiError')
          return
        }
        const mid = result.meeting_id
        startMeetingRef.current = mid
        capture.current = candidate.start(mid, () => {
          capture.current = null
          startMeetingRef.current = null
          void post(`/meeting/${mid}/capture-ended`, {}, { quiet: true })
        })
        setView('live')
      } catch (error) {
        candidate?.close()
        flash(['captureUnsupported', 'captureNoAudio', 'captureDenied'].includes(error.message) ? error.message : 'captureMicDenied')
      } finally {
        startingRef.current = false
        setStarting(false)
      }
    },
    mic: async (on) => {
      const active = capture.current
      if (!active) return
      try {
        if (on) await active.enableMic(() => void post('/meeting/mic', { on: false }))
        else active.disableMic()
        if (capture.current === active) await post('/meeting/mic', { on })
      } catch {
        flash('captureMicDenied')
      }
    },
    end: async () => {
      if (await modal.confirm({ title: T('end').replace('■ ', ''), message: T('endConfirm'), danger: true, okText: T('end').replace('■ ', '') })) {
        await capture.current?.stop()
        capture.current = null
        startMeetingRef.current = null
        await post('/meeting/end')
      }
    },
  }

  useEffect(() => {
    const closeCapture = () => {
      capture.current?.close()
      const mid = startMeetingRef.current
      if (mid) navigator.sendBeacon(`/api/meeting/${mid}/capture-ended`)
    }
    window.addEventListener('pagehide', closeCapture)
    return () => {
      window.removeEventListener('pagehide', closeCapture)
      closeCapture()
      clearTimeout(toastTimer.current)
    }
  }, [])

  const statusText = conn === 'dead' ? T('disconnected')
    : conn === 'connecting' ? T('connecting')
    : mode === 'recording' ? T('listening') : mode === 'paused' ? T('paused') : T('idle')
  const pillCls = conn === 'dead' || !ready ? 'warn' : mode === 'recording' ? 'rec' : ''

  const [navHidden, setNavHidden] = useState(() => { try { return localStorage.getItem('mc.navHidden') === '1' } catch { return false } })


  const [histListHidden, setHistListHidden] = useState(() => { try { return localStorage.getItem('mc.histListHidden') === '1' } catch { return false } })

  const toggleHistList = () => setHistListHidden((h) => { try { localStorage.setItem('mc.histListHidden', h ? '0' : '1') } catch {} return !h })
  const toggleNav = () => setNavHidden((h) => { try { localStorage.setItem('mc.navHidden', h ? '0' : '1') } catch {} return !h })

  if (bootError) return (
    <div className="startup-error"><div className="fatal-card">
      <div className="fatal-title">{T('fatalTitle')}</div>
      <p className="fatal-msg">{T(bootError.code === 'db_too_new' ? 'fatalDbTooNew' : bootError.code === 'db_error' ? 'fatalDbError' : 'fatalGeneric')}</p>
      <pre className="fatal-detail">{bootError.detail}</pre>
      <div className="fatal-actions">
        <button className="primary" onClick={() => window.location.reload()}>{T('fatalRetry')}</button>
      </div>
      <div className="hint">{T('fatalLogHint')}</div>
    </div></div>
  )
  if (desktopOnboarding === null) return null
  if (desktopOnboarding) return <DesktopOnboarding onDone={async () => {
    const result = await post('/desktop/onboarding', {})
    if (result?.completed) setDesktopOnboarding(false)
  }} />
  return (
    <div className={'shell' + (navHidden ? ' nav-collapsed' : '')}>
      <aside className="sidenav">
        <div className="logo">
          <div className="mark"><img src="/icon-mark.png" alt="Deban" /></div>
          <div className="logo-txt">
            <div className="name">Deban</div>
            <div className="tag">{T('tagline')}</div>
          </div>
        </div>
        <nav>
          {NAV.map(([v, Ic]) => (
            <button key={v} className={'navlink' + (view === v ? ' on' : '')} onClick={() => setView(v)} title={navHidden ? T(v) : undefined}>
              <Ic size={17} /><span className="lbl">{T(v)}</span>
              {v === 'live' && ivs.length > 0 && mode !== 'idle' ? <span className="badge">{ivs.length}</span> : null}
            </button>
          ))}
        </nav>
        <div className="nav-foot">
          <button className="navlink" onClick={() => setSettingsOpen(true)} aria-label={T('settings')} title={T('settings')}>
            <IcSettings size={17} /><span className="lbl">{T('settings')}</span>
          </button>
          <button className="nav-toggle" onClick={toggleNav} title={navHidden ? T('showNav') : T('hideNav')} aria-label={navHidden ? T('showNav') : T('hideNav')}>
            <IcPanelL size={17} />
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="pagebar">
          {(() => {
            const live = view === 'live' && mode !== 'idle' && startedAt
            const auto = startedAt ? `${T('mtgWord')} ${(() => { const d = new Date(startedAt * 1000); const p = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}` })()}` : ''
            const shown = live ? (liveTitle || auto) : T(view)
            if (live && titleEdit !== null) {
              return <input className="title-edit" autoFocus value={titleEdit} maxLength={40}
                            onChange={(e) => setTitleEdit(e.target.value)} onBlur={commitTitle}
                            onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitTitle(); if (e.key === 'Escape') setTitleEdit(null) }} />
            }
            return <h2 className={live ? 'editable' : ''} title={live ? T('renameMtgTip') : undefined}
                       onClick={live ? () => setTitleEdit(shown) : undefined}>{shown}</h2>
          })()}
          {view === 'history' && (
            <button className="nav-toggle history-list-toggle" onClick={toggleHistList} title={histListHidden ? T('showList') : T('hideList')}
                    aria-label={histListHidden ? T('showList') : T('hideList')}>
              <IcPanelL size={17} />
            </button>
          )}
          <span style={{ flex: 1 }} />
          <span className={'statuspill ' + pillCls}>
            <span className="dot" />{!ready && conn === 'ok' ? T(asrError ? 'modelFailed' : 'modelLoading') : statusText}
            {!ready && modelProg && <span className="pill-bar" title={`${Math.round(modelProg.done / 1e6)} / ${Math.round(modelProg.total / 1e6)} MB`}><span style={{ width: `${Math.min(100, Math.round(modelProg.done / modelProg.total * 100))}%` }} /></span>}
          </span>
        </header>
        <main className="view">
          {asrError && <div className="live-notice">{T('modelFailed')}
            {' '}<button onClick={async () => { setAsrError(''); await post('/models/prepare', {}) }}>{T('fatalRetry')}</button>
          </div>}
          {view === 'live' && (
            <Live mode={mode} startedAt={startedAt} meetingId={meetingId} ctl={ctl} utts={utts}
                  rosterTick={rosterTick} micOn={micOn} autoEnd={autoEnd} rosterLocked={rosterLocked} suggests={suggests} llmOn={llmOn}
                  topics={topics} ivs={ivs} onRenamed={onRenamed}
                  goHistory={(id) => { setHistOpenId(typeof id === 'number' ? id : null); setView('history') }}
                  showIv={(iv) => setCurrentIv(iv)} />
          )}
          {view === 'history' && <History onRenamed={onRenamed} mode={mode} histTick={histTick} openId={histOpenId} onOpened={() => setHistOpenId(null)} openIv={histOpenIv} onOpenedIv={() => setHistOpenIv(null)} listHidden={histListHidden} />}
          {view === 'library' && <Library />}
          {view === 'people' && <People />}
          {settingsOpen && (
            <>
              <div className="iv-backdrop" style={{ zIndex: 120 }} onClick={() => setSettingsOpen(false)} />
              <div className="modal settings-dialog" onKeyDown={(e) => { if (e.key === 'Escape') setSettingsOpen(false) }}>
                <div className="m-title settings-dialog-title">{T('settings')}<button className="drawer-x" onClick={() => setSettingsOpen(false)} aria-label={T('close')}><IcX size={15} /></button></div>
                <div className="m-body settings-body"><Settings mode={mode} llmOn={llmOn} /></div>
              </div>
            </>
          )}
        </main>
      </div>
      {toast && <div className="toast">{toast}</div>}
      <ModalHost />
      <TooltipHost />
      {currentIv && view === 'live' && (
        <IvDock resetKey={currentIv.iv_id}>
          <InterventionCard iv={currentIv} utts={utts} onClose={() => setCurrentIv(null)} />
        </IvDock>
      )}
    </div>
  )
}
