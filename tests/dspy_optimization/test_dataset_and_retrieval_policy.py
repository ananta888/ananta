from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.dspy_dataset_policy_service import DspyDatasetPolicyService
from ananta_contracts.dspy_optimization import DatasetManifestV1, canonical_digest
from worker.optimization.dspy.retriever_bridge import CodeCompassDspyRetrieverBridge


def _dataset(records: list[dict], *, sources: tuple[str, ...] = ("SRC_dataset",)) -> DatasetManifestV1:
    ids = [str(value["record_id"]) for value in records]
    return DatasetManifestV1(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version=1,
        content_digest=canonical_digest(records),
        record_schema_digest=canonical_digest(sorted(records[0])),
        split_digests={"train": "a" * 64, "validation": "b" * 64, "test": "c" * 64},
        split_record_ids={"train": ids[:1], "validation": ids[1:2], "test": ids[2:]},
        license_id="MIT",
        sensitivity="internal",
        retention_days=30,
        source_refs=sources,
    )


def test_dataset_admission_binds_content_splits_sources_and_privacy() -> None:
    records = [
        {"record_id": "r1", "input": "a", "output": "b"},
        {"record_id": "r2", "input": "c", "output": "d"},
        {"record_id": "r3", "input": "e", "output": "f"},
    ]
    service = DspyDatasetPolicyService(allowed_source_refs=frozenset({"SRC_dataset"}))
    assert service.admit(_dataset(records), records)["admitted"] is True
    poisoned = [{**records[0], "input": "api_key=secret"}, *records[1:]]
    result = service.admit(replace(_dataset(poisoned), content_digest=canonical_digest(poisoned)), poisoned)
    assert result["admitted"] is False
    assert "dspy_dataset_secret_detected" in result["reason_codes"]
    result = service.admit(_dataset(records, sources=("SRC_unknown",)), records)
    assert result["reason_codes"] == ["dspy_dataset_source_unverified"]
    denied_license = replace(_dataset(records), license_id="proprietary-unapproved")
    assert service.admit(denied_license, records)["reason_codes"] == ["dspy_dataset_license_denied"]


class RetrievalPort:
    def __init__(self, content: str = "bounded context") -> None:
        self.content = content

    def retrieve(self, **_kwargs):
        import hashlib

        return [
            {
                "source_ref": "SRC_allowed",
                "content": self.content,
                "score": 0.9,
                "content_digest": hashlib.sha256(self.content.encode()).hexdigest(),
            }
        ]


def test_retrieval_is_scope_source_digest_and_budget_bound() -> None:
    events: list[dict] = []
    bridge = CodeCompassDspyRetrieverBridge(
        RetrievalPort(),
        trusted_scope={
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "repository_id": "repo-1",
            "profile_id": "hybrid-v1",
            "role_id": "researcher",
        },
        allowed_source_refs=["SRC_allowed"],
        max_queries=1,
        audit_sink=lambda event: events.append(dict(event)),
    )
    assert bridge.retrieve("query", top_k=1)[0]["source_ref"] == "SRC_allowed"
    assert events[0]["result_count"] == 1
    with pytest.raises(RuntimeError, match="budget_exhausted"):
        bridge.retrieve("query 2", top_k=1)


def test_retrieval_fails_closed_for_unknown_source_and_backend_failure() -> None:
    scope = {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "profile_id": "hybrid-v1",
        "role_id": "researcher",
    }
    bridge = CodeCompassDspyRetrieverBridge(RetrievalPort(), trusted_scope=scope, allowed_source_refs=["SRC_different"])
    with pytest.raises(ValueError, match="result_invalid"):
        bridge.retrieve("query", top_k=1)

    class FailedPort:
        def retrieve(self, **_kwargs):
            raise ConnectionError("secret endpoint failed")

    bridge = CodeCompassDspyRetrieverBridge(FailedPort(), trusted_scope=scope, allowed_source_refs=[])
    with pytest.raises(RuntimeError, match="backend_unavailable") as error:
        bridge.retrieve("query", top_k=1)
    assert "secret endpoint" not in str(error.value)
