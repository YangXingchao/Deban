import { useEffect } from 'react'

export default function TooltipHost() {
  useEffect(() => {
    const tip = document.createElement('div')
    tip.className = 'tooltip'
    document.body.appendChild(tip)
    let cur = null
    let timer = null
    let watch = null

    const hide = () => {
      clearTimeout(timer)
      clearInterval(watch)
      timer = watch = null
      cur = null
      tip.classList.remove('show')
    }
    const show = (el) => {
      const text = el.dataset.tip
      if (!text) return
      tip.textContent = text
      tip.classList.add('show')
      const r = el.getBoundingClientRect()
      const tw = tip.offsetWidth, th = tip.offsetHeight
      let x = r.left + r.width / 2 - tw / 2
      x = Math.max(8, Math.min(x, window.innerWidth - tw - 8))
      let y = r.top - th - 8
      if (y < 8) y = r.bottom + 8
      tip.style.left = `${x}px`
      tip.style.top = `${y}px`
      clearInterval(watch)
      watch = setInterval(() => { if (!cur || !cur.isConnected) hide() }, 400)
    }
    const over = (e) => {
      const el = e.target.closest?.('[title], [data-tip]')
      if (!el || el === cur) return
      if (el.hasAttribute('title')) {
        const t = el.getAttribute('title')
        el.removeAttribute('title')
        if (t) el.dataset.tip = t
      }
      if (!el.dataset.tip) return
      hide()
      cur = el
      timer = setTimeout(() => show(el), 220)
    }
    const out = (e) => {
      if (cur && !cur.contains(e.relatedTarget)) hide()
    }
    document.addEventListener('mouseover', over)
    document.addEventListener('mouseout', out)
    document.addEventListener('mousedown', hide)
    window.addEventListener('scroll', hide, true)
    return () => {
      document.removeEventListener('mouseover', over)
      document.removeEventListener('mouseout', out)
      document.removeEventListener('mousedown', hide)
      window.removeEventListener('scroll', hide, true)
      tip.remove()
    }
  }, [])
  return null
}
