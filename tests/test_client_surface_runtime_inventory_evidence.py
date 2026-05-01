from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "data" / "client_surface_runtime_inventory.json"
STATUS_PATH = ROOT / "data" / "client_surface_runtime_status.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _surface_map(inventory: dict) -> dict[str, dict]:
    return {str(entry.get("surface")): entry for entry in list(inventory.get("surfaces") or []) if entry.get("surface")}


def _runtime_paths(surface_entry: dict) -> list[str]:
    evidence = dict(surface_entry.get("evidence") or {})
    return [str(path) for path in list(evidence.get("runtime") or [])]


def test_runtime_mvp_surfaces_have_real_runtime_evidence() -> None:
    inventory = _load_json(INVENTORY_PATH)
    status = _load_json(STATUS_PATH)
    surfaces = _surface_map(inventory)
    surface_status = dict(status.get("surface_status") or {})

    for surface_name, declared_status in surface_status.items():
        if declared_status not in {"runtime_mvp", "runtime_complete"}:
            continue
        entry = surfaces.get(surface_name)
        assert entry is not None, f"missing inventory entry for {surface_name}"
        assert entry.get("classification") == "real_implementation", (
            f"{surface_name} is {declared_status} but classification={entry.get('classification')}"
        )
        runtime_evidence = _runtime_paths(entry)
        assert runtime_evidence, f"{surface_name} must list runtime evidence"
        assert any(not path.endswith(".keep") for path in runtime_evidence), f"{surface_name} runtime evidence is .keep-only"
        assert any(not path.startswith("docs/") for path in runtime_evidence), f"{surface_name} runtime evidence is docs-only"
        assert any("agent/services/" not in path for path in runtime_evidence), (
            f"{surface_name} runtime evidence is foundation-service-only"
        )
        for rel_path in runtime_evidence:
            assert (ROOT / rel_path).exists(), f"runtime evidence path missing: {surface_name} -> {rel_path}"


def test_surface_statuses_align_with_expected_artifact_evidence() -> None:
    status = _load_json(STATUS_PATH)
    surface_status = dict(status.get("surface_status") or {})

    assert surface_status.get("tui_surface") == "runtime_mvp"
    assert (ROOT / "client_surfaces/tui_runtime/ananta_tui/__main__.py").exists()
    assert (ROOT / "scripts/smoke_tui_runtime.py").exists()

    assert surface_status.get("nvim_plugin") == "runtime_mvp"
    assert (ROOT / "client_surfaces/nvim_runtime/plugin/ananta.vim").exists()
    assert (ROOT / "scripts/smoke_nvim_runtime.py").exists()

    assert surface_status.get("vim_plugin") == "deferred"
    assert not (ROOT / "client_surfaces/vim_compat/plugin").exists() or not any(
        (ROOT / "client_surfaces/vim_compat/plugin").glob("*.vim")
    )

    assert surface_status.get("eclipse_plugin") == "runtime_mvp"
    assert (ROOT / "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml").exists()
    assert (ROOT / "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/META-INF/MANIFEST.MF").exists()
    assert (ROOT / "scripts/smoke_eclipse_runtime_bootstrap.py").exists()
    assert (ROOT / "scripts/run_eclipse_ui_golden_path.py").exists()
    assert (ROOT / "docker/eclipse-ui-e2e/Dockerfile").exists()
    ui_report = _load_json(ROOT / "ci-artifacts/eclipse/eclipse-ui-golden-path-report.json")
    assert "p2_install_from_update_site" in {str(item.get("check_id")) for item in list(ui_report.get("checks") or [])}
    assert ui_report.get("skipped") is False
    assert ui_report.get("runtime_complete_claim_allowed") is False


def test_tui_smoke_evidence_is_referenced_in_inventory_and_checklist() -> None:
    inventory = _load_json(INVENTORY_PATH)
    surfaces = _surface_map(inventory)
    tui_entry = surfaces["tui_surface"]
    runtime_evidence = _runtime_paths(tui_entry)
    checklist = (ROOT / "docs" / "editor-tui-smoke-checklists.md").read_text(encoding="utf-8")

    assert "scripts/smoke_tui_runtime.py" in runtime_evidence
    assert "scripts/smoke_client_golden_paths.py" in runtime_evidence
    assert "python3 scripts/smoke_tui_runtime.py" in checklist
    assert "task board" in checklist.lower()
    assert "artifact list/detail" in checklist.lower()
