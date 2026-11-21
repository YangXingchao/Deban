import threading
import queue


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._clients = set()
        self._history = []

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            history = list(self._history)
            self._clients.add(q)
        return q, history

    def unsubscribe(self, q):
        with self._lock:
            self._clients.discard(q)

    def publish(self, ev: dict, store=True):
        with self._lock:
            if store:
                ev["id"] = len(self._history)
                self._history.append(ev)
            else:
                ev["id"] = -1
            for q in list(self._clients):
                q.put(ev)

    def clear_history(self):
        with self._lock:
            self._history.clear()


bus = EventBus()
