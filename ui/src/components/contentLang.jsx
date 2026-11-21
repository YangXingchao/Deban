import { useEffect, useState } from 'react'
import { T } from '../i18n.js'
import { api } from '../api.js'

export function useContentLang() {
  return ['src', () => {}]
}
export const useCardLang = useContentLang

export function LangToggle() {
  return null
}

let _trEnabled = null
const trListeners = new Set()
export function useTranslateEnabled() {
  const [on, setOn] = useState(_trEnabled === null ? true : _trEnabled)
  useEffect(() => {
    trListeners.add(setOn)
    if (_trEnabled === null) {
      api('/settings').then((c) => { _trEnabled = !(c && c.translate_default === false); trListeners.forEach((fn) => fn(_trEnabled)) })
    }
    return () => trListeners.delete(setOn)
  }, [])
  return on
}
export function setTranslateEnabledCache(v) { _trEnabled = !!v; trListeners.forEach((fn) => fn(_trEnabled)) }

export function pickText(item, lang) {
  if (item == null) return ''
  if (typeof item === 'string') return item
  return lang === 'src' && item.src ? item.src : (item.text || '')
}
