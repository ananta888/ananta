from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = [
    "docs/security_baseline.md",
    "docs/hub_fallback.md",
    "docs/execution_scope.md",
    "docs/artifacts_and_routing.md",
    "docs/frontend_goal_ux.md",
    "docs/status/active_and_completed_tracks.md",
    "docs/status/documentation-command-contract.json",
    "docs/status/documentation-command-usage.md",
    "docs/status/documentation-drift-decision-matrix.md",
]

TRACK_ROW_PATTERN = re.compile(r"\|\s*`(todo[^`]+\.json)`\s*\|\s*`([^`]+)`\s*\|")


def test_docs_exist() -> None:
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).exists()]
    assert not missing, f"Missing docs: {missing}"


def test_active_track_inventory_points_to_existing_track_files() -> None:
    inventory = (ROOT / "docs" / "status" / "active_and_completed_tracks.md").read_text(encoding="utf-8")
    matches = TRACK_ROW_PATTERN.findall(inventory)
    assert matches, "No todo track rows found in active_and_completed_tracks.md"

    for file_name, expected_track in matches:
        payload = json.loads((ROOT / file_name).read_text(encoding="utf-8"))
        assert payload.get("track") == expected_track, (
            f"Track mismatch for {file_name}: expected {expected_track!r}, got {payload.get('track')!r}"
        )


def test_active_track_inventory_includes_documentation_track() -> None:
    inventory = (ROOT / "docs" / "status" / "active_and_completed_tracks.md").read_text(encoding="utf-8")
    assert "| `todo.doc.json` | `documentation_code_reconciliation` |" in inventory
    assert "| `todo.json` | `core_boundary_plugin_architecture` |" in inventory
