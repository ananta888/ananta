"""Create falsifiable optimization hypotheses from hotspots."""

from __future__ import annotations

import uuid
from typing import Any

from agent.performance.artifacts import build_optimization_hypothesis_artifact


class OptimizationHypothesisService:
    def generate(self, *, hotspot_report: dict[str, Any]) -> list[dict[str, Any]]:
        hypotheses = []
        for hotspot in hotspot_report.get("hotspots") or []:
            bottleneck = self._classify(hotspot)
            hypotheses.append(build_optimization_hypothesis_artifact(
                hypothesis_id=f"hyp-{uuid.uuid4().hex[:12]}",
                hotspot_refs=[{"hotspot_id": hotspot.get("hotspot_id"), "symbol": hotspot.get("symbol")}],
                suspected_bottleneck=bottleneck,
                expected_effect=self._expected_effect(bottleneck),
                affected_files=list(hotspot.get("affected_files") or []),
                risk="high" if bottleneck in {"concurrency", "algorithmic"} else "medium",
                required_measurements=["baseline_wall_time", "candidate_wall_time", "regression_result"],
                falsification_criteria=[
                    "candidate does not improve the primary metric above threshold",
                    "candidate changes output or fails regression tests",
                ],
            ))
        if not hypotheses:
            hypotheses.append(build_optimization_hypothesis_artifact(
                hypothesis_id=f"hyp-{uuid.uuid4().hex[:12]}",
                hotspot_refs=[{"source": "hotspot_report", "status": hotspot_report.get("status", "unknown")}],
                suspected_bottleneck="unknown",
                expected_effect="Collect more profiling evidence before proposing code changes.",
                affected_files=[],
                risk="medium",
                required_measurements=["more_profile_data"],
                falsification_criteria=["no stable hotspot is found after additional profiling"],
            ))
        return hypotheses

    @staticmethod
    def _classify(hotspot: dict[str, Any]) -> str:
        text = f"{hotspot.get('symbol', '')} {hotspot.get('suspected_layer', '')}".lower()
        if "copy" in text or "bus" in text:
            return "bus_bound"
        if "alloc" in text or "memory" in text or "mem" in text:
            return "memory_bound"
        if "io" in text or "read" in text or "write" in text:
            return "io_bound"
        if "thread" in text or "lock" in text:
            return "concurrency"
        if "config" in text:
            return "config_only"
        return "compute_bound" if hotspot.get("score") else "unknown"

    @staticmethod
    def _expected_effect(bottleneck: str) -> str:
        return {
            "bus_bound": "Reduce host/device transfer or overlap copy and compute.",
            "memory_bound": "Reduce allocations or memory traffic.",
            "io_bound": "Reduce blocking IO or batch file operations.",
            "concurrency": "Reduce contention or improve scheduling.",
            "config_only": "Tune configuration without code mutation.",
            "compute_bound": "Reduce expensive computation in the hotspot.",
        }.get(bottleneck, "Gather more evidence before changing code.")


def get_optimization_hypothesis_service() -> OptimizationHypothesisService:
    return OptimizationHypothesisService()
