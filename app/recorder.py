import logging
import threading
import wave

import numpy as np

from .audio.base import SAMPLE_RATE
from . import config

log = logging.getLogger("recorder")


DUCK_FRAME = SAMPLE_RATE // 50
DUCK_THRESH = 0.004
DUCK_TAIL_SEC = 0.35
DUCK_GAIN = 0.12

def _duck_mic(sys_a, mic_a):
    if len(mic_a) == 0 or len(sys_a) == 0:
        return mic_a
    f = DUCK_FRAME
    n_f = (max(len(sys_a), len(mic_a)) + f - 1) // f
    s = np.zeros(n_f * f, np.float32); s[:len(sys_a)] = sys_a
    rms = np.sqrt((s.reshape(n_f, f) ** 2).mean(axis=1))
    active = rms > DUCK_THRESH
    tail = int(DUCK_TAIL_SEC * SAMPLE_RATE / f)
    if tail > 0:
        idx = np.flatnonzero(active)
        ext = np.zeros(n_f, bool)
        for k in range(tail + 1):
            j = idx + k
            ext[j[j < n_f]] = True
        active = ext
    gain_f = np.where(active, DUCK_GAIN, 1.0).astype(np.float32)
    k = 5
    gain_f = np.convolve(np.pad(gain_f, (k // 2, k // 2), mode="edge"), np.ones(k, np.float32) / k, mode="valid")
    gain = np.repeat(gain_f, f)[:len(mic_a)]
    return mic_a * gain


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.buf = {"sys": [], "mic": []}
        self.active = False

    def start(self):
        with self.lock:
            self.buf = {"sys": [], "mic": []}
            self.active = True

    def feed(self, channel, samples):
        if not self.active:
            return
        with self.lock:
            self.buf[channel].append(samples)

    def stop_snapshot(self):
        with self.lock:
            self.active = False
            sys_a = np.concatenate(self.buf["sys"]) if self.buf["sys"] else np.zeros(0, np.float32)
            mic_a = np.concatenate(self.buf["mic"]) if self.buf["mic"] else np.zeros(0, np.float32)
            self.buf = {"sys": [], "mic": []}
        return sys_a, mic_a

    def save_segment(self, meeting_id, snap, part=0):
        sys_a, mic_a = snap
        n = max(len(sys_a), len(mic_a))
        if n < SAMPLE_RATE:
            return None
        mix = np.zeros(n, np.float32)
        mix[:len(sys_a)] += sys_a
        mix[:len(mic_a)] += _duck_mic(sys_a, mic_a)
        np.clip(mix, -1, 1, out=mix)
        suffix = "" if part == 0 else f"-p{part}"
        wav = config.DATA_DIR / "audio" / f"meeting-{meeting_id}{suffix}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((mix * 32767).astype(np.int16).tobytes())
        dur = n / SAMPLE_RATE
        return str(wav), dur
