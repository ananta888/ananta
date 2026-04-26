from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from tests.e2e.mock_llm import MockLLM
from worker.core.verification import build_verification_artifact
from worker.shell.command_executor import execute_command_plan
from worker.shell.command_planner import build_command_plan_artifact


def _prepare_repo(tmp_path: Path) -> Path:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "tdd_tiny_repo"
    repo = tmp_path / "repo"
    shutil.copytree(fixture, repo)
    return repo


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return str(path)


def _build_patch_artifact(*, task_id: str, capability_id: str, original: str, fixed: str) -> dict[str, Any]:
    unified_diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            fixed.splitlines(keepends=True),
            fromfile="a/calc.py",
            tofile="b/calc.py",
        )
    )
    patch_hash = hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()
    return {
        "schema": "patch_artifact.v1",
        "task_id": task_id,
        "capability_id": capability_id,
        "base_ref": "fixture",
        "patch": unified_diff,
        "patch_hash": patch_hash,
        "changed_files": ["calc.py"],
        "risk_classification": "high",
    }


def _apply_patch_with_approval(
    *,
    file_path: Path,
    patch_artifact: dict[str, Any],
    fixed_content: str,
    task_id: str,
    capability_id: str,
    context_hash: str,
    approval: dict[str, Any],
) -> dict[str, Any]:
    expected_hash = str(patch_artifact.get("patch_hash") or "")
    actual_hash = hashlib.sha256(str(patch_artifact.get("patch") or "").encode("utf-8")).hexdigest()
    if expected_hash != actual_hash:
        raise ValueError("patch_hash_mismatch")
    if str(approval.get("status") or "").strip().lower() != "approved":
        raise PermissionError("approval_not_approved")
    bindings = {
        "task_id": task_id,
        "capability_id": capability_id,
        "context_hash": context_hash,
        "patch_hash": expected_hash,
    }
    for key, expected in bindings.items():
        if str(approval.get(key) or "").strip() != expected:
            raise PermissionError(f"approval_binding_mismatch:{key}")
    file_path.write_text(fixed_content, encoding="utf-8")
    return {
        "status": "applied",
        "task_id": task_id,
        "capability_id": capability_id,
        "patch_hash": expected_hash,
        "changed_files": ["calc.py"],
        "artifact_refs": [f"patch:{expected_hash}"],
    }


def _login_admin(client) -> str:  # noqa: ANN001
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def test_tdd_tiny_repo_flow_records_red_patch_green_evidence(client, tmp_path: Path) -> None:
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    list_response = client.get("/teams/blueprints", headers=auth_header)
    assert list_response.status_code == 200
    tdd_blueprint = next(item for item in list_response.json["data"] if item["name"] == "TDD")

    instantiate_response = client.post(
        f"/teams/blueprints/{tdd_blueprint['id']}/instantiate",
        json={"name": "TDD Tiny Repo Smoke Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_snapshot"]["name"] == "TDD"

    repo = _prepare_repo(tmp_path)
    calc_path = repo / "calc.py"
    original = calc_path.read_text(encoding="utf-8")
    mock_provider_output = MockLLM().plan("TDD tiny repo absolute gap fix")
    assert mock_provider_output.goal_id.startswith("goal-")

    command_plan = build_command_plan_artifact(
        task_id="TDD-T07",
        capability_id="worker.command.plan",
        command="python3 -m unittest discover -s tests -p 'test_*.py'",
        explanation="Run red/green tests for tiny TDD repo",
        expected_effects=["Capture failing red-phase and passing green-phase evidence."],
        policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
    )
    red_result = execute_command_plan(
        repository_root=repo,
        command_plan_artifact=command_plan,
        task_id="TDD-T07",
        capability_id="worker.command.execute",
        context_hash="ctx-tdd-07",
        shell_policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
        hub_policy_decision="allow",
    )
    assert red_result["status"] == "failed"

    evidence_root = tmp_path / "evidence"
    red_path = _write_json(evidence_root / "red_test_result_artifact.json", red_result)
    fixed_content = original.replace("return a - b", "return abs(a - b)")
    calc_path.write_text(fixed_content, encoding="utf-8")
    patch_artifact = _build_patch_artifact(
        task_id="TDD-T07",
        capability_id="worker.patch.propose",
        original=original,
        fixed=fixed_content,
    )
    patch_path = _write_json(evidence_root / "patch_artifact.json", patch_artifact)
    calc_path.write_text(original, encoding="utf-8")

    # Ensure no direct mutation remains before approved patch apply.
    assert calc_path.read_text(encoding="utf-8") == original

    approval = {
        "status": "approved",
        "task_id": "TDD-T07",
        "capability_id": "worker.patch.apply",
        "context_hash": "ctx-tdd-07",
        "patch_hash": patch_artifact["patch_hash"],
    }
    apply_result = _apply_patch_with_approval(
        file_path=calc_path,
        patch_artifact=patch_artifact,
        fixed_content=fixed_content,
        task_id="TDD-T07",
        capability_id="worker.patch.apply",
        context_hash="ctx-tdd-07",
        approval=approval,
    )
    assert apply_result["status"] == "applied"

    green_result = execute_command_plan(
        repository_root=repo,
        command_plan_artifact=command_plan,
        task_id="TDD-T07",
        capability_id="worker.command.execute",
        context_hash="ctx-tdd-07",
        shell_policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
        hub_policy_decision="allow",
    )
    assert green_result["status"] == "passed"
    green_path = _write_json(evidence_root / "green_test_result_artifact.json", green_result)

    verification = build_verification_artifact(
        task_id="TDD-T07",
        test_results=[green_result],
        patch_artifact=patch_artifact,
        extra_evidence_refs=[red_path, patch_path, green_path],
    )
    assert verification["status"] == "passed"
    verification_path = _write_json(evidence_root / "verification_artifact.json", verification)

    evidence_refs = [red_path, patch_path, green_path, verification_path]
    assert evidence_refs.index(red_path) < evidence_refs.index(patch_path) < evidence_refs.index(green_path)
    smoke_report = {
        "schema": "tdd_blueprint_smoke_report.v1",
        "ok": True,
        "claims": {"red_phase_claimed": True, "green_phase_claimed": True},
        "provider_output": {
            "provider": "mock_llm_fixture",
            "task_title": mock_provider_output.task_title,
            "prompt": mock_provider_output.prompt,
        },
        "phases": {
            "red": {"status": "red_expected", "evidence_path": red_path},
            "patch": {"status": "applied", "evidence_path": patch_path, "patch_hash": patch_artifact["patch_hash"]},
            "green": {"status": "green_passed", "evidence_path": green_path},
            "degraded": {"status": "not_degraded", "reason": ""},
        },
        "verification": {"status": verification["status"], "evidence_path": verification_path},
        "evidence_refs": evidence_refs,
    }
    report_path = os.getenv("TDD_SMOKE_REPORT_PATH")
    if report_path:
        _write_json(Path(report_path), smoke_report)
