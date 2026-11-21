import { useEffect, useMemo, useState } from 'react'
import { T } from '../i18n.js'
import { post } from '../api.js'

export function Cites({ sources, onDoc, onUtt }) {
  const [open, setOpen] = useState(false)
  const s = sources || {}
  const mats = s.materials || [], utts = s.utterances || []
  if (!mats.length && !utts.length) return null
  return (
    <div className={'cites' + (open ? ' open' : '')}>
      <span className="cites-k cites-tg" onClick={() => setOpen((o) => !o)} role="button" aria-expanded={open}>
        <span className="chev">{open ? '▾' : '▸'}</span>{T('cites')} <span className="n">{mats.length + utts.length}</span>
      </span>
      {open && mats.map((m) => (
        <span key={'m' + m.id} className="cite doc"
              onClick={() => onDoc ? onDoc(m) : window.open(`/api/context/${m.id}/view`, '_blank')}>
          📄 {m.name}
        </span>
      ))}
      {open && utts.map((u) => (
        <span key={'u' + u.uid} className="cite utt"
              onClick={() => {
                if (onUtt) return onUtt(u.uid)
                const el = document.getElementById(`utt-${u.uid}`)
                if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1600) }
              }}>
          💬 {u.by}: {u.text.length > 22 ? u.text.slice(0, 22) + '…' : u.text}
        </span>
      ))}
    </div>
  )
}

export function laneLabel(l) { const k = 'lane_' + l; const v = T(k); return v === k ? l : v }

export { useCardLang, LangToggle, pickText } from './contentLang.jsx'
import { useCardLang, LangToggle, useTranslateEnabled } from './contentLang.jsx'
import SpeakerChip from './SpeakerChip.jsx'
const parseJ = (v, dflt) => { if (v == null || v === '') return dflt; if (typeof v !== 'string') return v; try { const x = JSON.parse(v); return x == null ? dflt : x } catch { return dflt } }
export function pick(iv, lang) {
  const ctxSrc = parseJ(iv.context_src, []), ctx = parseJ(iv.context, [])
  return {
    question: lang === 'src' ? (iv.question_src || iv.question) : iv.question,
    why: lang === 'src' ? (iv.why_src || iv.why) : iv.why,
    points: lang === 'src' ? (ctxSrc.length ? ctxSrc : ctx) : ctx,
  }
}

export function StanceReply({ iv, lang }) {
  const dflt = iv.stance || 'neutral'
  const [stance, setStance] = useState(dflt)
  useEffect(() => { setStance(iv.stance || 'neutral') }, [iv.iv_id || iv.id])
  const replies = parseJ(iv.replies, {})
  const base = { guide: iv.reply_guide, guide_src: iv.reply_guide_src, src: iv.reply_src || iv.suggested_answer, out: iv.reply_out || iv.answer_explain }
  const r = replies[stance] || (stance === dflt ? base : null)
  const guide = r ? (lang === 'src' ? (r.guide_src || r.guide) : r.guide) : ''
  const example = r ? (lang === 'src' ? r.src : (r.out || r.src)) : ''
  return (
    <>
      <div className="rs-acts">
        <span className="rs-tune">
          <span className="lb">{T('stanceLbl')}</span>
          <span className="segctl" role="radiogroup" aria-label={T('stanceLbl')}>
            {[['positive', '○', T('stancePos')], ['neutral', '△', T('stanceNeu')], ['negative', '×', T('stanceNeg')]].map(([k, mk, lb]) => (
              <button key={k} type="button" role="radio" aria-checked={stance === k}
                      className={'seg' + (stance === k ? ' on' : '') + (replies[k] || k === dflt ? '' : ' dim')} onClick={() => setStance(k)}>
                <span className="mk">{mk}</span>{lb}
              </button>
            ))}
          </span>
        </span>
      </div>
      <div className="reply">
        {r ? (
          <>
            {guide ? <div className="reply-guide">{guide}</div> : null}
            {example ? <div className="reply-ex"><span className="lb">{T('exampleLbl')}</span>{example}</div> : null}
          </>
        ) : <div className="reply-ex" style={{ color: 'var(--dim)' }}>{T('stancePending')}</div>}
      </div>
    </>
  )
}

export function WhyLine({ iv, points = [] }) {
  const [open, setOpen] = useState(false)
  const lane = iv.lane ? laneLabel(iv.lane) : ''
  const line = iv.why || (iv.pending ? T('preHint', iv.by) : lane)
  if (!line && !lane && !points.length) return null
  return (
    <div className="why iv-sec">
      <div className="sec-k">{T('whySecLbl')}</div>
      {line ? <span className="why-line">{line}</span> : null}
      {points.length > 0 && <div className="ctx">{points.map((c, i) => <div key={i}>· {c}</div>)}</div>}
      {(lane || iv.confidence) ? (
        <span className="why-btn" onClick={() => setOpen((o) => !o)}>{T('whyBtn')}</span>
      ) : null}
      {open && (
        <div className="why-detail">
          {lane ? <div>· {T('whyLane')}: {lane}</div> : null}
          {iv.confidence ? <div>· {T('confidence')}: {Math.round(iv.confidence * 100)}%</div> : null}
          {iv.why && iv.why !== line ? <div>· {iv.why}</div> : null}
        </div>
      )}
    </div>
  )
}

export default function InterventionCard({ iv, onClose, utts, compact }) {
  const [lang, toggleLang] = useCardLang()
  const trEnabled = useTranslateEnabled()
  const [replyOpen, setReplyOpen] = useState(() => { try { return localStorage.getItem('iv_reply_open') === '1' } catch { return false } })
  const toggleReply = () => setReplyOpen((o) => { try { localStorage.setItem('iv_reply_open', o ? '0' : '1') } catch {} ; return !o })
  useEffect(() => {
    if (!iv.cancelled || iv.cancelReason === 'error') return
    const t = setTimeout(onClose, 2600)
    return () => clearTimeout(t)
  }, [iv.cancelled])

  const ctxLines = useMemo(() => {
    if (!utts || !iv.uid) return []
    const k = utts.findIndex((u) => u.uid === iv.uid)
    if (k < 0) return []
    return utts.slice(Math.max(0, k - 2), k + 1)
  }, [utts, iv.uid])
  const jump = (uid) => {
    const el = document.getElementById(`utt-${uid}`)
    if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1600) }
  }

  const kindCls = { RESPOND: '', CONSIDER: 'consider', CONTRIBUTE: 'contribute', ASK: 'ask' }[iv.kind] || 'consider'
  const kindLabel = { RESPOND: T('yourTurn'), CONSIDER: T('consider'), CONTRIBUTE: T('kindCONTRIBUTE'), ASK: T('kindASK') }[iv.kind] || T('consider')
  const p = pick(iv, lang)
  const previewGuide = (() => { const rs = parseJ(iv.replies, {}); const r = rs[iv.stance || 'neutral']; const g = r ? (lang === 'src' ? (r.guide_src || r.guide) : r.guide) : (lang === 'src' ? (iv.reply_guide_src || iv.reply_guide) : iv.reply_guide); return g || iv.reply_src })()

  const dismiss = () => onClose()

  return (
    <div className={'iv-card ' + kindCls + (iv.pending ? ' pending' : '') + (iv.cancelled ? ' cancelled' : '') + (compact ? ' compact' : '')}>
      <div className="head">
        <span className="dot" style={{ background: 'currentColor' }} />{kindLabel}
        <span style={{ flex: 1 }} />
        {trEnabled ? <LangToggle lang={lang} onToggle={toggleLang} /> : null}
        <span className="x" onClick={dismiss} title={T('dismiss')}>✕</span>
      </div>
      <div className="iv-body">
      <div className="q">{iv.by}: {p.question}</div>
      {ctxLines.length > 0 && (
        <div className="ctx-lines">
          <div className="ctx-k">{T('ctxLbl')}</div>
          {ctxLines.map((u) => (
            <div key={u.uid} className={'cl' + (u.uid === iv.uid ? ' trig' : '')} onClick={() => jump(u.uid)}>
              <span className="who"><SpeakerChip spk={u.spk} name={u.name} named={u.named} /></span>
              <span className="tx">{lang === 'src' ? u.text : (u.zh || u.text)}</span>
            </div>
          ))}
        </div>
      )}
      <WhyLine iv={{ ...iv, why: p.why }} points={!iv.pending && !iv.cancelled ? p.points : []} />
      {iv.cancelled ? (
        <div className={'cancel-msg' + (iv.cancelReason === 'error' ? ' err' : '')}>{iv.cancelReason === 'error' ? T('llmFailMsg') : T('cancelledMsg')}</div>
      ) : (
        <div className={'reply-sec' + (replyOpen ? ' open' : '')}>
          <div className="rs-head" onClick={toggleReply}>
            <span className="rs-k">{T('replyLbl')}</span>
            {!replyOpen && <span className="rs-preview">{iv.pending ? T('pendingAnswer') : previewGuide}</span>}
            <span className="chev">{replyOpen ? '▴' : '▾'}</span>
          </div>
          {replyOpen && (iv.pending ? (
            <div className="reply skeleton">
              <div className="src">{T('pendingAnswer')}</div>
              <div className="bar" /><div className="bar short" />
            </div>
          ) : (
            <>
              <StanceReply iv={iv} lang={lang} />
              <Cites sources={iv.sources} />
            </>
          ))}
        </div>
      )}
      </div>
    </div>
  )
}
