import { useEffect, useState } from 'react'
import { api, del, post, put } from '../api.js'
import { T } from '../i18n.js'

const PROVIDERS = {
  openai: ['OpenAI', 'gpt-5.4-mini'],
  anthropic: ['Anthropic', 'claude-haiku-5-5'],
  gemini: ['Google Gemini', 'gemini-4-hydrogen'],
  grok: ['Grok (xAI)', 'grok-4.7'],
  kimi: ['Kimi (Moonshot AI)', 'kimi-k2.5'],
  kimi_cn: ['Kimi (Moonshot AI China)', 'kimi-k2.5'],
  deepseek: ['DeepSeek', 'deepseek-flash'],
  openrouter: ['OpenRouter', ''],
  opencode_chat: ['OpenCode Zen — Chat Completions', 'kimi-k2.5'],
  opencode_responses: ['OpenCode Zen — Responses', 'gpt-5.4-mini'],
  opencode_messages: ['OpenCode Zen — Messages', 'claude-sonnet-5'],
  compatible: ['OpenAI-compatible', ''],
}
const initial = { provider: 'openai', model: PROVIDERS.openai[1], base_url: '', api_key: '' }

export default function AISettings({ live }) {
  const [stored, setStored] = useState(null)
  const [draft, setDraft] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    api('/ai/connection').then((connection) => {
      if (!connection) return
      setStored(connection)
      if (connection.configured) setDraft({ ...connection, api_key: '' })
    })
  }, [])

  const set = (patch) => {
    setDraft((form) => ({ ...form, ...patch }))
    setMessage(null)
  }
  const sameEndpoint = stored?.provider === draft.provider && (
    draft.provider !== 'compatible' || stored.base_url === draft.base_url.trim().replace(/\/+$/, '')
  )
  const act = async (action) => {
    setBusy(true)
    try {
      const request = { provider: draft.provider, model: draft.model, base_url: draft.base_url, api_key: draft.api_key }
      const response = action === 'test' ? await post('/ai/test', request)
        : action === 'save' ? await put('/ai/connection', request)
          : await del('/ai/connection')
      if (!response || response.error) {
        const code = response?.error || ''
        const translated = T('aiError_' + code)
        setMessage({ text: translated === 'aiError_' + code ? T('aiError', code) : translated, error: true })
        return
      }
      if (action !== 'test') {
        setStored(response)
        setDraft(response.configured ? { ...response, api_key: '' } : initial)
      }
      setMessage({ text: T(action === 'test' ? 'aiTestOK' : action === 'clear' ? 'aiCleared' : 'settingsSaved') })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card set-card ai-settings">
      <h3>{T('aiConnection')}{message && <span className={'ai-status ' + (message.error ? 'error' : 'success')} role="status">{message.text}</span>}</h3>
      <p className="hint">{T('aiDataNote')}</p>
      <fieldset disabled={busy || live || !stored}>
        <label>{T('aiProvider')}
          <select value={draft.provider} onChange={(event) => {
            const provider = event.target.value
            const saved = stored?.provider === provider ? stored : null
            set({ provider, model: saved?.model || PROVIDERS[provider][1], base_url: saved?.base_url || '', api_key: '' })
          }}>
            {Object.entries(PROVIDERS).map(([id, [label]]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        {draft.provider === 'compatible' && <label>Base URL
          <input type="url" value={draft.base_url} placeholder="http://127.0.0.1:11434/v1"
                 onChange={(event) => set({ base_url: event.target.value })} />
        </label>}
        <label>{T('aiModel')}
          <input value={draft.model} onChange={(event) => set({ model: event.target.value })} />
        </label>
        <label>API Key
          <input type="password" autoComplete="new-password" value={draft.api_key}
                 placeholder={sameEndpoint && stored?.has_key ? T('aiKeySaved') : ''}
                 onChange={(event) => set({ api_key: event.target.value })} />
        </label>
        <div className="ai-actions">
          <button className="primary" disabled={!draft.model.trim()} onClick={() => act('save')}>{T('save')}</button>
          <button disabled={!draft.model.trim()} onClick={() => act('test')}>{T('aiTest')}</button>
          {stored?.configured && <button onClick={() => act('clear')}>{T('aiClear')}</button>}
        </div>
      </fieldset>
    </section>
  )
}
