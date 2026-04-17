from __future__ import annotations

from typing import Any

from agent.services.evolution import (
    EvolutionProposal,
    EvolutionResult,
    ValidationResult,
)

_TYPE_MAP = {
    "gene": "improvement",
    "capsule": "repair",
    "gep_prompt": "prompt",
    "prompt": "prompt",
    "policy": "policy",
    "repair": "repair",
    "improvement": "improvement",
}


def map_evolver_result(raw: dict[str, Any], *, provider_name: str = "evolver") -> EvolutionResult:
    proposals = [_map_proposal(item) for item in _proposal_items(raw)]
    validations = [_map_validation(item) for item in _validation_items(raw)]
    return EvolutionResult(
        provider_name=provider_name,
        status=str(raw.get("status") or "completed"),
        summary=str(raw.get("summary") or raw.get("message") or ""),
        proposals=proposals,
        validation_results=validations,
        provider_metadata={
            "evolver_run_id": raw.get("run_id") or raw.get("id"),
            "source": "evolver",
        },
        raw_payload=raw,
    )


def _proposal_items(raw: dict[str, Any]) -> list[Any]:
    for key in ("proposals", "improvements", "candidates", "events"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return []


def _validation_items(raw: dict[str, Any]) -> list[Any]:
    value = raw.get("validation_results") or raw.get("validations") or []
    return value if isinstance(value, list) else []


def _map_proposal(item: Any) -> EvolutionProposal:
    if not isinstance(item, dict):
        return EvolutionProposal(title="Evolver proposal", description=str(item), raw_payload={"value": item})

    evolver_kind = str(item.get("kind") or item.get("type") or item.get("proposal_type") or "improvement")
    proposal_type = _TYPE_MAP.get(evolver_kind, "improvement")
    title = str(item.get("title") or item.get("name") or f"Evolver {proposal_type} proposal")
    description = str(item.get("description") or item.get("summary") or item.get("content") or title)
    confidence = item.get("confidence")
    return EvolutionProposal(
        proposal_id=str(item.get("proposal_id") or item.get("id") or item.get("event_id") or ""),
        title=title,
        description=description,
        proposal_type=proposal_type,
        target_refs=item.get("target_refs") if isinstance(item.get("target_refs"), list) else [],
        rationale=item.get("rationale") or item.get("reason"),
        risk_level=str(item.get("risk_level") or item.get("risk") or "unknown"),
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        requires_review=bool(item.get("requires_review", True)),
        provider_metadata={
            "evolver_id": item.get("id") or item.get("gene_id") or item.get("capsule_id"),
            "evolver_kind": evolver_kind,
        },
        raw_payload=item,
    )


def _map_validation(item: Any) -> ValidationResult:
    if not isinstance(item, dict):
        return ValidationResult(status="unknown", valid=False, reasons=[str(item)], raw_payload={"value": item})
    return ValidationResult(
        proposal_id=item.get("proposal_id"),
        status=str(item.get("status") or "not_run"),
        valid=bool(item.get("valid", False)),
        reasons=item.get("reasons") if isinstance(item.get("reasons"), list) else [],
        checks=item.get("checks") if isinstance(item.get("checks"), list) else [],
        provider_metadata={"source": "evolver"},
        raw_payload=item,
    )
