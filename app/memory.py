import json
import logging
import re
import time

from . import config, db, localization

log = logging.getLogger("memory")

MIN_SELF_CHARS = 8
COMMIT_TTL_DAYS = 14
OWN_TTL_DAYS = 30
DEFAULT_TTL_DAYS = OWN_TTL_DAYS
USED_LINGER_DAYS = 1
DECAY_PER_WEEK = 0.1
MIN_WEIGHT = 0.3
MAX_INJECT_FACTS = 3
MAX_INJECT_UTTS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS my_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,
  utterance_id INTEGER,
  kind TEXT DEFAULT 'commit',
  text TEXT NOT NULL,
  source_text TEXT DEFAULT '',
  due TEXT DEFAULT '',
  weight REAL DEFAULT 1.0,
  created_at REAL NOT NULL,
  expires_at REAL
);
"""

EXTRACT_SYS = (
    "Extract only two kinds of facts from the user's own numbered utterances: "
    "commit for a concrete commitment and own for a statement of responsibility. "
    "Do not infer facts or attribute another person's work to the user. "
    "The meeting language is {src}; write text in {src}. "
    "The meeting date is {date}; resolve relative deadlines to YYYY-MM-DD where possible. "
    'Return only JSON: {{"facts": [{{"kind": "commit or own", "text": "fact", '
    '"due": "YYYY-MM-DD or empty", "uid": source utterance number}}]}}. '
    'Return {{"facts": []}} when no facts are present.')


def _now():
    return time.time()


def extract_from_meeting(mid, llm, src_name, todos=None, self_names=()):
    if not llm or not getattr(llm, "available", False):
        return 0
    rows = db.query("""SELECT id, text, corrected_text, ts FROM utterances
                       WHERE meeting_id=? AND speaker_id=-1 ORDER BY start_time""", (mid,))
    mine = [(r["id"], (r["corrected_text"] or r["text"] or "").strip()) for r in rows]
    mine = [(u, t) for u, t in mine if len(t) >= MIN_SELF_CHARS]
    n = 0
    started = (db.query_one("SELECT started_at FROM meetings WHERE id=?", (mid,)) or {}).get("started_at") or _now()
    people = json.dumps(people_of_meeting(mid))
    if mine:
        lines = "\n".join(f"#{u} {t}" for u, t in mine[-120:])
        try:
            import datetime as _dt
            date_s = _dt.datetime.fromtimestamp(started).strftime("%Y-%m-%d (%a)")
            data = llm.chat_json(localization.localized_prompt("memory", EXTRACT_SYS, src=src_name, date=date_s), lines, max_tokens=900, timeout=40.0, role="memory") or {}
        except Exception as e:
            log.warning("fact extraction failed: %s", str(e)[:120])
            data = {}
        valid = {u for u, _ in mine}
        for f in (data.get("facts") or [])[:12]:
            text = str(f.get("text", "")).strip()
            kind = str(f.get("kind", "commit")).strip()
            if not text or kind not in ("commit", "own"):
                continue
            try:
                uid = int(str(f.get("uid") or "0").lstrip("#") or 0)
            except Exception:
                uid = 0
            if uid not in valid:
                continue
            due = str(f.get("due", "") or "").strip()
            src_text = next((t for u, t in mine if u == uid), "")
            if not _due_to_ts(due):
                due = _local_due(src_text + " " + text, started) or ""
            exp = _due_to_ts(due) or (started + _ttl_days(kind) * 86400)
            db.insert("my_facts", {"meeting_id": mid, "utterance_id": uid, "kind": kind, "text": text[:200],
                                   "source_text": src_text[:300], "due": due, "weight": 1.0,
                                   "created_at": _now(), "expires_at": exp, "people": people})
            n += 1
    names = [s for s in (self_names or []) if s]
    for t in (todos or []):
        if not isinstance(t, dict):
            continue
        owner = str(t.get("owner", "") or "").lower()
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        if re.search(r"各自|全員|皆|みんな|每个人|大家|全体|everyone|everybody|\ball\b", text, re.IGNORECASE):
            continue
        if owner == "me" or any(nm in text for nm in names):
            import difflib as _dl
            if any(_dl.SequenceMatcher(None, r["text"], text).ratio() >= 0.45
                   for r in db.query("SELECT text FROM my_facts WHERE expires_at IS NULL OR expires_at > ?", (_now(),))):
                continue
            due = str(t.get("due", "") or "").strip()
            if not _due_to_ts(due):
                due = _local_due(text, started) or ""
            db.insert("my_facts", {"meeting_id": mid, "utterance_id": t.get("uid"), "kind": "todo", "text": text[:200],
                                   "source_text": "", "due": due, "weight": 1.0, "created_at": _now(),
                                   "expires_at": _due_to_ts(due) or (started + COMMIT_TTL_DAYS * 86400), "people": people})
            n += 1
    if n:
        log.info("memory: %d fact(s) from meeting %s", n, mid)
        try:
            from .events import bus
            bus.publish({"type": "debug", "kind": "memory", "at": _now(), "text": f"meeting {mid}: {n} fact(s) learned"}, store=False)
        except Exception:
            pass
    return n


def _ttl_days(kind):
    return OWN_TTL_DAYS if kind == "own" else COMMIT_TTL_DAYS


def people_of_meeting(mid):
    if not mid:
        return []
    rows = db.query("""SELECT DISTINCT s.id FROM speakers s
                       WHERE s.name != '' AND s.name IS NOT NULL AND (
                           s.id IN (SELECT speaker_id FROM meeting_participants WHERE meeting_id=?)
                        OR s.id IN (SELECT DISTINCT speaker_id FROM utterances WHERE meeting_id=? AND speaker_id >= 0))""",
                    (mid, mid))
    return sorted(int(r["id"]) for r in rows)


def _is_named(spk):
    if spk is None or spk < 0:
        return False
    r = db.query_one("SELECT name FROM speakers WHERE id=?", (spk,))
    return bool(r and (r["name"] or "").strip())


def backfill_people():
    rows = db.query("SELECT id, meeting_id FROM my_facts WHERE people IS NULL")
    for r in rows:
        db.execute("UPDATE my_facts SET people=? WHERE id=?", (json.dumps(people_of_meeting(r["meeting_id"])), r["id"]))
    if rows:
        log.info("memory: bound %d legacy fact(s) to their meeting's people", len(rows))


_WD_JA = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
_WD_ZH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WD_EN = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def _local_due(text, started):
    import calendar
    import datetime as _dt
    t = (text or "").lower()
    base = _dt.datetime.fromtimestamp(started).date()
    def fmt(d):
        return d.strftime("%Y-%m-%d")
    if re.search(r"明後日|后天|day after tomorrow", t):
        return fmt(base + _dt.timedelta(days=2))
    if re.search(r"明日|明天|tomorrow", t):
        return fmt(base + _dt.timedelta(days=1))
    wd = None
    m = re.search(r"([月火水木金土日])曜", t)
    if m:
        wd = _WD_JA[m.group(1)]
    m = m or re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", t)
    if wd is None and m:
        wd = _WD_ZH[m.group(1)]
    if wd is None:
        m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
        if m:
            wd = _WD_EN[m.group(1)]
    nxt = bool(re.search(r"来週|下周|下星期|next week|next ", t))
    if wd is not None:
        if nxt:
            d = base + _dt.timedelta(days=7 - base.weekday() + wd)
        else:
            d = base + _dt.timedelta(days=(wd - base.weekday()) % 7)
        return fmt(d)
    if re.search(r"月末|月底|end of (the )?month|今月中|本月", t):
        return fmt(base.replace(day=calendar.monthrange(base.year, base.month)[1]))
    if re.search(r"来月|下个月|下月|next month", t):
        y, mo = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
        return fmt(_dt.date(y, mo, calendar.monthrange(y, mo)[1]))
    if nxt:
        return fmt(base + _dt.timedelta(days=(6 - base.weekday()) + 7))
    if re.search(r"今週|本周|this week|週内", t):
        return fmt(base + _dt.timedelta(days=6 - base.weekday()))
    return None


def _due_to_ts(due):
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", due or "")
    if not m:
        return None
    import datetime
    try:
        d = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59)
        return d.timestamp() + 3 * 86400
    except Exception:
        return None


def _terms(text):
    ts = [t for t in re.findall(r"[一-鿿゠-ヿ]{2,}|[A-Za-z0-9][A-Za-z0-9+\-]{1,}", text or "") if re.search(r"[A-Za-z一-鿿゠-ヿ]", t)]
    stop = {"それ", "これ", "あれ", "ここ", "そこ", "こと", "もの", "ところ", "ため", "よう", "について", "とか", "ちょっと"}
    out = []
    for t in ts:
        if t in stop or t in out:
            continue
        out.append(t)
    return out[:8]


def retrieve(trigger_text, current_mid=None, trigger_spk=None):
    terms = _terms(trigger_text)
    facts, utts = [], []
    now = _now()
    spk_named = _is_named(trigger_spk) if trigger_spk is not None else None
    cur_people = set(people_of_meeting(current_mid)) if (trigger_spk is not None and current_mid) else set()
    if terms:
        rows = db.query("""SELECT f.*, m.title AS mtitle, m.started_at FROM my_facts f
                           LEFT JOIN meetings m ON m.id = f.meeting_id
                           WHERE f.weight >= ? AND (f.expires_at IS NULL OR f.expires_at > ?)
                           ORDER BY f.created_at DESC LIMIT 200""", (MIN_WEIGHT, now))
        scored = []
        for r in rows:
            if effective_weight(r, now) < MIN_WEIGHT:
                continue
            try:
                ppl = set(json.loads(r["people"] or "[]")) if r.get("people") else set()
            except Exception:
                ppl = set()
            if ppl and trigger_spk is not None and trigger_spk >= 0:
                if spk_named and trigger_spk not in ppl:
                    continue
                if not spk_named and not (cur_people & ppl):
                    continue
            hay = (r["text"] or "") + " " + (r["source_text"] or "")
            hit = [t for t in terms if t in hay]
            score = len(hit)
            if score >= 2 or any(len(t) >= 4 for t in hit):
                scored.append((score, r["weight"], r))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        facts = [dict(r) for _s, _w, r in scored[:MAX_INJECT_FACTS]]
        try:
            q = " OR ".join(f'"{t}"' for t in terms[:6])
            ids = db.fts_search("utterances_fts", q, limit=40)
        except Exception:
            ids = []
        if ids:
            ph = ",".join("?" * len(ids))
            for u in db.query(f"""SELECT u.id, u.meeting_id, u.text, u.corrected_text, u.ts, m.title AS mtitle
                                  FROM utterances u LEFT JOIN meetings m ON m.id = u.meeting_id
                                  WHERE u.id IN ({ph}) AND u.speaker_id = -1 AND u.meeting_id != COALESCE(?, -1)
                                  ORDER BY u.ts DESC LIMIT ?""", (*ids, current_mid, MAX_INJECT_UTTS)):
                if len((u["corrected_text"] or u["text"] or "")) >= MIN_SELF_CHARS:
                    utts.append(dict(u))
    return facts, utts


def prompt_block(facts, utts):
    import datetime
    if not facts and not utts:
        return ""
    lines = ["=== User statements and commitments (reference only; ignore if unrelated) ==="]
    for f in facts:
        d = datetime.datetime.fromtimestamp(f.get("started_at") or f["created_at"]).strftime("%m/%d")
        due = f" Due {f['due']}" if f.get("due") else ""
        lines.append(f"[F{f['id']}|{d} {(f.get('mtitle') or '')[:16]}] {f['kind']}: {f['text']}{due}")
    for u in utts:
        d = datetime.datetime.fromtimestamp(u["ts"]).strftime("%m/%d")
        lines.append(f"[U#{u['id']}|{d} {(u.get('mtitle') or '')[:16]}] User said: {(u['corrected_text'] or u['text'])[:100]}")
    return "\n".join(lines)


def effective_weight(row, now=None):
    now = now or _now()
    ref = row.get("used_at") or row.get("created_at") or now
    return float(row.get("weight") or 0) - DECAY_PER_WEEK * max(0.0, (now - ref) / (7 * 86400))


def mark_used(fact_ids):
    now = _now()
    for fid in fact_ids or []:
        r = db.query_one("SELECT kind, expires_at FROM my_facts WHERE id=?", (fid,))
        if not r:
            continue
        exp = float(r.get("expires_at") or 0)
        if r.get("kind") == "own":
            exp = max(exp, now + OWN_TTL_DAYS * 86400)
        else:
            exp = min(exp or 1e18, now + USED_LINGER_DAYS * 86400)
        db.execute("UPDATE my_facts SET used_at=?, expires_at=? WHERE id=?", (now, exp, fid))


def feedback(fact_ids, delta):
    for fid in fact_ids or []:
        db.execute("UPDATE my_facts SET weight = MAX(0, MIN(2, weight + ?)) WHERE id=?", (delta, fid))


def list_facts():
    now = _now()
    names = {r["id"]: r["name"] for r in db.query("SELECT id, name FROM speakers WHERE name != '' AND name IS NOT NULL")}
    out = []
    for r in db.query("""SELECT f.*, m.title AS mtitle FROM my_facts f LEFT JOIN meetings m ON m.id = f.meeting_id
                         WHERE (f.expires_at IS NULL OR f.expires_at > ?) ORDER BY f.created_at DESC LIMIT 100""", (now,)):
        r = dict(r)
        try:
            ids = json.loads(r.get("people") or "[]")
        except Exception:
            ids = []
        r["people_names"] = [names[i] for i in ids if i in names]
        out.append(r)
    return out


def delete_fact(fid):
    db.execute("DELETE FROM my_facts WHERE id=?", (fid,))
