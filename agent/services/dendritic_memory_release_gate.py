"""Release and runtime receipts that fail closed on missing evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.dendritic_memory import canonical_digest


class DendriticMemoryReleaseGate:
    def evaluate(
        self,
        *,
        p0_complete: bool,
        ci_green: bool,
        seed_count: int,
        task_family_count: int,
        critical_security_findings: int,
        rollback_verified: bool,
        revoke_verified: bool,
        deletion_verified: bool,
        allowed_source_refs: Sequence[str],
        allowed_run_refs: Sequence[str],
        requested_source_refs: Sequence[str],
        requested_run_refs: Sequence[str],
    ) -> dict[str, Any]:
        allowed_sources = set(allowed_source_refs)
        allowed_runs = set(allowed_run_refs)
        sources_valid = (
            bool(requested_source_refs)
            and all(str(value).startswith("SRC_") for value in requested_source_refs)
            and set(requested_source_refs) <= allowed_sources
        )
        runs_valid = (
            bool(requested_run_refs)
            and all(str(value).startswith("RUN_") for value in requested_run_refs)
            and set(requested_run_refs) <= allowed_runs
        )
        reasons: list[str] = []
        checks: Mapping[str, bool] = {
            "p0_complete": p0_complete,
            "ci_green": ci_green,
            "seeds_sufficient": seed_count >= 3,
            "task_families_sufficient": task_family_count >= 2,
            "security_clear": critical_security_findings == 0,
            "rollback_verified": rollback_verified,
            "revoke_verified": revoke_verified,
            "deletion_verified": deletion_verified,
            "source_evidence_valid": sources_valid,
            "run_evidence_valid": runs_valid,
        }
        reasons.extend(f"dendritic_release_{name}_failed" for name, passed in checks.items() if not passed)
        result = {
            "eligible": not reasons,
            "reason_codes": reasons,
            "checks": dict(checks),
            "requested_source_refs": list(requested_source_refs) if sources_valid else [],
            "requested_run_refs": list(requested_run_refs) if runs_valid else [],
            "experimental": True,
            "production_eligible": False,
            "claims_verified": sources_valid and runs_valid,
            "human_intervention_required": False,
        }
        result["receipt_digest"] = canonical_digest(result)
        return result


__all__ = ["DendriticMemoryReleaseGate"]
