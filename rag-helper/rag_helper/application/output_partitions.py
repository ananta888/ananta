from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def write_partitioned_jsonl(
    out_dir: Path,
    directory_name: str,
    items: list[dict],
    *,
    key_getter,
) -> list[str]:
    if not items:
        return []

    partition_dir = out_dir / directory_name
    partition_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        key = _safe_partition_name(key_getter(item))
        grouped[key].append(item)

    relative_paths: list[str] = []
    for key, partition_items in sorted(grouped.items()):
        rel_path = f"{directory_name}/{key}.jsonl"
        with (out_dir / rel_path).open("w", encoding="utf-8") as handle:
            for item in partition_items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        relative_paths.append(rel_path)
    return relative_paths


def _safe_partition_name(value: str | None) -> str:
    text = str(value or "unknown").strip() or "unknown"
    safe = []
    for char in text:
        if char.isalnum() or char in {"-", "_", "."}:
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe)[:120]
