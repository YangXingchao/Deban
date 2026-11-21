import os
import re
import zlib

import numpy as np

from . import config, models

HALLUCINATIONS = {
    "ご視聴ありがとうございました", "ご視聴ありがとうございました。",
    "チャンネル登録お願いします", "チャンネル登録よろしくお願いします",
    "字幕視聴ありがとうございました", "おやすみなさい", "はい。", "ん。",
}
_LEAD = r"^(はい|ええ|うん|あ|あの|えー)?"
GREETING_RE = re.compile(_LEAD + r"(おはよう(ございます)?|こんにちは|こんばんは|はじめまして|おやすみ(なさい)?|"
                         r"good ?(morning|afternoon|evening)|hello|hi)$", re.I)
STOCK_RE = re.compile(
    _LEAD + r"(どうも)?(ありがとう(ございま(す|した))?|お疲れ様?でした|おつかれさまでした|よろしくお願い(致|いた)?します|"
    r"ご視聴ありがとうございました|失礼します|お願いします|thank ?you( ?very ?much)?|thanks( ?for ?watching)?|"
    r"おはよう(ございます)?|こんにちは|こんばんは|はじめまして|おやすみ(なさい)?)$", re.I)
STOCK_NO_SPEECH = 0.3
STOCK_LOGPROB = -0.8


class ASR:
    def __init__(self, glossary_terms=()):
        self.engine = None
        self.last_conf = {}
        self.set_glossary(glossary_terms)

    def set_glossary(self, terms):
        terms = list(dict.fromkeys([*(config.get("my_names") or []), *(terms or [])]))
        self.terms = [t for t in terms if t.strip()]
        self.prompt = ", ".join(self.terms)[:200] if self.terms else None
        self.patterns = []
        for t in self.terms:
            if len(t) >= 4 and re.fullmatch(r"[A-Za-z0-9]+", t):
                p = re.compile(r"[\s\-・]{0,2}".join(re.escape(c) for c in t), re.IGNORECASE)
                self.patterns.append((p, t))

    def warmup(self):
        from faster_whisper import WhisperModel

        source = models.ensure(models.WHISPER)
        self.engine = WhisperModel(str(source), device="cpu", compute_type="int8",
                                   cpu_threads=min(os.cpu_count() or 1, 8))
        self.transcribe(np.zeros(16000, dtype=np.float32), language=config.get("src_lang"))

    def correct(self, text):
        for p, t in self.patterns:
            text = p.sub(t, text)
        return text

    @staticmethod
    def normalize(audio):
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if 0 < rms < 0.03:
            audio = np.clip(audio * min(0.05 / rms, 10.0), -1.0, 1.0)
        return audio

    def transcribe(self, audio, language=None):
        if self.engine is None:
            raise RuntimeError("Transcription model is still being prepared")
        segments, info = self.engine.transcribe(
            self.normalize(audio), language=language, task="transcribe",
            condition_on_previous_text=False, no_speech_threshold=0.5,
            initial_prompt=self.prompt, beam_size=1, without_timestamps=True,
        )
        segments = list(segments)
        text = "".join(segment.text for segment in segments).strip()
        self.last_conf = {
            "no_speech": min((s.no_speech_prob for s in segments), default=0.0),
            "logprob": max((s.avg_logprob for s in segments), default=0.0),
        }
        if not text or text in HALLUCINATIONS or self.last_conf["no_speech"] > 0.6:
            return None, None
        raw = text.encode()
        if len(raw) > 60 and len(zlib.compress(raw)) / len(raw) < 0.25:
            return None, None
        plain = re.sub(r"[\s、。，,.!?！？~ー〜…・]", "", text)
        if STOCK_RE.match(plain) and (
                self.last_conf["no_speech"] > STOCK_NO_SPEECH or self.last_conf["logprob"] < STOCK_LOGPROB):
            return None, None
        return self.correct(text), info.language or language
