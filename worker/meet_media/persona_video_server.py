"""Dedicated CPU clip worker. No GPU, public ports or publication credentials."""

import os
from pathlib import Path

from ananta_contracts.persona_inspection_wire import VIDEO_WIRE
from worker.meet_media.contract import load_key
from worker.meet_media.persona_inspection_executor import PersonaInspectionExecutor
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard
from worker.meet_media.persona_video_inspector import PersonaVideoInspector

if __name__ == "__main__":
    key = load_key(os.environ["PERSONA_VIDEO_WORKER_KEY_FILE"])
    endpoint = os.environ["PERSONA_VIDEO_HUB_LEASE_URL"]
    Path("/state").mkdir(exist_ok=True)
    executor = PersonaInspectionExecutor(
        "/state/persona-video-leases.sqlite",
        wire=VIDEO_WIRE,
        inspector=PersonaVideoInspector,
        guard_factory=lambda assignment: PersonaLeaseGuard(endpoint, key, assignment, kind="video"),
    )
    create_inspection_server(("0.0.0.0", 8096), key, executor, wire=VIDEO_WIRE).serve_forever()
