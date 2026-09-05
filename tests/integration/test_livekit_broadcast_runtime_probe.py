from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/probe_livekit_broadcast_runtime.py"
SPEC = importlib.util.spec_from_file_location("livekit_broadcast_runtime_probe", SCRIPT)
assert SPEC and SPEC.loader
probe_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe_module
SPEC.loader.exec_module(probe_module)


def _by_name(report: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = report["capabilities"]
    assert isinstance(rows, list)
    return {str(row["capability"]): row for row in rows}


def test_static_probe_fails_closed_without_runtime_evidence() -> None:
    report = probe_module.probe(root=ROOT, execute_runtime=False)
    capabilities = _by_name(report)

    assert report["decision"] == "blocked"
    assert report["runtime_control_mode"] == "livekit_control_api"
    assert report["placement_owner"] == "livekit_native"
    assert report["feature_defaults"]["sfu_broadcast_enabled"] is False
    assert "runtime_evidence_missing" in report["reason_codes"]
    assert capabilities["room_service_update_subscriptions"]["status"] != "available"
    assert capabilities["room_service_send_data"]["status"] != "available"
    assert capabilities["participant_permissions"]["status"] != "available"
    assert capabilities["browser_e2ee"]["status"] != "available"


def test_current_static_bindings_accept_hub_capacity_and_layer_policy() -> None:
    bindings = probe_module.collect_static_bindings(ROOT)

    assert "four_peer_room_capacity_not_bound" not in bindings.reason_codes
    assert "client_stream_policy_binding_missing" not in bindings.reason_codes
    assert probe_module._client_stream_policy_bound(bindings.adapter_text) is True


def test_static_layer_policy_rejects_unconditional_or_disabled_dynacast() -> None:
    assert probe_module._client_stream_policy_bound(
        "adaptiveStream: true, dynacast: true"
    ) is False
    assert probe_module._client_stream_policy_bound(
        "adaptiveStream: options.layerControlMode === 'adaptive_stream', dynacast: false"
    ) is False


def test_mock_only_runtime_claim_cannot_mark_capability_available() -> None:
    bindings = probe_module.collect_static_bindings(ROOT)
    forged = probe_module.RuntimeObservation(
        provenance="mock",
        image_reference=probe_module.EXPECTED_IMAGE_REFERENCE,
        image_id_sha256=probe_module.EXPECTED_IMAGE_DIGEST,
        server_version=probe_module.EXPECTED_SERVER_VERSION,
        config_sha256=bindings.config_sha256,
        container_id_sha256="0" * 64,
        smoke_report_sha256="1" * 64,
        publisher_count=1,
        receiver_count=3,
        publisher_upload_count=1,
        publisher_publication_count=1,
        decoded_receiver_count=3,
        room_service_update_subscriptions=True,
        room_service_send_data=True,
        participant_permissions=True,
        e2ee=True,
        cleanup_complete=True,
        reason_codes=(),
    )
    report = probe_module.build_report(bindings, forged)
    capabilities = _by_name(report)

    assert report["decision"] == "blocked"
    assert capabilities["room_service_update_subscriptions"]["status"] == "degraded"
    assert capabilities["room_service_send_data"]["status"] == "degraded"
    assert capabilities["participant_permissions"]["status"] == "degraded"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("server_version", "1.13.2", "browser_smoke_version_binding_invalid"),
        ("server_digest", "sha256:" + "0" * 64, "browser_smoke_version_binding_invalid"),
        ("client_version", "2.20.2", "browser_smoke_version_binding_invalid"),
    ],
)
def test_spike_report_rejects_version_drift(field: str, value: str, reason: str) -> None:
    report = _valid_spike_report()
    report["pinned"][field] = value
    assert reason in probe_module.validate_spike_report(report)


def test_spike_report_requires_one_publisher_and_three_receivers() -> None:
    report = _valid_spike_report()
    report["topology"]["receivers"] = 2
    report["engines"][0]["peers"].pop()
    assert "browser_smoke_topology_invalid" in probe_module.validate_spike_report(report)


def test_spike_report_distinguishes_publication_from_simulcast_encodings() -> None:
    report = _valid_spike_report()
    publisher = report["engines"][0]["peers"][0]
    publisher["outbound_video_streams"] = 3

    assert probe_module.validate_spike_report(report) == ()

    publisher["local_video_publication_count"] = 2
    assert "browser_smoke_media_evidence_invalid" in probe_module.validate_spike_report(report)


def test_cli_writes_blocked_report_and_returns_nonzero_without_runtime(tmp_path: Path) -> None:
    output = tmp_path / "capabilities.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode != 0
    assert report["decision"] == "blocked"
    assert report["runtime_evidence"] is None


def _valid_spike_report() -> dict[str, object]:
    peers: list[dict[str, object]] = [{
        "identity": "publisher",
        "peer_connections": 1,
        "local_video_publication_count": 1,
        "outbound_video_streams": 1,
        "outbound_video_bytes": 100,
    }]
    peers.extend({
        "identity": f"receiver-{index}",
        "decoded_samples": 3,
        "inbound_video_bytes": 100,
    } for index in range(1, 4))
    return {
        "schema": "ananta.semantic-sfu-three-peer-spike.v1",
        "release_evidence": False,
        "pinned": {
            "server_version": probe_module.EXPECTED_SERVER_VERSION,
            "server_digest": probe_module.EXPECTED_IMAGE_DIGEST,
            "client_version": probe_module.EXPECTED_CLIENT_VERSION,
        },
        "topology": {
            "publishers": 1,
            "receivers": 3,
            "expected_publisher_publications": 1,
        },
        "e2ee": {"enabled": True, "server_plaintext_access": False},
        "engines": [{"peers": peers}],
        "verdict": "pass",
    }
