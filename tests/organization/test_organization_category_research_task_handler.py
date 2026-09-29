from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from agent.services.organization_research_delegation_policy_service import (
    context_bundle_integrity_digest,
)
from worker.planning.organization_category_research_task_handler import (
    PLANNING_RESEARCH_EXECUTE_COMMAND,
    OrganizationCategoryResearchTaskHandler,
)


def _fixture(*, runner):
    task_id = "sub-category-1"
    parent_task_id = "category-parent-1"
    worker_job_id = "worker-job-1"
    origin_bundle_id = "catalog-context-1"
    clone_bundle_id = "delegated-context-1"
    source_catalog_id = "catalog-abc123"
    source_catalog_hash = "a" * 64
    source_refs = ["SRC_0001", "SRC_0002"]
    run_refs = ["RUN_0001"]
    destination_digest = "b" * 64
    payload_digest = "c" * 64
    original_metadata = {
        "schema": "organization_research_source_catalog_context.v1",
        "catalog_id": source_catalog_id,
        "catalog_hash": source_catalog_hash,
        "repository_revision": "d" * 64,
    }
    chunks = [
        {
            "source": "agent/example.py",
            "content": "The evaluator compares deterministic reasoning results.",
            "metadata": {"source_id": "SRC_0001"},
        },
        {
            "source": "tests/test_example.py",
            "content": "The acceptance test verifies the evaluation report.",
            "metadata": {"source_id": "SRC_0002"},
        },
    ]
    context_text = "\n\n".join(
        f"[{row['metadata']['source_id']}] {row['source']}\n{row['content']}"
        for row in chunks
    )
    manifest = {
        "schema": "organization_research_context_manifest.v1",
        "id": origin_bundle_id,
        "retrieval_run_id": "catalog-retrieval-1",
        "task_id": parent_task_id,
        "bundle_type": "worker_execution_context",
    }
    origin_digest = context_bundle_integrity_digest(
        SimpleNamespace(
            id=origin_bundle_id,
            retrieval_run_id=manifest["retrieval_run_id"],
            task_id=parent_task_id,
            bundle_type=manifest["bundle_type"],
            context_text=context_text,
            chunks=chunks,
            token_estimate=100,
            bundle_metadata=original_metadata,
        )
    )
    dispatch = {
        "schema": "organization_research_delegated_context.v1",
        "parent_task_id": parent_task_id,
        "worker_job_id": worker_job_id,
        "origin_context_bundle_id": origin_bundle_id,
        "origin_context_bundle_digest": origin_digest,
        "destination_binding_digest": destination_digest,
        "payload_digest": payload_digest,
    }
    bundle = SimpleNamespace(
        id=clone_bundle_id,
        task_id=task_id,
        context_text=context_text,
        chunks=chunks,
        token_estimate=100,
        bundle_metadata={**original_metadata, "hub_research_dispatch": dispatch},
    )
    source_catalog = {
        "schema": "source_catalog.v2",
        "source_catalog_id": source_catalog_id,
        "source_catalog_hash": source_catalog_hash,
        "sources": [{"source_id": value} for value in source_refs],
    }
    task = {
        "id": task_id,
        "title": "Research the reasoning workbench",
        "description": "Produce the source-grounded Category todo.",
        "task_kind": "planning_research",
        "parent_task_id": parent_task_id,
        "current_worker_job_id": worker_job_id,
        "context_bundle_id": clone_bundle_id,
        "callback_url": "http://hub/tasks/category-parent-1/subtask-callback",
        "callback_token": "callback-token",
        "worker_execution_context": {
            "context_bundle_id": clone_bundle_id,
            "origin_context_bundle_id": origin_bundle_id,
            "allowed_source_refs": source_refs,
            "allowed_run_refs": run_refs,
            "source_context_bundle_manifest": manifest,
            "source_context_policy": {
                "schema": "organization_research_source_context_policy.v1",
                "context_bundle_id": origin_bundle_id,
                "context_bundle_digest": origin_digest,
                "source_catalog_id": source_catalog_id,
                "source_catalog_hash": source_catalog_hash,
                "llm_scope": "local_only",
            },
            "research_destination_binding": {
                "schema": "organization_research_destination_binding.v1",
                "provider_id": "codex",
                "model_id": "gpt-5-codex",
                "provider_location": "local_container",
                "llm_scope": "local_only",
                "binding_digest": destination_digest,
            },
            "planning_research_binding": {
                "source_catalog_id": source_catalog_id,
                "source_catalog_hash": source_catalog_hash,
                "allowed_source_refs": source_refs,
                "allowed_run_refs": run_refs,
                "source_catalog": source_catalog,
            },
            "hub_research_dispatch_admission": {
                "schema": "organization_research_worker_admission.v1",
                "parent_task_id": parent_task_id,
                "worker_job_id": worker_job_id,
                "origin_context_bundle_id": origin_bundle_id,
                "destination_binding_digest": destination_digest,
                "payload_digest": payload_digest,
            },
        },
    }
    task["last_proposal"] = {"command": PLANNING_RESEARCH_EXECUTE_COMMAND}

    class BundleAccess:
        def resolve_task_reference(self, *, task, task_id):
            assert task["id"] == task_id
            return bundle

        def resolve_task_reference_or_none(self, *, task, task_id):
            return self.resolve_task_reference(task=task, task_id=task_id)

    finalized = []

    def finalizer(**kwargs):
        finalized.append(kwargs)
        return {
            "task_id": kwargs["tid"],
            "status": kwargs["status"],
            "output": kwargs["output"],
            "exit_code": kwargs["exit_code"],
        }

    handler = OrganizationCategoryResearchTaskHandler(
        cli_runner=runner,
        bundle_access=BundleAccess(),
        cli_invoker=lambda callable_runner, **kwargs: callable_runner(**kwargs),
        finalizer=finalizer,
        clock=lambda: 10.0,
    )
    return handler, task, finalized


def _valid_output() -> str:
    payload = {
        "version": "1.0",
        "created": "2026-08-21",
        "updated": "2026-08-21",
        "project": "HRM Reasoning Workbench",
        "review_basis": {
            "reviewed_commit_range": "d" * 64,
            "review_goal": "Create an evidence-grounded implementation order.",
        },
        "categories": [
            {
                "name": "reasoning-workbench",
                "label": "Reasoning Workbench",
                "items": [
                    {
                        "id": "HRM-001",
                        "title": "Verify deterministic evaluation reports",
                        "status": "open",
                        "priority": "high",
                        "risk": "medium",
                        "type": "implementation",
                        "depends_on": [],
                        "acceptance_criteria": [
                            "The evaluator emits a deterministic report for the bound inputs."
                        ],
                        "evidence_claim_refs": ["CLM_0001", "CLM_0002"],
                    }
                ],
            }
        ],
        "meta": {
            "total_items": 1,
            "by_status": {"completed": 0, "partial": 0, "open": 1},
            "notes": [],
            "recommended_order": ["HRM-001"],
        },
        "planning_quality_profile": {
            "schema": "category_todo_quality_profile.v1",
            "source_catalog_id": "catalog-abc123",
            "source_catalog_hash": "a" * 64,
            "allowed_source_refs": ["SRC_0001", "SRC_0002"],
            "allowed_run_refs": ["RUN_0001"],
            "research_summary": "The workbench has an evaluator and acceptance coverage.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "The evaluator compares deterministic reasoning results.",
                    "claim_type": "source_fact",
                    "citation_refs": ["SRC_0001", "SRC_0002"],
                    "confidence": "verified",
                },
                {
                    "claim_id": "CLM_0002",
                    "text": "The delegated research execution completed.",
                    "claim_type": "tool_result",
                    "citation_refs": ["RUN_0001"],
                    "confidence": "verified",
                },
            ],
            "unsupported_notes": [],
            "grounding_status": "verified",
            "grounding_reason": "All claims cite assignment-bound evidence.",
        },
    }
    return json.dumps(payload)


def _request():
    return SimpleNamespace(
        command=PLANNING_RESEARCH_EXECUTE_COMMAND,
        timeout=120,
        retries=0,
        retry_delay=1,
        retry_policy_override=None,
        task_kind="planning_research",
        model_fields_set={"timeout", "task_kind"},
    )


def test_handler_proposes_only_the_bound_executor_command():
    calls = []
    handler, task, _finalized = _fixture(
        runner=lambda **kwargs: calls.append(kwargs)
    )

    result = handler.propose(tid=task["id"], task=task)

    assert result["status"] == "executable"
    assert result["command"] == PLANNING_RESEARCH_EXECUTE_COMMAND
    assert result["backend"] == "codex"
    assert result["model"] == "gpt-5-codex"
    assert calls == []


def test_handler_executes_bound_cli_and_finalizes_valid_category_result():
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return 0, _valid_output(), "", "codex"

    handler, task, finalized = _fixture(runner=runner)

    result = handler.execute(
        tid=task["id"],
        task=task,
        request_data=_request(),
    )

    assert result["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["backend"] == "codex"
    assert calls[0]["model"] == "gpt-5-codex"
    assert "SRC_0001" in calls[0]["prompt"]
    assert "RUN_0001" in calls[0]["prompt"]
    assert len(finalized) == 1
    assert finalized[0]["status"] == "completed"
    assert json.loads(finalized[0]["output"])["project"] == "HRM Reasoning Workbench"


def test_handler_repairs_an_unknown_source_reference_once():
    calls = []
    invalid = _valid_output().replace("SRC_0002", "SRC_9999")

    def runner(**kwargs):
        calls.append(kwargs)
        output = invalid if len(calls) == 1 else _valid_output()
        return 0, output, "", "codex"

    handler, task, finalized = _fixture(runner=runner)

    result = handler.execute(
        tid=task["id"],
        task=task,
        request_data=_request(),
    )

    assert result["status"] == "completed"
    assert len(calls) == 2
    assert "category_research" in calls[1]["prompt"]
    assert finalized[0]["retries_used"] == 1


def test_handler_promotes_nested_quality_and_binds_authoritative_metadata():
    calls = []
    payload = json.loads(_valid_output())
    quality = payload.pop("planning_quality_profile")
    quality.pop("schema")
    quality.pop("source_catalog_id")
    quality.pop("source_catalog_hash")
    quality.pop("allowed_source_refs")
    quality.pop("allowed_run_refs")
    payload["meta"]["planning_quality_profile"] = quality

    def runner(**kwargs):
        calls.append(kwargs)
        return 0, json.dumps(payload), "", "codex"

    handler, task, finalized = _fixture(runner=runner)

    result = handler.execute(
        tid=task["id"],
        task=task,
        request_data=_request(),
    )

    assert result["status"] == "completed"
    assert len(calls) == 1
    normalized = json.loads(finalized[0]["output"])
    bound = normalized["planning_quality_profile"]
    assert bound["schema"] == "category_todo_quality_profile.v1"
    assert bound["source_catalog_id"] == "catalog-abc123"
    assert bound["source_catalog_hash"] == "a" * 64
    assert bound["allowed_source_refs"] == ["SRC_0001", "SRC_0002"]
    assert bound["allowed_run_refs"] == ["RUN_0001"]
    assert bound["claims"] == quality["claims"]


def test_handler_adapts_model_cited_items_when_quality_profile_is_missing():
    calls = []
    payload = json.loads(_valid_output())
    payload.pop("planning_quality_profile")
    item = payload["categories"][0]["items"][0]
    item["evidence_claim_refs"] = ["CLM_0042"]
    item["source_citation_refs"] = ["SRC_0001"]
    item["evidence_summary"] = (
        "The evaluator compares deterministic reasoning results."
    )

    def runner(**kwargs):
        calls.append(kwargs)
        return 0, json.dumps(payload), "", "codex"

    handler, task, finalized = _fixture(runner=runner)

    result = handler.execute(
        tid=task["id"],
        task=task,
        request_data=_request(),
    )

    assert result["status"] == "completed"
    assert len(calls) == 1
    normalized = json.loads(finalized[0]["output"])
    bound = normalized["planning_quality_profile"]
    assert bound["claims"][0] == {
        "claim_id": "CLM_0001",
        "text": "The evaluator compares deterministic reasoning results.",
        "claim_type": "inference",
        "citation_refs": ["SRC_0001"],
        "confidence": "partially_verified",
    }
    assert bound["claims"][1]["citation_refs"] == ["RUN_0001"]
    assert normalized["categories"][0]["items"][0][
        "evidence_claim_refs"
    ] == ["CLM_0001", "CLM_0002"]


def test_handler_projects_root_level_claims_into_closed_quality_schema():
    calls = []
    payload = json.loads(_valid_output())
    quality = payload.pop("planning_quality_profile")
    payload.update(quality)

    def runner(**kwargs):
        calls.append(kwargs)
        return 0, json.dumps(payload), "", "codex"

    handler, task, finalized = _fixture(runner=runner)

    result = handler.execute(
        tid=task["id"],
        task=task,
        request_data=_request(),
    )

    assert result["status"] == "completed"
    assert len(calls) == 1
    normalized = json.loads(finalized[0]["output"])
    bound = normalized["planning_quality_profile"]
    assert set(bound) == {
        "schema",
        "source_catalog_id",
        "source_catalog_hash",
        "allowed_source_refs",
        "allowed_run_refs",
        "research_summary",
        "claims",
        "unsupported_notes",
        "grounding_status",
        "grounding_reason",
    }
    assert bound["claims"] == quality["claims"]


def test_handler_repairs_scalar_item_contract_fields_once():
    calls = []
    invalid = json.loads(_valid_output())
    invalid_item = invalid["categories"][0]["items"][0]
    invalid_item["acceptance_criteria"] = "not-an-array"
    invalid_item["evidence_claim_refs"] = "CLM_0001"

    def runner(**kwargs):
        calls.append(kwargs)
        output = json.dumps(invalid) if len(calls) == 1 else _valid_output()
        return 0, output, "", "codex"

    handler, task, finalized = _fixture(runner=runner)

    result = handler.execute(
        tid=task["id"],
        task=task,
        request_data=_request(),
    )

    assert result["status"] == "completed"
    assert len(calls) == 2
    assert "category_research_item_evidence_invalid" in calls[1]["prompt"]
    assert "category_research_item_acceptance_missing" in calls[1]["prompt"]
    assert finalized[0]["retries_used"] == 1


def test_handler_denies_tampered_context_before_cli_execution():
    calls = []
    handler, task, finalized = _fixture(
        runner=lambda **kwargs: calls.append(kwargs)
    )
    task["worker_execution_context"]["source_context_policy"][
        "context_bundle_digest"
    ] = hashlib.sha256(b"tampered").hexdigest()

    result = handler.propose(tid=task["id"], task=task)

    assert result["status"] == "denied"
    assert result["reason_code"] == "category_research_context_lineage_invalid"
    assert calls == []
    assert finalized == []


def test_prompt_context_projection_is_bounded_and_keeps_all_source_refs():
    chunks = [
        {
            "source_id": f"SRC_{index:04d}",
            "source": f"agent/example_{index}.py",
            "content": "source-grounded content " * 2000,
        }
        for index in range(1, 48)
    ]

    projected = OrganizationCategoryResearchTaskHandler._prompt_context(chunks)

    assert len(projected) <= 20_000
    for index in range(1, 48):
        assert projected.count(f"[SRC_{index:04d}]") == 1
