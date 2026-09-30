"""Public error type of the browser-folder workspace snapshot upload."""

from __future__ import annotations


class WorkspaceSnapshotUploadError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


__all__ = ["WorkspaceSnapshotUploadError"]
