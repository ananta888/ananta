from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from agent.services.planning_evidence_resolver_service import (
    AssignmentEvidenceContext,
    PlanningEvidenceResolverService,
)

_ROOT = Path(__file__).resolve().parents[2]
_TODO_SCHEMA_PATH = _ROOT / "todos" / "todo.schema.json"
_QUALITY_SCHEMA_PATH = _ROOT / "schemas" / "planning" / "category_todo_quality_profile.v1.json"
_REQUIRED_ITEM_FIELDS = {
    "id",
    "title",
    "status",
    "priority",
    "risk",
    "type",
    "depends_on",
    "acceptance_criteria",
}


def stable_planning_digest(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def category_schema_hash() -> str:
    return stable_planning_digest(json.loads(_TODO_SCHEMA_PATH.read_text(encoding="utf-8")))


class PlanningCategoryContractService:
    """Validate the research artifact without granting execution authority."""

    def __init__(self, *, evidence_resolver: PlanningEvidenceResolverService | None = None) -> None:
        self._evidence_resolver = evidence_resolver or PlanningEvidenceResolverService()

    def validate_and_recompute(
        self,
        payload: dict[str, Any],
        *,
        evidence_context: AssignmentEvidenceContext | None = None,
        source_catalog: Mapping[str, Any] | None = None,
        tool_run_catalog: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        candidate = json.loads(json.dumps(dict(payload or {})))
        if evidence_context is not None:
            quality = self._quality_profile(candidate)
            if quality:
                # These values are Hub authority, not Worker-authored claims.
                quality.update(
                    {
                        "source_catalog_id": evidence_context.source_catalog_id,
                        "source_catalog_hash": evidence_context.source_catalog_hash,
                        "allowed_source_refs": sorted(
                            evidence_context.allowed_source_refs
                        ),
                        "allowed_run_refs": sorted(
                            evidence_context.allowed_run_refs
                        ),
                    }
                )
                candidate["planning_quality_profile"] = quality
        issues = self._schema_issues(candidate)
        items = self._items(candidate)
        issues.extend(self._semantic_issues(candidate, items))
        issues.extend(self._dag_issues(items))

        quality = self._quality_profile(candidate)
        grounding = self._validate_grounding(
            candidate=candidate,
            quality=quality,
            evidence_context=evidence_context,
            source_catalog=source_catalog,
            tool_run_catalog=tool_run_catalog,
        )
        if grounding["status"] != "verified":
            issues.append(
                {
                    "path": "planning_quality_profile/grounding_status",
                    "reason_code": str(grounding.get("reason") or "category_grounding_unverified"),
                    "human_message": "Category research evidence is not fully verified.",
                }
            )

        candidate["meta"] = self._recompute_meta(candidate, items)
        return {
            "valid": not issues,
            "promotable": not issues and grounding["status"] == "verified",
            "issues": issues,
            "payload": candidate,
            "content_digest": stable_planning_digest(candidate),
            "schema_ref": "todos/todo.schema.json",
            "schema_hash": category_schema_hash(),
            "grounding": grounding,
        }

    @staticmethod
    def _items(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            dict(item)
            for category in list(candidate.get("categories") or [])
            if isinstance(category, Mapping)
            for item in list(category.get("items") or [])
            if isinstance(item, Mapping)
        ]

    @staticmethod
    def _schema_issues(candidate: dict[str, Any]) -> list[dict[str, str]]:
        schema = json.loads(_TODO_SCHEMA_PATH.read_text(encoding="utf-8"))
        issues: list[dict[str, str]] = []
        for error in sorted(Draft202012Validator(schema).iter_errors(candidate), key=lambda row: list(row.path)):
            issues.append(
                {
                    "path": "/".join(map(str, error.path)) or "$",
                    "reason_code": "category_schema_validation_error",
                    "human_message": error.message,
                }
            )
        return issues

    def _semantic_issues(
        self,
        candidate: Mapping[str, Any],
        items: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        quality = self._quality_profile(candidate)
        if not quality:
            issues.append(self._issue("planning_quality_profile", "category_quality_profile_required"))
        elif _QUALITY_SCHEMA_PATH.exists():
            quality_schema = json.loads(_QUALITY_SCHEMA_PATH.read_text(encoding="utf-8"))
            for error in sorted(
                Draft202012Validator(quality_schema).iter_errors(quality),
                key=lambda row: list(row.path),
            ):
                issues.append(
                    {
                        "path": "planning_quality_profile/" + ("/".join(map(str, error.path)) or "$"),
                        "reason_code": "category_quality_profile_invalid",
                        "human_message": error.message,
                    }
                )
        ids: set[str] = set()
        claim_ids = {
            str(claim.get("claim_id") or "")
            for claim in list(self._quality_profile(candidate).get("claims") or [])
            if isinstance(claim, Mapping)
        }
        claims = [
            dict(claim)
            for claim in list(self._quality_profile(candidate).get("claims") or [])
            if isinstance(claim, Mapping)
        ]
        if len(claim_ids) != len(claims):
            issues.append(self._issue("planning_quality_profile/claims", "category_claim_id_duplicate"))
        for index, item in enumerate(items):
            missing = sorted(field for field in _REQUIRED_ITEM_FIELDS if field not in item)
            for field in missing:
                issues.append(self._issue(f"categories/items/{index}/{field}", "category_item_field_required"))
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                issues.append(self._issue(f"categories/items/{index}/id", "category_item_id_required"))
            elif item_id in ids:
                issues.append(self._issue(f"categories/items/{index}/id", "category_item_id_duplicate"))
            ids.add(item_id)
            if not str(item.get("title") or "").strip():
                issues.append(self._issue(f"categories/items/{index}/title", "category_item_title_required"))
            if not list(item.get("acceptance_criteria") or []):
                issues.append(
                    self._issue(f"categories/items/{index}/acceptance_criteria", "category_acceptance_required")
                )
            evidence_claim_refs = list(item.get("evidence_claim_refs") or [])
            if not evidence_claim_refs:
                issues.append(
                    self._issue(
                        f"categories/items/{index}/evidence_claim_refs",
                        "category_item_evidence_required",
                    )
                )
            for claim_ref in evidence_claim_refs:
                if str(claim_ref or "") not in claim_ids:
                    issues.append(
                        self._issue(f"categories/items/{index}/evidence_claim_refs", "category_orphan_claim_ref")
                    )
        return issues

    @staticmethod
    def _dag_issues(items: list[dict[str, Any]]) -> list[dict[str, str]]:
        ids = {str(item.get("id") or "").strip() for item in items}
        incoming: dict[str, int] = {item_id: 0 for item_id in ids if item_id}
        outgoing: dict[str, list[str]] = {item_id: [] for item_id in incoming}
        issues: list[dict[str, str]] = []
        for item in items:
            item_id = str(item.get("id") or "").strip()
            for dependency in list(item.get("depends_on") or []):
                dep = str(dependency or "").strip()
                if dep not in ids:
                    issues.append(
                        PlanningCategoryContractService._issue(
                            "categories/items/depends_on", "category_dependency_unknown"
                        )
                    )
                    continue
                if item_id and item_id in incoming:
                    outgoing[dep].append(item_id)
                    incoming[item_id] += 1
        queue = deque(sorted(item_id for item_id, count in incoming.items() if count == 0))
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for child in sorted(outgoing[current]):
                incoming[child] -= 1
                if incoming[child] == 0:
                    queue.append(child)
        if visited != len(incoming):
            issues.append(
                PlanningCategoryContractService._issue("categories/items/depends_on", "category_dependency_cycle")
            )
        return issues

    def _validate_grounding(
        self,
        *,
        candidate: Mapping[str, Any],
        quality: Mapping[str, Any],
        evidence_context: AssignmentEvidenceContext | None,
        source_catalog: Mapping[str, Any] | None,
        tool_run_catalog: list[Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        if evidence_context is None or source_catalog is None:
            return {"status": "unverified", "reason": "category_evidence_context_required"}
        if str(quality.get("source_catalog_id") or "") != evidence_context.source_catalog_id:
            return {"status": "failed", "reason": "source_catalog_id_mismatch"}
        if str(quality.get("source_catalog_hash") or "") != evidence_context.source_catalog_hash:
            return {"status": "failed", "reason": "source_catalog_hash_mismatch"}
        if set(quality.get("allowed_source_refs") or []) != set(evidence_context.allowed_source_refs):
            return {"status": "failed", "reason": "source_allowlist_mismatch"}
        if set(quality.get("allowed_run_refs") or []) != set(evidence_context.allowed_run_refs):
            return {"status": "failed", "reason": "run_allowlist_mismatch"}
        answer_payload = {
            "schema": "grounded_answer.v1",
            "answer": str(
                quality.get("research_summary")
                or candidate.get("purpose")
                or candidate.get("project")
                or "Research plan"
            ),
            "claims": list(quality.get("claims") or []),
            "unsupported_notes": list(quality.get("unsupported_notes") or []),
        }
        result = self._evidence_resolver.verify_grounded_claims(
            answer_payload=answer_payload,
            source_catalog=source_catalog,
            tool_run_catalog=tool_run_catalog,
            context=evidence_context,
        )
        if result.get("status") == "verified" and int(result.get("unverified_claim_count") or 0) > 0:
            result = {
                **result,
                "status": "unverified",
                "reason": "category_claims_unverified",
            }
        declared = str(quality.get("grounding_status") or "").strip()
        if declared != "verified" and result.get("status") == "verified":
            return {**result, "status": "unverified", "reason": "declared_grounding_not_verified"}
        return result

    @staticmethod
    def _quality_profile(candidate: Mapping[str, Any]) -> dict[str, Any]:
        value = candidate.get("planning_quality_profile")
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _recompute_meta(candidate: Mapping[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        existing = dict(candidate.get("meta") or {})
        statuses = Counter(str(item.get("status") or "open").strip().lower() or "open" for item in items)
        for required in ("completed", "partial", "open"):
            statuses.setdefault(required, 0)
        incoming = {str(item.get("id") or ""): 0 for item in items if str(item.get("id") or "")}
        outgoing = {item_id: [] for item_id in incoming}
        for item in items:
            item_id = str(item.get("id") or "")
            for dep in list(item.get("depends_on") or []):
                dependency = str(dep or "")
                if dependency in outgoing and item_id in incoming:
                    outgoing[dependency].append(item_id)
                    incoming[item_id] += 1
        queue = deque(sorted(item_id for item_id, count in incoming.items() if count == 0))
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for child in sorted(outgoing[current]):
                incoming[child] -= 1
                if incoming[child] == 0:
                    queue.append(child)
        return {
            **existing,
            "total_items": len(items),
            "by_status": dict(sorted(statuses.items())),
            "notes": [str(note) for note in list(existing.get("notes") or [])],
            "recommended_order": order,
        }

    @staticmethod
    def _issue(path: str, reason_code: str) -> dict[str, str]:
        return {
            "path": path,
            "reason_code": reason_code,
            "human_message": reason_code.replace("_", " "),
        }


__all__ = [
    "PlanningCategoryContractService",
    "category_schema_hash",
    "stable_planning_digest",
]
