"""Separate opt-in generator listener, reusing bounded signed HTTP infrastructure."""

import os

from ananta_contracts.persona_generation import GenerationWire
from worker.meet_media.contract import load_key
from worker.meet_media.persona_generation_executor import PersonaGenerationExecutor
from worker.meet_media.persona_generator import ProceduralPersonaGenerator
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard


def main():
    key = load_key(os.environ["PERSONA_GENERATION_WORKER_KEY_FILE"])
    endpoint = os.environ["PERSONA_GENERATION_HUB_LEASE_URL"]
    executor = PersonaGenerationExecutor(
        "/state/generation-leases.db",
        guard_factory=lambda assignment: PersonaLeaseGuard(endpoint, key, assignment, kind="generation"),
        generator=ProceduralPersonaGenerator,
    )
    create_inspection_server(("0.0.0.0", 8098), key, executor, wire=GenerationWire()).serve_forever()


if __name__ == "__main__":
    main()
