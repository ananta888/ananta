"""Private, atomic Hub journal and cross-process project lock."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


class Journal:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / 'journal.json'

    @contextmanager
    def locked(self, *, blocking=True):
        with (self.root / 'project.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'items': {}}

    def write(self, state):
        temporary = self.root / 'journal.tmp'
        with temporary.open('w') as stream:
            os.chmod(temporary, 0o600)
            json.dump(state, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
