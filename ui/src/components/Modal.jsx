import { useEffect, useRef, useState } from 'react'
import { isImeKey } from '../keyboard.js'
import { T } from '../i18n.js'
import { IcX } from './Icons.jsx'

let _open = null

export const modal = {
  prompt: (opts) => new Promise((resolve) => _open({ type: 'prompt', ...opts, resolve })),
  confirm: (opts) => new Promise((resolve) => _open({ type: 'confirm', ...opts, resolve })),
  form: (opts) => new Promise((resolve) => _open({ type: 'form', ...opts, resolve })),
  choose: (opts) => new Promise((resolve) => _open({ type: 'choose', ...opts, resolve })),
  split: (opts) => new Promise((resolve) => _open({ type: 'split', ...opts, resolve })),
}

export function ModalHost() {
  const [m, setM] = useState(null)
  const [val, setVal] = useState('')
  const [formVals, setFormVals] = useState({})
  const [cut, setCut] = useState(-1)
  const [filter, setFilter] = useState('')
  const [regName, setRegName] = useState('')
  const [facet, setFacet] = useState({})
  const inputRef = useRef(null)

  useEffect(() => {
    _open = (opts) => {
      setVal(opts.value || '')
      setFormVals(Object.fromEntries((opts.fields || []).map((f) => [f.key, f.value || ''])))
      setCut(-1)
      setFilter(''); setRegName('')
      setFacet({})
      setM(opts)
    }
    return () => { _open = null }
  }, [])

  useEffect(() => {
    if (m && inputRef.current) { inputRef.current.focus(); inputRef.current.select?.() }
  }, [m])
  useEffect(() => {
    if (!m) return
    const h = (e) => { if (e.key === 'Escape' && !isImeKey(e)) { setM(null); m.resolve(m.type === 'confirm' ? false : null) } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [m])

  if (!m) return null
  const close = (result) => { setM(null); m.resolve(result) }
  const chars = m.type === 'split' ? Array.from(m.text || '') : []
  const cutOk = cut > 0 && cut < chars.length
  const ok = () => {
    if (m.type === 'prompt') close(val)
    else if (m.type === 'confirm') close(true)
    else if (m.type === 'split') { if (cutOk) close(cut) }
    else close(formVals)
  }
  const onSplitKey = (e) => {
    if (e.key === 'ArrowLeft' && cut > 1) { e.preventDefault(); setCut(cut - 1) }
    if (e.key === 'ArrowRight' && cut >= 0 && cut < chars.length - 1) { e.preventDefault(); setCut(cut + 1) }
    if (e.key === 'Enter' && cutOk) { e.preventDefault(); ok() }
  }
  const cancel = () => close(m.type === 'confirm' ? false : null)
  const dismiss = () => close(null)
  const onKey = (e) => {
    if (isImeKey(e)) return
    if (e.key === 'Enter'
        && (m.type === 'prompt' || m.type === 'confirm')
        && e.target.tagName !== 'BUTTON') { e.preventDefault(); ok() }
    if (e.key === 'Escape') dismiss()
  }

  if (m.type === 'choose') {
    const fq = filter.trim().toLowerCase()
    const facets = m.filters || []
    const has = (v) => Array.isArray(v) ? v.length > 0 : !!v
    const passFacets = (o) => facets.every((f) => !has(facet[f.key]) || f.match(o.meta || {}, facet[f.key]))
    const opts = (m.options || []).filter((o) => String(o.value).startsWith('__')
      || ((!fq || String(o.label).toLowerCase().includes(fq)) && passFacets(o)))
    return (
      <>
        <div className="drawer-backdrop" style={{ zIndex: 124 }} onClick={dismiss} />
        <div className="drawer pick-drawer" style={{ zIndex: 125 }} onKeyDown={onKey}>
          <div className="drawer-head">{m.title}<button className="drawer-x" onClick={dismiss} aria-label={T('close')}><IcX size={15} /></button></div>
          {(m.searchable || (m.options || []).length > 5) && (
            <div className="pick-search">
              <input type="text" ref={inputRef} className="m-filter" value={filter} placeholder={m.searchPh || T('searchPeoplePh')}
                     onChange={(e) => setFilter(e.target.value)} />
            </div>
          )}
          {facets.length > 0 && (
            <div className="pick-facets">
              <div className="facet-row">
                {facets.map((f) => {
                  if (f.type === 'date') {
                    return <input key={f.key} type="date" value={facet[f.key] || ''} title={f.label}
                                  onChange={(e) => setFacet((x) => ({ ...x, [f.key]: e.target.value }))} />
                  }
                  if (f.type === 'multi') {
                    const sel = facet[f.key] || []
                    return (
                      <select key={f.key} value="" onChange={(e) => { const v = e.target.value; if (v) setFacet((x) => ({ ...x, [f.key]: [...(x[f.key] || []), v] })) }}>
                        <option value="">{sel.length ? `${f.label} +` : f.label}</option>
                        {f.options.filter((o) => !sel.includes(String(o.value))).map((o) => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
                      </select>
                    )
                  }
                  return (
                    <select key={f.key} value={facet[f.key] || ''} onChange={(e) => setFacet((x) => ({ ...x, [f.key]: e.target.value }))}>
                      <option value="">{f.label}</option>
                      {f.options.map((o) => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
                    </select>
                  )
                })}
              </div>
              {facets.filter((f) => f.type === 'multi' && (facet[f.key] || []).length > 0).map((f) => (
                <div key={f.key} className="facet-chips">
                  {(facet[f.key] || []).map((v) => (
                    <span key={v} className="ctx-chip on">{(f.options.find((o) => String(o.value) === v) || {}).label || v}
                      <span className="chip-x" onClick={() => setFacet((x) => ({ ...x, [f.key]: (x[f.key] || []).filter((y) => y !== v) }))}>✕</span>
                    </span>
                  ))}
                </div>
              ))}
            </div>
          )}
          <div className="drawer-body">
            {opts.filter((o) => !o.danger && !o.footer).map((o) => (
              <div key={String(o.value)} className="m-choice" title={o.title} onClick={() => close(o.value)}>
                <span className="l">{o.label}</span>
                {o.hint ? <span className="h">{o.hint}</span> : null}
              </div>
            ))}
            {opts.filter((o) => !o.danger && !o.footer).length === 0 && <div className="empty">—</div>}
          </div>
          {(m.register || opts.some((o) => o.danger || o.footer)) && (
            <div className="drawer-foot pick-foot">
              {m.register && (
                <div className="pick-reg">
                  <input type="text" value={regName} placeholder={m.register.placeholder} maxLength={30}
                         onChange={(e) => setRegName(e.target.value)}
                         onKeyDown={(e) => { if (e.key === 'Enter' && !isImeKey(e) && regName.trim()) { e.preventDefault(); close({ register: regName.trim() }) } }} />
                  <button className="primary" disabled={!regName.trim()} onClick={() => close({ register: regName.trim() })}>{m.register.button}</button>
                </div>
              )}
              {[...opts.filter((o) => o.footer && !o.danger), ...opts.filter((o) => o.danger)].map((o) => (
                <div key={String(o.value)} className={'m-choice' + (o.danger ? ' danger' : '')} title={o.title} onClick={() => close(o.value)}>
                  <span className="l">{o.label}</span>
                  {o.hint ? <span className="h">{o.hint}</span> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </>
    )
  }

  return (
    <>
      <div className="iv-backdrop" style={{ zIndex: 120 }} onClick={dismiss} />
      <div className="modal" onKeyDown={onKey}>
        {m.title ? <div className="m-title">{m.title}</div> : null}
        {m.message ? <div className="m-msg">{m.message}</div> : null}
        <div className="m-body">
          {m.type === 'prompt' && (
            <input ref={inputRef} type="text" value={val} maxLength={m.maxLength} onChange={(e) => setVal(e.target.value)} />
          )}
          {m.type === 'split' && (<>
            <div className="m-splitbox" tabIndex={0} ref={inputRef} onKeyDown={onSplitKey}>
              {chars.map((ch, i) => (
                <span key={i} onClick={() => setCut(i)}
                      className={'sc' + (cut < 0 ? '' : i < cut ? ' a' : ' b') + (i === cut ? ' cut' : '')}>{ch}</span>
              ))}
            </div>
            {cutOk && (
              <div className="m-split-prev">
                <div><label>{T('splitFirst')}</label><div className="pv a">{chars.slice(0, cut).join('')}</div></div>
                <div><label>{T('splitSecond')}</label><div className="pv b">{chars.slice(cut).join('')}</div></div>
              </div>
            )}
          </>)}
          {m.type === 'form' && (m.fields || []).map((f, i) => (
            <div key={f.key} className="m-field">
              <label>{f.label}</label>
              {f.multiline
                ? <textarea rows={f.rows || 2} ref={i === 0 ? inputRef : null} value={formVals[f.key]} maxLength={f.maxLength}
                            onChange={(e) => setFormVals({ ...formVals, [f.key]: e.target.value })} />
                : <input type="text" ref={i === 0 ? inputRef : null} value={formVals[f.key]} maxLength={f.maxLength}
                         onChange={(e) => setFormVals({ ...formVals, [f.key]: e.target.value })} />}
            </div>
          ))}
        </div>
        <div className="m-btns">
          <button className="ghost" onClick={cancel}>{T('cancel')}</button>
          <button className={m.danger ? 'danger' : 'primary'} onClick={ok} disabled={m.type === 'split' && !cutOk}>
            {m.okText || (m.danger ? T('del') : T('ok'))}
          </button>
        </div>
      </div>
    </>
  )
}
