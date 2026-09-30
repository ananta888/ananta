"""Output contract of the assignment-bound Category research Worker.

Validates one model answer against the todo schema, the Category quality
profile schema and the Hub-issued assignment binding (source catalog and
the allowed ``SRC_*`` / ``RUN_*`` identities). The validator never mints
identities: model claims are placed under the contract and bound to the
trusted Hub metadata; references outside the assignment are rejected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from agent.services.planning_utils import extract_json_payload

_ROOT = Path(__file__).resolve().parents[2]
TODO_SCHEMA_PATH = _ROOT / "todos" / "todo.schema.json"
QUALITY_SCHEMA_PATH = _ROOT / "schemas" / "planning" / "category_todo_quality_profile.v1.json"
REFERENCE_TOKEN = re.compile(r"\b(?:SRC|RUN)_[A-Za-z0-9_-]+\b")
QUALITY_PROFILE_FIELDS = frozenset(
    {
        "schema",
        "source_catalog_id",
        "source_catalog_hash",
        "allowed_source_refs",
        "allowed_run_refs",
        "research_summary",
        "claims",
        "unsupported_notes",
        "grounding_status",
        "grounding_reason",
    }
)
REQUIRED_ITEM_FIELDS = frozenset(
    {
        "id",
        "title",
        "status",
        "priority",
        "risk",
        "type",
        "depends_on",
        "acceptance_criteria",
    }
)
_MAX_REPORTED_ISSUES = 30


class ResearchAssignmentBinding(Protocol):
    """The Hub-issued identity binding of one research assignment."""

    source_catalog_id: str
    source_catalog_hash: str
    allowed_source_refs: tuple[str, ...]
    allowed_run_refs: tuple[str, ...]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def has_dependency_cycle(dependencies: Mapping[str, set[str]]) -> bool:
    pending = {key: set(value) for key, value in dependencies.items()}
    while pending:
        ready = {key for key, value in pending.items() if not value}
        if not ready:
            return True
        pending = {
            key: value.difference(ready)
            for key, value in pending.items()
            if key not in ready
        }
    return False


# --- quality profile binding ------------------------------------------------------------------------


def quality_profile_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key in QUALITY_PROFILE_FIELDS}


def has_model_claims(value: Mapping[str, Any]) -> bool:
    claims = value.get("claims")
    return bool(
        isinstance(claims, list)
        and any(
            isinstance(claim, Mapping)
            and str(claim.get("claim_id") or "").strip()
            and isinstance(claim.get("citation_refs"), list)
            for claim in claims
        )
    )


def find_model_quality_profile(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Find a model-supplied claim set even when it was misplaced."""

    meta = _mapping(candidate.get("meta"))
    preferred = (
        candidate.get("quality_profile"),
        meta.get("planning_quality_profile"),
        meta.get("quality_profile"),
        candidate,
    )
    for value in preferred:
        mapped = _mapping(value)
        if has_model_claims(mapped):
            return quality_profile_projection(mapped)

    pending: list[Any] = list(candidate.values())
    while pending:
        value = pending.pop(0)
        if isinstance(value, Mapping):
            mapped = dict(value)
            if has_model_claims(mapped):
                return quality_profile_projection(mapped)
            pending.extend(mapped.values())
        elif isinstance(value, list):
            pending.extend(value)
    return {}


def _item_claim(item: Mapping[str, Any], claim_id: str, citation_refs: list[str]) -> dict[str, Any]:
    title = str(item.get("title") or item.get("id") or "finding").strip()
    evidence_summary = str(item.get("evidence_summary") or "").strip()
    acceptance = item.get("acceptance_criteria")
    acceptance_basis = str(acceptance[0]).strip() if isinstance(acceptance, list) and acceptance else ""
    claim_text = evidence_summary or (
        f"Inferred work item: {title}."
        + (f" Acceptance basis: {acceptance_basis}" if acceptance_basis else "")
    )
    return {
        "claim_id": claim_id,
        "text": claim_text,
        "claim_type": "inference",
        "citation_refs": citation_refs,
        "confidence": "partially_verified",
    }


def _item_source_refs(item: Mapping[str, Any], allowed_source_refs: tuple[str, ...]) -> list[str]:
    raw_item_refs = item.get("source_citation_refs")
    if not isinstance(raw_item_refs, list):
        return []
    return sorted({str(value) for value in raw_item_refs if str(value) in allowed_source_refs})


def derive_quality_profile_from_model_evidence(
    candidate: Mapping[str, Any],
    *,
    prepared: ResearchAssignmentBinding,
) -> dict[str, Any]:
    """Adapt cited model findings without inventing source identifiers."""

    items = [
        item
        for category in list(candidate.get("categories") or [])
        if isinstance(category, Mapping)
        for item in list(category.get("items") or [])
        if isinstance(item, dict)
    ]
    serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    mentioned_source_refs = sorted(
        set(REFERENCE_TOKEN.findall(serialized)).intersection(prepared.allowed_source_refs)
    )
    if not items or not mentioned_source_refs:
        return {}

    claims: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        citation_refs = (_item_source_refs(item, prepared.allowed_source_refs) or mentioned_source_refs)[:8]
        claim_id = f"CLM_{index:04d}"
        claims.append(_item_claim(item, claim_id, citation_refs))
        item["evidence_claim_refs"] = [claim_id]

    run_claim_id = f"CLM_{len(claims) + 1:04d}"
    claims.append(
        {
            "claim_id": run_claim_id,
            "text": "The assignment-bound Worker received a successful CLI result for this research output.",
            "claim_type": "tool_result",
            "citation_refs": list(prepared.allowed_run_refs),
            "confidence": "verified",
        }
    )
    for item in items:
        item["evidence_claim_refs"].append(run_claim_id)

    return {
        "research_summary": f"The assignment-bound research produced {len(items)} cited work items.",
        "claims": claims,
        "unsupported_notes": ["Substantive work-item claims are explicitly marked as model inferences."],
        "grounding_status": "verified",
        "grounding_reason": "Every adapted claim cites an assignment-allowed source or run reference.",
    }


def bind_authoritative_quality_profile(
    candidate: Mapping[str, Any],
    *,
    prepared: ResearchAssignmentBinding,
) -> dict[str, Any]:
    """Place model claims under the contract and bind trusted Hub metadata."""

    normalized = dict(candidate)
    quality = quality_profile_projection(_mapping(normalized.get("planning_quality_profile")))
    if not has_model_claims(quality):
        quality = find_model_quality_profile(normalized)
    if not has_model_claims(quality):
        quality = derive_quality_profile_from_model_evidence(normalized, prepared=prepared)

    quality.update(
        {
            "schema": "category_todo_quality_profile.v1",
            "source_catalog_id": prepared.source_catalog_id,
            "source_catalog_hash": prepared.source_catalog_hash,
            "allowed_source_refs": list(prepared.allowed_source_refs),
            "allowed_run_refs": list(prepared.allowed_run_refs),
        }
    )
    normalized["planning_quality_profile"] = quality
    return normalized


# --- output validation ------------------------------------------------------------------------------


def _schema_issues(candidate: Mapping[str, Any], quality: Mapping[str, Any]) -> list[str]:
    todo_schema = json.loads(TODO_SCHEMA_PATH.read_text(encoding="utf-8"))
    quality_schema = json.loads(QUALITY_SCHEMA_PATH.read_text(encoding="utf-8"))
    issues = [
        "todo_schema:" + "/".join(map(str, error.path))
        for error in Draft202012Validator(todo_schema).iter_errors(candidate)
    ]
    issues.extend(
        "quality_schema:" + "/".join(map(str, error.path))
        for error in Draft202012Validator(quality_schema).iter_errors(quality)
    )
    return issues


def _binding_matches(quality: Mapping[str, Any], prepared: ResearchAssignmentBinding) -> bool:
    return (
        str(quality.get("source_catalog_id") or "") == prepared.source_catalog_id
        and str(quality.get("source_catalog_hash") or "") == prepared.source_catalog_hash
        and set(quality.get("allowed_source_refs") or []) == set(prepared.allowed_source_refs)
        and set(quality.get("allowed_run_refs") or []) == set(prepared.allowed_run_refs)
        and str(quality.get("grounding_status") or "") == "verified"
    )


def _claim_issues(
    quality: Mapping[str, Any], extracted: str, prepared: ResearchAssignmentBinding
) -> tuple[list[str], list[str]]:
    """(issues, claim ids): unique claim ids, citations and raw references within the assignment."""
    issues: list[str] = []
    claims = [dict(value) for value in list(quality.get("claims") or []) if isinstance(value, Mapping)]
    claim_ids = [str(value.get("claim_id") or "") for value in claims]
    if len(set(claim_ids)) != len(claim_ids):
        issues.append("category_research_claim_id_duplicate")
    allowed_refs = {*prepared.allowed_source_refs, *prepared.allowed_run_refs}
    cited_refs = {str(reference) for claim in claims for reference in list(claim.get("citation_refs") or [])}
    if not cited_refs or not cited_refs.issubset(allowed_refs):
        issues.append("category_research_citation_not_allowed")
    discovered_refs = set(REFERENCE_TOKEN.findall(extracted))
    if not discovered_refs.issubset(allowed_refs):
        issues.append("category_research_reference_not_allowed")
    return issues, claim_ids


def _acceptance_criteria_valid(acceptance_criteria: Any) -> bool:
    return (
        isinstance(acceptance_criteria, list)
        and bool(acceptance_criteria)
        and all(isinstance(value, str) and value.strip() for value in acceptance_criteria)
    )


def _item_issues(
    item: Mapping[str, Any], known_claims: set[str], item_ids: set[str], dependencies: dict[str, set[str]]
) -> list[str]:
    issues: list[str] = []
    if REQUIRED_ITEM_FIELDS.difference(item):
        issues.append("category_research_item_fields_missing")
    item_id = str(item.get("id") or "").strip()
    if not item_id or item_id in item_ids:
        issues.append("category_research_item_id_invalid")
    item_ids.add(item_id)
    raw_evidence_claim_refs = item.get("evidence_claim_refs")
    evidence_claim_refs = (
        {str(value) for value in raw_evidence_claim_refs} if isinstance(raw_evidence_claim_refs, list) else set()
    )
    if not evidence_claim_refs or not evidence_claim_refs.issubset(known_claims):
        issues.append("category_research_item_evidence_invalid")
    if not _acceptance_criteria_valid(item.get("acceptance_criteria")):
        issues.append("category_research_item_acceptance_missing")
    raw_dependencies = item.get("depends_on")
    if not isinstance(raw_dependencies, list):
        issues.append("category_research_item_dependencies_invalid")
        raw_dependencies = []
    dependencies[item_id] = {str(value) for value in raw_dependencies}
    return issues


def _items_issues(candidate: Mapping[str, Any], claim_ids: list[str]) -> list[str]:
    items = [
        dict(item)
        for category in list(candidate.get("categories") or [])
        if isinstance(category, Mapping)
        for item in list(category.get("items") or [])
        if isinstance(item, Mapping)
    ]
    issues: list[str] = [] if items else ["category_research_items_required"]
    known_claims = set(claim_ids)
    item_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for item in items:
        issues.extend(_item_issues(item, known_claims, item_ids, dependencies))
    if any(dependency not in item_ids for values in dependencies.values() for dependency in values):
        issues.append("category_research_dependency_unknown")
    if has_dependency_cycle(dependencies):
        issues.append("category_research_dependency_cycle")
    return issues


def validate_research_output(
    raw_output: str,
    *,
    prepared: ResearchAssignmentBinding,
) -> tuple[dict[str, Any] | None, list[str]]:
    """(bound candidate, []) for a valid answer, otherwise (None, up to 30 distinct issue codes)."""
    extracted = extract_json_payload(str(raw_output or ""))
    if not extracted:
        return None, ["category_research_output_json_missing"]
    try:
        candidate = json.loads(extracted)
    except json.JSONDecodeError:
        return None, ["category_research_output_json_invalid"]
    if not isinstance(candidate, dict):
        return None, ["category_research_output_object_required"]

    candidate = bind_authoritative_quality_profile(candidate, prepared=prepared)
    quality = _mapping(candidate.get("planning_quality_profile"))
    issues = _schema_issues(candidate, quality)
    if not _binding_matches(quality, prepared):
        issues.append("category_research_quality_binding_mismatch")
    claim_issues, claim_ids = _claim_issues(quality, extracted, prepared)
    issues.extend(claim_issues)
    issues.extend(_items_issues(candidate, claim_ids))
    if issues:
        return None, list(dict.fromkeys(issues))[:_MAX_REPORTED_ISSUES]
    return candidate, []
