import { T } from '../i18n.js'

export default function DocViewer({ doc, onClose }) {
  if (!doc) return null
  return (
    <>
      <div className="iv-backdrop" onClick={onClose} />
      <div className="viewer">
        <div className="viewer-head">
          <span className="vt">{doc.name}</span>
          <a href={`/api/context/${doc.id}/download`} download><button className="ghost">⬇ {T('download')}</button></a>
          <button className="ghost" onClick={onClose}>✕</button>
        </div>
        <iframe title="preview" src={`/api/context/${doc.id}/view`} />
      </div>
    </>
  )
}
