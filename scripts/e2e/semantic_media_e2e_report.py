"""Shared fail-closed Playwright runner for semantic-media live evidence."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Mapping, Sequence

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
)
from scripts.e2e.private_runtime_host import private_runtime_host
from scripts.e2e.semantic_media_e2e_sources import _source_paths
from scripts.e2e.semantic_media_e2e_summary import (
    _project_measurement_prefix,
    _summarize_report,  # noqa: F401 - compatibility re-export for tests
    _summarize_reports,
)


ROOT = Path(__file__).resolve().parents[2]
_private_runtime_host = private_runtime_host


_KEY_MATERIAL_PATTERNS = (
    re.compile(rb"-----BEGIN (?:ENCRYPTED )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(
        rb'"(?:private[_-]?key(?:_b64)?|privateKey|aes[_-]?key(?:_b64)?|aesKeyB64|'
        rb'raw[_-]?key(?:_b64)?|rawKey(?:B64)?|key[_-]?material|keyMaterial|contentKey)"'
        rb'\s*:\s*"[^"\\]{8,}"',
        re.IGNORECASE,
    ),
    re.compile(rb"\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~-]{16,}", re.IGNORECASE),
    re.compile(
        rb'"(?:access_token|refresh_token|sender_token|recipient_token)"\s*:\s*"[^"\\]{16,}"',
        re.IGNORECASE,
    ),
)


_DEFAULT_PLAYWRIGHT_TEST_TIMEOUT_MS = 60_000
_PAIR_PLAYWRIGHT_TEST_TIMEOUT_MS = 240_000
_PLAYWRIGHT_WORKER_COUNT = 1
_PROCESS_GROUP_TERMINATION_GRACE_SECONDS = 5
_PAIR_PROJECT_ISOLATION = "fresh_stack_per_project"
_SHARED_PROJECT_ISOLATION = "shared_stack"
_PAIR_RUN_SCOPED_ENVIRONMENT_KEYS = frozenset(
    {
        "ANANTA_E2E_FORCE_ISOLATED",
        "ANANTA_E2E_USE_EXISTING",
        "E2E_ALPHA_PORT",
        "E2E_ALPHA_URL",
        "E2E_BETA_PORT",
        "E2E_BETA_URL",
        "E2E_DATABASE_URL",
        "E2E_DATA_ROOT",
        "E2E_FRONTEND_URL",
        "E2E_HUB_PORT",
        "E2E_HUB_URL",
        "E2E_PID_FILE",
        "E2E_PORT",
        "E2E_RESULTS_DIR",
        "E2E_REUSE_SERVER",
        "E2E_VOICE_RUNTIME_PORT",
        "E2E_VOICE_RUNTIME_URL",
    }
)


@dataclass(frozen=True, slots=True)
class _PlaywrightProjectRun:
    project_name: str
    returncode: int
    raw_report: bytes
    captured_output: bytes


class _PlaywrightProjectExecutionError(RuntimeError):
    def __init__(self, project_name: str, stdout: bytes, stderr: bytes, reason_code: str) -> None:
        super().__init__(reason_code)
        self.project_name = project_name
        self.stdout = stdout
        self.stderr = stderr
        self.reason_code = reason_code


class _PlaywrightProjectTimeout(_PlaywrightProjectExecutionError):
    def __init__(self, project_name: str, stdout: bytes, stderr: bytes) -> None:
        super().__init__(project_name, stdout, stderr, "live_browser_gate_timeout")


class _PlaywrightProjectCleanupError(_PlaywrightProjectExecutionError):
    def __init__(self, project_name: str, stdout: bytes, stderr: bytes) -> None:
        super().__init__(project_name, stdout, stderr, "live_browser_gate_cleanup_failed")


class _ProcessGroupCleanupTimeout(TimeoutError):
    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        super().__init__("playwright_process_group_cleanup_timeout")
        self.stdout = stdout
        self.stderr = stderr


def playwright_gate_config(
    *,
    spec: str,
    required_browsers: Sequence[str] = ("chromium", "firefox"),
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Return the single source of truth for source-bound browser budgets."""

    playwright_test_timeout_ms = (
        _PAIR_PLAYWRIGHT_TEST_TIMEOUT_MS
        if spec == "semantic-media-pair.spec.ts"
        else _DEFAULT_PLAYWRIGHT_TEST_TIMEOUT_MS
    )
    return {
        "spec": spec,
        "required_browsers": list(required_browsers),
        "live_driver_required": spec
        in {
            "semantic-media-pair.spec.ts",
            "semantic-media-group.spec.ts",
            "semantic-visual-lifecycle.spec.ts",
        },
        "timeout_seconds": timeout_seconds,
        "playwright_test_timeout_ms": playwright_test_timeout_ms,
        "project_isolation": (
            _PAIR_PROJECT_ISOLATION if spec == "semantic-media-pair.spec.ts" else _SHARED_PROJECT_ISOLATION
        ),
        "worker_count": _PLAYWRIGHT_WORKER_COUNT,
        "retain_evidence_artifacts": False,
        "termination_grace_seconds": _PROCESS_GROUP_TERMINATION_GRACE_SECONDS,
    }


def _as_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value or "").encode("utf-8", "replace")


def _playwright_project_environment(
    base_environment: Mapping[str, str],
    *,
    project_name: str,
    required_browsers: Sequence[str],
    isolated_project: bool,
    run_directory: Path,
) -> dict[str, str]:
    """Build one bounded environment without leaking pair state across projects."""

    environment = dict(base_environment)
    if isolated_project:
        for key in _PAIR_RUN_SCOPED_ENVIRONMENT_KEYS:
            environment.pop(key, None)
        environment["E2E_BROWSERS"] = project_name
    else:
        environment["E2E_BROWSERS"] = ",".join(required_browsers)
    _configure_isolated_ports(environment)
    environment["CORS_ORIGINS"] = str(
        environment.get("E2E_FRONTEND_URL")
        or f"http://127.0.0.1:{environment['E2E_PORT']}"
    )
    environment["E2E_DATA_ROOT"] = str(run_directory / "data")
    environment["E2E_PID_FILE"] = str(run_directory / "services.json")
    environment["E2E_RESULTS_DIR"] = str(run_directory / "results")
    # Evidence execution is deliberately serial. Ambient values and explicit
    # overrides must not turn two isolated project runs into shared concurrency.
    environment["E2E_WORKERS"] = str(_PLAYWRIGHT_WORKER_COUNT)
    environment["E2E_RETAIN_EVIDENCE_ARTIFACTS"] = "0"
    return environment


def _signal_process_group(process_id: int, requested_signal: signal.Signals) -> bool:
    try:
        os.killpg(process_id, requested_signal)
    except ProcessLookupError:
        return False
    return True


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: int,
) -> tuple[bytes, bytes]:
    """Bound and reap the isolated Playwright process group."""

    _signal_process_group(process.pid, signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired as error:
        partial_stdout = _as_bytes(error.stdout)
        partial_stderr = _as_bytes(error.stderr)
        if not _signal_process_group(process.pid, signal.SIGKILL):
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=grace_seconds)
        except subprocess.TimeoutExpired as kill_error:
            _signal_process_group(process.pid, signal.SIGKILL)
            raise _ProcessGroupCleanupTimeout(
                _as_bytes(kill_error.stdout) or partial_stdout,
                _as_bytes(kill_error.stderr) or partial_stderr,
            ) from kill_error
        return (_as_bytes(stdout) or partial_stdout, _as_bytes(stderr) or partial_stderr)
    # The Playwright leader may have exited while service descendants still own
    # its session. A final group kill is idempotent and prevents retained Hub,
    # browser or voice-runtime processes after both successful and failed runs.
    _signal_process_group(process.pid, signal.SIGKILL)
    return _as_bytes(stdout), _as_bytes(stderr)


def _run_playwright_project(
    *,
    spec: str,
    project_name: str,
    environment: Mapping[str, str],
    run_directory: Path,
    deadline: float,
    termination_grace_seconds: int,
) -> _PlaywrightProjectRun:
    """Execute one Playwright process and return its in-memory report surfaces."""

    command = ["npx", "playwright", "test", f"tests/{spec}", "--reporter=json"]
    if project_name:
        command.extend(("--project", project_name))
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _PlaywrightProjectTimeout(project_name, b"", b"")
    project_environment = dict(environment)
    report_path = run_directory / "playwright-report.json"
    project_environment["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(report_path)
    process = subprocess.Popen(
        command,
        cwd=ROOT / "frontend-angular",
        env=project_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout = b""
    stderr = b""
    timeout_error: subprocess.TimeoutExpired | None = None
    cleanup_output = (b"", b"")
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        timeout_error = error
    finally:
        try:
            cleanup_output = _terminate_process_group(
                process,
                grace_seconds=termination_grace_seconds,
            )
        except _ProcessGroupCleanupTimeout as error:
            raise _PlaywrightProjectCleanupError(
                project_name,
                error.stdout,
                error.stderr,
            ) from error
    if timeout_error is not None:
        raise _PlaywrightProjectTimeout(
            project_name,
            cleanup_output[0] or _as_bytes(timeout_error.stdout),
            cleanup_output[1] or _as_bytes(timeout_error.stderr),
        ) from timeout_error
    stdout = _as_bytes(stdout)
    stderr = _as_bytes(stderr)
    raw_report = report_path.read_bytes() if report_path.is_file() else stdout
    return _PlaywrightProjectRun(
        project_name=project_name,
        returncode=int(process.returncode if process.returncode is not None else 1),
        raw_report=raw_report,
        captured_output=b"\n".join((stdout, stderr)),
    )


def run_playwright_gate(
    *,
    gate_id: str,
    spec: str,
    execute_live: bool,
    required_browsers: tuple[str, ...] = ("chromium", "firefox"),
    timeout_seconds: int = 900,
    environment_overrides: Mapping[str, str] | None = None,
) -> GateEvidence:
    config = playwright_gate_config(
        spec=spec,
        required_browsers=required_browsers,
        timeout_seconds=timeout_seconds,
    )
    playwright_test_timeout_ms = int(config["playwright_test_timeout_ms"])
    termination_grace_seconds = int(config["termination_grace_seconds"])
    source_digest = source_hash(
        ROOT,
        _source_paths(
            spec,
            (
                "agent/services/semantic_media_program_evidence.py",
                "scripts/e2e/semantic_media_e2e_report.py",
                "scripts/e2e/semantic_media_e2e_sources.py",
                "scripts/e2e/semantic_media_e2e_summary.py",
                f"frontend-angular/tests/{spec}",
            ),
        ),
    )
    config_digest = canonical_sha256(config)
    if not execute_live:
        return unavailable_evidence(
            gate_id,
            source_sha256=source_digest,
            config_sha256=config_digest,
            reason_code="live_browser_evidence_not_requested",
        )
    if shutil.which("npx") is None:
        return unavailable_evidence(
            gate_id,
            source_sha256=source_digest,
            config_sha256=config_digest,
            reason_code="playwright_runtime_unavailable",
        )
    if os.name != "posix" or not hasattr(os, "killpg"):
        return unavailable_evidence(
            gate_id,
            source_sha256=source_digest,
            config_sha256=config_digest,
            reason_code="playwright_process_group_control_unavailable",
        )
    environment = dict(os.environ)
    if environment_overrides:
        environment.update({str(key): str(value) for key, value in environment_overrides.items()})
    environment["RUN_SEMANTIC_MEDIA_LIVE_E2E"] = "1"
    # The pair flow deliberately exercises native whisper.cpp finalization,
    # segment correction, curation and revocation in one test. Its bounded
    # budget is longer than the generic UI timeout and is part of the evidence
    # config digest above; ambient environment values cannot weaken or hide it.
    environment["E2E_TEST_TIMEOUT_MS"] = str(playwright_test_timeout_ms)
    environment["E2E_WORKERS"] = str(_PLAYWRIGHT_WORKER_COUNT)
    environment["E2E_RETAIN_EVIDENCE_ARTIFACTS"] = "0"
    privacy_canaries: tuple[str, ...] = ()
    if spec == "semantic-media-pair.spec.ts":
        privacy_canaries = (
            f"ANANTA_DIRECT_CANARY_{secrets.token_hex(24)}",
            f"ANANTA_RELAY_CANARY_{secrets.token_hex(24)}",
        )
        environment["ANANTA_PAIR_DIRECT_CANARY"] = privacy_canaries[0]
        environment["ANANTA_PAIR_RELAY_CANARY"] = privacy_canaries[1]
    isolated_pair_projects = spec == "semantic-media-pair.spec.ts"
    project_names = required_browsers if isolated_pair_projects else ("",)
    deadline = monotonic() + timeout_seconds
    project_runs: list[_PlaywrightProjectRun] = []
    try:
        with tempfile.TemporaryDirectory(prefix="ananta-semantic-playwright-") as temporary:
            run_directory = Path(temporary)
            for project_index, project_name in enumerate(project_names):
                project_run_directory = run_directory / (
                    f"{project_index:02d}-{_project_measurement_prefix(project_name or 'matrix')}"
                )
                project_run_directory.mkdir()
                project_environment = _playwright_project_environment(
                    environment,
                    project_name=project_name,
                    required_browsers=required_browsers,
                    isolated_project=isolated_pair_projects,
                    run_directory=project_run_directory,
                )
                project_runs.append(
                    _run_playwright_project(
                        spec=spec,
                        project_name=project_name,
                        environment=project_environment,
                        run_directory=project_run_directory,
                        deadline=deadline,
                        termination_grace_seconds=termination_grace_seconds,
                    )
                )
    except _PlaywrightProjectExecutionError as error:
        failure_output = b"\n".join((error.stdout, error.stderr))
        summary = _summarize_reports(
            tuple((run.project_name, run.raw_report) for run in project_runs),
            failed_projects=(error.project_name,),
        )
        privacy_surfaces = tuple(surface for run in project_runs for surface in (run.captured_output, run.raw_report))
        canary_leaks, key_material_matches = _privacy_leak_counts(
            privacy_canaries,
            *privacy_surfaces,
            failure_output,
        )
        summary["canary_leak_count"] = canary_leaks
        summary["crypto_canary_match_count"] = key_material_matches
        failure_reasons = [
            error.reason_code,
            f"live_browser_project_{_project_measurement_prefix(error.project_name)}_failed",
        ]
        if canary_leaks:
            failure_reasons.append("pair_plaintext_canary_leaked")
        if key_material_matches:
            failure_reasons.append("pair_key_material_leaked")
        return GateEvidence(
            gate_id,
            "failed",
            tuple(sorted(failure_reasons)),
            source_digest,
            config_digest,
            summary,
        )
    failed_projects = tuple(run.project_name for run in project_runs if run.returncode != 0)
    summary = _summarize_reports(
        tuple((run.project_name, run.raw_report) for run in project_runs),
        failed_projects=failed_projects,
    )
    privacy_surfaces = tuple(surface for run in project_runs for surface in (run.captured_output, run.raw_report))
    canary_leaks, key_material_matches = _privacy_leak_counts(
        privacy_canaries,
        *privacy_surfaces,
    )
    summary["canary_leak_count"] = canary_leaks
    summary["crypto_canary_match_count"] = key_material_matches
    reasons: list[str] = []
    if failed_projects:
        reasons.append("live_browser_gate_failed")
    for project_name in required_browsers:
        prefix = _project_measurement_prefix(project_name)
        if summary.get(f"{prefix}_failed_tests", 0) or summary.get(f"{prefix}_skipped_tests", 0):
            reasons.append(f"live_browser_project_{prefix}_failed")
    if summary["executed_tests"] < len(required_browsers):
        reasons.append("live_browser_coverage_missing")
    if summary["failed_tests"] or summary["skipped_tests"]:
        reasons.append("live_browser_result_not_passed")
    if summary["browser_count"] < len(required_browsers):
        reasons.append("live_browser_engine_missing")
    if canary_leaks:
        reasons.append("pair_plaintext_canary_leaked")
    if key_material_matches:
        reasons.append("pair_key_material_leaked")
    if spec == "semantic-media-pair.spec.ts" and (
        summary.get("peer_product_scenario_count", 0) < len(required_browsers)
        or summary.get("cross_engine_process_count", 0) < 2
        or summary.get("cross_engine_count", 0) < 2
        or min(
            summary.get("product_facade_count", 0),
            summary.get("product_ui_projection_count", 0),
            summary.get("backend_route_count", 0),
            summary.get("p2p_product_delivery_count", 0),
            summary.get("forced_relay_delivery_count", 0),
            summary.get("duplicate_rejection_count", 0),
            summary.get("reorder_recovery_count", 0),
            summary.get("signed_preview_visible_count", 0),
            summary.get("comparison_preview_visible_count", 0),
            summary.get("reconnect_resume_count", 0),
            summary.get("revoke_ack_count", 0),
            summary.get("hub_curation_count", 0),
            summary.get("hub_receipt_verified_count", 0),
            summary.get("hub_dataset_reservation_count", 0),
            summary.get("live_revision_continuity_count", 0),
            summary.get("partial_observation_count", 0),
            summary.get("segment_mode_count", 0),
            summary.get("accelerated_rotation_count", 0),
            summary.get("voice_reconnect_count", 0),
            summary.get("observed_stream_404_count", 0),
            summary.get("observed_stop_409_count", 0),
            summary.get("observed_chunk_413_count", 0),
            summary.get("backpressure_count", 0),
            summary.get("ordinary_fallback_count", 0),
        )
        < len(required_browsers)
        or summary.get("final_segment_count", 0) < len(required_browsers)
        or summary.get("correction_after_final_count", 0) < summary.get("final_segment_count", 0)
        or summary.get("partial_latency_max_ms", 0) <= 0
        or summary.get("partial_latency_max_ms", 0) >= 250
        or summary.get("synthetic_harness_count", 1) != 0
        or summary.get("observed_error_response_count", 0) < 4 * len(required_browsers)
        or summary.get("plaintext_canary_probe_count", 0) < 2 * len(required_browsers)
        or summary.get("canary_leak_count", 0) != 0
        or summary.get("crypto_canary_match_count", 0) != 0
    ):
        reasons.append("cross_engine_peer_sync_evidence_missing")
    if spec == "semantic-media-group.spec.ts" and (
        summary.get("group_hub_authority_scenario_count", 0) < len(required_browsers)
        or summary.get("group_participant_context_min", 0) < 7
        or summary.get("hub_admission_conflict_rejection_count", 0) < len(required_browsers)
        or summary.get("hub_admission_success_response_count", 0) < 8 * len(required_browsers)
        or summary.get("hub_admission_denied_response_count", 0) < 2 * len(required_browsers)
        or summary.get("hub_publication_generation_min", 0) < 2
        or summary.get("hub_published_track_min", 0) < 2
        or summary.get("hub_group_epoch_min", 0) < 2
        or summary.get("group_package_ack_min", 0) < 10
        or summary.get("membership_revoke_count", 0) < len(required_browsers)
        or summary.get("independent_receiver_min", 0) < 2
        or summary.get("weak_receiver_fallback_count", 0) < len(required_browsers)
        or summary.get("browser_restart_recovery_count", 0) < len(required_browsers)
        or summary.get("ordinary_fallback_count", 0) < len(required_browsers)
        or summary.get("late_join_old_epoch_key_count", 0) != 0
        or summary.get("removed_member_new_epoch_key_count", 0) != 0
        or summary.get("client_minted_sfu_token_count", 0) != 0
        or summary.get("client_generated_group_epoch_count", 0) != 0
    ):
        reasons.append("group_hub_authority_evidence_missing")
    if spec == "semantic-visual-lifecycle.spec.ts" and (
        summary.get("visual_lifecycle_scenario_count", 0) < len(required_browsers)
        or summary.get("visual_process_count", 0) < 2
        or summary.get("visual_engine_count", 0) < 2
        or min(
            summary.get("visual_scenario_min", 0),
            summary.get("visual_observe_min", 0),
            summary.get("visual_active_min", 0),
            summary.get("visual_recovery_min", 0),
            summary.get("visual_revoke_min", 0),
            summary.get("visual_reconnect_min", 0),
            summary.get("visual_ordinary_fallback_min", 0),
        )
        < 6
        or summary.get("visual_direct_link_min", 0) < 2
        or summary.get("visual_ordinary_receiver_min", 0) < 2
    ):
        reasons.append("visual_lifecycle_evidence_missing")
    evidence = GateEvidence(
        gate_id=gate_id,
        status="passed" if not reasons else "failed",
        reason_codes=tuple(sorted(set(reasons))),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements=summary,
    )
    artifact_canary_leaks, artifact_key_matches = _privacy_leak_counts(
        privacy_canaries,
        json.dumps(evidence.as_document(), sort_keys=True, separators=(",", ":")),
    )
    if artifact_canary_leaks or artifact_key_matches:
        sanitized = dict(summary)
        sanitized["canary_leak_count"] += artifact_canary_leaks
        sanitized["crypto_canary_match_count"] += artifact_key_matches
        artifact_reasons = set(reasons)
        if artifact_canary_leaks:
            artifact_reasons.add("pair_plaintext_canary_leaked")
        if artifact_key_matches:
            artifact_reasons.add("pair_key_material_leaked")
        return GateEvidence(
            gate_id=gate_id,
            status="failed",
            reason_codes=tuple(sorted(artifact_reasons)),
            source_sha256=source_digest,
            config_sha256=config_digest,
            measurements=sanitized,
        )
    return evidence


def _privacy_leak_counts(canaries: tuple[str, ...], *surfaces: bytes | str) -> tuple[int, int]:
    encoded_surfaces = tuple(
        value if isinstance(value, bytes) else value.encode("utf-8", "replace") for value in surfaces
    )
    canary_hits = sum(surface.count(canary.encode("utf-8")) for canary in canaries for surface in encoded_surfaces)
    key_material_hits = sum(
        len(pattern.findall(surface)) for pattern in _KEY_MATERIAL_PATTERNS for surface in encoded_surfaces
    )
    return canary_hits, key_material_hits


def _configure_isolated_ports(environment: dict[str, str]) -> None:
    """Avoid borrowing or terminating a developer's running local stack."""

    reserved: set[int] = set()

    def port() -> int:
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                candidate = int(probe.getsockname()[1])
            if candidate not in reserved:
                reserved.add(candidate)
                return candidate

    frontend = int(environment.get("E2E_PORT") or port())
    hub = int(environment.get("E2E_HUB_PORT") or port())
    alpha = int(environment.get("E2E_ALPHA_PORT") or port())
    beta = int(environment.get("E2E_BETA_PORT") or port())
    voice_runtime = int(environment.get("E2E_VOICE_RUNTIME_PORT") or port())
    environment.setdefault("E2E_PORT", str(frontend))
    environment.setdefault("E2E_HUB_URL", f"http://127.0.0.1:{hub}")
    environment.setdefault("E2E_ALPHA_URL", f"http://127.0.0.1:{alpha}")
    environment.setdefault("E2E_BETA_URL", f"http://127.0.0.1:{beta}")
    environment.setdefault("E2E_VOICE_RUNTIME_URL", f"http://{private_runtime_host()}:{voice_runtime}")
    environment.setdefault("ANANTA_E2E_FORCE_ISOLATED", "1")
    environment.setdefault("E2E_DATA_ROOT", f"/tmp/ananta-semantic-e2e-{frontend}/data")
    environment.setdefault("E2E_PID_FILE", f"/tmp/ananta-semantic-e2e-{frontend}/services.json")
    environment.setdefault("E2E_RESULTS_DIR", f"/tmp/ananta-semantic-e2e-{frontend}/results")


__all__ = ["run_playwright_gate"]
