import { useEffect, useState } from 'react'
import { T, getUiLang, setUiLang } from '../i18n.js'
import { api, post, put, del } from '../api.js'
import InfoTip from '../components/InfoTip.jsx'
import TrashCard from '../components/TrashCard.jsx'
import AISettings from '../components/AISettings.jsx'

function useSaved() {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); setTimeout(() => setSaved(false), 1500) }
  return [saved, flash]
}

function Lock({ live, children }) {
  return live
    ? <div className="lock-wrap" title={T('lockedInMeeting')}><div className="lock-inner">{children}</div></div>
    : children
}

function Switch({ checked, onChange }) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} onChange={onChange} />
      <span className="tr" />
    </label>
  )
}

export default function Settings({ mode, llmOn }) {
  const live = mode && mode !== 'idle'
  const [cfg, setCfg] = useState(null)
  const [prof, setProf] = useState(null)
  const [terms, setTerms] = useState('')
  const [savedCfg, flashCfg] = useSaved()
  const [savedProf, flashProf] = useSaved()
  const [savedGl, flashGl] = useSaved()
  const [tab, setTab] = useState('general')
  useEffect(() => {
    api('/settings').then(setCfg)
    api('/profile').then(setProf)
    api('/glossary').then((g) => setTerms((g || []).join('\n')))
  }, [])

  const saveCfg = async (patch) => {
    const next = await post('/settings', patch)
    setCfg(next); flashCfg()
  }
  const saveProf = async () => {
    await put('/profile', prof); flashProf()
  }
  const saveGlossary = async () => {
    await put('/glossary', terms.split('\n').map((s) => s.trim()).filter(Boolean)); flashGl()
  }
  const [nameInput, setNameInput] = useState('')
  const addName = () => {
    const v = nameInput.trim()
    if (!v) return
    const cur = cfg.my_names || []
    if (!cur.includes(v)) saveCfg({ my_names: [...cur, v] })
    setNameInput('')
  }
  const rmName = (v) => saveCfg({ my_names: (cfg.my_names || []).filter((x) => x !== v) })

  const [expInput, setExpInput] = useState('')
  const addExp = () => {
    const v = expInput.trim()
    if (!v) return
    const cur = prof.expertise || []
    if (!cur.includes(v)) setProf({ ...prof, expertise: [...cur, v] })
    setExpInput('')
  }
  const rmExp = (v) => setProf({ ...prof, expertise: (prof.expertise || []).filter((x) => x !== v) })

  if (!cfg || !prof) return <div className="empty">…</div>
  return (
    <div className="settings">
      <div className="subtabs">
        {[['general', T('setTabGeneral')], ['ai', T('aiConnection')], ['personal', T('setTabPersonal')], ['data', T('setTabData')]].map(([v, lb]) => (
          <button key={v} className={'subtab' + (tab === v ? ' on' : '')} onClick={() => setTab(v)}>{lb}</button>
        ))}
      </div>

      {live && <div className="lock-banner">{T('lockBanner')}</div>}
      {tab === 'general' && (
      <>
      <div className="card set-card">
        <div className="form-row">
          <label htmlFor="ui-language">{T('setSecLang')}</label>
          <select id="ui-language" value={getUiLang()} onChange={(event) => setUiLang(event.target.value)}>
            <option value="en">English</option><option value="ja">日本語</option>
            <option value="zh">简体中文</option><option value="zh-Hant">繁體中文</option>
          </select>
        </div>
      </div>
      <div className="card set-card">
      <h3>{T('setSecRecord')}</h3>
      <Lock live={live}>
      <div className="toggle-row">
        <Switch checked={!!cfg.record_audio} onChange={(e) => saveCfg({ record_audio: e.target.checked })} />
        {T('record')}<InfoTip>{T('perMeetingHint')}</InfoTip>
      </div>
      <div className="toggle-row">
        <Switch checked={!!cfg.mic_enabled} onChange={(e) => saveCfg({ mic_enabled: e.target.checked })} />
        {T('micEnabled')}<InfoTip>{T('micHint')}</InfoTip>
      </div>
      </Lock>
      </div>

      <div className="card set-card">
      <h3>{T('setSecAlerts')}</h3>
      <Lock live={live}><fieldset disabled={!llmOn} className="ai-feature-controls" title={!llmOn ? T('aiUnavailable') : undefined}><div className="form-row" style={{ alignItems: 'flex-start' }}>
        <label>{T('alertTypes')}<InfoTip>{T('alertTypesHint')}</InfoTip></label>
        <div className="alert-controls">
          <div className="toggle-row">
            <Switch checked={cfg.alerts_enabled !== false} onChange={(e) => saveCfg({ alerts_enabled: e.target.checked })} />
            {T('alertsEnabled')}
          </div>
          {cfg.alerts_enabled !== false && <div className="alert-toggles">
            {[['alert_respond', 'respond', T('yourTurn')], ['alert_consider', 'consider', T('consider')], ['alert_contribute', 'contribute', T('kindCONTRIBUTE')], ['alert_ask', 'ask', T('kindASK')]].map(([k, tone, lb]) => (
              <div className="toggle-row" key={k}>
                <Switch checked={cfg[k] !== false} onChange={(e) => saveCfg({ [k]: e.target.checked })} />
                <span className={'alert-dot ' + tone} />{lb.replace(/^[🔴🟡🔵🟢]\s*/, '')}
              </div>
            ))}
          </div>}
        </div>
      </div></fieldset></Lock>
      {!llmOn && <div className="hint">{T('aiUnavailable')}</div>}
      </div>

      <div className="card set-card">
      <h3>{T('setSecMeeting')}</h3>
      <Lock live={live}><div className="form-row">
        <label>{T('autoEnd')}<InfoTip>{T('autoEndHint')}</InfoTip></label>
        <select value={String(cfg.auto_end_min ?? 10)} onChange={(e) => saveCfg({ auto_end_min: Number(e.target.value) })}>
          <option value="0">{T('autoEndOff')}</option>
          {[5, 10, 15, 30].map((n) => <option key={n} value={String(n)}>{T('autoEndMin', n)}</option>)}
        </select>
      </div></Lock>
      </div>
      </>
      )}

      {tab === 'ai' && <AISettings live={live} />}

      {tab === 'personal' && (
      <>
      <div className="card set-card">
      <h3>{T('profile')}{(savedProf || savedCfg) && <span className="saved-tip">{T('settingsSaved')}</span>}</h3>
      <Lock live={live}>
      <div className="form-row">
        <label>{T('selfName')}</label>
        <input type="text" defaultValue={cfg.self_name} maxLength={20}
               onBlur={(e) => saveCfg({ self_name: e.target.value.trim() })} placeholder={T('me')} />
      </div>
      <div className="form-row" style={{ alignItems: 'flex-start' }}>
        <label style={{ paddingTop: 8 }}>{T('myNames')}</label>
        <div style={{ flex: 1 }}>
          {(cfg.my_names || []).length > 0 && <div className="chip-select" style={{ marginBottom: 8 }}>
            {(cfg.my_names || []).map((n) => (
              <span key={n} className="chip on">{n}
                <span className="chip-x" onClick={() => rmName(n)}>✕</span>
              </span>
            ))}
          </div>}
          <input type="text" value={nameInput} placeholder={T('myNamesPh')} maxLength={20}
                 onChange={(e) => setNameInput(e.target.value)}
                 onKeyDown={(e) => { if ((e.key === 'Enter' && !e.nativeEvent.isComposing) || e.key === ',') { e.preventDefault(); addName() } }}
                 onBlur={addName} style={{ width: '100%' }} />
          <div className="hint" style={{ marginTop: 6 }}>{T('myNamesHint')}</div>
        </div>
      </div>
      <div className="form-row" style={{ alignItems: 'flex-start' }}>
        <label style={{ paddingTop: 8 }}>{T('profileExpertise')}</label>
        <div style={{ flex: 1 }}>
          {(prof.expertise || []).length > 0 && <div className="chip-select" style={{ marginBottom: 8 }}>
            {(prof.expertise || []).map((v) => (
              <span key={v} className="chip on">{v}
                <span className="chip-x" onClick={() => rmExp(v)}>✕</span>
              </span>
            ))}
          </div>}
          <input type="text" value={expInput} placeholder={T('expPh')} style={{ width: '100%' }} maxLength={30}
                 onChange={(e) => setExpInput(e.target.value)}
                 onKeyDown={(e) => { if ((e.key === 'Enter' && !e.nativeEvent.isComposing) || e.key === ',') { e.preventDefault(); addExp() } }}
                 onBlur={addExp} />
        </div>
      </div>
      <button className="primary" onClick={saveProf}>{T('save')}</button>
      </Lock>
      </div>
      <div className="card set-card">
      <h3>{T('glossary')}{savedGl && <span className="saved-tip">{T('settingsSaved')}</span>}</h3>
      <div className="hint">{T('glossaryHint')}</div>
      <Lock live={live}>
      <textarea rows={6} value={terms} onChange={(e) => setTerms(e.target.value)} />
      <div style={{ marginTop: 10 }}><button className="primary" onClick={saveGlossary}>{T('save')}</button></div>
      </Lock>
      </div>
      </>
      )}

      {tab === 'data' && <Lock live={live}><DataCard /></Lock>}
      {tab === 'data' && <Lock live={live}><TrashCard /></Lock>}
    </div>
  )
}


function DataCard() {
  const [about, setAbout] = useState(null)
  const [vp, setVp] = useState(true)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  useEffect(() => { api('/about').then(setAbout) }, [])
  const downloadArchive = () => {
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    const filename = `deban-all-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.deban`
    const a = document.createElement('a')
    a.href = `/api/export?voiceprints=${vp ? 1 : 0}&filename=${encodeURIComponent(filename)}`
    a.download = filename
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }
  const doImport = async (file) => {
    if (!file) return
    setBusy(true); setResult(null)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await fetch(`/api/import?voiceprints=${vp ? 1 : 0}`, { method: 'POST', body: fd })
      const j = await r.json()
      setResult(j)
    } catch (e) { setResult({ error: String(e) }) }
    setBusy(false)
  }
  return (
    <div className="card set-card">
      <h3>{T('dataTitle')}</h3>
      {about && (
        <div className="hint" style={{ marginBottom: 10 }}>
          {T('dataVersions', about.app_version, about.db_version, about.export_version)} · {about.data_dir}
        </div>
      )}
      <div className="form-row">
        <label>{T('exportAll')}</label>
        <button className="primary" onClick={downloadArchive}>{T('exportBtn')}</button>
        <label className="chk"><input type="checkbox" checked={vp} onChange={(e) => setVp(e.target.checked)} /> {T('withVoiceprints')}</label>
      </div>
      <div className="form-row">
        <label>{T('importArchive')}</label>
        <input type="file" accept=".deban,.zip" disabled={busy} onChange={(e) => { doImport(e.target.files[0]); e.target.value = '' }} />
        {busy ? <span className="hint">{T('importing')}</span> : null}
      </div>
      {result && (
        <div className={'hint ' + (result.error ? 'warn' : 'ok')}>
          {result.error === 'busy' ? T('importBusy') : result.error ? `${T('importFailed')}: ${result.error}`
            : T('importDone', result.meetings, result.skipped, result.utterances, result.speakers_new, result.contexts_new, result.recordings)}
          {result.errors && result.errors.length ? <div>{result.errors.join(' / ')}</div> : null}
        </div>
      )}
      <div className="hint" style={{ marginTop: 8 }}>{T('dataHint')}</div>
    </div>
  )
}
