from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


class WorkspaceConstraintError(ValueError):
    """Raised when workspace boundaries are violated."""


@dataclass(frozen=True)
class WorkspaceConstraints:
    constraint_id: str
    allowed_roots: list[str]
    writable_output_paths: list[str]
    max_files: int
    max_bytes: int
    allowed_commands: list[str]
    allow_main_tree_apply: bool = False

    @classmethod
    def from_dict(cls, payload: dict) -> "WorkspaceConstraints":
        return cls(
            constraint_id=str(payload.get("constraint_id") or "").strip(),
            allowed_roots=[str(item) for item in list(payload.get("allowed_roots") or [])],
            writable_output_paths=[str(item) for item in list(payload.get("writable_output_paths") or [])],
            max_files=int(payload.get("max_files") or 0),
            max_bytes=int(payload.get("max_bytes") or 0),
            allowed_commands=[str(item) for item in list(payload.get("allowed_commands") or [])],
            allow_main_tree_apply=bool(payload.get("allow_main_tree_apply")),
        )

    def validate(self) -> None:
        if not self.constraint_id:
            raise WorkspaceConstraintError("constraint_id_required")
        if not self.allowed_roots:
            raise WorkspaceConstraintError("allowed_roots_required")
        if not self.writable_output_paths:
            raise WorkspaceConstraintError("writable_output_paths_required")
        if self.max_files <= 0:
            raise WorkspaceConstraintError("max_files_must_be_positive")
        if self.max_bytes <= 0:
            raise WorkspaceConstraintError("max_bytes_must_be_positive")


@dataclass(frozen=True)
class WorkerWorkspace:
    task_id: str
    path: Path
    constraints: WorkspaceConstraints

    def ensure_within_allowed_roots(self, candidate: Path) -> None:
        normalized_candidate = candidate.resolve()
        for root in self.constraints.allowed_roots:
            root_path = Path(root).resolve()
            if normalized_candidate == root_path or root_path in normalized_candidate.parents:
                return
        raise WorkspaceConstraintError("path_outside_allowed_roots")

    def ensure_output_path_allowed(self, candidate: Path) -> None:
        normalized_candidate = candidate.resolve()
        for output_prefix in self.constraints.writable_output_paths:
            prefix_path = Path(output_prefix).resolve()
            if normalized_candidate == prefix_path or prefix_path in normalized_candidate.parents:
                return
        raise WorkspaceConstraintError("path_outside_writable_outputs")

    def usage_summary(self) -> dict[str, int]:
        file_count = 0
        byte_count = 0
        for path in self.path.rglob("*"):
            if path.is_file():
                file_count += 1
                byte_count += path.stat().st_size
        return {"file_count": file_count, "byte_count": byte_count}


def create_worker_workspace(*, task_id: str, constraints: WorkspaceConstraints, base_dir: Path | None = None) -> WorkerWorkspace:
    constraints.validate()
    workspace_path = Path(
        tempfile.mkdtemp(
            prefix=f"ananta-worker-{str(task_id).strip() or 'task'}-",
            dir=str(base_dir) if base_dir is not None else None,
        )
    )
    return WorkerWorkspace(task_id=str(task_id).strip(), path=workspace_path, constraints=constraints)


def cleanup_worker_workspace(workspace: WorkerWorkspace) -> None:
    if workspace.path.exists():
        shutil.rmtree(workspace.path, ignore_errors=True)
