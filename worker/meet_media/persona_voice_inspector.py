"""Bounded delegated metadata inspection; no synthesis, downloads or identity issuance."""

import time

from ananta_contracts.persona_voice import inspect_voice_descriptor


class PersonaVoiceInspector:
    def __init__(self, *, require_current, deadline_monotonic):
        self.require_current, self.deadline = require_current, deadline_monotonic

    def _checkpoint(self):
        if time.monotonic() >= self.deadline:
            raise ValueError("persona_voice_inspection_expired")
        self.require_current()
        if time.monotonic() >= self.deadline:
            raise ValueError("persona_voice_inspection_expired")

    def inspect(self, content, media_type):
        self._checkpoint()
        result = inspect_voice_descriptor(content, media_type)
        self._checkpoint()
        return result
