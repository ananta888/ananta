"""Local execution slot accounting, without Task identities or queue ownership."""

import threading


class DialogSlots:
    def __init__(self, capacity):
        if type(capacity) is not int or not 1 <= capacity <= 4:
            raise ValueError("meet_dialog_slots_invalid")
        self._capacity = capacity
        self._semaphore = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._active = 0

    def acquire(self, blocking=True, timeout=None):
        acquired = self._semaphore.acquire(blocking, timeout)
        if acquired:
            with self._lock:
                self._active += 1
        return acquired

    def release(self, n=1):
        with self._lock:
            self._semaphore.release(n)
            self._active -= n

    def snapshot(self):
        with self._lock:
            return {"capacity": self._capacity, "active": self._active}
