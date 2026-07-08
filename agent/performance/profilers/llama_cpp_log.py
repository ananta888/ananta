"""Parser for llama.cpp style throughput log snippets."""

from __future__ import annotations

import re

from agent.performance.profilers.base import ProfileObservation

_TPS = re.compile(
    r"(?P<label>prompt eval|eval|decode|prompt processing)[^\n]*?"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*tokens per second",
    re.I,
)


class LlamaCppLogProfilerParser:
    def parse(self, text: str) -> ProfileObservation:
        metrics = {}
        for match in _TPS.finditer(str(text or "")):
            label = match.group("label").lower().replace(" ", "_")
            metrics[f"{label}_tokens_per_second"] = float(match.group("value"))
        if not metrics:
            return ProfileObservation(
                parser="llama_cpp_log",
                status="degraded",
                warnings=["no_llama_cpp_throughput_found"],
                raw_excerpt=str(text or "")[:1000],
            )
        hotspots = [
            {"symbol": key, "score": 1.0 / max(value, 0.0001), "evidence": f"{key}={value} tokens/s"}
            for key, value in metrics.items()
        ]
        return ProfileObservation(parser="llama_cpp_log", status="completed", metrics=metrics, hotspots=hotspots)
