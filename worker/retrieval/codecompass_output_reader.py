from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTPUT_FILENAME_BY_KEY = {
    "index": "index.jsonl",
    "details": "details.jsonl",
    "context": "context.jsonl",
    "embedding": "embedding.jsonl",
    "relations": "relations.jsonl",
    "graph_nodes": "graph_nodes.jsonl",
    "graph_edges": "graph_edges.jsonl",
}


@dataclass(frozen=True)
class ReaderDiagnostics:
    malformed_line_count: int = 0
    skipped_non_object_count: int = 0
    missing_outputs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "malformed_line_count": int(self.malformed_line_count),
            "skipped_non_object_count": int(self.skipped_non_object_count),
            "missing_outputs": list(self.missing_outputs),
        }


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 64), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _iter_jsonl_records(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    malformed = 0
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = line.strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(parsed, dict):
            skipped += 1
            continue
        records.append(parsed)
    return records, malformed, skipped


def _normalize_output_entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    records, malformed, skipped = _iter_jsonl_records(path)
    return {
        "path": str(path),
        "sha256": _file_sha256(path),
        "mtime": float(stat.st_mtime),
        "record_count": len(records),
        "_records": records,
        "_malformed": malformed,
        "_skipped": skipped,
    }


def build_output_manifest(
    *,
    output_dir: str | Path,
    codecompass_version: str = "unknown",
    profile_name: str = "default",
    source_scope: str = "repo",
    generated_at: str = "unknown",
) -> dict[str, Any]:
    directory = Path(output_dir).resolve()
    outputs: dict[str, dict[str, Any] | None] = {}
    for key, filename in OUTPUT_FILENAME_BY_KEY.items():
        file_path = directory / filename
        outputs[key] = _normalize_output_entry(file_path) if file_path.exists() else None
    manifest = {
        "schema": "codecompass_output_manifest.v1",
        "codecompass_version": str(codecompass_version or "unknown").strip() or "unknown",
        "profile_name": str(profile_name or "default").strip() or "default",
        "source_scope": str(source_scope or "repo").strip() or "repo",
        "generated_at": str(generated_at or "unknown").strip() or "unknown",
        "output_dir": str(directory),
        "outputs": {
            key: (
                {
                    "path": value["path"],
                    "sha256": value["sha256"],
                    "mtime": value["mtime"],
                    "record_count": value["record_count"],
                }
                if value is not None
                else None
            )
            for key, value in outputs.items()
        },
    }
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    manifest["manifest_hash"] = manifest_hash
    return manifest


class CodeCompassOutputReader:
    def load_from_output_dir(
        self,
        *,
        output_dir: str | Path,
        codecompass_version: str = "unknown",
        profile_name: str = "default",
        source_scope: str = "repo",
        generated_at: str = "unknown",
    ) -> dict[str, Any]:
        directory = Path(output_dir).resolve()
        manifest = build_output_manifest(
            output_dir=directory,
            codecompass_version=codecompass_version,
            profile_name=profile_name,
            source_scope=source_scope,
            generated_at=generated_at,
        )
        records: list[dict[str, Any]] = []
        malformed_total = 0
        skipped_total = 0
        missing_outputs: list[str] = []
        for key, filename in OUTPUT_FILENAME_BY_KEY.items():
            file_path = directory / filename
            if not file_path.exists():
                missing_outputs.append(key)
                continue
            loaded_records, malformed, skipped = _iter_jsonl_records(file_path)
            malformed_total += malformed
            skipped_total += skipped
            for index, record in enumerate(loaded_records, start=1):
                records.append(
                    {
                        **record,
                        "_provenance": {
                            "engine": "codecompass_output_reader",
                            "record_id": str(record.get("id") or f"{key}:{index}"),
                            "output_kind": key,
                            "output_file": str(file_path),
                            "manifest_hash": str(manifest.get("manifest_hash") or ""),
                            "source_scope": str(source_scope or "repo"),
                        },
                    }
                )
        diagnostics = ReaderDiagnostics(
            malformed_line_count=malformed_total,
            skipped_non_object_count=skipped_total,
            missing_outputs=tuple(sorted(missing_outputs)),
        )
        return {
            "manifest": manifest,
            "records": records,
            "diagnostics": diagnostics.as_dict(),
            "standalone_compatible": True,
        }
