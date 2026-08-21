"""Capability-gated local Markdown correction port and Obsidian adapter."""

from __future__ import annotations

import hashlib
import difflib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import portalocker

from ananta_contracts.knowledge_hygiene import CorrectionProposal


class KnowledgeWritebackError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class WritebackReceipt:
    correction_id: str
    target_path: str
    previous_sha256: str
    resulting_sha256: str
    backup_path: str


class KnowledgeWritebackPort(Protocol):
    def preview(self, proposal: CorrectionProposal) -> dict[str, str]: ...
    def apply(self, proposal: CorrectionProposal) -> WritebackReceipt: ...


def content_sha256(content: bytes | str) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


class ObsidianMarkdownWritebackAdapter:
    """Atomic, race-safe adapter for explicitly allowed local vault roots only."""

    def __init__(self, *, allowed_roots: Sequence[Path], max_patch_bytes: int) -> None:
        self._roots = tuple(root.expanduser().resolve(strict=True) for root in allowed_roots)
        self._max_patch_bytes = int(max_patch_bytes)
        if not self._roots:
            raise KnowledgeWritebackError("obsidian_root_not_configured")

    def preview(self, proposal: CorrectionProposal) -> dict[str, str]:
        target = self._target(proposal.source_locator)
        current = self._read_current(target)
        current_hash = content_sha256(current)
        diff = "".join(
            difflib.unified_diff(
                current.decode("utf-8", errors="replace").splitlines(keepends=True),
                proposal.proposed_content.splitlines(keepends=True),
                fromfile="current",
                tofile="proposed",
                n=3,
            )
        )
        return {
            "target_path": str(target),
            "base_sha256": proposal.base_content_sha256,
            "current_sha256": current_hash,
            "proposed_sha256": content_sha256(proposal.proposed_content),
            "status": "clean" if current_hash == proposal.base_content_sha256 else "source_changed",
            "diff": diff[: self._max_patch_bytes],
        }

    def apply(self, proposal: CorrectionProposal) -> WritebackReceipt:
        proposed = proposal.proposed_content.encode("utf-8")
        if len(proposed) > self._max_patch_bytes:
            raise KnowledgeWritebackError("correction_too_large")
        target = self._target(proposal.source_locator)
        lock_path = target.parent / f".{target.name}.knowledge-hygiene.lock"
        with portalocker.Lock(str(lock_path), mode="a", timeout=5):
            target = self._target(proposal.source_locator)
            current = self._read_current(target)
            current_hash = content_sha256(current)
            if current_hash != proposal.base_content_sha256:
                raise KnowledgeWritebackError("source_revision_race")
            backup_dir = target.parent / ".knowledge-hygiene-backups"
            backup_dir.mkdir(mode=0o700, exist_ok=True)
            backup = backup_dir / f"{target.name}.{current_hash[:16]}.bak"
            if not backup.exists():
                shutil.copy2(target, backup, follow_symlinks=False)
            fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(proposed)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, target.stat(follow_symlinks=False).st_mode & 0o777)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return WritebackReceipt(
            correction_id=proposal.correction_id,
            target_path=str(target),
            previous_sha256=current_hash,
            resulting_sha256=content_sha256(proposed),
            backup_path=str(backup),
        )

    def _target(self, locator: str) -> Path:
        candidate = Path(locator).expanduser()
        if candidate.suffix.casefold() not in {".md", ".markdown"}:
            raise KnowledgeWritebackError("unsupported_writeback_type")
        if candidate.is_symlink():
            raise KnowledgeWritebackError("symlink_writeback_forbidden")
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, RuntimeError) as exc:
            raise KnowledgeWritebackError("writeback_target_not_found") from exc
        for parent in (candidate, *candidate.parents):
            if parent.is_symlink():
                raise KnowledgeWritebackError("symlink_writeback_forbidden")
        if not resolved.is_file():
            raise KnowledgeWritebackError("writeback_target_not_file")
        if not any(resolved == root or root in resolved.parents for root in self._roots):
            raise KnowledgeWritebackError("writeback_path_outside_allowed_roots")
        return resolved

    def _read_current(self, target: Path) -> bytes:
        size = target.stat(follow_symlinks=False).st_size
        if size > self._max_patch_bytes:
            raise KnowledgeWritebackError("source_too_large")
        return target.read_bytes()
