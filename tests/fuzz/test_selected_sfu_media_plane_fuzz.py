from scripts.run_sfu_media_plane_fuzz_gate import evaluate
from scripts.sfu_broadcast_release_common import canonical_sha256


def _profile() -> dict:
    return {
        "required_image_ids": ["sfu", "turn"],
        "seeds": [11, 13, 17],
        "seeds_min": 3,
        "cases_per_seed_min": 8,
        "required_protocol_classes": ["rtp_header"],
        "unsupported_internal_protocol_classes": ["private_router"],
        "cross_layer_mutation": {"required_path_components": ["public_listener", "livekit"]},
        "limits": {
            "packet_count_max": 20, "bytes_max": 4096, "cpu_seconds_max": 2,
            "memory_bytes_max": 67108864, "file_descriptors_max": 16,
            "sockets_max": 8, "timeout_seconds": 3,
        },
    }


def _evidence(profile: dict) -> dict:
    digest = "a" * 64
    return {
        "schema": "ananta.sfu-broadcast-real-media-fuzz-result.v1",
        "status": "passed",
        "bindings": {
            "source_sha256": digest, "config_sha256": canonical_sha256(profile),
            "infrastructure_sha256": digest,
            "image_digests": {"sfu": digest, "turn": digest},
        },
        "execution": {"backend": "real_container", "mock_used": False, "public_listener_reached": True},
        "seeds": [11, 13, 17], "cases_per_seed": 8,
        "corpus_sha256": digest, "coverage_sha256": digest,
        "protocol_classes": ["rtp_header"],
        "unsupported_protocol_classes": ["private_router"],
        "cross_layer_mutation": {"cases": 0, "public_injection_path_verified": False, "path_components": []},
        "resource_peaks": {
            "packet_count": 20, "bytes": 4096, "cpu_seconds": 1,
            "memory_bytes": 67108864, "file_descriptors": 8,
            "sockets": 4, "timeout_seconds": 2,
        },
        "safety": {
            "no_crashloop": True, "no_oom": True, "cpu_bounded": True,
            "allocation_bounded": True, "no_cross_scope_route": True,
            "no_auth_e2ee_downgrade": True, "no_payload_export": True,
            "hub_signature_boundary_preserved": True,
        },
        "minimized_failures": [],
    }


def test_media_plane_fuzz_rejects_mock_only_execution() -> None:
    profile = _profile()
    evidence = _evidence(profile)
    evidence["execution"]["mock_used"] = True
    assert "media_fuzz_real_backend_missing" in evaluate(profile, evidence)["reason_codes"]


def test_media_plane_fuzz_rejects_unverified_cross_layer_injection() -> None:
    profile = _profile()
    evidence = _evidence(profile)
    evidence["cross_layer_mutation"]["cases"] = 1
    assert "media_fuzz_cross_layer_injection_unverified" in evaluate(profile, evidence)["reason_codes"]


def test_media_plane_fuzz_rejects_resource_limit_overrun() -> None:
    profile = _profile()
    evidence = _evidence(profile)
    evidence["resource_peaks"]["sockets"] = 9
    assert "media_fuzz_resource_sockets_exceeded" in evaluate(profile, evidence)["reason_codes"]
