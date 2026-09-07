"""Dedicated descriptor worker; no model, GPU, capture or publication credentials."""

import os
from pathlib import Path

from ananta_contracts.persona_inspection_wire import VOICE_WIRE
from worker.meet_media.contract import load_key
from worker.meet_media.persona_inspection_executor import PersonaInspectionExecutor
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard
from worker.meet_media.persona_voice_inspector import PersonaVoiceInspector

if __name__ == "__main__":
    key = load_key(os.environ["PERSONA_VOICE_WORKER_KEY_FILE"])
    endpoint = os.environ["PERSONA_VOICE_HUB_LEASE_URL"]
    Path("/state").mkdir(exist_ok=True)
    executor = PersonaInspectionExecutor(
        "/state/persona-voice-leases.sqlite",
        wire=VOICE_WIRE,
        inspector=PersonaVoiceInspector,
        guard_factory=lambda assignment: PersonaLeaseGuard(endpoint, key, assignment, kind="voice"),
    )
    create_inspection_server(("0.0.0.0", 8097), key, executor, wire=VOICE_WIRE).serve_forever()
