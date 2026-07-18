from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.retrieval import SourceRef


def _authorized_source_id() -> str:
    source_id = os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID", "").strip()
    if not source_id:
        pytest.skip("authoritative_source_evidence_unavailable")
    return source_id


def test_source_ref_v2_round_trip_and_schema() -> None:
    source_id = _authorized_source_id()
    value = SourceRef(
        source_id=source_id,
        source_version="snapshot-7",
        tenant_id="tenant-a",
        scope="repo",
        provenance_digest="a" * 64,
    )
    payload = value.to_dict()
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "source" / "source_ref.v2.json").read_text(encoding="utf-8")
    )

    assert SourceRef.from_mapping(payload) == value
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"schema": "ananta.source_ref.v1"}, "source_ref_schema_invalid"),
        ({"source_id": "SRC_PATH_docs"}, "source_ref_id_invalid"),
        ({"source_version": ""}, "source_ref_version_invalid"),
        ({"tenant_id": ""}, "source_ref_tenant_invalid"),
        ({"scope": ""}, "source_ref_scope_invalid"),
        ({"provenance_digest": "short"}, "source_ref_provenance_digest_invalid"),
    ],
)
def test_source_ref_v2_rejects_unqualified_or_invented_identity(overrides, reason) -> None:
    source_id = "not-a-source-id" if "source_id" in overrides else _authorized_source_id()
    values = {
        "source_id": source_id,
        "source_version": "snapshot-7",
        "tenant_id": "tenant-a",
        "scope": "repo",
        "provenance_digest": "a" * 64,
        **overrides,
    }

    with pytest.raises(ValueError, match=reason):
        SourceRef(**values)
