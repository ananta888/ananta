from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from agent.visual_process.task_kind_registry import ALL_TASK_KINDS, LEGACY_MAP, list_task_kinds
from scripts.generate_visual_process_assistant_baseline import (
    DEFAULT_OUTPUT,
    build_baseline,
    canonical_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate_visual_process_assistant_baseline.py"


def test_committed_baseline_is_byte_identical_to_source_projection() -> None:
    expected = canonical_bytes(build_baseline())

    assert DEFAULT_OUTPUT.is_file()
    assert DEFAULT_OUTPUT.read_bytes() == expected
    assert expected.endswith(b"\n")


def test_registry_alias_fallback_definition_and_adapter_inventory_is_closed() -> None:
    payload = build_baseline()
    records = payload["task_kind_inventory"]
    canonical = [item for item in records if item["classification"] == "canonical"]
    aliases = [item for item in records if item["classification"] == "alias"]

    assert payload["verification_status"] == "verified"
    assert payload["validation_errors"] == []
    assert {item["token"] for item in canonical} == set(ALL_TASK_KINDS)
    assert {item["token"] for item in aliases} == set(LEGACY_MAP)
    assert len(records) == len({item["token"] for item in records})
    assert {item["token"] for item in canonical} == {item["id"] for item in list_task_kinds()}
    assert all(item["frontend"]["fallback_present"] for item in canonical)
    assert all(
        item["runtime"]["execution_mode"] in {"worker_dispatch", "vp_adapter", "not_executable"} for item in canonical
    )
    assert all(
        (item["runtime"]["execution_mode"] == "vp_adapter") == item["runtime"]["adapter_active"] for item in canonical
    )
    assert all(not item["runtime"]["adapter_active"] or bool(item["runtime"]["adapter"]) for item in canonical)


def test_consumed_paths_are_unique_and_explicitly_classified() -> None:
    fields = build_baseline()["consumed_field_inventory"]
    identities = [(item["canonical_kind"], item["path"]) for item in fields]

    assert len(identities) == len(set(identities))
    assert all(item["classification"] in {"canonical", "alias", "drift"} for item in fields)
    assert all(item["path"].startswith("/") for item in fields)
    assert all(item["consumers"] for item in fields)


def test_required_contract_findings_are_verified_and_source_grounded() -> None:
    payload = build_baseline()
    findings = {item["finding_id"]: item for item in payload["contract_findings"]}

    assert findings.keys() == {
        "reranker-weight-contract",
        "embed-api-plaintext-secret",
        "effective-process-source-enum",
        "validation-issue-path",
        "drag-dirty-validation-invalidation",
    }
    assert findings["reranker-weight-contract"]["state"] == "aligned_with_read_only_alias"
    assert findings["reranker-weight-contract"]["observed"]["backend_compatibility_alias_read"] is True
    assert findings["embed-api-plaintext-secret"]["state"] == "quarantined"
    assert findings["validation-issue-path"]["state"] == "present"
    assert findings["drag-dirty-validation-invalidation"]["state"] == "atomic_command_transaction"
    for finding in findings.values():
        assert finding["verification_status"] == "verified"
        assert finding["evidence"]
        for evidence in finding["evidence"]:
            assert evidence["verification_status"] == "verified"
            assert not Path(evidence["path"]).is_absolute()
            assert (ROOT / evidence["path"]).is_file()
            assert evidence["symbol"]
            assert 1 <= evidence["line_start"] <= evidence["line_end"]


def test_baseline_has_no_volatile_or_synthetic_identity_material() -> None:
    encoded = canonical_bytes(build_baseline()).decode("utf-8")
    payload = json.loads(encoded)

    assert "generated_at" not in encoded
    assert "timestamp" not in encoded
    assert str(ROOT) not in encoded
    assert re.search(r"\b(?:SRC|RUN)_", encoded) is None
    assert payload["source_grounding"] == {
        "repository_relative_evidence_only": True,
        "generated_source_identifiers": False,
        "generated_run_identifiers": False,
    }


def test_generator_check_mode_is_directly_reproducible() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
