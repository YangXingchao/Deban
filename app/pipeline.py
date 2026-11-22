import datetime
import json
import re
import logging
import queue
import sys
import threading
import time

import numpy as np

from . import config, db, meetings, localization
from .asr import ASR, GREETING_RE as _GREETING_RE, STOCK_RE as _STOCK_RE
from .audio.base import SAMPLE_RATE
from .context import glossary
from .engine.rules import RuleEngine
from .engine.reasoning import ReasoningEngine, out_name, tr_name
from .events import bus
from .llm import LLM
from .recorder import Recorder
from .speakers import SpeakerStore, SELF, SPK_MIN_DUR

MIC_MIN_DUR = 1.0
MIC_NO_SPEECH_MAX = 0.5
MIC_LOGPROB_MIN = -1.0
SELF_GATE_MIN_N = 5
SELF_GATE_SIM = 0.30
ECHO_OVERLAP = 0.65
ECHO_TEXT_SIM = 0.6
CONTRIB_THROTTLE_SEC = 10
CONTRIB_QUIET_SEC = 45
LANG_NAMES = {"ja": "Japanese", "zh": "Chinese", "en": "English"}
from .vad import ChannelVAD

log = logging.getLogger("pipeline")

_F = (r"うん+|うーん|はい|ええ+|えっ?と|あ+ー*|えー+|ん+ー*|ふー?ん|なるほど|そう+|まあ|まぁ|"
      r"お+ー*|え+|ね|はー+|へー+|ほー+|いや|なんか|あの+|その|えっ|おっ|おう|ええと|はいはい|な+あ+な*|あら+|"
      r"嗯+|哦+|噢+|啊+|呃+|唉|欸|额|唔|喂|好的?|对|嗨|OK|ok|Okay|okay|[Yy]eah|[Yy]es|[Nn]o|[Uu]m+|[Uu]h+|[Uu]h[- ]?huh|[MmHh]m+")
FILLER_RE = re.compile(
    r"^[\s、。，,.!?！？~ー〜…・]*"
    rf"(?:(?:{_F})[\s、。，,.!?！？ー〜…・]*)+$")
FILLER_MAX_DUR = 4.0
GREETING_GRACE_SEC = 180
NOISE_MAX_CHARS = 20
HIRA_ONLY_MAX = 3

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def usable_kw(k: str) -> bool:
    k = (k or "").strip()
    return len(k) >= (2 if _CJK_RE.search(k) else 3)


def fix_ok(orig: str, fixed: str) -> bool:
    import difflib
    if not fixed or fixed == orig:
        return False
    return difflib.SequenceMatcher(None, orig, fixed).ratio() >= 0.6

TRANSLATE_SYS_TMPL = (
    "You are a professional meeting interpreter. Given a speech recognition transcript, "
    "use the context and glossary to correct obvious recognition errors in the original language. "
    "Keep uncertain text unchanged. Translate naturally into {out}, preserving proper nouns. "
    'Return only JSON: {{"src": "corrected original", "out": "translation"}}')

SUMMARY_SYS_TMPL = (
    "Summarize this meeting transcript. Each utterance has a speaker and a #number. "
    "Infer meaning from context. The meeting language is {src}; write title and points in {out}. "
    'Return JSON: {{"merge_with_prev": true or false, "title": "short topic title", '
    '"points": [{{"text": "key point", "uid": supporting utterance number}}], '
    '"fixes": [{{"id": utterance number, "text": "corrected original"}}], '
    '"noise": [utterance numbers]}}. '
    "Merge with the previous topic only when this is clearly a continuation, covering both topics. "
    "Otherwise summarize only this segment. Give one to five points and identify speakers when useful. "
    "Use transcript or previous topic numbers as uid, or null if uncertain. "
    "Include fixes only for unambiguous recognition errors. Mark only short, obvious hallucinations as noise; "
    "keep normal responses. Use empty arrays when there are no fixes or noise.")

FINAL_SYS_TMPL = (
    "Summarize the completed meeting using its topics and numbered transcript, in {out}. "
    'Return JSON: {{"summary": [{{"text": "key point", "uid": supporting utterance number}}], '
    '"decisions": [{{"text": "explicit decision", "uid": utterance number}}], '
    '"todos": [{{"text": "action and deadline", "owner": "me, speaker name, or empty", "uid": utterance number}}]}}. '
    "Provide three to eight summary points covering conclusions and disagreements. "
    "Include only explicit decisions and action items, using empty arrays when absent. "
    "Use speaker names from the transcript for owners and me for the user. "
    "Leave owner empty when unclear. Use only utterance numbers present in the transcript.")

REFINE_TOPICS_SYS_TMPL = (
    "Organize these chronological meeting topics. Merge only adjacent segments clearly about the same issue. "
    "Remove segments without meaningful information. Rewrite concise titles and points in {out}. "
    'Return JSON: {{"topics": [{{"ids": [original topic ids], "title": "...", '
    '"points": [{{"text": "...", "uid": supporting utterance number or null}}]}}]}}. '
    "Preserve chronological order and keep most segments separate. "
    "Do not include removed IDs. Preserve cited utterance numbers where possible.")


def _norm_points(raw, valid_uids):
    out = []
    for p in raw or []:
        if isinstance(p, dict):
            text = str(p.get("text", "")).strip()
            src = str(p.get("src") or "").strip()
            try:
                uid = int(str(p.get("uid") or "0").lstrip("#") or 0)
            except Exception:
                uid = None
            item = {"text": text, "uid": uid if uid in valid_uids else None}
            if src and src != text:
                item["src"] = src
            out.append(item)
        else:
            text = str(p).strip()
            out.append({"text": text, "uid": None})
    return [p for p in out if p["text"]]


TITLE_MAX = 40


def _clean_title(title, fallback=""):
    t = re.sub(r"[（(]\s*#\d+(?:\s*[,、，]\s*#?\d+)*\s*[)）]", "", str(title or "")).strip(" 　:：;；。.,、")
    if len(t) > TITLE_MAX:
        head = re.split(r"[:：]", t, 1)[0].strip()
        if 2 <= len(head) <= TITLE_MAX:
            return head
        if fallback and len(fallback) <= TITLE_MAX:
            return fallback
        cut = re.split(r"[；;。！？!?]", t, 1)[0].strip()
        t = cut if 2 <= len(cut) <= TITLE_MAX else t[:TITLE_MAX - 1] + "…"
    return t


def _pts_line(pts, with_uid=False):
    return "；".join(
        f"{meetings.pt_text(p)} (#{p['uid']})"
        if with_uid and isinstance(p, dict) and p.get("uid") else meetings.pt_text(p)
        for p in pts)


class Pipeline:
    def __init__(self):
        self.state = "idle"
        self.meeting_id = None
        self.meeting_started = None
        self.roster = set()
        self.cur_max_spk = 0
        self.recent = []
        self.topic_buffer = []
        self.last_topic = None
        self.cur_record = False
        self.cur_translate = True
        self.cur_mic = bool(config.get("mic_enabled"))
        self.vads = {}
        self.series_digest = ""
        self.live_embs = {}
        self.bind_backup = {}
        self.anon_seq = {}
        self.suggest_votes = {}
        self.suggest_done = set()
        self.archiving_ids = set()
        self.meeting_terms = []
        self.meeting_rules = []
        self.last_contrib_eval = 0.0
        self.last_contrib_shown = 0.0
        self.last_mem_eval = 0.0
        self.win = {}
        self.last_engine_at = 0.0
        self.scan_pending = 0
        self.cur_unknown = 0
        self.live_since = None
        self.cur_brief = ""
        self.last_utt_time = None
        self.ended_ctx = {}
        self._pause_started = None
        self.cur_auto_end = True
        self.roster_locked = False
        self.last_activity = None
        self.lock = threading.Lock()

        self.llm = LLM()
        self.speakers = SpeakerStore()
        self.asr = ASR(glossary.load())
        self.rules = RuleEngine()
        self.reasoning = ReasoningEngine(self.llm, self.speakers)
        self.reasoning.disp = self.disp
        self.recorder = Recorder()

        self.transcribe_q = queue.Queue()
        self.translate_q = queue.Queue()
        self.engine_q = queue.Queue()
        self.capture_seen = None
        self.native_capture_stop = None
        self._model_lock = threading.Lock()
        self.asr_error = ""
        self.ready = False
        self.asr_loading = False
        self.speaker_recognition_ready = False
        self.speaker_recognition_error = ""
        self.boot_error = None

    def boot(self):
        threading.Thread(target=self.llm.verify_saved, daemon=True).start()
        threading.Thread(target=self._boot_guarded, daemon=True).start()

    def _boot_guarded(self):
        try:
            self._boot()
        except Exception as e:
            log.exception("boot failed")
            self.boot_error = {"code": db.classify_error(e), "detail": str(e)[:300]}
            self.asr_loading = False
            try:
                self._pub_state()
                bus.publish({"type": "fatal", **self.boot_error}, store=False)
            except Exception:
                pass

    def _boot(self):
        orphans = []
        for m in db.query("SELECT id, started_at FROM meetings WHERE status='live'"):
            last = db.query_one(
                "SELECT MAX(ts) t FROM utterances WHERE meeting_id=?", (m["id"],))
            ended = (last and last["t"]) or m["started_at"]
            n = db.query_one("SELECT COUNT(*) c FROM utterances WHERE meeting_id=?", (m["id"],))["c"]
            if n == 0:
                db.execute("DELETE FROM meetings WHERE id=?", (m["id"],))
                continue
            title = self._fallback_title(datetime.datetime.fromtimestamp(m["started_at"]), orphan=True)
            db.execute("""UPDATE meetings SET status='done', ended_at=?, duration=?, title=?
                          WHERE id=? AND (title='' OR title IS NULL)""",
                       (ended, ended - m["started_at"], title, m["id"]))
            db.execute("UPDATE meetings SET status='done', ended_at=? WHERE id=?", (ended, m["id"]))
            orphans.append(m["id"])
            log.info("recovered orphan meeting %s (%d utterances)", m["id"], n)
        self.speakers.purge_unnamed()
        try:
            from . import memory as _mem0
            _mem0.backfill_people()
        except Exception:
            log.exception("memory backfill failed")
        from . import trash as _trash
        def _trash_loop():
            while True:
                try:
                    p = db.backup()
                    log.info("db backup: %s", p.name)
                except Exception:
                    log.exception("db backup failed")
                try:
                    _trash.purge_expired()
                except Exception:
                    log.exception("trash purge failed")
                time.sleep(6 * 3600)
        threading.Thread(target=_trash_loop, daemon=True).start()
        from .context import profile as _profile
        threading.Thread(target=lambda: _profile.expand_keywords(self.llm), daemon=True).start()
        threading.Thread(target=self._engine_worker, daemon=True, name="engine-2").start()
        threading.Thread(target=self._translate_worker, daemon=True, name="translate-2").start()
        for target in (self._asr_worker, self._translate_worker, self._engine_worker,
                       self._summary_worker, self._auto_end_loop):
            threading.Thread(target=target, daemon=True).start()
        self.ready = True
        bus.publish({"type": "status", "text": "ready"}, store=False)
        self._pub_state()
        log.info("pipeline ready, llm=%s", self.llm.label)
        self.prepare_models()
        if orphans:
            threading.Thread(target=self._recover_orphans, args=(orphans,), daemon=True).start()

    def prepare_models(self):
        if not self._model_lock.acquire(blocking=False):
            return

        def run():
            try:
                self.asr_error = ""
                if self.asr.engine is None:
                    self.asr_loading = True
                    self._pub_state()
                    try:
                        self.asr.warmup()
                    except Exception:
                        self.asr.engine = None
                        self.asr_error = "asr_unavailable"
                        log.exception("ASR preparation failed")
                    finally:
                        self.asr_loading = False
                        self._pub_state()
                if not self.speaker_recognition_ready:
                    try:
                        self.speakers.load_model()
                        self.speaker_recognition_ready = True
                        self.speaker_recognition_error = ""
                    except Exception as error:
                        self.speaker_recognition_error = str(error)[:300]
                        log.exception("Speaker recognition unavailable")
            finally:
                self._model_lock.release()

        threading.Thread(target=run, daemon=True, name="models-prepare").start()

    def _recover_orphans(self, mids):
        if not self.llm.available:
            log.warning("orphan meetings %s: LLM unavailable, skipping summaries", mids)
            return
        for mid in mids:
            self.archiving_ids.add(mid)
            self.llm.set_meeting_ctx(mid)
            try:
                last = db.query_one("SELECT id, end_ts FROM topics WHERE meeting_id=? ORDER BY id DESC LIMIT 1", (mid,))
                since = (last or {}).get("end_ts") or 0
                tail = db.query("""SELECT id, ts, speaker_id, text, corrected_text FROM utterances
                                   WHERE meeting_id=? AND ts>? ORDER BY id""", (mid, since))
                buf = [{"uid": u["id"], "ts": u["ts"], "spk": u["speaker_id"], "text": u["corrected_text"] or u["text"]}
                       for u in tail]
                if len(buf) >= 2:
                    try:
                        self._summarize(mid, buf, brief="", last_topic=(last or {}).get("id"))
                    except Exception:
                        log.exception("orphan %s: tail topic failed", mid)
                try:
                    summary, todos, decisions = self._final_summary(mid, brief="")
                except Exception:
                    log.exception("orphan %s: final summary failed", mid)
                    summary, todos, decisions = [], [], []
                prev = db.query_one("SELECT summary, todos, decisions FROM meetings WHERE id=?", (mid,)) or {}
                summary = summary or json.loads(prev.get("summary") or "[]")
                todos = todos or json.loads(prev.get("todos") or "[]")
                decisions = decisions or json.loads(prev.get("decisions") or "[]")
                try:
                    from . import memory as _mem
                    from .context import profile as _prof
                    _mem.extract_from_meeting(mid, self.llm, self.src_lang_name(mid), todos=todos,
                                              self_names=[config.get("self_name") or "", _prof.load().get("name") or "",
                                                          *(config.get("my_names") or [])])
                except Exception:
                    log.exception("orphan %s: memory extraction failed", mid)
                try:
                    self._refine_topics(mid)
                except Exception:
                    log.exception("orphan %s: topic refine failed", mid)
                db.execute("UPDATE meetings SET language=?, summary=?, todos=?, decisions=? WHERE id=?",
                           (config.get("src_lang"), db.to_json(summary), db.to_json(todos), db.to_json(decisions), mid))
                title = (db.query_one("SELECT title FROM meetings WHERE id=?", (mid,)) or {}).get("title") or ""
                bus.publish({"type": "meeting_done", "meeting_id": mid, "title": title}, store=False)
                log.info("orphan meeting %s: summaries recovered (%d points, %d todos)", mid, len(summary), len(todos))
            except Exception:
                log.exception("orphan %s: recovery failed", mid)
            finally:
                self.archiving_ids.discard(mid)
                self.llm.set_meeting_ctx(None)

    def disp(self, spk, mid=None):
        if spk == SELF or self.speakers.is_named(spk):
            return self.speakers.name(spk)
        if mid is None or mid == self.meeting_id:
            n = self.anon_seq.get(spk)
            if n is None:
                n = len(self.anon_seq) + 1
                self.anon_seq[spk] = n
                if self.meeting_id:
                    try:
                        db.execute("INSERT OR IGNORE INTO meeting_anon_labels(meeting_id, speaker_id, n) "
                                   "VALUES(?,?,?)", (self.meeting_id, spk, n))
                    except Exception:
                        pass
            return config.anon_label(n)
        row = db.query_one("SELECT n FROM meeting_anon_labels WHERE meeting_id=? AND speaker_id=?",
                           (mid, spk))
        if row:
            return config.anon_label(row['n'])
        nxt = db.query_one("SELECT COALESCE(MAX(n), 0) + 1 AS n FROM meeting_anon_labels "
                           "WHERE meeting_id=?", (mid,))["n"]
        try:
            db.execute("INSERT OR IGNORE INTO meeting_anon_labels(meeting_id, speaker_id, n) "
                       "VALUES(?,?,?)", (mid, spk, nxt))
        except Exception:
            pass
        return config.anon_label(nxt)

    def snapshot(self):
        return {"auto_end": self.cur_auto_end, "roster_locked": self.roster_locked,
                "state": self.state, "meeting_id": self.meeting_id,
                "ready": self.can_record, "asr_loading": self.asr_loading, "asr_error": self.asr_error,
                "boot_error": self.boot_error, "llm_on": self.llm.available,
                "started_at": self.meeting_started,
                "title": self.live_title(),
                "translate": self.cur_translate, "mic": self.cur_mic,
                "archiving": sorted(self.archiving_ids),
                "known_speakers": self.speakers.known_names()}

    def start_meeting(self, max_spk=None, context_ids=None, role="", brief="",
                      participants=None, record=None, unknown=0, translate=None,
                      context_prios=None, prev_meeting_id=None, mic=None):
        with self.lock:
            if self.state != "idle":
                return None
            self.cur_translate = bool(config.get("translate_default")) if translate is None else bool(translate)
            self.cur_mic = bool(config.get("mic_enabled")) if mic is None else bool(mic)
            participants = [int(p) for p in (participants or [])]
            unknown = max(0, int(unknown or 0))
            self.cur_unknown = unknown
            if participants or unknown:
                self.cur_max_spk = len(participants) + unknown
            else:
                self.cur_max_spk = int(max_spk or 0)
            digest = meetings.meeting_digest(prev_meeting_id) if prev_meeting_id else ""
            self.series_digest = digest
            self.meeting_id = db.insert("meetings", {
                "title": "", "started_at": db.now(), "created_at": db.now(),
                "status": "live", "max_spk": self.cur_max_spk, "role": role or "",
                "translate_on": int(self.cur_translate),
                "prev_meeting_id": prev_meeting_id, "series_digest": digest,
                "notes": (brief or "").strip()[:2000]})
            prios = {int(k): v for k, v in (context_prios or {}).items()}
            for cid in list(context_ids or [])[:5]:
                c = db.query_one("SELECT name FROM contexts WHERE id=?", (cid,))
                db.insert("meeting_contexts", {"meeting_id": self.meeting_id, "context_id": cid,
                                               "name": (c or {}).get("name", ""),
                                               "priority": max(0, min(2, prios.get(cid, 1)))})
            if (brief or "").strip():
                from .context import files as ctx_files
                stamp = datetime.datetime.now().strftime("%m-%d %H:%M")
                row = ctx_files.add_text(f"会议简介 {stamp}", brief.strip())
                db.insert("meeting_contexts", {"meeting_id": self.meeting_id, "context_id": row["id"],
                                               "name": row["name"]})
            for pid in participants:
                db.insert("meeting_participants", {"meeting_id": self.meeting_id, "speaker_id": pid})
            self.meeting_started = db.now()
            self.live_since = db.now()
            voiced = {s["id"] for s in self.speakers.list_named() if s["has_voice"]}
            self.roster = {p for p in participants if p in voiced}
            self.recent = []
            self.win = {}
            self.last_engine_at = time.time()
            self.scan_pending = 0
            self.live_embs = {}
            self.bind_backup = {}
            self.anon_seq = {}
            self.suggest_votes = {}
            self.suggest_done = set()
            self.speakers.pending_new = []
            self.topic_buffer = []
            self.last_topic = None
            self.cur_record = bool(config.get("record_audio")) if record is None else bool(record)
            self.cur_auto_end = float(config.get("auto_end_min") or 0) > 0
            self.roster_locked = bool(participants or unknown)
            self.last_activity = db.now()
            self.cur_brief = (brief or "").strip()
            self.meeting_terms = []
            self.meeting_rules = []
            self.state = "recording"
            self.capture_seen = time.monotonic()
            self.vads = {channel: ChannelVAD(channel, self._on_segment, lambda: self.state == "recording")
                         for channel in ("sys", "mic")}
            bus.clear_history()
            self.asr.set_glossary(glossary.load())
            threading.Thread(target=self._extract_meeting_terms,
                             args=(self.meeting_id,), daemon=True).start()
            self.reasoning.lane_bumps, self.reasoning.lane_bias = self._adaptive_bumps()
            self.reasoning.reset_meeting()
            if self.cur_record:
                self.recorder.start()
                self._native_record_anchor = time.monotonic()
                self._native_record_offset = 0
                self._native_record_samples = {"sys": 0, "mic": 0}
        log.info("meeting %s started: participants=%s unknown=%s cap=%s locked=%s role=%s",
                 self.meeting_id, participants, unknown, self.cur_max_spk, self.roster_locked, role)
        self._pub_state()
        bus.publish({"type": "reset"}, store=False)
        return self.meeting_id

    def pause(self):
        with self.lock:
            if self.state != "recording":
                return
            for vad in self.vads.values():
                vad.flush()
            self.state = "paused"
            self._pause_started = time.time()
        self._pub_state()

    def resume(self):
        with self.lock:
            if self.state != "paused":
                return
            gap = time.time() - (self._pause_started or time.time())
            if self.meeting_started:
                self.meeting_started += gap
            if self.live_since:
                self.live_since += gap
            self._pause_started = None
            self.last_activity = time.time()
            self.state = "recording"
            if self.cur_record:
                self._native_record_offset = max(self._native_record_samples.values())
                self._native_record_anchor = time.monotonic()
        self._pub_state()

    def end_meeting(self, expected_mid=None):
        with self.lock:
            if self.state == "idle" or not self.meeting_id:
                return None
            mid = self.meeting_id
            if expected_mid is not None and mid != expected_mid:
                return None
            for vad in self.vads.values():
                vad.flush()
            transcription_done = threading.Event()
            self.transcribe_q.put((None, None, None, transcription_done))
            snap = self.recorder.stop_snapshot()
            self.state = "idle"
            self.meeting_id = None
            buf, self.topic_buffer = self.topic_buffer, []
            ctx = {"live_since": self.live_since, "started": self.meeting_started,
                   "brief": self.cur_brief, "last_topic": self.last_topic,
                   "roster": set(self.roster), "cap": self.cur_max_spk}
            self.ended_ctx[mid] = ctx
            self.archiving_ids.add(mid)
            self._pause_started = None
            self.meeting_terms = []
            self.meeting_rules = []
            self.cur_brief = ""
            self.live_embs = {}
            self.asr.set_glossary(glossary.load())
        if self.native_capture_stop:
            self.native_capture_stop(mid)
        self._pub_state()
        bus.clear_history()
        bus.publish({"type": "reset"}, store=False)
        bus.publish({"type": "notice", "text": "archiving"}, store=False)
        threading.Thread(target=self._archive, args=(mid, buf, snap, ctx, transcription_done), daemon=True).start()
        return mid

    def _archive(self, mid, buf, snap, ctx, transcription_done):
        self.llm.set_meeting_ctx(mid)
        try:
            transcription_done.wait()
            if self.llm.available and buf:
                try:
                    self._summarize(mid, buf, brief=ctx["brief"], last_topic=ctx["last_topic"])
                except Exception:
                    log.exception("final segment summarize failed")
            m0 = db.query_one("SELECT audio_path FROM meetings WHERE id=?", (mid,)) or {}
            n_seg = db.query_one("SELECT COUNT(*) c FROM audio_segments WHERE meeting_id=?", (mid,))["c"]
            if m0.get("audio_path") and n_seg == 0:
                db.insert("audio_segments", {"meeting_id": mid, "path": m0["audio_path"],
                                             "start_off": 0, "duration": None})
                n_seg = 1
            audio_path = m0.get("audio_path")
            seg = self.recorder.save_segment(mid, snap, part=n_seg)
            if seg:
                path, dur = seg
                start_off = max(0.0, (ctx["live_since"] or ctx["started"]) - ctx["started"])
                db.insert("audio_segments", {"meeting_id": mid, "path": path,
                                             "start_off": start_off, "duration": dur})
                if not audio_path:
                    audio_path = path
            cur = db.query_one("SELECT title FROM meetings WHERE id=?", (mid,))
            old_title = (cur or {}).get("title") or ""
            auto = (not old_title) or "未正常结束" in old_title or "正常終了せず" in old_title \
                or bool(re.match(r"^(会议|会議|Meeting) \d{4}-\d{2}-\d{2} ", old_title))
            if not auto:
                title = old_title
            else:
                title = self._fallback_title(datetime.datetime.fromtimestamp(ctx["started"]))
            try:
                summary, todos, decisions = self._final_summary(mid, brief=ctx["brief"])
            except Exception:
                log.exception("final summary failed")
                summary, todos, decisions = [], [], []
            prev = db.query_one("SELECT summary, todos, decisions FROM meetings WHERE id=?", (mid,)) or {}
            summary = summary or json.loads(prev.get("summary") or "[]")
            todos = todos or json.loads(prev.get("todos") or "[]")
            decisions = decisions or json.loads(prev.get("decisions") or "[]")
            try:
                from . import memory as _mem
                from .context import profile as _prof
                _mem.extract_from_meeting(mid, self.llm, self.src_lang_name(mid), todos=todos,
                                          self_names=[config.get("self_name") or "", _prof.load().get("name") or "", *(config.get("my_names") or [])])
            except Exception:
                log.exception("memory extraction failed")
            try:
                self._refine_topics(mid)
            except Exception:
                log.exception("topic refine failed (keeping live topics)")
            prev_dur = (db.query_one("SELECT duration FROM meetings WHERE id=?", (mid,)) or {}).get("duration") or 0
            duration = prev_dur + (db.now() - (ctx["live_since"] or ctx["started"]))
            db.execute("""UPDATE meetings SET status='done', ended_at=?, duration=?,
                          title=?, audio_path=?, language=?, summary=?, todos=?, decisions=? WHERE id=?""",
                       (db.now(), duration, title, audio_path,
                        config.get("src_lang"), db.to_json(summary), db.to_json(todos),
                        db.to_json(decisions), mid))
            with self.lock:
                idle_now = self.state == "idle"
            if idle_now:
                self.speakers.purge_unnamed()
            bus.publish({"type": "meeting_done", "meeting_id": mid, "title": title}, store=False)
            log.info("meeting %s archived: %s", mid, title)
        except Exception:
            log.exception("archive failed for meeting %s", mid)
            db.execute("""UPDATE meetings SET status='done', ended_at=COALESCE(ended_at, ?)
                          WHERE id=? AND status='live'""", (db.now(), mid))
            bus.publish({"type": "meeting_done", "meeting_id": mid, "title": ""}, store=False)
        finally:
            self.llm.set_meeting_ctx(None)
            self.archiving_ids.discard(mid)
            self.ended_ctx.pop(mid, None)

    def bind_speaker(self, src_id: int, dst_id: int):
        if dst_id not in self.bind_backup:
            self.bind_backup[dst_id] = self.speakers.snapshot(dst_id)
        self.speakers.merge(src_id, dst_id)
        db.execute("DELETE FROM meeting_anon_labels WHERE speaker_id=?", (src_id,))
        if src_id in self.roster:
            self.roster.discard(src_id)
            self.roster.add(dst_id)
        bus.publish({"type": "speaker_merge", "from": src_id, "to": dst_id,
                     "name": self.disp(dst_id)})
        bus.publish({"type": "roster"}, store=False)

    def _recalc_cap(self, mid):
        named = db.query_one("SELECT COUNT(*) c FROM meeting_participants WHERE meeting_id=?", (mid,))["c"]
        total = named + self.cur_unknown
        self.cur_max_spk = total if total > 0 else 0
        db.execute("UPDATE meetings SET max_spk=? WHERE id=?", (self.cur_max_spk, mid))
        return False

    def roster_edit(self, mid, action, sid=None):
        with self.lock:
            if self.meeting_id != mid or self.state == "idle":
                return False
            added_voiced = False
            if action == "add" and sid:
                if not db.query_one("SELECT 1 x FROM meeting_participants WHERE meeting_id=? AND speaker_id=?",
                                    (mid, sid)):
                    c = db.query_one(
                        "SELECT name, embedding IS NOT NULL AND embedding != '' AS v FROM speakers WHERE id=?",
                        (sid,))
                    db.insert("meeting_participants", {"meeting_id": mid, "speaker_id": sid,
                                                       "name": (c or {}).get("name", "")})
                    if c and c["v"]:
                        self.roster.add(sid)
                        added_voiced = True
            elif action == "remove" and sid:
                db.execute("DELETE FROM meeting_participants WHERE meeting_id=? AND speaker_id=?", (mid, sid))
                self.roster.discard(sid)
                if not self.speakers.is_named(sid):
                    n = db.query_one("SELECT COUNT(*) c FROM utterances WHERE meeting_id=? AND speaker_id=?",
                                     (mid, sid))["c"]
                    if n == 0:
                        self.speakers.remove(sid)
                        db.execute("DELETE FROM meeting_anon_labels WHERE meeting_id=? AND speaker_id=?", (mid, sid))
            elif action == "add_unknown":
                self.cur_unknown = min(20, self.cur_unknown + 1)
            elif action == "remove_unknown":
                self.cur_unknown = max(0, self.cur_unknown - 1)
            else:
                return False
            lock_changed = self._recalc_cap(mid)
        if lock_changed:
            self._pub_state()
        if action == "add" and sid and added_voiced:
            self._retro_match(mid, sid)
        bus.publish({"type": "roster"}, store=False)
        return True

    def _retro_match(self, mid, sid):
        thr = float(config.get("spk_threshold"))
        moved = 0
        for u in db.query("SELECT id, speaker_id FROM utterances WHERE meeting_id=?", (mid,)):
            old = u["speaker_id"]
            if old == sid or old < 0 or self.speakers.is_named(old):
                continue
            rec = self.live_embs.get(u["id"])
            if not rec:
                continue
            emb, dur = rec
            c = self.speakers.centroid_of(sid)
            if c is None or float(emb @ c) < thr:
                continue
            db.execute("UPDATE utterances SET speaker_id=? WHERE id=?", (sid, u["id"]))
            self.reassign_feedback(u["id"], old, sid)
            bus.publish({"type": "utterance_speaker", "uid": u["id"], "spk": sid,
                         "name": self.disp(sid), "named": True})
            moved += 1
        if moved:
            log.info("retro-matched %d utterances to newly added participant %d", moved, sid)

    def reassign_feedback(self, uid, old_spk, new_spk, mid=None):
        if self.state == "idle" or not self.meeting_id:
            return
        if mid is not None and mid != self.meeting_id:
            return
        if old_spk is None or old_spk == new_spk or old_spk < 0 or new_spk < 0:
            return
        rec = self.live_embs.get(uid)
        if rec:
            emb, dur = rec
            if dur >= SPK_MIN_DUR:
                self.speakers.unlearn(emb, old_spk)
                self.speakers.learn(emb, dur, new_spk, False)
        roster_changed = new_spk not in self.roster
        self.roster.add(new_spk)
        left = db.query_one("SELECT COUNT(*) c FROM utterances WHERE meeting_id=? AND speaker_id=?",
                            (self.meeting_id, old_spk))["c"]
        if left == 0 and not self.speakers.is_named(old_spk):
            self.speakers.remove(old_spk)
            self.roster.discard(old_spk)
            db.execute("DELETE FROM meeting_anon_labels WHERE meeting_id=? AND speaker_id=?",
                       (self.meeting_id, old_spk))
            roster_changed = True
            log.info("empty anonymous speaker %d removed after reassign", old_spk)
        if roster_changed:
            bus.publish({"type": "roster"}, store=False)

    def _undo_bind_voice(self, member_id):
        if member_id not in self.bind_backup:
            return
        left = db.query_one("SELECT COUNT(*) c FROM utterances WHERE meeting_id=? AND speaker_id=?",
                            (self.meeting_id, member_id))["c"]
        if left:
            return
        self.speakers.restore(member_id, self.bind_backup.pop(member_id))
        log.info("voiceprint of member %s restored to its pre-bind state", member_id)

    def new_anon_speaker(self):
        if self.state == "idle" or not self.meeting_id:
            return None
        sid = db.insert("speakers", {"created_at": db.now()})
        with self.lock:
            self.roster.add(sid)
        name = self.disp(sid)
        bus.publish({"type": "roster"}, store=False)
        return {"id": sid, "name": name}

    def detach_speaker(self, member_id):
        if self.state == "idle" or not self.meeting_id or member_id is None or member_id < 0:
            return {"error": "not live"}
        mid = self.meeting_id
        rows = db.query("SELECT id FROM utterances WHERE meeting_id=? AND speaker_id=?", (mid, member_id))
        new_sid = db.insert("speakers", {"created_at": db.now()})
        for r in rows:
            rec = self.live_embs.get(r["id"])
            if rec:
                emb, dur = rec
                if dur >= SPK_MIN_DUR:
                    self.speakers.unlearn(emb, member_id)
                    self.speakers.learn(emb, dur, new_sid, False)
        db.execute("UPDATE utterances SET speaker_id=? WHERE meeting_id=? AND speaker_id=?",
                   (new_sid, mid, member_id))
        self._undo_bind_voice(member_id)
        with self.lock:
            self.roster.discard(member_id)
            self.roster.add(new_sid)
        old_name, new_name = self.speakers.name(member_id), self.disp(new_sid)
        self.refresh_topic_labels(old_name, new_name)
        if self._recalc_cap(mid):
            self._pub_state()
        bus.publish({"type": "speaker_merge", "from": member_id, "to": new_sid,
                     "name": new_name, "named": False})
        bus.publish({"type": "roster"}, store=False)
        log.info("detached %d utterances: %s -> %s (this meeting)", len(rows), old_name, new_name)
        return {"ok": True, "id": new_sid, "name": new_name, "moved": len(rows)}

    def reassign_speaker(self, old_spk, new_spk):
        if self.state == "idle" or not self.meeting_id:
            return {"error": "not live"}
        if old_spk == new_spk or old_spk < 0 or new_spk < 0:
            return {"error": "bad target"}
        mid = self.meeting_id
        rows = db.query("SELECT id FROM utterances WHERE meeting_id=? AND speaker_id=?",
                        (mid, old_spk))
        moved = 0
        for r in rows:
            rec = self.live_embs.get(r["id"])
            if rec:
                emb, dur = rec
                if dur >= SPK_MIN_DUR:
                    self.speakers.unlearn(emb, old_spk)
                    self.speakers.learn(emb, dur, new_spk, False)
            moved += 1
        db.execute("UPDATE utterances SET speaker_id=? WHERE meeting_id=? AND speaker_id=?",
                   (new_spk, mid, old_spk))
        self._undo_bind_voice(old_spk)
        self.roster.add(new_spk)
        self.roster.discard(old_spk)
        old_name, new_name = self.speakers.name(old_spk), self.speakers.name(new_spk)
        self.refresh_topic_labels(old_name, new_name)
        meetings.drop_empty_anon_label(mid, old_spk, store=self.speakers)
        bus.publish({"type": "speaker_merge", "from": old_spk, "to": new_spk,
                     "name": new_name})
        bus.publish({"type": "roster"}, store=False)
        log.info("reassigned %d utterances: %s -> %s (this meeting)", moved, old_name, new_name)
        return {"ok": True, "moved": moved, "name": new_name}

    def spk_suggest_decision(self, spk, member, accept):
        self.suggest_done.add((spk, member))
        if not accept:
            bus.publish({"type": "spk_suggest_clear", "spk": spk}, store=False)
            return {"ok": True}
        old_label = self.disp(spk)
        self.bind_speaker(spk, member)
        name = self.speakers.name(member)
        self.refresh_topic_labels(old_label, name)
        bus.publish({"type": "spk_suggest_clear", "spk": spk}, store=False)
        return {"ok": True, "name": name}

    def refresh_topic_labels(self, old_name, new_name, mid=None):
        mid = mid or self.meeting_id
        if not mid or not old_name or old_name == new_name:
            return
        changed = meetings.replace_topic_label(mid, old_name, new_name)
        if mid != self.meeting_id or not changed:
            return
        for t in db.query("SELECT * FROM topics WHERE meeting_id=?", (mid,)):
            if t["id"] not in changed:
                continue
            s = datetime.datetime.fromtimestamp(t["start_ts"] or self.meeting_started)
            e = datetime.datetime.fromtimestamp(t["end_ts"] or self.meeting_started)
            bus.publish({"type": "topic", "topic_id": t["id"], "merged": True,
                         "range": f"{s:%H:%M}~{e:%H:%M}", "title": t["title"], "title_src": t["title_src"] or "",
                         "points": json.loads(t["points"] or "[]")})

    def set_roster_lock(self, on: bool):
        with self.lock:
            self.roster_locked = bool(on)
        self._pub_state()
        bus.publish({"type": "roster"}, store=False)

    def set_auto_end(self, on: bool):
        with self.lock:
            self.cur_auto_end = bool(on)
            if on:
                self.last_activity = time.time()
        self._pub_state()

    def set_translate(self, on: bool):
        with self.lock:
            self.cur_translate = bool(on)
            if self.meeting_id:
                db.execute("UPDATE meetings SET translate_on=? WHERE id=?",
                           (int(on), self.meeting_id))
        self._pub_state()

    def set_mic(self, on: bool):
        with self.lock:
            self.cur_mic = bool(on)
        self._pub_state()

    @property
    def can_record(self):
        return self.ready and not self.asr_loading and self.asr.engine is not None and not self.asr_error

    def _pub_state(self):
        bus.publish({"type": "state", "mode": self.state, "ready": self.can_record, "asr_loading": self.asr_loading, "asr_error": self.asr_error,
                     "boot_error": self.boot_error,
                     "meeting_id": self.meeting_id, "llm_on": self.llm.available,
                     "translate": self.cur_translate, "mic": self.cur_mic,
                     "auto_end": self.cur_auto_end, "roster_locked": self.roster_locked,
                     "started_at": self.meeting_started, "title": self.live_title()}, store=False)

    def live_title(self):
        if not self.meeting_id or not self.meeting_started:
            return ""
        cur = (db.query_one("SELECT title FROM meetings WHERE id=?", (self.meeting_id,)) or {}).get("title") or ""
        return cur or self._fallback_title(datetime.datetime.fromtimestamp(self.meeting_started))

    def src_lang_name(self, mid):
        code = str(config.get("src_lang") or "ja")
        return LANG_NAMES.get(code[:2], "the meeting language")

    @staticmethod
    def _fallback_title(dt, orphan=False):
        lang = str(config.get("src_lang") or "ja")
        stamp = f"{dt:%Y-%m-%d %H:%M}"
        if lang == "ja":
            return f"会議 {stamp}" + ("（正常終了せず）" if orphan else "")
        if lang == "en":
            return f"Meeting {stamp}" + (" (not closed properly)" if orphan else "")
        return f"会议 {stamp}" + (" (未正常结束)" if orphan else "")

    FB_STEP = {"should": -0.02, "no": 0.01, "good": -0.01, "bad": 0.02}
    FB_SIGN = {"should": 1, "no": -1, "good": 1, "bad": -1}
    NM_BUMP_MIN, NM_BUMP_MAX = -0.08, 0.05

    def _adaptive_bumps(self):
        rows = db.query("""SELECT lane, verdict, created_at AS at FROM near_misses WHERE verdict IS NOT NULL AND lane != ''
                           UNION ALL
                           SELECT lane, verdict, created_at AS at FROM interventions WHERE verdict IS NOT NULL AND lane != ''
                           ORDER BY at DESC LIMIT 300""")
        seen, bumps, bias = {}, {}, {}
        for r in rows:
            lane = r["lane"]
            if seen.get(lane, 0) >= 30 or r["verdict"] not in self.FB_STEP:
                continue
            seen[lane] = seen.get(lane, 0) + 1
            bumps[lane] = bumps.get(lane, 0.0) + self.FB_STEP[r["verdict"]]
            bias[lane] = bias.get(lane, 0) + self.FB_SIGN[r["verdict"]]
        bumps = {k: round(max(self.NM_BUMP_MIN, min(self.NM_BUMP_MAX, v)), 3) for k, v in bumps.items() if abs(v) >= 0.005}
        if bumps:
            log.info("adaptive lane bumps: %s (bias %s)", bumps, bias)
        return bumps, bias

    def _extract_meeting_terms(self, mid):
        if not self.llm.available:
            return
        parts = []
        if self.cur_brief:
            parts.append(self.cur_brief)
        if self.series_digest:
            parts.append(self.series_digest)
        for r in db.query("""SELECT k.text FROM meeting_contexts mc
                             JOIN context_chunks k ON k.context_id = mc.context_id
                             WHERE mc.meeting_id=? AND k.seq < 3
                             ORDER BY COALESCE(mc.priority, 1) DESC, k.seq LIMIT 12""", (mid,)):
            parts.append(r["text"])
        if not parts:
            return
        try:
            data = self.llm.chat_json(
                "Extract up to 30 proper names, products, projects, people and abbreviations likely "
                "to be spoken in this meeting, preserving their original spelling. Extract any user-defined "
                "alert instructions as rules with a description and 5 to 10 relevant keywords likely to occur "
                "in Japanese, Chinese and English. Return only JSON: "
                '{"terms": [...], "rules": [{"desc": "...", "keywords": ["..."]}]}. '
                "Use empty arrays when no items are found.",
                "\n---\n".join(parts)[:6000], max_tokens=1200, role="classifier")
            terms = [str(t).strip() for t in (data or {}).get("terms", []) if str(t).strip()][:30]
            rules = []
            for r in (data or {}).get("rules", []):
                desc = str(r.get("desc", "")).strip()
                kws = [str(k).strip() for k in r.get("keywords", []) if usable_kw(str(k))][:10]
                if desc and kws:
                    rules.append({"desc": desc, "keywords": kws})
        except Exception:
            log.exception("term extraction failed")
            return
        if self.meeting_id == mid:
            if terms:
                self.meeting_terms = terms
                self.asr.set_glossary(glossary.load() + terms)
                log.info("meeting terms extracted: %s", ", ".join(terms[:10]) + ("..." if len(terms) > 10 else ""))
            if rules:
                self.meeting_rules = rules[:8]
                log.info("meeting alert rules: %s", "; ".join(
                    f"{r['desc']} [{','.join(r['keywords'])}]" for r in self.meeting_rules))

    def _refine_topics(self, mid):
        if not self.llm.available:
            return
        rows = db.query("SELECT id, start_ts, end_ts, title, points FROM topics "
                        "WHERE meeting_id=? ORDER BY id", (mid,))
        if len(rows) < 2:
            return
        lines = "\n".join(
            f"id={r['id']} [{datetime.datetime.fromtimestamp(r['start_ts']):%H:%M}] "
            f"{r['title']}: {_pts_line(json.loads(r['points'] or '[]'), with_uid=True)}" for r in rows)
        data = self.llm.chat_json(localization.localized_prompt("refine", REFINE_TOPICS_SYS_TMPL, out=out_name(), src=self.src_lang_name(self.meeting_id)),
                                  lines, max_tokens=2500, role="summary")
        groups = (data or {}).get("topics") or []
        by_id = {r["id"]: r for r in rows}
        valid_uids = {r["id"] for r in db.query(
            "SELECT id FROM utterances WHERE meeting_id=?", (mid,))}
        seen, clean = set(), []
        for g in groups:
            try:
                ids = [int(i) for i in (g.get("ids") or [])]
                ids_ok = [int(i) for i in ids if int(i) in by_id]
                title = _clean_title(g.get("title", ""), fallback=(by_id[ids_ok[0]]["title"] if ids_ok else ""))
                points = _norm_points(g.get("points"), valid_uids)
            except Exception:
                return
            if not ids or not title or not points:
                return
            if any(i not in by_id or i in seen for i in ids):
                return
            seen.update(ids)
            clean.append((ids, title, points, str(g.get("title_src") or "").strip()))
        if not clean or len(seen) < max(1, len(rows) // 3):
            return
        if len(rows) >= 4:
            if len(clean) < max(2, len(rows) // 3):
                log.warning("topic refine over-merged (%d -> %d), keeping live version",
                            len(rows), len(clean))
                return
            if any(len(ids) > max(2, len(rows) // 2) for ids, _, _ in clean):
                log.warning("topic refine has an oversized merge group, keeping live version")
                return
        for ids, title, points, title_src in clean:
            keep = ids[0]
            start = min(by_id[i]["start_ts"] or 0 for i in ids)
            end = max(by_id[i]["end_ts"] or 0 for i in ids)
            db.execute("UPDATE topics SET title=?, title_src=?, points=?, start_ts=?, end_ts=? WHERE id=?", (title, title_src, db.to_json(points), start, end, keep))
        drop = [r["id"] for r in rows if r["id"] not in {g[0][0] for g in clean}]
        if drop:
            ph = ",".join("?" * len(drop))
            db.execute(f"DELETE FROM topics WHERE id IN ({ph})", tuple(drop))
        log.info("topics refined: %d -> %d", len(rows), len(clean))

    def _final_summary(self, mid, brief=None):
        brief = self.cur_brief if brief is None else brief
        if not self.llm.available:
            return [], [], []
        utts = db.query("""SELECT id, speaker_id, text, corrected_text
                           FROM utterances WHERE meeting_id=? ORDER BY start_time""", (mid,))
        if len(utts) < 3:
            return [], [], []
        topics = db.query("SELECT title, points FROM topics WHERE meeting_id=? ORDER BY id", (mid,))
        parts = []
        if brief:
            parts.append("=== Meeting brief ===\n" + brief[:500])
        if topics:
            parts.append("=== Topic summaries ===\n" + "\n".join(
                f"{t['title']}: {_pts_line(json.loads(t['points']))}" for t in topics))
        labels = meetings.speaker_labels(mid)
        parts.append("=== Transcript ===\n" + "\n".join(
            f"#{u['id']} {labels.get(u['speaker_id'], self.speakers.name(u['speaker_id']))}: "
            f"{u['corrected_text'] or u['text']}"
            for u in utts[-400:]))
        try:
            data = self.llm.chat_json(localization.localized_prompt("final", FINAL_SYS_TMPL, out=out_name(), src=self.src_lang_name(mid)),
                                      "\n\n".join(parts), max_tokens=2500, role="summary")
        except Exception:
            log.exception("final summary failed")
            return [], [], []
        if not data:
            return [], [], []
        valid = {u["id"] for u in utts}
        def norm(items):
            out = []
            for it in (items or []):
                if not isinstance(it, dict):
                    continue
                text = str(it.get("text", "")).strip()
                if not text:
                    continue
                try:
                    uid = int(str(it.get("uid") or "0").lstrip("#") or 0)
                except Exception:
                    uid = None
                item = {"text": text, "uid": uid if uid in valid else None}
                src = str(it.get("src") or "").strip()
                if src and src != text:
                    item["src"] = src
                if it.get("owner"):
                    item["owner"] = str(it.get("owner"))[:30]
                out.append(item)
            return [x for x in out if x["text"]]
        return norm(data.get("summary")), norm(data.get("todos")), norm(data.get("decisions"))

    def _auto_end_loop(self):
        while True:
            time.sleep(10)
            try:
                if self.state == "idle":
                    continue
                mid = self.meeting_id
                if self.capture_seen is not None and time.monotonic() - self.capture_seen > 45:
                    if self.end_meeting(expected_mid=mid):
                        bus.publish({"type": "notice", "text": "audio_lost"}, store=False)
                    continue
                mins = float(config.get("auto_end_min") or 0)
                if mins <= 0 or not self.cur_auto_end or not self.last_activity:
                    continue
                if time.time() - self.last_activity >= mins * 60:
                    log.info("auto-ending meeting: %.0f min of silence", mins)
                    bus.publish({"type": "notice", "text": "auto_ended"}, store=False)
                    self.end_meeting(expected_mid=mid)
            except Exception:
                log.exception("auto-end check failed")

    def receive_audio(self, mid, samples):
        with self.lock:
            if mid != self.meeting_id:
                return False
            self.capture_seen = time.monotonic()
            if self.state == "recording":
                for index, channel in enumerate(("sys", "mic")):
                    audio = samples[:, index].copy()
                    if channel == "mic" and not self.cur_mic:
                        audio.fill(0)
                    if self.cur_record:
                        self.recorder.feed(channel, audio)
                    self.vads[channel].feed(audio)
            return True

    def receive_native_audio(self, mid, channel, samples):
        if channel not in ("sys", "mic"):
            return False
        with self.lock:
            if mid != self.meeting_id:
                return False
            self.capture_seen = time.monotonic()
            if self.state == "recording":
                if self.cur_record:
                    expected = self._native_record_offset + max(0, int((time.monotonic() - self._native_record_anchor) * SAMPLE_RATE) - len(samples))
                    gap = expected - self._native_record_samples[channel]
                    if gap > SAMPLE_RATE // 4:
                        self.recorder.feed(channel, np.zeros(gap, dtype=np.float32))
                        self._native_record_samples[channel] += gap
                    if channel == "mic" and not self.cur_mic:
                        samples = np.zeros_like(samples)
                    self.recorder.feed(channel, samples)
                    self._native_record_samples[channel] += len(samples)
                if channel == "mic" and not self.cur_mic:
                    return True
                self.vads[channel].feed(samples)
            return True

    def _on_segment(self, channel, started_at, audio):
        self.transcribe_q.put((self.meeting_id, channel, started_at, audio))

    def _asr_worker(self):
        while True:
            mid, channel, started_at, audio = self.transcribe_q.get()
            if mid is None:
                audio.set()
                continue
            try:
                self._process_segment(mid, channel, started_at, audio)
            except Exception:
                log.exception("asr segment failed")

    def _process_segment(self, mid, channel, started_at, audio):
        dur = len(audio) / SAMPLE_RATE
        emb, spk, is_new, newcomer = None, SELF, False, False
        live = (mid == self.meeting_id)
        ectx = None if live else self.ended_ctx.get(mid) or {}
        roster = self.roster if live else ectx.setdefault("roster", set())
        cap = (self.cur_max_spk if live else int(ectx.get("cap") or 0))
        if live:
            cap = max(1, len(roster), self.cur_max_spk) if self.roster_locked else 0
        mic_emb = None
        speaker_enabled = self.speaker_recognition_ready
        if channel == "mic":
            if not self.cur_mic:
                return
            if dur < MIC_MIN_DUR:
                return
            t0 = started_at.timestamp()
            sysv = self.vads.get("sys")
            overlap = sysv.speech_ratio(t0, t0 + dur) if sysv else 0.0
            if overlap >= ECHO_OVERLAP:
                log.info("mic segment dropped: overlaps system audio %.0f%% (echo)", overlap * 100)
                return
            if speaker_enabled:
                try:
                    mic_emb = self.speakers.embed(audio.astype("float32"))
                    sim = self.speakers.similarity_to_known(mic_emb)
                    sim_me = self.speakers.similarity_to_self(mic_emb)
                    if sim >= float(config.get("spk_threshold")) and sim > sim_me:
                        log.info("mic segment dropped as echo (sim=%.2f, me=%.2f)", sim, sim_me)
                        return
                    if self.speakers.self_n >= SELF_GATE_MIN_N and sim_me < SELF_GATE_SIM:
                        log.info("mic segment dropped: not my voice (me=%.2f)", sim_me)
                        return
                except Exception:
                    pass
            if overlap > 0.1:
                mic_emb = None
        else:
            spk = 0
            if speaker_enabled:
                emb = self.speakers.embed(audio.astype("float32"))
            if self.cur_mic and self.speakers.self_n >= SELF_GATE_MIN_N:
                micv = self.vads.get("mic")
                t0 = started_at.timestamp()
                mic_ov = micv.speech_ratio(t0, t0 + dur) if micv else 0.0
                sim_me = self.speakers.similarity_to_self(emb)
                if mic_ov >= ECHO_OVERLAP and sim_me >= max(0.55, float(config.get("spk_threshold")) + 0.1) \
                        and sim_me > self.speakers.similarity_to_known(emb):
                    log.info("sys segment dropped as own-voice echo (me=%.2f, mic overlap %.0f%%)", sim_me, mic_ov * 100)
                    return
            spk, is_new, confident = self.speakers.match(emb, dur, roster=roster, cap=cap)
        src_cfg = config.get("src_lang") or "ja"
        lang_arg = src_cfg
        text, det = self.asr.transcribe(audio, language=lang_arg)
        if not text:
            return
        if channel == "mic":
            conf = getattr(self.asr, "last_conf", None) or {}
            if conf.get("no_speech", 0) > MIC_NO_SPEECH_MAX or conf.get("logprob", 0) < MIC_LOGPROB_MIN:
                log.info("mic segment dropped: low ASR confidence %s %r", conf, text[:20])
                return
            import difflib
            now_ts = started_at.timestamp()
            for e in self.recent[-6:]:
                if e.get("spk") != SELF and abs(e["ts"] - now_ts) <= 6.0 \
                        and difflib.SequenceMatcher(None, e["text"], text).ratio() >= ECHO_TEXT_SIM:
                    log.info("mic segment dropped: same text as #%s (echo)", e["uid"])
                    return
        plain = re.sub(r"[\s、。，,.!?！？~ー〜…・]", "", text)
        if channel != "mic" and self.meeting_started and started_at.timestamp() - self.meeting_started > GREETING_GRACE_SEC \
                and _GREETING_RE.match(plain):
            log.info("greeting dropped as hallucination (%.0fs into meeting): %r", started_at.timestamp() - self.meeting_started, text)
            return
        if len(plain) <= 2 or (dur < FILLER_MAX_DUR and FILLER_RE.match(text)) \
                or (len(plain) <= HIRA_ONLY_MAX and re.fullmatch(r"[\u3041-\u3096ー]+", plain)):
            log.debug("filler dropped: %r (%.1fs)", text, dur)
            return
        if mic_emb is not None:
            self.speakers.learn_self(mic_emb, dur)
        if emb is not None:
            if is_new or confident:
                spk = self.speakers.learn(emb, dur, spk, is_new)
            newcomer = spk not in roster
            roster.add(spk)
        ts = started_at.timestamp()
        base = self.meeting_started if live else ectx.get("started")
        if base is None:
            r = db.query_one("SELECT started_at FROM meetings WHERE id=?", (mid,))
            base = (r or {}).get("started_at") or ts
        offset = max(0.0, ts - base)
        uid = db.insert("utterances", {
            "meeting_id": mid, "speaker_id": spk, "start_time": offset,
            "end_time": offset + dur, "ts": ts, "lang": det or "",
            "text": text})
        if live and emb is not None and newcomer:
            bus.publish({"type": "roster"}, store=False)
        if emb is not None and live:
            self.live_embs[uid] = (emb, dur)
            if len(self.live_embs) > 3000:
                for k in list(self.live_embs)[:500]:
                    del self.live_embs[k]
        entry = {"uid": uid, "ts": ts, "spk": spk, "text": text, "zh": "", "lang": det}
        if live:
            self.last_activity = time.time()
            with self.lock:
                self.recent.append(entry)
                del self.recent[:-40]
            if spk != SELF:
                self._late_echo_fix(entry)
            bus.publish({"type": "utterance", "uid": uid, "spk": spk,
                         "name": (config.get("self_name") or "") if spk == SELF else self.disp(spk), "named": self.speakers.is_named(spk),
                         "lang": det or "", "off": round(offset, 2),
                         "pending_tr": bool(self.llm.available and self.cur_translate),
                         "ts": datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
                         "text": text})
            if (emb is not None and spk >= 0
                    and not self.speakers.is_named(spk) and dur >= SPK_MIN_DUR):
                bn = self.speakers.best_named(emb)
                if bn and bn[1] >= float(config.get("spk_threshold")):
                    key = (spk, bn[0])
                    if key not in self.suggest_done:
                        self.suggest_votes[key] = self.suggest_votes.get(key, 0) + 1
                        if self.suggest_votes[key] >= 2:
                            self.suggest_done.add(key)
                            bus.publish({"type": "spk_suggest", "spk": spk, "member": bn[0],
                                         "name": self.speakers.name(bn[0])})
        if spk == SELF:
            self.rules.note_self_spoke()
        if self.llm.available:
            self.translate_q.put((mid, uid, entry, det))
            if spk != SELF and live:
                wtext = self._window_text(spk, uid, offset, dur, text)
                self.scan_pending += 1
                feats = self.rules.features(wtext, spk, det, known_names=self.speakers.known_names())
                if feats["name_hit"] and not self.rules.name_in(text):
                    feats["name_hit"] = False
                cand, why = self.rules.candidate(feats)
                _c = getattr(self.asr, "last_conf", None) or {}
                log.info("utt #%s %s: %s | name=%s fuzzy=%s q=%s kw=%s problem=%s invite=%s ns=%.2f lp=%.2f",
                         uid, self.disp(spk), wtext[-60:], int(feats["name_hit"]), feats.get("name_fuzzy") or "-",
                         int(feats["question"]), feats["keywords"][:2], int(bool(feats.get("problem"))), int(feats["invite"]),
                         _c.get("no_speech", 0.0), _c.get("logprob", 0.0))
                bus.publish({"type": "debug", "kind": "utt", "at": time.time(), "uid": uid, "by": self.disp(spk), "text": wtext[-80:],
                             "name": bool(feats["name_hit"]), "fuzzy": feats.get("name_fuzzy") or "", "q": bool(feats["question"]),
                             "kw": feats["keywords"][:3], "problem": bool(feats.get("problem")), "invite": bool(feats["invite"])}, store=False)
                if not cand and self.meeting_rules:
                    low = wtext.lower()
                    for r in self.meeting_rules:
                        if any(k.lower() in low for k in r["keywords"]):
                            cand, why = True, "user_rule"
                            break
                if not cand and feats["keywords"] and not feats["question"] \
                        and time.time() - self.last_contrib_eval > CONTRIB_THROTTLE_SEC \
                        and time.time() - self.last_contrib_shown > CONTRIB_QUIET_SEC:
                    self.last_contrib_eval = time.time()
                    cand, why = True, "topic_keyword"
                if not cand and feats.get("problem") \
                        and time.time() - self.last_contrib_shown > CONTRIB_QUIET_SEC:
                    self.last_contrib_eval = time.time()
                    cand, why = True, "problem"
                if not cand and time.time() - self.last_mem_eval > 10:
                    try:
                        from . import memory as _mem
                        mfacts, _ = _mem.retrieve(wtext, mid, spk)
                    except Exception:
                        mfacts = []
                    if mfacts:
                        self.last_mem_eval = time.time()
                        cand, why = True, "memory"
                scan_iv = int(config.get("scan_interval_sec") or 0)
                if not cand and scan_iv > 0 and self.scan_pending >= 3 \
                        and time.time() - self.last_engine_at >= scan_iv:
                    cand, why = True, "scan"
                if cand:
                    log.info("candidate lane=%s: %s", why, wtext[:60])
                    bus.publish({"type": "debug", "kind": "candidate", "at": time.time(), "uid": uid, "lane": why, "text": wtext[-80:]}, store=False)
                    self.last_engine_at = time.time()
                    self.scan_pending = 0
                    if why in ("name", "user_rule"):
                        bus.publish({"type": "intervention_pre", "uid": uid, "kind": "RESPOND",
                                     "by": self.disp(spk), "question": wtext, "lane": why}, store=False)
                    self.engine_q.put((mid, uid, wtext, spk, why, time.time()))

    def _late_echo_fix(self, entry):
        import difflib
        victims = [e for e in self.recent[-10:]
                   if e.get("spk") == SELF and abs(e["ts"] - entry["ts"]) <= 8.0 and len(e["text"]) >= 6
                   and difflib.SequenceMatcher(None, e["text"], entry["text"]).ratio() >= ECHO_TEXT_SIM]
        for e in victims:
            db.execute("DELETE FROM utterances WHERE id=?", (e["uid"],))
            with self.lock:
                self.recent = [x for x in self.recent if x["uid"] != e["uid"]]
                self.topic_buffer = [x for x in self.topic_buffer if x["uid"] != e["uid"]]
            bus.publish({"type": "utterance_delete", "uid": e["uid"]})
            log.info("late echo removed: self #%s %r ≈ %s #%s", e["uid"], e["text"][:20], self.disp(entry["spk"]), entry["uid"])

    def inject_self_utterance(self, text):
        mid = self.meeting_id
        if not mid or self.state != "recording" or not (text or "").strip():
            return None
        text = text.strip()[:300]
        ts = time.time()
        offset = max(0.0, ts - (self.meeting_started or ts))
        dur = max(1.0, len(text) / 5.0)
        uid = db.insert("utterances", {"meeting_id": mid, "speaker_id": SELF, "start_time": offset,
                                       "end_time": offset + dur, "ts": ts, "lang": "", "text": text})
        entry = {"uid": uid, "ts": ts, "spk": SELF, "text": text, "zh": "", "lang": None}
        self.last_activity = time.time()
        with self.lock:
            self.recent.append(entry)
            del self.recent[:-40]
        bus.publish({"type": "utterance", "uid": uid, "spk": SELF, "name": config.get("self_name") or "", "named": True,
                     "lang": "", "off": round(offset, 2), "pending_tr": bool(self.llm.available and self.cur_translate),
                     "ts": datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S"), "text": text})
        self.rules.note_self_spoke()
        if self.llm.available:
            self.translate_q.put((mid, uid, entry, None))
        bus.publish({"type": "debug", "kind": "utt", "at": ts, "uid": uid, "by": config.get("self_name") or "Me", "text": text[-80:],
                     "name": False, "fuzzy": "", "q": False, "kw": [], "problem": False, "invite": False}, store=False)
        return uid

    WIN_GAP = 2.5
    WIN_MAX_SEG = 5
    WIN_MAX_CHARS = 160

    def _window_text(self, spk, uid, offset, dur, text):
        w = self.win.get(spk) or []
        if w and offset - w[-1][1] > self.WIN_GAP:
            w = []
        w.append((uid, offset + dur, text))
        w = w[-self.WIN_MAX_SEG:]
        self.win[spk] = w
        for other in list(self.win):
            if other != spk:
                self.win.pop(other, None)
        joined = "".join(t for _, _, t in w) if any("\u3040" <= ch <= "\u9fff" for ch in text) \
            else " ".join(t for _, _, t in w)
        return joined[-self.WIN_MAX_CHARS:]

    def _translate_worker(self):
        recent_pairs = []
        last_mid = None
        while True:
            mid, uid, entry, lang = self.translate_q.get()
            if mid != self.meeting_id and mid not in self.archiving_ids:
                continue
            self.llm.set_meeting_ctx(mid)
            if mid != last_mid:
                recent_pairs.clear()
                last_mid = mid
            if lang == config.get("out_lang") or not self.cur_translate:
                zh = ""
            else:
                ctx = ""
                if recent_pairs:
                    ctx = "(Previous context for reference only; do not translate:\n" + "\n".join(
                        f"{a} => {b}" for a, b in recent_pairs[-4:]) + ")\n\n"
                try:
                    from .context import glossary as _gl
                    terms = ", ".join((_gl.load() + self.meeting_terms)[:45])
                    gl_part = f"(Glossary: {terms})\n" if terms else ""
                    data = self.llm.chat_json(
                        localization.localized_prompt("translation", TRANSLATE_SYS_TMPL, out=tr_name()),
                        f"{gl_part}{ctx}Current utterance: {entry['text']}", max_tokens=900, timeout=20.0, role="translation")
                    zh = str((data or {}).get("out", "")).strip()
                    fixed = str((data or {}).get("src", "")).strip()
                    if zh.strip() == entry["text"].strip():
                        zh = ""
                    if entry.get("spk") != SELF and fix_ok(entry["text"], fixed):
                        meetings.edit_utterance(uid, text=fixed)
                        entry["text"] = fixed
                        if mid == self.meeting_id:
                            bus.publish({"type": "revise",
                                         "fixes": [{"uid": uid, "text": fixed, "translation": zh}]})
                    recent_pairs.append((entry["text"], zh))
                    del recent_pairs[:-8]
                except Exception as e:
                    log.warning("translate failed for #%s: %s", uid, e)
                    zh = ""
            entry["zh"] = zh
            if zh:
                db.execute("UPDATE utterances SET translation=? WHERE id=?", (zh, uid))
            if mid == self.meeting_id and not entry.get("retrans"):
                with self.lock:
                    self.topic_buffer.append(entry)
                    self.last_utt_time = time.time()
                bus.publish({"type": "translation", "uid": uid, "text": zh})

    ENGINE_STALE_SEC = 30.0

    def _engine_worker(self):
        while True:
            mid, uid, text, spk, why, t_q = self.engine_q.get()
            pre = why in ("name", "user_rule")
            if mid != self.meeting_id:
                if pre:
                    bus.publish({"type": "intervention_cancel", "uid": uid, "reason": "none"}, store=False)
                continue
            self.llm.set_meeting_ctx(mid)
            if not pre and time.time() - t_q > self.ENGINE_STALE_SEC:
                log.info("stale candidate dropped (lane=%s, queued %.0fs)", why, time.time() - t_q)
                continue
            try:
                tail = [f"#{e['uid']} {self.disp(e['spk'])}: {e['text']}"
                        for e in self.recent[-25:]]
                topics_tail = [f"{t['title']}: {_pts_line(json.loads(t['points']))}"
                               for t in db.query(
                                   "SELECT title, points FROM topics WHERE meeting_id=? ORDER BY id DESC LIMIT 2",
                                   (mid,))]
                ev = self.reasoning.evaluate(mid, uid, text, spk, tail, topics_tail,
                                             rules=self.meeting_rules, lane=why)
                if ev:
                    if ev.get("kind") == "CONTRIBUTE":
                        self.last_contrib_shown = time.time()
                    threading.Thread(target=self._expand_stances, args=(mid, ev["iv_id"], uid), daemon=True).start()
                if pre and not ev:
                    bus.publish({"type": "intervention_cancel", "uid": uid, "reason": "none"}, store=False)
            except Exception as e:
                log.error("engine failed (lane=%s): %s", why, str(e)[:200])
                if pre:
                    bus.publish({"type": "intervention_cancel", "uid": uid, "reason": "error"}, store=False)

    def _expand_stances(self, mid, iv_id, uid):
        try:
            replies = self.reasoning.expand_stances(iv_id)
            if replies and mid == self.meeting_id:
                bus.publish({"type": "intervention_update", "iv_id": iv_id, "uid": uid, "replies": replies})
        except Exception as e:
            log.warning("expand stances failed for #%s: %s", iv_id, str(e)[:120])

    def _summary_worker(self):
        while True:
            time.sleep(4)
            if self.state == "idle" or not self.llm.available:
                continue
            with self.lock:
                if not self.topic_buffer:
                    continue
                chars = sum(len(e["text"]) for e in self.topic_buffer)
                idle = time.time() - self.last_utt_time if self.last_utt_time else 0
                due = (idle > float(config.get("topic_pause_sec")) and len(self.topic_buffer) >= 2) \
                    or chars > int(config.get("topic_max_chars"))
                if not due:
                    continue
                buf, self.topic_buffer = self.topic_buffer, []
                mid = self.meeting_id
            if mid:
                try:
                    self._summarize(mid, buf)
                except Exception:
                    log.exception("summary failed")

    def _summarize(self, mid, buf, brief=None, last_topic=None):
        brief = self.cur_brief if brief is None else brief
        last_topic = self.last_topic if last_topic is None else last_topic
        lines = "\n".join(
            f"#{e['uid']} [{datetime.datetime.fromtimestamp(e['ts']):%H:%M:%S}] "
            f"{self.disp(e['spk'], mid)}: {e['text']}" for e in buf)
        prev = ""
        valid_uids = {e["uid"] for e in buf}
        if brief:
            prev += f"=== Meeting brief (provided by user) ===\n{brief[:500]}\n\n"
        if last_topic:
            row = db.query_one("SELECT title, points FROM topics WHERE id=?", (last_topic,))
            if row:
                prev_pts = json.loads(row["points"] or "[]")
                valid_uids |= {p["uid"] for p in prev_pts if isinstance(p, dict) and p.get("uid")}
                prev += f"=== Previous topic ===\n{row['title']}: {_pts_line(prev_pts, with_uid=True)}\n\n"
        data = self.llm.chat_json(localization.localized_prompt("summary", SUMMARY_SYS_TMPL, out=out_name(), src=self.src_lang_name(mid)), prev + lines, max_tokens=2500, role="summary")
        if not data:
            return
        title = _clean_title(data.get("title", ""))
        title_src = str(data.get("title_src") or "").strip()
        if title_src == title:
            title_src = ""
        points = _norm_points(data.get("points"), valid_uids)
        merge = bool(data.get("merge_with_prev")) and last_topic
        if merge:
            tid = last_topic
            db.execute("UPDATE topics SET title=?, title_src=?, points=?, end_ts=? WHERE id=?",
                       (title, title_src, db.to_json(points), buf[-1]["ts"], tid))
            row = db.query_one("SELECT start_ts FROM topics WHERE id=?", (tid,))
            start_ts = (row or {}).get("start_ts") or buf[0]["ts"]
        else:
            tid = db.insert("topics", {"meeting_id": mid, "start_ts": buf[0]["ts"],
                                       "end_ts": buf[-1]["ts"],
                                       "title": title, "title_src": title_src, "points": db.to_json(points)})
            start_ts = buf[0]["ts"]
        if mid == self.meeting_id:
            self.last_topic = tid
        s = datetime.datetime.fromtimestamp(start_ts)
        e = datetime.datetime.fromtimestamp(buf[-1]["ts"])
        if mid == self.meeting_id:
            bus.publish({"type": "topic", "topic_id": tid, "merged": bool(merge),
                         "range": f"{s:%H:%M}~{e:%H:%M}", "title": title, "title_src": title_src, "points": points})
        by_uid = {x["uid"]: x for x in buf}
        for nid in (data.get("noise") or [])[:5]:
            try:
                nid = int(nid)
                ent = by_uid.get(nid)
                if not ent or ent.get("spk") == SELF:
                    continue
                plain = re.sub(r"[\s、。，,.!?！？~ー〜…・]", "", ent["text"] or "")
                if len(plain) > NOISE_MAX_CHARS or not (_STOCK_RE.match(plain) or _GREETING_RE.match(plain) or len(plain) <= 4):
                    continue
                if not db.query_one("SELECT 1 x FROM utterances WHERE id=?", (nid,)):
                    continue
                db.execute("DELETE FROM utterances WHERE id=?", (nid,))
                meetings.drop_empty_anon_label(mid, ent.get("spk"), store=self.speakers)
                bus.publish({"type": "utterance_delete", "uid": nid})
                log.info("noise utterance #%s removed by context check: %r", nid, ent["text"])
            except Exception:
                continue
        applied = []
        for f in (data.get("fixes") or []):
            try:
                uid = int(f.get("id"))
                text = str(f.get("text", "")).strip()
                ent = by_uid.get(uid)
                if not ent or ent.get("spk") == SELF or not fix_ok(ent["text"], text):
                    continue
                meetings.edit_utterance(uid, text=text)
                ent["text"] = text
                applied.append({"uid": uid, "text": text, "translation": ent["zh"]})
                if self.cur_translate and self.llm.available:
                    self.translate_q.put((mid, uid, {**ent, "retrans": True}, ent.get("lang")))
            except Exception:
                continue
        if applied and mid == self.meeting_id:
            bus.publish({"type": "revise", "fixes": applied})


pipeline = Pipeline()
