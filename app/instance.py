import os
import pathlib
import sys


class InstanceLock:
    def __init__(self, directory: pathlib.Path):
        self.path = directory / 'instance.lock'
        self._file = None

    def acquire(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        stream = os.fdopen(fd, 'r+b', buffering=0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b'0')
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self._file = stream
        return True

    def release(self):
        stream, self._file = self._file, None
        if stream is None:
            return
        try:
            if sys.platform == 'win32':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
