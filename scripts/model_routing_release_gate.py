#!/usr/bin/env python3
"""Run and record the deterministic central-model-settings release gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv/bin/python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.is_file():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
sys.path.insert(0, str(REPO_ROOT))

from agent.services.model_routing_release_evidence_service import (  # noqa: E402
    RELEASE_EVIDENCE_SCHEMA,
    release_source_digests,
)

EVIDENCE_PATH = REPO_ROOT / "artifacts/test-gates/model-routing-release-gate.json"
GATE_COMMANDS = {
    "contract": (
        ".venv/bin/pytest -q tests/test_model_routing_contract.py "
        "tests/test_model_inventory_service.py tests/test_cli_model_inventory.py "
        "tests/test_openrouter_model_inventory_adapter.py"
    ),
    "security": (
        ".venv/bin/pytest -q tests/test_model_routing_security_policy.py "
        "tests/test_model_routing_validation_policy.py "
        "tests/test_model_routing_transfer_service.py"
    ),
    "frontend_unit": (
        "npx vitest run "
        "src/app/features/system/model-dashboard/model-dashboard.store.spec.ts "
        "src/app/features/system/model-dashboard/model-routing-migration.store.spec.ts"
    ),
    "e2e": (
        "E2E_TEST_TIMEOUT_MS=120000 E2E_EXPECT_TIMEOUT_MS=30000 "
        "npx playwright test tests/central-model-settings.spec.ts "
        "--project=chromium --workers=1 --retries=0"
    ),
}


def _run(command: str, *, cwd: Path) -> bool:
    print(f"[model-routing-release-gate] {command}", flush=True)
    return subprocess.run(
        ["bash", "-lc", command], cwd=cwd, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    ).returncode == 0


def main() -> int:
    results = {
        "contract": _run(GATE_COMMANDS["contract"], cwd=REPO_ROOT),
        "security": _run(GATE_COMMANDS["security"], cwd=REPO_ROOT),
        "frontend_unit": _run(
            GATE_COMMANDS["frontend_unit"], cwd=REPO_ROOT / "frontend-angular"
        ),
        "e2e": _run(GATE_COMMANDS["e2e"], cwd=REPO_ROOT / "frontend-angular"),
    }
    results["performance"] = results["e2e"]
    if not all(results.values()):
        print("[model-routing-release-gate] failed; existing evidence kept", file=sys.stderr)
        return 1
    payload = {
        "schema": RELEASE_EVIDENCE_SCHEMA,
        "suite_revision": "central-model-settings-v1",
        "source_digests": release_source_digests(REPO_ROOT),
        "gates": {
            gate_id: {
                "passed": passed,
                "command": (
                    GATE_COMMANDS["e2e"] if gate_id == "performance"
                    else GATE_COMMANDS[gate_id]
                ),
            }
            for gate_id, passed in sorted(results.items())
        },
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=EVIDENCE_PATH.parent,
        prefix="model-routing-release-gate.", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(EVIDENCE_PATH)
    print(f"[model-routing-release-gate] passed: {EVIDENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
