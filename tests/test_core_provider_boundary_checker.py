from __future__ import annotations

import json
from pathlib import Path

from scripts.check_core_provider_boundaries import check_core_provider_boundaries


def _write_config(path: Path, module_path: str) -> None:
    payload = {
        "core_modules_for_checks": [module_path],
        "allowlist_import_prefixes": ["agent.providers"],
        "allowlist_string_patterns": ["provider_family"],
        "forbidden_terms": ["blender", "n8n", "opencode"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_checker_reports_synthetic_forbidden_import_and_string(tmp_path: Path) -> None:
    src = tmp_path / "agent" / "providers" / "core_like.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("import blender_bridge\nVALUE = 'opencode'\n", encoding="utf-8")
    cfg = tmp_path / "boundary.json"
    _write_config(cfg, "agent/providers/core_like.py")

    violations = check_core_provider_boundaries(root=tmp_path, config_path=cfg)
    assert any(item.violation_type == "forbidden_import" for item in violations)
    assert any(item.violation_type == "forbidden_string" for item in violations)


def test_checker_respects_allowlisted_import_prefixes(tmp_path: Path) -> None:
    src = tmp_path / "agent" / "providers" / "core_like.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("from agent.providers.interfaces import ProviderDescriptor\n", encoding="utf-8")
    cfg = tmp_path / "boundary.json"
    _write_config(cfg, "agent/providers/core_like.py")

    violations = check_core_provider_boundaries(root=tmp_path, config_path=cfg)
    assert violations == []
