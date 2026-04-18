from __future__ import annotations

from typing import Any

from agent.services.evolution import (
    EvolutionProposal,
    EvolutionResult,
    ValidationResult,
)

_PROPOSAL_SOURCE_FIELDS = ("proposals", "improvements", "candidates", "events")
_VALIDATION_SOURCE_FIELDS = ("validation_results", "validations")

_TYPE_MAP = {
    "gene": "improvement",
    "capsule": "repair",
    "gep_prompt": "prompt",
    "prompt": "prompt",
    "policy": "policy",
    "repair": "repair",
    "improvement": "improvement",
}

_RISK_MAP = {
    "none": "low",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "moderate": "medium",
    "high": "high",
    "critical": "critical",
}

_STATUS_MAP = {
    "ok": "completed",
    "success": "completed",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "failed": "failed",
    "error": "failed",
    "partial": "partial",
    "running": "running",
    "pending": "pending",
    "not_run": "not_run",
    "passed": "passed",
    "valid": "passed",
    "rejected": "failed",
}


class EvolverResponseSchemaError(ValueError):
    pass


def map_evolver_result(raw: dict[str, Any], *, provider_name: str = "evolver") -> EvolutionResult:
    validate_evolver_response(raw)
    proposal_source, proposal_items = _proposal_items(raw)
    proposals = [_map_proposal(item, source_field=proposal_source) for item in proposal_items]
    validations = [_map_validation(item) for item in _validation_items(raw)]
    return EvolutionResult(
        provider_name=provider_name,
        status=_map_status(raw.get("status"), default="completed"),
        summary=str(raw.get("summary") or raw.get("message") or ""),
        proposals=proposals,
        validation_results=validations,
        provider_metadata={
            "evolver_run_id": raw.get("run_id") or raw.get("id"),
            "source": "evolver",
        },
        raw_payload=raw,
    )


def validate_evolver_response(raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict):
        raise EvolverResponseSchemaError("evolver_response_must_be_object")

    for key in _PROPOSAL_SOURCE_FIELDS:
        if key in raw and raw[key] is not None and not isinstance(raw[key], list):
            raise EvolverResponseSchemaError(f"evolver_response_field_must_be_list:{key}")

    proposal_sources = [key for key in _PROPOSAL_SOURCE_FIELDS if isinstance(raw.get(key), list)]
    if len(proposal_sources) > 1:
        joined = ",".join(proposal_sources)
        raise EvolverResponseSchemaError(f"evolver_response_ambiguous_proposal_sources:{joined}")

    for key in _VALIDATION_SOURCE_FIELDS:
        if key in raw and raw[key] is not None and not isinstance(raw[key], list):
            raise EvolverResponseSchemaError(f"evolver_response_field_must_be_list:{key}")

    validation_sources = [key for key in _VALIDATION_SOURCE_FIELDS if isinstance(raw.get(key), list)]
    if len(validation_sources) > 1:
        joined = ",".join(validation_sources)
        raise EvolverResponseSchemaError(f"evolver_response_ambiguous_validation_sources:{joined}")

    if "status" in raw and raw["status"] is not None and not isinstance(raw["status"], str):
        raise EvolverResponseSchemaError("evolver_response_status_must_be_string")

    if "summary" in raw and raw["summary"] is not None and not isinstance(raw["summary"], str):
        raise EvolverResponseSchemaError("evolver_response_summary_must_be_string")


def _proposal_items(raw: dict[str, Any]) -> tuple[str | None, list[Any]]:
    for key in _PROPOSAL_SOURCE_FIELDS:
        value = raw.get(key)
        if isinstance(value, list):
            return key, value
    return None, []


def _validation_items(raw: dict[str, Any]) -> list[Any]:
    value = raw.get("validation_results") or raw.get("validations") or []
    return value if isinstance(value, list) else []


def _map_proposal(item: Any, *, source_field: str | None) -> EvolutionProposal:
    if not isinstance(item, dict):
        return EvolutionProposal(
            title="Evolver proposal",
            description=str(item),
            provider_metadata={"source": "evolver", "evolver_source_field": source_field},
            raw_payload={"value": item},
        )

    evolver_kind = _normalized_text(item.get("kind") or item.get("type") or item.get("proposal_type"), "improvement")
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
        risk_level=_map_risk(item.get("risk_level") or item.get("risk")),
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        requires_review=bool(item.get("requires_review", True)),
        provider_metadata={
            "evolver_id": item.get("id") or item.get("gene_id") or item.get("capsule_id"),
            "evolver_kind": evolver_kind,
            "evolver_source_field": source_field,
        },
        raw_payload=item,
    )


def _map_validation(item: Any) -> ValidationResult:
    if not isinstance(item, dict):
        return ValidationResult(status="unknown", valid=False, reasons=[str(item)], raw_payload={"value": item})
    return ValidationResult(
        proposal_id=item.get("proposal_id"),
        status=_map_status(item.get("status"), default="not_run"),
        valid=bool(item.get("valid", False)),
        reasons=item.get("reasons") if isinstance(item.get("reasons"), list) else [],
        checks=item.get("checks") if isinstance(item.get("checks"), list) else [],
        provider_metadata={"source": "evolver"},
        raw_payload=item,
    )


def _map_risk(value: Any) -> str:
    text = _normalized_text(value, "unknown")
    return _RISK_MAP.get(text, "unknown")


def _map_status(value: Any, *, default: str) -> str:
    text = _normalized_text(value, default)
    return _STATUS_MAP.get(text, text)


def _normalized_text(value: Any, default: str) -> str:
    text = str(value or default).strip().lower()
    return text or default
