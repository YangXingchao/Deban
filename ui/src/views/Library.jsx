import { useEffect, useRef, useState } from 'react'
import { T } from '../i18n.js'
import { api, post, put, del, upload } from '../api.js'
import { modal } from '../components/Modal.jsx'
import { IcDoc, IcUpload, IcEdit, IcTrash, IcDownload } from '../components/Icons.jsx'
import PillSelect from '../components/PillSelect.jsx'
import DocViewer from '../components/DocViewer.jsx'

export default function Library() {
  const [ctxs, setCtxs] = useState([])
  const [q, setQ] = useState('')
  const [preview, setPreview] = useState(null)
  const [sort, setSort] = useState('newest')
  const fileRef = useRef(null)
  const reload = (query = q) => api('/context' + (query.trim() ? `?q=${encodeURIComponent(query)}` : '')).then((l) => setCtxs(l || []))
  useEffect(() => { reload('') }, [])
  useEffect(() => {
    const t = setTimeout(() => reload(), 300)
    return () => clearTimeout(t)
  }, [q])

  const shown = [...ctxs].sort((a, b) => {
    if (sort === 'name') return a.name.localeCompare(b.name, 'ja')
    if (sort === 'type') return a.type.localeCompare(b.type) || b.created_at - a.created_at
    return b.created_at - a.created_at
  })

  const doUpload = async (f) => {
    if (!f) return
    if (f.size > 10 * 1024 * 1024) { await modal.confirm({ message: T('uploadTooLarge'), okText: T('ok') }); return }
    const r = await upload('/context', f)
    if (r && r.error) { await modal.confirm({ message: T(r.error === 'too large' ? 'uploadTooLarge' : 'uploadNoText'), okText: T('ok') }); return }
    reload()
  }
  const addText = async () => {
    const r = await modal.form({
      title: T('addText').replace('＋ ', ''),
      fields: [
        { key: 'name', label: T('textName'), value: '', maxLength: 60 },
        { key: 'text', label: T('textContent'), value: '', multiline: true, rows: 8, maxLength: 20000 },
      ],
    })
    if (!r || !r.text.trim()) return
    await post('/context/text', { name: r.name, text: r.text })
    reload()
  }
  const editText = async (c) => {
    const cur = await api(`/context/${c.id}/text`)
    if (!cur || cur.error) return
    const r = await modal.form({
      title: T('edit'),
      fields: [
        { key: 'name', label: T('textName'), value: cur.name },
        { key: 'text', label: T('textContent'), value: cur.text, multiline: true },
      ],
    })
    if (!r || !r.text.trim()) return
    await put(`/context/${c.id}`, { name: r.name, text: r.text })
    reload()
  }
  const doDelete = async (c) => {
    if (!await modal.confirm({ title: T('del'), message: T('ctxDelConfirm', c.name), danger: true })) return
    await del(`/context/${c.id}`)
    reload()
  }

  return (
    <div className="library">
      <div className="lib-actions">
        <input type="text" value={q} placeholder={T('searchCtxPh')} style={{ flex: 1 }}
               onChange={(e) => setQ(e.target.value)} />
        <PillSelect prefix="↓ " value={sort} onChange={setSort}
                    options={[
                      { value: 'newest', label: T('sortNewest') },
                      { value: 'name', label: T('sortName') },
                      { value: 'type', label: T('sortType') },
                    ]} />
      </div>
      <div className="lib-actions">
        <input ref={fileRef} type="file" style={{ display: 'none' }}
               accept=".pdf,.md,.markdown,.txt,.docx,.pptx"
               onChange={(e) => { doUpload(e.target.files[0]); e.target.value = '' }} />
        <button className="primary" onClick={() => fileRef.current.click()}><IcUpload size={14} /> {T('uploadFile').replace('＋ ', '')}</button>
        <button onClick={addText}><IcEdit size={14} /> {T('addText').replace('＋ ', '')}</button>
      </div>
      <div className="card lib-list">
        {shown.length === 0 && <div className="empty">{T('libEmpty')}</div>}
        {shown.map((c) => (
          <div className="lib-row clickable" key={c.id}>
            <div className="ic" onClick={() => setPreview(c)}><IcDoc size={16} /></div>
            <div style={{ minWidth: 0, flex: 1, cursor: 'pointer' }} onClick={() => setPreview(c)}>
              <div className="t">{c.name}</div>
              <div className="s">
                <span className="pill gray">{c.type}</span> · {new Date(c.created_at * 1000).toLocaleDateString()}
              </div>
            </div>
            <span className="lib-acts">
              {c.type === 'text' && (
                <span className="lib-act" title={T('edit')} onClick={() => editText(c)}><IcEdit size={15} /></span>
              )}
              <a className="lib-act" title={T('download')} href={`/api/context/${c.id}/download`} download>
                <IcDownload size={15} />
              </a>
              <span className="lib-act danger" title={T('del')} onClick={() => doDelete(c)}><IcTrash size={15} /></span>
            </span>
          </div>
        ))}
      </div>
      <div className="hint" style={{ marginTop: 12 }}>{T('libHint')}</div>
      <DocViewer doc={preview} onClose={() => setPreview(null)} />
    </div>
  )
}
