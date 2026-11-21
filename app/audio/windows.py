import ctypes
import logging
import queue
import threading
import time
import numpy as np
from .base import AudioSource, SAMPLE_RATE
log = logging.getLogger('audio.windows')
BLOCK = 1600
BUFFER_FRAMES = SAMPLE_RATE // 2
CHECK_SEC = 2.0
STALL_SEC = 10.0
MIC_RETRY_SEC = 10.0
_COINIT_MULTITHREADED = 0
_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = 2147549446
_ERROR_INSUFFICIENT_BUFFER = 122
_CONSENT_KEY = 'Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone'

def _ensure_com():
    hr = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    if hr not in (_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE) and hr < 0:
        raise OSError(f'CoInitializeEx failed: 0x{hr & 4294967295:08x}')

def _package_family_name():
    try:
        k32 = ctypes.windll.kernel32
        n = ctypes.c_uint32(0)
        if k32.GetCurrentPackageFamilyName(ctypes.byref(n), None) != _ERROR_INSUFFICIENT_BUFFER:
            return None
        buf = ctypes.create_unicode_buffer(n.value)
        if k32.GetCurrentPackageFamilyName(ctypes.byref(n), buf) != 0:
            return None
        return buf.value
    except Exception:
        return None

def mic_access_denied():
    import winreg
    pfn = _package_family_name()
    keys = [(winreg.HKEY_LOCAL_MACHINE, _CONSENT_KEY)]
    if pfn:
        keys += [(winreg.HKEY_CURRENT_USER, _CONSENT_KEY), (winreg.HKEY_CURRENT_USER, f'{_CONSENT_KEY}\\{pfn}')]
    else:
        keys += [(winreg.HKEY_CURRENT_USER, f'{_CONSENT_KEY}\\NonPackaged')]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                if str(winreg.QueryValueEx(k, 'Value')[0]).lower() == 'deny':
                    return True
        except OSError:
            pass
    return False

class _Stream:

    def __init__(self, channel, device_id):
        self.channel = channel
        self.device_id = device_id
        self.stop = threading.Event()
        self.thread = None
        self.delivered = False
        self.last = time.monotonic()
        self.error = None

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

class WindowsAudioSource(AudioSource):

    def __init__(self, mic_wanted=None):
        self.q = queue.Queue(maxsize=200)
        self.running = False
        self.streams = {}
        self.mic_wanted = mic_wanted
        self._next_check = 0.0
        self._mic_retry_at = 0.0
        self._mic_warned = False

    def _pump(self, stream, device):
        try:
            _ensure_com()
            with device.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BUFFER_FRAMES) as rec:
                while self.running and (not stream.stop.is_set()):
                    data = rec.record(numframes=BLOCK)
                    if stream.stop.is_set():
                        break
                    stream.last = time.monotonic()
                    stream.delivered = True
                    mono = data.mean(axis=1).astype(np.float32) if data.ndim > 1 else data.astype(np.float32)
                    try:
                        self.q.put((stream.channel, mono), timeout=1)
                    except queue.Full:
                        pass
        except Exception as e:
            stream.error = e
            if stream.channel == 'sys' or stream.delivered:
                log.exception('Windows %s audio capture stopped', stream.channel)

    def _open(self, channel, device):
        stream = _Stream(channel, device.id)
        stream.thread = threading.Thread(target=self._pump, args=(stream, device), daemon=True, name=f'audio-{channel}')
        stream.thread.start()
        self.streams[channel] = stream

    def _close(self, channel):
        stream = self.streams.pop(channel, None)
        if stream:
            stream.stop.set()

    def _open_loopback(self, sc, speaker):
        self._open('sys', sc.get_microphone(speaker.id, include_loopback=True))

    def _wants_mic(self):
        if self.mic_wanted is not None:
            return bool(self.mic_wanted())
        from .. import config
        return bool(config.get('mic_enabled'))

    def start(self):
        import soundcard as sc
        _ensure_com()
        self.streams = {}
        self.running = True
        try:
            self._open_loopback(sc, sc.default_speaker())
        except Exception as e:
            self.running = False
            raise RuntimeError(f'Windows system audio is unavailable: {e}') from e
        self._sync_mic(sc, time.monotonic())
        self._next_check = time.monotonic() + CHECK_SEC

    def _sync_mic(self, sc, now):
        try:
            stream = self.streams.get('mic')
            if not self._wants_mic():
                self._close('mic')
                return
            if stream and stream.delivered:
                self._mic_warned = False
            device = sc.default_microphone()
            if stream and stream.alive():
                if stream.device_id == device.id:
                    return
                log.info('Windows default microphone changed; switching')
            elif stream:
                if not stream.delivered and (not self._mic_warned):
                    log.warning('Windows microphone could not be opened; retrying: %s', stream.error)
                    self._mic_warned = True
                if now < self._mic_retry_at:
                    return
            self._close('mic')
            self._open('mic', device)
            self._mic_retry_at = now + MIC_RETRY_SEC
        except Exception as e:
            if not self._mic_warned:
                log.warning('Windows microphone is unavailable; continuing without it: %s', e)
                self._mic_warned = True

    def _check(self):
        import soundcard as sc
        now = time.monotonic()
        loop = self.streams.get('sys')
        if not loop or not loop.alive():
            return False
        if now - loop.last > STALL_SEC:
            log.warning('Windows system audio stalled; restarting capture')
            return False
        try:
            speaker = sc.default_speaker()
        except Exception as e:
            log.warning('Windows default speaker is unavailable: %s', e)
            return False
        if speaker.id != loop.device_id:
            log.info('Windows default speaker changed; switching system audio')
            self._close('sys')
            try:
                self._open_loopback(sc, speaker)
            except Exception as e:
                log.warning('Windows system audio is unavailable: %s', e)
                return False
        self._sync_mic(sc, now)
        return True

    def read(self):
        while self.running:
            if time.monotonic() >= self._next_check:
                self._next_check = time.monotonic() + CHECK_SEC
                if not self._check():
                    return None
            try:
                return self.q.get(timeout=CHECK_SEC)
            except queue.Empty:
                pass
        return None

    def stop(self):
        self.running = False
