"""Safe fulfillment for WorkerContextRequest-style file reads."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.context_file_reader_service import ContextFileReaderService, FileReadPolicy


class WorkerContextRequestService:
    """Fulfill read-only context requests through the hub policy layer."""

    def fulfill(
        self,
        requests: list[dict[str, Any]],
        *,
        workspace_root: str | Path,
    ) -> dict[str, Any]:
        reader = ContextFileReaderService(policy=FileReadPolicy(workspace_root=workspace_root))
        context_files: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for request in list(requests or []):
            if not isinstance(request, dict):
                continue
            action = str(request.get("action") or request.get("type") or "read_file").strip()
            path = str(request.get("path") or "").strip()
            if action != "read_file":
                errors.append({"path": path, "error": f"unsupported_action:{action}"})
                continue
            if not path:
                errors.append({"path": "", "error": "path_required"})
                continue
            try:
                result = reader.read_file(path)
            except ValueError as exc:
                errors.append({"path": path, "error": str(exc)})
                continue
            if result.error:
                errors.append({"path": path, "error": result.error})
                continue
            context_files.append(result.as_context_file_dict())
        return {
            "schema": "worker_context_request_result.v1",
            "context_files": context_files,
            "errors": errors,
        }


worker_context_request_service = WorkerContextRequestService()


def get_worker_context_request_service() -> WorkerContextRequestService:
    return worker_context_request_service
