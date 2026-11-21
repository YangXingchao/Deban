import { useEffect, useState } from 'react'
import { T } from '../i18n.js'
import { api } from '../api.js'
import StartPanel from '../components/StartPanel.jsx'
import { IcLive, IcMsg, IcBell, IcClockMini } from '../components/Icons.jsx'

export default function Home({ mode, ctl, goHistory, llmOn }) {
  const [list, setList] = useState([])
  useEffect(() => { api('/history').then((r) => setList(r || [])) }, [mode])

  const totalMin = Math.round(list.reduce((s, m) => s + (m.duration || 0), 0) / 60)
  const totalResp = list.reduce((s, m) => s + (m.responds || 0), 0)

  return (
    <div className="home">
      <div className="stat-grid">
        <div className="card stat">
          <div className="k"><IcMsg size={14} />{T('statMeetings')}</div>
          <div className="v">{list.length}</div>
        </div>
        <div className="card stat">
          <div className="k"><IcClockMini size={14} />{T('statMinutes')}</div>
          <div className="v">{totalMin}<small>{T('minUnit')}</small></div>
        </div>
        <div className="card stat">
          <div className="k"><IcBell size={14} />{T('statResponds')}</div>
          <div className="v">{totalResp}</div>
        </div>
      </div>
      <div className="home-grid">
        <div className="card start-card">
          <h3><IcLive size={15} />{mode === 'idle' ? T('startMeeting') : T('listening')}</h3>
          {mode === 'idle' ? <StartPanel onStart={ctl.start} starting={ctl.starting} llmOn={llmOn} /> : <div className="empty">{T('listening')}…</div>}
        </div>
        <div className="card recent-card">
          <h3>{T('recentMeetings')}</h3>
          {list.length === 0 && <div className="empty">{T('noMeetings')}</div>}
          {list.slice(0, 7).map((m) => (
            <div className="mtg-row" key={m.id} onClick={() => goHistory(m.id)}>
              <div className="ic"><IcLive size={16} /></div>
              <div style={{ minWidth: 0 }}>
                <div className="t" title={[...(m.me ? [m.me.name || T('me')] : []), ...(m.participants || [])].join('、')}>{m.title || `#${m.id}`}</div>
                <div className="s">
                  {new Date(m.started_at * 1000).toLocaleString()} · {m.utterances}{T('utt')}
                </div>
              </div>
              {m.responds ? <span className="pill red" style={{ marginLeft: 'auto' }}>{m.responds}</span> : null}
            </div>
          ))}
        </div>
      </div>
      <div className="tagline">{T('tagline')}</div>
    </div>
  )
}
