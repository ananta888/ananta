"""Compatible exact-image erasure adapter over descriptor-confined storage."""

# Preserve the existing diagnostic/test monkeypatch seam; both adapters use
# the same stdlib module, never a substituted pathname or filesystem backend.
import os as os

from agent.services.persona_file_erasure_store import PersonaFileErasureStore


class PersonaImageErasureStore:
    def __init__(self, base_dir):
        self._store = PersonaFileErasureStore(base_dir, profile="image")
        self.base_dir = self._store.base_dir

    def erase(self, reference, expected_size, *, checkpoint):
        return self._store.erase(reference, expected_size, checkpoint=checkpoint)
