import { useEffect, useState } from 'react'
import { T } from '../i18n.js'
import { api, post, del } from '../api.js'
import { modal } from '../components/Modal.jsx'
import { IcUsers, IcEdit, IcTrash, IcMic } from '../components/Icons.jsx'
import PillSelect from '../components/PillSelect.jsx'

export default function People() {
  const [people, setPeople] = useState([])
  const [nameInput, setNameInput] = useState('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('name')
  const [me, setMe] = useState(null)
  const reload = () => { api('/speakers').then((l) => setPeople(l || [])); api('/me').then((m) => setMe(m && !m.error ? m : null)) }
  useEffect(() => { reload() }, [])

  const addPerson = async () => {
    const r = await modal.form({
      title: T('preRegister'),
      fields: [
        { key: 'name', label: T('profileName'), value: '', maxLength: 30 },
        { key: 'note', label: T('memberNote'), value: '', maxLength: 60 },
      ],
    })
    if (!r || !r.name || !r.name.trim()) return
    await post('/speakers', { name: r.name.trim(), note: (r.note || '').trim() })
    reload()
  }
  const rename = async (p) => {
    const r = await modal.form({
      title: T('editMember'),
      fields: [
        { key: 'name', label: T('profileName'), value: p.name, maxLength: 30 },
        { key: 'note', label: T('memberNote'), value: p.note || '', maxLength: 60 },
      ],
    })
    if (!r || !r.name || !r.name.trim()) return
    await post(`/speaker/${p.id}/rename`, { name: r.name.trim(), note: (r.note || '').trim() })
    reload()
  }
  const remove = async (p) => {
    if (!await modal.confirm({
      title: T('del'),
      message: T(p.has_voice ? 'personDelVoiceConfirm' : 'personDelConfirm', p.name),
      danger: true })) return
    await del(`/speaker/${p.id}`)
    reload()
  }

  const shown = people
    .filter((p) => !q.trim() || p.name.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => {
      if (sort === 'recent') return (b.last_ts || 0) - (a.last_ts || 0)
      if (sort === 'utts') return (b.n_utts || 0) - (a.n_utts || 0)
      return a.name.localeCompare(b.name, 'ja')
    })

  return (
    <div className="library">
      <div className="lib-actions">
        <input type="text" value={q} placeholder={T('searchPeoplePh')} style={{ flex: 1 }}
               onChange={(e) => setQ(e.target.value)} />
        <PillSelect prefix="↓ " value={sort} onChange={setSort}
                    options={[
                      { value: 'name', label: T('sortName') },
                      { value: 'recent', label: T('sortRecent') },
                      { value: 'utts', label: T('sortUtts') },
                    ]} />
        <button className="primary" onClick={addPerson}>{T('preRegister')}</button>
      </div>
      <div className="card lib-list">
        {me && (!q.trim() || (me.name || T('me')).toLowerCase().includes(q.trim().toLowerCase())) && (
          <div className="lib-row me" title={T('meHint')}>
            <div className="ic me"><IcMic size={16} /></div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="t">{me.name || T('me')} <span className="pill gray me-tag">{T('meTag')}</span></div>
              <div className="s">
                {me.mic_enabled
                  ? <span className="pill green"><IcMic size={10} /> {T('micOn')}</span>
                  : <span className="pill amber">{T('micOff')}</span>}
                {me.n_utts ? <> · {me.n_utts}{T('utt')}</> : null}
                {me.last_ts ? <> · {new Date(me.last_ts * 1000).toLocaleDateString()}</> : null}
              </div>
              <div className="s note">{T('meHint')}</div>
            </div>
          </div>
        )}
        {shown.length === 0 && !me && <div className="empty">{T('peopleEmpty')}</div>}
        {shown.map((p) => (
          <div className="lib-row" key={p.id}>
            <div className="ic"><IcUsers size={16} /></div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="t">{p.name}</div>
              {p.note ? <div className="s note">{p.note}</div> : null}
              <div className="s">
                {p.has_voice
                  ? <span className="pill green"><IcMic size={10} /> {T('voiceKnown')}</span>
                  : <span className="pill gray">◌ {T('voiceUnknown')}</span>}
                {p.n_utts ? <> · {p.n_utts}{T('utt')}</> : null}
                {p.last_ts ? <> · {new Date(p.last_ts * 1000).toLocaleDateString()}</> : null}
              </div>
            </div>
            <span className="lib-acts">
              <span className="lib-act" title={T('editMember')} onClick={() => rename(p)}><IcEdit size={15} /></span>
              <span className="lib-act danger" title={T('del')} onClick={() => remove(p)}><IcTrash size={15} /></span>
            </span>
          </div>
        ))}
      </div>
      <div className="hint" style={{ marginTop: 12 }}>{T('peopleHint')}</div>
    </div>
  )
}
