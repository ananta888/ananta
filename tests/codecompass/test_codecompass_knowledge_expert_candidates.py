from types import SimpleNamespace

from worker.retrieval.codecompass_knowledge_expert_candidates import (
    CodeCompassKnowledgeExpertCandidateSource,
)
from worker.retrieval.knowledge_expert_index_projector import KnowledgeExpertIndexProjector
from worker.retrieval.knowledge_expert_router import KnowledgeExpertCandidate


class _Retrieval:
    def __init__(self):
        self.payload = None

    def retrieve(self, payload, *, capability):
        self.payload = payload
        return {
            "status": "ok",
            "evidence": [
                {"id": f"expert-manifest:{'a' * 64}", "kind": "knowledge_expert_manifest", "score": 0.9},
                {"id": "ordinary-code", "kind": "symbol", "score": 1.0},
                {"id": f"expert-manifest:{'b' * 64}", "kind": "knowledge_expert_manifest", "score": 0.8},
            ],
        }


class _Catalog:
    def resolve(self, *, manifest_digest):
        tenant = "other" if manifest_digest == "b" * 64 else "tenant-1"
        return KnowledgeExpertCandidate(manifest_digest, tenant, "workspace-1", "repo-1", 0.0)


def test_candidate_source_reuses_codecompass_and_filters_kind_and_scope():
    retrieval = _Retrieval()
    source = CodeCompassKnowledgeExpertCandidateSource(
        retrieval=retrieval,
        catalog=_Catalog(),
        capability={"scope": "delegated"},
        scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1", "repository_id": "repo-1"},
    )
    candidates = source.search(query="retry policy", top_k=3)
    assert [item.manifest_digest for item in candidates] == ["a" * 64]
    assert retrieval.payload["schema"] == "codecompass.agentic-retrieval.v1"
    assert retrieval.payload["scope"]["tenant_id"] == "tenant-1"


def test_candidate_source_never_runs_with_empty_scope():
    retrieval = _Retrieval()
    source = CodeCompassKnowledgeExpertCandidateSource(
        retrieval=retrieval,
        catalog=_Catalog(),
        capability=None,
        scope={"tenant_id": "", "workspace_id": "workspace-1", "repository_id": "repo-1"},
    )
    assert source.search(query="anything", top_k=3) == ()
    assert retrieval.payload is None


def test_projector_emits_only_admitted_scope_bound_codecompass_records():
    manifest = SimpleNamespace(
        manifest_digest="a" * 64,
        expert_id="expert-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        repository_id="repo-1",
        generation_id="generation-1",
    )
    bank = SimpleNamespace(
        status="admitted",
        expert_manifest_digests=(manifest.manifest_digest,),
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        repository_id="repo-1",
    )
    records = KnowledgeExpertIndexProjector().project(
        bank=bank, manifests=[manifest], text_surrogates={manifest.manifest_digest: "payments retry policy"}
    )
    assert records[0]["kind"] == "knowledge_expert_manifest"
    assert records[0]["id"] == f"expert-manifest:{manifest.manifest_digest}"
