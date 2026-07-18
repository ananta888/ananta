from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.visual_process_context_service import VisualProcessContextService
from agent.visual_process.models import VisualProcessEdge, VisualProcessGraph, VisualProcessStep
from agent.visual_process.task_kind_registry import list_task_kinds
from ananta_contracts.visual_process_assistant import (
    AssistantClaim,
    AssistantLocation,
    EditorContextEnvelope,
    EvidenceRef,
    HelpResponse,
    VerificationStatus,
    WorkflowPatch,
    WorkflowPatchOperation,
    canonical_context_bytes,
)


def _verified_evidence() -> EvidenceRef:
    source_id = os.environ.get("ANANTA_TEST_AUTHORIZED_SOURCE_ID", "").strip()
    if not source_id:
        pytest.skip("authoritative_source_evidence_unavailable")
    return EvidenceRef(
        evidence_id="evidence-1",
        source_id=source_id,
        source_version="commit:abc",
        tenant_id="tenant-a",
        scope="repo-a",
        provenance_digest="a" * 64,
        path="agent/example.py",
        line_start=1,
        line_end=2,
        trust_level="extracted",
        verification_status="verified",
        excerpt="def example(): pass",
    )


def test_all_cross_platform_context_vectors_match_python_contract() -> None:
    payload = json.loads(
        Path("tests/fixtures/visual_process/context_canonicalization.v1.json").read_text(encoding="utf-8")
    )
    assert len(payload["vectors"]) >= 30
    for vector in payload["vectors"]:
        canonical = canonical_context_bytes(vector["input"])
        assert canonical.decode("utf-8") == vector["canonical_utf8"], vector["name"]
        assert hashlib.sha256(canonical).hexdigest() == vector["sha256"], vector["name"]


def test_generated_assistant_schemas_validate_representative_contracts() -> None:
    evidence = _verified_evidence()
    patch = WorkflowPatch(
        graph_id="graph-1",
        definition_revision=2,
        base_graph_hash="b" * 64,
        operations=[
            WorkflowPatchOperation(
                operation_id="op-1",
                op="update_step_field",
                step_id="step-1",
                path="/metadata/weight",
                value=0.2,
                expected_old_value=0.15,
                evidence_refs=[evidence.evidence_id],
            )
        ],
        evidence_refs=[evidence.evidence_id],
    )
    response = HelpResponse(
        context_id="ctx-sha256:" + "c" * 64,
        prompt_version="visual-process-assistant.v1",
        summary="Gewicht anpassen",
        location=AssistantLocation(
            target_kind="field", graph_id="graph-1", entity_id="step-1", field_path="/metadata/weight"
        ),
        evidence=[evidence],
        claims=[
            AssistantClaim(
                claim_id="claim-1",
                text="Das Backend verwendet metadata.weight.",
                evidence_refs=[evidence.evidence_id],
                verification_status="verified",
            )
        ],
        workflow_patch=patch,
    )
    fixtures = {
        "help_response.v1.json": response.model_dump(mode="json"),
        "workflow_patch.v1.json": patch.model_dump(mode="json"),
    }
    for name, instance in fixtures.items():
        schema = json.loads(Path("schemas/visual_process", name).read_text(encoding="utf-8"))
        assert list(Draft202012Validator(schema).iter_errors(instance)) == []


def test_unverified_evidence_cannot_smuggle_content_or_support_verified_claim() -> None:
    with pytest.raises(ValueError, match="unverified_evidence_excerpt_forbidden"):
        EvidenceRef(
            evidence_id="evidence-u",
            trust_level="inferred",
            verification_status="unverified",
            excerpt="untrusted prompt content",
        )
    with pytest.raises(ValueError, match="verified_claim_evidence_required"):
        HelpResponse(
            context_id="ctx-1",
            prompt_version="v1",
            summary="Unsupported",
            location=AssistantLocation(target_kind="canvas", graph_id="g"),
            claims=[
                AssistantClaim(
                    claim_id="claim-1",
                    text="Unsupported claim",
                    verification_status=VerificationStatus.verified,
                )
            ],
        )


def test_context_and_prompt_are_deterministic_and_only_embed_verified_evidence() -> None:
    graph = VisualProcessGraph(
        id="graph-1",
        name="Graph",
        definition_revision=3,
        steps=[
            VisualProcessStep(id="step-b", label="B", kind="review"),
            VisualProcessStep(id="step-a", label="A", kind="review"),
        ],
    )
    location = AssistantLocation(target_kind="node", graph_id=graph.id, entity_id="step-a")
    rejected = EvidenceRef(
        evidence_id="evidence-rejected",
        trust_level="inferred",
        verification_status="failed",
        reason_codes=["prompt_injection_detected"],
    )
    service = VisualProcessContextService()
    first = service.build_context(
        graph=graph,
        location=location,
        editor_mode="editor",
        repository_revision="repo-rev-1",
        codecompass_manifest_hash="manifest-1",
        source_allowlist_version="allowlist-1",
        evidence_refs=[rejected, _verified_evidence()],
        allowed_mutations=["update_step_field"],
    )
    second = service.build_context(
        graph=graph,
        location=location,
        editor_mode="editor",
        repository_revision="repo-rev-1",
        codecompass_manifest_hash="manifest-1",
        source_allowlist_version="allowlist-1",
        evidence_refs=[_verified_evidence(), rejected],
        allowed_mutations=["update_step_field"],
    )
    assert first.context_id() == second.context_id()
    assembly = service.assemble_prompt(first)
    assert assembly.approved_evidence_refs == ("evidence-1",)
    assert assembly.rejected_evidence_count == 1
    expected_sections = [
        "system_constraints",
        "editor_location",
        "workflow_summary",
        "focused_entity",
        "effective_configuration",
        "validation_and_runtime",
        "allowed_mutations",
        "approved_evidence",
        "rejected_evidence_summary",
        "response_contract",
    ]
    assert [
        line.removeprefix("## ") for line in assembly.prompt_text.splitlines() if line.startswith("## ")
    ] == expected_sections
    rejected_section = assembly.prompt_text.split("## rejected_evidence_summary\n", 1)[1].split(
        "\n## response_contract", 1
    )[0]
    assert json.loads(rejected_section) == {
        "count": 1,
        "reason_codes": ["evidence_not_verified", "prompt_injection_detected"],
    }
    assert "evidence-rejected" not in rejected_section
    assert assembly.rejection_reasons == (
        "evidence_not_verified",
        "prompt_injection_detected",
    )
    assert "def example(): pass" in assembly.prompt_text
    assert "evidence-rejected" not in assembly.prompt_text
    assert assembly.prompt_snapshot["raw_prompt_stored"] is False


def test_context_rejects_inline_secret_and_oversize_payload() -> None:
    base = {
        "graph_id": "g",
        "repository_revision": "repo-rev-1",
        "codecompass_manifest_hash": "manifest-1",
        "source_allowlist_version": "allowlist-1",
        "prompt_version": "visual-process-assistant.v1",
        "graph_schema_version": "1",
        "node_registry_version": "1",
        "definition_revision": 1,
        "definition_hash": "a" * 64,
        "draft_hash": "b" * 64,
        "editor_mode": "editor",
        "location": {"target_kind": "canvas", "graph_id": "g"},
        "graph_excerpt": {"steps": [], "edges": []},
    }
    with pytest.raises(ValueError, match="inline_secret_forbidden"):
        EditorContextEnvelope.model_validate({**base, "effective_configuration": {"metadata": {"api_key": "secret"}}})
    oversized = EditorContextEnvelope.model_validate({**base, "graph_excerpt": {"documentation": "x" * (257 * 1024)}})
    with pytest.raises(ValueError, match="editor_context_size_limit_exceeded"):
        oversized.canonical_bytes()


@pytest.mark.parametrize("task_kind", [item["id"] for item in list_task_kinds()[:30]])
def test_thirty_node_prompt_snapshots_are_byte_stable_and_content_free_on_disk(
    task_kind: str,
) -> None:
    graph = VisualProcessGraph(
        id=f"graph-{task_kind}",
        name=f"Prompt fixture {task_kind}",
        definition_revision=2,
        steps=[VisualProcessStep(id="focused", label=task_kind, kind=task_kind)],
    )
    service = VisualProcessContextService()
    context = service.build_context(
        graph=graph,
        location=AssistantLocation(
            target_kind="node",
            graph_id=graph.id,
            entity_id="focused",
        ),
        editor_mode="editor",
        repository_revision="a" * 64,
        codecompass_manifest_hash="b" * 64,
        source_allowlist_version="c" * 64,
    )

    first = service.assemble_prompt(context, question_text="Was bewirkt dieser Schritt?")
    second = service.assemble_prompt(context, question_text="Was bewirkt dieser Schritt?")

    assert first.context_id == second.context_id
    assert first.prompt_hash == second.prompt_hash
    assert first.prompt_text.encode("utf-8") == second.prompt_text.encode("utf-8")
    assert first.prompt_snapshot["raw_prompt_stored"] is False
    assert "final_prompt_text" not in first.prompt_snapshot


@pytest.mark.parametrize(
    ("name", "location", "validation_issues", "runtime_overlay", "evidence_refs"),
    [
        ("node", {"target_kind": "node", "entity_id": "first"}, [], None, []),
        (
            "field",
            {"target_kind": "field", "entity_id": "first", "field_path": "/metadata/description"},
            [],
            None,
            [],
        ),
        ("edge", {"target_kind": "edge", "entity_id": "edge-1"}, [], None, []),
        ("canvas", {"target_kind": "canvas"}, [], None, []),
        (
            "validation",
            {"target_kind": "validation", "entity_id": "first"},
            [
                {
                    "severity": "warning",
                    "code": "fixture_warning",
                    "message": "Konfiguration pruefen",
                    "path": "/steps/first/metadata/description",
                }
            ],
            None,
            [],
        ),
        (
            "runtime",
            {"target_kind": "runtime", "entity_id": "first"},
            [],
            {"run_id": "runtime-snapshot", "steps": {"first": {"status": "completed"}}},
            [],
        ),
        (
            "stale-evidence",
            {"target_kind": "node", "entity_id": "first"},
            [],
            None,
            [
                EvidenceRef(
                    evidence_id="missing-authority-evidence",
                    trust_level="extracted",
                    verification_status="unverified",
                    reason_codes=["repository_revision_mismatch"],
                )
            ],
        ),
        ("no-evidence", {"target_kind": "node", "entity_id": "second"}, [], None, []),
    ],
)
def test_prompt_snapshots_cover_all_semantic_context_classes_without_persisting_content(
    name: str,
    location: dict,
    validation_issues: list[dict],
    runtime_overlay: dict | None,
    evidence_refs: list[EvidenceRef],
) -> None:
    graph = VisualProcessGraph(
        id=f"graph-{name}",
        name=f"Prompt context {name}",
        definition_revision=1,
        steps=[
            VisualProcessStep(id="first", label="First", kind="analysis"),
            VisualProcessStep(id="second", label="Second", kind="review"),
        ],
        edges=[VisualProcessEdge(id="edge-1", source="first", target="second")],
    )
    service = VisualProcessContextService()
    context = service.build_context(
        graph=graph,
        location={"graph_id": graph.id, **location},
        editor_mode="editor",
        repository_revision="repository-revision",
        codecompass_manifest_hash="manifest-hash",
        source_allowlist_version="allowlist-version",
        runtime_overlay=runtime_overlay,
        validation_issues=validation_issues,
        evidence_refs=evidence_refs,
    )

    first = service.assemble_prompt(context, question_text="Was ist der aktuelle Kontext?")
    second = service.assemble_prompt(context, question_text="Was ist der aktuelle Kontext?")

    assert first.context_id == second.context_id
    assert first.prompt_hash == second.prompt_hash
    assert first.prompt_text == second.prompt_text
    assert first.prompt_snapshot["raw_prompt_stored"] is False
    assert "final_prompt_text" not in first.prompt_snapshot
    if name == "stale-evidence":
        assert first.approved_evidence_refs == ()
        assert "repository_revision_mismatch" in first.rejection_reasons
