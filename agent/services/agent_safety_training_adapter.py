"""Provider-neutral durable training outbox owned by the Hub."""

from __future__ import annotations

import uuid
from typing import Any

from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from ananta_contracts.agent_safety import canonical_digest, utc_now


class HubQueuedSafetyTrainingAdapter:
    """Queues bounded records for a separately delegated training worker.

    The adapter grants no model authority and performs no training inside the
    Hub. A deployment may consume the durable outbox with any compatible
    provider Worker; an absent consumer leaves the job queued, not interactive.
    """

    def __init__(self, store: AgentSafetyStateStorePort, *, max_records: int = 10_000) -> None:
        self._store = store
        self._max_records = min(max(int(max_records), 1), 10_000)

    def submit(self, *, channel: str, dataset_digest: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        if not records or len(records) > self._max_records:
            raise ValueError("agent_safety_training_record_count_invalid")
        training_id = f"ast_{uuid.uuid4().hex}"
        payload = {
            "training_id": training_id,
            "channel": channel,
            "dataset_digest": dataset_digest,
            "record_count": len(records),
            "records_digest": canonical_digest({"records": records}),
            "state": "queued",
            "provider": "delegated_worker",
            "created_at": utc_now(),
            "human_intervention_required": False,
        }
        self._store.append("training_outbox", training_id, payload, expected_revision=0)
        return payload


__all__ = ["HubQueuedSafetyTrainingAdapter"]
