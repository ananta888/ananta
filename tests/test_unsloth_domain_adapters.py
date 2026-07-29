from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pytest

from agent.services.unsloth_data_recipe_adapter import (
    DataRecipeRequest,
    DatasetSnapshot,
    UnslothDataRecipeAdapter,
)
from agent.services.unsloth_evaluation_promotion_service import (
    EvaluationSnapshot,
    PromotionRequest,
    UnslothEvaluationPromotionService,
)
from agent.services.unsloth_evidence import (
    EvidenceVerificationError,
    ProvidedEvidenceRegistry,
)
from agent.services.unsloth_model_source_adapter import (
    ModelSourceRequest,
    UnslothModelSourceAdapter,
)
from agent.services.unsloth_runtime_handoff_service import (
    RuntimeArtifact,
    RuntimeHandoffRequest,
    UnslothRuntimeHandoffService,
)


SOURCE_ID = "SRC_supplied-model"
RUN_ID = "RUN_supplied-evaluation"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


@dataclass
class FakeTasks:
    calls: list[dict[str, object]] = field(default_factory=list)

    def submit(self, **kwargs):
        self.calls.append(kwargs)
        return "task-1"


@dataclass
class FakeAudit:
    calls: list[dict[str, object]] = field(default_factory=list)

    def record(self, **kwargs):
        self.calls.append(kwargs)


class FakeDatasets:
    def get_snapshot(self, *, tenant_id: str, dataset_id: str):
        return DatasetSnapshot(
            dataset_id=dataset_id,
            tenant_id=tenant_id,
            dataset_hash=HASH_A,
            dataset_ref="tenants/tenant-a/train.jsonl",
            dataset_partition_sha256=HASH_B,
            state="approved",
            secret_scan_state="passed",
            pii_state="clear",
            license_state="approved",
            row_count=10,
        )


def evidence() -> ProvidedEvidenceRegistry:
    return ProvidedEvidenceRegistry(
        source_ids=[SOURCE_ID],
        run_ids=[RUN_ID],
    )


def test_unknown_evidence_is_never_promoted_to_verified() -> None:
    with pytest.raises(EvidenceVerificationError) as error:
        evidence().require_source("SRC_invented")

    assert error.value.code == "source_id_unknown"


def test_data_recipe_is_deterministic_and_hash_bound() -> None:
    adapter = UnslothDataRecipeAdapter(
        datasets=FakeDatasets(),
        evidence=evidence(),
    )
    request = DataRecipeRequest(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        source_id=SOURCE_ID,
        run_id=RUN_ID,
        objective="causal_lm",
        prompt_field="prompt",
        response_field="answer",
    )

    first = adapter.build(request)
    second = adapter.build(request)

    assert first == second
    assert first.dataset_hash == HASH_A


def test_model_download_requires_confirmation_and_immutable_revision() -> None:
    tasks = FakeTasks()
    audit = FakeAudit()
    adapter = UnslothModelSourceAdapter(
        tasks=tasks,
        audit=audit,
        evidence=evidence(),
    )
    plan = adapter.plan(
        ModelSourceRequest(
            tenant_id="tenant-a",
            project_id="project-a",
            source_id=SOURCE_ID,
            kind="huggingface_snapshot",
            expected_sha256=HASH_A,
            model_id="org/model",
            revision="c" * 40,
            network_authorized=True,
            license_status="approved",
        )
    )

    task_id = adapter.submit(
        plan,
        confirmation_digest=plan.confirmation_digest,
    )

    assert task_id == "task-1"
    assert tasks.calls[0]["task_type"] == "ml.model.import"
    assert audit.calls[0]["event_type"] == "unsloth.model_import_submitted"


def test_runtime_handoff_has_no_implicit_fallback() -> None:
    tasks = FakeTasks()
    service = UnslothRuntimeHandoffService(
        tasks=tasks,
        audit=FakeAudit(),
        evidence=evidence(),
    )
    plan = service.plan(
        RuntimeHandoffRequest(
            tenant_id="tenant-a",
            endpoint_id="endpoint-a",
            provider="local-provider",
            artifact=RuntimeArtifact(
                artifact_id="artifact-a",
                tenant_id="tenant-a",
                artifact_sha256=HASH_A,
                registry_state="promoted",
                verification_state="verified",
                format="adapter",
            ),
            source_ids=(SOURCE_ID,),
            run_ids=(RUN_ID,),
            job_id="evaluation-a",
            attempt_id="attempt-a",
            fencing_token_digest=HASH_C,
            base_model_id="model-a",
            base_model_sha256=HASH_D,
            adapter_id="artifact-a",
            adapter_sha256=HASH_E,
            promotion_id="promotion-a",
            provider_descriptor={
                "provider_id": "local-provider",
                "provider_type": "local-openai-compatible",
                "model_id": "model-a",
                "provider_revision": "revision-a",
            },
            endpoint_descriptor={
                "endpoint_id": "endpoint-a",
                "display_name": "Endpoint A",
                "routing_key": "endpoint-a",
            },
            api_capabilities={
                "openai_chat": True,
                "openai_responses": False,
                "anthropic_messages": False,
                "streaming": False,
                "tools": False,
                "structured_output": False,
            },
            limits={
                "timeout_seconds": 60,
                "context_tokens": 4096,
                "max_output_tokens": 1024,
                "stream_idle_timeout_seconds": 30,
            },
            reason_sha256=HASH_F,
            expected_endpoint_revision=2,
        )
    )

    service.submit(plan, confirmation_digest=plan.confirmation_digest)

    assert tasks.calls[0]["payload"]["fallback"] is None


class FakeEvaluations:
    def get(self, *, tenant_id: str, evaluation_id: str):
        return EvaluationSnapshot(
            evaluation_id=evaluation_id,
            tenant_id=tenant_id,
            artifact_id="artifact-a",
            artifact_sha256=HASH_A,
            dataset_hash=HASH_B,
            state="passed",
            metrics={"accuracy": 0.9},
            source_ids=(SOURCE_ID,),
            run_ids=(RUN_ID,),
            job_id="evaluation-a",
            attempt_id="attempt-a",
            fencing_token_digest=HASH_C,
            base_model_id="model-a",
            base_model_sha256=HASH_D,
            adapter_id="artifact-a",
            adapter_sha256=HASH_E,
            export_sha256=HASH_F,
        )


@dataclass
class FakePromotions:
    calls: list[dict[str, object]] = field(default_factory=list)

    def promote(self, **kwargs) -> int:
        self.calls.append(kwargs)
        return 3


def test_promotion_is_evaluation_and_revision_fenced() -> None:
    promotions = FakePromotions()
    service = UnslothEvaluationPromotionService(
        evaluations=FakeEvaluations(),
        promotions=promotions,
        evidence=evidence(),
        audit=FakeAudit(),
    )
    plan = service.plan(
        PromotionRequest(
            tenant_id="tenant-a",
            artifact_id="artifact-a",
            artifact_sha256=HASH_A,
            dataset_hash=HASH_B,
            evaluation_id="evaluation-a",
            minimum_metrics={"accuracy": 0.8},
            expected_registry_revision=2,
            job_id="evaluation-a",
            attempt_id="attempt-a",
            fencing_token_digest=HASH_C,
            base_model_id="model-a",
            base_model_sha256=HASH_D,
            adapter_id="artifact-a",
            adapter_sha256=HASH_E,
            export_sha256=HASH_F,
        )
    )

    revision = service.promote(
        plan,
        confirmation_digest=plan.confirmation_digest,
    )

    assert revision == 3
    assert promotions.calls[0]["expected_revision"] == 2
