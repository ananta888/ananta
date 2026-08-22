from __future__ import annotations

from worker.hrm_experiments.heartbeat import HrmCapabilityHeartbeat


class _Publisher:
    def __init__(self) -> None:
        self.values: list[dict] = []

    def advertise_capability(self, capability) -> None:
        self.values.append(dict(capability))


def test_heartbeat_refreshes_only_the_capability_projection() -> None:
    publisher = _Publisher()
    heartbeat = HrmCapabilityHeartbeat(
        publisher, {"capability_digest": "sha256:test"}, interval_seconds=90
    )

    heartbeat.refresh()

    assert publisher.values == [{"capability_digest": "sha256:test"}]
    assert heartbeat.last_reason_code is None
