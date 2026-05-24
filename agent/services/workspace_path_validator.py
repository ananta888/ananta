from __future__ import annotations

import os
from dataclasses import dataclass

# Reason codes
REASON_OK = "ok"
REASON_INVALID_PATH = "invalid_path"
REASON_OUTSIDE_WORKSPACE = "outside_workspace"
REASON_PATH_TRAVERSAL = "path_traversal"
REASON_SYMLINK_ESCAPE = "symlink_escape"


@dataclass(frozen=True)
class PathValidationResult:
    ok: bool
    resolved_path: str
    reason: str

    @classmethod
    def success(cls, resolved_path: str) -> "PathValidationResult":
        return cls(ok=True, resolved_path=resolved_path, reason=REASON_OK)

    @classmethod
    def failure(cls, reason: str) -> "PathValidationResult":
        return cls(ok=False, resolved_path="", reason=reason)


class WorkspacePathValidator:
    """Validates file paths before opening editor sessions.

    Rules enforced:
    - Path must not be empty or contain only whitespace.
    - Resolved path must be within the authorized workspace root.
    - Symlinks are followed; the resolved target must also be within workspace.
    - Path arguments are returned as pre-split argv components, never as shell strings.
    """

    def __init__(self, workspace_root: str) -> None:
        if not workspace_root or not str(workspace_root).strip():
            raise ValueError("workspace_root must not be empty")
        self._workspace_root = os.path.realpath(os.path.abspath(str(workspace_root)))

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    def validate(self, file_path: str) -> PathValidationResult:
        """Validate and resolve a file path against the workspace root.

        Returns PathValidationResult with ok=True and the normalized absolute
        path on success, or ok=False with a reason code on failure.
        """
        raw = str(file_path or "").strip()
        if not raw:
            return PathValidationResult.failure(REASON_INVALID_PATH)

        # Absolute path of the raw input (before symlink resolution)
        if os.path.isabs(raw):
            abs_path = os.path.normpath(raw)
        else:
            abs_path = os.path.normpath(os.path.join(self._workspace_root, raw))

        # Quick traversal check on the un-resolved path
        if not _is_within(abs_path, self._workspace_root):
            if ".." in raw.split(os.sep) or ".." in raw.split("/"):
                return PathValidationResult.failure(REASON_PATH_TRAVERSAL)
            return PathValidationResult.failure(REASON_OUTSIDE_WORKSPACE)

        # Resolve symlinks and re-check
        try:
            real_path = os.path.realpath(abs_path)
        except OSError:
            return PathValidationResult.failure(REASON_INVALID_PATH)

        if not _is_within(real_path, self._workspace_root):
            return PathValidationResult.failure(REASON_SYMLINK_ESCAPE)

        return PathValidationResult.success(real_path)

    def build_safe_argv(self, file_path: str) -> list[str]:
        """Return [resolved_path] as a single-element list for safe argv use.

        Raises ValueError if the path does not pass validation.
        """
        result = self.validate(file_path)
        if not result.ok:
            raise ValueError(f"Path validation failed ({result.reason}): {file_path!r}")
        return [result.resolved_path]


def _is_within(path: str, root: str) -> bool:
    """Return True if path is inside root (inclusive)."""
    # Ensure both end with sep so /workspace doesn't match /workspace-other
    norm_root = root.rstrip(os.sep) + os.sep
    norm_path = path.rstrip(os.sep) + os.sep
    return norm_path.startswith(norm_root)
