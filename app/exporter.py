import base64
import datetime
import json
import logging
import pathlib
import shutil
import tempfile
import time
import zipfile
import uuid

from . import APP_VERSION, config, db

log = logging.getLogger("exporter")

EXPORT_VERSION = 1
FORMAT = "deban-export"
MAX_ARCHIVE_UNCOMPRESSED = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_JSON = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20000

MEETING_TABLES = ["utterances", "topics", "interventions", "near_misses", "meeting_participants",
                  "meeting_anon_labels", "audio_segments", "meeting_contexts", "my_facts"]


def _rows(sql, params=()):
    return [dict(r) for r in db.query(sql, params)]


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def export_archive(dest: pathlib.Path, meeting_ids=None, include_voiceprints=False) -> dict:
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if meeting_ids:
        ph = ",".join("?" * len(meeting_ids))
        meetings = _rows(f"SELECT * FROM meetings WHERE id IN ({ph}) ORDER BY started_at", tuple(meeting_ids))
    else:
        meetings = _rows("SELECT * FROM meetings WHERE status='done' ORDER BY started_at")
    speakers = _rows("SELECT * FROM speakers")
    ctx_ids = {r["context_id"] for m in meetings for r in _rows("SELECT context_id FROM meeting_contexts WHERE meeting_id=?", (m["id"],))}
    if not meeting_ids:
        ctx_ids |= {r["id"] for r in _rows("SELECT id FROM contexts")}
    contexts = _rows(f"SELECT * FROM contexts WHERE id IN ({','.join('?' * len(ctx_ids))})", tuple(ctx_ids)) if ctx_ids else []
    spk_uuid = {s["id"]: s["uuid"] for s in speakers}
    ctx_uuid = {c["id"]: c["uuid"] for c in contexts}
    counts = {"meetings": 0, "utterances": 0, "speakers": len(speakers), "contexts": len(contexts), "recordings": 0}

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        out = []
        for s in speakers:
            d = {k: v for k, v in s.items() if k != "embedding"}
            if include_voiceprints and s.get("embedding"):
                d["embedding_b64"] = base64.b64encode(s["embedding"]).decode()
            out.append(d)
        z.writestr("speakers.json", json.dumps(out, ensure_ascii=False, indent=1))
        for c in contexts:
            chunks = _rows("SELECT seq, text FROM context_chunks WHERE context_id=? ORDER BY seq", (c["id"],))
            entry = {**c, "chunks": chunks, "file": ""}
            p = pathlib.Path(c.get("path") or "")
            if c.get("path") and p.exists():
                arc = f"contexts/files/{c['uuid']}{p.suffix.lower()}"
                z.write(p, arc)
                entry["file"] = arc
            z.writestr(f"contexts/{c['uuid']}.json", json.dumps(entry, ensure_ascii=False))
        for m in meetings:
            mid = m["id"]
            entry = {"meeting": m}
            for t in MEETING_TABLES:
                rows = _rows(f"SELECT * FROM {t} WHERE meeting_id=?", (mid,))
                entry[t] = rows
            counts["utterances"] += len(entry["utterances"])
            for r in entry["meeting_participants"]:
                r["speaker_uuid"] = spk_uuid.get(r["speaker_id"])
            for r in entry["meeting_contexts"]:
                r["context_uuid"] = ctx_uuid.get(r["context_id"])
            for r in entry["my_facts"]:
                try:
                    r["people_uuids"] = [spk_uuid.get(int(x)) for x in json.loads(r.get("people") or "[]")]
                except Exception:
                    r["people_uuids"] = []
            files = []
            paths = [r["path"] for r in entry["audio_segments"]] or ([m["audio_path"]] if m.get("audio_path") else [])
            for i, pth in enumerate(paths):
                p = pathlib.Path(pth or "")
                if p.exists():
                    arc = f"recordings/{m['uuid']}/{p.name}"
                    z.write(p, arc)
                    files.append({"path": pth, "arc": arc})
                    counts["recordings"] += 1
            entry["recording_files"] = files
            z.writestr(f"meetings/{m['uuid']}.json", json.dumps(entry, ensure_ascii=False))
            counts["meetings"] += 1
        cfg = dict(config.load())
        z.writestr("settings/config.json", json.dumps(cfg, ensure_ascii=False, indent=1))
        for name in ("profile.yaml", "glossary.txt"):
            p = config.DATA_DIR / name
            if p.exists():
                z.write(p, f"settings/{name}")
        if include_voiceprints:
            p = config.DATA_DIR / "speakers" / "self.npy"
            if p.exists():
                z.write(p, "voiceprints/self.npy")
        z.writestr("manifest.json", json.dumps({
            "format": FORMAT, "export_version": EXPORT_VERSION, "app_version": APP_VERSION,
            "schema_version": db.SCHEMA_VERSION, "created_at": time.time(),
            "created_at_iso": datetime.datetime.now().isoformat(timespec="seconds"),
            "include_voiceprints": bool(include_voiceprints), "counts": counts,
        }, ensure_ascii=False, indent=1))
    return counts


class ImportError_(Exception):
    pass


def _remap_uids(obj, umap):
    if isinstance(obj, list):
        return [_remap_uids(x, umap) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "uid" and isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = umap.get(int(v))
            elif k == "utterances" and isinstance(v, list) and all(isinstance(x, dict) for x in v):
                out[k] = [{**x, "uid": umap.get(int(x["uid"]))} if isinstance(x.get("uid"), (int, float)) else x for x in v]
            else:
                out[k] = _remap_uids(v, umap)
        return out
    return obj


def _remap_json_field(s, umap):
    try:
        return db.to_json(_remap_uids(json.loads(s), umap)) if s else s
    except Exception:
        return s


def _insert_row(table, row, drop=("id",)):
    d = {k: v for k, v in row.items() if k not in drop}
    cols = {r[1] for r in db.conn().execute(f"PRAGMA table_info({table})")}
    d = {k: v for k, v in d.items() if k in cols}
    return db.insert(table, d)


PORTABLE_CONFIG = ("src_lang", "out_lang", "translate_default", "record_audio", "mic_enabled", "self_name", "my_names")


def _restore_config(z, names):
    if "settings/config.json" not in names:
        return
    try:
        src = json.loads(z.read("settings/config.json").decode("utf-8"))
    except Exception:
        return
    try:
        disk = json.loads(config._config_path().read_text())
    except Exception:
        disk = {}
    keys = [k for k in config.DEFAULTS if k in PORTABLE_CONFIG or k.startswith("alert")]
    patch = {k: src[k] for k in keys if k in src and k not in disk and src[k] != config.DEFAULTS.get(k)}
    if patch:
        config.update(patch)


def import_archive(src: pathlib.Path, restore_voiceprints=False) -> dict:
    src = pathlib.Path(src)
    if not zipfile.is_zipfile(src):
        raise ImportError_("not a Deban export (zip expected)")
    summary = {"meetings": 0, "skipped": 0, "utterances": 0, "speakers_new": 0, "contexts_new": 0, "recordings": 0, "errors": []}
    with zipfile.ZipFile(src) as z, tempfile.TemporaryDirectory() as tmp:
        entries = z.infolist()
        if (len(entries) > MAX_ARCHIVE_ENTRIES
                or sum(entry.file_size for entry in entries) > MAX_ARCHIVE_UNCOMPRESSED
                or any(entry.filename.endswith(".json") and entry.file_size > MAX_ARCHIVE_JSON for entry in entries)
                or len({entry.filename for entry in entries}) != len(entries)):
            raise ImportError_("archive exceeds limits or has duplicate entries")
        names = {entry.filename for entry in entries}
        if "manifest.json" not in names:
            raise ImportError_("manifest.json missing")
        man = json.loads(z.read("manifest.json"))
        if man.get("format") != FORMAT:
            raise ImportError_("unknown archive format")
        if int(man.get("export_version", 1)) > EXPORT_VERSION:
            raise ImportError_(f"archive was written by a newer Deban (export v{man.get('export_version')}); please update the app")
        tmpd = pathlib.Path(tmp)

        spk_map = {}
        existing = _rows("SELECT id, uuid, name FROM speakers")
        by_uuid = {s["uuid"]: s["id"] for s in existing if s.get("uuid")}
        by_name = {s["name"]: s["id"] for s in existing if (s.get("name") or "").strip()}
        for s in json.loads(z.read("speakers.json")) if "speakers.json" in names else []:
            new_id = by_uuid.get(s.get("uuid")) or by_name.get((s.get("name") or "").strip() or None)
            if not new_id:
                row = {k: v for k, v in s.items() if k not in ("id", "embedding_b64")}
                if restore_voiceprints and s.get("embedding_b64"):
                    row["embedding"] = base64.b64decode(s["embedding_b64"])
                row.setdefault("created_at", time.time())
                new_id = _insert_row("speakers", row)
                summary["speakers_new"] += 1
                if s.get("uuid"):
                    by_uuid[s["uuid"]] = new_id
            spk_map[s["id"]] = new_id

        def spk(old):
            if old is None or old < 0:
                return old
            if old not in spk_map:
                spk_map[old] = db.insert("speakers", {"name": "", "created_at": time.time()})
            return spk_map[old]

        ctx_map = {}
        exist_ctx = _rows("SELECT id, uuid, content_hash FROM contexts")
        c_uuid = {c["uuid"]: c["id"] for c in exist_ctx if c.get("uuid")}
        c_hash = {c["content_hash"]: c["id"] for c in exist_ctx if c.get("content_hash")}
        for n in sorted(x for x in names if x.startswith("contexts/") and x.endswith(".json")):
            c = json.loads(z.read(n))
            new_id = c_uuid.get(c.get("uuid")) or c_hash.get(c.get("content_hash") or None)
            if not new_id:
                row = {k: v for k, v in c.items() if k not in ("id", "chunks", "file")}
                if c.get("file") and c["file"] in names:
                    dest_dir = config.DATA_DIR / "contexts"; dest_dir.mkdir(parents=True, exist_ok=True)
                    suffix = pathlib.Path(c["file"]).suffix.lower()
                    if len(suffix) > 16 or (suffix and not suffix[1:].isalnum()):
                        raise ImportError_("invalid context file extension")
                    dest = dest_dir / f"{uuid.uuid4().hex}{suffix}"
                    with z.open(c["file"]) as fsrc, open(dest, "xb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    row["path"] = str(dest)
                else:
                    row["path"] = ""
                new_id = _insert_row("contexts", row)
                for ch in c.get("chunks") or []:
                    db.insert("context_chunks", {"context_id": new_id, "seq": ch["seq"], "text": ch["text"]})
                summary["contexts_new"] += 1
                if c.get("uuid"):
                    c_uuid[c["uuid"]] = new_id
            ctx_map[c["id"]] = new_id

        have = {m["uuid"] for m in _rows("SELECT uuid FROM meetings") if m.get("uuid")}
        entries = []
        for n in names:
            if n.startswith("meetings/") and n.endswith(".json"):
                entries.append(json.loads(z.read(n)))
        entries.sort(key=lambda e: e["meeting"].get("started_at") or 0)
        mtg_map = {}
        conn = db.conn()
        for e in entries:
            m = e["meeting"]
            if m.get("uuid") in have:
                summary["skipped"] += 1
                continue
            try:
              with db.transaction():
                row = {k: v for k, v in m.items() if k != "id"}
                row["prev_meeting_id"] = mtg_map.get(m.get("prev_meeting_id"))
                row["audio_path"] = None
                new_mid = _insert_row("meetings", row)
                mtg_map[m["id"]] = new_mid
                umap = {}
                for u in sorted(e.get("utterances", []), key=lambda r: r["id"]):
                    r = {k: v for k, v in u.items() if k != "id"}
                    r["meeting_id"] = new_mid; r["speaker_id"] = spk(u["speaker_id"])
                    umap[u["id"]] = _insert_row("utterances", r)
                summary["utterances"] += len(umap)
                for k in ("summary", "todos", "decisions"):
                    if m.get(k):
                        conn.execute(f"UPDATE meetings SET {k}=? WHERE id=?", (_remap_json_field(m[k], umap), new_mid))
                for t in e.get("topics", []):
                    r = {k: v for k, v in t.items() if k != "id"}; r["meeting_id"] = new_mid
                    r["points"] = _remap_json_field(r.get("points"), umap)
                    _insert_row("topics", r)
                for it in e.get("interventions", []):
                    r = {k: v for k, v in it.items() if k != "id"}; r["meeting_id"] = new_mid
                    r["utterance_id"] = umap.get(it.get("utterance_id"))
                    for k in ("sources", "context", "context_src", "facts"):
                        if r.get(k):
                            r[k] = _remap_json_field(r[k], umap) if k != "facts" else "[]"
                    _insert_row("interventions", r)
                for nm in e.get("near_misses", []):
                    r = {k: v for k, v in nm.items() if k != "id"}; r["meeting_id"] = new_mid
                    r["utterance_id"] = umap.get(nm.get("utterance_id"))
                    _insert_row("near_misses", r)
                for p in e.get("meeting_participants", []):
                    sid = by_uuid.get(p.get("speaker_uuid")) or spk(p["speaker_id"])
                    conn.execute("INSERT OR IGNORE INTO meeting_participants (meeting_id, speaker_id, name) VALUES (?,?,?)",
                                 (new_mid, sid, p.get("name") or ""))
                for a in e.get("meeting_anon_labels", []):
                    conn.execute("INSERT OR IGNORE INTO meeting_anon_labels (meeting_id, speaker_id, n) VALUES (?,?,?)",
                                 (new_mid, spk(a["speaker_id"]), a["n"]))
                for mc in e.get("meeting_contexts", []):
                    cid = c_uuid.get(mc.get("context_uuid")) or ctx_map.get(mc["context_id"])
                    if cid:
                        conn.execute("INSERT OR IGNORE INTO meeting_contexts (meeting_id, context_id, name, priority) VALUES (?,?,?,?)",
                                     (new_mid, cid, mc.get("name") or "", mc.get("priority", 1)))
                for f in e.get("my_facts", []):
                    r = {k: v for k, v in f.items() if k not in ("id", "people_uuids")}; r["meeting_id"] = new_mid
                    r["utterance_id"] = umap.get(f.get("utterance_id"))
                    ppl = [by_uuid.get(u) for u in (f.get("people_uuids") or []) if by_uuid.get(u)]
                    r["people"] = json.dumps(ppl)
                    _insert_row("my_facts", r)
                adir = config.DATA_DIR / "audio"; adir.mkdir(parents=True, exist_ok=True)
                arc_by_old = {f["path"]: f["arc"] for f in e.get("recording_files", [])}
                first_path = None
                for i, seg in enumerate(e.get("audio_segments", [])):
                    arc = arc_by_old.get(seg["path"])
                    if not arc or arc not in names:
                        continue
                    dest = adir / f"meeting-{new_mid}{'' if i == 0 else f'-p{i}'}{pathlib.Path(arc).suffix}"
                    with z.open(arc) as fsrc, open(dest, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    db.insert("audio_segments", {"meeting_id": new_mid, "path": str(dest),
                                                 "start_off": seg.get("start_off") or 0, "duration": seg.get("duration")})
                    first_path = first_path or str(dest)
                    summary["recordings"] += 1
                if not e.get("audio_segments") and m.get("audio_path") in arc_by_old and arc_by_old[m["audio_path"]] in names:
                    arc = arc_by_old[m["audio_path"]]
                    dest = adir / f"meeting-{new_mid}{pathlib.Path(arc).suffix}"
                    with z.open(arc) as fsrc, open(dest, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    first_path = str(dest); summary["recordings"] += 1
                if first_path:
                    conn.execute("UPDATE meetings SET audio_path=? WHERE id=?", (first_path, new_mid))
              have.add(m.get("uuid"))
              summary["meetings"] += 1
            except Exception as ex:
                log.exception("import: meeting %s failed", m.get("title"))
                summary["errors"].append(f"{m.get('title') or m.get('uuid')}: {ex}")
        try:
            _restore_config(z, names)
            for name in ("profile.yaml", "glossary.txt"):
                p = config.DATA_DIR / name
                if f"settings/{name}" in names and not p.exists():
                    p.write_bytes(z.read(f"settings/{name}"))
            if restore_voiceprints and "voiceprints/self.npy" in names:
                p = config.DATA_DIR / "speakers" / "self.npy"
                if not p.exists():
                    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(z.read("voiceprints/self.npy"))
        except Exception:
            log.exception("import: settings")
    return summary
