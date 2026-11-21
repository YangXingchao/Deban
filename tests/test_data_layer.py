import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable


def run(code: str, data_dir: pathlib.Path, env=None) -> dict:
    e = {**os.environ, "MC_DATA_DIR": str(data_dir), **(env or {})}
    r = subprocess.run([PY, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"subprocess failed:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


SEED = """
import json, time
from app import db
mid = db.insert("meetings", {"title": "t1", "started_at": time.time() - 600, "created_at": time.time(), "status": "done",
                             "summary": json.dumps([{"text": "p", "uid": 0}])})
spk = db.insert("speakers", {"name": "デバン", "created_at": time.time()})
u1 = db.insert("utterances", {"meeting_id": mid, "speaker_id": spk, "start_time": 1.0, "ts": time.time(), "text": "こんにちは太陽"})
u2 = db.insert("utterances", {"meeting_id": mid, "speaker_id": 999, "start_time": 5.0, "ts": time.time(), "text": "匿名の発言"})
db.execute("UPDATE meetings SET summary=? WHERE id=?", (json.dumps([{"text": "p", "uid": u1}]), mid))
db.insert("topics", {"meeting_id": mid, "start_ts": 1, "end_ts": 2, "title": "T", "points": json.dumps([{"text": "x", "uid": u2}])})
db.execute("INSERT INTO meeting_anon_labels (meeting_id, speaker_id, n) VALUES (?,?,?)", (mid, 999, 1))
db.execute("INSERT INTO meeting_participants (meeting_id, speaker_id, name) VALUES (?,?,?)", (mid, spk, ""))
"""


class SchemaTests(unittest.TestCase):
    def test_fresh_db_gets_current_version_and_uuids(self):
        with tempfile.TemporaryDirectory() as d:
            out = run(SEED + """
from app import db
print(json.dumps({"uv": db.user_version(), "schema": db.SCHEMA_VERSION,
                  "uuid_ok": all(r["uuid"] for r in db.query("SELECT uuid FROM meetings UNION ALL SELECT uuid FROM speakers"))}))
""", pathlib.Path(d))
            self.assertEqual(out["uv"], out["schema"])
            self.assertTrue(out["uuid_ok"])

    def test_legacy_db_is_snapshotted_before_migration(self):
        with tempfile.TemporaryDirectory() as d:
            dd = pathlib.Path(d)
            import sqlite3
            c = sqlite3.connect(dd / "database.sqlite")
            c.executescript("CREATE TABLE meetings (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'live');"
                            "INSERT INTO meetings (title, started_at, created_at, status) VALUES ('old', 1, 1, 'done');")
            c.commit(); c.close()
            out = run("""
import json
from app import db, config
uv = db.user_version()   # Open and migrate the database before checking the snapshot.
snaps = sorted(p.name for p in (config.DATA_DIR / "backups").glob("pre-migrate-v0-*.sqlite"))
print(json.dumps({"uv": uv, "snaps": snaps, "uuid": db.query_one("SELECT uuid FROM meetings")["uuid"] is not None}))
""", pathlib.Path(d))
            self.assertEqual(out["uv"], 2)
            self.assertEqual(len(out["snaps"]), 1)
            self.assertTrue(out["uuid"])

    def test_newer_db_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            dd = pathlib.Path(d)
            import sqlite3
            c = sqlite3.connect(dd / "database.sqlite"); c.execute("PRAGMA user_version = 99"); c.execute("CREATE TABLE x (a)"); c.commit(); c.close()
            out = run("""
import json
from app import db
try:
    db.conn(); print(json.dumps({"refused": False}))
except db.DBTooNew:
    print(json.dumps({"refused": True}))
""", pathlib.Path(d))
            self.assertTrue(out["refused"])


class SpeakerHistoryTests(unittest.TestCase):
    def test_history_bind_transfers_voice_without_reassigning_other_meetings(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json, time
import numpy as np
from app import db, meetings
from app.speakers import SpeakerStore
now = time.time()
m1 = db.insert("meetings", {"title": "one", "started_at": now, "created_at": now, "status": "done"})
m2 = db.insert("meetings", {"title": "two", "started_at": now, "created_at": now, "status": "done"})
member = db.insert("speakers", {"name": "Member", "created_at": now})
anon = db.insert("speakers", {"created_at": now,
    "embedding": np.array([1.0, 0.0], dtype=np.float64).tobytes(), "count": 3})
db.insert("utterances", {"meeting_id": m1, "speaker_id": anon, "start_time": 0, "ts": now, "text": "first"})
db.insert("utterances", {"meeting_id": m2, "speaker_id": anon, "start_time": 0, "ts": now, "text": "second"})
result = meetings.speaker_edit(m1, "bind", anon, member, store=SpeakerStore())
target = db.query_one("SELECT embedding, count FROM speakers WHERE id=?", (member,))
source = db.query_one("SELECT embedding, count FROM speakers WHERE id=?", (anon,))
first = db.query_one("SELECT speaker_id FROM utterances WHERE meeting_id=?", (m1,))
second = db.query_one("SELECT speaker_id FROM utterances WHERE meeting_id=?", (m2,))
print(json.dumps({"ok": result.get("ok"), "target_voice": bool(target["embedding"]),
    "target_count": target["count"], "source_voice": bool(source["embedding"]),
    "source_count": source["count"], "first": first["speaker_id"], "second": second["speaker_id"],
    "member": member, "anon": anon}))
""", pathlib.Path(d))
            self.assertTrue(out["ok"])
            self.assertTrue(out["target_voice"])
            self.assertEqual(out["target_count"], 3)
            self.assertFalse(out["source_voice"])
            self.assertEqual(out["source_count"], 0)
            self.assertEqual(out["first"], out["member"])
            self.assertEqual(out["second"], out["anon"])

    def test_history_bind_keeps_an_already_known_members_voice(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json, time
import numpy as np
from app import db, meetings
from app.speakers import SpeakerStore
now = time.time()
m1 = db.insert("meetings", {"title": "one", "started_at": now, "created_at": now, "status": "done"})
m2 = db.insert("meetings", {"title": "two", "started_at": now, "created_at": now, "status": "done"})
member_emb = np.array([0.0, 1.0], dtype=np.float64).tobytes()
anon_emb = np.array([1.0, 0.0], dtype=np.float64).tobytes()
member = db.insert("speakers", {"name": "Known", "created_at": now, "embedding": member_emb, "count": 7})
anon = db.insert("speakers", {"created_at": now, "embedding": anon_emb, "count": 3})
db.insert("utterances", {"meeting_id": m1, "speaker_id": anon, "start_time": 0, "ts": now, "text": "first"})
db.insert("utterances", {"meeting_id": m2, "speaker_id": anon, "start_time": 0, "ts": now, "text": "second"})
meetings.speaker_edit(m1, "bind", anon, member, store=SpeakerStore())
target = db.query_one("SELECT embedding, count FROM speakers WHERE id=?", (member,))
source = db.query_one("SELECT embedding FROM speakers WHERE id=?", (anon,))
first = db.query_one("SELECT speaker_id FROM utterances WHERE meeting_id=?", (m1,))
print(json.dumps({"target_kept": target["embedding"] == member_emb, "target_count": target["count"],
    "source_kept": source["embedding"] == anon_emb, "first": first["speaker_id"], "member": member}))
""", pathlib.Path(d))
            self.assertTrue(out["target_kept"])
            self.assertEqual(out["target_count"], 7)
            self.assertTrue(out["source_kept"])
            self.assertEqual(out["first"], out["member"])


class ExportImportTests(unittest.TestCase):
    def test_round_trip_and_dedup(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            arc = pathlib.Path(a) / "x.deban"
            exp = run(SEED + f"""
from app import exporter
import pathlib
print(json.dumps(exporter.export_archive(pathlib.Path({str(arc)!r}), None, include_voiceprints=True)))
""", pathlib.Path(a))
            self.assertEqual(exp["meetings"], 1)
            self.assertEqual(exp["utterances"], 2)
            imp = run(f"""
import json, pathlib
from app import exporter, db
s1 = exporter.import_archive(pathlib.Path({str(arc)!r}))
s2 = exporter.import_archive(pathlib.Path({str(arc)!r}))
m = db.query_one("SELECT id, summary FROM meetings")
uid = json.loads(m["summary"])[0]["uid"]
tp = json.loads(db.query_one("SELECT points FROM topics")["points"])[0]["uid"]
print(json.dumps({{"s1": s1, "s2": s2,
  "summary_uid_valid": db.query_one("SELECT 1 x FROM utterances WHERE id=?", (uid,)) is not None,
  "topic_uid_valid": db.query_one("SELECT 1 x FROM utterances WHERE id=?", (tp,)) is not None,
  "anon_label": db.query_one("SELECT COUNT(*) c FROM meeting_anon_labels")["c"],
  "named": db.query_one("SELECT name FROM speakers WHERE name != ''")["name"],
  "fts": len(db.fts_search("utterances_fts", "太陽", limit=5))}}))
""", pathlib.Path(b))
            self.assertEqual(imp["s1"]["meetings"], 1)
            self.assertEqual(imp["s1"]["utterances"], 2)
            self.assertEqual(imp["s2"]["skipped"], 1)
            self.assertEqual(imp["s2"]["meetings"], 0)
            self.assertTrue(imp["summary_uid_valid"] and imp["topic_uid_valid"])
            self.assertEqual(imp["anon_label"], 1)
            self.assertEqual(imp["named"], "デバン")
            self.assertEqual(imp["fts"], 1)

    def test_import_bounds_and_untrusted_context_filename(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json, pathlib, zipfile
from app import config, exporter
config.ensure_dirs()
(config.DATA_DIR / 'contexts' / 'existing.txt').write_text('original')
archive = config.DATA_DIR / 'test.deban'
with zipfile.ZipFile(archive, 'w') as z:
    z.writestr('manifest.json', json.dumps({'format': exporter.FORMAT, 'export_version': 1}))
    z.writestr('contexts/item.json', json.dumps({'id': 1, 'uuid': '../../outside', 'name': 'Example', 'type': 'txt', 'created_at': 1, 'path': 'existing.txt', 'file': 'contexts/files/example.txt', 'chunks': []}))
    z.writestr('contexts/files/example.txt', 'imported')
exporter.import_archive(archive)
from app import db
row = db.query_one('SELECT path FROM contexts')
imported = pathlib.Path(row['path'])
exporter.MAX_ARCHIVE_UNCOMPRESSED = 1
try:
    exporter.import_archive(archive)
    rejected = False
except exporter.ImportError_:
    rejected = True
print(json.dumps({'inside': imported.parent == config.DATA_DIR / 'contexts', 'content': imported.read_text(),
                  'original': (config.DATA_DIR / 'contexts' / 'existing.txt').read_text(),
                  'outside': (config.DATA_DIR.parent / 'outside.txt').exists(), 'rejected': rejected}))
""", pathlib.Path(d))
            self.assertEqual(out, {"inside": True, "content": "imported", "original": "original", "outside": False, "rejected": True})

    def test_rejects_foreign_zip(self):
        with tempfile.TemporaryDirectory() as d:
            z = pathlib.Path(d) / "junk.zip"
            import zipfile
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("hello.txt", "x")
            out = run(f"""
import json, pathlib
from app import exporter
try:
    exporter.import_archive(pathlib.Path({str(z)!r})); print(json.dumps({{"err": ""}}))
except exporter.ImportError_ as e:
    print(json.dumps({{"err": str(e)}}))
""", pathlib.Path(d))
            self.assertIn("manifest", out["err"])


class ConfigTests(unittest.TestCase):
    def test_new_install_defaults_to_recording_and_alerts_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json
from app import config
print(json.dumps({"record": config.get("record_audio"), "alerts": config.get("alerts_enabled")}))
""", pathlib.Path(d))
            self.assertTrue(out["record"])
            self.assertTrue(out["alerts"])

    def test_traditional_chinese_is_a_valid_output_language(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json
from app import config
config.update({"out_lang": "zh-Hant"})
print(json.dumps({"out_lang": config.get("out_lang")}))
""", pathlib.Path(d))
            self.assertEqual(out["out_lang"], "zh-Hant")

    def test_provider_key_is_not_a_user_setting(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json
from app import config
before = dict(config.load())
after = config.update({"openai_api_key": "must-not-persist"})
print(json.dumps({"before": "openai_api_key" in before,
                  "after": "openai_api_key" in after,
                  "stored": (config.DATA_DIR / "config.json").read_text() if (config.DATA_DIR / "config.json").exists() else ""}))
""", pathlib.Path(d))
            self.assertFalse(out["before"])
            self.assertFalse(out["after"])
            self.assertNotIn("must-not-persist", out["stored"])

    def test_data_dir_env_wins(self):
        with tempfile.TemporaryDirectory() as d:
            out = run("""
import json
from app import config
print(json.dumps({"dir": str(config.DATA_DIR)}))
""", pathlib.Path(d))
            self.assertEqual(out["dir"], d)


class ConfigRoundTrip(unittest.TestCase):

    def test_config_restored_only_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = (pathlib.Path(tmp) / x for x in ("a", "b", "c"))
            for d in (a, b, c):
                d.mkdir()
            out = run(SEED + """
import pathlib
from app import config, exporter
config.update({"my_names": ["Deban", "デバン"], "self_name": "Deban", "out_lang": "ja", "alert_consider": False})
dest = pathlib.Path(%r) / "x.deban"
exporter.export_archive(dest)
print(json.dumps({"ok": dest.exists()}))
""" % str(a), a)
            self.assertTrue(out["ok"])
            arc = str(a / "x.deban")
            got = run("""
import json, pathlib
from app import config, exporter
exporter.import_archive(pathlib.Path(%r))
print(json.dumps(json.loads(config._config_path().read_text())))
""" % arc, b)
            self.assertEqual(got.get("my_names"), ["Deban", "デバン"])
            self.assertEqual(got.get("self_name"), "Deban")
            self.assertEqual(got.get("out_lang"), "ja")
            self.assertIs(got.get("alert_consider"), False)
            self.assertNotIn("port", got)
            kept = run("""
import json, pathlib
from app import config, exporter
config.update({"my_names": ["pre"], "out_lang": "en"})
exporter.import_archive(pathlib.Path(%r))
print(json.dumps(json.loads(config._config_path().read_text())))
""" % arc, c)
            self.assertEqual(kept.get("my_names"), ["pre"])
            self.assertEqual(kept.get("out_lang"), "en")
            self.assertEqual(kept.get("self_name"), "Deban")


if __name__ == "__main__":
    unittest.main()
