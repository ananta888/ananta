from __future__ import annotations

import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from agent.services.codecompass_agentic_retrieval_service import (
    AgenticRetrievalContractError,
    CodeCompassAgenticRetrievalService,
)
from agent.services.codecompass_authority_policy import contains_client_authority
from agent.services.codecompass_hardening_migration_adapters import (
    build_filesystem_migration_service,
)
from agent.services.codecompass_rlm_service import CodeCompassRlmService
from agent.services.hub_git_authorization_provisioning import (
    HubGitAuthorizationProvisioningError,
)
from agent.services.hub_git_github_authorization_provider import (
    GitHubAppInstallationSecretResolver,
    migrate_legacy_github_app_reference,
)
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)
from agent.services.mcp_registry_service import MCPRegistryService
from worker.incremental_index.garbage_collector import GarbageCollector
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.knowledge_hygiene.claim_extraction_port import ClaimSpan, admit_claims
from worker.retrieval.duckdb_output_importer import DuckDBOutputImporter
from worker.retrieval.duckdb_snapshot_manager import (
    DuckDBSnapshotManager,
    _pointer_payload,
)
from worker.retrieval.duckdb_vector_store_config import (
    DuckDBResourceConfig,
    DuckDBVectorStoreConfig,
)
from worker.retrieval.vector_store_contract import VectorScope, VectorStoreError
from worker.rlm.recursive_query_planner import RecursivePlan, RetrievalStep


def test_recursive_authority_rejection_is_uniform_and_pre_backend() -> None:
    nested = {"filters": [{"nested": {"credentials": {"token": "forged"}}}]}
    assert contains_client_authority(nested)
    with pytest.raises(ValueError, match="client_authority_forbidden"):
        MCPRegistryService().call_tool(
            name="codecompass.retrieve",
            arguments=nested,
            context={},
        )

    calls = []
    service = CodeCompassAgenticRetrievalService(
        exact_search=lambda *args, **kwargs: calls.append((args, kwargs)) or []
    )
    response = service.retrieve({"query": "symbol", **nested})
    assert response["status"] == "error"
    assert response["reason_code"] == "client_authority_forbidden"
    assert calls == []


def test_missing_capability_never_calls_retrieval_backend() -> None:
    calls = []
    service = CodeCompassAgenticRetrievalService(
        exact_search=lambda *args, **kwargs: calls.append((args, kwargs)) or []
    )
    response = service.retrieve(
        {
            "schema": "codecompass.agentic-retrieval.v1",
            "kind": "request",
            "query": "find symbol",
            "mode": "exact",
            "scope": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "repository_id": "repository-a",
                "source_scope": "repository",
                "revision": "rev-a",
                "allowed_paths": ["src"],
            },
        }
    )
    assert response["status"] == "error"
    assert calls == []


def test_scope_mismatched_hits_are_removed_for_every_channel() -> None:
    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "repository_id": "repository-a",
        "source_scope": "repository",
        "revision": "rev-a",
    }
    for channel in ("codecompass_fts", "codecompass_vector", "codecompass_graph"):
        assert CodeCompassAgenticRetrievalService._hit_matches_scope(
            {"channel": channel, "metadata": dict(scope)},
            scope,
        )
        assert not CodeCompassAgenticRetrievalService._hit_matches_scope(
            {
                "channel": channel,
                "metadata": {**scope, "tenant_id": "tenant-b"},
            },
            scope,
        )


def test_knowledge_projection_rejects_stale_scope_metadata() -> None:
    service = object.__new__(KnowledgeIndexRetrievalService)
    service.search = lambda *args, **kwargs: [
        SimpleNamespace(
            metadata={
                "record_id": "stale",
                "repo_relative_path": "src/stale.py",
                "tenant_id": "tenant-b",
            },
            source="src/stale.py",
            content="stale",
            score=1.0,
        ),
        SimpleNamespace(
            metadata={
                "record_id": "current",
                "repo_relative_path": "src/current.py",
                "tenant_id": "tenant-a",
            },
            source="src/current.py",
            content="current",
            score=0.9,
        ),
    ]
    records = service.search_records(
        "query",
        authoritative_scope={"tenant_id": "tenant-a"},
    )
    assert [item["id"] for item in records] == ["current"]


def _continuation_context() -> tuple[dict, dict, dict]:
    request = {
        "query": "find symbol",
        "mode": "exact",
        "task_kind": "bugfix",
        "retrieval_intent": "implementation",
        "budget": {
            "candidate_limit": 3,
            "top_k": 3,
            "max_chars": 25,
            "max_tokens": 100,
        },
    }
    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "repository_id": "repository-a",
        "source_scope": "repository",
        "revision": "rev-a",
        "allowed_paths": ["src"],
        "subject_id": "user-a",
        "capability_digest": "digest-a",
    }
    plan = {"plan_id": "plan-a", "signals": ["exact"]}
    return request, scope, plan


def test_continuation_expiry_extreme_offset_and_pages() -> None:
    now = [100.0]
    service = CodeCompassAgenticRetrievalService(
        continuation_secret=b"x" * 32,
        continuation_ttl_seconds=1,
        clock=lambda: now[0],
    )
    request, scope, plan = _continuation_context()
    extreme = service._encode_continuation(
        99,
        request=request,
        scope=scope,
        plan=plan,
    )
    with pytest.raises(AgenticRetrievalContractError):
        service._continuation_offset(
            extreme,
            request=request,
            scope=scope,
            plan=plan,
        )

    selected = [
        {
            "record_id": f"r{index}",
            "path": f"src/{index}.py",
            "content": "x" * 20,
            "content_hash": f"h{index}",
            "metadata": {},
        }
        for index in range(3)
    ]
    first, truncated, handle, _usage = service._budget_evidence(
        selected,
        request=request,
        scope=scope,
        plan=plan,
    )
    assert truncated and handle
    second_request = {**request, "continuation_handle": handle}
    second, _truncated, _handle, _usage = service._budget_evidence(
        selected,
        request=second_request,
        scope=scope,
        plan=plan,
    )
    assert {item["id"] for item in first}.isdisjoint({item["id"] for item in second})

    now[0] = 102.0
    with pytest.raises(AgenticRetrievalContractError):
        service._continuation_offset(
            handle,
            request=request,
            scope=scope,
            plan=plan,
        )


class _CyclePlanner:
    max_steps = 4

    def create_plan(self, query, graph=None, root_handles=None):
        return RecursivePlan(
            plan_id="cycle-plan",
            query=query,
            steps=[RetrievalStep("step-a", "A", "exact", 0)],
            max_depth=3,
            max_fanout=1,
        )

    def expand_step(self, parent, evidence):
        if parent.query == "A":
            return [RetrievalStep(f"{parent.step_id}-b", "B", "exact", parent.depth + 1)]
        return [RetrievalStep(f"{parent.step_id}-a", "A", "exact", parent.depth + 1)]


def test_rlm_depth_two_cycle_is_traced_and_bounded(monkeypatch) -> None:
    retrieval = SimpleNamespace(
        retrieve=lambda payload, capability=None: {
            "status": "ok",
            "evidence": [],
        }
    )
    monkeypatch.setattr(
        "agent.services.codecompass_rlm_service.get_codecompass_agentic_retrieval_service",
        lambda: retrieval,
    )
    result = CodeCompassRlmService(planner=_CyclePlanner()).analyze(
        "why does this architecture cross the complete repository boundary",
        enabled=True,
        max_depth=3,
        max_fanout=1,
        max_steps=4,
    )
    assert result["status"] == "executed"
    assert any(item.get("depth") == 2 for item in result["trace"])
    assert any(item.get("stopped") == "cycle_detected" for item in result["trace"])
    assert len(result["trace"]) <= 4


def test_claim_admission_binds_source_run_revision_and_content() -> None:
    source = "fact"
    digest = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
    accepted = admit_claims(
        source,
        [ClaimSpan("fact", 0, 4)],
        source_ref="SRC_FACT",
        revision="rev-1",
        allowed_source_refs={"SRC_FACT"},
        run_ref="RUN_FACT",
        allowed_run_refs={"RUN_FACT"},
        allowed_revisions={"rev-1"},
        content_digest=digest,
    )
    assert accepted["status"] == "ok"
    assert accepted["source_verification_status"] == "verified"
    assert accepted["run_verification_status"] == "verified"
    assert accepted["content_digest"] == digest

    for kwargs, reason in (
        ({"run_ref": "RUN_UNKNOWN", "allowed_run_refs": {"RUN_FACT"}}, "unknown_run_reference"),
        ({"allowed_revisions": {"rev-2"}}, "revision_not_allowed"),
        ({"content_digest": "sha256:" + "0" * 64}, "content_digest_mismatch"),
    ):
        rejected = admit_claims(
            source,
            [ClaimSpan("fact", 0, 4)],
            source_ref="SRC_FACT",
            revision="rev-1",
            allowed_source_refs={"SRC_FACT"},
            **kwargs,
        )
        assert rejected["status"] == "rejected"
        assert rejected["reason"] == reason


def test_layer_scope_keys_rollback_and_stale_cas(tmp_path) -> None:
    registry = LayerHeadRegistry(tmp_path)
    first_key = registry.scoped_profile_id(
        "default",
        tenant_id="tenant-a",
        workspace_id="workspace",
        repository_id="repository",
    )
    second_key = registry.scoped_profile_id(
        "default",
        tenant_id="tenant-b",
        workspace_id="workspace",
        repository_id="repository",
    )
    assert first_key != second_key
    assert registry.create_head(
        first_key,
        layer_id="layer-1",
        snapshot_revision="rev-1",
        layer_set={"symbols": "layer-1"},
    ).new_generation == 1
    assert registry.update_head(
        first_key,
        expected_generation=1,
        new_layer_id="layer-2",
        snapshot_revision="rev-2",
        append_delta=False,
        new_layer_set={"symbols": "layer-2"},
        replace_artifact_kinds=["symbols"],
    ).new_generation == 2
    rolled_back = registry.rollback(
        first_key,
        target_generation=1,
        expected_generation=2,
    )
    assert rolled_back.new_generation == 3
    assert registry.get_head_history(first_key)[-1]["layer_set"] == {"symbols": "layer-1"}
    stale = registry.rollback(
        first_key,
        target_generation=1,
        expected_generation=2,
    )
    assert stale.new_generation == 3
    assert not stale.success


def test_gc_preserves_in_flight_layers() -> None:
    deleted = []
    store = SimpleNamespace(
        list_layers=lambda: [
            SimpleNamespace(layer_id="protected", size_bytes=1),
            SimpleNamespace(layer_id="dead", size_bytes=1),
        ],
        delete_layer=lambda layer_id: deleted.append(layer_id),
    )
    heads = SimpleNamespace(list_profiles=lambda: [], get_head=lambda profile: None)
    result = GarbageCollector(store, heads).collect(
        "default",
        dry_run=False,
        protected_layer_ids={"protected"},
    )
    assert deleted == ["dead"]
    assert result.swept_artifacts == 1


def test_duckdb_import_budget_counts_utf8_bytes(tmp_path) -> None:
    config = DuckDBVectorStoreConfig(
        snapshot_root=tmp_path,
        resources=DuckDBResourceConfig(max_import_bytes=1024),
    )
    importer = DuckDBOutputImporter(config)
    connection = SimpleNamespace(execute=lambda *args, **kwargs: None)
    with pytest.raises(VectorStoreError, match="duckdb_import_budget_exceeded"):
        importer.import_records(
            connection,
            records=[{"id": "r1", "path": "src/a.py", "text": "€" * 400}],
            scope=VectorScope("workspace", "repository"),
            manifest_hash="manifest",
        )


def test_duckdb_snapshot_paths_are_scope_safe_and_pointer_writes_serialize(tmp_path) -> None:
    manager = DuckDBSnapshotManager(DuckDBVectorStoreConfig(snapshot_root=tmp_path))
    scope = VectorScope("../workspace", "../../repository", "../profile")
    snapshot_path = manager.snapshot_path(scope, "../fingerprint", "../../version")
    assert tmp_path.resolve() in snapshot_path.resolve().parents
    assert ".." not in snapshot_path.parts
    assert "workspace" not in snapshot_path.parts

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"snapshot")
    payload = _pointer_payload(
        path=snapshot_path,
        scope=scope,
        manifest_hash="manifest",
        compatibility_fingerprint="fingerprint",
        source_revision="revision",
    )
    failures = []

    def publish_pointer() -> None:
        try:
            manager._write_pointer(scope, payload)
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [threading.Thread(target=publish_pointer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert manager.read_pointer(scope)["generation"] == 2


class _SuspendedGitHubApi:
    def __init__(self) -> None:
        self.token_calls = 0

    def inspect_installation(self, *, installation_id, app_jwt):
        return {"id": installation_id, "suspended_at": "2026-08-20T00:00:00Z"}

    def create_installation_token(self, **kwargs):
        self.token_calls += 1
        return {"token": "must-not-be-issued"}


def test_suspended_github_installation_and_legacy_refs_fail_closed() -> None:
    api = _SuspendedGitHubApi()
    resolver = GitHubAppInstallationSecretResolver(
        api=api,
        jwt_issuer=SimpleNamespace(issue=lambda: "jwt"),
    )
    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_github_installation_inactive",
    ):
        resolver.resolve(
            "secret://github-app/installation/7/repository/owner%2Frepository"
        )
    assert api.token_calls == 0

    legacy = "secret://github-app/installation/7"
    assert migrate_legacy_github_app_reference(legacy)["status"] == "invalidated"
    migrated = migrate_legacy_github_app_reference(
        legacy,
        repository="owner/repository",
        dry_run=False,
    )
    assert migrated["status"] == "migrated"
    assert migrated["reference"].endswith("/repository/owner%2Frepository")


def test_filesystem_migration_is_observable_idempotent_and_reversible(tmp_path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "layer.json").write_text(
        json.dumps({"kind": "layer_head", "profile_id": "profile-a"}),
        encoding="utf-8",
    )
    events = []
    service = build_filesystem_migration_service(
        inventory_roots={"layer_head": legacy},
        journal_root=tmp_path / "journal",
        output_root=tmp_path / "output",
        writes_enabled=True,
        observer=lambda event: events.append(dict(event)),
    )
    plan = service.plan()
    assert len(plan["operations"]) == 1
    first = service.run(dry_run=False)
    second = service.run(dry_run=False)
    assert first["migration_id"] == second["migration_id"]
    assert any(item["event"] == "migration_completed" for item in events)
    rollback = service.rollback(first["migration_id"])
    assert rollback["status"] == "rolled_back"
    assert not list((tmp_path / "output").glob("*.json"))
