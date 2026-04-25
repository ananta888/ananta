from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = ROOT / "client_surfaces" / "vscode_extension"


def test_vscode_extension_bootstrap_files_exist() -> None:
    required = [
        EXT_ROOT / "package.json",
        EXT_ROOT / "tsconfig.json",
        EXT_ROOT / ".eslintrc.cjs",
        EXT_ROOT / "src" / "extension.ts",
        EXT_ROOT / "src" / "runtime" / "backendClient.ts",
        EXT_ROOT / "src" / "runtime" / "settings.ts",
        EXT_ROOT / "src" / "runtime" / "secretStore.ts",
        EXT_ROOT / "src" / "runtime" / "redaction.ts",
        EXT_ROOT / "src" / "views" / "statusTreeProvider.ts",
    ]
    for path in required:
        assert path.exists(), f"missing file: {path}"


def test_vscode_package_manifest_declares_runtime_contributions() -> None:
    manifest = json.loads((EXT_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = manifest.get("scripts") or {}
    contributes = manifest.get("contributes") or {}
    configuration = ((contributes.get("configuration") or {}).get("properties") or {})

    assert "ananta.checkHealth" in (manifest.get("activationEvents") or [])
    assert "compile" in scripts
    assert "test" in scripts
    assert "lint" in scripts
    assert "package" in scripts
    assert "ananta.baseUrl" in configuration
    assert "ananta.profileId" in configuration
    assert "ananta.auth.mode" in configuration
    assert "ananta.auth.secretStorageKey" in configuration
    assert "ananta.timeoutMs" in configuration

    commands = {entry.get("command") for entry in (contributes.get("commands") or [])}
    expected_commands = {
        "ananta.checkHealth",
        "ananta.submitGoal",
        "ananta.analyzeSelection",
        "ananta.reviewFile",
        "ananta.patchPlan",
        "ananta.projectNew",
        "ananta.projectEvolve",
    }
    assert expected_commands.issubset(commands)

    views = contributes.get("views") or {}
    assert "ananta" in views
    assert any(view.get("id") == "ananta.statusView" for view in views["ananta"])


def test_vscode_frontend_api_surface_map_is_backend_reuse_focused() -> None:
    payload = json.loads((ROOT / "data" / "vscode_frontend_api_surface_map.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "vscode_frontend_api_surface_map_v1"

    all_entries = []
    for entries in (payload.get("by_section") or {}).values():
        all_entries.extend(entries)
    assert all_entries, "expected at least one surface mapping entry"
    assert any(entry.get("source") == "backend_api" for entry in all_entries)
    assert all(entry.get("source") != "local_only_model" for entry in all_entries)


def test_vscode_scope_and_architecture_docs_exist() -> None:
    docs = [
        ROOT / "docs" / "vscode-plugin-scope-boundary.md",
        ROOT / "docs" / "vscode-extension-architecture.md",
        ROOT / "docs" / "vscode-extension-build-and-package.md",
    ]
    for doc in docs:
        assert doc.exists(), f"missing doc: {doc}"
