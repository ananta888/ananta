from __future__ import annotations

import copy
import struct
from pathlib import Path

from agent.services.semantic_media_program_evidence import GateEvidence
from scripts.e2e.semantic_media_group_e2e import _bind_capture_evidence, _failover_measurements
from scripts.e2e.semantic_media_packet_capture import (
    capture_measurements,
    pcap_packet_count,
)
from scripts.e2e.semantic_sfu_failover_e2e import (
    DIGEST,
    _classify_browser_failure,
    recompute_live_failover_evidence,
    write_isolated_deployment,
)
from scripts.e2e.semantic_sfu_group_e2e import load_report, parse_size, percentile, sample_metrics


def _pcap(*payloads: bytes) -> bytes:
    header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65_535, 1)
    records = b"".join(
        struct.pack("<IIII", index, 0, len(payload), len(payload)) + payload
        for index, payload in enumerate(payloads, start=1)
    )
    return header + records


def test_sfu_boundary_capture_is_parsed_without_persisting_payload(tmp_path: Path) -> None:
    capture = _pcap(b"opaque-rtp-one", b"opaque-rtp-two")
    path = tmp_path / "capture.pcap"
    path.write_bytes(capture)

    assert pcap_packet_count(capture) == 2
    assert capture_measurements(path, "sfu") == {
        "sfu_boundary_capture_verified": True,
        "sfu_boundary_packet_count": 2,
        "sfu_boundary_capture_bytes": len(capture),
        "sfu_boundary_known_marker_matches": 0,
        "sfu_boundary_credential_matches": 0,
    }


def test_sfu_boundary_capture_fails_gate_when_known_plaintext_is_present(tmp_path: Path) -> None:
    path = tmp_path / "capture.pcap"
    path.write_bytes(_pcap(b"synthetic-control-marker"))
    browser = GateEvidence(
        gate_id="ASMP-QA-006",
        status="passed",
        reason_codes=(),
        source_sha256="a" * 64,
        config_sha256="b" * 64,
        measurements={"executed_tests": 2, "passed_tests": 2, "failed_tests": 0},
    )

    evidence = _bind_capture_evidence(
        browser,
        {
            **capture_measurements(path, "sfu"),
            **{
                key.replace("sfu_", "turn_", 1): value
                for key, value in capture_measurements(path, "sfu").items()
            },
            "turn_relay_verified": True,
            "turn_relay_engine_count": 2,
            "turn_relay_scenario_count": 2,
        },
        _live_failover_report(),
    )

    assert evidence.status == "failed"
    assert evidence.reason_codes == ("sfu_boundary_known_plaintext_detected",)


def test_size_and_percentile_parsing_are_deterministic() -> None:
    assert parse_size("10MiB") == 10 * 1024 * 1024
    assert parse_size("1.5 GB") == 1_500_000_000
    assert percentile([30, 10, 20], 0.95) == 30


def test_load_report_is_fail_closed_against_real_measurements() -> None:
    passing = {
        "ingress": 100,
        "egress": 200,
        "drops": 0,
        "turn": 0,
        "latency": 2_000,
        "cpu": 1.0,
        "memory": 10_000_000,
    }
    report = load_report({2: [passing] * 3, 4: [passing] * 3})
    assert report["verdict"] == "pass"
    failing = {**passing, "drops": 9}
    report = load_report({2: [passing] * 3, 4: [passing, passing, failing]})
    assert report["verdict"] == "fail"
    assert report["levels"][1]["verdict"] == "fail"


def test_browser_metrics_preserve_only_bounded_aggregate_values() -> None:
    report = {
        "engines": [
            {
                "peers": [
                    {"identity": "publisher", "outbound_video_bytes": 100, "selected_candidate_types": []},
                    {
                        "identity": "receiver-1",
                        "inbound_video_bytes": 90,
                        "dropped_video_frames": 1,
                        "selected_candidate_types": ["host:host"],
                    },
                    {
                        "identity": "receiver-2",
                        "inbound_video_bytes": 80,
                        "dropped_video_frames": 2,
                        "selected_candidate_types": ["relay:host"],
                    },
                ]
            }
        ]
    }
    assert sample_metrics(report, 500, 2.5, 12_000_000) == {
        "ingress": 100,
        "egress": 170,
        "drops": 3,
        "turn": 1,
        "latency": 500,
        "cpu": 2.5,
        "memory": 12_000_000,
    }


def _live_failover_report() -> dict:
    engine = {
        "engine": "chromium",
        "pre_failure": {
            "publisher_outbound_bytes": 100,
            "receiver_count": 2,
            "receiver_min_inbound_bytes": 50,
            "receiver_min_decoded_samples": 3,
            "stale_probe_initial_inbound_bytes": 50,
            "stale_probe_initial_decoded_samples": 3,
        },
        "outage": {
            "sfu_sigkill_acknowledged": True,
            "hub_sigkill_acknowledged": True,
            "hub_api_unavailable_verified": True,
            "reconnecting_client_count": 4,
            "disconnected_client_count": 4,
            "semantic_room_count_during_fallback": 0,
            "ordinary_peer_connection_count": 4,
            "ordinary_receiver_count": 2,
            "ordinary_min_outbound_bytes": 25,
            "ordinary_min_inbound_bytes": 20,
            "controlled_mode": "ordinary_audio_fallback",
        },
        "recovery": {
            "sfu_restart_acknowledged": True,
            "hub_restart_acknowledged": True,
            "persistent_admission_state_resumed": True,
            "admission_revision_before_restart": 8,
            "admission_revision_after_restart": 8,
            "old_authorization_rejected_count": 4,
            "fresh_admission_count": 8,
            "signature_verification_count": 12,
            "group_key_epoch": 2,
            "previous_group_key_epoch": 1,
            "reason": "hub_failover",
            "fresh_key_distinct": True,
            "receiver_count": 2,
            "receiver_min_inbound_bytes": 60,
            "receiver_min_decoded_samples": 3,
            "stale_key_probe_inbound_bytes": 40,
            "stale_key_probe_decoded_samples": 0,
        },
        "compute": {
            "initial_primary_lease_count": 1,
            "initial_validator_lease_count": 1,
            "persisted_active_lease_count_after_restart": 2,
            "revoked_primary_lease_count": 1,
            "replacement_primary_lease_count": 1,
            "replacement_fencing_token_advanced": True,
            "validator_conflict_request_count": 2,
            "validator_conflict_success_count": 1,
            "validator_conflict_rejection_count": 1,
            "validator_conflict_reason": "lease_overlap",
            "conflict_scope_active_primary_count": 1,
            "conflict_scope_active_validator_count": 1,
            "duplicate_active_lease_count": 0,
            "hub_remained_sole_lease_authority": True,
        },
        "cleanup": {
            "ordinary_peer_connections_closed": 4,
            "ordinary_tracks_ended": 1,
            "livekit_rooms_remaining": 0,
            "livekit_workers_terminated": 8,
            "livekit_tracks_ended": 2,
            "browser_closed": True,
        },
        "verdict": "pass",
    }
    firefox = copy.deepcopy(engine)
    firefox["engine"] = "firefox"
    return {
        "schema": "ananta.semantic-sfu-live-failover.v1",
        "pinned": {
            "server_version": "1.13.1",
            "server_digest": DIGEST,
            "client_version": "2.20.1",
        },
        "topology": {
            "publishers": 1,
            "required_receivers": 2,
            "stale_key_probes": 1,
            "browser_engines": ["chromium", "firefox"],
        },
        "authority": {
            "kind": "productive-hub-api",
            "admission_api": "semantic_sfu_admission_bp",
            "compute_api": "semantic_media_contracts_bp",
            "state_repository": "sql_cas",
            "signature_algorithm": "Ed25519",
            "browser_mints_admission": False,
            "epoch_transition": [1, 2],
            "recovery_reason": "hub_failover",
        },
        "persisted_source_data": False,
        "engines": [engine, firefox],
        "runner": {
            "unique_compose_project": True,
            "matching_dynamic_rtc_ports": True,
            "sigkill_count": 2,
            "restart_count": 2,
            "hub_sigkill_count": 2,
            "hub_restart_count": 2,
            "hub_process_identity_changed": True,
            "ephemeral_hub_state_removed": True,
            "known_credential_match_count": 0,
            "compose_project_removed": True,
            "browser_process_exit_verified": True,
            "hub_boundary_capture_verified": True,
            "hub_boundary_packet_count": 42,
            "hub_boundary_capture_bytes": 4_096,
            "hub_boundary_known_marker_matches": 0,
            "hub_boundary_rtp_rtcp_packet_count": 0,
            "hub_boundary_filter_protocol": "tcp_control_only",
            "hub_boundary_capture_persisted": False,
            "hub_boundary_credential_scan_performed": False,
        },
        "verdict": "pass",
    }


def test_live_failover_parser_recomputes_kill_fallback_epoch_and_cleanup() -> None:
    report = _live_failover_report()
    assert recompute_live_failover_evidence(report) == []

    assert _failover_measurements(report) == {
        "live_failover_verified": True,
        "live_failover_engine_count": 2,
        "sfu_restart_count": 2,
        "hub_restart_count": 2,
        "persistent_admission_recovery_count": 2,
        "old_authorization_rejection_count": 8,
        "fresh_admission_count": 16,
        "primary_replacement_count": 2,
        "validator_conflict_rejection_count": 2,
        "duplicate_active_lease_count": 0,
        "hub_sole_lease_authority_count": 2,
        "restart_ordinary_fallback_count": 2,
    }

    report["verdict"] = "pass"
    report["engines"][0]["recovery"]["stale_key_probe_decoded_samples"] = 1
    assert recompute_live_failover_evidence(report) == ["chromium_fresh_epoch_recovery_missing"]


def test_live_failover_parser_rejects_simulated_hub_and_duplicate_validator_lease() -> None:
    report = _live_failover_report()
    report["authority"]["kind"] = "test-side-hub-simulator"
    report["engines"][1]["compute"]["duplicate_active_lease_count"] = 1
    assert recompute_live_failover_evidence(report) == [
        "firefox_compute_failover_fencing_missing",
        "live_failover_hub_authority_invalid",
    ]


def test_live_failover_parser_requires_ephemeral_hub_boundary_capture() -> None:
    report = _live_failover_report()
    report["runner"]["hub_boundary_rtp_rtcp_packet_count"] = 1

    assert recompute_live_failover_evidence(report) == [
        "live_failover_runner_cleanup_or_isolation_invalid"
    ]


def test_browser_failure_classifier_exposes_only_closed_public_reason_codes() -> None:
    assert _classify_browser_failure("Error: recovered receiver flow missing\n/path/stack") == (
        "live_failover_recovered_receiver_flow_missing"
    )
    assert _classify_browser_failure(
        "Error: productive Hub API rejected 409:lease_overlap\nBearer secret-value"
    ) == "live_failover_hub_api_rejected_lease_overlap"
    assert _classify_browser_failure("Error: token super-secret-value") == (
        "live_failover_browser_process_failed"
    )


def test_isolated_deployment_uses_matching_dynamic_rtc_ports_and_pinned_image(tmp_path: Path) -> None:
    compose = write_isolated_deployment(tmp_path, http_port=31001, tcp_port=31002, udp_port=31003)
    rendered = compose.read_text(encoding="utf-8")
    config = (tmp_path / "livekit.yaml").read_text(encoding="utf-8")
    assert f"livekit/livekit-server@{DIGEST}" in rendered
    assert '"127.0.0.1:31002:31002/tcp"' in rendered
    assert '"127.0.0.1:31003:31003/udp"' in rendered
    assert "tcp_port: 31002" in config and "udp_port: 31003" in config
    assert 'restart: "no"' in rendered
