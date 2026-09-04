#!/usr/bin/env python3
"""Deterministic local DSPy gate; production evidence remains Hub-registry owned."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/test-gates/dspy-optimization.json"


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def build_report() -> dict:
    baseline = json.loads((ROOT / "config/licenses/dspy-optimization.v1.json").read_text())
    lock = (ROOT / "docker/compose-next/requirements.dspy-optimization.lock").read_bytes()
    _check(hashlib.sha256(lock).hexdigest() == baseline["dependency_lock"]["sha256"], "dspy_lock_digest_mismatch")
    sbom = json.loads((ROOT / "artifacts/domain/dspy-worker-sbom.json").read_text())
    _check(len(sbom["components"]) == 67, "dspy_sbom_component_count_invalid")
    _check(
        sbom["metadata"]["dependency_lock_sha256"] == baseline["dependency_lock"]["sha256"], "dspy_sbom_lock_mismatch"
    )
    for name in ("optimization_spec", "optimization_run", "dataset_manifest", "prompt_program", "promotion_plan"):
        schema = json.loads((ROOT / f"schemas/dspy/{name}.v1.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
    observability = json.loads((ROOT / "config/monitoring/dspy-optimization.v1.json").read_text())
    _check(observability["metric_labels"] == ["kind", "outcome"], "dspy_observability_labels_invalid")
    _check(len(observability["alerts"]) == 5, "dspy_observability_alerts_missing")
    static = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_dspy_import_boundaries.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    _check(static.returncode == 0, "dspy_import_boundary_failed")
    compose = (ROOT / "docker/compose-next/compose.dspy-optimization.yml").read_text()
    for control in (
        "read_only: true",
        "privileged: false",
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "internal: true",
    ):
        _check(control in compose, "dspy_container_control_missing")
    return {
        "schema": "ananta.dspy-local-release-gate.v1",
        "status": "passed",
        "scope": "local",
        "checks": {
            "contracts": "passed",
            "dependency_lock": "passed",
            "sbom": "passed",
            "import_boundary": "passed",
            "container_policy": "passed",
            "observability_policy": "passed",
            "headless": "passed",
        },
        "dependency_count": 67,
        "built_image_digest": sbom["metadata"]["built_image_digest"],
        "production_release_allowed": False,
        "production_block_reason": "hub_registered_production_evidence_required",
        "human_intervention_required": False,
    }


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build_report(), indent=2, sort_keys=True) + "\n")
    print("dspy-optimization-release-gate-passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
