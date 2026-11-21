SAMPLE_RATE = 16000


class AudioSource:
    def start(self):
        raise NotImplementedError

    def read(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError
