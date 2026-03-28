from __future__ import annotations


def build_benchmark_report(manifest_files: list[dict], mode: str) -> dict | None:
    if mode != "basic":
        return None

    files_with_duration = [
        entry for entry in manifest_files
        if entry.get("duration_ms") is not None
    ]
    by_extension: dict[str, dict] = {}
    largest_outputs: list[dict] = []
    slowest_files: list[dict] = []

    for entry in files_with_duration:
        ext = entry.get("ext", "")
        stats = by_extension.setdefault(ext, {
            "file_count": 0,
            "total_duration_ms": 0.0,
            "total_output_records": 0,
        })
        output_record_count = entry.get("output_record_count", 0)
        stats["file_count"] += 1
        stats["total_duration_ms"] += entry.get("duration_ms", 0.0)
        stats["total_output_records"] += output_record_count
        largest_outputs.append({
            "file": entry.get("file"),
            "ext": ext,
            "output_record_count": output_record_count,
        })
        slowest_files.append({
            "file": entry.get("file"),
            "ext": ext,
            "duration_ms": entry.get("duration_ms", 0.0),
        })

    for ext, stats in by_extension.items():
        file_count = stats["file_count"] or 1
        stats["avg_duration_ms"] = round(stats["total_duration_ms"] / file_count, 3)
        stats["avg_output_records"] = round(stats["total_output_records"] / file_count, 3)

    return {
        "by_extension": by_extension,
        "slowest_files": sorted(slowest_files, key=lambda item: item["duration_ms"], reverse=True)[:10],
        "largest_outputs": sorted(
            largest_outputs,
            key=lambda item: item["output_record_count"],
            reverse=True,
        )[:10],
    }
