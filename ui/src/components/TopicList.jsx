import { T } from '../i18n.js'
import { pickText } from './contentLang.jsx'

export default function TopicList({ topics, onJump, lang = 'out' }) {
  if (!topics.length) return <div className="empty">{T('topicHint')}</div>
  return topics.map((t, i) => (
    <div className="topic" key={i}>
      <div className="range">{t.range}</div>
      <div className="title">{lang === 'src' && t.title_src ? t.title_src : t.title}</div>
      <ul>
        {t.points.map((p, j) => {
          const text = pickText(p, lang)
          const uid = p && typeof p === 'object' ? p.uid : null
          return (
            <li key={j}>
              {text}
              {uid && onJump
                ? <span className="jump" title={T('jumpTo')} onClick={() => onJump(uid)}>↗</span>
                : null}
            </li>
          )
        })}
      </ul>
    </div>
  ))
}
