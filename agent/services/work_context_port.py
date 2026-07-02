"""WorkContext Port — COSMOS-012

IDE/Editor-Arbeitskontext als optionales Ranking-Signal.
Erweitert niemals allowed_paths — verändert ausschließlich Ranking-Gewichte.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

SECRET_PATH_PATTERNS = [
    ".env", ".env.local", ".env.production", ".envrc",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.p8",
    "secrets.json", "credentials.json", ".netrc",
    "id_rsa", "id_ed25519", "id_ecdsa",
]


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class FileRange:
    path: str
    line_start: int
    line_end: int


@dataclass
class WorkContextSnapshot:
    open_files: list[str]
    active_file: str | None
    selection: FileRange | None
    active_branch: str | None
    dirty_files: list[str]           # uncommitted changes
    dirty_secret_files: list[str]    # dirty files matching SECRET_PATH_PATTERNS (redacted)
    source: str                      # "ide_plugin" | "cli_introspection" | "null" | "manual"


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class WorkContext(Protocol):
    def get_snapshot(self) -> WorkContextSnapshot: ...
    def is_available(self) -> bool: ...


# ── NullWorkContext ───────────────────────────────────────────────────────────

class NullWorkContext:
    """Safe default: no context available. Never fails."""

    def get_snapshot(self) -> WorkContextSnapshot:
        return WorkContextSnapshot(
            open_files=[],
            active_file=None,
            selection=None,
            active_branch=None,
            dirty_files=[],
            dirty_secret_files=[],
            source="null",
        )

    def is_available(self) -> bool:
        return False


# ── StaticWorkContext ─────────────────────────────────────────────────────────

class StaticWorkContext:
    """Test helper: returns a fixed snapshot."""

    def __init__(self, snapshot: WorkContextSnapshot) -> None:
        self._snapshot = snapshot

    def get_snapshot(self) -> WorkContextSnapshot:
        return self._snapshot

    def is_available(self) -> bool:
        return True


# ── WorkContextRankingBoost ───────────────────────────────────────────────────

class WorkContextRankingBoost:
    """Computes score boosts for context items based on WorkContext.

    Does NOT expand allowed_paths — ranking only.
    Dirty files matching SECRET_PATH_PATTERNS are never passed to external providers.
    """

    ACTIVE_FILE_BOOST = 0.30
    OPEN_FILE_BOOST = 0.15
    ACTIVE_BRANCH_BOOST = 0.05

    def compute_boost(self, item_path: str, context: WorkContextSnapshot) -> float:
        """Returns score boost for item_path based on context. Max +0.30."""
        boost = 0.0

        if context.active_file and item_path == context.active_file:
            boost += self.ACTIVE_FILE_BOOST
        elif item_path in context.open_files:
            boost += self.OPEN_FILE_BOOST

        if context.active_branch and item_path == context.active_branch:
            boost += self.ACTIVE_BRANCH_BOOST

        return min(boost, self.ACTIVE_FILE_BOOST)

    def should_warn_dirty_secret(self, path: str) -> bool:
        """True if path matches SECRET_PATH_PATTERNS."""
        return self.is_secret_path(path)

    def is_secret_path(self, path: str) -> bool:
        """True if path matches any SECRET_PATH_PATTERNS. Uses fnmatch."""
        basename = os.path.basename(path)
        for pattern in SECRET_PATH_PATTERNS:
            if fnmatch.fnmatch(basename, pattern):
                return True
            # Also check the full path for patterns without wildcards
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def get_redacted_dirty_files(
        self, dirty_files: list[str]
    ) -> tuple[list[str], list[str]]:
        """Returns (clean_files, secret_files). Secret files are listed separately."""
        clean_files: list[str] = []
        secret_files: list[str] = []
        for path in dirty_files:
            if self.is_secret_path(path):
                secret_files.append(path)
            else:
                clean_files.append(path)
        return clean_files, secret_files
