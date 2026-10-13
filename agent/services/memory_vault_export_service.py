from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.memory_storage_boundary import MemoryStorageBoundary
from agent.services.memory_tree_store_service import get_memory_tree_store_service
from ananta_contracts.memory_access import MemoryOperation

_DEFAULT_EXCLUDE = {"secret", "credential", "security_sensitive"}


class MemoryVaultChunkReadPort(Protocol):
    def get_chunks_by_source(self, source_id: str, *, lifecycle=None, limit=500) -> list: ...


def _origin_frontmatter(chunk, export_origin):
    """Preserve source and export observations without claiming verification."""
    source_origin = (getattr(chunk, "chunk_metadata", None) or {}).get("memory_provenance")
    source_json = json.dumps(source_origin, ensure_ascii=False, sort_keys=True, allow_nan=False)
    export_json = json.dumps(export_origin, ensure_ascii=False, sort_keys=True, allow_nan=False)
    content = str(getattr(chunk, "content", "") or "")
    return [
        "grounding_state: unverified", "production_release_evidence: false",
        f"content_digest: {hashlib.sha256(content.encode('utf-8')).hexdigest()}",
        f"memory_access_origin: {source_json}", f"export_access_origin: {export_json}",
    ]


def _stable_name(prefix: str, raw_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{raw_id}".encode("utf-8", errors="replace")).hexdigest()[:12]
    safe = "".join(ch for ch in str(raw_id or "unknown") if ch.isalnum() or ch in {"-", "_", "."})[:48]
    return f"{safe}-{digest}.md"


class MemoryVaultExportService:
    """Exports Memory Tree chunks/nodes into deterministic markdown files."""

    def __init__(self, *, export_root: Path | None = None, store: MemoryVaultChunkReadPort | None = None):
        self._export_root = Path(export_root).resolve() if export_root is not None else None
        self._boundary = MemoryStorageBoundary("hub-memory-vault")
        self._store = store

    def export(
        self,
        *,
        cfg: dict[str, Any] | None,
        source_ids: list[str] | None = None,
        include_sensitive_local_only: bool = False,
    ) -> dict[str, Any]:
        config = dict(cfg or {})
        export_cfg = dict(config.get("memory_vault_export") or {})
        if not bool(export_cfg.get("enabled", False)):
            return {"enabled": False, "written": 0, "skipped_sensitive": 0, "output_dir": None}

        decision = self._boundary.authorize(MemoryOperation.append)
        root = self._export_root
        if root is None:
            from flask import current_app, has_app_context

            configured = current_app.config.get("MEMORY_VAULT_EXPORT_ROOT") if has_app_context() else None
            if not configured:
                raise MemoryAccessError("memory_export_root_required")
            root = Path(configured).resolve()
        requested = Path(str(export_cfg.get("output_dir") or root)).resolve()
        if not requested.is_relative_to(root):
            raise MemoryAccessError("memory_export_path_forbidden")
        # Stable task namespace, independent of the short-lived read/admission session.
        output_dir = requested / self._boundary.scoped_id(decision, "vault-namespace")
        if output_dir.is_symlink() or (output_dir / "sources").is_symlink():
            raise MemoryAccessError("memory_export_path_forbidden")

        excluded = {str(x).strip().lower()
                    for x in (export_cfg.get("exclude_sensitivity") or list(_DEFAULT_EXCLUDE))}
        if include_sensitive_local_only:
            excluded = set()

        store = self._store if self._store is not None else get_memory_tree_store_service()
        rows: list[Any] = []
        if source_ids:
            for sid in source_ids:
                rows.extend(store.get_chunks_by_source(str(sid), lifecycle=None, limit=2000))

        self._boundary.recheck(decision)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "sources").mkdir(exist_ok=True)
        written = 0
        skipped_sensitive = 0
        for chunk in rows:
            sensitivity = str(getattr(chunk, "sensitivity", "internal") or "internal").lower()
            if sensitivity in excluded:
                skipped_sensitive += 1
                continue
            name = _stable_name("source", str(getattr(chunk, "id", "")))
            frontmatter = [
                "---",
                f"source_type: {json.dumps(getattr(chunk, 'source_type', 'unknown'))}",
                f"sensitivity: {sensitivity}",
                f"provenance: {json.dumps(getattr(chunk, 'provenance_ref', '') or '')}",
                "worker_allowed: false",
                "cloud_allowed: false",
                "trust_level: untrusted",
                "scope: task",
                f"hash: {getattr(chunk, 'id', '')}",
                *_origin_frontmatter(chunk, self._boundary.metadata(decision)["memory_provenance"]),
                f"generated_at: {int(time.time())}",
                "---",
                "",
            ]
            body = [
                f"# {getattr(chunk, 'label', 'chunk')}",
                "",
                str(getattr(chunk, "content", "") or ""),
                "",
            ]
            self._boundary.recheck(decision)
            self._write_namespace_file(output_dir / "sources", name, "\n".join(frontmatter + body))
            written += 1

        return {
            "enabled": True,
            "written": written,
            "skipped_sensitive": skipped_sensitive,
            "output_dir": str(output_dir),
        }

    @staticmethod
    def _write_namespace_file(directory, name, content):
        from agent.adapters.memory_namespace_files import atomic_write_namespace_file

        atomic_write_namespace_file(directory, name, content)


_SERVICE = MemoryVaultExportService()


def get_memory_vault_export_service() -> MemoryVaultExportService:
    return _SERVICE

