import datetime
import json

from . import config, db


def list_meetings(active_id=None):
    rows = db.query("""
        SELECT m.id, m.title, m.started_at, m.ended_at, m.duration, m.status,
          m.starred, m.prev_meeting_id, COUNT(u.id) AS utterances,
          COALESCE(SUM(u.speaker_id = -1), 0) AS me_utts,
          m.audio_path IS NOT NULL AND m.audio_path != '' AS has_audio,
          (SELECT COUNT(*) FROM interventions i WHERE i.meeting_id = m.id AND i.type='RESPOND') AS responds,
          (SELECT GROUP_CONCAT(sid) FROM (
              SELECT speaker_id AS sid FROM meeting_participants mp WHERE mp.meeting_id = m.id
              UNION SELECT DISTINCT u3.speaker_id FROM utterances u3 JOIN speakers s3 ON s3.id = u3.speaker_id
              WHERE u3.meeting_id = m.id AND s3.name != '' AND s3.name IS NOT NULL)) AS participant_ids,
          COALESCE(NULLIF((SELECT COUNT(*) FROM meeting_participants mp WHERE mp.meeting_id = m.id), 0),
                   (SELECT COUNT(DISTINCT u2.speaker_id) FROM utterances u2
                    WHERE u2.meeting_id = m.id AND u2.speaker_id >= 0)) AS n_people
        FROM meetings m LEFT JOIN utterances u ON u.meeting_id = m.id
        WHERE m.status='done' OR m.id != COALESCE(?, -1)
        GROUP BY m.id ORDER BY m.started_at DESC""", (active_id,))
    names = {r["id"]: r["name"] for r in db.query("SELECT id, name FROM speakers WHERE name != '' AND name IS NOT NULL")}
    out = []
    for r in rows:
        r = dict(r)
        ids = [int(x) for x in str(r.get("participant_ids") or "").split(",") if x.strip().lstrip("-").isdigit()]
        r["participants"] = [names[x] for x in ids if x in names]
        r["me"] = {"name": config.get("self_name") or ""} if r.get("me_utts") else None
        out.append(r)
    return out

def speaker_labels(meeting_id):
    named = {r["id"]: r["name"] for r in db.query("SELECT id, name FROM speakers") if r["name"]}
    fixed = {r["speaker_id"]: r["n"] for r in db.query(
        "SELECT speaker_id, n FROM meeting_anon_labels WHERE meeting_id=?", (meeting_id,))}
    next_n = max(fixed.values(), default=0) + 1
    labels = {}
    for r in db.query("""SELECT speaker_id, MIN(start_time) t FROM utterances
                         WHERE meeting_id=? AND speaker_id >= 0
                         GROUP BY speaker_id ORDER BY t""", (meeting_id,)):
        sid = r["speaker_id"]
        if sid in named:
            labels[sid] = named[sid]
        elif sid in fixed:
            labels[sid] = config.anon_label(fixed[sid])
        else:
            db.execute("INSERT OR IGNORE INTO meeting_anon_labels(meeting_id, speaker_id, n) "
                       "VALUES(?,?,?)", (meeting_id, sid, next_n))
            labels[sid] = config.anon_label(next_n)
            next_n += 1
    return labels


def topic_rows(meeting_id):
    return db.query("SELECT * FROM topics WHERE meeting_id=? ORDER BY id", (meeting_id,))


def live_topics(meeting_id):
    if meeting_id is None:
        return []
    topics = []
    for row in topic_rows(meeting_id):
        start = row["start_ts"]
        end = row["end_ts"]
        time_range = ""
        if start is not None and end is not None:
            time_range = f"{datetime.datetime.fromtimestamp(start):%H:%M}~{datetime.datetime.fromtimestamp(end):%H:%M}"
        topics.append({
            "range": time_range,
            "title": row["title"],
            "title_src": row["title_src"] or "",
            "points": json.loads(row["points"] or "[]"),
        })
    return topics


def detail(meeting_id):
    m = db.query_one("SELECT * FROM meetings WHERE id=?", (meeting_id,))
    if not m:
        return None
    utts = db.query("SELECT * FROM utterances WHERE meeting_id=? ORDER BY start_time", (meeting_id,))
    topics = topic_rows(meeting_id)
    ivs = db.query("SELECT * FROM interventions WHERE meeting_id=? ORDER BY id", (meeting_id,))
    spk_ids = {u["speaker_id"] for u in utts}
    names = speaker_labels(meeting_id)
    ctxs = db.query("""
        SELECT mc.context_id AS id, COALESCE(NULLIF(mc.name, ''), c.name, '?') AS name,
               c.id IS NOT NULL AS exists_now, COALESCE(mc.priority, 1) AS priority
        FROM meeting_contexts mc LEFT JOIN contexts c ON c.id = mc.context_id
        WHERE mc.meeting_id=?""", (meeting_id,))
    prev_title = None
    if m.get("prev_meeting_id"):
        pr = db.query_one("SELECT title FROM meetings WHERE id=?", (m["prev_meeting_id"],))
        prev_title = ((pr or {}).get("title") or f"#{m['prev_meeting_id']}") if pr else None
    ids = [i for i in spk_ids if i is not None and i >= 0]
    notes = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in db.query(f"SELECT id, note, grp FROM speakers WHERE id IN ({ph})", tuple(ids)):
            tip = " · ".join(x for x in ((r["grp"] or "").strip(), (r["note"] or "").strip()) if x)
            if tip:
                notes[r["id"]] = tip
    return {"meeting": m, "utterances": utts, "topics": topics, "prev_title": prev_title,
            "interventions": ivs, "speaker_names": names, "speaker_notes": notes, "speaker_ids": sorted(spk_ids),
            "contexts": [{"id": r["id"], "name": r["name"], "exists": bool(r["exists_now"]),
                          "priority": r["priority"]} for r in ctxs]}


def _participant_set(mid):
    ids = {r["speaker_id"] for r in db.query("SELECT speaker_id FROM meeting_participants WHERE meeting_id=?", (mid,))}
    ids |= {r["speaker_id"] for r in db.query("""SELECT DISTINCT u.speaker_id FROM utterances u JOIN speakers s ON s.id = u.speaker_id
                                                 WHERE u.meeting_id=? AND s.name != '' AND s.name IS NOT NULL""", (mid,))}
    return ids


def suggest_prev(participant_ids):
    want = {int(p) for p in participant_ids if p is not None}
    if not want:
        return None
    best, best_score = None, 0.0
    for m in db.query("SELECT id, title, started_at FROM meetings WHERE status='done' ORDER BY started_at DESC LIMIT 30"):
        have = _participant_set(m["id"])
        if not have:
            continue
        score = len(want & have) / len(want | have)
        if score > best_score:
            best, best_score = m, score
    if not best or best_score < 0.5:
        return None
    return {"id": best["id"], "title": best["title"], "started_at": best["started_at"], "score": round(best_score, 2)}


def meeting_prefill(mid):
    m = db.query_one("SELECT * FROM meetings WHERE id=?", (mid,))
    if not m:
        return {"last": None, "participants": [], "contexts": []}
    parts = [r["speaker_id"] for r in db.query(
        "SELECT speaker_id FROM meeting_participants WHERE meeting_id=?", (m["id"],))]
    ctxs = db.query("""SELECT mc.context_id AS id, c.name, COALESCE(mc.priority, 1) AS priority
                       FROM meeting_contexts mc JOIN contexts c ON c.id = mc.context_id
                       WHERE mc.meeting_id=? AND c.name NOT LIKE '会议简介%'""", (m["id"],))
    import json as _j
    last = {"id": m["id"], "title": m["title"], "started_at": m["started_at"],
            "memo": m["memo"] or "",
            "summary": _j.loads(m["summary"] or "[]"),
            "decisions": _j.loads(m["decisions"] or "[]"),
            "todos": _j.loads(m["todos"] or "[]")}
    return {"last": last, "participants": parts, "contexts": [dict(c) for c in ctxs]}


def meeting_digest(mid):
    if not mid:
        return ""
    l = meeting_prefill(mid)["last"]
    if not l:
        return ""
    date = datetime.datetime.fromtimestamp(l["started_at"]).strftime("%Y-%m-%d")
    lines = [f"Previous meeting: {l['title'] or '#' + str(l['id'])} ({date})"]
    if l["summary"]:
        lines.append("Key points: " + "; ".join(s["text"] for s in l["summary"][:6]))
    if l["decisions"]:
        lines.append("Decisions: " + "; ".join(s["text"] for s in l["decisions"][:6]))
    if l["todos"]:
        lines.append("TODO: " + "; ".join(s["text"] for s in l["todos"][:8]))
    if l["memo"].strip():
        lines.append("User notes: " + l["memo"].strip()[:300])
    return "\n".join(lines) if len(lines) > 1 else ""


def replace_topic_label(meeting_id, old_label, new_label):
    import json as _j
    import re as _re
    if not old_label or old_label == new_label:
        return []
    variants = {old_label}
    m = _re.match(r"^(?:[话話]者|发言人|發言人|Speaker )(\d+)$", old_label)
    if m:
        variants |= {tpl.format(m.group(1)) for tpl in config._ANON_LABEL.values()} | {f"话者{m.group(1)}"}

    def sub(s):
        for v in variants:
            s = s.replace(v, new_label)
        return s

    def sub_items(items):
        out = []
        for p in items:
            if isinstance(p, dict):
                q = {**p, "text": sub(p.get("text", ""))}
                if q.get("src"):
                    q["src"] = sub(q["src"])
                out.append(q)
            else:
                out.append(sub(str(p)))
        return out

    changed = []
    for t in db.query("SELECT * FROM topics WHERE meeting_id=?", (meeting_id,)):
        pts0 = _j.loads(t["points"] or "[]")
        title, title_src, pts = sub(t["title"] or ""), sub(t["title_src"] or ""), sub_items(pts0)
        if title != (t["title"] or "") or title_src != (t["title_src"] or "") or pts != pts0:
            db.execute("UPDATE topics SET title=?, title_src=?, points=? WHERE id=?",
                       (title, title_src, db.to_json(pts), t["id"]))
            changed.append(t["id"])
    mt = db.query_one("SELECT summary, todos, decisions FROM meetings WHERE id=?", (meeting_id,))
    if mt:
        upd = {}
        for col in ("summary", "todos", "decisions"):
            try:
                items = _j.loads(mt[col] or "[]")
            except Exception:
                continue
            new_items = sub_items(items)
            if new_items != items:
                upd[col] = db.to_json(new_items)
        if upd:
            db.execute("UPDATE meetings SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?",
                       (*upd.values(), meeting_id))
    return changed


def pt_text(p):
    return p.get("text", "") if isinstance(p, dict) else str(p)


def drop_empty_anon_label(meeting_id, sid, store=None):
    if sid is None or sid < 0 or (store and store.is_named(sid)):
        return
    n = db.query_one("SELECT COUNT(*) c FROM utterances WHERE meeting_id=? AND speaker_id=?",
                     (meeting_id, sid))["c"]
    if n == 0:
        db.execute("DELETE FROM meeting_anon_labels WHERE meeting_id=? AND speaker_id=?",
                   (meeting_id, sid))


def speaker_edit(meeting_id, action, sid, target=None, store=None):
    labels_before = speaker_labels(meeting_id)
    old_label = labels_before.get(sid, config.anon_label(sid))

    def drop_if_orphan_anon(spk):
        if spk is None or spk < 0 or (store and store.is_named(spk)):
            return
        n = db.query_one("SELECT COUNT(*) c FROM utterances WHERE speaker_id=?", (spk,))["c"]
        if n == 0 and store:
            store.remove(spk)

    if action in ("bind", "merge"):
        if target is None or target == sid:
            return {"error": "bad target"}
        if action == "bind" and store and store.is_named(sid):
            return {"error": "source is a named person"}
        if action == "bind" and store:
            store.transfer_voice(sid, target)
        db.execute("UPDATE utterances SET speaker_id=? WHERE meeting_id=? AND speaker_id=?",
                   (target, meeting_id, sid))
        db.execute("DELETE FROM meeting_participants WHERE meeting_id=? AND speaker_id=?",
                   (meeting_id, sid))
        db.execute("DELETE FROM meeting_anon_labels WHERE meeting_id=? AND speaker_id=?",
                   (meeting_id, sid))
        drop_if_orphan_anon(sid)
        new_label = speaker_labels(meeting_id).get(target, "")
        replace_topic_label(meeting_id, old_label, new_label)
        return {"ok": True}
    if action == "detach":
        new_sid = db.insert("speakers", {"created_at": db.now()})
        db.execute("UPDATE utterances SET speaker_id=? WHERE meeting_id=? AND speaker_id=?",
                   (new_sid, meeting_id, sid))
        db.execute("DELETE FROM meeting_participants WHERE meeting_id=? AND speaker_id=?",
                   (meeting_id, sid))
        new_label = speaker_labels(meeting_id).get(new_sid, config.anon_label("?"))
        replace_topic_label(meeting_id, old_label, new_label)
        return {"ok": True}
    if action == "delete_utts":
        db.execute("DELETE FROM utterances WHERE meeting_id=? AND speaker_id=?", (meeting_id, sid))
        db.execute("DELETE FROM meeting_participants WHERE meeting_id=? AND speaker_id=?",
                   (meeting_id, sid))
        db.execute("DELETE FROM meeting_anon_labels WHERE meeting_id=? AND speaker_id=?",
                   (meeting_id, sid))
        drop_if_orphan_anon(sid)
        return {"ok": True}
    return {"error": "bad action"}


def rename(meeting_id, title):
    db.execute("UPDATE meetings SET title=? WHERE id=?", (title[:40], meeting_id))


def delete(meeting_id):
    import pathlib
    m = db.query_one("SELECT audio_path FROM meetings WHERE id=?", (meeting_id,))
    db.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
    if m and m["audio_path"]:
        pathlib.Path(m["audio_path"]).unlink(missing_ok=True)


def edit_utterance(uid, text=None, translation=None):
    if text is not None:
        db.execute("UPDATE utterances SET corrected_text=? WHERE id=?", (text, uid))
    if translation is not None:
        db.execute("UPDATE utterances SET translation=? WHERE id=?", (translation, uid))


def search(q, limit=40):
    out = []
    seen_m = set()
    for m in db.query("""SELECT id, title, started_at FROM meetings
                         WHERE status='done' AND title LIKE ? ORDER BY started_at DESC LIMIT 10""",
                      (f"%{q.strip()}%",)):
        out.append({"meeting_id": m["id"], "title": m["title"],
                    "snippet": "", "started_at": m["started_at"]})
        seen_m.add(m["id"])
    people = db.query("SELECT id, name FROM speakers WHERE name != '' AND name LIKE ?", (f"%{q.strip()}%",))
    if people:
        ph = ",".join("?" * len(people))
        pids = tuple(p["id"] for p in people)
        pname = {p["id"]: p["name"] for p in people}
        rows = db.query(f"""
            SELECT m.id, m.title, m.started_at, x.speaker_id FROM meetings m JOIN (
                SELECT meeting_id, speaker_id FROM utterances WHERE speaker_id IN ({ph})
                UNION SELECT meeting_id, speaker_id FROM meeting_participants WHERE speaker_id IN ({ph})
            ) x ON x.meeting_id = m.id
            WHERE m.status='done' GROUP BY m.id, x.speaker_id ORDER BY m.started_at DESC LIMIT 40""",
            pids + pids)
        for r in rows:
            if r["id"] in seen_m:
                continue
            seen_m.add(r["id"])
            out.append({"meeting_id": r["id"], "title": r["title"],
                        "snippet": f"👤 {pname.get(r['speaker_id'], '')}", "started_at": r["started_at"]})
    ids = db.fts_search("utterances_fts", q, limit=limit)
    if ids:
        ph = ",".join("?" * len(ids))
        for u in db.query(f"""
            SELECT u.id, u.meeting_id, u.text, u.corrected_text, u.translation,
                   m.title, m.started_at
            FROM utterances u JOIN meetings m ON m.id = u.meeting_id
            WHERE u.id IN ({ph}) ORDER BY m.started_at DESC""", tuple(ids)):
            out.append({"meeting_id": u["meeting_id"], "title": u["title"],
                        "snippet": (u["corrected_text"] or u["text"])[:48],
                        "started_at": u["started_at"]})
    return out[:limit]


def _fmt_ts(sec):
    return str(datetime.timedelta(seconds=int(sec)))


def export_md(meeting_id, speaker_name):
    d = detail(meeting_id)
    if not d:
        return None
    m = d["meeting"]
    date = datetime.datetime.fromtimestamp(m["started_at"])
    out = [f"# {m['title'] or 'Meeting'} ({date:%Y-%m-%d %H:%M})\n"]
    import json as _j
    summary = _j.loads(m.get("summary") or "[]")
    todos = _j.loads(m.get("todos") or "[]")
    decisions = _j.loads(m.get("decisions") or "[]")
    if (m.get("memo") or "").strip():
        out.append("## Memo\n")
        out.append(m["memo"].strip() + "\n")
    if summary:
        out.append("## Summary\n")
        out += [f"- {s['text']}" for s in summary] + [""]
    if decisions:
        out.append("## Decisions\n")
        out += [f"- {s['text']}" for s in decisions] + [""]
    if todos:
        out.append("## TODO\n")
        out += [f"- [ ] {s['text']}" for s in todos] + [""]
    if d["interventions"]:
        out.append("## Response Opportunities\n")
        off = {u["id"]: u["start_time"] for u in d["utterances"]}
        for iv in d["interventions"]:
            mark = "🔴" if iv["type"] == "RESPOND" else "🟡"
            out.append(f"- {mark} **[{_fmt_ts(off.get(iv['utterance_id'], 0))}]** "
                       f"{iv['by_speaker']}: {iv['question']}\n"
                       f"  Suggested reply: {iv['suggested_answer']}")
        out.append("")
    if d["topics"]:
        out.append("## Topics\n")
        for t in d["topics"]:
            import json as _j
            out.append(f"### {t['title']}\n")
            for p in _j.loads(t["points"] or "[]"):
                out.append(f"- {pt_text(p)}")
            out.append("")
    out.append("## Transcript\n")
    for u in d["utterances"]:
        text = u["corrected_text"] or u["text"]
        out.append(f"**[{_fmt_ts(u['start_time'])}] {speaker_name(u['speaker_id'])}**: {text}")
        if u["translation"]:
            out.append(f"> {u['translation']}")
        out.append("")
    return "\n".join(out)


def export_srt(meeting_id):
    d = detail(meeting_id)
    if not d:
        return None
    out = []
    for i, u in enumerate(d["utterances"], 1):
        s, e = u["start_time"], u["end_time"] or (u["start_time"] + 4)
        def srt_t(x):
            ms = int((x % 1) * 1000)
            return f"{int(x)//3600:02}:{int(x)%3600//60:02}:{int(x)%60:02},{ms:03}"
        text = u["corrected_text"] or u["text"]
        line = text + (f"\n{u['translation']}" if u["translation"] else "")
        out.append(f"{i}\n{srt_t(s)} --> {srt_t(e)}\n{line}\n")
    return "\n".join(out)
