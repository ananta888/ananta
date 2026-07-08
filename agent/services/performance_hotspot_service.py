"""Map profiling observations to bounded hotspot records."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PerformanceHotspotService:
    def resolve_hotspots(
        self,
        *,
        profile_observation: dict[str, Any],
        workspace_dir: str | Path = ".",
        max_hotspots: int = 10,
    ) -> dict[str, Any]:
        hotspots = []
        for index, item in enumerate(list(profile_observation.get("hotspots") or [])[:max_hotspots], start=1):
            symbol = str(item.get("symbol") or item.get("name") or f"hotspot-{index}")
            affected_files = self._candidate_files(symbol, workspace_dir)
            hotspots.append({
                "hotspot_id": f"hotspot-{index}",
                "symbol": symbol,
                "score": float(item.get("score") or 0.0),
                "evidence_refs": [
                    {"source": "profile_observation", "symbol": symbol, "evidence": item.get("evidence", "")}
                ],
                "suspected_layer": self._suspected_layer(symbol),
                "affected_files": affected_files,
            })
        status = "completed" if hotspots else "degraded"
        return {
            "schema": "performance_hotspot_report.v1",
            "status": status,
            "reason_code": "success" if hotspots else "codecompass_index_unavailable",
            "hotspots": hotspots,
        }

    @staticmethod
    def _candidate_files(symbol: str, workspace_dir: str | Path) -> list[str]:
        root = Path(workspace_dir)
        token = symbol.split(":")[0].split(".")[0].strip()
        if not token:
            return []
        matches = []
        for path in root.rglob("*"):
            if len(matches) >= 5:
                break
            if path.is_file() and token.lower() in path.name.lower():
                try:
                    matches.append(str(path.relative_to(root)))
                except ValueError:
                    matches.append(str(path))
        return matches

    @staticmethod
    def _suspected_layer(symbol: str) -> str:
        lower = symbol.lower()
        if any(key in lower for key in ("read", "write", "io", "disk")):
            return "io"
        if any(key in lower for key in ("copy", "mem", "alloc")):
            return "memory"
        if any(key in lower for key in ("decode", "tokens", "gpu", "cuda")):
            return "model_runtime"
        return "application"


def get_performance_hotspot_service() -> PerformanceHotspotService:
    return PerformanceHotspotService()
