import logging
import threading

from . import create_source
from ..events import bus

log = logging.getLogger("audio.desktop")


class DesktopCapture:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self._lock = threading.Lock()
        self._active = None

    def start(self, meeting_id):
        source = create_source(mic_wanted=lambda: self.pipeline.cur_mic)
        stop = threading.Event()
        with self._lock:
            if self._active is not None:
                raise RuntimeError("Native audio capture is already active")
            self._active = (meeting_id, source, stop)
        thread = threading.Thread(target=self._run, args=(meeting_id, source, stop),
                                  name="desktop-audio", daemon=True)
        thread.start()

    def stop(self, meeting_id=None):
        with self._lock:
            active = self._active
            if active is None or (meeting_id is not None and active[0] != meeting_id):
                return
            self._active = None
        active[2].set()
        try:
            active[1].stop()
        except Exception:
            log.exception("Unable to stop native audio capture")

    def _run(self, meeting_id, source, stop):
        try:
            if stop.is_set():
                return
            source.start()
            while not stop.is_set():
                block = source.read()
                if block is None:
                    raise RuntimeError("Native audio capture ended")
                channel, samples = block
                if not self.pipeline.receive_native_audio(meeting_id, channel, samples):
                    return
        except Exception:
            if not stop.is_set():
                log.exception("Native audio capture failed")
                if self.pipeline.end_meeting(expected_mid=meeting_id):
                    bus.publish({"type": "notice", "text": "audio_lost"}, store=False)
        finally:
            self.stop(meeting_id)
            try:
                source.stop()
            except Exception:
                log.exception("Unable to stop native audio capture")
