from agent.services.worker_runtime_selection_service import (
    WorkerRuntimeSelectionRequest,
    WorkerRuntimeSelectionService,
)
from worker.core.runtime_target import (
    RuntimeDataBoundary,
    RuntimeHealthState,
    SelectionDecisionStatus,
    WorkerCandidate,
    WorkerKind,
    WorkerRuntimeKind,
    WorkerRuntimeTarget,
    WorkerSelectionMode,
    WorkerSelectionPolicy,
)


def _native_worker():
    return WorkerCandidate(
        worker_id="native-01",
        worker_kind=WorkerKind.native_ananta_worker,
        capabilities=["repair.execute.inspect", "repair.execute.low_risk", "repair.verify", "planning"],
        supported_execution_modes=["deterministic_repair_plan", "deterministic_repair_step", "repair_preview"],
        runtime_target_ids=["docker-local"],
        health_state=RuntimeHealthState.ready,
        priority=10,
    )


def _opencode_worker():
    return WorkerCandidate(
        worker_id="opencode-local-01",
        worker_kind=WorkerKind.opencode,
        capabilities=["patch_propose", "code_read", "planning"],
        supported_execution_modes=["patch_propose", "repair_preview"],
        runtime_target_ids=["docker-local"],
        health_state=RuntimeHealthState.ready,
        priority=20,
    )


def _hermes_worker():
    return WorkerCandidate(
        worker_id="hermes-cloud-01",
        worker_kind=WorkerKind.hermes,
        capabilities=["planning", "review", "patch_propose"],
        supported_execution_modes=["repair_preview", "patch_propose"],
        runtime_target_ids=["cloud-runtime"],
        health_state=RuntimeHealthState.ready,
        priority=30,
    )


def _docker_runtime():
    return WorkerRuntimeTarget(
        runtime_target_id="docker-local",
        runtime_kind=WorkerRuntimeKind.docker_container,
        workspace_scope="/workspace",
        allowed_capabilities=["repair.execute.inspect", "repair.execute.low_risk", "repair.verify", "planning", "patch_propose", "code_read"],
        data_boundary=RuntimeDataBoundary.project_private,
        health_state=RuntimeHealthState.ready,
    )


def _cloud_runtime():
    return WorkerRuntimeTarget(
        runtime_target_id="cloud-runtime",
        runtime_kind=WorkerRuntimeKind.cloud_worker,
        allowed_capabilities=["planning", "review", "patch_propose"],
        data_boundary=RuntimeDataBoundary.cloud,
        health_state=RuntimeHealthState.ready,
    )


def test_fixed_native_worker_selection_positive():
    decision = WorkerRuntimeSelectionService().select(WorkerRuntimeSelectionRequest(
        policy=WorkerSelectionPolicy(
            mode=WorkerSelectionMode.fixed,
            fixed_worker_id="native-01",
            allowed_worker_kinds=[WorkerKind.native_ananta_worker],
            allow_cloud=False,
            allow_external_workers=False,
        ),
        workers=[_native_worker(), _opencode_worker()],
        runtime_targets=[_docker_runtime()],
        required_capabilities=["repair.execute.low_risk", "repair.verify"],
        execution_mode="deterministic_repair_plan",
        policy_decision_ref="policy-1",
    ))
    assert decision.decision_status == SelectionDecisionStatus.selected
    assert decision.selected_worker_id == "native-01"
    assert decision.selected_runtime_target_id == "docker-local"
    assert "fixed" in decision.selected_reason


def test_fixed_unavailable_worker_denies_without_fallback():
    unavailable = _native_worker().model_copy(update={"health_state": RuntimeHealthState.unavailable})
    decision = WorkerRuntimeSelectionService().select(WorkerRuntimeSelectionRequest(
        policy=WorkerSelectionPolicy(
            mode=WorkerSelectionMode.fixed,
            fixed_worker_id="native-01",
            allow_cloud=False,
            allow_external_workers=False,
        ),
        workers=[unavailable, _opencode_worker()],
        runtime_targets=[_docker_runtime()],
        required_capabilities=["repair.execute.low_risk"],
    ))
    assert decision.decision_status == SelectionDecisionStatus.no_eligible_worker
    assert decision.selected_worker_id is None
    assert any(r.reason_code == "worker_unavailable" for r in decision.rejected_candidates)


def test_automatic_selects_native_for_repair_mutation_and_rejects_hermes():
    decision = WorkerRuntimeSelectionService().select(WorkerRuntimeSelectionRequest(
        policy=WorkerSelectionPolicy(
            mode=WorkerSelectionMode.automatic,
            allowed_worker_kinds=[WorkerKind.native_ananta_worker, WorkerKind.opencode, WorkerKind.hermes],
            allow_cloud=True,
            allow_external_workers=True,
            prefer_local=True,
        ),
        workers=[_hermes_worker(), _opencode_worker(), _native_worker()],
        runtime_targets=[_docker_runtime(), _cloud_runtime()],
        required_capabilities=["repair.execute.low_risk", "repair.verify"],
        execution_mode="deterministic_repair_plan",
    ))
    assert decision.decision_status == SelectionDecisionStatus.selected
    assert decision.selected_worker_kind == WorkerKind.native_ananta_worker
    assert any(r.worker_kind == WorkerKind.hermes for r in decision.rejected_candidates)


def test_cloud_external_worker_rejected_when_cloud_and_external_false():
    decision = WorkerRuntimeSelectionService().select(WorkerRuntimeSelectionRequest(
        policy=WorkerSelectionPolicy(
            mode=WorkerSelectionMode.automatic,
            allowed_worker_kinds=[WorkerKind.hermes],
            allow_cloud=False,
            allow_external_workers=False,
        ),
        workers=[_hermes_worker()],
        runtime_targets=[_cloud_runtime()],
        required_capabilities=["planning"],
        execution_mode="repair_preview",
    ))
    assert decision.decision_status == SelectionDecisionStatus.no_eligible_worker
    reason_codes = {r.reason_code for r in decision.rejected_candidates}
    assert "external_worker_denied_allow_external_false" in reason_codes


def test_opencode_selected_for_patch_propose_when_policy_allows():
    decision = WorkerRuntimeSelectionService().select(WorkerRuntimeSelectionRequest(
        policy=WorkerSelectionPolicy(
            mode=WorkerSelectionMode.automatic,
            allowed_worker_kinds=[WorkerKind.native_ananta_worker, WorkerKind.opencode],
            allow_cloud=False,
            allow_external_workers=False,
            prefer_local=True,
        ),
        workers=[_native_worker(), _opencode_worker()],
        runtime_targets=[_docker_runtime()],
        required_capabilities=["patch_propose", "code_read"],
        execution_mode="patch_propose",
    ))
    assert decision.decision_status == SelectionDecisionStatus.selected
    assert decision.selected_worker_kind == WorkerKind.opencode


def test_selection_is_deterministic_for_same_inputs():
    request = WorkerRuntimeSelectionRequest(
        policy=WorkerSelectionPolicy(
            mode=WorkerSelectionMode.policy_ranked,
            allowed_worker_kinds=[WorkerKind.native_ananta_worker, WorkerKind.opencode],
            allow_cloud=False,
            allow_external_workers=False,
        ),
        workers=[_opencode_worker(), _native_worker()],
        runtime_targets=[_docker_runtime()],
        required_capabilities=["planning"],
        execution_mode="repair_preview",
    )
    first = WorkerRuntimeSelectionService().select(request)
    second = WorkerRuntimeSelectionService().select(request)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.decision_hash == second.decision_hash
