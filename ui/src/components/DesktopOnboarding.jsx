import { useEffect, useState } from 'react'
import { T } from '../i18n.js'
import { api, post } from '../api.js'

export default function DesktopOnboarding({ onDone }) {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const bridge = window.webkit?.messageHandlers?.debanPermissions
  const refresh = async () => {
    try {
      const value = bridge ? await bridge.postMessage('status') : await api('/desktop/permissions', { quiet: true })
      if (value) setStatus(value)
    } catch { setError(true) }
  }
  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [])
  const request = async (kind) => {
    setBusy(true); setError(false)
    try {
      const value = bridge ? await bridge.postMessage(kind) : await post('/desktop/permissions', { kind }, { quiet: true })
      if (value) setStatus(value)
      else setError(true)
    } catch { setError(true) }
    finally { setBusy(false) }
  }
  return <div className="desktop-onboarding"><div className="card desktop-onboarding-card">
    <h2>{T('desktopWelcome')}</h2>
    <p>{T('desktopPermissionIntro')}</p>
    {status && status.screen !== null && <section><h3>{T('desktopScreen')}</h3><p>{T('desktopScreenHint')}</p>
      <button disabled={busy || status?.screen === true} onClick={() => request('screen')}>
        {status?.screen ? T('desktopGranted') : T('desktopAllow')}
      </button></section>}
    <section><h3>{T('desktopMic')}</h3><p>{T('desktopMicHint')}</p>
      <button disabled={busy || status?.mic === true} onClick={() => request('mic')}>
        {status?.mic ? T('desktopGranted') : T('desktopAllow')}
      </button></section>
    {error && <p role="alert">{T('desktopPermissionError')}</p>}
    <p className="hint">{T('desktopPermissionLater')}</p>
    <button className="primary" disabled={busy} onClick={onDone}>{T('desktopContinue')}</button>
  </div></div>
}
