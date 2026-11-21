import json
import logging
import sqlite3
import threading
import time

from .config import DATA_DIR

_lock = threading.RLock()
log = logging.getLogger("db")

SCHEMA_VERSION = 2


class DBTooNew(Exception):
    pass
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL,
  ended_at REAL,
  duration REAL,
  language TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'live',
  audio_path TEXT,
  role TEXT DEFAULT '',
  max_spk INTEGER DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS utterances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  speaker_id INTEGER NOT NULL,
  start_time REAL NOT NULL,
  end_time REAL,
  ts REAL NOT NULL,
  lang TEXT DEFAULT '',
  text TEXT NOT NULL,
  translation TEXT DEFAULT '',
  corrected_text TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS speakers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT DEFAULT '',
  embedding BLOB,
  count INTEGER DEFAULT 1,
  language TEXT DEFAULT '',
  lang_votes TEXT DEFAULT '{}',
  role TEXT DEFAULT '',
  company TEXT DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS contexts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  path TEXT DEFAULT '',
  content_hash TEXT DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS context_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  context_id INTEGER NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_contexts (
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  context_id INTEGER NOT NULL,
  name TEXT DEFAULT '',
  PRIMARY KEY (meeting_id, context_id)
);
CREATE TABLE IF NOT EXISTS meeting_participants (
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  speaker_id INTEGER NOT NULL,
  name TEXT DEFAULT '',
  PRIMARY KEY (meeting_id, speaker_id)
);
CREATE TABLE IF NOT EXISTS interventions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  utterance_id INTEGER,
  type TEXT NOT NULL,
  confidence REAL DEFAULT 0,
  urgency TEXT DEFAULT '',
  by_speaker TEXT DEFAULT '',
  question TEXT DEFAULT '',
  context TEXT DEFAULT '[]',
  suggested_answer TEXT DEFAULT '',
  answer_explain TEXT DEFAULT '',
  user_action TEXT DEFAULT '',
  created_at REAL NOT NULL
);
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
CREATE TABLE IF NOT EXISTS near_misses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  utterance_id INTEGER,
  by_speaker TEXT DEFAULT '',
  text TEXT DEFAULT '',
  lane TEXT DEFAULT '',
  llm_type TEXT DEFAULT '',
  confidence REAL DEFAULT 0,
  why TEXT DEFAULT '',
  verdict TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS misses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audio_segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  start_off REAL DEFAULT 0,
  duration REAL
);
CREATE TABLE IF NOT EXISTS meeting_anon_labels (
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  speaker_id INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (meeting_id, speaker_id)
);
CREATE TABLE IF NOT EXISTS trash (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER NOT NULL,
  label TEXT DEFAULT '',
  payload TEXT NOT NULL,
  files TEXT DEFAULT '[]',
  deleted_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  meeting_id INTEGER,
  role TEXT NOT NULL,
  backend TEXT DEFAULT '',
  model TEXT DEFAULT '',
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  tokens_cached INTEGER DEFAULT 0,
  latency_ms INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  start_ts REAL, end_ts REAL,
  title TEXT DEFAULT '',
  points TEXT DEFAULT '[]'
);
CREATE VIRTUAL TABLE IF NOT EXISTS utterances_fts USING fts5(
  text, translation, corrected_text, content='utterances', content_rowid='id',
  tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS utt_ai AFTER INSERT ON utterances BEGIN
  INSERT INTO utterances_fts(rowid, text, translation, corrected_text)
  VALUES (new.id, new.text, new.translation, new.corrected_text);
END;
CREATE TRIGGER IF NOT EXISTS utt_au AFTER UPDATE ON utterances BEGIN
  INSERT INTO utterances_fts(utterances_fts, rowid, text, translation, corrected_text)
  VALUES ('delete', old.id, old.text, old.translation, old.corrected_text);
  INSERT INTO utterances_fts(rowid, text, translation, corrected_text)
  VALUES (new.id, new.text, new.translation, new.corrected_text);
END;
CREATE TRIGGER IF NOT EXISTS utt_ad AFTER DELETE ON utterances BEGIN
  INSERT INTO utterances_fts(utterances_fts, rowid, text, translation, corrected_text)
  VALUES ('delete', old.id, old.text, old.translation, old.corrected_text);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, content='context_chunks', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS chk_ai AFTER INSERT ON context_chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chk_ad AFTER DELETE ON context_chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


def conn():
    global _conn
    with _lock:
        if _conn is None:
            path = DATA_DIR / "database.sqlite"
            _conn = sqlite3.connect(path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            uv = int(_conn.execute("PRAGMA user_version").fetchone()[0])
            if uv > SCHEMA_VERSION:
                _conn.close(); _conn = None
                raise DBTooNew(f"database schema v{uv} is newer than this app supports (v{SCHEMA_VERSION}); please update Deban")
            had_tables = _conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
            if uv < SCHEMA_VERSION and had_tables:
                _snapshot_before_migration(path, uv)
            _conn.executescript(SCHEMA)
            cols = [r[1] for r in _conn.execute("PRAGMA table_info(meeting_contexts)")]
            if cols and "name" not in cols:
                _conn.executescript("""
                    CREATE TABLE meeting_contexts_new (
                      meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                      context_id INTEGER NOT NULL, name TEXT DEFAULT '',
                      PRIMARY KEY (meeting_id, context_id));
                    INSERT INTO meeting_contexts_new
                      SELECT mc.meeting_id, mc.context_id, COALESCE(c.name, '')
                      FROM meeting_contexts mc LEFT JOIN contexts c ON c.id = mc.context_id;
                    DROP TABLE meeting_contexts;
                    ALTER TABLE meeting_contexts_new RENAME TO meeting_contexts;
                """)
            for mig in ("ALTER TABLE meeting_participants ADD COLUMN name TEXT DEFAULT ''",
                        "ALTER TABLE meetings ADD COLUMN max_spk INTEGER DEFAULT 0",
                        "ALTER TABLE meetings ADD COLUMN summary TEXT DEFAULT '[]'",
                        "ALTER TABLE meetings ADD COLUMN todos TEXT DEFAULT '[]'",
                        "ALTER TABLE meetings ADD COLUMN decisions TEXT DEFAULT '[]'",
                        "ALTER TABLE interventions ADD COLUMN latency_ms INTEGER DEFAULT 0",
                        "ALTER TABLE interventions ADD COLUMN reply_guide TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN reply_guide_src TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN question_src TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN why_src TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN context_src TEXT DEFAULT '[]'",
                        "ALTER TABLE interventions ADD COLUMN stance TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN replies TEXT DEFAULT '{}'",
                        "ALTER TABLE topics ADD COLUMN title_src TEXT DEFAULT ''",
                        "ALTER TABLE meetings ADD COLUMN prev_meeting_id INTEGER",
                        "ALTER TABLE interventions ADD COLUMN facts TEXT DEFAULT '[]'",
                        "ALTER TABLE my_facts ADD COLUMN used_at REAL",
                        "ALTER TABLE my_facts ADD COLUMN people TEXT",
                        "ALTER TABLE near_misses ADD COLUMN reason TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN verdict TEXT",
                        "ALTER TABLE near_misses ADD COLUMN detail TEXT DEFAULT ''",
                        "ALTER TABLE meetings ADD COLUMN starred INTEGER DEFAULT 0",
                        "ALTER TABLE meetings ADD COLUMN memo TEXT DEFAULT ''",
                        "ALTER TABLE meetings ADD COLUMN translate_on INTEGER DEFAULT 1",
                        "ALTER TABLE meeting_contexts ADD COLUMN priority INTEGER DEFAULT 1",
                        "ALTER TABLE meetings ADD COLUMN series_id INTEGER",
                        "ALTER TABLE meetings ADD COLUMN series_digest TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN sources TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN why TEXT DEFAULT ''",
                        "ALTER TABLE interventions ADD COLUMN lane TEXT DEFAULT ''",
                        "ALTER TABLE speakers ADD COLUMN note TEXT DEFAULT ''",
                        "ALTER TABLE speakers ADD COLUMN grp TEXT DEFAULT ''"):
                try:
                    _conn.execute(mig)
                except Exception:
                    pass
            for t in ("meetings", "speakers", "contexts"):
                try:
                    _conn.execute(f"ALTER TABLE {t} ADD COLUMN uuid TEXT")
                except Exception:
                    pass
                _conn.execute(f"UPDATE {t} SET uuid = lower(hex(randomblob(16))) WHERE uuid IS NULL OR uuid = ''")
                _conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_uuid ON {t}(uuid)")
            if uv < SCHEMA_VERSION:
                _conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                if had_tables:
                    log.info("db: schema migrated v%s → v%s", uv, SCHEMA_VERSION)
            _conn.commit()
        return _conn


def classify_error(e):
    if isinstance(e, DBTooNew):
        return "db_too_new"
    if isinstance(e, sqlite3.Error) or "database" in str(e).lower() or "sqlite" in str(e).lower():
        return "db_error"
    return "generic"


def _snapshot_before_migration(path, from_version):
    bdir = DATA_DIR / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"pre-migrate-v{from_version}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite"
    try:
        src = sqlite3.connect(path)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        log.info("db: snapshot before migration → %s", dest.name)
    except Exception:
        log.exception("db: pre-migration snapshot failed")


def user_version():
    return int(conn().execute("PRAGMA user_version").fetchone()[0])


_tx_depth = 0


class transaction:
    def __enter__(self):
        global _tx_depth
        _lock.acquire()
        if _tx_depth == 0:
            conn().execute("BEGIN")
        _tx_depth += 1
        return self

    def __exit__(self, et, ev, tb):
        global _tx_depth
        _tx_depth -= 1
        try:
            if _tx_depth == 0:
                if et is None:
                    conn().commit()
                else:
                    conn().rollback()
        finally:
            _lock.release()
        return False


def execute(sql, params=()):
    with _lock:
        cur = conn().execute(sql, params)
        if _tx_depth == 0:
            conn().commit()
        return cur


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def insert(table, data: dict) -> int:
    if table in ("meetings", "speakers", "contexts") and not data.get("uuid"):
        import uuid as _uuid
        data = {**data, "uuid": _uuid.uuid4().hex}
    keys = ",".join(data)
    ph = ",".join("?" * len(data))
    cur = execute(f"INSERT INTO {table} ({keys}) VALUES ({ph})", tuple(data.values()))
    return cur.lastrowid


def now():
    return time.time()


def fts_escape(q: str) -> str:
    import re as _re
    q = _re.sub(r"[\x00-\x1f]", " ", q or "")
    terms = [t.replace('"', "") for t in q.split() if len(t.strip()) >= 3]
    return " OR ".join(f'"{t}"' for t in terms) if terms else ""


def fts_search(fts_table, q, limit=50):
    expr = fts_escape(q)
    if expr:
        try:
            rows = query(f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ? LIMIT ?", (expr, limit))
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return [r["rowid"] for r in rows]
    src_table = {"utterances_fts": "utterances", "chunks_fts": "context_chunks"}[fts_table]
    col = "text" if src_table == "context_chunks" else "text || ' ' || translation || ' ' || corrected_text"
    like = f"%{q.strip()}%"
    return [r["id"] for r in query(f"SELECT id FROM {src_table} WHERE {col} LIKE ? LIMIT ?", (like, limit))]


def to_json(v):
    return json.dumps(v, ensure_ascii=False)


BACKUP_KEEP = 10


def backup(reason="periodic"):
    import datetime as _dt
    import time as _t
    bdir = DATA_DIR / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"db-{_dt.datetime.now():%Y%m%d-%H%M}.sqlite"
    if dest.exists():
        return dest
    src = sqlite3.connect(DATA_DIR / "database.sqlite")
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    olds = sorted(bdir.glob("db-*.sqlite"))
    for p in olds[:-BACKUP_KEEP]:
        p.unlink(missing_ok=True)
    return dest
