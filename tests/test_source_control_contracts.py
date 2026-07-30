from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ananta_contracts.source_control import (
    DestinationDescriptor,
    SourceControlContractError,
    SourceRevision,
    derive_destination_id,
    parse_source_control_contract,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "source_control"
VALID_FIXTURE = FIXTURE_ROOT / "contracts.v1.valid.json"
INVALID_FIXTURE = FIXTURE_ROOT / "contracts.v1.invalid.json"
CONTRACT_MODULE = ROOT / "ananta_contracts" / "source_control.py"
SCHEMA_ROOT = ROOT / "schemas" / "source-control"
SCHEMA_PATHS = {
    "source_connection": SCHEMA_ROOT / "source_connection.v1.json",
    "source_revision": SCHEMA_ROOT / "source_revision.v1.json",
    "source_ref_mapping": SCHEMA_ROOT / "source_ref_mapping.v1.json",
    "destination_descriptor": SCHEMA_ROOT / "destination_descriptor.v1.json",
    "source_access_grant": SCHEMA_ROOT / "source_access_grant.v1.json",
    "delegated_source_manifest_ref": (
        SCHEMA_ROOT / "delegated_source_manifest_ref.v1.json"
    ),
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(kind: str) -> dict:
    schema = _json(SCHEMA_PATHS[kind])
    Draft202012Validator.check_schema(schema)
    return schema


def test_positive_golden_fixture_roundtrips_every_contract() -> None:
    fixture = _json(VALID_FIXTURE)
    assert fixture["fixture_version"] == (
        "ananta.source-control.contracts.valid.v1"
    )
    assert fixture["_meta"]["deterministic"] is True

    parsed = {}
    for kind, payload in fixture["contracts"].items():
        assert list(
            Draft202012Validator(_schema(kind)).iter_errors(payload)
        ) == []
        parsed[kind] = parse_source_control_contract(kind, payload)
        assert parsed[kind].to_wire() == payload

    connection = parsed["source_connection"]
    revision = parsed["source_revision"]
    source_ref = parsed["source_ref_mapping"]
    assert len(
        {
            connection.connection_id,
            revision.source_revision_id,
            source_ref.source_ref_id,
        }
    ) == 3
    assert source_ref.connection_id == connection.connection_id
    assert source_ref.source_revision_id == revision.source_revision_id


def test_negative_fixture_is_fail_closed_in_schema_and_python() -> None:
    valid = _json(VALID_FIXTURE)["contracts"]
    fixture = _json(INVALID_FIXTURE)
    assert fixture["_meta"]["deterministic"] is True

    for case in fixture["cases"]:
        payload = dict(valid[case["contract"]])
        payload.update(case["set"])
        errors = list(
            Draft202012Validator(_schema(case["contract"])).iter_errors(
                payload
            )
        )
        assert bool(errors) is case["schema_rejects"], case["name"]
        with pytest.raises(SourceControlContractError):
            parse_source_control_contract(case["contract"], payload)


def test_provider_or_model_change_produces_a_new_destination_identity() -> None:
    payload = _json(VALID_FIXTURE)["contracts"]["destination_descriptor"]
    original = DestinationDescriptor.model_validate(payload)
    coordinates = {
        key: payload[key]
        for key in (
            "worker_id",
            "worker_kind",
            "runtime_id",
            "runtime_kind",
            "provider_id",
            "model_id",
            "model_class",
            "provider_location",
            "data_residency",
        )
    }
    provider_changed = dict(coordinates, provider_id="provider-other")
    model_changed = dict(coordinates, model_id="model-other")

    assert derive_destination_id(**provider_changed) != original.destination_id
    assert derive_destination_id(**model_changed) != original.destination_id
    assert (
        derive_destination_id(**provider_changed)
        != derive_destination_id(**model_changed)
    )


def test_source_revision_contract_is_immutable() -> None:
    payload = _json(VALID_FIXTURE)["contracts"]["source_revision"]
    revision = SourceRevision.model_validate(payload)

    with pytest.raises(ValidationError):
        revision.admission_state = "blocked"


def test_worker_contract_contains_only_hub_issued_ids_and_digests() -> None:
    payload = _json(VALID_FIXTURE)["contracts"][
        "delegated_source_manifest_ref"
    ]
    assert set(payload) == {
        "schema",
        "authority",
        "manifest_id",
        "manifest_digest",
        "source_revision_id",
        "destination_id",
        "source_access_grant_id",
        "policy_version",
    }
    assert not {
        "connector_config",
        "credential",
        "path",
        "provider_id",
        "model_id",
        "cloud_effective",
        "external_effective",
    }.intersection(payload)


def test_contract_layer_has_no_hub_route_persistence_or_worker_dependency() -> None:
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
    forbidden_prefixes = (
        "agent.",
        "flask",
        "sqlalchemy",
        "worker",
    )
    assert not any(
        module == prefix.rstrip(".") or module.startswith(prefix)
        for module in imported_modules
        for prefix in forbidden_prefixes
    )


def test_fixtures_do_not_invent_grounded_source_or_run_ids() -> None:
    fixture_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (VALID_FIXTURE, INVALID_FIXTURE)
    )
    assert re.search(r"\b(?:SRC|RUN)_[A-Za-z0-9_-]+\b", fixture_text) is None
