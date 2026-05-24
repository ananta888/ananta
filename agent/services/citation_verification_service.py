from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_ANSWER_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "answers" / "grounded_answer.v1.json"


class CitationVerificationService:
    """Deterministic claim-to-source verifier for grounded answers."""

    def _validate_answer_shape(self, answer_payload: dict[str, Any]) -> list[str]:
        schema = json.loads(_ANSWER_SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(answer_payload), key=lambda err: list(err.path))
        return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]

    def verify(
        self,
        *,
        task_id: str,
        answer_payload: dict[str, Any],
        source_catalog: dict[str, Any],
        tool_run_catalog: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        schema_errors = self._validate_answer_shape(answer_payload)
        if schema_errors:
            return {
                "status": "failed_schema",
                "reason": "invalid_grounded_answer_schema",
                "schema_errors": schema_errors,
                "verified_claim_count": 0,
                "unverified_claim_count": 0,
                "failed_claims": [],
            }

        source_map = {str(s.get("source_id")): dict(s) for s in list(source_catalog.get("sources") or []) if isinstance(s, dict)}
        run_map = {str(r.get("source_id")): dict(r) for r in list(tool_run_catalog or []) if isinstance(r, dict)}
        all_sources = {**source_map, **run_map}
        failed_claims: list[dict[str, Any]] = []
        verified = 0
        unverified = 0

        for claim in list(answer_payload.get("claims") or []):
            claim_id = str(claim.get("claim_id") or "")
            claim_type = str(claim.get("claim_type") or "")
            refs = [str(x) for x in list(claim.get("citation_refs") or [])]
            confidence = str(claim.get("confidence") or "")
            if confidence == "unverified":
                unverified += 1
                continue
            if not refs:
                failed_claims.append({"claim_id": claim_id, "reason": "failed_missing_citation"})
                continue
            claim_failed = False
            has_run = False
            for ref in refs:
                src = all_sources.get(ref)
                if not src:
                    failed_claims.append({"claim_id": claim_id, "reason": "failed_unknown_source", "source_id": ref})
                    claim_failed = True
                    continue
                if str(src.get("task_id") or task_id) != str(task_id):
                    failed_claims.append({"claim_id": claim_id, "reason": "failed_cross_task_source", "source_id": ref})
                    claim_failed = True
                    continue
                if not bool(src.get("allowed_for_llm_scope", True)):
                    failed_claims.append({"claim_id": claim_id, "reason": "failed_policy_scope", "source_id": ref})
                    claim_failed = True
                    continue
                stype = str(src.get("source_type") or "")
                if stype in {"tool_run", "test_result", "generated_artifact"}:
                    has_run = True
            if claim_type == "tool_result" and not has_run:
                failed_claims.append({"claim_id": claim_id, "reason": "failed_missing_tool_run"})
                claim_failed = True
            if not claim_failed:
                verified += 1

        status = "verified" if not failed_claims else failed_claims[0]["reason"]
        return {
            "status": status,
            "verified_claim_count": verified,
            "unverified_claim_count": unverified,
            "failed_claims": failed_claims,
        }


_SERVICE = CitationVerificationService()


def get_citation_verification_service() -> CitationVerificationService:
    return _SERVICE

