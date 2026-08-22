"""Hub-owned RUN evidence for assignment-bound Category research results."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from collections.abc import Collection, Mapping
from typing import Any


CATEGORY_RESEARCH_RUN_SOURCE_ID = "RUN_0001"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OrganizationCategoryRunEvidenceError(ValueError):
    """Raised when a Category result cannot become Hub run evidence."""


def _canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OrganizationCategoryRunEvidenceError(
            "category_run_evidence_not_json"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


class OrganizationCategoryRunEvidenceService:
    """Reserve and materialize the one Hub-authoritative Category run ref.

    The identifier is transported to the Worker before execution, but the
    evidence entry is materialized only after the assignment-bound result
    capability and authoritative dispatch lease have been admitted by the
    Hub pipeline.
    """

    @staticmethod
    def reserved_refs() -> list[str]:
        return [CATEGORY_RESEARCH_RUN_SOURCE_ID]

    def build_catalog(
        self,
        *,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        worker_id: str,
        raw_output: str,
        raw_output_digest: str,
        allowed_run_refs: Collection[str],
        runtime_artifact_hashes: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        bindings = {
            "task_id": str(task_id or "").strip(),
            "assignment_id": str(assignment_id or "").strip(),
            "dispatch_lease_id": str(dispatch_lease_id or "").strip(),
            "worker_id": str(worker_id or "").strip(),
        }
        if any(not value for value in bindings.values()):
            raise OrganizationCategoryRunEvidenceError(
                "category_run_evidence_binding_invalid"
            )

        normalized_refs = {
            str(value or "").strip() for value in allowed_run_refs
        }
        if normalized_refs != {CATEGORY_RESEARCH_RUN_SOURCE_ID}:
            raise OrganizationCategoryRunEvidenceError(
                "category_run_evidence_reservation_invalid"
            )

        output = str(raw_output or "")
        digest = str(raw_output_digest or "").strip().lower().removeprefix(
            "sha256:"
        )
        expected_digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
        if (
            not output
            or _SHA256.fullmatch(digest) is None
            or not hmac.compare_digest(digest, expected_digest)
        ):
            raise OrganizationCategoryRunEvidenceError(
                "category_run_evidence_output_digest_mismatch"
            )

        artifacts = {
            str(key): str(value)
            for key, value in dict(runtime_artifact_hashes or {}).items()
            if str(key) and str(value)
        }
        binding: dict[str, Any] = {
            "schema": "organization_category_run_evidence_binding.v1",
            **bindings,
            "source_id": CATEGORY_RESEARCH_RUN_SOURCE_ID,
            "result_payload_digest": f"sha256:{digest}",
            "runtime_artifact_hashes": dict(sorted(artifacts.items())),
        }
        binding["binding_digest"] = _canonical_digest(binding)
        run_id = "category-run-" + _canonical_digest(binding)[:32]
        empty_digest = hashlib.sha256(b"").hexdigest()
        entry: dict[str, Any] = {
            "source_id": CATEGORY_RESEARCH_RUN_SOURCE_ID,
            "source_type": "tool_run",
            "task_id": bindings["task_id"],
            "run_id": run_id,
            "tool_name": "delegated_category_research",
            "command": "execute_assignment_bound_category_research",
            "exit_code": 0,
            "stdout_hash": digest[:32],
            "stdout_sha256": digest,
            "stderr_hash": empty_digest[:32],
            "artifact_paths": sorted(artifacts),
            "allowed_for_llm_scope": True,
            "evidence_binding": copy.deepcopy(binding),
        }
        entry["evidence_digest"] = _canonical_digest(entry)
        return [entry]


__all__ = [
    "CATEGORY_RESEARCH_RUN_SOURCE_ID",
    "OrganizationCategoryRunEvidenceError",
    "OrganizationCategoryRunEvidenceService",
]
