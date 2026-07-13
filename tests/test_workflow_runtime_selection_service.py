from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.services.config_profile_service import ConfigProfileService
from agent.services.workflow_control_service import (
    RuntimeSelection,
    WorkflowControlService,
    WorkflowPrincipal,
    WorkflowRunHandle,
)
from agent.services.workflow_runtime import ExecutionPlan
from agent.services.workflow_runtime_capability_service import (
    default_workflow_runtime_capability_service,
)
from agent.services.workflow_runtime_selection_service import (
    ExplicitFallbackPolicy,
    InMemoryRuntimeCatalog,
    InMemoryRuntimeHealthService,
    InMemoryRuntimeSelectionAudit,
    RuntimeCandidate,
    RuntimeHealthSnapshot,
    RuntimeSelectionContext,
    RuntimeSelectionProfile,
    StrictRuntimeBudgetService,
    StrictRuntimeDataLocalityService,
    VersionBoundRuntimePolicy,
    WorkflowRuntimeProfileService,
    WorkflowRuntimeSelectionService,
    default_workflow_runtime_profile_service,
)


def _plan(
    *,
    capabilities: tuple[str, ...] = ("retrieval",),
    policy_version: str = "policy-v1",
    metadata: dict[str, object] | None = None,
    timeout_seconds: float = 30,
    max_tokens: int | None = 100,
) -> ExecutionPlan:
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-1",
            "plan_id": "plan-1",
            "workflow_id": "workflow-1",
            "policy_version": policy_version,
            "capabilities": list(capabilities),
            "nodes": [
                {
                    "id": "step-1",
                    "required_capabilities": list(capabilities),
                }
            ],
            "budget": {
                "timeout_seconds": timeout_seconds,
                "max_tokens": max_tokens,
            },
            "metadata": dict(metadata or {}),
        }
    )


def _candidate(
    runtime_id: str,
    *,
    capabilities: tuple[str, ...] = (
        "audit",
        "authorization",
        "policy",
        "retrieval",
        "side_effect_guard",
    ),
    localities: tuple[str, ...] = ("eu", "local"),
    policy_versions: tuple[str, ...] = ("policy-v1",),
    max_timeout_seconds: float | None = 300,
    max_tokens: int | None = 1000,
    priority: int = 10,
) -> RuntimeCandidate:
    return RuntimeCandidate(
        runtime_id=runtime_id,
        capabilities=frozenset(capabilities),
        mode="live",
        data_localities=frozenset(localities),
        policy_versions=frozenset(policy_versions),
        max_timeout_seconds=max_timeout_seconds,
        max_tokens=max_tokens,
        max_cost_micros=100000,
        priority=priority,
        version="1.0.0",
    )


def _profile(
    *,
    preferred: str = "native",
    allowed: tuple[str, ...] = ("native",),
    required: tuple[str, ...] = ("audit",),
    fallback_enabled: bool = False,
    fallback_targets: tuple[str, ...] = (),
) -> RuntimeSelectionProfile:
    return RuntimeSelectionProfile(
        profile_id="test-profile",
        preferred_runtime=preferred,
        allowed_runtimes=allowed,
        required_capabilities=required,
        explicit_fallback_policy=ExplicitFallbackPolicy(
            enabled=fallback_enabled,
            allowed_runtimes=fallback_targets,
            semantic_class="equivalent",
        ),
    )


class _AllowReleaseEvidence:
    def evaluate(self, **_kwargs) -> tuple[bool, str]:
        return True, "runtime_release_gate_verified"


class _DenyReleaseEvidence:
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code

    def evaluate(self, **_kwargs) -> tuple[bool, str]:
        return False, self.reason_code


def _service(
    candidates: tuple[RuntimeCandidate, ...],
    *,
    health: dict[str, str | RuntimeHealthSnapshot] | None = None,
    audit: InMemoryRuntimeSelectionAudit | None = None,
) -> tuple[WorkflowRuntimeSelectionService, InMemoryRuntimeSelectionAudit]:
    audit_store = audit or InMemoryRuntimeSelectionAudit()
    snapshots = health or {candidate.runtime_id: "ready" for candidate in candidates}
    return (
        WorkflowRuntimeSelectionService(
            catalog=InMemoryRuntimeCatalog(candidates),
            health=InMemoryRuntimeHealthService(snapshots),
            policy=VersionBoundRuntimePolicy(),
            locality=StrictRuntimeDataLocalityService(),
            budget=StrictRuntimeBudgetService(),
            audit=audit_store,
            release_evidence=_AllowReleaseEvidence(),
        ),
        audit_store,
    )


def test_preferred_runtime_is_selected_and_all_alternatives_are_audited() -> None:
    service, audit = _service((_candidate("native"), _candidate("langgraph", priority=20)))

    selection = service.select(
        plan=_plan(metadata={"data_locality": "eu"}),
        profile=_profile(allowed=("native", "langgraph")),
    )

    assert selection.runtime_id == "native"
    assert selection.mode == "live"
    assert selection.reason_code == "runtime_selected_preferred"
    assert selection.profile_id == "test-profile"
    assert selection.audit_ref.startswith("rsa-")
    assert selection.rejected == (
        {
            "runtime_id": "langgraph",
            "reason_code": "runtime_not_selected_lower_rank",
            "detail": "runtime_not_selected_lower_rank",
        },
    )
    assert audit.records[0].evaluations[0].selected is False
    assert audit.records[0].evaluations[1].selected is True


@pytest.mark.parametrize(
    "reason_code",
    (
        "runtime_release_gate_missing",
        "runtime_release_gate_contract_hash_mismatch",
        "runtime_release_gate_artifact_hash_mismatch",
    ),
)
def test_release_evidence_is_mandatory_current_and_hash_consistent(reason_code: str) -> None:
    audit = InMemoryRuntimeSelectionAudit()
    service = WorkflowRuntimeSelectionService(
        catalog=InMemoryRuntimeCatalog((_candidate("native"),)),
        health=InMemoryRuntimeHealthService({"native": "ready"}),
        policy=VersionBoundRuntimePolicy(),
        locality=StrictRuntimeDataLocalityService(),
        budget=StrictRuntimeBudgetService(),
        audit=audit,
        release_evidence=_DenyReleaseEvidence(reason_code),
    )

    selection = service.select(plan=_plan(), profile=_profile())

    assert selection.runtime_id == ""
    assert selection.mode == "blocked"
    assert reason_code in audit.records[0].evaluations[0].reason_codes


def test_release_evidence_adapter_failure_is_caught_and_blocks_selection() -> None:
    class BrokenEvidence:
        def evaluate(self, **_kwargs):
            raise OSError("evidence store unavailable")

    audit = InMemoryRuntimeSelectionAudit()
    service = WorkflowRuntimeSelectionService(
        catalog=InMemoryRuntimeCatalog((_candidate("native"),)),
        health=InMemoryRuntimeHealthService({"native": "ready"}),
        policy=VersionBoundRuntimePolicy(),
        locality=StrictRuntimeDataLocalityService(),
        budget=StrictRuntimeBudgetService(),
        audit=audit,
        release_evidence=BrokenEvidence(),
    )

    selection = service.select(plan=_plan(), profile=_profile())

    assert selection.runtime_id == ""
    assert selection.mode == "blocked"
    assert "runtime_release_evidence_unavailable" in audit.records[0].evaluations[0].reason_codes


@pytest.mark.parametrize(
    ("candidate", "plan", "health", "expected_reason"),
    [
        (
            _candidate("native", capabilities=("retrieval",)),
            _plan(),
            {"native": "ready"},
            "runtime_capabilities_missing:audit",
        ),
        (
            _candidate("native"),
            _plan(),
            {"native": "unavailable"},
            "runtime_health_unavailable",
        ),
        (
            _candidate("native", localities=("local",)),
            _plan(metadata={"data_locality": "eu"}),
            {"native": "ready"},
            "runtime_data_locality_not_satisfied",
        ),
        (
            _candidate("native", max_timeout_seconds=10),
            _plan(timeout_seconds=30),
            {"native": "ready"},
            "runtime_budget_timeout_exceeded",
        ),
        (
            _candidate("native", policy_versions=("policy-v2",)),
            _plan(policy_version="policy-v1"),
            {"native": "ready"},
            "runtime_policy_version_not_supported",
        ),
    ],
)
def test_eligibility_dimensions_fail_closed_with_stable_reasons(
    candidate: RuntimeCandidate,
    plan: ExecutionPlan,
    health: dict[str, str],
    expected_reason: str,
) -> None:
    service, audit = _service((candidate,), health=health)

    selection = service.select(plan=plan, profile=_profile())

    assert selection.runtime_id == ""
    assert selection.mode in {"blocked", "incompatible"}
    reasons = audit.records[0].evaluations[0].reason_codes
    assert expected_reason in reasons


def test_degraded_health_requires_explicit_context_permission() -> None:
    service, _ = _service((_candidate("native"),), health={"native": "degraded"})

    denied = service.select(plan=_plan(), profile=_profile())
    allowed = service.select(
        plan=_plan(),
        profile=_profile(),
        context=RuntimeSelectionContext(
            timeout_seconds=30,
            max_tokens=100,
            allow_degraded_health=True,
        ),
    )

    assert denied.mode == "blocked"
    assert allowed.runtime_id == "native"


def test_fallback_is_blocked_unless_explicit_and_semantically_equivalent() -> None:
    candidates = (_candidate("native"), _candidate("langgraph", priority=20))
    service, audit = _service(
        candidates,
        health={"native": "unavailable", "langgraph": "ready"},
    )

    blocked = service.select(
        plan=_plan(),
        profile=_profile(allowed=("native", "langgraph")),
    )
    selected = service.select(
        plan=_plan(),
        profile=_profile(
            allowed=("native", "langgraph"),
            fallback_enabled=True,
            fallback_targets=("langgraph",),
        ),
    )

    assert blocked.mode == "blocked"
    assert "runtime_fallback_not_explicitly_enabled" in {
        reason for evaluation in audit.records[0].evaluations for reason in evaluation.reason_codes
    }
    assert selected.runtime_id == "langgraph"
    assert selected.reason_code == "runtime_selected_explicit_fallback"


def test_fallback_capability_loss_is_blocked_even_when_profile_enables_it() -> None:
    source_caps = (
        "audit",
        "authorization",
        "policy",
        "retrieval",
        "side_effect_guard",
        "structured_output",
    )
    target_caps = tuple(value for value in source_caps if value != "structured_output")
    service, audit = _service(
        (
            _candidate("native", capabilities=source_caps),
            _candidate("langgraph", capabilities=target_caps, priority=20),
        ),
        health={"native": "unavailable", "langgraph": "ready"},
    )

    selection = service.select(
        plan=_plan(),
        profile=_profile(
            allowed=("native", "langgraph"),
            fallback_enabled=True,
            fallback_targets=("langgraph",),
        ),
    )

    assert selection.mode == "blocked"
    assert "runtime_fallback_capability_loss" in {
        reason for evaluation in audit.records[0].evaluations for reason in evaluation.reason_codes
    }


def test_selection_and_audit_reference_are_deterministic() -> None:
    service, audit = _service((_candidate("langgraph", priority=20), _candidate("native", priority=10)))
    profile = _profile(allowed=("langgraph", "native"))

    first = service.select(plan=_plan(), profile=profile)
    second = service.select(plan=_plan(), profile=profile)

    assert first == second
    assert audit.records[0].to_dict() == audit.records[1].to_dict()


def test_audit_failure_prevents_runtime_selection() -> None:
    class FailingAudit:
        def record(self, record) -> None:
            raise OSError("audit unavailable")

    service = WorkflowRuntimeSelectionService(
        catalog=InMemoryRuntimeCatalog((_candidate("native"),)),
        health=InMemoryRuntimeHealthService({"native": "ready"}),
        policy=VersionBoundRuntimePolicy(),
        locality=StrictRuntimeDataLocalityService(),
        budget=StrictRuntimeBudgetService(),
        audit=FailingAudit(),
        release_evidence=_AllowReleaseEvidence(),
    )

    with pytest.raises(RuntimeError, match="runtime_selection_audit_failed"):
        service.select(plan=_plan(), profile=_profile())


def test_json_profiles_are_visible_through_config_profile_projection() -> None:
    profile_service = default_workflow_runtime_profile_service()
    config_service = ConfigProfileService()

    assert profile_service.resolve("temporal-durable").preferred_runtime == "temporal"
    assert config_service.get_workflow_runtime_profile("temporal-durable") is not None
    assert config_service.get_workflow_runtime_profile("unknown") is None
    assert {item["profile_id"] for item in config_service.list_workflow_runtime_profiles()} == {
        "native-safe-default",
        "temporal-durable",
    }


def test_checked_in_matrix_and_profile_select_native_without_hidden_defaults() -> None:
    matrix = default_workflow_runtime_capability_service()
    observed_health = InMemoryRuntimeHealthService({"ananta-native": "ready"})
    audit = InMemoryRuntimeSelectionAudit()
    service = WorkflowRuntimeSelectionService(
        catalog=matrix,
        health=observed_health,
        policy=VersionBoundRuntimePolicy(),
        locality=StrictRuntimeDataLocalityService(),
        budget=StrictRuntimeBudgetService(),
        audit=audit,
        release_evidence=_AllowReleaseEvidence(),
    )
    profile = default_workflow_runtime_profile_service().resolve("native-safe-default")

    selection = service.select(plan=_plan(), profile=profile)

    assert selection.runtime_id == "ananta-native"
    assert selection.reason_code == "runtime_selected_preferred"
    assert audit.records[0].profile_id == "native-safe-default"


class _Authorization:
    def authorize(self, **kwargs) -> str:
        return "allowed"


class _Selection:
    def __init__(self, selection: RuntimeSelection) -> None:
        self.selection = selection
        self.calls: list[dict[str, object]] = []

    def select(self, **kwargs) -> RuntimeSelection:
        self.calls.append(kwargs)
        return self.selection


@dataclass
class _Bridge:
    starts: int = 0

    def start(self, **kwargs) -> WorkflowRunHandle:
        self.starts += 1
        return WorkflowRunHandle(
            tenant_id=kwargs["principal"].tenant_id,
            workflow_id=kwargs["plan"].workflow_id,
            run_id=kwargs["run_id"],
            runtime_id=kwargs["selection"].runtime_id,
            status="created",
            task_ref="task-1",
        )


def test_control_service_resolves_profile_and_never_delegates_blocked_selection() -> None:
    profile = _profile()
    profiles = WorkflowRuntimeProfileService({profile.profile_id: profile})
    selection = _Selection(
        RuntimeSelection(
            runtime_id="",
            capabilities=frozenset(),
            mode="blocked",
            reason_code="runtime_selection_no_safe_runtime",
            profile_id=profile.profile_id,
            audit_ref="rsa-test",
        )
    )
    bridge = _Bridge()
    control = WorkflowControlService(
        authorization=_Authorization(),
        selection=selection,
        bridge=bridge,
        runtime_profiles=profiles,
    )

    with pytest.raises(RuntimeError, match="workflow_runtime_selection_blocked"):
        control.start(
            principal=WorkflowPrincipal("tenant-1", "user-1"),
            plan=_plan(),
            run_id="run-1",
            authorization_envelope={"signature": "opaque"},
            runtime_profile_id=profile.profile_id,
        )

    assert bridge.starts == 0
    assert selection.calls[0]["profile"] == profile


def test_control_service_denies_profile_widening_overrides() -> None:
    profile = _profile()
    control = WorkflowControlService(
        authorization=_Authorization(),
        selection=_Selection(RuntimeSelection("native", frozenset({"retrieval"}), "live", "selected")),
        bridge=_Bridge(),
        runtime_profiles=WorkflowRuntimeProfileService({profile.profile_id: profile}),
    )

    with pytest.raises(ValueError, match="runtime_profile_override_denied"):
        control.start(
            principal=WorkflowPrincipal("tenant-1", "user-1"),
            plan=_plan(),
            run_id="run-1",
            authorization_envelope={},
            runtime_profile_id=profile.profile_id,
            preferred_runtime="langgraph",
        )
