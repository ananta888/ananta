"""Closed scheduler facts cannot imply registry issuance or broader scope."""

from dataclasses import FrozenInstanceError

import pytest

from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest


def manifest():
    return PiRuntimeManifest(
        tenant_id="synthetic-tenant", project_id="synthetic-project", worker_id="synthetic-worker",
        worker_url="http://synthetic-worker:5000", repository_revision="a" * 40, image_digest="sha256:" + "b" * 64,
        node_version="24.18.0", pi_version="0.85.1", evidence_scope="test", synthetic=True,
    )


def test_manifest_is_an_immutable_bound_fact_projection_not_an_evidence_identity():
    value = manifest()
    assert PiRuntimeManifest.from_mapping(value.to_dict()) == value
    assert len(value.digest) == len(value.environment_digest) == 64
    assert "SRC_" not in str(value) and "RUN_" not in str(value)
    view = value.to_dict()
    view["project_id"] = "changed"
    assert value.project_id == "synthetic-project"
    with pytest.raises(FrozenInstanceError):
        value.project_id = "changed"


@pytest.mark.parametrize("field,value", [
    ("tenant_id", "foreign tenant"), ("project_id", ""), ("worker_id", 1),
    ("worker_url", "http://user:secret@worker:5000"), ("worker_url", "file:///tmp/worker"),
    ("worker_url", "http://worker:99999"), ("worker_url", "http://worker:5000/"),
    ("worker_url", "http://worker:5000?token=private"), ("worker_url", "http://[malformed"),
    ("repository_revision", "main"), ("image_digest", "ananta:latest"),
    ("node_version", "22.18.9"), ("node_version", "24.18.0-nightly"), ("node_version", True),
    ("pi_version", "latest"), ("pi_version", "0.85.0"), ("execution_profile", "tools-enabled"),
    ("evidence_scope", "production"), ("evidence_scope", "local"), ("evidence_scope", []),
    ("synthetic", "false"), ("synthetic", 1), ("schema", "unknown"),
])
def test_manifest_rejects_coercive_unpinned_or_broadened_facts(field, value):
    raw = manifest().to_dict()
    raw[field] = value
    with pytest.raises(ValueError, match="^pi_runtime_manifest_invalid$"):
        PiRuntimeManifest.from_mapping(raw)


@pytest.mark.parametrize("change", ["missing", "extra", "not_mapping"])
def test_manifest_wire_fields_are_closed(change):
    raw = manifest().to_dict()
    if change == "missing":
        raw.pop("synthetic")
    elif change == "extra":
        raw["caller_evidence_id"] = "not-authority"
    else:
        raw = []
    with pytest.raises(ValueError, match="^pi_runtime_manifest_fields_invalid$"):
        PiRuntimeManifest.from_mapping(raw)
