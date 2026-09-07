"""Private video transport; image endpoint/signature domains are not accepted."""

from agent.services.persona_inspection_transport import HttpPersonaInspectionWorker
from agent.services.private_container_network_policy import pin_private_container_address
from ananta_contracts.persona_inspection_wire import VIDEO_WIRE


class HttpPersonaVideoWorker:
    def __init__(self, endpoint, key):
        self._worker = HttpPersonaInspectionWorker(
            endpoint,
            key,
            wire=VIDEO_WIRE,
            resolve_address=lambda host, port: pin_private_container_address(host, port),
        )
        self.endpoint, self.key = self._worker.endpoint, key

    def execute(self, assignment, content, media_type):
        return self._worker.execute(assignment, content, media_type)
