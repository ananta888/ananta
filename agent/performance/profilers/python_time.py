"""Parser for simple wall-clock timing text."""

from __future__ import annotations

import re

from agent.performance.profilers.base import ProfileObservation

_SECONDS = re.compile(r"(?P<label>[A-Za-z0-9_.:/ -]{1,80})[:=]\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|seconds)\b")


class PythonTimeProfilerParser:
    def parse(self, text: str) -> ProfileObservation:
        metrics = {}
        hotspots = []
        for match in _SECONDS.finditer(str(text or "")):
            label = match.group("label").strip()
            value = float(match.group("value"))
            metrics[label] = value
            hotspots.append({"symbol": label, "score": value, "evidence": f"{label}={value}s"})
        if not metrics:
            return ProfileObservation(
                parser="python_time",
                status="degraded",
                warnings=["no_wall_clock_metrics_found"],
                raw_excerpt=str(text or "")[:1000],
            )
        return ProfileObservation(parser="python_time", status="completed", metrics=metrics, hotspots=hotspots)
