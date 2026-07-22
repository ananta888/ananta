#!/usr/bin/env python3
"""Run the versioned SFU broadcast CI gate matrix with bounded cleanup."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_gate_common import (  # noqa: E402
    SfuBroadcastGateError,
    atomic_write_report,
    canonical_sha256,
    digest_paths,
    read_bounded_json,
    run_bounded_command,
    scan_content_free_document,
)

DEFAULT_MANIFEST = ROOT / "config/release/sfu_broadcast_gate_manifest.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-gate-matrix.json"
SAFE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/@+-]+$")


def validate_manifest(document: Mapping[str, Any]) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    if document.get("schema") != "ananta.sfu-broadcast-gate-manifest.v1":
        raise SfuBroadcastGateError("gate_matrix_schema_invalid")
    if not isinstance(document.get("manifest_version"), int) or document["manifest_version"] < 1:
        raise SfuBroadcastGateError("gate_matrix_version_invalid")
    if scan_content_free_document(document):
        raise SfuBroadcastGateError("gate_matrix_content_scan_failed")
    execution = document.get("execution")
    if not isinstance(execution, Mapping):
        raise SfuBroadcastGateError("gate_matrix_execution_missing")
    stages = execution.get("stages")
    gates = execution.get("gates")
    sources = execution.get("source_files")
    if not isinstance(stages, Mapping) or not stages:
        raise SfuBroadcastGateError("gate_matrix_stages_invalid")
    if not isinstance(gates, list) or not gates:
        raise SfuBroadcastGateError("gate_matrix_gates_invalid")
    if not isinstance(sources, list) or not sources:
        raise SfuBroadcastGateError("gate_matrix_sources_invalid")
    seen: set[str] = set()
    for gate in gates:
        if not isinstance(gate, Mapping):
            raise SfuBroadcastGateError("gate_matrix_gate_contract_invalid")
        gate_id = gate.get("gate_id")
        if not isinstance(gate_id, str) or gate_id in seen:
            raise SfuBroadcastGateError("gate_matrix_gate_id_invalid")
        seen.add(gate_id)
        if gate.get("stage") not in stages:
            raise SfuBroadcastGateError("gate_matrix_gate_stage_invalid")
        command = gate.get("command")
        if not isinstance(command, list) or not command or any(
            not isinstance(item, str) or not item or len(item) > 1024 for item in command
        ):
            raise SfuBroadcastGateError("gate_matrix_command_invalid")
        artifact = gate.get("artifact")
        if not isinstance(artifact, str) or SAFE_PATH.fullmatch(artifact) is None:
            raise SfuBroadcastGateError("gate_matrix_artifact_path_invalid")
        if gate.get("artifact_mode") not in {"observed", "external"}:
            raise SfuBroadcastGateError("gate_matrix_artifact_mode_invalid")
        if not isinstance(gate.get("artifact_schema"), str):
            raise SfuBroadcastGateError("gate_matrix_artifact_schema_invalid")
        for key in ("timeout_seconds", "cpu_seconds_max", "memory_bytes_max", "retention_days"):
            value = gate.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise SfuBroadcastGateError("gate_matrix_limit_invalid")
        cleanup = gate.get("cleanup")
        if not isinstance(cleanup, Mapping) or not isinstance(cleanup.get("commands"), list):
            raise SfuBroadcastGateError("gate_matrix_cleanup_invalid")
        if cleanup.get("strategy") != "none" and not cleanup.get("commands"):
            raise SfuBroadcastGateError("gate_matrix_cleanup_command_missing")
        for command_row in cleanup.get("commands", []):
            if not isinstance(command_row, list) or not command_row or any(
                not isinstance(item, str) or not item for item in command_row
            ):
                raise SfuBroadcastGateError("gate_matrix_cleanup_command_invalid")
        if not isinstance(cleanup.get("deadline_seconds"), int) or cleanup["deadline_seconds"] < 1:
            raise SfuBroadcastGateError("gate_matrix_cleanup_deadline_invalid")
    return execution, list(gates)


def build_plan(
    document: Mapping[str, Any],
    *,
    stage: str,
    root: Path = ROOT,
) -> dict[str, Any]:
    execution, gates = validate_manifest(document)
    if stage not in execution["stages"]:
        raise SfuBroadcastGateError("gate_matrix_stage_unknown")
    selected = [gate for gate in gates if gate["stage"] == stage]
    if not selected:
        raise SfuBroadcastGateError("gate_matrix_stage_empty")
    source_sha256 = digest_paths(root, execution["source_files"])
    return {
        "schema": "ananta.sfu-broadcast-gate-plan.v1",
        "manifest_version": document["manifest_version"],
        "manifest_sha256": canonical_sha256(document),
        "source_sha256": source_sha256,
        "stage": stage,
        "operator_approval_required": execution["stages"][stage]["operator_approval_required"],
        "gates": [
            {
                "gate_id": gate["gate_id"],
                "task_ids": sorted(gate["task_ids"]),
                "artifact": gate["artifact"],
                "artifact_schema": gate["artifact_schema"],
                "timeout_seconds": gate["timeout_seconds"],
                "cleanup_strategy": gate["cleanup"]["strategy"],
                "retention_days": gate["retention_days"],
                "requires_real_backend": gate["requires_real_backend"],
            }
            for gate in sorted(selected, key=lambda item: item["gate_id"])
        ],
    }


def _cleanup(
    gate: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
) -> tuple[bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    complete = True
    cleanup = gate["cleanup"]
    commands = cleanup["commands"]
    per_command = max(1, int(cleanup["deadline_seconds"]) // max(1, len(commands)))
    for command in commands:
        result = run_bounded_command(
            command, cwd=ROOT, env=environment, timeout_seconds=per_command,
            cpu_seconds_max=per_command,
            memory_bytes_max=min(int(gate["memory_bytes_max"]), 1073741824),
        )
        passed = result.exit_code == 0 and not result.timed_out
        complete = complete and passed
        rows.append({
            "command_sha256": canonical_sha256(command),
            "passed": passed,
            "timed_out": result.timed_out,
            "elapsed_ms": result.elapsed_ms,
        })
    return complete, rows


def _run_gate(
    gate: Mapping[str, Any],
    *,
    manifest_sha256: str,
    source_sha256: str,
    environment: Mapping[str, str],
    operator_approved: bool,
) -> dict[str, Any]:
    reasons: set[str] = set()
    result = None
    cleanup_complete = False
    cleanup_rows: list[dict[str, Any]] = []
    try:
        if gate.get("operator_approval_required") is True and not operator_approved:
            reasons.add("gate_matrix_operator_approval_missing")
        else:
            result = run_bounded_command(
                gate["command"], cwd=ROOT, env=environment,
                timeout_seconds=int(gate["timeout_seconds"]),
                cpu_seconds_max=int(gate["cpu_seconds_max"]),
                memory_bytes_max=int(gate["memory_bytes_max"]),
            )
            if result.timed_out:
                reasons.add("gate_matrix_command_timeout")
            if result.exit_code != 0:
                reasons.add("gate_matrix_command_failed")
            artifact_path = ROOT / gate["artifact"]
            if gate["artifact_mode"] == "observed":
                observation = {
                    "schema": gate["artifact_schema"],
                    "gate_id": gate["gate_id"],
                    "status": "passed" if not reasons else "failed",
                    "manifest_sha256": manifest_sha256,
                    "source_sha256": source_sha256,
                    "command_sha256": canonical_sha256(gate["command"]),
                    "resource_usage": {
                        "elapsed_ms": result.elapsed_ms,
                        "cpu_seconds": result.cpu_seconds,
                        "peak_rss_bytes": result.peak_rss_bytes,
                    },
                    "reason_codes": sorted(reasons),
                }
                atomic_write_report(artifact_path, observation)
            if not artifact_path.is_file():
                reasons.add("gate_matrix_required_artifact_missing")
            else:
                artifact = read_bounded_json(artifact_path)
                if artifact.get("schema") != gate["artifact_schema"]:
                    reasons.add("gate_matrix_artifact_schema_mismatch")
                if artifact.get("status") != "passed":
                    reasons.add("gate_matrix_artifact_not_passed")
    finally:
        cleanup_complete, cleanup_rows = _cleanup(gate, environment=environment)
        if not cleanup_complete:
            reasons.add("gate_matrix_cleanup_failed")
    return {
        "gate_id": gate["gate_id"],
        "status": "passed" if not reasons else "failed",
        "reason_codes": sorted(reasons),
        "command": {
            "timed_out": result.timed_out if result else False,
            "elapsed_ms": result.elapsed_ms if result else 0,
            "cpu_seconds": result.cpu_seconds if result else 0,
            "peak_rss_bytes": result.peak_rss_bytes if result else 0,
        },
        "cleanup_complete": cleanup_complete,
        "cleanup": cleanup_rows,
        "artifact": gate["artifact"],
    }


def execute(
    document: Mapping[str, Any],
    *,
    stage: str,
    operator_approved: bool,
) -> dict[str, Any]:
    execution, gates = validate_manifest(document)
    plan = build_plan(document, stage=stage)
    if execution["stages"][stage]["operator_approval_required"] and not operator_approved:
        return {
            "schema": "ananta.sfu-broadcast-gate-matrix-result.v1",
            "gate_id": "SFB-GATE-015",
            "status": "failed",
            "activation_allowed": False,
            "stage": stage,
            "manifest_sha256": plan["manifest_sha256"],
            "source_sha256": plan["source_sha256"],
            "reason_codes": ["gate_matrix_operator_approval_missing"],
            "gates": [],
        }
    token = canonical_sha256({
        "manifest": plan["manifest_sha256"], "source": plan["source_sha256"], "stage": stage,
    })[:20]
    environment = dict(os.environ)
    environment["ANANTA_SFU_GATE_OWNERSHIP_TOKEN"] = token
    environment["COMPOSE_PROJECT_NAME"] = f"ananta-sfu-gate-{token}"
    selected = [gate for gate in gates if gate["stage"] == stage]
    rows = [
        _run_gate(
            gate, manifest_sha256=plan["manifest_sha256"],
            source_sha256=plan["source_sha256"], environment=environment,
            operator_approved=operator_approved,
        )
        for gate in selected
    ]
    reasons = sorted({
        reason for row in rows for reason in row["reason_codes"]
    })
    return {
        "schema": "ananta.sfu-broadcast-gate-matrix-result.v1",
        "gate_id": "SFB-GATE-015",
        "status": "passed" if not reasons else "failed",
        "activation_allowed": False,
        "stage": stage,
        "manifest_sha256": plan["manifest_sha256"],
        "source_sha256": plan["source_sha256"],
        "reason_codes": reasons,
        "gates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--operator-approved", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        manifest = read_bounded_json(args.manifest)
        if args.plan_only:
            report = build_plan(manifest, stage=args.stage)
        else:
            report = execute(
                manifest, stage=args.stage,
                operator_approved=args.operator_approved,
            )
    except (SfuBroadcastGateError, KeyboardInterrupt) as exc:
        report = {
            "schema": "ananta.sfu-broadcast-gate-matrix-result.v1",
            "gate_id": "SFB-GATE-015",
            "status": "failed",
            "activation_allowed": False,
            "stage": args.stage,
            "reason_codes": [
                exc.reason_code if isinstance(exc, SfuBroadcastGateError)
                else "gate_matrix_cancelled"
            ],
            "gates": [],
        }
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("status") == "passed" or args.plan_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
