import { useEffect, useRef, useState } from 'react'
import { T } from '../i18n.js'
import { api, post } from '../api.js'

export default function DesktopOnboarding({ onDone }) {
  const [perm, setPerm] = useState(null)
  const [models, setModels] = useState(null)
  const [names, setNames] = useState([])
  const [nameInput, setNameInput] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(false)
  const [screenRequested, setScreenRequested] = useState(false)
  const [micSettingsOpened, setMicSettingsOpened] = useState(false)
  const permissionActive = useRef(false)
  const permissionReturnedAt = useRef(0)
  const finishArmedAt = useRef(0)
  const bridge = window.webkit?.messageHandlers?.debanPermissions

  useEffect(() => {
    api('/settings').then(cfg => { if (cfg) setNames(cfg.my_names || []) })
    const refresh = async () => {
      try {
        const value = bridge ? await bridge.postMessage('status') : await api('/desktop/permissions', { quiet: true })
        if (value) setPerm(value)
      } catch { setError(true) }
    }
    const refreshModels = () => api('/models/status', { quiet: true }).then(value => { if (value) setModels(value) })
    refresh(); refreshModels()
    const permissionTimer = setInterval(refresh, 3000)
    const modelTimer = setInterval(refreshModels, 1000)
    return () => { clearInterval(permissionTimer); clearInterval(modelTimer) }
  }, [])

  const request = async kind => {
    permissionActive.current = true; setBusy(kind); setError(false)
    if (kind === 'screen') setScreenRequested(true)
    try {
      const value = bridge ? await bridge.postMessage(kind) : await post('/desktop/permissions', { kind }, { quiet: true })
      if (value?.settingsOpened) setMicSettingsOpened(true)
      if (value) setPerm(value); else setError(true)
    } catch { setError(true) }
    finally { permissionReturnedAt.current = Date.now(); permissionActive.current = false; setBusy('') }
  }
  const saveNames = async next => {
    const previous = names
    setNames(next)
    try { if (!await post('/settings', { my_names: next })) { setNames(previous); setError(true) } }
    catch { setNames(previous); setError(true) }
  }
  const addName = () => {
    const value = nameInput.trim()
    if (value && !names.includes(value)) saveNames([...names, value])
    setNameInput('')
  }
  const armFinish = e => {
    if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return
    finishArmedAt.current = Date.now()
  }
  const finish = async () => {
    const now = Date.now()
    if (now - finishArmedAt.current >= 1200 || permissionActive.current || now - permissionReturnedAt.current < 2500) return
    finishArmedAt.current = 0
    if (bridge) await bridge.postMessage('onboardingDone').catch(() => {})
    onDone()
  }
  const screenRelevant = perm && perm.screen !== null
  const screenOk = !!perm?.screen
  const micOk = !!perm?.mic
  const readyValues = Object.values(models?.ready || {})
  const modelOk = readyValues.length > 0 && readyValues.every(Boolean)
  const allOk = (!screenRelevant || screenOk) && names.length > 0 && modelOk
  const pct = models?.total ? Math.min(100, Math.round(models.done / models.total * 100)) : 0
  const mb = n => `${Math.round((n || 0) / 1e6)} MB`
  return <div className="onboard"><div className="ob-card">
    <div className="ob-head"><div className="mark"><img src="/icon-mark.png" alt="Deban" /></div><div>
      <div className="ob-title">{T('desktopWelcome')}</div><div className="ob-sub">{T('obSub')}</div>
    </div></div>
    {screenRelevant && <Step n={1} ok={screenOk} title={T('desktopScreen')} desc={T('desktopScreenHint')}>
      <button className="primary" disabled={!!busy} onClick={() => request(bridge && screenRequested ? 'screenSettings' : 'screen')}>
        {busy ? T('obWaiting') : bridge && screenRequested ? T('desktopOpenSettings') : T('desktopAllow')}
      </button>
      {screenRequested && <div className="ob-hint" role="status">{T('desktopScreenRequested')}</div>}
    </Step>}
    <Step n={screenRelevant ? 2 : 1} ok={micOk} optional title={T('desktopMic')} desc={T('desktopMicHint')}>
      <button disabled={!!busy} onClick={() => request(bridge && perm?.micDenied ? 'micSettings' : 'mic')}>
        {busy ? T('obWaiting') : (bridge && perm?.micDenied) || micSettingsOpened ? T('desktopOpenSettings') : T('desktopAllow')}
      </button>
      {micSettingsOpened && <div className="ob-hint" role="status">{T('desktopWindowsMicSettings')}</div>}
    </Step>
    <Step n={screenRelevant ? 3 : 2} title={T('myNames')} desc={T('onboardNamesDesc')}>
      <div className="ob-name-list chip-select">{names.map(name => <span className="chip on" key={name}>{name}
        <button className="chip-x" aria-label={`${T('obRemove')} ${name}`} onClick={() => saveNames(names.filter(n => n !== name))}>✕</button>
      </span>)}</div>
      <div className="ob-row"><input value={nameInput} placeholder={T('myNamesPh')} maxLength={20}
        onChange={e => setNameInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); addName() } }} />
        <button onClick={addName} disabled={!nameInput.trim()}>{T('add')}</button></div>
    </Step>
    <Step n={screenRelevant ? 4 : 3} ok={modelOk} title={T('obModel')} desc={T('obModelDesc')}>
      <div className="ob-model"><div className="ob-bar"><div className="ob-bar-in" style={{ width: `${pct}%` }} /></div>
        <div className="hint">{models?.error || models?.asr_error ? T('obModelErr') : models?.total ? `${T('obModelDl')} ${mb(models.done)} / ${mb(models.total)}` : T('obModelWait')}</div>
        {(models?.error || models?.asr_error) && <button onClick={() => post('/models/prepare', {})}>{T('fatalRetry')}</button>}
      </div>
    </Step>
    {error && <p role="alert">{T('desktopPermissionError')}</p>}
    <div className="ob-foot"><button className="ghost" onPointerDown={armFinish} onKeyDown={armFinish} onClick={finish}>{T('obSkip')}</button>
      <button className={allOk ? 'primary' : ''} onPointerDown={armFinish} onKeyDown={armFinish} onClick={finish}>{allOk ? T('obStart') : T('obLater')}</button></div>
  </div></div>
}

function Step({ n, ok, optional, title, desc, children }) {
  return <div className={'ob-step' + (ok ? ' ok' : '')}><div className="ob-n">{ok ? '✓' : n}</div><div className="ob-body">
    <div className="ob-t">{title}{optional && <span className="ob-opt">{T('obOptional')}</span>}</div>
    <div className="ob-d">{desc}</div>{!ok && children && <div className="ob-act">{children}</div>}
  </div></div>
}
