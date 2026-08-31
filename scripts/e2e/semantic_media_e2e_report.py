"""Shared fail-closed Playwright runner for semantic-media live evidence."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
from time import monotonic
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
)

ROOT = Path(__file__).resolve().parents[2]

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

# The pair report is reused as release evidence for the live speech and peer
# evidence paths.  Keep its source boundary explicit and reviewable: these are
# the production layers executed by the Playwright scenario plus the UI/facade
# consumers of its shared acceptance policy, not a recursive repository glob.
# A change to any listed contract, adapter or persistence guard therefore
# invalidates older evidence.
_PAIR_ANGULAR_SOURCE_PATHS = (
    "frontend-angular/angular.json",
    "frontend-angular/package.json",
    "frontend-angular/playwright.config.ts",
    "frontend-angular/tsconfig.semantic-media-e2e.json",
    "frontend-angular/src/bootstrap-ananta-application.ts",
    "frontend-angular/src/main.ts",
    "frontend-angular/src/main.semantic-media-e2e.ts",
    "frontend-angular/src/app/e2e/semantic-media-pair-live-driver.ts",
    "frontend-angular/src/app/e2e/semantic-media-pair-ui-observer.ts",
    "frontend-angular/src/app/features/voice/peer-evidence-acceptance.ts",
    "frontend-angular/src/app/features/voice/peer-evidence-sync-panel.component.ts",
    "frontend-angular/src/app/features/voice/peer-evidence-sync.facade.ts",
    "frontend-angular/src/app/features/voice/semantic-media-program-host.component.ts",
    "frontend-angular/src/app/features/voice/semantic-media-program.facade.ts",
    "frontend-angular/src/app/features/voice/semantic-speech-panel.component.ts",
    "frontend-angular/src/app/features/voice/voice-audio-capture.ts",
    "frontend-angular/src/app/services/speech-transcript-revision.store.ts",
    "frontend-angular/src/app/features/voice/voice-api.service.ts",
    "frontend-angular/src/app/features/voice/voice.models.ts",
    "frontend-angular/src/app/features/voice/voice-live-session.controller.ts",
    "frontend-angular/src/app/features/voice/voice-long-run.controller.ts",
    "frontend-angular/src/app/services/auth.interceptor.ts",
    "frontend-angular/src/app/services/hub-api-core.service.ts",
    "frontend-angular/src/app/services/semantic-speech-capture-producer.service.ts",
    "frontend-angular/src/app/services/semantic-speech-runtime-coordinator.service.ts",
    "frontend-angular/src/app/services/semantic-speech-settings.ts",
    "frontend-angular/src/app/services/semantic-speech-source-correction-api.service.ts",
    "frontend-angular/src/app/services/semantic-speech-transport.service.ts",
    "frontend-angular/src/app/services/semantic-speech-quality-controller.service.ts",
    "frontend-angular/src/app/shared/crypto/sha256.ts",
    "frontend-angular/src/app/services/speech-evidence-hub-curation.facade.ts",
    "frontend-angular/src/app/services/speech-evidence-sync-api.service.ts",
    "frontend-angular/src/app/services/speech-evidence-sync.validators.ts",
    "frontend-angular/src/app/services/webrtc-datachannel.service.ts",
    "frontend-angular/src/app/services/network-profile.service.ts",
    "frontend-angular/src/app/services/webrtc-priority-send-queue.ts",
    "frontend-angular/src/app/services/webrtc-send-operation.ts",
    "frontend-angular/src/app/services/webrtc-session.service.ts",
    "frontend-angular/src/app/services/webrtc-signaling.service.ts",
    "frontend-angular/src/app/services/webrtc-transport.service.ts",
    "frontend-angular/tests/global-setup.ts",
    "frontend-angular/tests/global-teardown.ts",
)

_PAIR_HUB_SOURCE_PATHS = (
    "agent/bootstrap/routes.py",
    "agent/bootstrap/semantic_media_services.py",
    "agent/db_models/__init__.py",
    "agent/db_models/governance.py",
    "agent/db_models/semantic_media.py",
    "agent/db_models/speech_evidence.py",
    "agent/db_models/speech_evidence_sync.py",
    "agent/repositories/semantic_media_audit_outbox.py",
    "agent/repositories/semantic_media_audit_repository.py",
    "agent/repositories/semantic_relay_repository.py",
    "agent/repositories/semantic_relay_shared_store.py",
    "agent/repositories/speech_consent_repository.py",
    "agent/repositories/speech_evidence.py",
    "agent/repositories/speech_evidence_lineage.py",
    "agent/repositories/speech_evidence_sync.py",
    "agent/repositories/webrtc_epoch_repository.py",
    "agent/repositories/webrtc_peer_key_repository.py",
    "agent/routes/semantic_media_e2e_support.py",
    "agent/routes/speech_evidence_sync.py",
    "agent/routes/voice.py",
    "agent/routes/voice_http_adapter.py",
    "agent/routes/voice_live_runs.py",
    "agent/services/ml_intern_speech_admission_policy.py",
    "agent/services/ml_intern_speech_dataset_build_service.py",
    "agent/services/ml_intern_speech_dataset_port.py",
    "agent/services/semantic_media_audit_service.py",
    "agent/services/semantic_media_feature_flags.py",
    "agent/services/semantic_relay_authorization.py",
    "agent/services/semantic_relay_composition.py",
    "agent/services/semantic_relay_limits.py",
    "agent/services/semantic_relay_observability.py",
    "agent/services/semantic_relay_rate_limiter.py",
    "agent/services/semantic_relay_service.py",
    "agent/services/semantic_speech_relay.py",
    "agent/services/share_session_relay_membership.py",
    "agent/services/share_session_service.py",
    "agent/services/speech_evidence_admission_policy.py",
    "agent/services/speech_evidence_consent_service.py",
    "agent/services/speech_evidence_curation_task_service.py",
    "agent/services/speech_evidence_encryption_port.py",
    "agent/services/speech_evidence_key_service.py",
    "agent/services/speech_evidence_offer_service.py",
    "agent/services/speech_evidence_peer_curation_composition.py",
    "agent/services/speech_evidence_poisoning_policy.py",
    "agent/services/speech_evidence_receipt_service.py",
    "agent/services/speech_evidence_store_service.py",
    "agent/services/speech_evidence_sync_composition.py",
    "agent/services/voice_governance_domain.py",
    "agent/services/voice_delegation_task_service.py",
    "agent/services/voice_idempotency_service.py",
    "agent/services/voice_live_run_correction_service.py",
    "agent/services/voice_live_run_service.py",
    "agent/services/voice_live_run_start_lease_service.py",
    "agent/services/voice_live_run_task_port.py",
    "agent/services/voice_provider.py",
    "agent/services/voice_stream_session_service.py",
    "agent/services/webrtc_epoch_service.py",
    "agent/services/webrtc_peer_identity_service.py",
    "voice_runtime/evidence_identity.py",
    "voice_runtime/evidence_quality.py",
    "voice_runtime/app.py",
    "voice_runtime/backends/provenance.py",
    "voice_runtime/backends/registry.py",
    "voice_runtime/backends/router.py",
    "voice_runtime/backends/whisper_cpp.py",
    "voice_runtime/config.py",
    "voice_runtime/execution_control.py",
    "voice_runtime/execution_policy.py",
    "voice_runtime/model_manifest.py",
    "voice_runtime/parallel.py",
    "voice_runtime/pipeline.py",
    "voice_runtime/preprocessing/audio_decode.py",
    "voice_runtime/resources.py",
    "voice_runtime/routes.py",
    "voice_runtime/streaming.py",
)

_PAIR_CONTRACT_AND_SCHEMA_SOURCE_PATHS = (
    "ananta_contracts/speech_evidence_crypto.py",
    "ananta_contracts/speech_evidence_governance.py",
    "ananta_contracts/speech_evidence_sync.py",
    "ananta_contracts/webrtc_datachannel.py",
    "migrations/versions/a9b0c1d2e3f4_add_signed_speech_evidence_offer_previews.py",
    "migrations/versions/d5e6f7a8b9c0_add_speech_evidence_sync_control_plane.py",
    "migrations/versions/d8e9f0a1b2c3_add_semantic_relay_store.py",
    "migrations/versions/e9f0a1b2c3d4_add_semantic_relay_wire_metadata.py",
    "migrations/versions/f0a1b2c3d4e5_add_speech_evidence_governance.py",
    "scripts/e2e/semantic_media_pair_e2e.py",
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


def _project_measurement_prefix(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized or "unknown"


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


def _summarize_report(raw: bytes) -> dict[str, int]:
    return _summarize_reports((("", raw),))


def _summarize_reports(
    raw_reports: Sequence[tuple[str, bytes]],
    *,
    failed_projects: Sequence[str] = (),
) -> dict[str, int]:
    reports: list[tuple[str, Mapping[str, Any]]] = []
    invalid_projects: list[str] = []
    for fallback_project, raw in raw_reports:
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            invalid_projects.append(fallback_project or "unknown")
            continue
        report = _extract_playwright_json(decoded)
        if report is None:
            invalid_projects.append(fallback_project or "unknown")
            continue
        reports.append((fallback_project, report))

    statuses: list[tuple[str, str]] = []
    browsers: set[str] = set()
    browsers.update(project for project, _raw in raw_reports if project)
    peer_product_metrics: list[dict[str, int]] = []
    group_hub_authority_metrics: list[dict[str, int]] = []
    visual_lifecycle_metrics: list[dict[str, int]] = []
    seen_metric_annotations: set[tuple[str, str]] = set()

    def walk(value: Any, inherited_project: str = "", fixed_project: str = "") -> None:
        if isinstance(value, Mapping):
            project = value.get("projectName")
            if fixed_project:
                inherited_project = fixed_project
            elif isinstance(project, str) and project:
                browsers.add(project)
                inherited_project = project
            results = value.get("results")
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, Mapping) and isinstance(result.get("status"), str):
                        statuses.append((inherited_project or "unknown", str(result["status"])))
            annotations = value.get("annotations")
            if isinstance(annotations, list):
                for annotation in annotations:
                    if not isinstance(annotation, Mapping):
                        continue
                    try:
                        metrics = json.loads(str(annotation.get("description") or ""))
                    except json.JSONDecodeError:
                        continue
                    if annotation.get("type") == "semantic-peer-product-path-v2" and (
                        isinstance(metrics, Mapping)
                        and set(metrics)
                        == {
                            "browser_processes",
                            "browser_engines",
                            "product_facade_count",
                            "product_ui_projection_count",
                            "backend_route_count",
                            "p2p_product_delivery_count",
                            "forced_relay_delivery_count",
                            "duplicate_rejection_count",
                            "reorder_recovery_count",
                            "signed_preview_visible_count",
                            "comparison_preview_visible_count",
                            "reconnect_resume_count",
                            "revoke_ack_count",
                            "hub_curation_count",
                            "hub_receipt_verified_count",
                            "hub_dataset_reservation_count",
                            "live_revision_continuity_count",
                            "plaintext_canary_probe_count",
                            "synthetic_harness_count",
                            "partial_latency_ms",
                            "partial_observation_count",
                            "segment_mode_count",
                            "final_segment_count",
                            "correction_after_final_count",
                            "accelerated_rotation_count",
                            "voice_reconnect_count",
                            "observed_stream_404_count",
                            "observed_stop_409_count",
                            "observed_chunk_413_count",
                            "backpressure_count",
                            "ordinary_fallback_count",
                            "observed_error_response_count",
                        }
                        and all(type(item) is int and item >= 0 for item in metrics.values())
                    ):
                        normalized = {str(key): int(item) for key, item in metrics.items()}
                        metric_key = (inherited_project, canonical_sha256(normalized))
                        if metric_key not in seen_metric_annotations:
                            seen_metric_annotations.add(metric_key)
                            peer_product_metrics.append(normalized)
                    if annotation.get("type") == "semantic-group-hub-authority-v2" and (
                        isinstance(metrics, Mapping)
                        and set(metrics)
                        == {
                            "participant_contexts",
                            "hub_admission_conflict_rejections",
                            "hub_admission_success_responses",
                            "hub_admission_denied_responses",
                            "hub_publication_generations",
                            "hub_published_track_count",
                            "hub_group_epoch_count",
                            "group_package_ack_count",
                            "membership_revoke_count",
                            "late_join_old_epoch_key_count",
                            "removed_member_new_epoch_key_count",
                            "independent_receiver_count",
                            "weak_receiver_fallback_count",
                            "browser_restart_recovery_count",
                            "ordinary_fallback_count",
                            "client_minted_sfu_token_count",
                            "client_generated_group_epoch_count",
                        }
                        and all(type(item) is int and item >= 0 for item in metrics.values())
                    ):
                        normalized = {str(key): int(item) for key, item in metrics.items()}
                        metric_key = (inherited_project, canonical_sha256(normalized))
                        if metric_key not in seen_metric_annotations:
                            seen_metric_annotations.add(metric_key)
                            group_hub_authority_metrics.append(normalized)
                    if annotation.get("type") == "semantic-visual-lifecycle-v1" and (
                        isinstance(metrics, Mapping)
                        and set(metrics)
                        == {
                            "browser_processes",
                            "browser_engines",
                            "scenario_count",
                            "observe_count",
                            "active_count",
                            "recovery_count",
                            "revoke_count",
                            "reconnect_count",
                            "ordinary_fallback_count",
                            "direct_peer_links",
                            "ordinary_audio_receivers",
                        }
                        and all(type(item) is int and item >= 0 for item in metrics.values())
                    ):
                        normalized = {str(key): int(item) for key, item in metrics.items()}
                        metric_key = (inherited_project, canonical_sha256(normalized))
                        if metric_key not in seen_metric_annotations:
                            seen_metric_annotations.add(metric_key)
                            visual_lifecycle_metrics.append(normalized)
            for nested in value.values():
                walk(nested, inherited_project, fixed_project)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, inherited_project, fixed_project)

    for fallback_project, report in reports:
        walk(report, fallback_project, fallback_project)

    passed = sum(status in {"passed", "expected"} for _project, status in statuses)
    skipped = sum(status == "skipped" for _project, status in statuses)
    reported_failed = len(statuses) - passed - skipped
    invalid_project_set = set(invalid_projects)
    normalized_failed_projects = {project or "unknown" for project in failed_projects}
    process_only_failures = {
        project
        for project in normalized_failed_projects
        if project not in invalid_project_set
        and not any(
            status not in {"passed", "expected", "skipped"}
            for status_project, status in statuses
            if status_project == project
        )
    }
    failed = reported_failed + len(invalid_projects) + len(process_only_failures)
    summary = {
        "executed_tests": len(statuses) - skipped,
        "passed_tests": passed,
        "failed_tests": failed,
        "skipped_tests": skipped,
        "browser_count": len(browsers),
    }
    project_names = {
        *(project for project, _status in statuses),
        *invalid_projects,
        *normalized_failed_projects,
        *browsers,
    }
    failed_project_count = 0
    for project in sorted(project_names):
        project_statuses = [status for status_project, status in statuses if status_project == project]
        project_passed = sum(status in {"passed", "expected"} for status in project_statuses)
        project_skipped = sum(status == "skipped" for status in project_statuses)
        project_failed = (
            len(project_statuses)
            - project_passed
            - project_skipped
            + invalid_projects.count(project)
            + int(project in process_only_failures)
        )
        prefix = _project_measurement_prefix(project)
        summary.update(
            {
                f"{prefix}_executed_tests": len(project_statuses) - project_skipped,
                f"{prefix}_passed_tests": project_passed,
                f"{prefix}_failed_tests": project_failed,
                f"{prefix}_skipped_tests": project_skipped,
            }
        )
        failed_project_count += int(project_failed > 0 or project_skipped > 0)
    summary["failed_project_count"] = failed_project_count
    if peer_product_metrics:
        summed_fields = (
            "product_facade_count",
            "product_ui_projection_count",
            "backend_route_count",
            "p2p_product_delivery_count",
            "forced_relay_delivery_count",
            "duplicate_rejection_count",
            "reorder_recovery_count",
            "signed_preview_visible_count",
            "comparison_preview_visible_count",
            "reconnect_resume_count",
            "revoke_ack_count",
            "hub_curation_count",
            "hub_receipt_verified_count",
            "hub_dataset_reservation_count",
            "live_revision_continuity_count",
            "plaintext_canary_probe_count",
            "synthetic_harness_count",
            "partial_observation_count",
            "segment_mode_count",
            "final_segment_count",
            "correction_after_final_count",
            "accelerated_rotation_count",
            "voice_reconnect_count",
            "observed_stream_404_count",
            "observed_stop_409_count",
            "observed_chunk_413_count",
            "backpressure_count",
            "ordinary_fallback_count",
            "observed_error_response_count",
        )
        summary.update(
            {
                "peer_product_scenario_count": len(peer_product_metrics),
                "cross_engine_process_count": min(row["browser_processes"] for row in peer_product_metrics),
                "cross_engine_count": min(row["browser_engines"] for row in peer_product_metrics),
                "partial_latency_max_ms": max(row["partial_latency_ms"] for row in peer_product_metrics),
                **{field: sum(row[field] for row in peer_product_metrics) for field in summed_fields},
            }
        )
    if group_hub_authority_metrics:
        summed_fields = (
            "hub_admission_conflict_rejections",
            "hub_admission_success_responses",
            "hub_admission_denied_responses",
            "membership_revoke_count",
            "late_join_old_epoch_key_count",
            "removed_member_new_epoch_key_count",
            "weak_receiver_fallback_count",
            "browser_restart_recovery_count",
            "ordinary_fallback_count",
            "client_minted_sfu_token_count",
            "client_generated_group_epoch_count",
        )
        summary.update(
            {
                "group_hub_authority_scenario_count": len(group_hub_authority_metrics),
                "group_participant_context_min": min(
                    row["participant_contexts"] for row in group_hub_authority_metrics
                ),
                "hub_publication_generation_min": min(
                    row["hub_publication_generations"] for row in group_hub_authority_metrics
                ),
                "hub_published_track_min": min(row["hub_published_track_count"] for row in group_hub_authority_metrics),
                "hub_group_epoch_min": min(row["hub_group_epoch_count"] for row in group_hub_authority_metrics),
                "group_package_ack_min": min(row["group_package_ack_count"] for row in group_hub_authority_metrics),
                "independent_receiver_min": min(
                    row["independent_receiver_count"] for row in group_hub_authority_metrics
                ),
                **{
                    field.removesuffix("s") + "_count"
                    if field
                    in {
                        "hub_admission_conflict_rejections",
                        "hub_admission_success_responses",
                        "hub_admission_denied_responses",
                    }
                    else field: sum(row[field] for row in group_hub_authority_metrics)
                    for field in summed_fields
                },
            }
        )
    if visual_lifecycle_metrics:
        summary.update(
            {
                "visual_lifecycle_scenario_count": len(visual_lifecycle_metrics),
                "visual_process_count": min(row["browser_processes"] for row in visual_lifecycle_metrics),
                "visual_engine_count": min(row["browser_engines"] for row in visual_lifecycle_metrics),
                "visual_scenario_min": min(row["scenario_count"] for row in visual_lifecycle_metrics),
                "visual_observe_min": min(row["observe_count"] for row in visual_lifecycle_metrics),
                "visual_active_min": min(row["active_count"] for row in visual_lifecycle_metrics),
                "visual_recovery_min": min(row["recovery_count"] for row in visual_lifecycle_metrics),
                "visual_revoke_min": min(row["revoke_count"] for row in visual_lifecycle_metrics),
                "visual_reconnect_min": min(row["reconnect_count"] for row in visual_lifecycle_metrics),
                "visual_ordinary_fallback_min": min(row["ordinary_fallback_count"] for row in visual_lifecycle_metrics),
                "visual_direct_link_min": min(row["direct_peer_links"] for row in visual_lifecycle_metrics),
                "visual_ordinary_receiver_min": min(
                    row["ordinary_audio_receivers"] for row in visual_lifecycle_metrics
                ),
            }
        )
    return summary


def _extract_playwright_json(value: str) -> Mapping[str, Any] | None:
    """Recover the JSON reporter document after inherited service logs."""

    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, Mapping) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates: list[Mapping[str, Any]] = []
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping) and (
            isinstance(parsed.get("suites"), list)
            or isinstance(parsed.get("stats"), Mapping)
            or isinstance(parsed.get("config"), Mapping)
        ):
            candidates.append(parsed)
    return candidates[-1] if candidates else None


def _source_paths(spec: str, common: tuple[str, ...]) -> tuple[str, ...]:
    additions: tuple[str, ...] = ()
    if spec == "semantic-media-pair.spec.ts":
        additions = (
            *_PAIR_ANGULAR_SOURCE_PATHS,
            *_PAIR_HUB_SOURCE_PATHS,
            *_PAIR_CONTRACT_AND_SCHEMA_SOURCE_PATHS,
        )
    elif spec == "semantic-media-group.spec.ts":
        additions = (
            "frontend-angular/angular.json",
            "frontend-angular/package.json",
            "frontend-angular/playwright.config.ts",
            "frontend-angular/tsconfig.semantic-media-e2e.json",
            "frontend-angular/src/bootstrap-ananta-application.ts",
            "frontend-angular/src/main.ts",
            "frontend-angular/src/main.semantic-media-e2e.ts",
            "frontend-angular/src/app/e2e/semantic-media-group-live-driver.ts",
            "frontend-angular/src/app/services/e2e-encryption.service.ts",
            "frontend-angular/src/app/services/livekit-sfu-room.adapter.ts",
            "frontend-angular/src/app/services/livekit-sfu-transport.service.ts",
            "frontend-angular/src/app/services/secure-key-store.service.ts",
            "frontend-angular/src/app/services/semantic-sfu-admission-api.service.ts",
            "frontend-angular/src/app/services/semantic-sfu-group-key-api.service.ts",
            "frontend-angular/src/app/services/semantic-sfu-group-key.service.ts",
            "frontend-angular/src/app/services/semantic-sfu-path-coordinator.service.ts",
            "frontend-angular/src/app/services/webrtc-group-key.service.ts",
            "frontend-angular/src/app/services/webrtc-peer-key.service.ts",
            "agent/bootstrap/routes.py",
            "agent/bootstrap/semantic_media_services.py",
            "agent/db_models/semantic_media.py",
            "agent/repositories/semantic_sfu_admission_repository.py",
            "agent/repositories/webrtc_epoch_repository.py",
            "agent/repositories/webrtc_peer_key_repository.py",
            "agent/routes/semantic_media_e2e_support.py",
            "agent/routes/semantic_sfu_admission.py",
            "agent/routes/share_sessions.py",
            "agent/services/semantic_sfu_admission_service.py",
            "agent/services/semantic_sfu_group_key_service.py",
            "agent/services/share_security_negotiation_service.py",
            "agent/services/share_session_service.py",
            "agent/services/webrtc_epoch_service.py",
            "agent/services/webrtc_group_key_authorization_service.py",
            "agent/services/webrtc_peer_identity_service.py",
            "migrations/versions/e6f7a8b9c0d1_add_semantic_sfu_admission_state.py",
        )
    elif spec == "semantic-media-accessibility.spec.ts":
        additions = (
            "frontend-angular/src/app/app.component.ts",
            "frontend-angular/src/app/features/voice/semantic-media-program-host.component.ts",
            "frontend-angular/src/app/features/voice/semantic-media-program-shell.component.html",
            "frontend-angular/src/app/features/voice/semantic-media-program-shell.component.ts",
            "frontend-angular/src/app/features/voice/semantic-media-program.facade.ts",
        )
    elif spec == "semantic-visual-lifecycle.spec.ts":
        additions = (
            "frontend-angular/src/main.ts",
            "frontend-angular/src/app/e2e/semantic-visual-lifecycle-live-driver.ts",
            "frontend-angular/src/app/services/semantic-visual-controller.service.ts",
            "frontend-angular/src/app/services/semantic-visual-fallback.service.ts",
            "frontend-angular/src/app/services/semantic-recovery.service.ts",
        )
    return (*common, *additions)


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
    environment.setdefault("E2E_VOICE_RUNTIME_URL", f"http://{_private_runtime_host()}:{voice_runtime}")
    environment.setdefault("ANANTA_E2E_FORCE_ISOLATED", "1")
    environment.setdefault("E2E_DATA_ROOT", f"/tmp/ananta-semantic-e2e-{frontend}/data")
    environment.setdefault("E2E_PID_FILE", f"/tmp/ananta-semantic-e2e-{frontend}/services.json")
    environment.setdefault("E2E_RESULTS_DIR", f"/tmp/ananta-semantic-e2e-{frontend}/results")


def _private_runtime_host() -> str:
    """Return an address accepted by the Hub's container-only SSRF policy."""

    networks = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    candidates = {str(item[4][0]) for item in socket.getaddrinfo(socket.gethostname(), 0, type=socket.SOCK_STREAM)}
    # Minimal CI hosts frequently map their hostname to 127.0.1.1 only.  A UDP
    # route probe does not send traffic, but lets the kernel select the real
    # host-side address that containers can use for the isolated voice runtime.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_probe:
            route_probe.connect(("198.18.0.1", 9))
            candidates.add(str(route_probe.getsockname()[0]))
    except OSError:
        pass
    for candidate in sorted(candidates):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if any(address in network for network in networks):
            return candidate
    raise RuntimeError("semantic_media_private_runtime_address_unavailable")


__all__ = ["run_playwright_gate"]
