from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_CONFIG_PATH = ROOT / "config" / "core_provider_boundary.json"
BOUNDARY_DOC_PATH = ROOT / "docs" / "architecture" / "core-boundary.md"


def test_core_boundary_config_is_readable_and_has_required_zones() -> None:
    payload = json.loads(BOUNDARY_CONFIG_PATH.read_text(encoding="utf-8"))
    zones = payload["zones"]
    assert {"core", "provider_interface", "provider_implementation", "client_adapter"} <= set(zones)
    assert payload["core_modules_for_checks"]


def test_core_modules_for_checks_exist_in_repository() -> None:
    payload = json.loads(BOUNDARY_CONFIG_PATH.read_text(encoding="utf-8"))
    for rel in payload["core_modules_for_checks"]:
        assert (ROOT / rel).exists(), f"missing boundary-scoped core module: {rel}"


def test_core_boundary_doc_references_machine_readable_config() -> None:
    text = BOUNDARY_DOC_PATH.read_text(encoding="utf-8")
    assert "config/core_provider_boundary.json" in text
    assert "Provider Interface" in text
    assert "Provider Implementation" in text
