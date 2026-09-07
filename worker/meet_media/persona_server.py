"""Dedicated private image worker; compatible startup and signature domain."""

import os
from pathlib import Path

from ananta_contracts.persona_inspection_wire import IMAGE_WIRE
from worker.meet_media.contract import load_key
from worker.meet_media.persona_executor import PersonaImageExecutor
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard


def create_server(address, key, executor):
    return create_inspection_server(address, key, executor, wire=IMAGE_WIRE)


if __name__ == "__main__":
    key = load_key(os.environ["PERSONA_IMAGE_WORKER_KEY_FILE"])
    endpoint = os.environ["PERSONA_IMAGE_HUB_LEASE_URL"]
    Path("/state").mkdir(exist_ok=True)
    executor = PersonaImageExecutor(
        "/state/persona-image-leases.sqlite",
        guard_factory=lambda assignment: PersonaLeaseGuard(endpoint, key, assignment),
    )
    create_server(("0.0.0.0", 8095), key, executor).serve_forever()
