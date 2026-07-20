from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from agent.services.semantic_media_program_evidence import ProgramEvidenceError
from ananta_contracts import (
    semantic_compute,
    semantic_speech,
    semantic_visual,
    speech_adaptation,
    speech_evidence_sync,
    speech_reconciliation,
    webrtc_security,
)
from scripts.run_semantic_media_contract_gate import (
    CATALOG,
    DOMAIN_VECTORS,
    PRODUCTION_SOURCE_FILES,
    contract_source_paths,
    validate_catalog,
)
from tests.speech_adaptation_support import speech_job_payload

DIGEST = "a" * 64


def _load(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (float("nan") if value == "__NON_FINITE__" else value) for key, value in raw.items()}


def _extras(raw: Mapping[str, Any], admitted: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in admitted}


def _signature() -> dict[str, str]:
    return {"algorithm": "ed25519", "key_id": "key-a", "value": "signed-value-0001"}


def _envelope(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload: dict[str, Any] = {
        "version": 1,
        "scope": {"kind": "session", "id": "session-a"},
        "sender_id": "peer-a",
        "recipient": {"kind": "peer", "id": "peer-b"},
        "epoch": 1,
        "sequence": values["sequence"],
        "key_id": "key-a",
        "payload_type": "semantic.scene",
        "expires_at_ms": values["expires_at_ms"],
        "nonce_b64": base64.b64encode(bytes(12)).decode("ascii"),
        "aad": {"traffic_class": "semantic", "content_encoding": "json", "contract_digest": DIGEST},
        "ciphertext_b64": base64.b64encode(bytes(16)).decode("ascii"),
    }
    payload.update(_extras(values, {"sequence", "expires_at_ms"}))
    webrtc_security.canonical_security_json(payload)
    webrtc_security.validate_secure_envelope(payload, now_ms=now_ms)


def _permission(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload: dict[str, Any] = {
        "schema": semantic_compute.CAPABILITY_SCHEMA,
        "advertisement_id": "capability-a",
        "session_id": "session-a",
        "epoch": 1,
        "sender_id": "peer-a",
        "algorithms": ["heuristic-visual-v1"],
        "roles": ["executor"],
        "task_types": ["visual_extract"],
        "resource_profile": {
            "cpu": "medium",
            "memory": "medium",
            "gpu": "none",
            "codec": "software",
            "battery": "mains",
            "network": "normal",
        },
        "measurements_expires_at_ms": values["measurements_expires_at_ms"],
        "expires_at_ms": values["measurements_expires_at_ms"],
        "max_delay_ms": values["max_delay_ms"],
        "max_artifact_bytes": 1024,
        "signature": _signature(),
    }
    payload.update(_extras(values, {"max_delay_ms", "measurements_expires_at_ms"}))
    semantic_compute.validate_capability_advertisement(payload, now_ms=now_ms)


def _contract(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload: dict[str, Any] = {
        "schema": semantic_compute.CONTRACT_SCHEMA,
        "contract_id": "contract-a",
        "session_id": "session-a",
        "epoch": 1,
        "revision": 1,
        "issuer": "hub",
        "policy_version": "policy-v1",
        "profile": "balanced",
        "quality_level": "standard",
        "delay_ms": 2_000,
        "security_mode": "strict_e2ee",
        "trusted_compute_grant": False,
        "consent_version": 1,
        "roles": {"primary": ["peer-a"]},
        "task_types": ["visual_extract"],
        "max_artifact_bytes": 1024,
        "deadline_ms": values["deadline_ms"],
        "expires_at_ms": values["expires_at_ms"],
        "contract_digest": DIGEST,
        "signature": _signature(),
    }
    payload["contract_digest"] = semantic_compute.contract_digest(payload)
    payload.update(_extras(values, {"deadline_ms", "expires_at_ms"}))
    semantic_compute.validate_quality_contract(payload, now_ms=now_ms)


def _lease_payload(raw: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    values = _finite_projection(raw)
    expires_at_ms = values["expires_at_ms"]
    issued_at_ms = min(now_ms, expires_at_ms - 1) if isinstance(expires_at_ms, int) else now_ms
    payload: dict[str, Any] = {
        "schema": semantic_compute.LEASE_SCHEMA,
        "lease_id": "lease-a",
        "contract_id": "contract-a",
        "contract_digest": DIGEST,
        "session_id": "session-a",
        "epoch": 1,
        "task_type": "visual_extract",
        "role": "primary",
        "executor_id": values.get("executor_id", "worker-a"),
        "audience": "worker-a",
        "sequence_start": 0,
        "sequence_end": 1,
        "fencing_token": values["fencing_token"],
        "resource_budget": {"cpu_ms": 1, "memory_bytes": 1_048_576, "artifact_bytes": 1},
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
        "deadline_ms": 100,
        "issuer": "hub",
        "signature": _signature(),
    }
    payload.update(_extras(values, {"fencing_token", "expires_at_ms", "executor_id"}))
    return payload


def _lease(raw: Mapping[str, Any], now_ms: int) -> None:
    semantic_compute.validate_task_lease(_lease_payload(raw, now_ms), now_ms=now_ms)


def _scene(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload: dict[str, Any] = {
        "schema": semantic_visual.SCENE_SCHEMA,
        "scene_id": "scene-a",
        "session_id": "session-a",
        "contract_id": "contract-a",
        "contract_digest": DIGEST,
        "epoch": 1,
        "sequence": values["sequence"],
        "source_frame_digest": DIGEST,
        "coordinate_space": {"unit": "normalized", "origin": "top_left", "width": 1, "height": 1},
        "timebase": {"unit": "milliseconds", "captured_at_ms": values["captured_at_ms"], "duration_ms": 0},
        "provenance": {"source": "heuristic", "algorithm": "standard", "version": "v1", "authoritative": False},
        "nodes": [],
        "security": {"classification": "derived_semantic_metadata", "raw_media_included": False},
    }
    payload.update(_extras(values, {"sequence", "captured_at_ms"}))
    semantic_visual.validate_semantic_scene(payload, now_ms=now_ms)


def _speech(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload: dict[str, Any] = {
        "version": "ananta.semantic-speech.v1",
        "kind": "transcript_revision",
        "session_id": "session-a",
        "epoch": 1,
        "turn_id": "turn-a",
        "revision": values["revision"],
        "sender_id": "peer-a",
        "audience_id": "peer-b",
        "consent_version": 1,
        "expires_at_ms": values["expires_at_ms"],
        "contract_digest": DIGEST,
        "source_digest": None,
        "authority": "final",
        "text": "deterministic fixture",
    }
    payload.update(_extras(values, {"revision", "expires_at_ms"}))
    semantic_speech.validate_semantic_speech_transport_payload(
        payload,
        session_id="session-a",
        epoch=1,
        local_peer_id="peer-b",
        remote_peer_id="peer-a",
        consent_version=1,
        contract_digest=DIGEST,
        now_ms=now_ms,
    )


def _evidence(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload_body = {
        "traffic_class": "control",
        "inventory_id": "inventory-a",
        "root_digest": DIGEST,
        "leaf_count": 0,
        "total_bytes": 0,
        "scope_digest": DIGEST,
        "retention_until_ms": now_ms + 1,
        "cursor_digest": DIGEST,
    }
    expires_at_ms = values["expires_at_ms"]
    issued_at_ms = min(now_ms, expires_at_ms - 1) if isinstance(expires_at_ms, int) else now_ms
    payload: dict[str, Any] = {
        "protocol_version": speech_evidence_sync.PROTOCOL_VERSION,
        "message_type": "inventory",
        "message_id": "message-a",
        "session_id": "session-a",
        "pair_id": "pair-a",
        "sender_id": "peer-a",
        "audience_id": "peer-b",
        "epoch": 1,
        "sequence": values["sequence"],
        "consent_version": 1,
        "key_id": "key-a",
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
        "payload_digest": speech_evidence_sync.canonical_sha256(payload_body),
        "payload": payload_body,
        "signature_algorithm": speech_evidence_sync.SIGNATURE_ALGORITHM,
        "signature_b64": base64.b64encode(bytes(64)).decode("ascii"),
    }
    payload.update(_extras(values, {"sequence", "expires_at_ms"}))
    bounded = speech_evidence_sync.parse_bounded_message(payload)
    header = speech_evidence_sync.parse_header(bounded, now_ms=now_ms)
    speech_evidence_sync.validate_payload(header.message_type, payload_body, protocol_version=header.protocol_version)


def _reconciliation(raw: Mapping[str, Any], now_ms: int) -> None:
    values = _finite_projection(raw)
    payload: dict[str, Any] = {
        "contract_version": speech_reconciliation.CONTRACT_VERSION,
        "job_id": "job-a",
        "attempt_id": "attempt-a",
        "fencing_token_digest": DIGEST,
        "fencing_epoch": 1,
        "consent_id": "consent-a",
        "consent_version": values["consent_version"],
        "revocation_epoch": 0,
        "input_manifest_digest": DIGEST,
        "input_lineage_digest": DIGEST,
        "input_artifact_ref": "artifact://speech-evidence/input-a",
        "policy_digest": DIGEST,
        "research_policy_ref": None,
        "source_duration_ms": 1,
        "max_compute_factor": 1,
        "ledger_sequence": 0,
        "key_epoch": 1,
        "deadline_at_ms": values["deadline_at_ms"],
        "stage": "admission",
    }
    payload.update(_extras(values, {"consent_version", "deadline_at_ms"}))
    speech_reconciliation.SpeechReconciliationJob.from_mapping(payload, now_ms=now_ms)


def _training_payload(raw: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    values = _finite_projection(raw)
    payload = speech_job_payload(now_ms=now_ms)
    payload["budget"]["max_artifact_bytes"] = values["artifact_size_bytes"]
    payload["budget"]["budget_digest"] = speech_adaptation.speech_budget_digest(payload["budget"])
    payload["consent"]["expires_at_ms"] = values["consent_expires_at_ms"]
    bindings = {
        "artifact_target_digest": payload["artifact_target"]["target_digest"],
        "attempt_digest": payload["attempt"]["attempt_digest"],
        "budget_digest": payload["budget"]["budget_digest"],
        "config_digest": payload["configuration"]["config_digest"],
        "consent_digest": payload["consent"]["consent_digest"],
        "dataset_digest": payload["dataset"]["dataset_digest"],
        "fencing_digest": payload["fencing"]["fencing_digest"],
        "lineage_digest": payload["dataset"]["lineage_digest"],
        "model_digest": payload["base_model"]["model_digest"],
        "scope_digest": payload["scope"]["scope_digest"],
        "split_digest": payload["dataset"]["split_digest"],
    }
    payload["binding_digest"] = speech_adaptation.speech_job_binding_digest(bindings)
    payload.update(_extras(values, {"artifact_size_bytes", "consent_expires_at_ms"}))
    return payload


def _training(raw: Mapping[str, Any], now_ms: int) -> None:
    payload = _training_payload(raw, now_ms)
    speech_adaptation.canonical_json(payload)
    speech_adaptation.SpeechAdaptationJob.from_mapping(payload, now_ms=now_ms)


PRODUCTION_ADAPTERS: Mapping[str, Callable[[Mapping[str, Any], int], None]] = {
    "envelope": _envelope,
    "permission": _permission,
    "contract": _contract,
    "lease": _lease,
    "scene": _scene,
    "speech": _speech,
    "evidence": _evidence,
    "reconciliation": _reconciliation,
    "training": _training,
}

REASON_COMPATIBILITY: Mapping[str, Mapping[str, str]] = {
    "envelope": {
        "unknown_field": "unknown_field",
        "sequence_invalid": "integer_out_of_bounds",
        "expired": "stale_time",
        "non_finite_or_unserializable": "non_finite",
    },
    "permission": {
        "invalid_capability": "unknown_field",
        "impossible_budget": "integer_out_of_bounds",
        "capability_expired": "stale_time",
    },
    "contract": {
        "invalid_contract": "unknown_field",
        "impossible_budget": "integer_out_of_bounds",
        "contract_expired": "stale_time",
    },
    "lease": {
        "invalid_lease": "unknown_field",
        "impossible_budget": "integer_out_of_bounds",
        "lease_expired": "stale_time",
        "non_finite_value": "non_finite",
        "invalid_executor_id": "unsafe_executor",
    },
    "scene": {
        "invalid_scene": "unknown_field",
        "invalid_integer": "integer_out_of_bounds",
        "stale_scene": "stale_time",
        "non_finite": "non_finite",
    },
    "speech": {
        "semantic_speech_unknown_field": "unknown_field",
        "semantic_speech_context_mismatch": "context_mismatch",
    },
    "evidence": {
        "speech_evidence_unknown_field": "unknown_field",
        "speech_evidence_sequence_invalid": "integer_out_of_bounds",
        "speech_evidence_expired": "stale_time",
    },
    "reconciliation": {
        "speech_reconciliation_shape_invalid": "unknown_field",
        "speech_reconciliation_integer_invalid": "integer_out_of_bounds",
        "speech_reconciliation_deadline_expired": "stale_time",
    },
    "training": {
        "speech_contract_unknown_field": "unknown_field",
        "speech_contract_limit_exceeded": "integer_out_of_bounds",
        "speech_consent_expires_before_deadline": "stale_time",
        "speech_contract_not_canonical": "non_finite",
    },
}


def _reason(exc: BaseException) -> str:
    return str(getattr(exc, "reason_code", getattr(exc, "code", str(exc))))


def _admit_with_production_parser(domain: str, raw: Mapping[str, Any], now_ms: int) -> tuple[bool, str]:
    try:
        PRODUCTION_ADAPTERS[domain](raw, now_ms)
    except (TypeError, ValueError) as exc:
        actual = _reason(exc)
        return False, REASON_COMPATIBILITY[domain].get(actual, f"unmapped:{actual}")
    return True, "accepted"


def test_contract_catalog_is_complete_deterministic_and_path_safe() -> None:
    catalog = _load(CATALOG)
    digest, measurements = validate_catalog(catalog)
    assert len(digest) == 64
    assert measurements["domain_count"] == 9
    assert measurements["case_count"] >= 36
    assert measurements["shared_vector_count"] == 2
    assert measurements["domain_vector_count"] >= 48
    assert measurements["non_finite_vector_count"] >= 3
    serialized = CATALOG.read_text(encoding="utf-8") + DOMAIN_VECTORS.read_text(encoding="utf-8")
    assert "/home/" not in serialized and "C:\\" not in serialized


def test_contract_catalog_rejects_unknown_domain_fields_and_stale_golden_hash() -> None:
    catalog = _load(CATALOG)
    catalog["domains"][0]["permissive"] = True
    with pytest.raises(ProgramEvidenceError, match="contract_domain_shape_invalid"):
        validate_catalog(catalog)
    catalog = _load(CATALOG)
    catalog["canonical_utf8"]["sha256"] = "0" * 64
    with pytest.raises(ProgramEvidenceError, match="contract_canonical_fixture_mismatch"):
        validate_catalog(catalog)


def test_python_uses_shared_canonical_json_and_hash_vectors() -> None:
    catalog = _load(CATALOG)
    for vector in catalog["vectors"]:
        assert speech_evidence_sync.canonical_json(vector["input"]).decode("utf-8") == vector["canonical_json"]
        assert speech_evidence_sync.canonical_sha256(vector["input"]) == vector["sha256"]


def test_source_binding_covers_every_runtime_parser_test_and_schema() -> None:
    catalog = _load(CATALOG)
    paths = set(contract_source_paths(catalog))
    assert PRODUCTION_SOURCE_FILES <= paths
    for row in catalog["domains"]:
        assert row["schema_reference"] in paths
        assert row["python_test"] in paths
        assert row["typescript_test"] in paths
        if row["worker_test"] is not None:
            assert row["worker_test"] in paths


def test_python_executes_every_golden_vector_through_production_parsers() -> None:
    fixture = _load(DOMAIN_VECTORS)
    seen: set[str] = set()
    negative_count = 0
    for row in fixture["domains"]:
        domain = row["name"]
        seen.add(domain)
        for vector in row["vectors"]:
            actual = _admit_with_production_parser(domain, vector["input"], fixture["reference_clock_ms"])
            expected = (vector["expected"]["accepted"], vector["expected"]["reason_code"])
            assert actual == expected, f"{vector['id']}: {actual} != {expected}"
            negative_count += int(not expected[0])
    assert seen == set(PRODUCTION_ADAPTERS)
    assert negative_count >= 30


def test_each_domain_has_real_unknown_bound_and_time_negative_vectors() -> None:
    fixture = _load(DOMAIN_VECTORS)
    for row in fixture["domains"]:
        cases = {vector["case"] for vector in row["vectors"] if not vector["expected"]["accepted"]}
        assert {"unknown-field", "integer-overflow", "stale-time"} <= cases, row["name"]
