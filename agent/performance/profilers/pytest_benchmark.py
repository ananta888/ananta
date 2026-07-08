"""Parser for pytest-benchmark-like output snippets."""

from __future__ import annotations

import re

from agent.performance.profilers.base import ProfileObservation

_ROW = re.compile(
    r"(?P<name>[A-Za-z0-9_./:-]+)\s+"
    r"(?P<min>[0-9]+(?:\.[0-9]+)?)\s+"
    r"(?P<max>[0-9]+(?:\.[0-9]+)?)\s+"
    r"(?P<mean>[0-9]+(?:\.[0-9]+)?)",
)


class PytestBenchmarkProfilerParser:
    def parse(self, text: str) -> ProfileObservation:
        rows = []
        for match in _ROW.finditer(str(text or "")):
            rows.append({
                "name": match.group("name"),
                "min": float(match.group("min")),
                "max": float(match.group("max")),
                "mean": float(match.group("mean")),
            })
        if not rows:
            return ProfileObservation(
                parser="pytest_benchmark",
                status="degraded",
                warnings=["no_pytest_benchmark_rows_found"],
                raw_excerpt=str(text or "")[:1000],
            )
        hotspots = [
            {"symbol": row["name"], "score": row["mean"], "evidence": f"mean={row['mean']}"}
            for row in sorted(rows, key=lambda item: item["mean"], reverse=True)
        ]
        return ProfileObservation(
            parser="pytest_benchmark",
            status="completed",
            metrics={"benchmarks": rows},
            hotspots=hotspots,
        )
