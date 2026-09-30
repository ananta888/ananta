#!/usr/bin/env python3
"""One fail-closed release command for the semantic-media/speech program."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import jsonschema

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.services.semantic_media_program_evidence import (  # noqa: E402
    GateEvidence,
    ProgramEvidenceError,
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
from scripts.semantic_media_release_paths import OUTPUT, ROOT, SCHEMA, TODO  # noqa: E402
from scripts.semantic_media_release_gate_catalog import (  # noqa: E402
    CommandGate,
    LOCAL_GATES,
    MILESTONE_GATES,
    QA_GATES,
    task_gate_requirements,
)
from scripts.semantic_media_release_document import (  # noqa: E402
    build_release_document,
    program_config_projection,
    program_source_projection,
    _result,
    _unverified,
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


__all__ = [
    "ROOT",
    "TODO",
    "SCHEMA",
    "OUTPUT",
    "CommandGate",
    "LOCAL_GATES",
    "MILESTONE_GATES",
    "QA_GATES",
    "task_gate_requirements",
    "evaluate_container_build_manifest",
    "evaluate_sfu_artifacts",
    "evaluate_optional_bound_gate",
    "evaluate_visual_activation_artifacts",
    "collect_gates",
    "build_release_document",
    "program_source_projection",
    "program_config_projection",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
