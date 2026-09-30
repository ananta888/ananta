"""Bounded manifest and JSONL previews of materialized rag-helper indices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.db_models import KnowledgeIndexDB


def load_index_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_jsonl_preview(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    preview: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                preview.append(payload)
            if len(preview) >= limit:
                break
    except Exception:
        return []
    return preview


def load_partitioned_jsonl_preview(
    output_dir: Path,
    files: list[str] | None,
    *,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    preview: dict[str, list[dict[str, Any]]] = {}
    for relative_path in files or []:
        path = output_dir / relative_path
        preview[path.stem] = load_jsonl_preview(path, limit=limit)
    return preview


def build_knowledge_index_preview(
    knowledge_index: KnowledgeIndexDB,
    *,
    limit: int,
) -> dict[str, Any] | None:
    """Preview an index whose ``output_dir`` is already known to be set."""

    output_dir = Path(knowledge_index.output_dir)
    if not output_dir.exists():
        return None
    manifest_path = (
        Path(knowledge_index.manifest_path)
        if knowledge_index.manifest_path
        else output_dir / "manifest.json"
    )
    manifest = load_index_manifest(manifest_path)
    partitioned_outputs = manifest.get("partitioned_outputs") or {}
    return {
        "knowledge_index": knowledge_index.model_dump(),
        "manifest": manifest,
        "available_outputs": partitioned_outputs,
        "preview": {
            "index": load_jsonl_preview(output_dir / "index.jsonl", limit=limit),
            "details": load_jsonl_preview(output_dir / "details.jsonl", limit=limit),
            "relations": load_jsonl_preview(output_dir / "relations.jsonl", limit=limit),
            "xml_overview": load_jsonl_preview(output_dir / "xml_overview.jsonl", limit=limit),
            "gems_by_domain": load_partitioned_jsonl_preview(
                output_dir,
                partitioned_outputs.get("gems"),
                limit=limit,
            ),
            "xsd_full": {
                "index": load_jsonl_preview(output_dir / "xsd_full" / "index.jsonl", limit=limit),
                "details": load_jsonl_preview(output_dir / "xsd_full" / "details.jsonl", limit=limit),
                "relations": load_jsonl_preview(output_dir / "xsd_full" / "relations.jsonl", limit=limit),
            },
        },
    }
