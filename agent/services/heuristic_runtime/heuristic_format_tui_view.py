"""HeuristicFormatTuiView — terminal status view for the heuristic format system.

Renders a compact status table of active heuristics, validation state,
and pending candidates. Designed for use in TUI panels or CLI status output.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_HEURISTICS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "heuristics")
)

_DOMAIN_SHORT: dict[str, str] = {
    "tui_snake": "tui_snake",
    "snake_eclipse": "eclipse",
    "eclipse_snake": "eclipse",
    "chat_codecompass": "chat_cc",
    "helpcenter": "helpctr",
    "planning": "planning",
    "universal": "universal",
}


@dataclass
class HeuristicStatusRow:
    heuristic_id: str
    domain: str
    status: str
    version: str
    runtime_mode: str
    has_warnings: bool = False

    def render_line(self, width: int = 100) -> str:
        domain_short = _DOMAIN_SHORT.get(self.domain, self.domain[:8])
        flag = "!" if self.has_warnings else " "
        return (
            f"{flag} {self.heuristic_id:<48} {domain_short:<10} "
            f"{self.status:<10} {self.version:<8} {self.runtime_mode}"
        )


@dataclass
class FormatTuiViewModel:
    active_count: int = 0
    candidate_count: int = 0
    quarantine_count: int = 0
    validation_errors: int = 0
    rows: list[HeuristicStatusRow] = field(default_factory=list)
    index_version: str = ""
    catalog_dir: str = ""

    def render(self) -> str:
        lines: list[str] = []
        lines.append("─" * 80)
        lines.append("  HEURISTIC FORMAT STATUS")
        lines.append("─" * 80)
        lines.append(
            f"  Active: {self.active_count}  "
            f"Candidates: {self.candidate_count}  "
            f"Quarantine: {self.quarantine_count}  "
            f"Validation errors: {self.validation_errors}"
        )
        if self.index_version:
            lines.append(f"  Index: {self.index_version}  Catalog: {self.catalog_dir}")
        lines.append("")
        if self.rows:
            lines.append(
                f"  {'ID':<48} {'DOMAIN':<10} {'STATUS':<10} {'VERSION':<8} MODE"
            )
            lines.append("  " + "-" * 78)
            for row in self.rows:
                lines.append("  " + row.render_line())
        else:
            lines.append("  (no heuristics)")
        lines.append("─" * 80)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_count": self.active_count,
            "candidate_count": self.candidate_count,
            "quarantine_count": self.quarantine_count,
            "validation_errors": self.validation_errors,
            "index_version": self.index_version,
            "rows": [
                {
                    "heuristic_id": r.heuristic_id,
                    "domain": r.domain,
                    "status": r.status,
                    "version": r.version,
                    "runtime_mode": r.runtime_mode,
                    "has_warnings": r.has_warnings,
                }
                for r in self.rows
            ],
        }


class HeuristicFormatTuiView:
    """Builds a FormatTuiViewModel from the heuristics directory."""

    def __init__(self, base_path: str | None = None) -> None:
        self._base_path = base_path or _DEFAULT_HEURISTICS_DIR

    def build(self) -> FormatTuiViewModel:
        model = FormatTuiViewModel(catalog_dir=self._base_path)

        # Load index for active count + rows
        index_path = os.path.join(self._base_path, "index.json")
        index_data: dict[str, Any] = {}
        if os.path.isfile(index_path):
            try:
                with open(index_path, encoding="utf-8") as f:
                    index_data = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

        model.index_version = str(index_data.get("version") or "")
        entries: list[dict[str, Any]] = index_data.get("heuristics") or []

        for e in entries:
            status = str(e.get("status") or "")
            if status == "active":
                model.active_count += 1

        # Count candidates
        candidates_dir = os.path.join(self._base_path, "candidates")
        if os.path.isdir(candidates_dir):
            model.candidate_count = sum(
                1 for f in os.listdir(candidates_dir) if f.endswith(".json")
            )

        # Count quarantine
        quarantine_dir = os.path.join(self._base_path, "quarantine")
        if os.path.isdir(quarantine_dir):
            model.quarantine_count = sum(
                1 for f in os.listdir(quarantine_dir) if f.endswith(".json")
            )

        # Validate active files, count errors
        active_dir = os.path.join(self._base_path, "active")
        validation_errors = 0
        file_warning_ids: set[str] = set()
        if os.path.isdir(active_dir):
            try:
                from agent.services.heuristic_runtime.heuristic_catalog_validator import HeuristicCatalogValidator
                validator = HeuristicCatalogValidator()
                cat_result = validator.validate_directory(active_dir)
                validation_errors = cat_result.failed
                for fr in cat_result.results:
                    if fr.warnings:
                        file_warning_ids.add(fr.file)
            except Exception:
                pass
        model.validation_errors = validation_errors

        # Build rows from index
        for e in entries:
            hid = str(e.get("heuristic_id") or "")
            fname = str(e.get("file") or "")
            has_warn = os.path.basename(fname) in file_warning_ids
            model.rows.append(HeuristicStatusRow(
                heuristic_id=hid,
                domain=str(e.get("domain") or ""),
                status=str(e.get("status") or ""),
                version=str(e.get("version") or ""),
                runtime_mode=str(e.get("runtime_mode") or ""),
                has_warnings=has_warn,
            ))

        return model

    def render(self) -> str:
        return self.build().render()
