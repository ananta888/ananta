from __future__ import annotations

from agent.services.chat_process_binding import public_graph
from agent.services.opaque_secret_reference_service import OpaqueSecretReferenceService
from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
from agent.visual_process.step_adapters import EmbedApiAdapter, RerankAdapter
from agent.visual_process.validator import ValidationIssue, ValidationResult, VisualProcessValidator


def test_legacy_plaintext_embedding_secret_is_quarantined_from_read_and_execution() -> None:
    graph = {
        "id": "legacy",
        "metadata": {"kept": True},
        "steps": [
            {
                "id": "embed",
                "metadata": {"provider": "openai_compatible", "api_key": "must-not-leak"},
            }
        ],
    }

    projection = public_graph(graph)
    assert projection["steps"][0]["metadata"] == {"provider": "openai_compatible"}
    assert projection["security_quarantine"] == {
        "reason_code": "legacy_inline_secret_quarantined",
        "field_paths": ["/steps/0/metadata/api_key"],
        "migration": "remove legacy value and explicitly set an opaque *_secret_ref",
    }
    assert graph["steps"][0]["metadata"]["api_key"] == "must-not-leak"

    result = EmbedApiAdapter().execute(
        VisualProcessStep(
            id="embed",
            label="Embed",
            kind="embed_api",
            metadata={
                "provider": "openai_compatible",
                "api_key": "must-not-leak",
                "texts": ["hello"],
                "external_calls_allowed": True,
            },
        ),
        {},
        {},
    )
    assert result.status == "failed"
    assert result.diagnostics == {"error": "legacy_plaintext_api_key_quarantined"}
    assert "must-not-leak" not in str(result.as_dict())


def test_embedding_adapter_resolves_only_an_opaque_environment_reference() -> None:
    resolver = OpaqueSecretReferenceService({"EMBEDDING_KEY": "resolved-secret"})
    adapter = EmbedApiAdapter(secret_resolver=resolver)
    provider = adapter._build_provider(  # contract seam; no network request
        VisualProcessStep(
            id="embed",
            label="Embed",
            kind="embed_api",
            metadata={
                "provider": "openai_compatible",
                "base_url": "http://embedding.local/v1",
                "api_key_secret_ref": "env://EMBEDDING_KEY",
                "external_calls_allowed": True,
            },
        ),
        "openai_compatible",
    )
    assert provider.api_key == "resolved-secret"


def test_embed_validation_and_issue_serialization_are_fail_closed_and_stable() -> None:
    graph = VisualProcessGraph(
        id="legacy",
        name="Legacy",
        steps=[
            VisualProcessStep(
                id="embed",
                label="Embed",
                kind="embed_api",
                metadata={"provider": "openai_compatible", "api_key": "secret"},
            )
        ],
    )
    result = VisualProcessValidator().validate(graph)
    codes = {issue.code for issue in result.errors()}
    assert {
        "embed_api_plaintext_secret_quarantined",
        "embed_api_secret_reference_required",
        "embed_api_external_calls_not_allowed",
    } <= codes

    serialized = ValidationResult(
        valid=False,
        issues=[
            ValidationIssue("warning", "z-code", "Z", path="/z"),
            ValidationIssue("error", "b-code", "B", path="/b"),
            ValidationIssue("error", "a-code", "A", path="/a"),
        ],
    ).as_dict()
    assert [(item["severity"], item["code"], item["path"]) for item in serialized["issues"]] == [
        ("error", "a-code", "/a"),
        ("error", "b-code", "/b"),
        ("warning", "z-code", "/z"),
    ]


def test_reranker_reads_legacy_weight_but_canonical_weight_wins() -> None:
    adapter = RerankAdapter()
    artifacts = {"query": "alpha", "candidates": [{"text": "alpha", "score": 0.2}]}
    legacy = adapter.execute(
        VisualProcessStep(
            id="rerank",
            label="Rerank",
            kind="rerank",
            metadata={"reranker_weight": 0.4},
        ),
        artifacts,
        {},
    )
    canonical = adapter.execute(
        VisualProcessStep(
            id="rerank",
            label="Rerank",
            kind="rerank",
            metadata={"reranker_weight": 0.4, "weight": 0.1},
        ),
        artifacts,
        {},
    )
    assert legacy.outputs["reranked"][0]["final_score"] == 0.4
    assert canonical.outputs["reranked"][0]["final_score"] == 0.1
