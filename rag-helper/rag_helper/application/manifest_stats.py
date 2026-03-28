from __future__ import annotations

from collections import Counter, defaultdict


def count_records_by_kind(*record_groups: list[dict]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for records in record_groups:
        for record in records:
            kind = record.get("kind")
            if kind:
                counter[str(kind)] += 1
    return dict(sorted(counter.items()))


def collect_error_entries(manifest_files: list[dict]) -> list[dict]:
    return [
        {
            "file": entry.get("file"),
            "ext": entry.get("ext"),
            "stage": entry.get("stage"),
            "error": entry.get("error"),
        }
        for entry in manifest_files
        if entry.get("error")
    ]


def collect_skip_reason_counts(manifest_files: list[dict]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for entry in manifest_files:
        if entry.get("skipped") and entry.get("skip_reason"):
            counter[str(entry["skip_reason"])] += 1
    return dict(sorted(counter.items()))


def collect_extension_stats(manifest_files: list[dict]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {
        "file_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "cache_hit_count": 0,
    })
    for entry in manifest_files:
        ext = str(entry.get("ext") or "<unknown>")
        stats[ext]["file_count"] += 1
        if entry.get("skipped"):
            stats[ext]["skipped_count"] += 1
        if entry.get("error"):
            stats[ext]["error_count"] += 1
        if entry.get("cache_hit"):
            stats[ext]["cache_hit_count"] += 1
    return dict(sorted(stats.items()))
