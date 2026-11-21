import json
import logging
import threading

import numpy as np

from . import config, db, models

log = logging.getLogger("speakers")

SELF = -1
SPK_MIN_DUR = 2.0
NEW_SPK_MIN_DUR = 3.0
LANG_STICKY_DUR = 4.0
MAX_SPEAKERS = 24


class SpeakerStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.ids = []
        self.centroids = []
        self.counts = []
        self.names = {}
        self.langs = {}
        self.model = None
        self._torch = None
        self.self_emb = None
        self.self_n = 0
        self.pending_new = []
        self._confirm_emb = None
        self._load()
        self._load_self()

    def _load(self):
        for r in db.query("SELECT * FROM speakers ORDER BY id"):
            if r["embedding"]:
                emb = np.frombuffer(r["embedding"], dtype=np.float64)
                self.ids.append(r["id"])
                self.centroids.append(emb)
                self.counts.append(r["count"] or 1)
            if r["name"]:
                self.names[r["id"]] = r["name"]
            try:
                self.langs[r["id"]] = json.loads(r["lang_votes"] or "{}")
            except Exception:
                self.langs[r["id"]] = {}

    def reload(self):
        with self.lock:
            self.ids, self.centroids, self.counts = [], [], []
            self.names, self.langs = {}, {}
            self._load()

    def load_model(self):
        import torch
        torch.set_num_threads(2)
        from speechbrain.inference.speaker import EncoderClassifier
        model_dir = models.ensure(models.ECAPA)
        self.model = EncoderClassifier.from_hparams(
            source=str(model_dir), savedir=str(model_dir),
            overrides={"pretrained_path": str(model_dir)},
        )
        self._torch = torch
        self.embed(np.zeros(16000, dtype=np.float32))

    def embed(self, audio):
        t = self._torch.from_numpy(audio).unsqueeze(0)
        with self._torch.no_grad():
            e = self.model.encode_batch(t).squeeze().cpu().numpy().astype(np.float64)
        return e / (np.linalg.norm(e) + 1e-9)

    @staticmethod
    def new_spk_gate(n_anon, thr):
        need_dur = min(SPK_MIN_DUR + 0.5 * n_anon, 6.0)
        if n_anon >= 3:
            max_sim = max(0.25, thr - 0.03 * (n_anon - 2))
        else:
            max_sim = thr
        return need_dur, max_sim

    @staticmethod
    def anon_thr(n_anon, thr):
        return max(0.30, thr - 0.03 * max(0, n_anon - 1))

    CONFIRM_FROM = 3
    CONFIRM_TTL = 180.0

    def _can_create(self, n_anon, dur, best_sim, thr, emb=None):
        need_dur, max_sim = self.new_spk_gate(n_anon, thr)
        if not (dur >= need_dur and (best_sim is None or best_sim <= max_sim)):
            return False
        if emb is None or n_anon < self.CONFIRM_FROM:
            return True
        import time as _t
        now = _t.time()
        self.pending_new = [(e, exp) for e, exp in self.pending_new if exp > now][-5:]
        for i, (e, _) in enumerate(self.pending_new):
            if float(emb @ e) >= thr - 0.05:
                self._confirm_emb = e
                del self.pending_new[i]
                return True
        self.pending_new.append((emb, now + self.CONFIRM_TTL))
        return False

    def match(self, emb, dur, roster=None, cap=0, allowed=None):
        thr = float(config.get("spk_threshold"))
        with self.lock:
            sims = {sid: float(emb @ c) for sid, c in zip(self.ids, self.centroids)}
            if cap and roster is not None:
                pool = set(roster) | set(allowed or [])
                cand = [(s, sid) for sid, s in sims.items() if sid in pool]
                if cand:
                    best_s, best_id = max(cand)
                    if best_s >= thr:
                        return best_id, False, True
                    n_anon = sum(1 for sid in roster if sid not in self.names)
                    if best_id not in self.names and best_s >= self.anon_thr(n_anon, thr):
                        return best_id, False, True
                    if dur < SPK_MIN_DUR:
                        return best_id, False, False
                    if len(roster) >= cap:
                        rcand = [(s, sid) for sid, s in sims.items() if sid in roster]
                        if rcand:
                            bs, bid = max(rcand)
                            return bid, False, bs >= thr
                        return best_id, False, False
                    n_anon = sum(1 for sid in roster if sid not in self.names)
                    if not self._can_create(n_anon, dur, best_s, thr, emb):
                        return best_id, False, False
                    return 0, True, True
                if len(roster) < cap:
                    n_anon = sum(1 for sid in roster if sid not in self.names)
                    if self._can_create(n_anon, dur, None, thr, emb):
                        return 0, True, True
                    anon = {sid: v for sid, v in sims.items() if sid not in self.names}
                    if anon:
                        b = max(anon, key=anon.get)
                        return b, False, False
                    return 0, True, True
                rcand = [(s, sid) for sid, s in sims.items() if sid in roster]
                if rcand:
                    bs, bid = max(rcand)
                    return bid, False, bs >= thr
                return 0, True, True
            if roster:
                present = [(s, sid) for sid, s in sims.items()
                           if sid in roster and sid in self.names]
                if present:
                    bs, bid = max(present)
                    if bs >= thr:
                        return bid, False, True
            anon_sims = {sid: v for sid, v in sims.items() if sid not in self.names}
            if not anon_sims:
                return 0, True, True
            best_id = max(anon_sims, key=anon_sims.get)
            if anon_sims[best_id] >= self.anon_thr(len(anon_sims), thr):
                return best_id, False, True
            if len(anon_sims) >= MAX_SPEAKERS:
                return best_id, False, False
            if not self._can_create(len(anon_sims), dur, anon_sims[best_id], thr, emb):
                return best_id, False, False
            return 0, True, True

    def best_named(self, emb):
        with self.lock:
            best = None
            for sid, c in zip(self.ids, self.centroids):
                if sid in self.names:
                    v = float(emb @ c)
                    if best is None or v > best[1]:
                        best = (sid, v)
            return best

    def centroid_of(self, spk_id):
        with self.lock:
            if spk_id in self.ids:
                return self.centroids[self.ids.index(spk_id)].copy()
        return None

    def unlearn(self, emb, spk_id):
        with self.lock:
            if spk_id not in self.ids:
                return
            i = self.ids.index(spk_id)
            n = self.counts[i]
            if n <= 1:
                return
            w = min(n - 1, 50)
            c = self.centroids[i] * (w + 1) - emb
            nrm = np.linalg.norm(c)
            if nrm < 1e-6:
                return
            self.centroids[i] = c / nrm
            self.counts[i] = n - 1
            db.execute("UPDATE speakers SET embedding=?, count=? WHERE id=?",
                       (self.centroids[i].tobytes(), n - 1, spk_id))

    def learn(self, emb, dur, spk_id, is_new):
        with self.lock:
            if is_new:
                if self._confirm_emb is not None:
                    emb = emb + self._confirm_emb
                    emb = emb / (np.linalg.norm(emb) + 1e-9)
                    self._confirm_emb = None
                new_id = db.insert("speakers", {
                    "embedding": emb.tobytes(), "count": 1, "created_at": db.now()})
                self.ids.append(new_id)
                self.centroids.append(emb)
                self.counts.append(1)
                return new_id
            if dur >= SPK_MIN_DUR and spk_id in self.ids:
                i = self.ids.index(spk_id)
                n = self.counts[i]
                c = (self.centroids[i] * min(n, 50) + emb) / (min(n, 50) + 1)
                self.centroids[i] = c / (np.linalg.norm(c) + 1e-9)
                self.counts[i] = n + 1
                db.execute("UPDATE speakers SET embedding=?, count=? WHERE id=?",
                           (self.centroids[i].tobytes(), self.counts[i], spk_id))
            return spk_id

    def name(self, spk_id):
        if spk_id == SELF:
            return config.get("self_name") or "Me"
        with self.lock:
            if spk_id in self.names:
                return self.names[spk_id]
            try:
                return config.anon_label(self.ids.index(spk_id) + 1)
            except ValueError:
                return config.anon_label(spk_id)

    def is_named(self, spk_id):
        return spk_id == SELF or spk_id in self.names

    def rename(self, spk_id, name):
        with self.lock:
            self.names[spk_id] = name
        db.execute("""INSERT INTO speakers(id, name, created_at) VALUES(?,?,?)
                      ON CONFLICT(id) DO UPDATE SET name=excluded.name""",
                   (spk_id, name, db.now()))

    def set_meta(self, spk_id, role=None, company=None):
        if role is not None:
            db.execute("UPDATE speakers SET role=? WHERE id=?", (role, spk_id))
        if company is not None:
            db.execute("UPDATE speakers SET company=? WHERE id=?", (company, spk_id))

    def _self_path(self):
        return config.DATA_DIR / "speakers" / "self.npy"

    def _load_self(self):
        try:
            p = self._self_path()
            if p.exists():
                arr = np.load(p)
                self.self_emb, self.self_n = arr[:-1], int(arr[-1])
        except Exception:
            self.self_emb, self.self_n = None, 0

    def learn_self(self, emb, dur):
        if dur < SPK_MIN_DUR:
            return
        with self.lock:
            if self.self_emb is None:
                c = emb
            else:
                n = min(self.self_n, 50)
                c = (self.self_emb * n + emb) / (n + 1)
            self.self_emb = c / (np.linalg.norm(c) + 1e-9)
            self.self_n += 1
            try:
                np.save(self._self_path(), np.append(self.self_emb, float(self.self_n)))
            except Exception:
                pass

    def similarity_to_self(self, emb):
        with self.lock:
            if self.self_emb is None:
                return 0.0
            return float(emb @ self.self_emb)

    def self_info(self):
        return {"has_voice": self.self_emb is not None, "n_segments": self.self_n}

    def similarity_to_known(self, emb):
        with self.lock:
            if not self.centroids:
                return 0.0
            return max(float(emb @ c) for c in self.centroids)

    def lang_hint(self, spk_id):
        with self.lock:
            c = self.langs.get(spk_id)
            if not c:
                return None
            lang, n = max(c.items(), key=lambda kv: kv[1])
            return lang if n >= 2 else None

    def note_lang(self, spk_id, lang):
        with self.lock:
            d = self.langs.setdefault(spk_id, {})
            d[lang] = d.get(lang, 0) + 1
            votes = json.dumps(d)
        top = max(d.items(), key=lambda kv: kv[1])[0]
        if spk_id >= 0:
            db.execute("UPDATE speakers SET lang_votes=?, language=? WHERE id=?", (votes, top, spk_id))

    def create_placeholder(self, name: str, note: str = "", group: str = "") -> int:
        with self.lock:
            new_id = db.insert("speakers", {"name": name[:30], "note": (note or "")[:60],
                                            "grp": (group or "")[:30], "created_at": db.now()})
            self.names[new_id] = name[:30]
        return new_id

    def snapshot(self, spk_id):
        with self.lock:
            if spk_id in self.ids:
                i = self.ids.index(spk_id)
                return (self.centroids[i].copy(), self.counts[i])
            return None

    def restore(self, spk_id, snap):
        with self.lock:
            if snap is None:
                if spk_id in self.ids:
                    i = self.ids.index(spk_id)
                    self.ids.pop(i); self.centroids.pop(i); self.counts.pop(i)
                db.execute("UPDATE speakers SET embedding=NULL, count=0 WHERE id=?", (spk_id,))
                return
            c, n = snap
            if spk_id in self.ids:
                i = self.ids.index(spk_id)
                self.centroids[i], self.counts[i] = c, n
            else:
                self.ids.append(spk_id); self.centroids.append(c); self.counts.append(n)
            db.execute("UPDATE speakers SET embedding=?, count=? WHERE id=?", (c.tobytes(), n, spk_id))

    def merge(self, src_id: int, dst_id: int):
        with self.lock:
            if src_id in self.ids:
                i = self.ids.index(src_id)
                if dst_id in self.ids:
                    j = self.ids.index(dst_id)
                    sim = float(self.centroids[i] @ self.centroids[j])
                    if sim >= float(config.get("spk_threshold")):
                        n_new = self.counts[i]
                        n_old = min(self.counts[j], n_new)
                        c = self.centroids[i] * n_new + self.centroids[j] * n_old
                        self.centroids[j] = c / (np.linalg.norm(c) + 1e-9)
                        self.counts[j] = min(n_new + n_old, 50)
                    else:
                        self.centroids[j], self.counts[j] = self.centroids[i], self.counts[i]
                    db.execute("UPDATE speakers SET embedding=?, count=? WHERE id=?",
                               (self.centroids[j].tobytes(), self.counts[j], dst_id))
                    self.ids.pop(i); self.centroids.pop(i); self.counts.pop(i)
                else:
                    self.ids[i] = dst_id
                    db.execute("UPDATE speakers SET embedding=?, count=? WHERE id=?",
                               (self.centroids[i].tobytes(), self.counts[i], dst_id))
            votes = self.langs.pop(src_id, None)
            if votes:
                d = self.langs.setdefault(dst_id, {})
                for k, v in votes.items():
                    d[k] = d.get(k, 0) + v
                db.execute("UPDATE speakers SET lang_votes=? WHERE id=?", (json.dumps(d), dst_id))
            self.names.pop(src_id, None)
        db.execute("UPDATE utterances SET speaker_id=? WHERE speaker_id=?", (dst_id, src_id))
        db.execute("DELETE FROM speakers WHERE id=?", (src_id,))

    def transfer_voice(self, src_id: int, dst_id: int) -> bool:
        with self.lock:
            if src_id not in self.ids or dst_id in self.ids or src_id == dst_id:
                return False
            i = self.ids.index(src_id)
            src_centroid, src_count = self.centroids[i], self.counts[i]
            self.ids.append(dst_id)
            self.centroids.append(src_centroid)
            self.counts.append(src_count)
            target_i = self.ids.index(dst_id)
            db.execute("UPDATE speakers SET embedding=?, count=? WHERE id=?",
                       (self.centroids[target_i].tobytes(), self.counts[target_i], dst_id))
            self.ids.pop(i)
            self.centroids.pop(i)
            self.counts.pop(i)
            db.execute("UPDATE speakers SET embedding=NULL, count=0 WHERE id=?", (src_id,))
            return True

    def list_named(self):
        rows = db.query("""
            SELECT s.id, s.name, s.note, s.grp, s.embedding IS NOT NULL AND s.embedding != '' AS has_voice,
                   COUNT(u.id) AS n_utts, MAX(u.ts) AS last_ts
            FROM speakers s LEFT JOIN utterances u ON u.speaker_id = s.id
            WHERE s.name != '' GROUP BY s.id
            ORDER BY (MAX(u.ts) IS NULL), MAX(u.ts) DESC, s.name""")
        return [{"id": r["id"], "name": r["name"], "note": r["note"] or "", "group": r["grp"] or "",
                 "has_voice": bool(r["has_voice"]), "n_utts": r["n_utts"], "last_ts": r["last_ts"]} for r in rows]

    def set_note(self, spk_id, note):
        db.execute("UPDATE speakers SET note=? WHERE id=?", ((note or "").strip()[:60], spk_id))

    def set_group(self, spk_id, group):
        db.execute("UPDATE speakers SET grp=? WHERE id=?", ((group or "").strip()[:30], spk_id))

    @staticmethod
    def tip(group, note):
        return " · ".join(x for x in ((group or "").strip(), (note or "").strip()) if x)

    def notes_for(self, ids):
        ids = [i for i in set(ids or []) if i is not None and i >= 0]
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        out = {}
        for r in db.query(f"SELECT id, note, grp FROM speakers WHERE id IN ({ph})", tuple(ids)):
            t = self.tip(r["grp"], r["note"])
            if t:
                out[r["id"]] = t
        return out

    def purge_unnamed(self):
        rows = db.query("SELECT id FROM speakers WHERE name='' OR name IS NULL")
        for r in rows:
            self.remove(r["id"])
        if rows:
            import logging
            logging.getLogger("speakers").info("purged %d unnamed voiceprints", len(rows))
        return len(rows)

    def remove(self, spk_id: int):
        with self.lock:
            if spk_id in self.ids:
                i = self.ids.index(spk_id)
                self.ids.pop(i); self.centroids.pop(i); self.counts.pop(i)
            self.names.pop(spk_id, None)
            self.langs.pop(spk_id, None)
        db.execute("DELETE FROM speakers WHERE id=?", (spk_id,))

    def known_names(self):
        with self.lock:
            return list(self.names.values())
