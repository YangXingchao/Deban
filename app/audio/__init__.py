import sys


def create_source(mic_wanted=None):
    if sys.platform == "darwin":
        from .mac import MacAudioSource
        return MacAudioSource()
    if sys.platform == "win32":
        from .windows import WindowsAudioSource
        return WindowsAudioSource(mic_wanted=mic_wanted)
    raise RuntimeError("Native audio capture is unavailable on this platform")
