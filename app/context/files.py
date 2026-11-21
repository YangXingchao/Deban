import hashlib
import logging
import pathlib
import re

from .. import config, db

log = logging.getLogger("context")

CHUNK_CHARS = 800
CHUNK_OVERLAP = 120
SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx"}


def parse_file(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for tb in d.tables:
            for row in tb.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if ext == ".pptx":
        from pptx import Presentation
        parts = []
        for i, slide in enumerate(Presentation(str(path)).slides, 1):
            parts.append(f"[Slide {i}]")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    parts.append(shape.text_frame.text)
        return "\n".join(parts)
    return path.read_text(errors="replace")


def chunk_text(text: str):
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i + CHUNK_CHARS])
        i += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks if any(c.strip() for c in chunks) else []


def add_file(src_path: str, name=None) -> dict:
    src = pathlib.Path(src_path)
    if src.suffix.lower() not in SUPPORTED:
        raise ValueError(f"unsupported type: {src.suffix}")
    text = parse_file(src)
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    dup = db.query_one("SELECT id FROM contexts WHERE content_hash=?", (h,))
    if dup:
        return db.query_one("SELECT * FROM contexts WHERE id=?", (dup["id"],))
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("no extractable text (scanned or empty file?)")
    dest = config.DATA_DIR / "contexts" / f"{h}{src.suffix.lower()}"
    dest.write_bytes(src.read_bytes())
    cid = db.insert("contexts", {
        "name": (name or src.name)[:120], "type": src.suffix.lower().lstrip("."),
        "path": str(dest), "content_hash": h, "created_at": db.now()})
    for i, c in enumerate(chunks):
        db.insert("context_chunks", {"context_id": cid, "seq": i, "text": c})
    log.info("context added: %s (%d chunks)", name or src.name, len(chunks))
    return db.query_one("SELECT * FROM contexts WHERE id=?", (cid,))


def add_text(name: str, text: str) -> dict:
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    dup = db.query_one("SELECT id FROM contexts WHERE content_hash=? AND type='text'", (h,))
    if dup:
        return db.query_one("SELECT * FROM contexts WHERE id=?", (dup["id"],))
    cid = db.insert("contexts", {
        "name": name, "type": "text", "path": "", "content_hash": h, "created_at": db.now()})
    for i, c in enumerate(chunk_text(text)):
        db.insert("context_chunks", {"context_id": cid, "seq": i, "text": c})
    return db.query_one("SELECT * FROM contexts WHERE id=?", (cid,))


def get_text(cid: int) -> str:
    rows = db.query("SELECT text FROM context_chunks WHERE context_id=? ORDER BY seq", (cid,))
    if not rows:
        return ""
    out = rows[0]["text"]
    for r in rows[1:]:
        out += r["text"][CHUNK_OVERLAP:]
    return out


def update_text(cid: int, name: str, text: str):
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    db.execute("UPDATE contexts SET name=?, content_hash=? WHERE id=?", (name[:60], h, cid))
    db.execute("DELETE FROM context_chunks WHERE context_id=?", (cid,))
    for i, c in enumerate(chunk_text(text)):
        db.insert("context_chunks", {"context_id": cid, "seq": i, "text": c})


def list_all(q=None):
    rows = db.query("""
        SELECT c.*, COUNT(k.id) AS chunks FROM contexts c
        LEFT JOIN context_chunks k ON k.context_id = c.id
        GROUP BY c.id ORDER BY c.created_at DESC""")
    if not q or not q.strip():
        return rows
    q = q.strip()
    hit_ids = set()
    for cid in db.query("SELECT id FROM contexts WHERE name LIKE ?", (f"%{q}%",)):
        hit_ids.add(cid["id"])
    chunk_ids = db.fts_search("chunks_fts", q, limit=100)
    if chunk_ids:
        ph = ",".join("?" * len(chunk_ids))
        for r in db.query(f"SELECT DISTINCT context_id FROM context_chunks WHERE id IN ({ph})",
                          tuple(chunk_ids)):
            hit_ids.add(r["context_id"])
    return [r for r in rows if r["id"] in hit_ids]


def delete(cid: int):
    row = db.query_one("SELECT path FROM contexts WHERE id=?", (cid,))
    db.execute("DELETE FROM contexts WHERE id=?", (cid,))
    if row and row["path"]:
        pathlib.Path(row["path"]).unlink(missing_ok=True)


def retrieve(query: str, meeting_id=None, limit=4):
    ids = db.fts_search("chunks_fts", query, limit=30)
    rows = []
    if ids:
        ph = ",".join("?" * len(ids))
        rows = db.query(f"""
            SELECT k.id, k.text, k.context_id, c.name FROM context_chunks k
            JOIN contexts c ON c.id = k.context_id
            WHERE k.id IN ({ph})""", tuple(ids))
    prio = {}
    if meeting_id:
        prio = {r["context_id"]: (r["priority"] if r["priority"] is not None else 1)
                for r in db.query(
                    "SELECT context_id, priority FROM meeting_contexts WHERE meeting_id=?",
                    (meeting_id,))}
    fts_order = {cid: i for i, cid in enumerate(ids or [])}
    rows.sort(key=lambda r: (-prio.get(r["context_id"], -1), fts_order.get(r["id"], 999)))
    rows = [dict(r) for r in rows[:limit]]
    for r in rows:
        r["priority"] = prio.get(r["context_id"], -1)
    hit = {r["context_id"] for r in rows}
    for cid, p in prio.items():
        if p == 2 and cid not in hit:
            k = db.query_one("""SELECT k.id, k.text, k.context_id, c.name FROM context_chunks k
                                JOIN contexts c ON c.id = k.context_id
                                WHERE k.context_id=? ORDER BY k.seq LIMIT 1""", (cid,))
            if k:
                rows.append({**dict(k), "priority": 2})
    return rows[:max(limit, limit + 2)]
