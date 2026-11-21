import os
import select
import subprocess
import time
import numpy as np
from .base import AudioSource, SAMPLE_RATE
TAGS = {ord('S'): 'sys', ord('M'): 'mic'}
READY = ord('R')
READY_TIMEOUT = 30
STALL_TIMEOUT = 10

class MacAudioSource(AudioSource):

    def __init__(self):
        self.proc = None
        self._fd = None
        self._awaiting_ready = False

    def start(self):
        fifo = os.environ.get('MC_AUDIO_FIFO')
        if fifo:
            self._fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            try:
                self._awaiting_ready = True
                first = self._frame(READY_TIMEOUT)
                self._awaiting_ready = False
                if first is None:
                    raise RuntimeError('native audio capture did not start')
                if first[0] != READY:
                    raise RuntimeError('native audio channel joined mid-stream')
            except Exception:
                self.stop()
                raise
        else:
            raise RuntimeError('Native audio capture channel is unavailable')

    def _read_exact(self, n, timeout=STALL_TIMEOUT):
        buf = b''
        deadline = time.monotonic() + timeout
        while len(buf) < n:
            if self._fd is not None:
                remaining = max(0, deadline - time.monotonic())
                if not select.select([self._fd], [], [], remaining)[0]:
                    raise RuntimeError('native audio capture stalled')
                try:
                    chunk = os.read(self._fd, n - len(buf))
                except BlockingIOError:
                    continue
            else:
                chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                if self._fd is not None and self._awaiting_ready and not buf and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                return None
            buf += chunk
        return buf

    def _frame(self, timeout=STALL_TIMEOUT):
        hdr = self._read_exact(9, timeout)
        if hdr is None:
            return None
        tag = hdr[0]
        rate = int.from_bytes(hdr[1:5], 'little')
        ln = int.from_bytes(hdr[5:9], 'little')
        if ln > 4 * SAMPLE_RATE * 30:
            raise RuntimeError(f'corrupt audio frame (length {ln})')
        payload = self._read_exact(ln) if ln else b''
        if payload is None:
            return None
        return (tag, rate, payload)

    def read(self):
        while True:
            frame = self._frame()
            if frame is None:
                return None
            tag, rate, payload = frame
            if tag == READY:
                raise RuntimeError('native audio capture restarted')
            if tag not in TAGS:
                continue
            samples = np.frombuffer(payload, dtype=np.float32)
            if rate and rate != SAMPLE_RATE and (len(samples) > 1):
                import torch
                import torchaudio.functional as ta
                samples = ta.resample(torch.from_numpy(samples.copy()), rate, SAMPLE_RATE, resampling_method='sinc_interp_kaiser').numpy().astype(np.float32, copy=False)
            return (TAGS[tag], samples)

    def stop(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
