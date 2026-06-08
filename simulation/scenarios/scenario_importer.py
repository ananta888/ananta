"""Emergence-style Scenario Importer (SIM-028) + Import Report/Attribution (SIM-029).

Loads ScenarioConfig from external formats:
  - JSON / YAML file
  - Emergence v1 export format (partial compatibility)
  - Dict / raw payload

Produces an ImportReport with attribution metadata.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simulation.models.scenario import ScenarioConfig


@dataclass
class ImportReport:
    source: str
    format_detected: str
    imported_at: float
    scenario_name: str
    agent_count: int
    location_count: int
    law_count: int
    warnings: list[str] = field(default_factory=list)
    attribution: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "format_detected": self.format_detected,
            "imported_at": self.imported_at,
            "scenario_name": self.scenario_name,
            "agent_count": self.agent_count,
            "location_count": self.location_count,
            "law_count": self.law_count,
            "warnings": self.warnings,
            "attribution": self.attribution,
        }


class ScenarioImporter:
    """Loads ScenarioConfig from disk or dict and returns an ImportReport."""

    def from_file(self, path: str | Path) -> tuple[ScenarioConfig, ImportReport]:
        p = Path(path)
        raw_text = p.read_text(encoding="utf-8")
        fmt = "json"
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            try:
                import yaml
                data = yaml.safe_load(raw_text)
                fmt = "yaml"
            except ImportError:
                raise ValueError("YAML not installed; install pyyaml for YAML import")

        return self._parse(data, source=str(p), fmt=fmt)

    def from_dict(self, data: dict[str, Any], source: str = "inline") -> tuple[ScenarioConfig, ImportReport]:
        return self._parse(data, source=source, fmt="dict")

    def _parse(self, data: dict[str, Any], source: str,
                fmt: str) -> tuple[ScenarioConfig, ImportReport]:
        warnings: list[str] = []
        attribution: dict[str, Any] = {}

        # Detect Emergence v1 format
        if "emergence_version" in data or "world" in data:
            data, attribution, extra_warnings = self._translate_emergence_v1(data)
            warnings.extend(extra_warnings)
            fmt = "emergence_v1"

        # Pull attribution metadata from top-level
        for key in ("author", "source_url", "license", "created_at", "version"):
            if key in data:
                attribution[key] = data.pop(key)

        try:
            scenario = ScenarioConfig.model_validate(data)
        except Exception as exc:
            raise ValueError(f"Invalid scenario format: {exc}") from exc

        report = ImportReport(
            source=source,
            format_detected=fmt,
            imported_at=time.time(),
            scenario_name=scenario.name,
            agent_count=len(scenario.agents),
            location_count=len(scenario.locations),
            law_count=len(scenario.laws),
            warnings=warnings,
            attribution=attribution,
        )
        return scenario, report

    def _translate_emergence_v1(self, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Translate Emergence v1 format to our ScenarioConfig dict."""
        warnings: list[str] = []
        attribution: dict[str, Any] = {}
        world = data.get("world", data)

        translated: dict[str, Any] = {
            "name": data.get("name", world.get("name", "imported")),
            "description": data.get("description", "Imported from Emergence v1"),
            "seed": data.get("seed", 42),
        }

        # Agents
        agents = []
        for ag in world.get("agents", []):
            agents.append({
                "id": ag.get("id", ag.get("name", "unknown")),
                "name": ag.get("name", ag.get("id", "unknown")),
                "role": ag.get("role", "citizen"),
                "location_id": ag.get("location", ag.get("location_id", "default")),
                "starting_inventory": ag.get("inventory", {}),
            })
        translated["agents"] = agents

        # Locations
        locations = []
        for loc in world.get("locations", []):
            locations.append({
                "id": loc.get("id", loc.get("name", "default")),
                "name": loc.get("name", loc.get("id", "default")),
            })
        translated["locations"] = locations

        # Laws
        laws = []
        for law in world.get("laws", []):
            laws.append({
                "id": law.get("id", "law"),
                "description": law.get("description", ""),
                "forbidden_actions": law.get("forbidden_actions", []),
                "penalty": law.get("penalty", "reputation_loss"),
                "severity": float(law.get("severity", 0.5)),
            })
        translated["laws"] = laws

        if not locations:
            warnings.append("no locations found; run may fail without a location")

        attribution["emergence_version"] = data.get("emergence_version", "1.x")
        return translated, attribution, warnings
