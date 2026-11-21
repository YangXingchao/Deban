export function memberTip(p) {
  if (!p) return undefined
  return (p.note || '').trim() || undefined
}

export async function pickSpeaker(api, modal, T, excludeSpk, meetingId, post) {
  let people = []
  let anons = []
  if (meetingId) {
    const r = await api(`/meeting/${meetingId}/roster`)
    people = (r && r.participants) || []
    anons = (r && r.anons) || []
  }
  const inMeeting = new Set([...people.map((p) => p.id), ...anons.map((a) => a.id)])
  const others = ((await api('/speakers')) || []).filter((p) => !inMeeting.has(p.id))
  people = people.filter((p) => p.id !== excludeSpk)
  anons = anons.filter((a) => a.id !== excludeSpk)
  const v = await modal.choose({
    title: T('reassignMsg'), message: people.length || anons.length ? T('rosterOnly') : T('membersOnly'),
    register: post ? { placeholder: T('secNew'), button: T('registerBtn') } : undefined,
    options: [
      ...people.map((p) => ({ label: p.name, value: p.id, hint: p.has_voice ? '●' : '◌', title: memberTip(p) })),
      ...anons.map((a) => ({ label: a.name, value: a.id, hint: T('anonHint') })),
      ...others.filter((p) => p.id !== excludeSpk).map((p) => ({ label: p.name, value: p.id, hint: T('secOther'), title: memberTip(p) })),
    ],
  })
  if (v && typeof v === 'object' && v.register && post) {
    const m = await post('/speakers', { name: v.register })
    return m && m.id ? m.id : null
  }
  return v
}

export async function splitFlow(u, utts, api, post, modal, T, meetingId) {
  const idx = await modal.split({ title: T('split'), message: T('splitMsg'), text: u.text })
  if (idx == null) return false
  const chars = Array.from(u.text)
  const t1 = chars.slice(0, idx).join('').trim(), t2 = chars.slice(idx).join('').trim()
  if (!t1 || !t2) return false
  await post(`/utterance/${u.uid}/split`, { text1: t1, text2: t2, speaker_id2: u.spk })
  return true
}

export async function deleteUtt(u, del, modal, T) {
  if (!await modal.confirm({ title: T('del'), message: T('uttDelConfirm', u.text.slice(0, 40)), danger: true })) return false
  await del(`/utterance/${u.uid}`)
  return true
}
