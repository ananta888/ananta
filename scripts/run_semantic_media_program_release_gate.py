#!/usr/bin/env python3
"""One fail-closed release command for the semantic-media/speech program."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import jsonschema

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.services.semantic_media_program_evidence import (  # noqa: E402
    GateEvidence,
    ProgramEvidenceError,
    assert_content_free,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    verify_bound_report,
)
from agent.services.semantic_media_rollout_policy import ROLLOUT_STAGES  # noqa: E402
from scripts.build_semantic_media_containers import (  # noqa: E402
    ContainerBuildError,
    image_digest,
    validate_build_manifest,
)

try:
    from scripts.benchmark.peer_speech_evidence_sync import CONFIG_PATH as PEER_SYNC_BENCHMARK_CONFIG
    from scripts.benchmark.peer_speech_evidence_sync import SOURCE_PATHS as PEER_SYNC_BENCHMARK_SOURCES
    from scripts.benchmark.semantic_media_program import evaluate as evaluate_performance
    from scripts.benchmark.semantic_media_program import unavailable as performance_unavailable
    from scripts.e2e.semantic_media_e2e_report import run_playwright_gate
    from scripts.e2e.semantic_sfu_failover_e2e import recompute_live_failover_evidence
    from scripts.run_semantic_media_game_day import evaluate_live as evaluate_game_day
    from scripts.run_semantic_media_game_day import unavailable as game_day_unavailable
    from scripts.run_semantic_media_supply_chain_gate import evaluate as evaluate_supply
    from scripts.run_semantic_media_supply_chain_gate import unavailable as supply_unavailable
    from scripts.run_semantic_sfu_gate import (
        DEFAULT_FAILOVER as DEFAULT_SFU_FAILOVER,
    )
    from scripts.run_semantic_sfu_gate import DEFAULT_GROUP as DEFAULT_SFU_GROUP
    from scripts.run_semantic_sfu_gate import (
        DEFAULT_LOAD as DEFAULT_SFU_LOAD,
    )
    from scripts.run_semantic_sfu_gate import (
        DEFAULT_OUTPUT as DEFAULT_SFU_REPORT,
    )
    from scripts.run_semantic_sfu_gate import (
        DEFAULT_SPIKE as DEFAULT_SFU_SPIKE,
    )
    from scripts.run_semantic_sfu_gate import (
        evidence_binding as sfu_evidence_binding,
    )
    from scripts.run_semantic_sfu_gate import (
        recompute_evidence as recompute_sfu_evidence,
    )
    from scripts.run_semantic_sfu_gate import (
        static_reasons as sfu_static_reasons,
    )
    from scripts.run_semantic_visual_gate import DEFAULT_BENCHMARK as DEFAULT_VISUAL_BENCHMARK
    from scripts.run_semantic_visual_gate import DEFAULT_LIFECYCLE_E2E as DEFAULT_VISUAL_LIFECYCLE
    from scripts.run_semantic_visual_gate import DEFAULT_OUTPUT as DEFAULT_VISUAL_REPORT
    from scripts.run_semantic_visual_gate import DEFAULT_SPIKE as DEFAULT_VISUAL_SPIKE
    from scripts.run_semantic_visual_gate import evaluate_visual_gate
except ModuleNotFoundError:  # Direct execution sets scripts/ as sys.path[0].
    from benchmark.peer_speech_evidence_sync import CONFIG_PATH as PEER_SYNC_BENCHMARK_CONFIG
    from benchmark.peer_speech_evidence_sync import SOURCE_PATHS as PEER_SYNC_BENCHMARK_SOURCES
    from benchmark.semantic_media_program import evaluate as evaluate_performance
    from benchmark.semantic_media_program import unavailable as performance_unavailable
    from e2e.semantic_media_e2e_report import run_playwright_gate
    from e2e.semantic_sfu_failover_e2e import recompute_live_failover_evidence
    from run_semantic_media_game_day import evaluate_live as evaluate_game_day
    from run_semantic_media_game_day import unavailable as game_day_unavailable
    from run_semantic_media_supply_chain_gate import evaluate as evaluate_supply
    from run_semantic_media_supply_chain_gate import unavailable as supply_unavailable
    from run_semantic_sfu_gate import (
        DEFAULT_FAILOVER as DEFAULT_SFU_FAILOVER,
    )
    from run_semantic_sfu_gate import DEFAULT_GROUP as DEFAULT_SFU_GROUP
    from run_semantic_sfu_gate import (
        DEFAULT_LOAD as DEFAULT_SFU_LOAD,
    )
    from run_semantic_sfu_gate import (
        DEFAULT_OUTPUT as DEFAULT_SFU_REPORT,
    )
    from run_semantic_sfu_gate import (
        DEFAULT_SPIKE as DEFAULT_SFU_SPIKE,
    )
    from run_semantic_sfu_gate import (
        evidence_binding as sfu_evidence_binding,
    )
    from run_semantic_sfu_gate import (
        recompute_evidence as recompute_sfu_evidence,
    )
    from run_semantic_sfu_gate import (
        static_reasons as sfu_static_reasons,
    )
    from run_semantic_visual_gate import DEFAULT_BENCHMARK as DEFAULT_VISUAL_BENCHMARK
    from run_semantic_visual_gate import DEFAULT_LIFECYCLE_E2E as DEFAULT_VISUAL_LIFECYCLE
    from run_semantic_visual_gate import DEFAULT_OUTPUT as DEFAULT_VISUAL_REPORT
    from run_semantic_visual_gate import DEFAULT_SPIKE as DEFAULT_VISUAL_SPIKE
    from run_semantic_visual_gate import evaluate_visual_gate

ROOT = _PROJECT_ROOT
TODO = ROOT / "todos/archiv/todo.ai-snake-semantic-media-speech-program.json"
SCHEMA = ROOT / "schemas/release/semantic_media_program_evidence.v1.json"
OUTPUT = ROOT / "artifacts/test-gates/semantic-media-program-evidence.json"

_RELEASE_CORE_SOURCE_PATHS = (
    "agent/services/semantic_media_program_evidence.py",
    "agent/services/semantic_media_rollout_policy.py",
    "docs/operations/semantic-media-rollout.md",
    "schemas/release/semantic_media_program_evidence.v1.json",
    "scripts/run_semantic_media_program_release_gate.py",
    "todos/archiv/todo.ai-snake-semantic-media-speech-program.json",
)
_CONFIG_PREFIXES = (".github/workflows/", "config/", "docker/", "schemas/")
_CONFIG_NAMES = frozenset({".env.example", "pyproject.toml", "playwright.config.ts"})
_CONFIG_SUFFIXES = (
    ".ini",
    ".json",
    ".lock",
    ".toml",
    ".yaml",
    ".yml",
)
_MAX_PROGRAM_SOURCE_FILES = 4_096
_IGNORED_SOURCE_PARTS = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})


@dataclass(frozen=True, slots=True)
class CommandGate:
    gate_id: str
    command: tuple[str, ...]
    cwd: Path = ROOT
    timeout_seconds: int = 600


LOCAL_GATES = (
    CommandGate("planning_dag", (sys.executable, "scripts/validate_semantic_media_speech_track.py")),
    CommandGate(
        "architecture",
        (sys.executable, "-m", "pytest", "-q", "tests/architecture/test_semantic_media_speech_boundaries.py"),
    ),
    CommandGate(
        "python_unit",
        (sys.executable, "-m", "pytest", "-q", "tests"),
        timeout_seconds=3600,
    ),
    CommandGate(
        "angular_unit",
        ("npm", "run", "test:unit"),
        ROOT / "frontend-angular",
        1800,
    ),
    CommandGate("m1_crypto", (sys.executable, "scripts/run_webrtc_crypto_gate.py")),
    CommandGate(
        "m2_relay_multi_hub",
        (sys.executable, "scripts/e2e/semantic_relay_multi_hub_e2e.py", "--execute-live"),
        timeout_seconds=300,
    ),
    CommandGate("m2_transport", (sys.executable, "scripts/run_semantic_transport_gate.py", "--verify")),
    CommandGate(
        "m4_compute",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_semantic_compute_scheduler.py",
            "tests/test_semantic_compute_worker_contract.py",
            "tests/test_semantic_result_validator.py",
        ),
    ),
    CommandGate(
        "m5_visual_safe_disabled", (sys.executable, "scripts/run_semantic_visual_gate.py", "--expect-disabled")
    ),
    CommandGate(
        "m6_speech",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/contracts/test_semantic_speech_schemas.py",
            "tests/test_semantic_speech_runtime_gate.py",
            "tests/test_semantic_speech_state_machine.py",
        ),
    ),
    CommandGate(
        "m7_data",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_speech_evidence_consent_service.py",
            "tests/test_speech_evidence_consent_api.py",
            "tests/test_speech_evidence_revocation.py",
            "tests/test_speech_evidence_retention_cleanup.py",
            "tests/test_semantic_media_background_reconcilers.py",
        ),
    ),
    CommandGate(
        "m8_training",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_speech_adaptation_training_gate.py",
            "tests/worker/test_speech_training_backend_contract.py",
            "tests/test_ml_intern_speech_eval_service.py",
        ),
    ),
    CommandGate(
        "m9_sync",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_peer_speech_sync_lifecycle.py",
            "tests/test_speech_evidence_protocol.py",
            "tests/test_speech_peer_curation_composition.py",
            "tests/security/test_peer_speech_evidence_poisoning.py",
        ),
    ),
    CommandGate(
        "m10_offline_core",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/contracts/test_speech_reconciliation_schemas.py",
            "tests/test_speech_reconciliation_state_machine.py",
            "tests/test_speech_reconciliation_budget_ledger.py",
            "tests/test_speech_reconciliation_repository.py",
            "tests/test_speech_reconciliation_repository_adapters.py",
            "tests/test_speech_reconciliation_api.py",
            "tests/test_speech_reconciliation_task_projection.py",
            "tests/test_speech_reconciliation_recovery.py",
            "tests/test_speech_reconciliation_policy.py",
        ),
    ),
    CommandGate(
        "m10_offline_worker",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/security/test_speech_reconciliation_audio_security.py",
            "tests/test_speech_reconciliation_resolution.py",
        ),
    ),
    CommandGate(
        "m10_offline_ui",
        (
            "npx",
            "vitest",
            "run",
            "src/app/services/speech-reconciliation-api.service.spec.ts",
            "src/app/features/voice/speech-reconciliation-panel.component.spec.ts",
        ),
        ROOT / "frontend-angular",
    ),
    CommandGate("qa_security", (sys.executable, "scripts/run_semantic_media_security_gate.py")),
    CommandGate(
        "qa_audit",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/architecture/test_semantic_media_atomic_audit_boundaries.py",
            "tests/test_semantic_media_audit.py",
            "tests/test_semantic_media_audit_outbox.py",
            "tests/test_semantic_media_audit_repository.py",
            "tests/test_semantic_contract_audit_integration.py",
            "tests/test_semantic_compute_atomic_audit.py",
            "tests/test_ml_intern_adapter_training_atomic_audit.py",
            "tests/test_speech_lifecycle_atomic_audit.py",
            "tests/security/test_semantic_media_observability_privacy.py",
        ),
    ),
    CommandGate(
        "qa_status_ui",
        (
            "npx",
            "vitest",
            "run",
            "src/app/features/voice/semantic-media-program-shell.component.spec.ts",
            "src/app/features/pair-view/semantic-debug-panel.component.spec.ts",
        ),
        ROOT / "frontend-angular",
    ),
    CommandGate("qa_contracts", (sys.executable, "scripts/run_semantic_media_contract_gate.py", "--execute")),
    CommandGate("qa_chaos", (sys.executable, "scripts/run_semantic_media_chaos_gate.py", "--execute")),
    CommandGate("qa_privacy", (sys.executable, "scripts/run_speech_privacy_gate.py", "--execute")),
    CommandGate(
        "backend_worker_build",
        (sys.executable, "scripts/check_semantic_media_python_build.py"),
    ),
    CommandGate("angular_build", ("npm", "run", "build"), ROOT / "frontend-angular", 900),
)

MILESTONE_GATES: Mapping[str, tuple[str, ...]] = {
    "ASMP-M0": ("planning_dag", "architecture"),
    "ASMP-M1": ("m1_crypto",),
    "ASMP-M2": ("m2_transport", "m2_relay_multi_hub"),
    "ASMP-M3": ("m3_sfu_live",),
    "ASMP-M4": ("m4_compute",),
    "ASMP-M5": ("m5_visual_activation",),
    "ASMP-M6": ("m6_speech",),
    "ASMP-M7": ("m7_data",),
    "ASMP-M8": ("m8_training", "container_builds"),
    "ASMP-M9": ("m9_sync", "m9_peer_sync_performance"),
    "ASMP-M10": (
        "m10_offline_core",
        "m10_offline_worker",
        "m10_offline_ui",
        "m10_offline",
    ),
}

QA_GATES: Mapping[str, tuple[str, ...]] = {
    "ASMP-QA-001": ("qa_security",),
    "ASMP-QA-002": ("qa_audit",),
    "ASMP-QA-003": ("qa_status_ui", "qa_accessibility", "angular_build"),
    "ASMP-QA-004": (
        "qa_contracts",
        "python_unit",
        "angular_unit",
        "backend_worker_build",
    ),
    "ASMP-QA-005": ("qa_pair_e2e",),
    "ASMP-QA-006": ("qa_group_e2e", "m3_sfu_live", "qa_chaos"),
    "ASMP-QA-007": ("qa_chaos",),
    "ASMP-QA-008": ("qa_privacy",),
    "ASMP-QA-009": ("qa_performance", "m9_peer_sync_performance"),
    "ASMP-QA-010": ("qa_supply_chain", "container_builds"),
    "ASMP-QA-011": ("qa_game_day",),
}


def task_gate_requirements(task_id: str) -> tuple[str, ...]:
    """Return the narrowest release evidence applicable to one task."""

    if task_id in QA_GATES:
        return QA_GATES[task_id]
    explicit: dict[str, tuple[str, ...]] = {
        "ASMP-BASE-001": ("planning_dag",),
        "ASMP-BASE-002": ("architecture",),
        "ASMP-BASE-003": ("planning_dag",),
        "ASMP-BASE-004": ("architecture",),
        "ASMP-BASE-005": ("qa_audit",),
        "ASMP-BASE-006": ("planning_dag",),
        "ASMP-SEC-007": ("m1_crypto", "qa_pair_e2e"),
        "ASMP-SEC-008": ("m1_crypto", "qa_group_e2e"),
        "ASMP-SEC-010": ("m1_crypto", "qa_security", "qa_pair_e2e"),
        "ASMP-TRN-007": ("m2_transport", "m2_relay_multi_hub"),
        "ASMP-TRN-010": ("m2_transport", "m2_relay_multi_hub", "qa_chaos"),
        "ASMP-SFU-001": ("qa_pair_e2e",),
        "ASMP-SFU-002": ("qa_pair_e2e",),
        "ASMP-SFU-003": ("qa_pair_e2e", "qa_performance"),
        "ASMP-SFU-004": ("m3_sfu_live",),
        "ASMP-SFU-005": ("m3_sfu_live", "container_builds"),
        "ASMP-SFU-006": ("m3_sfu_live",),
        "ASMP-SFU-007": ("m3_sfu_live", "m1_crypto"),
        "ASMP-SFU-008": ("m3_sfu_live", "qa_group_e2e"),
        "ASMP-SFU-009": ("m3_sfu_live", "qa_group_e2e"),
        "ASMP-SFU-010": ("m3_sfu_live", "qa_group_e2e", "qa_performance"),
        "ASMP-CTL-009": ("m4_compute", "qa_status_ui", "angular_build"),
        "ASMP-CTL-010": ("m4_compute", "qa_status_ui"),
        "ASMP-VIS-012": ("m5_visual_safe_disabled", "m5_visual_activation"),
        "ASMP-SPR-007": ("m6_speech", "m2_transport"),
        "ASMP-SPR-010": ("m6_speech", "qa_status_ui", "qa_pair_e2e"),
        "ASMP-SPR-012": ("m6_speech", "qa_pair_e2e"),
        "ASMP-DAT-002": ("m7_data", "qa_privacy"),
        "ASMP-DAT-010": ("m7_data", "qa_privacy"),
        "ASMP-DAT-012": ("m7_data", "qa_privacy"),
        "ASMP-ML-004": ("m8_training", "container_builds", "qa_supply_chain"),
        "ASMP-ML-005": ("m8_training", "container_builds", "qa_supply_chain"),
        "ASMP-ML-007": ("m8_training", "container_builds", "qa_chaos"),
        "ASMP-ML-010": ("m8_training", "qa_status_ui"),
        "ASMP-SYN-006": ("m9_sync", "m2_transport"),
        "ASMP-SYN-012": (
            "m9_sync",
            "m9_peer_sync_performance",
            "qa_status_ui",
            "qa_pair_e2e",
        ),
        "ASMP-OFF-007": ("m10_offline_worker", "container_builds"),
        "ASMP-OFF-008": ("m10_offline_worker",),
        "ASMP-OFF-009": ("m10_offline_core", "m10_offline"),
        "ASMP-OFF-010": (
            "m10_offline_core",
            "m10_offline_ui",
            "m10_offline",
            "qa_performance",
        ),
    }
    if task_id in explicit:
        return explicit[task_id]
    if task_id.startswith("ASMP-SEC-"):
        return ("m1_crypto",)
    if task_id.startswith("ASMP-TRN-"):
        return ("m2_transport",)
    if task_id.startswith("ASMP-CTL-"):
        return ("m4_compute",)
    if task_id.startswith("ASMP-VIS-"):
        return ("m5_visual_safe_disabled",)
    if task_id.startswith("ASMP-SPR-"):
        return ("m6_speech",)
    if task_id.startswith("ASMP-DAT-"):
        return ("m7_data",)
    if task_id.startswith("ASMP-ML-"):
        return ("m8_training",)
    if task_id.startswith("ASMP-SYN-"):
        return ("m9_sync",)
    if task_id.startswith("ASMP-OFF-"):
        return ("m10_offline_core",)
    return ()


def _result(gate_id: str, status: str, reason_codes: Sequence[str], evidence: Any) -> dict[str, Any]:
    return {
        "id": gate_id,
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "evidence_sha256": canonical_sha256(evidence),
    }


def _run_command(gate: CommandGate) -> dict[str, Any]:
    if shutil.which(gate.command[0]) is None:
        return _result(gate.gate_id, "unverified", ("gate_runtime_unavailable",), {"command": gate.gate_id})
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(gate.command), cwd=gate.cwd, capture_output=True, check=False, timeout=gate.timeout_seconds
        )
    except subprocess.TimeoutExpired:
        return _result(
            gate.gate_id, "failed", ("gate_timeout",), {"command": gate.gate_id, "timeout": gate.timeout_seconds}
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    return _result(
        gate.gate_id,
        "passed" if completed.returncode == 0 else "failed",
        () if completed.returncode == 0 else ("gate_command_failed",),
        {"command": gate.gate_id, "exit_code": completed.returncode, "duration_ms": duration_ms},
    )


def _unverified(gate_id: str, reason: str) -> dict[str, Any]:
    return _result(gate_id, "unverified", (reason,), {"gate_id": gate_id, "verified_runs": 0})


def _from_evidence(gate_id: str, evidence) -> dict[str, Any]:
    return _result(gate_id, evidence.status, evidence.reason_codes, evidence.as_document())


def evaluate_container_build_manifest(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Bind release evidence to the current source projection and local image IDs."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise ContainerBuildError("container_build_manifest_invalid_or_stale")
        resolved = validate_build_manifest(document)
        if any(image_digest(reference) != digest for reference, digest in resolved.values()):
            raise ContainerBuildError("container_build_local_image_mismatch")
    except (OSError, json.JSONDecodeError, ContainerBuildError, subprocess.TimeoutExpired):
        return (
            _result(
                "container_builds",
                "failed",
                ("container_build_evidence_invalid_or_stale",),
                {"path": path.name},
            ),
            None,
        )
    return _result("container_builds", "passed", (), document), dict(document)


def evaluate_sfu_artifacts(
    *,
    report_path: Path,
    spike_path: Path,
    load_path: Path,
    failover_path: Path,
    group_path: Path,
    root: Path = ROOT,
) -> GateEvidence:
    """Revalidate the M3 artifact against current sources and raw live runs."""

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        spike = json.loads(spike_path.read_text(encoding="utf-8"))
        load = json.loads(load_path.read_text(encoding="utf-8"))
        failover = json.loads(failover_path.read_text(encoding="utf-8"))
        group = json.loads(group_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        source_digest = source_hash(root, ("scripts/run_semantic_sfu_gate.py",))
        return unavailable_evidence(
            "m3_sfu_live",
            source_sha256=source_digest,
            config_sha256=canonical_sha256({"required_artifact_count": 5}),
            reason_code="sfu_live_evidence_unavailable",
        )
    if not all(isinstance(value, dict) for value in (report, spike, load, failover, group)):
        raise ProgramEvidenceError("sfu_gate_artifact_contract_invalid")

    expected_source, expected_config = sfu_evidence_binding(spike, load, failover, group, root=root)
    reasons = list(recompute_sfu_evidence(spike, load, failover, group))
    reasons.extend(sfu_static_reasons(root))
    required_report_fields = {
        "schema",
        "gate",
        "source_sha256",
        "config_sha256",
        "evidence_recomputed",
        "tests",
        "measurements",
        "reasons",
        "verdict",
    }
    if set(report) != required_report_fields:
        reasons.append("sfu_gate_report_shape_invalid")
    if report.get("schema") != "ananta.semantic-sfu-release-gate.v1" or report.get("gate") != "semantic-sfu":
        reasons.append("sfu_gate_report_identity_invalid")
    if report.get("source_sha256") != expected_source:
        reasons.append("sfu_gate_report_source_stale")
    if report.get("config_sha256") != expected_config:
        reasons.append("sfu_gate_report_config_stale")
    if report.get("evidence_recomputed") is not True:
        reasons.append("sfu_gate_evidence_not_recomputed")

    tests = report.get("tests")
    if not isinstance(tests, list) or not tests:
        reasons.append("sfu_gate_focused_tests_missing")
        tests = []
    for row in tests:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"command", "exit_code"}
            or not isinstance(row.get("command"), str)
            or not row.get("command")
            or isinstance(row.get("exit_code"), bool)
            or not isinstance(row.get("exit_code"), int)
        ):
            reasons.append("sfu_gate_focused_test_contract_invalid")
            continue
        if row["exit_code"] != 0:
            reasons.append("sfu_gate_focused_tests_failed")

    measurements = report.get("measurements")
    if (
        not isinstance(measurements, Mapping)
        or measurements.get("external_live_failover_verified") is not True
        or measurements.get("live_failover_browser_engine_count") != 2
    ):
        reasons.append("sfu_gate_live_failover_measurement_missing")

    recomputed_reasons = sorted(set(reasons))
    artifact_reasons = report.get("reasons")
    if not isinstance(artifact_reasons, list) or artifact_reasons != recomputed_reasons:
        recomputed_reasons.append("sfu_gate_report_decision_stale")
    expected_verdict = "pass" if not recomputed_reasons else "fail"
    if report.get("verdict") != expected_verdict:
        recomputed_reasons.append("sfu_gate_report_verdict_inconsistent")
    recomputed_reasons = sorted(set(recomputed_reasons))
    engines = spike.get("engines") if isinstance(spike.get("engines"), list) else []
    levels = load.get("levels") if isinstance(load.get("levels"), list) else []
    live_failover_verified = not recompute_live_failover_evidence(failover)
    return GateEvidence(
        gate_id="m3_sfu_live",
        status="passed" if not recomputed_reasons else "failed",
        reason_codes=tuple(recomputed_reasons),
        source_sha256=expected_source,
        config_sha256=expected_config,
        measurements={
            "artifact_binding_verified": not recomputed_reasons,
            "browser_engine_count": len(engines),
            "focused_test_count": len(tests),
            "load_level_count": len(levels),
            "external_live_failover_verified": live_failover_verified,
        },
    )


def evaluate_optional_bound_gate(
    *,
    gate_id: str,
    report_path: Path | None,
    source_paths: Sequence[Path],
    config_paths: Sequence[Path],
    unavailable_reason: str,
    root: Path = ROOT,
) -> GateEvidence:
    """Verify an optional GateEvidence-v1 report against explicit repo files."""

    if report_path is None:
        return unavailable_evidence(
            gate_id,
            source_sha256=canonical_sha256({"gate_id": gate_id, "projection": "not_configured"}),
            config_sha256=canonical_sha256({"gate_id": gate_id, "configuration": "not_configured"}),
            reason_code=unavailable_reason,
        )
    if not source_paths or not config_paths:
        raise ProgramEvidenceError("bound_gate_projection_missing")
    relative_sources = _relative_projection(source_paths, root=root)
    relative_configs = _relative_projection(config_paths, root=root)
    expected_source = source_hash(root, relative_sources)
    expected_config = source_hash(root, relative_configs)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProgramEvidenceError("bound_gate_report_unavailable") from exc
    if not isinstance(report, Mapping):
        raise ProgramEvidenceError("bound_gate_report_shape_invalid")
    return verify_bound_report(
        report,
        expected_gate_id=gate_id,
        expected_source_sha256=expected_source,
        expected_config_sha256=expected_config,
    )


def evaluate_visual_activation_artifacts(
    *,
    report_path: Path,
    spike_path: Path,
    benchmark_path: Path,
    lifecycle_path: Path,
) -> dict[str, Any]:
    """Recompute the visual decision and distinguish verified NO-GO from missing evidence."""

    try:
        spike_bytes = spike_path.read_bytes()
        benchmark_bytes = benchmark_path.read_bytes()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        spike = json.loads(spike_bytes)
        benchmark = json.loads(benchmark_bytes)
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _unverified("m5_visual_activation", "semantic_visual_evidence_unavailable")
    if not all(isinstance(value, Mapping) for value in (report, spike, benchmark, lifecycle)):
        return _result(
            "m5_visual_activation",
            "failed",
            ("semantic_visual_evidence_invalid",),
            {"activation_authorized": False},
        )
    recomputed = dict(evaluate_visual_gate(spike, benchmark, lifecycle))
    reasons = list(recomputed.get("reasons") or [])
    if benchmark.get("source_spike_sha256") != hashlib.sha256(spike_bytes).hexdigest():
        reasons.append("benchmark_spike_binding_mismatch")
        recomputed["passed"] = False
        recomputed["semantic_visual_activation"] = False
    recomputed["reasons"] = sorted(set(reasons))
    expected = {
        **recomputed,
        "inputs": {
            "spike_sha256": hashlib.sha256(spike_bytes).hexdigest(),
            "benchmark_sha256": hashlib.sha256(benchmark_bytes).hexdigest(),
            "lifecycle_e2e_sha256": hashlib.sha256(
                json.dumps(lifecycle, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
    }
    if dict(report) != expected:
        recomputed["reasons"] = sorted({*recomputed["reasons"], "semantic_visual_gate_report_stale"})
    activation_authorized = bool(
        recomputed.get("passed") is True and recomputed.get("semantic_visual_activation") is True and not reasons
    )
    return _result(
        "m5_visual_activation",
        "passed" if activation_authorized else "failed",
        tuple(recomputed["reasons"]),
        {
            "activation_authorized": activation_authorized,
            "lifecycle_e2e_verified": recomputed.get("lifecycle_e2e_passed") is True,
            "ordinary_fallback_preserved": recomputed.get("ordinary_fallback_required") is True,
            "report_sha256": canonical_sha256(report),
        },
    )


def _relative_projection(paths: Sequence[Path], *, root: Path) -> tuple[str, ...]:
    projection: list[str] = []
    for candidate in paths:
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ProgramEvidenceError("bound_gate_source_path_unsafe")
        relative = candidate.as_posix()
        if not relative or relative == ".":
            raise ProgramEvidenceError("bound_gate_source_path_unsafe")
        projection.append(relative)
    return tuple(projection)


def collect_gates(args: argparse.Namespace) -> list[dict[str, Any]]:
    gates = (
        [_run_command(gate) for gate in LOCAL_GATES]
        if args.execute_local
        else [_unverified(gate.gate_id, "local_gate_execution_not_requested") for gate in LOCAL_GATES]
    )
    sfu = evaluate_sfu_artifacts(
        report_path=args.sfu_report,
        spike_path=args.sfu_spike,
        load_path=args.sfu_load,
        failover_path=args.sfu_failover,
        group_path=args.sfu_group,
    )
    offline = evaluate_optional_bound_gate(
        gate_id="m10_offline",
        report_path=args.offline_report,
        source_paths=args.offline_source,
        config_paths=args.offline_config,
        unavailable_reason="offline_reconciliation_evidence_unavailable",
    )
    peer_sync_performance = evaluate_optional_bound_gate(
        gate_id="m9_peer_sync_performance",
        report_path=args.peer_sync_performance_report,
        source_paths=tuple(Path(value) for value in PEER_SYNC_BENCHMARK_SOURCES),
        config_paths=(Path(PEER_SYNC_BENCHMARK_CONFIG),),
        unavailable_reason="peer_sync_performance_evidence_unavailable",
    )
    gates.extend(
        [
            _from_evidence("m3_sfu_live", sfu),
            evaluate_visual_activation_artifacts(
                report_path=args.visual_report,
                spike_path=args.visual_spike,
                benchmark_path=args.visual_benchmark,
                lifecycle_path=args.visual_lifecycle,
            ),
            _from_evidence("m9_peer_sync_performance", peer_sync_performance),
            _from_evidence("m10_offline", offline),
        ]
    )
    pair = run_playwright_gate(
        gate_id="ASMP-QA-005", spec="semantic-media-pair.spec.ts", execute_live=args.execute_live_e2e
    )
    group = run_playwright_gate(
        gate_id="ASMP-QA-006", spec="semantic-media-group.spec.ts", execute_live=args.execute_live_e2e
    )
    accessibility = run_playwright_gate(
        gate_id="ASMP-QA-003-accessibility",
        spec="semantic-media-accessibility.spec.ts",
        execute_live=args.execute_live_e2e,
    )
    gates.extend(
        [
            _from_evidence("qa_pair_e2e", pair),
            _from_evidence("qa_group_e2e", group),
            _from_evidence("qa_accessibility", accessibility),
        ]
    )
    build_manifest: dict[str, Any] | None = None
    if args.execute_container_builds:
        build_command = _run_command(
            CommandGate(
                "container_builds",
                (
                    sys.executable,
                    "scripts/build_semantic_media_containers.py",
                    "--output",
                    str(args.container_build_report),
                ),
                timeout_seconds=7200,
            )
        )
        if build_command["status"] == "passed":
            build_gate, build_manifest = evaluate_container_build_manifest(args.container_build_report)
        else:
            build_gate = build_command
    elif args.container_build_report.is_file():
        build_gate, build_manifest = evaluate_container_build_manifest(args.container_build_report)
    else:
        build_gate = _unverified("container_builds", "container_build_evidence_unavailable")
    gates.append(build_gate)

    performance = (
        performance_unavailable()
        if args.performance_report is None
        else evaluate_performance(json.loads(args.performance_report.read_text(encoding="utf-8")))[0]
    )
    supply = (
        supply_unavailable()
        if args.sbom_report is None or args.scanner_report is None or build_manifest is None
        else evaluate_supply(
            json.loads(args.sbom_report.read_text(encoding="utf-8")),
            json.loads(args.scanner_report.read_text(encoding="utf-8")),
            build_manifest=build_manifest,
            as_of=args.as_of,
        )
    )
    game_day = (
        game_day_unavailable()
        if args.game_day_report is None
        else evaluate_game_day(json.loads(args.game_day_report.read_text(encoding="utf-8")))
    )
    gates.extend(
        [
            _from_evidence("qa_performance", performance),
            _from_evidence("qa_supply_chain", supply),
            _from_evidence("qa_game_day", game_day),
        ]
    )
    return gates


def build_release_document(
    *,
    gates: Sequence[Mapping[str, Any]],
    stage: str,
    todo_document: Mapping[str, Any] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    if stage not in ROLLOUT_STAGES:
        raise ProgramEvidenceError("release_stage_invalid")
    by_id = {str(row["id"]): dict(row) for row in gates}
    if len(by_id) != len(gates):
        raise ProgramEvidenceError("release_gate_duplicate")
    todo = dict(todo_document) if todo_document is not None else json.loads(TODO.read_text(encoding="utf-8"))
    milestones, task_rows = _todo_inventory(todo)
    task_results: dict[str, dict[str, Any]] = {}
    task_milestones: dict[str, str] = {}
    task_statuses: dict[str, str] = {}
    for task_id, milestone_id, declared_status in task_rows:
        task_milestones[task_id] = milestone_id
        task_statuses[task_id] = declared_status
        if task_id == "ASMP-QA-012":
            continue
        task_results[task_id] = _task_result(
            task_id,
            declared_status=declared_status,
            requirements=task_gate_requirements(task_id),
            by_id=by_id,
        )

    milestone_results: list[dict[str, Any]] = []
    for milestone in milestones:
        if milestone == "ASMP-M11":
            continue
        rows = tuple(result for task_id, result in task_results.items() if task_milestones.get(task_id) == milestone)
        row = _aggregate_rows(milestone, rows) if rows else _unverified(milestone, "milestone_task_evidence_missing")
        milestone_results.append(row)

    qa_results = {task_id: task_results[task_id] for task_id in QA_GATES if task_id in task_results}
    prerequisites_verified = all(row["status"] != "unverified" for row in milestone_results) and all(
        row["status"] != "unverified" for row in qa_results.values()
    )
    qa12_gate = _result(
        "ASMP-QA-012",
        "passed" if prerequisites_verified else "unverified",
        () if prerequisites_verified else ("release_prerequisite_evidence_incomplete",),
        {"prerequisites_verified": prerequisites_verified, "stage": stage},
    )
    qa12 = _apply_declared_task_status(
        qa12_gate,
        declared_status=task_statuses.get("ASMP-QA-012", "todo"),
    )
    qa_results["ASMP-QA-012"] = qa12
    task_results["ASMP-QA-012"] = qa12
    m11 = _aggregate_rows("ASMP-M11", tuple(qa_results.values()))
    milestone_results.append(m11)

    tasks = [task_results[task_id] for task_id, _milestone, _status in task_rows]
    decision = (
        "go"
        if qa12["status"] == "passed"
        and all(row["status"] == "passed" for row in gates)
        and all(row["status"] == "passed" for row in milestone_results)
        and all(row["status"] == "passed" for row in tasks)
        else "no_go"
    )
    reasons = sorted(
        {
            reason
            for row in (*gates, *milestone_results, *tasks)
            if row["status"] != "passed"
            for reason in row["reason_codes"]
        }
    )[:128]
    source_projection = program_source_projection(todo, root=root)
    config_projection = program_config_projection(source_projection)
    source_digest = canonical_sha256(
        {
            "files_sha256": source_hash(root, source_projection),
            # Bind the exact in-memory acceptance state used for this decision,
            # including tests that deliberately supply a projected document.
            "todo_sha256": canonical_sha256(todo),
        }
    )
    document = {
        "schema": "ananta.semantic-media-program-release-evidence.v1",
        "decision": decision,
        "rollout_stage": stage,
        "ordinary_call_action": "preserve",
        "source_sha256": source_digest,
        "config_sha256": canonical_sha256(
            {
                "stage": stage,
                "gate_ids": sorted(by_id),
                "files_sha256": source_hash(root, config_projection),
            }
        ),
        "gates": sorted((dict(row) for row in gates), key=lambda row: row["id"]),
        "milestones": sorted(milestone_results, key=lambda row: row["id"]),
        "tasks": sorted(tasks, key=lambda row: row["id"]),
        "reason_codes": reasons,
    }
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(document)
    assert_content_free(document)
    return document


def program_source_projection(todo: Mapping[str, Any], *, root: Path = ROOT) -> tuple[str, ...]:
    """Resolve the reviewed program surface from task-owned paths.

    Release output artifacts are evidence inputs/outputs and are deliberately
    excluded from the source projection. Missing source declarations and empty
    globs fail closed instead of silently shrinking the reviewed surface.
    """

    declared: set[str] = set(_RELEASE_CORE_SOURCE_PATHS)
    tasks = todo.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ProgramEvidenceError("program_task_inventory_missing")
    for task in tasks:
        if not isinstance(task, Mapping):
            raise ProgramEvidenceError("program_task_inventory_invalid")
        affected = task.get("affected_files")
        if not isinstance(affected, list) or not affected:
            raise ProgramEvidenceError("program_task_source_projection_missing")
        for raw in affected:
            relative = str(raw or "")
            candidate = Path(relative)
            if not relative or candidate.is_absolute() or ".." in candidate.parts:
                raise ProgramEvidenceError("program_source_path_unsafe")
            if candidate.parts and candidate.parts[0] == "artifacts":
                continue
            if any(character in relative for character in "*?["):
                matches = tuple(
                    path.relative_to(root).as_posix() for path in sorted(root.glob(relative)) if path.is_file()
                )
                if not matches:
                    raise ProgramEvidenceError("program_source_glob_empty")
                declared.update(matches)
            else:
                resolved = root / candidate
                if resolved.is_dir():
                    matches = tuple(
                        path.relative_to(root).as_posix()
                        for path in sorted(resolved.rglob("*"))
                        if path.is_file() and not (_IGNORED_SOURCE_PARTS & set(path.parts))
                    )
                    if not matches:
                        raise ProgramEvidenceError("program_source_directory_empty")
                    declared.update(matches)
                else:
                    declared.add(candidate.as_posix())
    projection = tuple(sorted(declared))
    if len(projection) > _MAX_PROGRAM_SOURCE_FILES:
        raise ProgramEvidenceError("program_source_projection_too_large")
    # source_hash performs the final path and existence validation.
    source_hash(root, projection)
    return projection


def program_config_projection(source_projection: Sequence[str]) -> tuple[str, ...]:
    """Select deploy/runtime policy inputs from the complete source surface."""

    selected = tuple(
        sorted(
            path
            for path in set(source_projection)
            if path in _CONFIG_NAMES
            or path.startswith(_CONFIG_PREFIXES)
            or Path(path).name.startswith("docker-compose")
            or Path(path).name.startswith("compose.")
            or Path(path).name.startswith("requirements.")
            or Path(path).name in {"package.json", "package-lock.json"}
            or path.endswith(_CONFIG_SUFFIXES)
        )
    )
    if not selected:
        raise ProgramEvidenceError("program_config_projection_missing")
    return selected


def _aggregate(identifier: str, requirements: Sequence[str], by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = tuple(
        by_id.get(requirement, _unverified(requirement, "required_gate_missing")) for requirement in requirements
    )
    return _aggregate_rows(identifier, rows)


def _aggregate_rows(identifier: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if rows and all(row["status"] == "passed" for row in rows):
        status = "passed"
        reasons: tuple[str, ...] = ()
    elif any(row["status"] == "failed" for row in rows):
        status = "failed"
        reasons = tuple(sorted({reason for row in rows for reason in row["reason_codes"]}))
    else:
        status = "unverified"
        reasons = tuple(sorted({reason for row in rows for reason in row["reason_codes"]}))
    return _result(identifier, status, reasons, [dict(row) for row in rows])


def _task_result(
    task_id: str,
    *,
    declared_status: str,
    requirements: Sequence[str],
    by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not requirements:
        gate_result = _unverified(task_id, "task_gate_mapping_missing")
    else:
        gate_result = _aggregate(task_id, requirements, by_id)
    return _apply_declared_task_status(
        gate_result,
        declared_status=declared_status,
    )


def _apply_declared_task_status(
    gate_result: Mapping[str, Any],
    *,
    declared_status: str,
) -> dict[str, Any]:
    if declared_status == "done":
        return dict(gate_result)
    gate_status = str(gate_result["status"])
    status = "failed" if gate_status == "failed" else "unverified"
    reasons = tuple(
        sorted(
            {
                *gate_result["reason_codes"],
                "task_acceptance_not_complete",
            }
        )
    )
    return _result(
        str(gate_result["id"]),
        status,
        reasons,
        {"declared_status": declared_status, "gate_result": dict(gate_result)},
    )


def _todo_inventory(todo: Any) -> tuple[list[str], list[tuple[str, str, str]]]:
    milestones: set[str] = set()
    tasks: dict[str, tuple[str, str]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            identifier = str(value.get("id") or "")
            if re.fullmatch(r"ASMP-M\d+", identifier):
                milestones.add(identifier)
            milestone_id = str(value.get("milestone_id") or "")
            if identifier.startswith("ASMP-") and milestone_id and not re.fullmatch(r"ASMP-M\d+", identifier):
                tasks[identifier] = (
                    milestone_id,
                    str(value.get("status") or "todo"),
                )
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(todo)
    return sorted(milestones), sorted(
        (task_id, milestone_id, status) for task_id, (milestone_id, status) in tasks.items()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=ROLLOUT_STAGES, default="observe_only")
    parser.add_argument("--execute-local", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute-live-e2e", action="store_true")
    parser.add_argument("--execute-container-builds", action="store_true")
    parser.add_argument(
        "--container-build-report",
        type=Path,
        default=ROOT / "artifacts/domain/semantic-media-container-builds.json",
    )
    parser.add_argument("--performance-report", type=Path)
    parser.add_argument(
        "--peer-sync-performance-report",
        type=Path,
        help="source/config-bound M9 peer evidence benchmark GateEvidence-v1 report",
    )
    parser.add_argument("--sbom-report", type=Path)
    parser.add_argument("--scanner-report", type=Path)
    parser.add_argument("--game-day-report", type=Path)
    parser.add_argument("--sfu-report", type=Path, default=DEFAULT_SFU_REPORT)
    parser.add_argument("--sfu-spike", type=Path, default=DEFAULT_SFU_SPIKE)
    parser.add_argument("--sfu-load", type=Path, default=DEFAULT_SFU_LOAD)
    parser.add_argument("--sfu-failover", type=Path, default=DEFAULT_SFU_FAILOVER)
    parser.add_argument("--sfu-group", type=Path, default=DEFAULT_SFU_GROUP)
    parser.add_argument("--visual-report", type=Path, default=DEFAULT_VISUAL_REPORT)
    parser.add_argument("--visual-spike", type=Path, default=DEFAULT_VISUAL_SPIKE)
    parser.add_argument("--visual-benchmark", type=Path, default=DEFAULT_VISUAL_BENCHMARK)
    parser.add_argument("--visual-lifecycle", type=Path, default=DEFAULT_VISUAL_LIFECYCLE)
    parser.add_argument("--offline-report", type=Path, help="optional source-bound M10 GateEvidence-v1 report")
    parser.add_argument(
        "--offline-source",
        type=Path,
        action="append",
        default=[],
        help="repo-relative M10 source path included in source_sha256; repeat for every source",
    )
    parser.add_argument(
        "--offline-config",
        type=Path,
        action="append",
        default=[],
        help="repo-relative M10 config path included in config_sha256; repeat for every config",
    )
    parser.add_argument(
        "--as-of",
        type=lambda value: __import__("datetime").date.fromisoformat(value),
        default=__import__("datetime").date.today(),
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        gates = collect_gates(args)
        document = build_release_document(gates=gates, stage=args.stage)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError, ProgramEvidenceError) as exc:
        print(
            json.dumps(
                {"decision": "no_go", "reason_code": getattr(exc, "reason_code", "release_gate_invalid")},
                sort_keys=True,
            )
        )
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({"decision": document["decision"], "reason_codes": document["reason_codes"]}, sort_keys=True))
    return 0 if document["decision"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
