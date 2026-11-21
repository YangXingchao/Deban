function notifyError() {
  try { window.dispatchEvent(new CustomEvent('api-error')) } catch {}
}

export async function api(path, opts = {}) {
  const { quiet = false, ...requestOpts } = opts
  try {
    const r = await fetch('/api' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...requestOpts,
      body: requestOpts.body !== undefined && !(requestOpts.body instanceof FormData)
        ? JSON.stringify(requestOpts.body) : requestOpts.body,
    })
    if (!r.ok) { if (!quiet) notifyError(); return null }
    return await r.json()
  } catch {
    if (!quiet) notifyError()
    return null
  }
}

export const post = (path, body, opts = {}) => api(path, { ...opts, method: 'POST', body })
export const put = (path, body) => api(path, { method: 'PUT', body })
export const del = (path) => api(path, { method: 'DELETE' })

export async function upload(path, file) {
  try {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api' + path, { method: 'POST', body: fd })
    if (!r.ok) {
      const detail = await r.text().catch(() => '')
      return { error: detail || `upload failed (HTTP ${r.status})` }
    }
    return await r.json()
  } catch (e) {
    notifyError()
    return { error: String(e) }
  }
}
