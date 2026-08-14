from __future__ import annotations

import math
from typing import Any

import pytest

from agent.services.workflow_backend import (
    WORKFLOW_EVENT_SCHEMA,
    WORKFLOW_STATUS_SCHEMA,
    WORKFLOW_TERMINAL_STATUSES,
    WorkflowRequest,
    WorkflowStepRequest,
)
from agent.services.workflow_configured_bridge_reconciler import (
    ConfiguredBridgeReconciler,
    authoritative_runtime_status,
)
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.visual_process.definition_snapshot_contract import (
    VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY,
)

TEMPORAL_STATUS_SCHEMA = "ananta.temporal-workflow-status.v1"


def test_temporal_status_is_normalized_to_stable_public_projection() -> None:
    binding = _binding()
    projected = authoritative_runtime_status(
        _temporal_observation(
            binding,
            status="waiting_approval",
            revision=7,
            active_step_ids=[],
            current_step_id="builder",
            open_gates=["builder"],
            checkpoint_ref="temporal:workflow-public:7",
        ),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=123.5,
    )

    assert projected["schema"] == WORKFLOW_STATUS_SCHEMA
    assert projected["source_observation"] == {
        "schema": TEMPORAL_STATUS_SCHEMA,
        "status": "waiting_approval",
        "revision": 7,
    }
    assert projected["status"] == "waiting_for_approval"
    assert projected["steps"] == [{"step_id": "builder", "status": "waiting_for_approval"}]
    assert projected["updated_at"] == 123.5
    assert projected["workflow_id"] == "workflow-public"
    assert projected["run_id"] == "run-public"
    assert projected["revision"] == 7
    assert projected["backend"] == "temporal"
    assert projected["runtime_id"] == "temporal"


def test_public_start_ack_can_omit_binding_ids_revision_and_steps() -> None:
    projected = authoritative_runtime_status(
        {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "temporal",
            "status": "running",
        },
        binding=_binding(),
        previous=None,
        runtime_id="temporal",
        observed_at=100.0,
        allow_initial_ack=True,
    )

    assert projected["workflow_id"] == "workflow-public"
    assert projected["run_id"] == "run-public"
    assert projected["revision"] == 0
    assert projected["steps"] == [{"step_id": "builder", "status": "pending"}]
    assert projected["source_observation"] == {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "status": "running",
        "backend": "temporal",
    }


def test_temporal_step_sets_project_complete_binding_order() -> None:
    binding = _multi_step_binding()
    projected = authoritative_runtime_status(
        _temporal_observation(
            binding,
            status="waiting_approval",
            revision=7,
            completed_step_ids=["lead", "builder"],
            current_step_id="critic",
            active_step_ids=[],
            open_gates=["critic"],
        ),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=110.0,
    )

    assert projected["steps"] == [
        {"step_id": "lead", "status": "completed"},
        {"step_id": "builder", "status": "completed"},
        {"step_id": "critic", "status": "waiting_for_approval"},
    ]


def test_cancel_request_stays_nonterminal_and_observation_time_is_monotonic() -> None:
    binding = _binding()
    previous = authoritative_runtime_status(
        _temporal_observation(binding, status="running", revision=4),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=200.0,
    )

    projected = authoritative_runtime_status(
        _temporal_observation(binding, status="running", revision=5),
        binding=binding,
        previous=previous,
        runtime_id="temporal",
        observed_at=150.0,
    )

    assert projected["status"] == "running"
    assert projected["status"] not in WORKFLOW_TERMINAL_STATUSES
    assert projected["source_observation"]["status"] == "running"
    assert projected["updated_at"] == 200.0
    assert projected["revision"] == 5


def test_compatible_public_shape_remains_compatible_and_records_provenance() -> None:
    projected = authoritative_runtime_status(
        {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "local",
            "workflow_id": "workflow-public",
            "run_id": "run-public",
            "plan_hash": "plan-hash-a",
            "status": "running",
            "revision": 0,
            "checkpoint_ref": "checkpoint-initial",
            "updated_at": 1.0,
            "steps": [{"step_id": "builder", "status": "running"}],
        },
        binding=_binding(runtime_id="ananta-native"),
        previous=None,
        runtime_id="ananta-native",
        observed_at=321.0,
    )

    assert projected["schema"] == WORKFLOW_STATUS_SCHEMA
    assert projected["source_observation"] == {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "status": "running",
        "backend": "local",
        "revision": 0,
    }
    assert projected["updated_at"] == 321.0
    assert projected["workflow_id"] == "workflow-public"
    assert projected["run_id"] == "run-public"
    assert projected["revision"] == 0
    assert projected["backend"] == "ananta-native"
    assert projected["steps"] == [{"step_id": "builder", "status": "running"}]


@pytest.mark.parametrize("field_name", ["workflow_id", "run_id", "plan_hash"])
def test_present_foreign_source_identity_fails_closed(field_name: str) -> None:
    raw = {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "backend": "temporal",
        "workflow_id": "workflow-public",
        "run_id": "run-public",
        "plan_hash": "plan-hash-a",
        "status": "running",
        "revision": 1,
        "checkpoint_ref": "checkpoint-1",
        "steps": [{"step_id": "builder", "status": "running"}],
        field_name: "foreign-id",
    }

    with pytest.raises(
        ValueError,
        match=rf"workflow_runtime_source_{field_name}_mismatch",
    ):
        authoritative_runtime_status(
            raw,
            binding=_binding(),
            previous=None,
            runtime_id="temporal",
            observed_at=1.0,
        )


@pytest.mark.parametrize("field_name", ["workflow_id", "run_id", "plan_hash"])
def test_temporal_query_requires_all_bound_identity_fields(field_name: str) -> None:
    binding = _binding()
    raw = _temporal_observation(binding, status="running", revision=1)
    raw.pop(field_name)

    with pytest.raises(
        ValueError,
        match=rf"workflow_runtime_source_{field_name}_required",
    ):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=1.0,
        )


def test_present_foreign_source_backend_fails_closed() -> None:
    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_backend_mismatch",
    ):
        authoritative_runtime_status(
            {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": "foreign-runtime",
                "workflow_id": "workflow-public",
                "run_id": "run-public",
                "plan_hash": "plan-hash-a",
                "status": "running",
                "revision": 1,
                "checkpoint_ref": "checkpoint-1",
                "steps": [{"step_id": "builder", "status": "running"}],
            },
            binding=_binding(),
            previous=None,
            runtime_id="temporal",
            observed_at=1.0,
        )


@pytest.mark.parametrize("schema", [None, "", "ananta.unknown-status.v1"])
def test_missing_or_unknown_source_schema_fails_closed(schema: str | None) -> None:
    raw = {"status": "running"}
    if schema is not None:
        raw["schema"] = schema

    with pytest.raises(ValueError, match="workflow_runtime_source_schema"):
        authoritative_runtime_status(
            raw,
            binding=_binding(),
            previous=None,
            runtime_id="temporal",
            observed_at=1.0,
        )


def test_temporal_schema_is_not_relabelled_for_another_configured_runtime() -> None:
    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_schema_unsupported",
    ):
        authoritative_runtime_status(
            _temporal_observation(_binding(), status="running", revision=1),
            binding=_binding(runtime_id="configured-worker"),
            previous=None,
            runtime_id="configured-worker",
            observed_at=1.0,
        )


@pytest.mark.parametrize(
    "changes,reason_code",
    [
        ({"active_step_ids": ["unknown-step"]}, "step_unknown"),
        (
            {
                "active_step_ids": ["builder"],
                "completed_step_ids": ["builder"],
            },
            "step_state_overlap",
        ),
        (
            {
                "active_step_ids": ["builder"],
                "current_step_id": "builder",
                "open_gates": ["lead"],
            },
            "open_gate_mismatch",
        ),
    ],
)
def test_invalid_temporal_step_sets_fail_closed(
    changes: dict[str, Any],
    reason_code: str,
) -> None:
    binding = _multi_step_binding()
    raw = _temporal_observation(binding, status="running", revision=2)
    raw.update(changes)

    with pytest.raises(ValueError, match=reason_code):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=2.0,
        )


def test_source_revision_regression_fails_instead_of_relabelling_stale_state() -> None:
    binding = _binding()
    previous = authoritative_runtime_status(
        _temporal_observation(binding, status="running", revision=5),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=5.0,
    )

    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_revision_regressed",
    ):
        authoritative_runtime_status(
            _temporal_observation(binding, status="running", revision=2),
            binding=binding,
            previous=previous,
            runtime_id="temporal",
            observed_at=6.0,
        )


def test_same_source_revision_with_contradictory_state_fails_closed() -> None:
    binding = _binding()
    previous = authoritative_runtime_status(
        _temporal_observation(binding, status="running", revision=5),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=5.0,
    )
    contradiction = _temporal_observation(
        binding,
        status="failed",
        revision=5,
        active_step_ids=[],
        current_step_id="",
        failed_step_ids=["builder"],
    )

    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_revision_conflict",
    ):
        authoritative_runtime_status(
            contradiction,
            binding=binding,
            previous=previous,
            runtime_id="temporal",
            observed_at=6.0,
        )


def test_same_source_revision_allows_additive_hub_events_without_revision_bump() -> None:
    binding = _binding()
    raw = _temporal_observation(binding, status="running", revision=5)
    previous = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=5.0,
    )

    projected = authoritative_runtime_status(
        {**raw, "telemetry": {"poll": 2}},
        binding=binding,
        previous=previous,
        runtime_id="temporal",
        events=(
            {
                "schema": WORKFLOW_EVENT_SCHEMA,
                "event_id": "hub-event-2",
                "event_type": "workflow_observed",
            },
        ),
        observed_at=6.0,
    )

    assert projected["revision"] == 5
    assert projected["source_observation"]["revision"] == 5
    assert len(projected["events"]) == 1
    event = projected["events"][0]
    assert event["schema"] == WORKFLOW_EVENT_SCHEMA
    assert event["event_type"] == "workflow_observed"
    assert event["tenant_id"] == binding.tenant_id
    assert event["workflow_id"] == binding.workflow_id
    assert event["run_id"] == binding.run_id
    assert event["correlation_id"] == binding.request.correlation_id
    assert event["actor"] == "runtime-source"
    assert event["event_id"].startswith("wfe-runtime-")
    assert event["causation_id"] == f"runtime-source:{binding.run_id}"
    assert event["dedupe_key"].startswith("runtime-event:")
    assert "telemetry" not in projected


def test_event_projection_is_idempotent_across_a_same_revision_poll() -> None:
    binding = _binding()
    raw = _temporal_observation(binding, status="running", revision=5)
    first = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="temporal",
        events=(
            {
                "schema": WORKFLOW_EVENT_SCHEMA,
                "event_id": "source-event-1",
                "event_type": "workflow_observed",
                "actor": "untrusted-worker-actor",
            },
        ),
        observed_at=5.0,
    )

    second = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=first,
        runtime_id="temporal",
        observed_at=5.0,
    )

    assert second["events"] == first["events"]
    assert second["events"][0]["actor"] == "runtime-source"


@pytest.mark.parametrize(
    "current_step_id,open_gates",
    [
        ("builder", ["critic"]),
    ],
)
def test_waiting_approval_requires_exact_current_gate(
    current_step_id: str,
    open_gates: list[str],
) -> None:
    binding = _multi_step_binding()
    raw = _temporal_observation(
        binding,
        status="waiting_approval",
        revision=2,
        active_step_ids=[],
        current_step_id=current_step_id,
        open_gates=open_gates,
    )

    with pytest.raises(ValueError, match="workflow_runtime_source_open_gate_required"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=2.0,
        )


def test_waiting_approval_rejects_multiple_valid_open_gates() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-public",
        steps=(
            WorkflowStepRequest(
                step_id="builder",
                gate=True,
                policy_scope={"source": "reconciler-test"},
            ),
            WorkflowStepRequest(
                step_id="critic",
                gate=True,
                depends_on=("builder",),
                policy_scope={"source": "reconciler-test"},
            ),
        ),
        policy_scope={"source": "reconciler-test"},
    )
    binding = _run_binding(request, runtime_id="temporal")

    with pytest.raises(ValueError, match="workflow_runtime_source_open_gate_required"):
        authoritative_runtime_status(
            _temporal_observation(
                binding,
                status="waiting_approval",
                revision=2,
                active_step_ids=[],
                current_step_id="critic",
                open_gates=["builder", "critic"],
            ),
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=2.0,
        )


def test_temporal_terminal_status_never_publishes_live_step_state() -> None:
    binding = _binding()
    raw = _temporal_observation(
        binding,
        status="cancelled",
        revision=2,
        active_step_ids=["builder"],
        current_step_id="builder",
    )

    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_terminal_step_state_conflict",
    ):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=2.0,
        )


def test_completed_temporal_partial_failure_preserves_failed_step_evidence() -> None:
    binding = _multi_step_binding()
    projected = authoritative_runtime_status(
        _temporal_observation(
            binding,
            status="completed",
            revision=7,
            active_step_ids=[],
            current_step_id="",
            completed_step_ids=["lead", "critic"],
            failed_step_ids=["builder"],
        ),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=7.0,
    )

    assert projected["status"] == "completed"
    assert projected["steps"] == [
        {"step_id": "lead", "status": "completed"},
        {"step_id": "builder", "status": "failed"},
        {"step_id": "critic", "status": "completed"},
    ]


def test_completed_public_status_requires_every_requested_step_to_be_terminal() -> None:
    binding = _multi_step_binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="completed",
        revision=3,
        steps=[
            {"step_id": "lead", "status": "completed"},
            {"step_id": "builder", "status": "completed"},
        ],
    )

    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_completed_step_state_conflict",
    ):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=3.0,
        )


@pytest.mark.parametrize(
    ("overall_status", "step_status"),
    [
        ("failed", "running"),
        ("cancelled", "waiting_for_approval"),
        ("skipped", "in_progress"),
    ],
)
def test_terminal_public_status_rejects_live_step_evidence(
    overall_status: str,
    step_status: str,
) -> None:
    binding = _binding(runtime_id="ananta-native")

    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_terminal_step_state_conflict",
    ):
        authoritative_runtime_status(
            _public_observation(
                binding,
                status=overall_status,
                revision=2,
                steps=[{"step_id": "builder", "status": step_status}],
            ),
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=2.0,
        )


def test_completed_public_partial_failure_preserves_failed_step_evidence() -> None:
    binding = _multi_step_binding(runtime_id="ananta-native")
    projected = authoritative_runtime_status(
        _public_observation(
            binding,
            status="completed",
            revision=7,
            steps=[
                {"step_id": "lead", "status": "completed"},
                {"step_id": "builder", "status": "failed"},
                {"step_id": "critic", "status": "completed"},
            ],
        ),
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        observed_at=7.0,
    )

    assert projected["steps"] == [
        {"step_id": "lead", "status": "completed"},
        {"step_id": "builder", "status": "failed"},
        {"step_id": "critic", "status": "completed"},
    ]


def test_failed_temporal_status_does_not_invent_skipped_steps() -> None:
    binding = _multi_step_binding()
    projected = authoritative_runtime_status(
        _temporal_observation(
            binding,
            status="failed",
            revision=4,
            active_step_ids=[],
            current_step_id="",
        ),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        observed_at=4.0,
    )

    assert projected["status"] == "failed"
    assert {step["status"] for step in projected["steps"]} == {"unknown"}


def test_public_projection_discards_unknown_fields_and_redacts_nested_secrets() -> None:
    binding = _binding(runtime_id="ananta-native")
    projected = authoritative_runtime_status(
        {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "local",
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "tenant_id": binding.tenant_id,
            "plan_hash": binding.plan_hash,
            "status": "running",
            "revision": 1,
            "checkpoint_ref": "local:workflow-public:1",
            "steps": [
                {
                    "step_id": "builder",
                    "status": "running",
                    "error": "raw prompt with customer SSN 123-45-6789",
                    "training": {
                        "job_id": "job-public",
                        "status": "running",
                        "message": "unmarked private training prompt",
                        "value": "ghp_training-secret",
                    },
                    "diagnostics": {
                        "client_secret_value": "clear-client-secret",
                        "api_key_sk-live-secret": "value-hidden-in-key",
                        "job_id": "ghp_super_secret",
                        "trace_id": "123-45-6789",
                        "status": "worker_secret_payload",
                        "job_url": "/model-training?job_id=ghp_super_secret",
                        "artifact_id": "sk_live_51ABCDEF123456789",
                        "causation_id": "AKIAIOSFODNN7EXAMPLE",
                        "provider": "super_secret",
                        "message": "unmarked diagnostic prompt",
                        "value": "customer SSN 123-45-6789",
                        "input_tokens": 12,
                        "nested": {
                            "user_prompt_text": "clear-prompt",
                            "nested_api_key_value": "clear-api-key",
                            "apiKeyValue": "clear-camel-api-key",
                            "privateKeyValue": "clear-camel-private-key",
                            "input_tokens": 12,
                        },
                    },
                    "arbitrary_step_field": "must-not-leak",
                }
            ],
            "parameters": {"password": "clear-password"},
            "telemetry": {"raw": "must-not-leak"},
            "arbitrary_top_level": "must-not-leak",
            "reason": "Bearer clear-reason-token",
            "reason_code": "ghp_worker-secret",
            "error": "unmarked raw system prompt",
            "gate": {
                "gate_id": "approval-1",
                "status": "open",
                "message": "private approval prompt",
                "value": "123-45-6789",
            },
        },
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        events=(
            {
                "schema": WORKFLOW_EVENT_SCHEMA,
                "event_id": "event-secret",
                "event_type": "workflow_observed",
                "payload": {
                    "safe": "visible",
                    "message": "unmarked event prompt",
                    "value": "ghp_event-secret",
                    "nested_api_key_value": "clear-event-api-key",
                },
                "unknown_event_field": "must-not-leak",
            },
        ),
        observed_at=1.0,
    )

    serialized = repr(projected)
    assert "clear-" not in serialized
    assert "sk-live-secret" not in serialized
    assert "value-hidden-in-key" not in serialized
    assert "must-not-leak" not in serialized
    assert "123-45-6789" not in serialized
    assert "ghp_" not in serialized
    assert "unmarked" not in serialized
    assert "parameters" not in projected
    assert "telemetry" not in projected
    assert projected["reason"] == "[REDACTED]"
    assert projected["reason_code"] == "runtime_reason_redacted"
    assert projected["error"] == "[REDACTED]"
    assert projected["gate"] == {
        "gate_id": "[REDACTED]",
        "status": "open",
        "message": "[REDACTED]",
        "value": "[REDACTED]",
    }
    assert projected["steps"][0]["error"] == "[REDACTED]"
    assert projected["steps"][0]["training"] == {
        "job_id": "[REDACTED]",
        "status": "running",
        "message": "[REDACTED]",
        "value": "[REDACTED]",
    }
    assert projected["steps"][0]["diagnostics"]["redacted_secret"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["redacted_api_key"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["job_id"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["trace_id"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["status"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["job_url"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["artifact_id"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["causation_id"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["provider"] == "[REDACTED]"
    assert projected["steps"][0]["diagnostics"]["input_tokens"] == 12
    assert "nested" not in projected["steps"][0]["diagnostics"]
    assert projected["events"][0]["payload"] == {
        "message": "[REDACTED]",
        "value": "[REDACTED]",
        "redacted_api_key": "[REDACTED]",
    }


def test_public_projection_preserves_only_canonical_runtime_reason_codes() -> None:
    binding = _binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="failed",
        revision=1,
        steps=[{"step_id": "builder", "status": "failed"}],
    )
    raw["reason_code"] = "native_route_not_selected"

    projected = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        observed_at=1.0,
    )

    assert projected["reason_code"] == "native_route_not_selected"


@pytest.mark.parametrize(
    "reason_code",
    [
        "worker_error:sk_live_51abcdef123456789",
        "worker_secret",
        "runtime_failure:akiaiosfodnn7example",
        "workflow_error:super_secret",
    ],
)
def test_public_projection_redacts_reason_codes_outside_the_finite_registry(
    reason_code: str,
) -> None:
    binding = _binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="failed",
        revision=1,
        steps=[{"step_id": "builder", "status": "failed"}],
    )
    raw["reason_code"] = reason_code

    projected = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        observed_at=1.0,
    )

    assert projected["reason_code"] == "runtime_reason_redacted"
    assert reason_code not in repr(projected)


def test_public_projection_replaces_opaque_event_and_optional_source_identity() -> None:
    binding = _binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "running"}],
    )
    raw["process_version"] = "AKIAIOSFODNN7EXAMPLE"
    raw["temporal"] = {"run_id": "sk_live_51ABCDEF123456789"}

    projected = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        events=(
            {
                "schema": WORKFLOW_EVENT_SCHEMA,
                "event_id": "sk_live_51ABCDEF123456789",
                "event_type": "super_secret",
                "actor": "AKIAIOSFODNN7EXAMPLE",
                "causation_id": "super_secret",
                "dedupe_key": "123-45-6789",
            },
        ),
        observed_at=1.0,
    )

    event = projected["events"][0]
    assert projected["process_version"] == "[REDACTED]"
    assert projected["temporal"] == {"run_id": "[REDACTED]"}
    assert event["event_type"] == "workflow.runtime.observed"
    assert event["actor"] == "runtime-source"
    assert event["event_id"].startswith("wfe-runtime-")
    assert event["causation_id"] == f"runtime-source:{binding.run_id}"
    assert event["dedupe_key"].startswith("runtime-event:")
    serialized = repr(projected)
    for forbidden in (
        "sk_live_51ABCDEF123456789",
        "AKIAIOSFODNN7EXAMPLE",
        "super_secret",
        "123-45-6789",
    ):
        assert forbidden not in serialized


def test_public_projection_rejects_a_source_owned_opaque_checkpoint_reference() -> None:
    binding = _binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "running"}],
    )
    raw["checkpoint_ref"] = "sk_live_51ABCDEF123456789"

    with pytest.raises(
        ValueError,
        match="workflow_runtime_source_checkpoint_ref_unproven",
    ):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )


def test_public_projection_rejects_not_found_as_a_step_status() -> None:
    binding = _binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "not_found"}],
    )

    with pytest.raises(ValueError, match="workflow_runtime_source_step_status_unsupported"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )


def test_public_projection_uses_only_the_binding_owned_correlation_id() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-public",
        correlation_id="correlation-bound",
        steps=(WorkflowStepRequest(step_id="builder"),),
    )
    binding = _run_binding(request, runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "running"}],
    )

    projected = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        observed_at=1.0,
    )
    assert projected["correlation_id"] == "correlation-bound"

    raw["correlation_id"] = "correlation-foreign"
    with pytest.raises(ValueError, match="workflow_runtime_source_correlation_id_mismatch"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )

    unbound_binding = _binding(runtime_id="ananta-native")
    unbound = _public_observation(
        unbound_binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "running"}],
    )
    unbound_binding = _run_binding(
        WorkflowRequest(
            workflow_id="workflow-public",
            correlation_id="",
            steps=(WorkflowStepRequest(step_id="builder"),),
        ),
        runtime_id="ananta-native",
    )
    unbound["correlation_id"] = "runtime-selected"
    with pytest.raises(ValueError, match="workflow_runtime_source_correlation_id_mismatch"):
        authoritative_runtime_status(
            unbound,
            binding=unbound_binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )


def test_projection_binds_the_visual_process_definition_snapshot() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-public",
        steps=(WorkflowStepRequest(step_id="builder"),),
        metadata={VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY: "a" * 64},
    )
    binding = _run_binding(request, runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "running"}],
    )

    projected = authoritative_runtime_status(
        raw,
        binding=binding,
        previous=None,
        runtime_id="ananta-native",
        observed_at=1.0,
    )
    assert projected["snapshot_hash"] == "a" * 64

    raw["snapshot_hash"] = "b" * 64
    with pytest.raises(ValueError, match="workflow_runtime_source_snapshot_hash_mismatch"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )


@pytest.mark.parametrize(
    "event",
    [
        {
            "schema": WORKFLOW_EVENT_SCHEMA,
            "event_id": "event-unknown-step",
            "event_type": "workflow_observed",
            "step_id": "foreign-step",
        },
        {
            "schema": WORKFLOW_EVENT_SCHEMA,
            "event_id": "event-legacy-unknown-step",
            "event_type": "workflow_observed",
            "details": {"step_id": "foreign-step"},
        },
    ],
)
def test_event_projection_rejects_steps_outside_the_bound_plan(event: dict[str, Any]) -> None:
    binding = _binding()
    with pytest.raises(ValueError, match="workflow_runtime_event_step_id_unknown"):
        authoritative_runtime_status(
            _temporal_observation(binding, status="running", revision=1),
            binding=binding,
            previous=None,
            runtime_id="temporal",
            events=(event,),
            observed_at=1.0,
        )


def test_event_projection_fills_binding_identity_and_rejects_foreign_correlation() -> None:
    binding = _binding()
    event = {
        "schema": WORKFLOW_EVENT_SCHEMA,
        "event_id": "event-bound",
        "event_type": "workflow_observed",
        "step_id": "builder",
    }
    projected = authoritative_runtime_status(
        _temporal_observation(binding, status="running", revision=1),
        binding=binding,
        previous=None,
        runtime_id="temporal",
        events=(event,),
        observed_at=1.0,
    )
    projected_event = projected["events"][0]
    assert projected_event["schema"] == event["schema"]
    assert projected_event["event_type"] == event["event_type"]
    assert projected_event["step_id"] == event["step_id"]
    assert projected_event["tenant_id"] == binding.tenant_id
    assert projected_event["workflow_id"] == binding.workflow_id
    assert projected_event["run_id"] == binding.run_id
    assert projected_event["correlation_id"] == binding.request.correlation_id
    assert projected_event["event_id"].startswith("wfe-runtime-")
    assert projected_event["actor"] == "runtime-source"
    assert projected_event["causation_id"] == f"runtime-source:{binding.run_id}"
    assert projected_event["dedupe_key"].startswith("runtime-event:")

    with pytest.raises(ValueError, match="workflow_runtime_event_correlation_id_mismatch"):
        authoritative_runtime_status(
            _temporal_observation(binding, status="running", revision=1),
            binding=binding,
            previous=None,
            runtime_id="temporal",
            events=({**event, "correlation_id": "foreign-correlation"},),
            observed_at=1.0,
        )


def test_public_projection_rejects_boolean_step_gate_outside_the_typed_contract() -> None:
    binding = _binding(runtime_id="ananta-native")
    raw = _public_observation(
        binding,
        status="running",
        revision=1,
        steps=[{"step_id": "builder", "status": "running", "gate": False}],
    )

    with pytest.raises(ValueError, match="workflow_runtime_source_step_gate_invalid"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )


def test_projection_cardinality_fences_fail_before_unbounded_iteration() -> None:
    binding = _binding()
    temporal = _temporal_observation(binding, status="running", revision=1)
    temporal["active_step_ids"] = ["builder"] * 10_000
    with pytest.raises(ValueError, match="active_step_ids_too_many"):
        authoritative_runtime_status(
            temporal,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=1.0,
        )

    public_binding = _binding(runtime_id="ananta-native")
    public = {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "backend": "local",
        "workflow_id": public_binding.workflow_id,
        "run_id": public_binding.run_id,
        "plan_hash": public_binding.plan_hash,
        "status": "running",
        "revision": 1,
        "checkpoint_ref": "checkpoint-1",
        "steps": [{"step_id": "builder", "status": "running"}] * 10_000,
    }
    with pytest.raises(ValueError, match="workflow_runtime_source_steps_too_many"):
        authoritative_runtime_status(
            public,
            binding=public_binding,
            previous=None,
            runtime_id="ananta-native",
            observed_at=1.0,
        )


def test_event_count_depth_and_key_type_are_bounded_before_copying() -> None:
    binding = _binding()
    raw = _temporal_observation(binding, status="running", revision=1)
    event = {
        "schema": WORKFLOW_EVENT_SCHEMA,
        "event_id": "event-bounded",
        "event_type": "workflow_observed",
    }
    with pytest.raises(ValueError, match="workflow_runtime_events_too_many"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            events=tuple(event for _ in range(257)),
            observed_at=1.0,
        )

    too_deep: dict[str, Any] = {"value": "safe"}
    for _ in range(8):
        too_deep = {"metrics": too_deep}
    deep_event = {**event, "payload": too_deep}
    with pytest.raises(ValueError, match="event_payload_too_deep"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            events=(deep_event,),
            observed_at=1.0,
        )

    invalid_key_event = {**event, "payload": {1: "invalid"}}
    with pytest.raises(ValueError, match="event_payload_invalid"):
        authoritative_runtime_status(
            raw,
            binding=binding,
            previous=None,
            runtime_id="temporal",
            events=(invalid_key_event,),
            observed_at=1.0,
        )


def test_reconciler_uses_injected_hub_clock_for_raw_temporal_status() -> None:
    binding = _binding()
    bindings = InMemoryWorkflowControlBindingStore(clock=lambda: 10.0)
    bindings.put(binding)
    bindings.record_status(
        binding.workflow_id,
        authoritative_runtime_status(
            {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": "temporal",
                "status": "running",
            },
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=10.0,
            allow_initial_ack=True,
        ),
    )
    durable = _RecordingDurableRun(binding)
    projected: list[dict[str, Any]] = []
    reconciler = ConfiguredBridgeReconciler(
        runtime_id="temporal",
        bindings=bindings,
        durable_runs=durable,
        project=lambda _binding, status, **_kwargs: projected.append(dict(status)),
        owner_id="test-reconciler",
        clock=lambda: 456.0,
    )

    result = reconciler.reconcile_active()

    assert result == {"runtime_id": "temporal", "processed": 1, "failed": []}
    stored = bindings.last_status(binding.workflow_id)
    assert stored is not None
    assert stored["schema"] == WORKFLOW_STATUS_SCHEMA
    assert stored["status"] == "waiting_for_approval"
    assert stored["updated_at"] == 456.0
    assert stored["workflow_id"] == binding.workflow_id
    assert stored["run_id"] == binding.run_id
    assert stored["revision"] == 1
    assert projected == [stored]


def test_reconciler_commits_runtime_status_when_history_enrichment_fails() -> None:
    binding = _binding()
    bindings = InMemoryWorkflowControlBindingStore(clock=lambda: 10.0)
    bindings.put(binding)
    bindings.record_status(
        binding.workflow_id,
        authoritative_runtime_status(
            {
                "schema": WORKFLOW_STATUS_SCHEMA,
                "backend": "temporal",
                "status": "running",
            },
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=10.0,
            allow_initial_ack=True,
        ),
    )
    durable = _HistoryFailureDurableRun(binding)
    projected: list[tuple[dict[str, Any], tuple[dict[str, Any], ...]]] = []
    reconciler = ConfiguredBridgeReconciler(
        runtime_id="temporal",
        bindings=bindings,
        durable_runs=durable,
        project=lambda _binding, status, *, events: projected.append((dict(status), events)),
        owner_id="test-reconciler",
        clock=lambda: 456.0,
    )

    result = reconciler.reconcile_active()

    assert result == {
        "runtime_id": "temporal",
        "processed": 1,
        "failed": [],
        "trace_failed": [
            {
                "workflow_id": binding.workflow_id,
                "error_type": "RuntimeError",
            }
        ],
    }
    stored = bindings.last_status(binding.workflow_id)
    assert stored is not None
    assert stored["revision"] == 1
    assert stored["status"] == "waiting_for_approval"
    assert stored["event_cursor"] == "0"
    assert projected == [(stored, ())]


@pytest.mark.parametrize("observed_at", [float("nan"), float("inf"), -1.0])
def test_invalid_hub_observation_time_fails_closed(observed_at: float) -> None:
    assert not math.isfinite(observed_at) or observed_at < 0
    binding = _binding()
    with pytest.raises(ValueError, match="workflow_runtime_source_observed_at_invalid"):
        authoritative_runtime_status(
            _temporal_observation(binding, status="running", revision=1),
            binding=binding,
            previous=None,
            runtime_id="temporal",
            observed_at=observed_at,
        )


class _RecordingDurableRun:
    def __init__(self, binding: WorkflowControlRunBinding) -> None:
        self._binding = binding

    def describe(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        assert tenant_id == "tenant-a"
        assert run_id == "workflow-public"
        return _temporal_observation(
            self._binding,
            status="waiting_approval",
            revision=1,
            active_step_ids=[],
            current_step_id="builder",
            open_gates=["builder"],
        )

    def history(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_cursor: str = "",
    ) -> dict[str, Any]:
        assert tenant_id == "tenant-a"
        assert run_id == "workflow-public"
        assert after_cursor == "0"
        return {"events": [], "next_cursor": "0"}


class _HistoryFailureDurableRun(_RecordingDurableRun):
    def history(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_cursor: str = "",
    ) -> dict[str, Any]:
        del tenant_id, run_id, after_cursor
        raise RuntimeError("history_temporarily_unavailable")


def _public_observation(
    binding: WorkflowControlRunBinding,
    *,
    status: str,
    revision: int,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "backend": "local",
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "tenant_id": binding.tenant_id,
        "plan_hash": binding.plan_hash,
        "status": status,
        "revision": revision,
        "checkpoint_ref": f"local:{binding.workflow_id}:{revision}",
        "steps": steps,
    }


def _temporal_observation(
    binding: WorkflowControlRunBinding,
    *,
    status: str,
    revision: int,
    active_step_ids: list[str] | None = None,
    completed_step_ids: list[str] | None = None,
    failed_step_ids: list[str] | None = None,
    current_step_id: str | None = None,
    open_gates: list[str] | None = None,
    checkpoint_ref: str = "",
) -> dict[str, Any]:
    first_step_id = binding.request.steps[0].step_id
    active = [first_step_id] if active_step_ids is None else active_step_ids
    return {
        "schema": TEMPORAL_STATUS_SCHEMA,
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "status": status,
        "revision": revision,
        "current_step_id": first_step_id if current_step_id is None else current_step_id,
        "completed_step_ids": completed_step_ids or [],
        "retry_budget_remaining": 2,
        "checkpoint_ref": checkpoint_ref or f"temporal:{binding.workflow_id}:{revision}",
        "open_gates": open_gates or [],
        "reason_code": "",
        "parameters": {},
        "plan_hash": binding.plan_hash,
        "plan_revision": 1,
        "plan_ref": "",
        "active_step_ids": active,
        "failed_step_ids": failed_step_ids or [],
    }


def _binding(*, runtime_id: str = "temporal") -> WorkflowControlRunBinding:
    request = WorkflowRequest(
        workflow_id="workflow-public",
        steps=(
            WorkflowStepRequest(
                step_id="builder",
                gate=True,
                policy_scope={"source": "reconciler-test"},
            ),
        ),
        policy_scope={"source": "reconciler-test"},
    )
    return _run_binding(request, runtime_id=runtime_id)


def _multi_step_binding(*, runtime_id: str = "temporal") -> WorkflowControlRunBinding:
    request = WorkflowRequest(
        workflow_id="workflow-public",
        steps=(
            WorkflowStepRequest(
                step_id="lead",
                policy_scope={"source": "reconciler-test"},
            ),
            WorkflowStepRequest(
                step_id="builder",
                depends_on=("lead",),
                policy_scope={"source": "reconciler-test"},
            ),
            WorkflowStepRequest(
                step_id="critic",
                gate=True,
                depends_on=("builder",),
                policy_scope={"source": "reconciler-test"},
            ),
        ),
        policy_scope={"source": "reconciler-test"},
    )
    return _run_binding(request, runtime_id=runtime_id)


def _run_binding(
    request: WorkflowRequest,
    *,
    runtime_id: str,
) -> WorkflowControlRunBinding:
    return WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id="workflow-public",
        run_id="run-public",
        runtime_id=runtime_id,
        plan_hash="plan-hash-a",
        policy_version="policy-v1",
        checkpoint_id="checkpoint-initial",
        request=request,
    )
