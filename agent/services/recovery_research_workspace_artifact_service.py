"""Materialize Recovery research reports as real workspace evidence."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_BYTES,
)

_REPORT_RELATIVE_PATH = "recovery-artifacts/research-report.md"
_ENCODE_CHUNK_CHARACTERS = 64 * 1024


class RecoveryResearchWorkspaceArtifactError(RuntimeError):
    """Raised when a Recovery report cannot become workspace evidence."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class RecoveryResearchWorkspaceArtifactService:
    """Write one bounded report and return a metadata-only workspace claim."""

    def __init__(
        self,
        *,
        workspace_service_provider: Callable[[], Any] | None = None,
        workspace_file_reader_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._workspace_service_provider = workspace_service_provider
        self._workspace_file_reader_provider = (
            workspace_file_reader_provider
        )

    def materialize_claim(
        self,
        *,
        tid: str,
        task: Mapping[str, Any],
        research_artifact: Mapping[str, Any],
    ) -> dict[str, Any]:
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        task_id = str(tid or "").strip()
        if (
            not task_id
            or str(task.get("id") or "").strip() != task_id
            or recovery_task_role(task) != "child"
        ):
            raise RecoveryResearchWorkspaceArtifactError(
                "recovery_research_task_binding_invalid"
            )
        report = research_artifact.get("report_markdown")
        if (
            not isinstance(report, str)
            or not report
            or not any(
                not character.isspace()
                for character in report
            )
        ):
            raise RecoveryResearchWorkspaceArtifactError(
                "recovery_research_report_missing"
            )
        from agent.config import settings

        workspace_service = self._workspace_service()
        workspace_root = workspace_service.resolve_workspace_dir_for_read(
            task=dict(task),
            agent_name=(
                str(settings.agent_name or "").strip()
                or "worker"
            ),
        )
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
            workspace_root = workspace_root.resolve(strict=True)
        except OSError as exc:
            raise RecoveryResearchWorkspaceArtifactError(
                "recovery_research_workspace_unavailable"
            ) from exc

        lock_acquired = False
        acquire_lock = getattr(
            workspace_service,
            "acquire_output_dir_lock",
            None,
        )
        release_lock = getattr(
            workspace_service,
            "release_output_dir_lock",
            None,
        )
        if callable(acquire_lock):
            lock_acquired, reason = acquire_lock(
                task=dict(task),
                workspace_dir=workspace_root,
            )
            if not lock_acquired:
                raise RecoveryResearchWorkspaceArtifactError(
                    str(reason or "recovery_research_workspace_locked")
                )
        try:
            self._write_report(
                workspace_root=workspace_root,
                report=report,
            )
            snapshot = self._workspace_file_reader().read(
                workspace_root=workspace_root,
                relative_path=_REPORT_RELATIVE_PATH,
                maximum_bytes=MAX_RECOVERY_ARTIFACT_BYTES,
            )
        except RecoveryResearchWorkspaceArtifactError:
            raise
        except Exception as exc:
            reason_code = str(
                getattr(exc, "reason_code", "") or ""
            )
            raise RecoveryResearchWorkspaceArtifactError(
                reason_code
                or "recovery_research_workspace_materialization_failed"
            ) from exc
        finally:
            if lock_acquired and callable(release_lock):
                release_lock(
                    task=dict(task),
                    workspace_dir=workspace_root,
                )

        sources = research_artifact.get("sources")
        citations = research_artifact.get("citations")
        source_count = len(sources) if isinstance(sources, list) else 0
        citation_count = (
            len(citations) if isinstance(citations, list) else 0
        )
        return {
            "kind": "research_report",
            "task_id": task_id,
            "worker_job_id": (
                str(task.get("current_worker_job_id") or "").strip()
                or None
            ),
            "filename": snapshot.resolved_path.name,
            "media_type": "text/markdown",
            "workspace_relative_path": _REPORT_RELATIVE_PATH,
            "content_hash": hashlib.sha256(
                snapshot.content
            ).hexdigest(),
            "size_bytes": snapshot.size_bytes,
            "provenance_summary": {
                "artifact_type": "research_report",
                "authority": "executor_claim",
                "persisted": False,
                "source_count": source_count,
                "has_citations": citation_count > 0,
                "citation_count": citation_count,
                "trace_bundle_ref": _trace_bundle_ref(
                    research_artifact
                ),
            },
        }

    @staticmethod
    def _write_report(
        *,
        workspace_root: Path,
        report: str,
    ) -> None:
        output_dir = workspace_root / "recovery-artifacts"
        try:
            if output_dir.is_symlink():
                raise RecoveryResearchWorkspaceArtifactError(
                    "recovery_research_workspace_symlink_denied"
                )
            output_dir.mkdir(parents=True, exist_ok=True)
            resolved_output_dir = output_dir.resolve(strict=True)
            resolved_output_dir.relative_to(workspace_root)
        except RecoveryResearchWorkspaceArtifactError:
            raise
        except (OSError, ValueError) as exc:
            raise RecoveryResearchWorkspaceArtifactError(
                "recovery_research_workspace_path_invalid"
            ) from exc
        target = resolved_output_dir / "research-report.md"
        if target.is_symlink():
            raise RecoveryResearchWorkspaceArtifactError(
                "recovery_research_workspace_symlink_denied"
            )
        temporary = resolved_output_dir / (
            f".research-report-{uuid.uuid4().hex}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(str(temporary), flags, 0o600)
            total_bytes = 0
            for offset in range(
                0,
                len(report),
                _ENCODE_CHUNK_CHARACTERS,
            ):
                chunk = report[
                    offset : offset + _ENCODE_CHUNK_CHARACTERS
                ].encode("utf-8")
                total_bytes += len(chunk)
                if total_bytes > MAX_RECOVERY_ARTIFACT_BYTES:
                    raise RecoveryResearchWorkspaceArtifactError(
                        "recovery_research_report_too_large"
                    )
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short workspace write")
                    view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, target)
        except RecoveryResearchWorkspaceArtifactError:
            raise
        except (OSError, UnicodeError) as exc:
            raise RecoveryResearchWorkspaceArtifactError(
                "recovery_research_workspace_write_failed"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _workspace_service(self) -> Any:
        if self._workspace_service_provider is not None:
            return self._workspace_service_provider()
        from agent.services.worker_workspace_service import (
            get_worker_workspace_service,
        )

        return get_worker_workspace_service()

    def _workspace_file_reader(self) -> Any:
        if self._workspace_file_reader_provider is not None:
            return self._workspace_file_reader_provider()
        from agent.services.recovery_workspace_file_reader import (
            get_recovery_workspace_file_reader,
        )

        return get_recovery_workspace_file_reader()


def _trace_bundle_ref(
    research_artifact: Mapping[str, Any],
) -> str | None:
    trace = research_artifact.get("trace")
    if not isinstance(trace, Mapping):
        return None
    value = trace.get("trace_bundle_id")
    if not isinstance(value, str) or len(value) > 256:
        return None
    return value.strip() or None


_SERVICE = RecoveryResearchWorkspaceArtifactService()


def get_recovery_research_workspace_artifact_service() -> (
    RecoveryResearchWorkspaceArtifactService
):
    return _SERVICE


__all__ = [
    "RecoveryResearchWorkspaceArtifactError",
    "RecoveryResearchWorkspaceArtifactService",
    "get_recovery_research_workspace_artifact_service",
]
