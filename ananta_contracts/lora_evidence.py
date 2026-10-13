"""Execution-only evidence projection for an exact LoRA dispatch attempt."""

from typing import Any

from ananta_contracts.hub_evidence import HubEvidenceAssignmentError, validate_hub_evidence_assignment


def lora_evidence_assignment(value: Any, *, attempt_id: str) -> dict:
    projection = validate_hub_evidence_assignment(value)
    if projection["dispatch_lease_id"] != attempt_id:
        raise HubEvidenceAssignmentError("lora_evidence_dispatch_attempt_mismatch")
    return {**projection, "source_ids": list(projection["source_ids"])}
