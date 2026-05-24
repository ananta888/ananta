from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from agent.services.memory_tree_store_service import get_memory_tree_store_service

_DEFAULT_EXCLUDE = {"secret", "credential", "security_sensitive"}


def _stable_name(prefix: str, raw_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{raw_id}".encode("utf-8", errors="replace")).hexdigest()[:12]
    safe = "".join(ch for ch in str(raw_id or "unknown") if ch.isalnum() or ch in {"-", "_", "."})[:48]
    return f"{safe}-{digest}.md"


class MemoryVaultExportService:
    """Exports Memory Tree chunks/nodes into deterministic markdown files."""

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

        output_dir = Path(str(export_cfg.get("output_dir") or ".ananta/memory")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "sources").mkdir(parents=True, exist_ok=True)
        (output_dir / "topics").mkdir(parents=True, exist_ok=True)
        (output_dir / "goals").mkdir(parents=True, exist_ok=True)
        (output_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (output_dir / "decisions").mkdir(parents=True, exist_ok=True)
        (output_dir / "security").mkdir(parents=True, exist_ok=True)

        excluded = set(str(x).strip().lower() for x in (export_cfg.get("exclude_sensitivity") or list(_DEFAULT_EXCLUDE)))
        if include_sensitive_local_only:
            excluded = set()

        store = get_memory_tree_store_service()
        rows: list[Any] = []
        if source_ids:
            for sid in source_ids:
                rows.extend(store.get_chunks_by_source(str(sid), lifecycle=None, limit=2000))

        written = 0
        skipped_sensitive = 0
        for chunk in rows:
            sensitivity = str(getattr(chunk, "sensitivity", "internal") or "internal").lower()
            if sensitivity in excluded:
                skipped_sensitive += 1
                continue
            name = _stable_name("source", str(getattr(chunk, "id", "")))
            path = output_dir / "sources" / name
            frontmatter = [
                "---",
                f"source_type: {getattr(chunk, 'source_type', 'unknown')}",
                f"sensitivity: {sensitivity}",
                f"provenance: {getattr(chunk, 'provenance_ref', '') or ''}",
                "worker_allowed: true",
                f"cloud_allowed: {str(sensitivity in {'public', 'internal'}).lower()}",
                f"hash: {getattr(chunk, 'id', '')}",
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
            path.write_text("\n".join(frontmatter + body), encoding="utf-8")
            written += 1

        return {
            "enabled": True,
            "written": written,
            "skipped_sensitive": skipped_sensitive,
            "output_dir": str(output_dir),
        }


_SERVICE = MemoryVaultExportService()


def get_memory_vault_export_service() -> MemoryVaultExportService:
    return _SERVICE

