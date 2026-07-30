from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.model_intelligence import (
    ANALYSIS_JOB_SCHEMA,
    ARTIFACT_REF_SCHEMA,
    CAPABILITY_DESCRIPTOR_SCHEMA,
    ERROR_ENVELOPE_SCHEMA,
    MODEL_IDENTITY_SCHEMA,
    ErrorEnvelope,
    ModelIdentity,
    ModelIntelligenceContractError,
    ModelIntelligenceReasonCode,
    build_error_envelope,
    derive_model_id,
    parse_model_intelligence_contract,
    sanitize_error_details,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "model_intelligence"
VALID_FIXTURE = FIXTURE_ROOT / "contracts.v1.valid.json"
INVALID_FIXTURE = FIXTURE_ROOT / "contracts.v1.invalid.json"
CONTRACT_MODULE = ROOT / "ananta_contracts" / "model_intelligence.py"
TYPESCRIPT_CONTRACT = (
    ROOT
    / "frontend-angular"
    / "src"
    / "app"
    / "contracts"
    / "model-intelligence.contract.ts"
)
SCHEMA_ROOT = ROOT / "schemas" / "model-intelligence"
SCHEMA_PATHS = {
    "model_identity": SCHEMA_ROOT / "model_identity.v1.json",
    "capability_descriptor": SCHEMA_ROOT / "capability_descriptor.v1.json",
    "analysis_job": SCHEMA_ROOT / "analysis_job.v1.json",
    "artifact_ref": SCHEMA_ROOT / "artifact_ref.v1.json",
    "error_envelope": SCHEMA_ROOT / "error_envelope.v1.json",
}
SCHEMA_IDS = {
    "model_identity": MODEL_IDENTITY_SCHEMA,
    "capability_descriptor": CAPABILITY_DESCRIPTOR_SCHEMA,
    "analysis_job": ANALYSIS_JOB_SCHEMA,
    "artifact_ref": ARTIFACT_REF_SCHEMA,
    "error_envelope": ERROR_ENVELOPE_SCHEMA,
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(kind: str) -> dict:
    schema = _json(SCHEMA_PATHS[kind])
    Draft202012Validator.check_schema(schema)
    return schema


def test_positive_golden_fixture_roundtrips_all_contracts() -> None:
    fixture = _json(VALID_FIXTURE)
    assert fixture["fixture_version"] == (
        "ananta.model-intelligence.contracts.valid.v1"
    )
    assert fixture["_meta"]["deterministic"] is True

    for kind, payload in fixture["contracts"].items():
        errors = list(Draft202012Validator(_schema(kind)).iter_errors(payload))
        assert errors == []
        parsed = parse_model_intelligence_contract(kind, payload)
        assert parsed.to_wire() == payload


def test_negative_golden_fixture_is_rejected_by_schema_and_python() -> None:
    fixture = _json(INVALID_FIXTURE)
    assert fixture["_meta"]["deterministic"] is True

    for case in fixture["cases"]:
        errors = list(
            Draft202012Validator(_schema(case["contract"])).iter_errors(
                case["payload"]
            )
        )
        assert errors
        with pytest.raises(ModelIntelligenceContractError) as raised:
            parse_model_intelligence_contract(
                case["contract"],
                case["payload"],
            )
        assert raised.value.reason_code.value == case["expected_reason_code"]


def test_canonical_coordinates_derive_the_same_id_one_hundred_times() -> None:
    coordinates = {
        "source": "huggingface",
        "locator": "acme/tiny-transformer",
        "revision": "0123456789abcdef",
        "content_sha256": "a" * 64,
    }
    expected = (
        "model_4f992bdd84a4efa805bf8bebb7c06cbd"
        "5755e3406a4b6fe49abcc08907d63ba7"
    )
    assert {derive_model_id(**coordinates) for _ in range(100)} == {expected}
    assert ModelIdentity.create(**coordinates).model_id == expected


def test_model_identity_rejects_a_well_formed_but_wrong_derived_id() -> None:
    valid = _json(VALID_FIXTURE)["contracts"]["model_identity"]
    mismatched = dict(valid, model_id=f"model_{'0' * 64}")
    assert list(
        Draft202012Validator(_schema("model_identity")).iter_errors(
            mismatched
        )
    ) == []
    with pytest.raises(ModelIntelligenceContractError):
        parse_model_intelligence_contract("model_identity", mismatched)


def test_error_details_are_sanitized_and_retryability_is_policy_derived() -> None:
    details = sanitize_error_details(
        {
            "job_id": "analysis-job-001",
            "api_key": "secret",
            "operation": "Bearer hidden",
            "unknown": "not contracted",
        }
    )
    assert details == {"job_id": "analysis-job-001"}

    envelope = build_error_envelope(
        ModelIntelligenceReasonCode.RUNTIME_UNAVAILABLE,
        details=details,
    )
    assert isinstance(envelope, ErrorEnvelope)
    assert envelope.retryable is True
    assert envelope.details == details


def test_contract_module_has_no_route_or_worker_dependencies() -> None:
    tree = ast.parse(CONTRACT_MODULE.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module == "flask"
        or module.startswith("flask.")
        or module == "worker"
        or module.startswith("worker.")
        or module.startswith("agent.routes")
        for module in imported_modules
    )


def test_python_schema_and_typescript_version_markers_do_not_drift() -> None:
    typescript = TYPESCRIPT_CONTRACT.read_text(encoding="utf-8")
    for schema_id in SCHEMA_IDS.values():
        assert schema_id in typescript
    for type_name in (
        "ModelIdentity",
        "CapabilityDescriptor",
        "AnalysisJob",
        "ArtifactRef",
        "ErrorEnvelope",
    ):
        assert f"interface {type_name}" in typescript


def test_golden_fixtures_have_stable_content_digests() -> None:
    digests = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (VALID_FIXTURE, INVALID_FIXTURE)
    }
    assert set(digests) == {
        "contracts.v1.valid.json",
        "contracts.v1.invalid.json",
    }
    assert all(len(digest) == 64 for digest in digests.values())
