"""Pinned private voice inspection transport; no image/video signature fallback."""

from agent.services.persona_inspection_transport import HttpPersonaInspectionWorker
from agent.services.private_container_network_policy import pin_private_container_address
from ananta_contracts.persona_inspection_wire import VOICE_WIRE


def create_voice_worker_transport(endpoint, key):
    return HttpPersonaInspectionWorker(
        endpoint,
        key,
        wire=VOICE_WIRE,
        resolve_address=lambda host, port: pin_private_container_address(host, port),
    )
