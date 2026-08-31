from __future__ import annotations

import json
from pathlib import Path

from agent.services.semantic_media_program_evidence import source_hash
from scripts.e2e import semantic_media_e2e_report
from scripts.e2e.semantic_media_e2e_report import (
    _privacy_leak_counts,
    _source_paths,
    _summarize_report,
)

_PAIR_COMMON_SOURCE_PATHS = (
    "agent/services/semantic_media_program_evidence.py",
    "scripts/e2e/semantic_media_e2e_report.py",
    "frontend-angular/tests/semantic-media-pair.spec.ts",
)


class _FakeProcess:
    def __init__(
        self,
        *,
        process_id: int,
        returncode: int,
        outcomes: list[tuple[bytes, bytes] | BaseException],
    ) -> None:
        self.pid = process_id
        self.returncode = returncode
        self._outcomes = outcomes
        self.timeouts: list[float] = []
        self.kill_count = 0

    def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
        self.timeouts.append(timeout)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self) -> None:
        self.kill_count += 1


def _product_metrics(**overrides: int) -> dict[str, int]:
    value = {
        "browser_processes": 2,
        "browser_engines": 2,
        "product_facade_count": 1,
        "product_ui_projection_count": 1,
        "backend_route_count": 14,
        "p2p_product_delivery_count": 1,
        "forced_relay_delivery_count": 1,
        "duplicate_rejection_count": 1,
        "reorder_recovery_count": 1,
        "signed_preview_visible_count": 1,
        "comparison_preview_visible_count": 1,
        "reconnect_resume_count": 1,
        "revoke_ack_count": 1,
        "hub_curation_count": 1,
        "hub_receipt_verified_count": 1,
        "hub_dataset_reservation_count": 1,
        "live_revision_continuity_count": 1,
        "plaintext_canary_probe_count": 2,
        "synthetic_harness_count": 0,
        "partial_latency_ms": 80,
        "partial_observation_count": 1,
        "segment_mode_count": 1,
        "final_segment_count": 2,
        "correction_after_final_count": 2,
        "accelerated_rotation_count": 1,
        "voice_reconnect_count": 1,
        "observed_stream_404_count": 1,
        "observed_stop_409_count": 1,
        "observed_chunk_413_count": 1,
        "backpressure_count": 1,
        "ordinary_fallback_count": 1,
        "observed_error_response_count": 4,
    }
    value.update(overrides)
    return value


def test_playwright_summary_rejects_legacy_simulation_metrics() -> None:
    report = {
        "suites": [
            {
                "projectName": "chromium",
                "specs": [
                    {
                        "annotations": [
                            {
                                "type": "semantic-pair-metrics-v1",
                                "description": json.dumps(
                                    {
                                        "partial_latency_ms": 12,
                                        "final_count": 1,
                                        "correction_count": 1,
                                        "failure_state_count": 6,
                                        "reason_count": 0,
                                    }
                                ),
                            }
                        ],
                        "tests": [{"results": [{"status": "passed"}]}],
                    }
                ],
            },
            {
                "projectName": "firefox",
                "specs": [
                    {
                        "annotations": [
                            {
                                "type": "semantic-pair-metrics-v1",
                                "description": json.dumps(
                                    {
                                        "partial_latency_ms": 18,
                                        "final_count": 1,
                                        "correction_count": 1,
                                        "failure_state_count": 6,
                                        "reason_count": 0,
                                    }
                                ),
                            }
                        ],
                        "tests": [{"results": [{"status": "passed"}]}],
                    }
                ],
            },
        ]
    }
    summary = _summarize_report(json.dumps(report).encode())
    assert summary == {
        "executed_tests": 2,
        "passed_tests": 2,
        "failed_tests": 0,
        "skipped_tests": 0,
        "browser_count": 2,
        "chromium_executed_tests": 1,
        "chromium_passed_tests": 1,
        "chromium_failed_tests": 0,
        "chromium_skipped_tests": 0,
        "firefox_executed_tests": 1,
        "firefox_passed_tests": 1,
        "firefox_failed_tests": 0,
        "firefox_skipped_tests": 0,
        "failed_project_count": 0,
    }


def test_playwright_summary_deduplicates_reporter_annotation_projection_per_browser() -> None:
    metrics = _product_metrics()
    annotation = {"type": "semantic-peer-product-path-v2", "description": json.dumps(metrics)}
    report = {
        "suites": [
            {
                "projectName": browser,
                "annotations": [annotation],
                "tests": [{"annotations": [annotation], "results": [{"status": "passed"}]}],
            }
            for browser in ("chromium", "firefox")
        ]
    }

    summary = _summarize_report(json.dumps(report).encode())

    assert summary["peer_product_scenario_count"] == 2
    assert summary["final_segment_count"] == 4
    assert summary["correction_after_final_count"] == 4


def test_playwright_summary_ignores_unrecognized_or_content_bearing_annotations() -> None:
    report = {
        "projectName": "chromium",
        "annotations": [{"type": "debug", "description": "not evidence"}],
        "results": [{"status": "failed"}],
    }
    summary = _summarize_report(json.dumps(report).encode())
    assert summary["failed_tests"] == 1
    assert "scenario_count" not in summary


def test_playwright_summary_rejects_open_or_synthetic_product_annotations() -> None:
    open_metrics = {**_product_metrics(), "unexpected": 1}
    synthetic_metrics = _product_metrics(synthetic_harness_count=1)
    report = {
        "projectName": "chromium",
        "annotations": [
            {"type": "semantic-peer-product-path-v2", "description": json.dumps(open_metrics)},
            {"type": "semantic-peer-product-path-v2", "description": json.dumps(synthetic_metrics)},
        ],
        "results": [{"status": "passed"}],
    }

    summary = _summarize_report(json.dumps(report).encode())

    assert summary["peer_product_scenario_count"] == 1
    assert summary["synthetic_harness_count"] == 1


def test_playwright_summary_collects_real_hub_peer_sync_metrics() -> None:
    metrics = _product_metrics()
    report = {
        "suites": [
            {
                "projectName": browser,
                "annotations": [
                    {
                        "type": "semantic-peer-product-path-v2",
                        "description": json.dumps(metrics),
                    }
                ],
                "tests": [{"results": [{"status": "passed"}]}],
            }
            for browser in ("chromium", "firefox")
        ]
    }

    summary = _summarize_report(json.dumps(report).encode())

    assert summary["peer_product_scenario_count"] == 2
    assert summary["cross_engine_process_count"] == 2
    assert summary["cross_engine_count"] == 2
    assert summary["signed_preview_visible_count"] == 2
    assert summary["comparison_preview_visible_count"] == 2
    assert summary["product_facade_count"] == 2
    assert summary["product_ui_projection_count"] == 2
    assert summary["hub_curation_count"] == 2
    assert summary["hub_receipt_verified_count"] == 2
    assert summary["hub_dataset_reservation_count"] == 2
    assert summary["plaintext_canary_probe_count"] == 4
    assert summary["synthetic_harness_count"] == 0
    assert summary["partial_latency_max_ms"] == 80


def test_privacy_scan_detects_exact_canaries_and_private_key_material() -> None:
    direct = "ANANTA_DIRECT_CANARY_" + "a" * 48
    relay = "ANANTA_RELAY_CANARY_" + "b" * 48

    assert _privacy_leak_counts((direct, relay), b"content-free") == (0, 0)
    assert _privacy_leak_counts((direct, relay), direct, relay) == (2, 0)
    assert _privacy_leak_counts(
        (direct, relay),
        b'{"key_material":"sensitive-material"}',
        b"-----BEGIN PRIVATE KEY-----",
    ) == (0, 2)


def test_privacy_scan_detects_browser_key_names_and_hub_credentials() -> None:
    assert _privacy_leak_counts(
        (),
        b'{"aesKeyB64":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        b"Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
        b'{"sender_token":"eyJhbGciOiJIUzI1NiJ9.payload.signature"}',
    ) == (0, 3)


def test_privacy_scan_does_not_match_content_free_measurement_names() -> None:
    document = json.dumps({"measurements": {"canary_leak_count": 0, "crypto_canary_match_count": 0}})

    assert _privacy_leak_counts(("ANANTA_DIRECT_CANARY_" + "c" * 48,), document) == (0, 0)


def test_playwright_summary_collects_hub_authoritative_group_metrics() -> None:
    metrics = {
        "participant_contexts": 7,
        "hub_admission_conflict_rejections": 1,
        "hub_admission_success_responses": 24,
        "hub_admission_denied_responses": 2,
        "hub_publication_generations": 2,
        "hub_published_track_count": 2,
        "hub_group_epoch_count": 2,
        "group_package_ack_count": 10,
        "membership_revoke_count": 1,
        "late_join_old_epoch_key_count": 0,
        "removed_member_new_epoch_key_count": 0,
        "independent_receiver_count": 2,
        "weak_receiver_fallback_count": 1,
        "browser_restart_recovery_count": 1,
        "ordinary_fallback_count": 1,
        "client_minted_sfu_token_count": 0,
        "client_generated_group_epoch_count": 0,
    }
    report = {
        "suites": [
            {
                "projectName": browser,
                "annotations": [
                    {
                        "type": "semantic-group-hub-authority-v2",
                        "description": json.dumps(metrics),
                    }
                ],
                "tests": [{"results": [{"status": "passed"}]}],
            }
            for browser in ("chromium", "firefox")
        ]
    }

    summary = _summarize_report(json.dumps(report).encode())

    assert summary["group_hub_authority_scenario_count"] == 2
    assert summary["group_participant_context_min"] == 7
    assert summary["hub_admission_conflict_rejection_count"] == 2
    assert summary["hub_admission_success_response_count"] == 48
    assert summary["hub_admission_denied_response_count"] == 4
    assert summary["hub_publication_generation_min"] == 2
    assert summary["hub_published_track_min"] == 2
    assert summary["hub_group_epoch_min"] == 2
    assert summary["group_package_ack_min"] == 10
    assert summary["membership_revoke_count"] == 2
    assert summary["independent_receiver_min"] == 2
    assert summary["weak_receiver_fallback_count"] == 2
    assert summary["browser_restart_recovery_count"] == 2
    assert summary["ordinary_fallback_count"] == 2
    assert summary["late_join_old_epoch_key_count"] == 0
    assert summary["removed_member_new_epoch_key_count"] == 0
    assert summary["client_minted_sfu_token_count"] == 0
    assert summary["client_generated_group_epoch_count"] == 0


def test_playwright_summary_collects_two_browser_visual_lifecycle_metrics() -> None:
    metrics = {
        "browser_processes": 2,
        "browser_engines": 2,
        "scenario_count": 6,
        "observe_count": 12,
        "active_count": 12,
        "recovery_count": 12,
        "revoke_count": 12,
        "reconnect_count": 12,
        "ordinary_fallback_count": 12,
        "direct_peer_links": 2,
        "ordinary_audio_receivers": 2,
    }
    report = {
        "suites": [
            {
                "projectName": browser,
                "annotations": [
                    {
                        "type": "semantic-visual-lifecycle-v1",
                        "description": json.dumps(metrics),
                    }
                ],
                "tests": [{"results": [{"status": "passed"}]}],
            }
            for browser in ("chromium", "firefox")
        ]
    }

    summary = _summarize_report(json.dumps(report).encode())

    assert summary["visual_lifecycle_scenario_count"] == 2
    assert summary["visual_process_count"] == 2
    assert summary["visual_engine_count"] == 2
    assert summary["visual_scenario_min"] == 6
    assert summary["visual_active_min"] == 12
    assert summary["visual_ordinary_fallback_min"] == 12
    assert summary["visual_direct_link_min"] == 2
    assert summary["visual_ordinary_receiver_min"] == 2


def test_playwright_summary_recovers_report_after_service_logs() -> None:
    report = {
        "config": {},
        "suites": [{"projectName": "chromium", "tests": [{"results": [{"status": "passed"}]}]}],
        "stats": {"expected": 1},
    }
    raw = f"startup log\n{{not-json}}\n{json.dumps(report)}\nshutdown log".encode()
    summary = _summarize_report(raw)
    assert summary["executed_tests"] == 1
    assert summary["browser_count"] == 1


def test_isolated_port_configuration_uses_run_scoped_runtime_paths() -> None:
    environment: dict[str, str] = {"E2E_PORT": "42881"}

    semantic_media_e2e_report._configure_isolated_ports(environment)

    assert environment["E2E_DATA_ROOT"] == "/tmp/ananta-semantic-e2e-42881/data"
    assert environment["E2E_PID_FILE"] == "/tmp/ananta-semantic-e2e-42881/services.json"
    assert environment["E2E_RESULTS_DIR"] == "/tmp/ananta-semantic-e2e-42881/results"


def test_pair_source_binding_covers_executed_offer_v2_and_curation_boundaries() -> None:
    paths = set(_source_paths("semantic-media-pair.spec.ts", _PAIR_COMMON_SOURCE_PATHS))

    assert {
        "scripts/e2e/semantic_media_pair_e2e.py",
        "frontend-angular/playwright.config.ts",
        "frontend-angular/tsconfig.semantic-media-e2e.json",
        "frontend-angular/src/bootstrap-ananta-application.ts",
        "frontend-angular/src/main.semantic-media-e2e.ts",
        "frontend-angular/src/app/e2e/semantic-media-pair-live-driver.ts",
        "frontend-angular/src/app/e2e/semantic-media-pair-ui-observer.ts",
        "frontend-angular/src/app/features/voice/peer-evidence-acceptance.ts",
        "frontend-angular/src/app/features/voice/peer-evidence-sync-panel.component.ts",
        "frontend-angular/src/app/features/voice/peer-evidence-sync.facade.ts",
        "frontend-angular/src/app/features/voice/semantic-media-program-host.component.ts",
        "frontend-angular/src/app/features/voice/semantic-media-program.facade.ts",
        "frontend-angular/src/app/features/voice/voice-audio-capture.ts",
        "frontend-angular/src/app/features/voice/voice.models.ts",
        "frontend-angular/src/app/services/semantic-speech-capture-producer.service.ts",
        "frontend-angular/src/app/services/semantic-speech-runtime-coordinator.service.ts",
        "frontend-angular/src/app/services/semantic-speech-source-correction-api.service.ts",
        "frontend-angular/src/app/services/speech-evidence-hub-curation.facade.ts",
        "frontend-angular/src/app/services/speech-evidence-sync-api.service.ts",
        "frontend-angular/src/app/services/speech-evidence-sync.validators.ts",
        "frontend-angular/src/app/services/webrtc-datachannel.service.ts",
        "frontend-angular/src/app/services/webrtc-signaling.service.ts",
        "frontend-angular/src/app/services/webrtc-session.service.ts",
        "frontend-angular/src/app/features/voice/voice-api.service.ts",
        "frontend-angular/src/app/services/semantic-speech-quality-controller.service.ts",
        "agent/bootstrap/semantic_media_services.py",
        "agent/routes/speech_evidence_sync.py",
        "agent/routes/voice.py",
        "agent/routes/voice_live_runs.py",
        "agent/services/speech_evidence_offer_service.py",
        "agent/services/speech_evidence_peer_curation_composition.py",
        "agent/repositories/speech_evidence_sync.py",
        "agent/db_models/speech_evidence_sync.py",
        "ananta_contracts/speech_evidence_sync.py",
        "voice_runtime/app.py",
        "voice_runtime/streaming.py",
        "migrations/versions/a9b0c1d2e3f4_add_signed_speech_evidence_offer_previews.py",
    } <= paths
    assert all((semantic_media_e2e_report.ROOT / path).is_file() for path in paths)


def test_production_entry_cannot_install_semantic_media_e2e_drivers() -> None:
    production = (semantic_media_e2e_report.ROOT / "frontend-angular/src/main.ts").read_text(encoding="utf-8")
    e2e = (semantic_media_e2e_report.ROOT / "frontend-angular/src/main.semantic-media-e2e.ts").read_text(
        encoding="utf-8"
    )
    angular = json.loads((semantic_media_e2e_report.ROOT / "frontend-angular/angular.json").read_text(encoding="utf-8"))

    assert "app/e2e/" not in production
    assert "installSemanticMediaPairLiveDriver" not in production
    assert "installSemanticMediaPairLiveDriver" in e2e
    assert (
        angular["projects"]["ananta-angular"]["architect"]["build"]["configurations"]["semantic-media-e2e"]["browser"]
        == "src/main.semantic-media-e2e.ts"
    )
    assert (
        angular["projects"]["ananta-angular"]["architect"]["build"]["configurations"]["semantic-media-e2e"]["tsConfig"]
        == "tsconfig.semantic-media-e2e.json"
    )


def test_pair_release_scenario_has_no_test_owned_peer_or_hardcoded_failure_harness() -> None:
    spec = (semantic_media_e2e_report.ROOT / "frontend-angular/tests/semantic-media-pair.spec.ts").read_text(
        encoding="utf-8"
    )
    adapter = (
        semantic_media_e2e_report.ROOT / "frontend-angular/src/app/e2e/semantic-media-pair-live-driver.ts"
    ).read_text(encoding="utf-8")

    assert "__ANANTA_CROSS_SYNC__" not in spec
    assert "new RTCPeerConnection" not in spec
    assert "new RTCPeerConnection" not in adapter
    assert "semantic-pair-metrics-v1" not in spec
    assert "semantic-peer-sync-cross-engine-v1" not in spec
    assert "failureStates" not in adapter


def test_pair_source_binding_invalidates_evidence_for_each_security_layer(tmp_path) -> None:
    paths = _source_paths("semantic-media-pair.spec.ts", _PAIR_COMMON_SOURCE_PATHS)
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stable\n", encoding="utf-8")
    baseline = source_hash(tmp_path, paths)

    for relative in (
        "frontend-angular/src/app/features/voice/peer-evidence-acceptance.ts",
        "frontend-angular/src/app/features/voice/peer-evidence-sync.facade.ts",
        "frontend-angular/src/app/services/speech-evidence-sync.validators.ts",
        "agent/services/speech_evidence_offer_service.py",
        "agent/repositories/speech_evidence_sync.py",
        "agent/db_models/speech_evidence_sync.py",
        "ananta_contracts/speech_evidence_sync.py",
        "migrations/versions/a9b0c1d2e3f4_add_signed_speech_evidence_offer_previews.py",
    ):
        target = tmp_path / relative
        target.write_text(f"changed:{relative}\n", encoding="utf-8")
        assert source_hash(tmp_path, paths) != baseline
        target.write_text("stable\n", encoding="utf-8")


def test_pair_gate_fails_closed_without_copying_leaked_values_into_artifact(monkeypatch) -> None:
    leaked: dict[str, str] = {}
    observed: dict[str, str] = {}
    process_ids = iter((10_001, 10_002))

    def fake_popen(command, **kwargs):
        environment = kwargs["env"]
        direct = environment["ANANTA_PAIR_DIRECT_CANARY"]
        observed["test_timeout_ms"] = environment["E2E_TEST_TIMEOUT_MS"]
        leaked["direct"] = direct
        leaked["token"] = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
        report = {
            "projectName": command[command.index("--project") + 1],
            "annotations": [{"type": "debug", "description": direct}],
            "results": [{"status": "passed"}],
        }
        return _FakeProcess(
            process_id=next(process_ids),
            returncode=0,
            outcomes=[
                (
                    json.dumps(report).encode("utf-8"),
                    (
                        f'{{"aesKeyB64":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",'
                        f'"access_token":"{leaked["token"]}"}}'
                    ).encode("utf-8"),
                )
            ],
        )

    def missing_process_group(_process_id: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", missing_process_group)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=True,
        required_browsers=("chromium", "firefox"),
    )

    assert evidence.status == "failed"
    assert "pair_plaintext_canary_leaked" in evidence.reason_codes
    assert "pair_key_material_leaked" in evidence.reason_codes
    document = json.dumps(evidence.as_document(), sort_keys=True)
    assert leaked["direct"] not in document
    assert leaked["token"] not in document
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" not in document
    assert observed["test_timeout_ms"] == "240000"


def test_pair_gate_timeout_is_fixed_and_evidence_bound(monkeypatch) -> None:
    observed: dict[str, str] = {}
    process_ids = iter((10_101, 10_102))

    def fake_popen(*_args, **kwargs):
        observed["test_timeout_ms"] = kwargs["env"]["E2E_TEST_TIMEOUT_MS"]
        observed["workers"] = kwargs["env"]["E2E_WORKERS"]
        observed["retain"] = kwargs["env"]["E2E_RETAIN_EVIDENCE_ARTIFACTS"]
        return _FakeProcess(
            process_id=next(process_ids),
            returncode=1,
            outcomes=[(b"{}", b"")],
        )

    def missing_process_group(_process_id: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", missing_process_group)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=True,
        environment_overrides={
            "E2E_TEST_TIMEOUT_MS": "1",
            "E2E_WORKERS": "8",
            "E2E_RETAIN_EVIDENCE_ARTIFACTS": "1",
        },
    )

    assert observed["test_timeout_ms"] == "240000"
    assert observed["workers"] == "1"
    assert observed["retain"] == "0"
    assert evidence.config_sha256 == semantic_media_e2e_report.canonical_sha256(
        {
            "spec": "semantic-media-pair.spec.ts",
            "required_browsers": ["chromium", "firefox"],
            "live_driver_required": True,
            "timeout_seconds": 900,
            "playwright_test_timeout_ms": 240_000,
            "project_isolation": "fresh_stack_per_project",
            "worker_count": 1,
            "retain_evidence_artifacts": False,
            "termination_grace_seconds": 5,
        }
    )


def test_pair_gate_isolates_projects_under_one_deadline_and_attributes_failure(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []
    processes: list[_FakeProcess] = []
    popen_options: list[dict[str, object]] = []
    process_group_signals: list[tuple[int, int]] = []
    report_paths: list[Path] = []
    ports = iter((42_101, 42_201))
    process_ids = iter((10_201, 10_202))

    def fake_configure(environment: dict[str, str]) -> None:
        assert "E2E_PORT" not in environment
        assert "E2E_DATA_ROOT" not in environment
        frontend_port = next(ports)
        environment.update(
            {
                "E2E_PORT": str(frontend_port),
                "E2E_HUB_URL": f"http://127.0.0.1:{frontend_port + 1}",
                "E2E_ALPHA_URL": f"http://127.0.0.1:{frontend_port + 2}",
                "E2E_BETA_URL": f"http://127.0.0.1:{frontend_port + 3}",
                "E2E_VOICE_RUNTIME_URL": f"http://127.0.0.1:{frontend_port + 4}",
                "E2E_DATA_ROOT": f"/tmp/test-{frontend_port}/data",
                "E2E_PID_FILE": f"/tmp/test-{frontend_port}/services.json",
                "E2E_RESULTS_DIR": f"/tmp/test-{frontend_port}/results",
                "ANANTA_E2E_FORCE_ISOLATED": "1",
            }
        )

    def fake_popen(command, **kwargs):
        project = command[command.index("--project") + 1]
        status = "passed" if project == "chromium" else "failed"
        commands.append(list(command))
        environments.append(dict(kwargs["env"]))
        popen_options.append(dict(kwargs))
        report_paths.append(Path(kwargs["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"]))
        report = {
            "projectName": project,
            "annotations": [
                {
                    "type": "semantic-peer-product-path-v2",
                    "description": json.dumps(_product_metrics()),
                }
            ],
            "results": [{"status": status}],
        }
        process = _FakeProcess(
            process_id=next(process_ids),
            returncode=0 if status == "passed" else 1,
            outcomes=[(json.dumps(report).encode("utf-8"), b"")],
        )
        processes.append(process)
        return process

    def fake_killpg(process_id: int, requested_signal: int) -> None:
        process_group_signals.append((process_id, requested_signal))

    monotonic = iter((100.0, 110.0, 125.0))
    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report, "_configure_isolated_ports", fake_configure)
    monkeypatch.setattr(semantic_media_e2e_report, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", fake_killpg)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=True,
        environment_overrides={
            "E2E_WORKERS": "8",
            "E2E_RETAIN_EVIDENCE_ARTIFACTS": "1",
            "E2E_PORT": "9999",
            "E2E_DATA_ROOT": "/tmp/stale-data",
            "E2E_PID_FILE": "/tmp/stale-services.json",
            "E2E_RESULTS_DIR": "/tmp/stale-results",
        },
    )

    assert [command[command.index("--project") + 1] for command in commands] == [
        "chromium",
        "firefox",
    ]
    assert [environment["E2E_BROWSERS"] for environment in environments] == [
        "chromium",
        "firefox",
    ]
    assert {environment["E2E_PORT"] for environment in environments} == {"42101", "42201"}
    for key in ("E2E_DATA_ROOT", "E2E_PID_FILE", "E2E_RESULTS_DIR"):
        assert len({environment[key] for environment in environments}) == 2
    assert {environment["E2E_WORKERS"] for environment in environments} == {"1"}
    assert {environment["E2E_RETAIN_EVIDENCE_ARTIFACTS"] for environment in environments} == {"0"}
    assert all(option["stdout"] == semantic_media_e2e_report.subprocess.PIPE for option in popen_options)
    assert all(option["stderr"] == semantic_media_e2e_report.subprocess.PIPE for option in popen_options)
    assert all(option["start_new_session"] is True for option in popen_options)
    assert [process.timeouts for process in processes] == [[890.0, 5], [875.0, 5]]
    assert process_group_signals == [
        (10_201, semantic_media_e2e_report.signal.SIGTERM),
        (10_201, semantic_media_e2e_report.signal.SIGKILL),
        (10_202, semantic_media_e2e_report.signal.SIGTERM),
        (10_202, semantic_media_e2e_report.signal.SIGKILL),
    ]
    project_directories = {Path(environment["E2E_DATA_ROOT"]).parent for environment in environments}
    assert len({path.parent for path in project_directories}) == 1
    assert all(not path.exists() for path in project_directories)
    assert all(not path.exists() for path in report_paths)
    assert evidence.measurements["chromium_failed_tests"] == 0
    assert evidence.measurements["firefox_failed_tests"] == 1
    assert evidence.measurements["failed_project_count"] == 1
    assert "live_browser_project_firefox_failed" in evidence.reason_codes


def test_process_group_cleanup_reaps_parent_after_killpg_lookup_race(monkeypatch) -> None:
    process = _FakeProcess(
        process_id=10_301,
        returncode=0,
        outcomes=[(b"reaped-stdout", b"reaped-stderr")],
    )

    def missing_process_group(_process_id: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", missing_process_group)

    output = semantic_media_e2e_report._terminate_process_group(process, grace_seconds=5)

    assert output == (b"reaped-stdout", b"reaped-stderr")
    assert process.timeouts == [5]


def test_pair_gate_timeout_scans_output_and_terminates_the_process_group(monkeypatch) -> None:
    observed: dict[str, object] = {}
    process_group_signals: list[tuple[int, int]] = []

    def fake_configure(environment: dict[str, str]) -> None:
        environment.update(
            {
                "E2E_PORT": "42301",
                "E2E_HUB_URL": "http://127.0.0.1:42302",
                "E2E_ALPHA_URL": "http://127.0.0.1:42303",
                "E2E_BETA_URL": "http://127.0.0.1:42304",
                "E2E_VOICE_RUNTIME_URL": "http://127.0.0.1:42305",
            }
        )

    def fake_popen(command, **kwargs):
        direct = kwargs["env"]["ANANTA_PAIR_DIRECT_CANARY"]
        key_output = b'{"key_material":"sensitive-material"}'
        process = _FakeProcess(
            process_id=10_301,
            returncode=-15,
            outcomes=[
                semantic_media_e2e_report.subprocess.TimeoutExpired(
                    command,
                    900,
                    output=direct.encode("utf-8"),
                    stderr=key_output,
                ),
                semantic_media_e2e_report.subprocess.TimeoutExpired(
                    command,
                    5,
                    output=direct.encode("utf-8"),
                    stderr=key_output,
                ),
                (direct.encode("utf-8"), key_output),
            ],
        )
        observed["process"] = process
        observed["run_directory"] = Path(kwargs["env"]["E2E_DATA_ROOT"]).parent
        observed["start_new_session"] = kwargs["start_new_session"]
        return process

    def fake_killpg(process_id: int, requested_signal: int) -> None:
        process_group_signals.append((process_id, requested_signal))

    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report, "_configure_isolated_ports", fake_configure)
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", fake_killpg)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=True,
        required_browsers=("chromium",),
    )

    process = observed["process"]
    assert isinstance(process, _FakeProcess)
    assert observed["start_new_session"] is True
    assert process.timeouts[-2:] == [5, 5]
    assert process_group_signals == [
        (10_301, semantic_media_e2e_report.signal.SIGTERM),
        (10_301, semantic_media_e2e_report.signal.SIGKILL),
    ]
    assert evidence.status == "failed"
    assert "live_browser_gate_timeout" in evidence.reason_codes
    assert "pair_plaintext_canary_leaked" in evidence.reason_codes
    assert "pair_key_material_leaked" in evidence.reason_codes
    assert not Path(observed["run_directory"]).exists()


def test_pair_gate_cleanup_timeout_is_fail_closed_and_reaps_after_killpg_race(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_configure(environment: dict[str, str]) -> None:
        environment.update(
            {
                "E2E_PORT": "42401",
                "E2E_HUB_URL": "http://127.0.0.1:42402",
                "E2E_ALPHA_URL": "http://127.0.0.1:42403",
                "E2E_BETA_URL": "http://127.0.0.1:42404",
                "E2E_VOICE_RUNTIME_URL": "http://127.0.0.1:42405",
            }
        )

    def fake_popen(command, **kwargs):
        direct = kwargs["env"]["ANANTA_PAIR_DIRECT_CANARY"].encode("utf-8")

        def timeout(seconds: int):
            return semantic_media_e2e_report.subprocess.TimeoutExpired(
                command,
                seconds,
                output=direct,
                stderr=b'{"key_material":"sensitive-material"}',
            )

        process = _FakeProcess(
            process_id=10_401,
            returncode=-9,
            outcomes=[timeout(900), timeout(5), timeout(5)],
        )
        observed["process"] = process
        observed["run_directory"] = Path(kwargs["env"]["E2E_DATA_ROOT"]).parent
        return process

    def missing_process_group(_process_id: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report, "_configure_isolated_ports", fake_configure)
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", missing_process_group)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=True,
        required_browsers=("chromium",),
    )

    process = observed["process"]
    assert isinstance(process, _FakeProcess)
    assert len(process.timeouts) == 3
    assert process.kill_count == 1
    assert evidence.status == "failed"
    assert "live_browser_gate_cleanup_failed" in evidence.reason_codes
    assert "pair_plaintext_canary_leaked" in evidence.reason_codes
    assert "pair_key_material_leaked" in evidence.reason_codes
    assert not Path(observed["run_directory"]).exists()


def test_pair_gate_prefers_the_temporary_file_report_over_stdout(monkeypatch) -> None:
    observed_report_path: Path | None = None

    def fake_configure(environment: dict[str, str]) -> None:
        environment.update(
            {
                "E2E_PORT": "42501",
                "E2E_HUB_URL": "http://127.0.0.1:42502",
                "E2E_ALPHA_URL": "http://127.0.0.1:42503",
                "E2E_BETA_URL": "http://127.0.0.1:42504",
                "E2E_VOICE_RUNTIME_URL": "http://127.0.0.1:42505",
            }
        )

    def fake_popen(_command, **kwargs):
        nonlocal observed_report_path
        observed_report_path = Path(kwargs["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"])
        file_report = {
            "projectName": "chromium",
            "annotations": [
                {
                    "type": "semantic-peer-product-path-v2",
                    "description": json.dumps(_product_metrics()),
                }
            ],
            "results": [{"status": "passed"}],
        }
        observed_report_path.write_text(json.dumps(file_report), encoding="utf-8")
        stdout_report = {"projectName": "chromium", "results": [{"status": "failed"}]}
        return _FakeProcess(
            process_id=10_501,
            returncode=0,
            outcomes=[(json.dumps(stdout_report).encode("utf-8"), b"")],
        )

    def missing_process_group(_process_id: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report, "_configure_isolated_ports", fake_configure)
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", missing_process_group)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-005",
        spec="semantic-media-pair.spec.ts",
        execute_live=True,
        required_browsers=("chromium",),
    )

    assert evidence.measurements["passed_tests"] == 1
    assert evidence.measurements["failed_tests"] == 0
    assert observed_report_path is not None
    assert not observed_report_path.exists()


def test_non_pair_gate_keeps_one_shared_browser_matrix(monkeypatch) -> None:
    observed: dict[str, object] = {"commands": []}

    def fake_configure(environment: dict[str, str]) -> None:
        environment.update(
            {
                "E2E_PORT": "42601",
                "E2E_HUB_URL": "http://127.0.0.1:42602",
                "E2E_ALPHA_URL": "http://127.0.0.1:42603",
                "E2E_BETA_URL": "http://127.0.0.1:42604",
                "E2E_VOICE_RUNTIME_URL": "http://127.0.0.1:42605",
            }
        )

    def fake_popen(command, **kwargs):
        observed["commands"].append(list(command))
        observed["environment"] = dict(kwargs["env"])
        observed["run_directory"] = Path(kwargs["env"]["E2E_DATA_ROOT"]).parent
        report = {
            "suites": [
                {
                    "projectName": browser,
                    "tests": [{"results": [{"status": "passed"}]}],
                }
                for browser in ("chromium", "firefox")
            ]
        }
        return _FakeProcess(
            process_id=10_601,
            returncode=0,
            outcomes=[(json.dumps(report).encode("utf-8"), b"")],
        )

    def missing_process_group(_process_id: int, _requested_signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(semantic_media_e2e_report.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(semantic_media_e2e_report, "_configure_isolated_ports", fake_configure)
    monkeypatch.setattr(semantic_media_e2e_report.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(semantic_media_e2e_report.os, "killpg", missing_process_group)

    evidence = semantic_media_e2e_report.run_playwright_gate(
        gate_id="ASMP-QA-004",
        spec="semantic-media-accessibility.spec.ts",
        execute_live=True,
        environment_overrides={
            "E2E_WORKERS": "9",
            "E2E_RETAIN_EVIDENCE_ARTIFACTS": "1",
            "E2E_DATA_ROOT": "/tmp/stale-matrix-data",
        },
    )

    commands = observed["commands"]
    assert isinstance(commands, list)
    assert len(commands) == 1
    assert "--project" not in commands[0]
    environment = observed["environment"]
    assert isinstance(environment, dict)
    assert environment["E2E_BROWSERS"] == "chromium,firefox"
    assert environment["E2E_WORKERS"] == "1"
    assert environment["E2E_RETAIN_EVIDENCE_ARTIFACTS"] == "0"
    assert evidence.status == "passed"
    assert not Path(observed["run_directory"]).exists()


def test_playwright_gate_config_keeps_visual_and_pair_budgets_distinct() -> None:
    pair = semantic_media_e2e_report.playwright_gate_config(spec="semantic-media-pair.spec.ts")
    visual = semantic_media_e2e_report.playwright_gate_config(spec="semantic-visual-lifecycle.spec.ts")

    assert pair["playwright_test_timeout_ms"] == 240_000
    assert visual["playwright_test_timeout_ms"] == 60_000
    assert pair["live_driver_required"] is True
    assert visual["live_driver_required"] is True
    assert pair["project_isolation"] == "fresh_stack_per_project"
    assert visual["project_isolation"] == "shared_stack"
    assert pair["worker_count"] == 1
    assert visual["worker_count"] == 1
    assert pair["retain_evidence_artifacts"] is False
    assert visual["retain_evidence_artifacts"] is False
    assert pair["termination_grace_seconds"] == 5
    assert visual["termination_grace_seconds"] == 5
