import json
import logging
import time

from .. import config, db, localization
from ..context import profile, glossary, files
from .. import memory
from ..events import bus
from .rules import QUESTION_RE

log = logging.getLogger("engine")

OUT_NAMES = {"zh": "Simplified Chinese", "zh-Hant": "Traditional Chinese", "ja": "Japanese", "en": "English"}


SRC_NAMES = {"ja": "Japanese", "zh": "Chinese", "en": "English"}


def src_name():
    code = str(config.get("src_lang") or "ja")
    return SRC_NAMES.get(code, code)


def out_name():
    return src_name()


def tr_name():
    return OUT_NAMES.get(config.get("out_lang"), "Simplified Chinese")


def _system_prompt(src="the meeting language"):
    return (
        f"The meeting language is {src}. Write all output text in {src}. "
        "You are Deban, the user's meeting assistant. Evaluate the utterance using the user's profile, "
        "meeting context and relevant materials. Return only JSON with the following fields: "
        '{"type": "RESPOND|CONSIDER|CONTRIBUTE|ASK|NONE", "confidence": 0.0, '
        '"urgency": "high|normal", "by": "speaker", "question": "one sentence", '
        '"context_points": ["up to three points"], "stance": "positive|neutral|negative", '
        '"situation": "agenda|report|problem|question|other", '
        '"reply_guide": "one or two points describing how to respond", '
        '"reply_src": "a polite spoken example of at most two sentences", '
        '"why": "one sentence explaining the alert", '
        '"sources": {"materials": ["supporting material IDs"], "utterances": [123]}}. '
        "List only sources actually used. RESPOND means the user was named and a response is expected. "
        "CONSIDER means the user may be addressed or a question concerns the user's responsibilities. "
        "CONTRIBUTE means no one asked the user but the user can add specific valuable information. "
        "ASK means questions or opinions are invited and a concrete unresolved point can be raised. "
        "Use NONE when unrelated or no valuable contribution exists. "
        "A matching custom alert condition requires at least CONSIDER, or RESPOND if it requests a response. "
        "Mention the condition at the start of question. Account for possible transcription errors.")


class ReasoningEngine:
    def __init__(self, llm, speakers):
        self.llm = llm
        self.speakers = speakers
        self.disp = speakers.name
        self.lane_bumps = {}
        self.lane_bias = {}
        self.recent = []
        self.meeting_counts = {}

    RELAX_BIAS = 3
    RELAX_CONF = 0.85

    def reset_meeting(self):
        self.recent = []
        self.meeting_counts = {}

    def _dedup(self, question, by, uid=None):
        import difflib
        now = time.time()
        self.recent = [r for r in self.recent if now - r[2] < 90]
        for q, b, t, r_uid, _reply, *rest in self.recent:
            if b != by:
                continue
            if q[:12] == question[:12] or q == question:
                return True
            if difflib.SequenceMatcher(None, q, question).ratio() >= 0.6:
                return True
            if uid is not None and r_uid is not None and abs(uid - r_uid) <= 2 and now - t < 20:
                spk = rest[0] if rest else None
                lo, hi = sorted((uid, r_uid))
                between = db.query("SELECT DISTINCT speaker_id FROM utterances WHERE id > ? AND id < ?", (lo, hi))
                if spk is None or all(r["speaker_id"] == spk for r in between):
                    return True
        return False

    def _recent_alerts_prompt(self):
        now = time.time()
        rows = [r for r in self.recent if now - r[2] < 180][-2:]
        if not rows:
            return ""
        lines = [f"- {int(now - t)} seconds ago · {b}: {q}" + (f" (previous suggested reply: {reply[:60]})" if reply else "")
                 for q, b, t, _u, reply, *_ in rows]
        return ("\n\n=== Recent alerts ===\n" + "\n".join(lines)
                + "\nIf the triggering utterance continues or restates the same request or arrangement, do not alert again: "
                  'return type "NONE" and explain that it was already raised. Alert only for a new request or topic.')

    def build_user_prompt(self, meeting_id, trigger_text, trigger_spk, transcript_tail, topics_tail):
        parts = []
        pp = profile.as_prompt()
        if pp:
            parts.append("=== User profile ===\n" + pp)
        m = db.query_one("SELECT notes, series_digest FROM meetings WHERE id=?", (meeting_id,))
        sh = profile.style_hint()
        if sh:
            parts.append("=== Preferred reply style (apply to reply_src) ===\n" + sh)
        if m and (m.get("notes") or "").strip():
            parts.append("=== Meeting brief (provided by user) ===\n" + m["notes"][:800])
        if m and (m.get("series_digest") or "").strip():
            parts.append("=== Previous meeting summary in this series ===\n" + m["series_digest"][:1000])
        terms = glossary.load()
        if terms:
            parts.append("=== User glossary ===\n" + ", ".join(terms[:40]))
        chunks = files.retrieve(trigger_text, meeting_id=meeting_id)
        if chunks:
            tag = {2: "Critical|", 0: "Reference only|"}
            parts.append("=== Relevant materials (prioritize Critical; cite M IDs) ===\n" + "\n---\n".join(
                f"[M{i}|{tag.get(c.get('priority'), '')}{c['name']}] {c['text'][:400]}"
                for i, c in enumerate(chunks, 1)))
        if topics_tail:
            parts.append("=== Previous topics ===\n" + "\n".join(topics_tail))
        parts.append("=== Recent conversation (each utterance starts with #ID) ===\n" + "\n".join(transcript_tail))
        try:
            facts, myutts = memory.retrieve(trigger_text + " " + " ".join(transcript_tail[-3:]), meeting_id, trigger_spk)
        except Exception:
            facts, myutts = [], []
        self._last_facts = [f["id"] for f in facts]
        self._last_mem = ([{"id": f["id"], "text": f["text"][:60]} for f in facts], [{"id": u["id"], "text": (u["corrected_text"] or u["text"])[:60]} for u in myutts])
        blk = memory.prompt_block(facts, myutts)
        if blk:
            parts.append(blk)
        parts.append(f"=== Triggering utterance ===\n{self.disp(trigger_spk)}: {trigger_text}")
        self._last_chunks = chunks
        return "\n\n".join(parts)

    def _resolve_sources(self, data, meeting_id):
        src = data.get("sources") or {}
        chunks = getattr(self, "_last_chunks", []) or []
        mats, seen = [], set()
        for m in (src.get("materials") or []):
            try:
                i = int(str(m).upper().lstrip("M").strip()) - 1
            except Exception:
                continue
            if 0 <= i < len(chunks) and chunks[i]["context_id"] not in seen:
                c = chunks[i]
                seen.add(c["context_id"])
                mats.append({"id": c["context_id"], "name": c["name"],
                             "snippet": (c["text"] or "")[:120]})
        utts = []
        uids = []
        for u in (src.get("utterances") or []):
            try:
                uids.append(int(str(u).lstrip("#")))
            except Exception:
                continue
        if uids:
            ph = ",".join("?" * len(uids[:6]))
            rows = db.query(f"SELECT id, speaker_id, text, meeting_id FROM utterances WHERE id IN ({ph})", tuple(uids[:6]))
            by_id = {r["id"]: r for r in rows}
            for uid in uids[:6]:
                r = by_id.get(uid)
                if r:
                    utts.append({"uid": uid, "by": self.disp(r["speaker_id"]),
                                 "text": (r["text"] or "")[:80], "mid": r["meeting_id"]})
        return {"materials": mats, "utterances": utts}

    def _log_near_miss(self, meeting_id, utterance_id, trigger_text, trigger_spk, lane, llm_type, conf, why,
                       reason="", detail=""):
        try:
            db.insert("near_misses", {
                "meeting_id": meeting_id, "utterance_id": utterance_id,
                "by_speaker": self.disp(trigger_spk), "text": (trigger_text or "")[:200],
                "lane": lane, "llm_type": llm_type, "confidence": conf, "why": why,
                "reason": reason, "detail": detail, "created_at": db.now()})
        except Exception:
            log.exception("near-miss log failed")

    def evaluate(self, meeting_id, utterance_id, trigger_text, trigger_spk,
                 transcript_tail, topics_tail, rules=None, lane=""):
        t0 = time.time()
        prompt = self.build_user_prompt(
            meeting_id, trigger_text, trigger_spk, transcript_tail, topics_tail)
        if rules:
            prompt = ("=== User-defined alert conditions ===\n"
                      + "\n".join(f"- {r['desc']}" for r in rules) + "\n\n" + prompt)
        if lane == "scan":
            prompt += ("\n\nThis periodic scan has no local name or keyword signal. Check whether someone "
                       "awaits the user or the user can contribute specific useful information. Otherwise select NONE.")
        if lane != "name":
            prompt += self._recent_alerts_prompt()
        if lane == "name_fuzzy":
            prompt += ("\n\nA name before an honorific may be a transcription error resembling the user's name. "
                       "Use context to determine whether the user is addressed.")
        src = src_name()
        data = self.llm.chat_json(localization.localized_prompt("opportunity", _system_prompt(src), src=src), prompt, max_tokens=700, timeout=20.0, role="opportunity")
        if data and ("type" not in data or "confidence" not in data):
            log.warning("engine: reply without verdict (lane=%s): %s — retrying", lane, json.dumps(data, ensure_ascii=False)[:200])
            data = self.llm.chat_json(localization.localized_prompt("opportunity", _system_prompt(src), src=src), prompt, max_tokens=700, timeout=20.0, role="opportunity")
        if not data:
            return None
        typ = str(data.get("type", "NONE")).upper()
        if typ == "RESPOND" and lane not in self.DIRECT_LANES:
            typ = "CONSIDER"
        typ0 = typ
        conf = float(data.get("confidence") or 0)
        why = str(data.get("why", "")).strip()
        thr = lambda k: float(config.get(f"{k}_threshold")) + self.lane_bumps.get(lane, 0.0)
        drop = ("llm_none", "") if typ0 == "NONE" else None
        if typ == "RESPOND" and conf < thr("respond"):
            typ = "CONSIDER"
        for k in ("consider", "contribute", "ask"):
            if typ == k.upper() and conf < thr(k):
                drop = ("threshold", f"{conf:.2f} < {thr(k):.2f}")
                typ = "NONE"
        if lane in self.DIRECT_LANES and typ == "CONSIDER":
            typ = "RESPOND"
        if typ == "ASK" and lane not in ("ask_invite", "scan"):
            log.info("engine: lane=%s llm=ASK → ASK only from an invitation, dropped", lane)
            drop = ("ask_no_invite", "")
            typ = "NONE"
        if lane == "name_fuzzy":
            if typ == "RESPOND" and conf >= 0.9:
                pass
            elif typ in ("RESPOND", "CONSIDER"):
                typ = "CONSIDER"
            elif typ == "NONE" and QUESTION_RE.search(trigger_text or ""):
                typ, conf = "CONSIDER", max(conf, float(config.get("consider_threshold")))
                data.setdefault("question", "")
                if not data.get("question"):
                    data["question"] = trigger_text
                log.info("engine: lane=name_fuzzy llm=NONE but question addressed to a near-homophone → CONSIDER")
        if lane in ("topic_keyword", "problem") and typ == "CONTRIBUTE" and str(data.get("situation", "")).lower() in ("agenda", "report"):
            log.info("engine: lane=%s llm=CONTRIBUTE situation=%s → agenda/report mention, dropped", lane, data.get("situation"))
            drop = ("agenda_report", str(data.get("situation", "")))
            typ = "NONE"
        if lane in ("topic_keyword", "problem") and typ in ("RESPOND", "CONSIDER"):
            if str(data.get("situation", "")).lower() == "problem":
                typ = "CONSIDER"
                log.info("engine: lane=topic_keyword llm=%s situation=problem → CONSIDER", typ0)
            elif self.lane_bias.get(lane, 0) >= self.RELAX_BIAS and conf >= self.RELAX_CONF:
                typ = "CONSIDER"
                log.info("engine: lane=%s llm=%s situation=%s conf=%.2f → kept by feedback (bias %+d)",
                         lane, typ0, data.get("situation"), conf, self.lane_bias.get(lane, 0))
            else:
                log.info("engine: lane=topic_keyword llm=%s situation=%s → not about the user, dropped", typ, data.get("situation"))
                drop = ("not_about_user", str(data.get("situation", "")))
                typ = "NONE"
        by = str(data.get("by") or self.disp(trigger_spk))
        if lane == "name":
            now_ = time.time()
            u_now = db.query_one("SELECT start_time FROM utterances WHERE id=?", (utterance_id,))
            t_now = float((u_now or {}).get("start_time") or 0)
            def _gap(r):
                if len(r) > 6 and r[6] is not None:
                    return t_now - r[6]
                return now_ - r[2]
            recent_same = any(len(r) > 5 and r[5] == trigger_spk and 0 <= _gap(r) < self.NAME_REPEAT_SEC for r in self.recent)
            typ = "CONSIDER" if recent_same else "RESPOND"
        _mem = getattr(self, "_last_mem", ([], []))
        def _dbg(shown, reason=""):
            bus.publish({"type": "debug", "kind": "verdict", "at": time.time(), "uid": utterance_id, "lane": lane, "llm": typ0, "conf": round(conf, 2),
                         "situation": str(data.get("situation", "") or ""), "shown": shown, "reason": reason,
                         "ms": int((time.time() - t0) * 1000), "facts": _mem[0], "utts": _mem[1]}, store=False)
        if typ in ("RESPOND", "CONSIDER", "CONTRIBUTE", "ASK") and (
                not config.get("alerts_enabled") or not config.get(f"alert_{typ.lower()}")):
            _dbg("NONE", "disabled in settings")
            log.info("engine: lane=%s llm=%s → %s alerts are turned off in settings", lane, typ0, typ)
            return None
        if typ not in ("RESPOND", "CONSIDER", "CONTRIBUTE", "ASK"):
            _dbg("NONE", "threshold / lane rule")
            log.info("engine: lane=%s llm=%s conf=%.2f → no alert", lane, typ0, conf)
            if typ0 in ("RESPOND", "CONSIDER") or lane in ("name", "user_rule"):
                r, d = drop or ("llm_none", "")
                self._log_near_miss(meeting_id, utterance_id, trigger_text, trigger_spk,
                                    lane, typ0, conf, why, reason=r, detail=d)
            return None
        caps = {"CONTRIBUTE": int(config.get("contribute_max_per_meeting")),
                "ASK": int(config.get("ask_max_per_meeting"))}
        if typ in caps and self.meeting_counts.get(typ, 0) >= caps[typ]:
            log.info("suppressed %s: per-meeting cap %d reached", typ, caps[typ])
            _dbg("NONE", f"cap {caps[typ]}/meeting")
            self._log_near_miss(meeting_id, utterance_id, trigger_text, trigger_spk, lane, typ0, conf, why,
                                reason="cap", detail=f"{caps[typ]}")
            return None
        question = str(data.get("question", ""))
        if lane != "name" and self._dedup(question, by, utterance_id):
            log.info("suppressed duplicate intervention: %s", question[:40])
            _dbg("NONE", "duplicate")
            self._log_near_miss(meeting_id, utterance_id, trigger_text, trigger_spk, lane, typ0, conf, why,
                                reason="duplicate")
            return None
        u_row = db.query_one("SELECT start_time FROM utterances WHERE id=?", (utterance_id,))
        self.recent.append((question, by, time.time(), utterance_id, str(data.get("reply_src", "")), trigger_spk,
                            float((u_row or {}).get("start_time") or 0) if u_row else None))
        self.meeting_counts[typ] = self.meeting_counts.get(typ, 0) + 1
        sources = self._resolve_sources(data, meeting_id)
        u = db.query_one("SELECT ts, start_time, end_time FROM utterances WHERE id=?", (utterance_id,))
        tti_ms = 0
        if u:
            spoke_end = u["ts"] + max(0, (u["end_time"] or 0) - (u["start_time"] or 0))
            tti_ms = max(0, int((time.time() - spoke_end) * 1000))
        stance = str(data.get("stance", "neutral")).lower()
        if stance not in self.STYLES:
            stance = "neutral"
        replies = {stance: {"guide": str(data.get("reply_guide", "")), "guide_src": str(data.get("reply_guide_src", "")),
                            "src": str(data.get("reply_src", "")), "out": str(data.get("reply_out", ""))}}
        ctx_src = [str(c) for c in (data.get("context_points_src") or [])]
        iv_id = db.insert("interventions", {
            "meeting_id": meeting_id, "utterance_id": utterance_id, "latency_ms": tti_ms,
            "type": typ, "confidence": conf, "urgency": str(data.get("urgency", "normal")),
            "by_speaker": by, "question": question, "question_src": str(data.get("question_src", "")),
            "context": db.to_json([str(c) for c in data.get("context_points", [])]),
            "context_src": db.to_json(ctx_src),
            "suggested_answer": str(data.get("reply_src", "")),
            "answer_explain": str(data.get("reply_out", "")),
            "reply_guide": str(data.get("reply_guide", "")),
            "reply_guide_src": str(data.get("reply_guide_src", "")),
            "stance": stance, "replies": db.to_json(replies),
            "sources": db.to_json(sources), "why": why, "why_src": str(data.get("why_src", "")), "lane": lane,
            "facts": db.to_json(getattr(self, "_last_facts", []) or []),
            "created_at": db.now()})
        if getattr(self, "_last_facts", None):
            try:
                memory.mark_used(self._last_facts)
            except Exception:
                pass
        ev = {"type": "intervention", "iv_id": iv_id, "uid": utterance_id, "kind": typ,
              "confidence": conf, "by": by, "question": question, "question_src": str(data.get("question_src", "")),
              "why": why, "why_src": str(data.get("why_src", "")), "lane": lane,
              "context": [str(c) for c in data.get("context_points", [])], "context_src": ctx_src,
              "reply_src": str(data.get("reply_src", "")),
              "reply_out": str(data.get("reply_out", "")),
              "reply_guide": str(data.get("reply_guide", "")),
              "reply_guide_src": str(data.get("reply_guide_src", "")),
              "stance": stance, "replies": replies,
              "sources": sources,
              "latency_ms": int((time.time() - t0) * 1000)}
        bus.publish(ev)
        _dbg(typ)
        log.info("intervention %s conf=%.2f latency=%dms", typ, conf, ev["latency_ms"])
        return ev

    NAME_REPEAT_SEC = 12.0
    DIRECT_LANES = {"name", "user_rule"}

    STYLES = {
        "positive": "Positive and cooperative (agree, offer to help, or report progress)",
        "neutral": "Neutral and factual (explain the situation and points to confirm)",
        "negative": "Polite but clear disagreement (decline, explain difficulties, or request a delay)",
    }

    def expand_stances(self, iv_id):
        iv = db.query_one("SELECT * FROM interventions WHERE id=?", (iv_id,))
        if not iv:
            return None
        try:
            replies = json.loads(iv["replies"] or "{}")
        except Exception:
            replies = {}
        missing = [s for s in self.STYLES if s not in replies]
        if not missing:
            return replies
        have = iv["stance"] or next(iter(replies), "neutral")
        cur = replies.get(have, {})
        spec = "; ".join(f'"{s}": {self.STYLES[s]}' for s in missing)
        src = src_name()
        sys_p = (f"You are a meeting communication assistant. The meeting language is {src}. "
                 f"For each of the {len(missing)} remaining attitudes, provide guidance and a spoken example. "
                 f"Attitudes: {spec}. Return only JSON: {{"
                 + ", ".join(f'"{style}": {{"guide": "one or two points in {out_name()}", '
                             f'"src": "spoken example in {src}, at most two sentences"}}' for style in missing) + "}")
        user_p = (f"Question: {iv['question_src'] or iv['question']}\n"
                  f"Existing ({have}) guidance: {cur.get('guide', '')}\nExisting example: {cur.get('src', '')}")
        data = self.llm.chat_json(sys_p, user_p, max_tokens=900, timeout=25.0, role="answer") or {}
        for s in missing:
            d = data.get(s) or {}
            if d.get("src") or d.get("guide"):
                replies[s] = {"guide": str(d.get("guide", "")), "guide_src": str(d.get("guide_src", "")),
                              "src": str(d.get("src", "")), "out": str(d.get("out", ""))}
        db.execute("UPDATE interventions SET replies=? WHERE id=?", (db.to_json(replies), iv_id))
        return replies

    def refine(self, iv_id, style):
        iv = db.query_one("SELECT * FROM interventions WHERE id=?", (iv_id,))
        if not iv or style not in self.STYLES:
            return None
        sys_p = (f"You are a meeting communication assistant. Rewrite the guidance and example "
                 f"using this attitude: {self.STYLES[style]}. Return only JSON: "
                 f'{{"reply_guide": "one or two guidance points in {out_name()}", '
                 '"reply_src": "spoken example in the original language, at most two sentences", '
                 f'"reply_out": "example translated into {out_name()}"}}')
        user_p = (f"Question: {iv['question']}\nCurrent guidance: {iv['reply_guide'] or ''}\nCurrent example: {iv['suggested_answer']}")
        data = self.llm.chat_json(sys_p, user_p, max_tokens=600, role="answer")
        if not data:
            return None
        out = {"reply_guide": str(data.get("reply_guide", "")), "reply_src": str(data.get("reply_src", "")),
               "reply_out": str(data.get("reply_out", ""))}
        db.execute("UPDATE interventions SET reply_guide=?, suggested_answer=?, answer_explain=?, user_action='refined' WHERE id=?",
                   (out["reply_guide"], out["reply_src"], out["reply_out"], iv_id))
        return out

    @staticmethod
    def user_action(iv_id, action):
        db.execute("UPDATE interventions SET user_action=? WHERE id=?", (action, iv_id))
        try:
            row = db.query_one("SELECT facts FROM interventions WHERE id=?", (iv_id,))
            ids = json.loads((row or {}).get("facts") or "[]")
            if ids:
                memory.feedback(ids, {"dismissed": -0.15, "deferred": 0.0}.get(action, 0.2))
        except Exception:
            pass
