"""Cooperative owned model lifecycle HTTP; credentials stay outside the model."""

import sys
from copy import deepcopy

from ananta_contracts.meet_companion_task_model import HELPER_INPUT_LIMIT, MAX_RESPONSE_BYTES, validate_response
from worker.meet_media.companion_process_transport import CompanionProcessTransport
from worker.meet_media.companion_task_environment import assigned_model_environment


class AssignedCompanionModelProcess:
    def __init__(self, grants, *, environment=None):
        self.assignment = deepcopy(grants.assignment)
        self.endpoint, self.key = grants.endpoint, grants.key
        self.deadline, self.monotonic = grants.deadline, grants.monotonic
        self.environment = assigned_model_environment(self.assignment, environment=environment)

    def __call__(self, arguments, *, cooperate):
        if (not isinstance(arguments, dict) or set(arguments) != {"action", "payload"}
                or not isinstance(arguments["action"], str) or arguments["action"] not in {"reserve", "finish"}
                or not isinstance(arguments["payload"], dict)):
            raise ValueError("meet_companion_model_port_invalid")
        remaining = min(20, self.deadline - self.monotonic())
        if remaining <= 0:
            raise ValueError("meet_companion_assignment_expired")
        transport = CompanionProcessTransport(
            (sys.executable, "-m", "worker.meet_media.companion_task_model_runtime"),
            environment=self.environment, timeout_seconds=remaining,
            input_limit=HELPER_INPUT_LIMIT, output_limit=MAX_RESPONSE_BYTES,
        )
        try:
            result = transport.exchange({"assignment": self.assignment, "endpoint": self.endpoint,
                                         "key_hex": self.key.hex(), **deepcopy(arguments)}, cooperate=cooperate)
            cooperate()
            return validate_response(result, self.assignment, arguments["action"], arguments["payload"])
        finally:
            transport.close()
