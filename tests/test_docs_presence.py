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
SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _section(content: str, heading: str) -> str:
    matches = list(SECTION_PATTERN.finditer(content))
    for index, match in enumerate(matches):
        if match.group(1).strip() != heading:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        return content[start:end]
    raise AssertionError(f"Section not found: {heading}")


def test_docs_exist() -> None:
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).exists()]
    assert not missing, f"Missing docs: {missing}"


def test_active_track_inventory_points_to_existing_track_files() -> None:
    inventory = (ROOT / "docs" / "status" / "active_and_completed_tracks.md").read_text(encoding="utf-8")
    active_section = _section(inventory, "Active OSS tracks (working set)")
    matches = TRACK_ROW_PATTERN.findall(active_section)
    assert matches, "No active todo track rows found in active_and_completed_tracks.md"

    for file_name, expected_track in matches:
        payload = json.loads((ROOT / file_name).read_text(encoding="utf-8"))
        assert payload.get("track") == expected_track, (
            f"Track mismatch for {file_name}: expected {expected_track!r}, got {payload.get('track')!r}"
        )


def test_completed_documentation_track_is_archived_not_active() -> None:
    inventory = (ROOT / "docs" / "status" / "active_and_completed_tracks.md").read_text(encoding="utf-8")
    active_section = _section(inventory, "Active OSS tracks (working set)")
    completed_section = _section(inventory, "Completed / archived references")

    assert "| `todo.doc.json` |" not in active_section
    assert "| `todo.doc.json` | Completed and removed |" in completed_section
    assert not (ROOT / "todo.doc.json").exists()
    assert "| `todo.json` | `core_boundary_plugin_architecture` |" in active_section
