"""Pinned private HTTP generation dispatch, closed result and unchanged lease."""

import time
from urllib.parse import urlsplit

from agent.services.private_container_network_policy import pin_private_container_address
from ananta_contracts.persona_generation import GenerationWire, decode_result, recipe_digest, validate_assignment
from worker.meet_media.persona_http import signed_post


class HttpPersonaGenerationWorker:
    def __init__(self, endpoint, key, *, resolve_address=pin_private_container_address):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username
            or parsed.password
            or parsed.path != GenerationWire.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("persona_generation_worker_endpoint_invalid")
        self.endpoint, self.key, self.resolve_address = parsed, key, resolve_address

    def execute(self, assignment, recipe):
        validate_assignment(assignment, time.time())
        if recipe_digest(recipe) != assignment["source_sha256"]:
            raise ValueError("persona_generation_recipe_mismatch")
        deadline = time.monotonic() + assignment["deadline"] - time.time()
        parsed = self.endpoint
        address = self.resolve_address(parsed.hostname, parsed.port)
        host = f"[{address}]" if ":" in address else address
        result = signed_post(
            f"http://{host}:{parsed.port}{parsed.path}",
            self.key,
            GenerationWire.domain,
            {"assignment": assignment, "recipe": recipe},
            maximum=GenerationWire.result_limit,
            deadline=deadline,
            host=parsed.netloc,
        )
        validate_assignment(assignment, time.time())
        return decode_result(result, assignment, recipe)
