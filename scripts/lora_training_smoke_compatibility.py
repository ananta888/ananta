"""Compatibility-matrix and repeated-run attestations for LoRA smoke tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

_REQUIRED_UNSLOTH_RUNS = 3


def compatibility_matrix_entry(
    matrix_path: Path | None,
    entry_id: str | None,
) -> dict[str, Any]:
    if matrix_path is None or not str(entry_id or "").strip():
        return {
            "status": "not_run",
            "reason_code": "compatibility_matrix_entry_not_configured",
        }
    try:
        raw = matrix_path.read_bytes()
        matrix = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {
            "status": "not_run",
            "reason_code": "compatibility_matrix_unavailable",
        }
    entries = matrix.get("entries") if isinstance(matrix, Mapping) else None
    if not isinstance(entries, list) or matrix.get("schema") != "ananta.unsloth-gpu-compatibility-matrix.v1":
        return {
            "status": "failed",
            "reason_code": "compatibility_matrix_invalid",
        }
    selected = next(
        (
            dict(candidate)
            for candidate in entries
            if isinstance(candidate, Mapping) and candidate.get("id") == entry_id
        ),
        None,
    )
    if selected is None:
        return {
            "status": "not_run",
            "reason_code": "compatibility_matrix_entry_unknown",
        }
    return {
        "status": "selected",
        "entry": selected,
        "entry_id": entry_id,
        "matrix_sha256": hashlib.sha256(raw).hexdigest(),
    }


def compatibility_attestation(
    selection: Mapping[str, Any],
    *,
    probe: Mapping[str, Any],
    versions: Mapping[str, Any],
    image_attestation: Mapping[str, Any],
    required_runs: int,
    completed_runs: int | None = None,
) -> dict[str, Any]:
    if selection.get("status") != "selected":
        return dict(selection)
    entry = dict(selection.get("entry") or {})
    reasons: list[str] = []
    expected_python = str(entry.get("python") or "")
    if expected_python and str(versions.get("python") or "") != expected_python:
        reasons.append("compatibility_python_mismatch")
    approved_model_basenames = tuple(str(value) for value in entry.get("approved_model_basenames") or () if str(value))
    if approved_model_basenames and str(probe.get("model_basename") or "") not in approved_model_basenames:
        reasons.append("compatibility_model_not_approved")
    packages = dict(versions.get("packages") or {})
    expected_packages = dict(entry.get("packages") or {})
    for package, expected in sorted(expected_packages.items()):
        observed = packages.get(package)
        if observed != expected:
            reasons.append(f"compatibility_{package.replace('-', '_')}_mismatch")
    expected_cuda = str(entry.get("cuda_runtime") or "")
    if expected_cuda and str(probe.get("cuda_runtime") or "") != expected_cuda:
        reasons.append("compatibility_cuda_runtime_mismatch")
    minimum_driver = str(entry.get("minimum_nvidia_driver") or "")
    gpu_rows = list(probe.get("gpu") or [])
    observed_driver = str(gpu_rows[0].get("driver") or "") if gpu_rows and isinstance(gpu_rows[0], Mapping) else ""
    if minimum_driver and (not observed_driver or version_tuple(observed_driver) < version_tuple(minimum_driver)):
        reasons.append("compatibility_nvidia_driver_mismatch")
    if image_attestation.get("runtime_image_digest_supplied") is not True:
        reasons.append("runtime_image_digest_missing")
    if required_runs != int(entry.get("required_deterministic_runs") or 0):
        reasons.append("compatibility_required_run_count_mismatch")
    if completed_runs is not None and completed_runs != required_runs:
        reasons.append("deterministic_run_count_incomplete")
    status = "passed" if not reasons else ("not_run" if reasons == ["runtime_image_digest_missing"] else "failed")
    return {
        "schema": "ananta.unsloth-profile-attestation.v1",
        "status": status,
        "reason_codes": reasons,
        "profile_id": selection.get("entry_id"),
        "matrix_sha256": selection.get("matrix_sha256"),
        "runtime_image_digest": image_attestation.get("runtime_image_digest"),
        "build_input_sha256": image_attestation.get("build_input_sha256"),
        "required_runs": required_runs,
        "completed_runs": completed_runs,
        "observed": {
            "packages": packages,
            "cuda_runtime": probe.get("cuda_runtime"),
            "nvidia_driver": observed_driver or None,
        },
        "formats": list(entry.get("formats") or []),
    }


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(candidate) for candidate in re.findall(r"\d+", str(value or ""))[:4])


def aggregate_nvidia_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    compatibility_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    required_stages = (
        "training",
        "export",
        "training_evaluation",
        "adapter_evaluation",
        "promotion",
        "runtime_load",
        "rollback",
        "tamper_negative_paths",
    )
    run_attestations: list[dict[str, Any]] = []
    stage_coverage: dict[str, Any] = {}
    for stage in required_stages:
        stage_results = [dict(run.get("platform_stage_coverage", {}).get(stage) or {}) for run in runs]
        passed = bool(stage_results) and all(result.get("status") == "passed" for result in stage_results)
        stage_coverage[stage] = {
            "status": "passed" if passed else "failed",
            "run_statuses": [result.get("status", "missing") for result in stage_results],
            "run_reason_codes": [result.get("reason_code") for result in stage_results],
        }
    for index, run in enumerate(runs, start=1):
        coverage = dict(run.get("platform_stage_coverage") or {})
        digest_payload = {
            "run_index": index,
            "status": run.get("status"),
            "reason_code": run.get("reason_code"),
            "platform_stage_coverage": coverage,
            "dataset_sha256": run.get("dataset_sha256"),
            "base_model_sha256": run.get("base_model_sha256"),
            "export_evidence": run.get("export_evidence"),
        }
        run_attestations.append(
            {
                "run_index": index,
                "status": run.get("status", "failed"),
                "reason_code": run.get("reason_code"),
                "chain_sha256": coverage.get("chain_sha256"),
                "stage_results": {
                    stage: {
                        "status": dict(coverage.get(stage) or {}).get("status", "missing"),
                        "reason_code": dict(coverage.get(stage) or {}).get("reason_code"),
                        "diagnostic": dict(coverage.get(stage) or {}).get("diagnostic"),
                    }
                    for stage in required_stages
                },
                "attestation_sha256": hashlib.sha256(
                    json.dumps(
                        digest_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    passed_runs = sum(run.get("status") == "passed" for run in runs)
    all_passed = (
        len(runs) == _REQUIRED_UNSLOTH_RUNS
        and passed_runs == _REQUIRED_UNSLOTH_RUNS
        and compatibility_attestation.get("status") == "passed"
        and all(stage.get("status") == "passed" for stage in stage_coverage.values())
    )
    any_failed = any(run.get("status") == "failed" for run in runs) or any(
        stage.get("status") == "failed" for stage in stage_coverage.values()
    )
    status = "passed" if all_passed else ("failed" if any_failed else "not_run")
    result: dict[str, Any] = {
        "status": status,
        "backend": "unsloth",
        "deterministic_run_count": passed_runs,
        "required_deterministic_runs": _REQUIRED_UNSLOTH_RUNS,
        "compatibility_attestation": dict(compatibility_attestation),
        "platform_stage_coverage": stage_coverage,
        "runs": run_attestations,
    }
    if not all_passed:
        result["reason_code"] = "unsloth_gpu_run_failed" if any_failed else "deterministic_run_count_incomplete"
    return result
