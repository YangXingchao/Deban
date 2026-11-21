import collections
import datetime
import logging
import time

import numpy as np

from .audio.base import SAMPLE_RATE

log = logging.getLogger("vad")

FRAME = 512
FRAME_SEC = FRAME / SAMPLE_RATE
SILENCE_END = 0.7
MIN_SPEECH = 0.6
MAX_SEGMENT = 10.0
PRE_ROLL_FRAMES = 10

_silero_cls = None


def _load_silero():
    global _silero_cls
    if _silero_cls is None:
        try:
            from silero_vad import load_silero_vad
            _silero_cls = load_silero_vad
        except Exception as e:
            log.warning("silero-vad unavailable, fallback to energy VAD: %s", e)
            _silero_cls = False
    return _silero_cls


class ChannelVAD:
    def __init__(self, channel, on_segment, is_recording, *, silence_end=SILENCE_END, max_segment=MAX_SEGMENT):
        self.channel = channel
        self.on_segment = on_segment
        self.is_recording = is_recording
        self.seg = []
        self.silence = 0
        self.noise_floor = 1e-3
        self.started = None
        self.pending = np.zeros(0, dtype=np.float32)
        self.pre_roll = collections.deque(maxlen=PRE_ROLL_FRAMES)
        self.silence_end = silence_end
        self.max_segment = max_segment
        self.speech_log = collections.deque(maxlen=int(20 / FRAME_SEC))
        loader = _load_silero()
        try:
            self.model = loader() if loader else None
        except Exception as e:
            log.warning("silero-vad model unavailable, fallback to energy VAD: %s", e)
            self.model = None
        if self.model is not None:
            try:
                import torch
                self._torch = torch
            except Exception as e:
                log.warning("torch unavailable, fallback to energy VAD: %s", e)
                self.model = None

    def _is_speech(self, frame):
        if self.model is not None:
            try:
                with self._torch.no_grad():
                    p = self.model(self._torch.from_numpy(frame), SAMPLE_RATE).item()
                return p >= 0.5
            except Exception:
                self.model = None
        rms = float(np.sqrt(np.mean(frame ** 2)))
        threshold = max(self.noise_floor * 4, 0.0015)
        if rms < threshold:
            self.noise_floor = 0.98 * self.noise_floor + 0.02 * max(rms, 1e-5)
            return False
        return True

    def speech_ratio(self, t0, t1, pad=0.3):
        if t1 <= t0:
            return 0.0
        n = sum(1 for t in self.speech_log if t0 - pad <= t <= t1)
        return min(1.0, n * FRAME_SEC / (t1 - t0))

    def flush(self):
        if self.started is not None and (len(self.seg) - self.silence) * FRAME_SEC >= MIN_SPEECH:
            self.on_segment(self.channel, self.started, np.concatenate(self.seg))
        self.seg = []
        self.started = None
        self.silence = 0
        self.pending = np.zeros(0, dtype=np.float32)
        self.pre_roll.clear()
        if self.model is not None:
            self.model.reset_states()

    def feed(self, samples):
        self.pending = np.concatenate([self.pending, samples])
        while len(self.pending) >= FRAME:
            frame, self.pending = self.pending[:FRAME], self.pending[FRAME:]
            if not self.is_recording():
                self.flush()
                return
            speech = self._is_speech(frame)
            if speech:
                self.silence = 0
                if self.started is None:
                    self.started = datetime.datetime.now()
                    self.seg = list(self.pre_roll)
                    self.pre_roll.clear()
            else:
                self.silence += 1
            if self.started is not None:
                self.speech_log.append(time.time())
                self.seg.append(frame)
                dur = len(self.seg) * FRAME_SEC
                sil = self.silence * FRAME_SEC
                if sil >= self.silence_end or dur >= self.max_segment:
                    if dur - sil >= MIN_SPEECH:
                        self.on_segment(self.channel, self.started, np.concatenate(self.seg))
                    self.seg = []; self.started = None; self.silence = 0
            else:
                self.pre_roll.append(frame)
