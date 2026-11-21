import base64
import json
import logging
import pathlib
import time

from . import db

log = logging.getLogger("trash")

RETENTION_DAYS = 30


def _row(r):
    d = dict(r)
    for k, v in list(d.items()):
        if isinstance(v, bytes):
            d[k] = None
            d["__b64_" + k] = base64.b64encode(v).decode()
    return d


def _insert(table, d):
    d = dict(d)
    for k in [k for k in d if k.startswith("__b64_")]:
        d[k[len("__b64_"):]] = base64.b64decode(d.pop(k))
    db.insert(table, d)


def _put(kind, ref_id, label, payload, files=None):
    db.insert("trash", {"kind": kind, "ref_id": ref_id, "label": (label or "")[:80],
                        "payload": json.dumps(payload, ensure_ascii=False),
                        "files": json.dumps(files or []), "deleted_at": db.now()})


def trash_meeting(mid: int):
    m = db.query_one("SELECT * FROM meetings WHERE id=?", (mid,))
    if not m:
        return False
    payload = {"meetings": [_row(m)]}
    files = []
    for t in ("utterances", "topics", "interventions", "meeting_contexts",
              "meeting_participants", "audio_segments", "meeting_anon_labels"):
        payload[t] = [_row(r) for r in db.query(f"SELECT * FROM {t} WHERE meeting_id=?", (mid,))]
    if m["audio_path"]:
        files.append(m["audio_path"])
    files += [r["path"] for r in payload["audio_segments"] if r.get("path")]
    _put("meeting", mid, m["title"] or f"#{mid}", payload, sorted(set(files)))
    db.execute("DELETE FROM meetings WHERE id=?", (mid,))
    return True


def trash_context(cid: int):
    c = db.query_one("SELECT * FROM contexts WHERE id=?", (cid,))
    if not c:
        return False
    payload = {"contexts": [_row(c)],
               "context_chunks": [_row(r) for r in db.query(
                   "SELECT * FROM context_chunks WHERE context_id=?", (cid,))]}
    files = [c["path"]] if c["path"] else []
    _put("context", cid, c["name"], payload, files)
    db.execute("DELETE FROM contexts WHERE id=?", (cid,))
    return True


def trash_speaker(sid: int, store):
    r = db.query_one("SELECT * FROM speakers WHERE id=?", (sid,))
    if not r:
        return False
    _put("speaker", sid, r["name"] or f"#{sid}", {"speakers": [_row(r)]})
    store.remove(sid)
    return True


def list_items():
    out = []
    for r in db.query("SELECT id, kind, ref_id, label, deleted_at FROM trash ORDER BY deleted_at DESC"):
        left = max(0, RETENTION_DAYS - (db.now() - r["deleted_at"]) / 86400)
        out.append({"id": r["id"], "kind": r["kind"], "ref_id": r["ref_id"],
                    "label": r["label"], "deleted_at": r["deleted_at"],
                    "days_left": round(left, 1)})
    return out


def restore(trash_id: int, store=None):
    r = db.query_one("SELECT * FROM trash WHERE id=?", (trash_id,))
    if not r:
        return {"error": "not found"}
    payload = json.loads(r["payload"])
    order = ("meetings", "contexts", "speakers", "utterances", "topics", "interventions",
             "meeting_contexts", "meeting_participants", "audio_segments",
             "meeting_anon_labels", "context_chunks")
    try:
        for table in order:
            for row in payload.get(table, []):
                _insert(table, row)
    except Exception as e:
        log.exception("restore failed")
        return {"error": f"restore failed: {e}"}
    if r["kind"] == "speaker" and store is not None:
        store.reload()
    db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    log.info("restored %s %s from trash", r["kind"], r["ref_id"])
    return {"ok": True, "kind": r["kind"]}


def purge_one(trash_id: int):
    r = db.query_one("SELECT * FROM trash WHERE id=?", (trash_id,))
    if not r:
        return False
    for f in json.loads(r["files"] or "[]"):
        pathlib.Path(f).unlink(missing_ok=True)
    db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    return True


def purge_kind(kind: str):
    rows = db.query("SELECT id FROM trash WHERE kind=?", (kind,))
    for r in rows:
        purge_one(r["id"])
    return len(rows)


def purge_expired():
    cutoff = db.now() - RETENTION_DAYS * 86400
    n = 0
    for r in db.query("SELECT id FROM trash WHERE deleted_at < ?", (cutoff,)):
        purge_one(r["id"])
        n += 1
    if n:
        log.info("trash: purged %d expired item(s)", n)
    return n
