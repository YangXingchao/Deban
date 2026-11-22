import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class NativeAudioTests(unittest.TestCase):
    def test_native_recording_pads_late_microphone(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from app.pipeline import Pipeline

        frames = {"sys": [], "mic": []}
        recorder = SimpleNamespace(feed=lambda channel, samples: frames[channel].append(samples))
        vad = SimpleNamespace(feed=lambda samples: None)
        pipeline = SimpleNamespace(lock=threading.Lock(), meeting_id=1, state="recording",
                                   cur_record=True, cur_mic=True, recorder=recorder,
                                   vads={"sys": vad, "mic": vad}, _native_record_anchor=10,
                                   _native_record_offset=0, _native_record_samples={"sys": 0, "mic": 0})
        with patch('app.pipeline.time.monotonic', return_value=12):
            Pipeline.receive_native_audio(pipeline, 1, 'mic', np.ones(1600, dtype=np.float32))
        self.assertEqual(len(frames['mic'][0]), 30400)
        self.assertFalse(frames['mic'][0].any())
        self.assertTrue(frames['mic'][1].all())

    def test_mac_frames_and_heartbeat(self):
        code = '''
import io, json
from types import SimpleNamespace
import numpy as np
from app.audio.mac import MacAudioSource

def frame(tag, payload=b'', rate=16000):
    return tag.encode() + rate.to_bytes(4, 'little') + len(payload).to_bytes(4, 'little') + payload
source = MacAudioSource()
source.proc = SimpleNamespace(stdout=io.BytesIO(frame('H') + frame('S', np.array([0.25, -0.5], dtype=np.float32).tobytes())))
channel, samples = source.read()
print(json.dumps({'channel': channel, 'samples': samples.tolist(), 'eof': source.read() is None}))
'''
        with tempfile.TemporaryDirectory() as data_dir:
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                                    env={**os.environ, 'MC_DATA_DIR': data_dir},
                                    capture_output=True, text=True, check=True)
        import json
        self.assertEqual(json.loads(result.stdout), {'channel': 'sys', 'samples': [0.25, -0.5], 'eof': True})

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO required')
    def test_mac_waits_for_fifo_writer(self):
        code = """
import json, os, pathlib, tempfile, threading, time
from app.audio.mac import MacAudioSource
fifo = pathlib.Path(os.environ['MC_DATA_DIR']) / 'audio.fifo'
os.mkfifo(fifo)
os.environ['MC_AUDIO_FIFO'] = str(fifo)
def writer():
    time.sleep(.15)
    with open(fifo, 'wb', buffering=0) as f:
        f.write(b'R' + (0).to_bytes(4, 'little') + (0).to_bytes(4, 'little'))
threading.Thread(target=writer, daemon=True).start()
source = MacAudioSource()
source.start()
source.stop()
print(json.dumps({'ok': True}))
"""
        with tempfile.TemporaryDirectory() as data_dir:
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                                    env={**os.environ, 'MC_DATA_DIR': data_dir},
                                    capture_output=True, text=True, check=True, timeout=5)
        import json
        self.assertTrue(json.loads(result.stdout)['ok'])

    def test_desktop_capture_forwards_channels_and_stops(self):
        code = """
import json, queue, threading, time
from unittest.mock import patch
from app.audio.desktop import DesktopCapture
class Source:
    def __init__(self):
        self.blocks = queue.Queue()
        self.closed = threading.Event()
    def start(self): pass
    def read(self): return self.blocks.get(timeout=2)
    def stop(self):
        self.closed.set()
        self.blocks.put(None)
class Pipeline:
    cur_mic = True
    def __init__(self): self.frames = []
    def receive_native_audio(self, mid, channel, samples):
        self.frames.append([mid, channel, samples]); return True
    def end_meeting(self, expected_mid): raise AssertionError('Unexpected capture failure')
source, pipeline = Source(), Pipeline()
with patch('app.audio.desktop.create_source', return_value=source):
    capture = DesktopCapture(pipeline)
    capture.start(42)
    source.blocks.put(('sys', [1, 2]))
    source.blocks.put(('mic', [3, 4]))
    for _ in range(100):
        if len(pipeline.frames) == 2: break
        time.sleep(.01)
    capture.stop(42)
    print(json.dumps({'frames': pipeline.frames, 'closed': source.closed.wait(1)}))
"""
        with tempfile.TemporaryDirectory() as data_dir:
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                                    env={**os.environ, 'MC_DATA_DIR': data_dir},
                                    capture_output=True, text=True, check=True)
        import json
        self.assertEqual(json.loads(result.stdout), {'frames': [[42, 'sys', [1, 2]], [42, 'mic', [3, 4]]], 'closed': True})

    def test_data_directory_cannot_be_opened_by_two_processes(self):
        from app.instance import InstanceLock
        with tempfile.TemporaryDirectory() as data_dir:
            lock = InstanceLock(pathlib.Path(data_dir))
            self.assertTrue(lock.acquire())
            code = "from app.instance import InstanceLock; import pathlib,sys; print(InstanceLock(pathlib.Path(sys.argv[1])).acquire())"
            result = subprocess.run([sys.executable, '-c', code, data_dir], cwd=ROOT,
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), 'False')
            lock.release()
            result = subprocess.run([sys.executable, '-c', code, data_dir], cwd=ROOT,
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), 'True')

    def test_unsupported_platform_is_rejected(self):
        from unittest.mock import patch
        from app.audio import create_source
        with patch('app.audio.sys.platform', 'unsupported'):
            with self.assertRaises(RuntimeError):
                create_source()
