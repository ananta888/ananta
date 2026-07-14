"""Hub-owned filesystem boundary for Worker Context materialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkerContextPathPolicyError(ValueError):
    """A requested directory is invalid or outside the Hub-owned root."""

    def __init__(self, reason_code: str, *, field_name: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field_name = field_name


@dataclass(frozen=True)
class WorkerContextPathPolicy:
    """Resolve caller paths without allowing traversal or symlink escapes."""

    hub_workspace_root: Path

    @classmethod
    def from_value(cls, root: str | Path) -> "WorkerContextPathPolicy":
        raw_root = Path(str(root or "").strip()).expanduser()
        if not raw_root.is_absolute():
            raise WorkerContextPathPolicyError(
                "hub_workspace_root_must_be_absolute",
                field_name="hub_workspace_root",
            )
        resolved_root = raw_root.resolve(strict=False)
        return cls(hub_workspace_root=resolved_root)

    def resolve_directory(self, value: str, *, field_name: str) -> Path:
        raw_value = str(value or "").strip()
        if not raw_value:
            raise WorkerContextPathPolicyError(
                f"{field_name}_required",
                field_name=field_name,
            )

        candidate = Path(raw_value).expanduser()
        if not candidate.is_absolute():
            candidate = self.hub_workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise WorkerContextPathPolicyError(
                f"{field_name}_not_found",
                field_name=field_name,
            ) from exc

        try:
            resolved.relative_to(self.hub_workspace_root)
        except ValueError as exc:
            raise WorkerContextPathPolicyError(
                f"{field_name}_outside_hub_workspace",
                field_name=field_name,
            ) from exc
        if not resolved.is_dir():
            raise WorkerContextPathPolicyError(
                f"{field_name}_not_directory",
                field_name=field_name,
            )
        return resolved


__all__ = ["WorkerContextPathPolicy", "WorkerContextPathPolicyError"]
