"""Diagnostics for worker_context_handoff.v3 payloads."""
from __future__ import annotations

from typing import Any


class WorkerContextHandoffDiagnosticsService:
    """Build small, non-secret diagnostics for CWFH handoff payloads."""

    def summarize(self, handoff: dict[str, Any]) -> dict[str, Any]:
        candidate_files = [c for c in list(handoff.get("candidate_files") or []) if isinstance(c, dict)]
        context_files = [c for c in list(handoff.get("context_files") or []) if isinstance(c, dict)]
        required_reads = [str(path) for path in list(handoff.get("required_reads") or []) if str(path or "").strip()]
        read_paths = {str(item.get("path") or "") for item in context_files}
        missing_required_reads = [path for path in required_reads if path not in read_paths]
        return {
            "schema": str(handoff.get("schema") or ""),
            "candidate_file_count": len(candidate_files),
            "context_file_count": len(context_files),
            "required_read_count": len(required_reads),
            "missing_required_reads": missing_required_reads,
            "total_context_bytes": sum(int(item.get("byte_count") or 0) for item in context_files),
            "source_output_kinds": sorted({
                kind
                for item in candidate_files
                for kind in list(item.get("source_output_kinds") or [])
                if str(kind or "").strip()
            }),
            "policy_version": str(handoff.get("policy_version") or ""),
            "manifest_hash": str(handoff.get("manifest_hash") or ""),
        }


worker_context_handoff_diagnostics_service = WorkerContextHandoffDiagnosticsService()


def get_worker_context_handoff_diagnostics_service() -> WorkerContextHandoffDiagnosticsService:
    return worker_context_handoff_diagnostics_service
