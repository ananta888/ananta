from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = ROOT / "examples/workflow-runtime"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

from example_hub import ExampleHubError, ExampleHubState  # noqa: E402
from fake_provider import (  # noqa: E402
    DeterministicFakeProvider,
    FakeProviderContractError,
)
from offline_runtime_drills import run_langgraph_drill, run_native_drill  # noqa: E402
from runtime_example_support import (  # noqa: E402
    EXAMPLE_SCHEMA,
    PLAN_REF,
    SCENARIOS,
    assert_evidence_contract,
    build_temporal_input,
    evidence_boundary,
    load_plan,
)


def test_same_plan_compiles_to_every_temporal_example_scenario() -> None:
    plan = load_plan()
    raw = json.loads((ROOT / PLAN_REF).read_text(encoding="utf-8"))

    assert plan.to_dict() == raw
    for scenario in ("failure", "approval", "cancel", "crash"):
        workflow_input = build_temporal_input(plan, scenario=scenario, issued_at=100.0)
        assert workflow_input.plan_hash == plan.plan_hash
        assert [step.step_id for step in workflow_input.steps] == [node.node_id for node in plan.nodes]
        assert [step.gate for step in workflow_input.steps] == [False, True, False]
        assert workflow_input.steps[1].depends_on == ("draft",)
        assert workflow_input.steps[2].depends_on == ("publish",)


def test_native_and_pinned_langgraph_drills_use_real_runtime_paths() -> None:
    plan = load_plan()

    native = run_native_drill(plan)
    langgraph = run_langgraph_drill(plan)

    assert native["plan_hash"] == langgraph["plan_hash"] == plan.plan_hash
    assert native["scenarios"]["failure"]["attempts"] == 2
    assert native["scenarios"]["resume"]["terminal_status"] == "completed"
    assert langgraph["framework_path"] == "langgraph.graph.StateGraph"
    assert langgraph["scenarios"]["approval"]["approved_terminal_status"] == "completed"
    assert langgraph["scenarios"]["crash"]["production_equivalent"] is False


def test_example_hub_fails_once_and_revalidates_authorization(tmp_path: Path) -> None:
    plan = load_plan()
    workflow_input = build_temporal_input(plan, scenario="failure")
    step = workflow_input.steps[0]
    payload = {
        "operation_id": step.operation_id,
        "tenant_id": workflow_input.tenant_id,
        "workflow_id": workflow_input.workflow_id,
        "run_id": workflow_input.run_id,
        "correlation_id": workflow_input.correlation_id,
        "step_id": step.step_id,
        "plan_hash": workflow_input.plan_hash,
        "task_kind": step.task_kind,
        "authorization_envelope": step.authorization_envelope.to_dict(),
        "artifact_refs": [],
        "required_capabilities": list(step.required_capabilities),
        "activity_class": step.activity_class.value,
        "retry_budget_remaining": workflow_input.retry_budget_remaining,
        "retry_budget_maximum": workflow_input.retry_budget_maximum,
        "parameters": {},
    }
    state = ExampleHubState(tmp_path)

    with pytest.raises(ExampleHubError, match="example_fake_transient_failure"):
        state.submit(payload)
    receipt = state.submit(payload)

    assert receipt.status == "completed"
    assert receipt.authorization_state == "valid"
    assert receipt.ledger_state == "completed"
    events = (tmp_path / "workflow-runtime-example-hub-events-v1.jsonl").read_text(encoding="utf-8")
    assert "task_submission_failed" in events
    assert "task_submitted" in events


def test_checked_in_fake_provider_is_the_bounded_attempt_source() -> None:
    provider = DeterministicFakeProvider.from_default_fixture()

    failed = provider.next_response("draft", operation_scope="operation-a")
    completed = provider.next_response("draft", operation_scope="operation-a")
    isolated = provider.next_response("draft", operation_scope="operation-b")

    assert failed == isolated
    assert failed["reason_code"] == "example_fake_transient_failure"
    assert completed["status"] == "completed"
    assert completed["artifact_ref"] == "artifact://example/draft"
    with pytest.raises(FakeProviderContractError, match="attempt_exhausted"):
        provider.next_response("draft", operation_scope="operation-a")


def test_final_evidence_is_example_only_and_cannot_be_a_release_gate() -> None:
    plan = load_plan()
    runtime = {
        "plan_hash": plan.plan_hash,
        "scenarios": {name: {"status": "observed"} for name in SCENARIOS},
        "durable_server_path": True,
    }
    evidence = {
        "schema": EXAMPLE_SCHEMA,
        "classification": "example_only",
        "production_release_gate": False,
        "status": "passed_example",
        "plan": {"ref": PLAN_REF, "hash": plan.plan_hash},
        "boundaries": evidence_boundary(),
        "security": {},
        "runtimes": {
            "native": {"plan_hash": plan.plan_hash},
            "langgraph": {"plan_hash": plan.plan_hash},
            "temporal": runtime,
        },
    }

    assert_evidence_contract(evidence, final=True)
    invalid = copy.deepcopy(evidence)
    invalid["production_release_gate"] = True
    with pytest.raises(ValueError, match="must_not_be_release_gate"):
        assert_evidence_contract(invalid, final=True)


def test_runner_has_no_pytest_dependency_and_compose_is_hardened() -> None:
    runner = (EXAMPLE_DIR / "run_example.py").read_text(encoding="utf-8")
    temporal_worker = (EXAMPLE_DIR / "temporal_worker.py").read_text(encoding="utf-8")
    public_material = (EXAMPLE_DIR / "example_public_material.py").read_text(encoding="utf-8")
    example_hub = (EXAMPLE_DIR / "example_hub.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker/compose-next/Dockerfile.workflow-runtime-example").read_text(encoding="utf-8")
    compose = (ROOT / "docker/compose-next/compose.workflow-runtime-example.yml").read_text(encoding="utf-8")

    assert "import pytest" not in runner
    assert "runtime_example_support" not in temporal_worker
    assert "agent." not in temporal_worker
    assert "agent." not in public_material and "worker." not in public_material
    assert "worker." not in example_hub
    assert "requirements-dev" not in dockerfile
    assert "python -m pytest" not in dockerfile
    assert compose.count('cap_drop: ["ALL"]') == 3
    assert compose.count("no-new-privileges:true") == 3
    assert "docker.sock" not in compose
    assert "production_release_gate" not in compose
    assert 'restart: "no"' in compose
    assert "temporal-runtime:" in compose


def _registered_activities(source: str) -> str:
    marker = "activities=["
    start = source.index(marker) + len(marker)
    return source[start : source.index("]", start)].strip()


def test_example_worker_registers_the_same_activities_as_the_production_worker() -> None:
    # The example hand-rolls its own Worker instead of calling build_worker, so
    # an Activity added to production silently stays missing here.  That drift
    # is invisible at startup: the workflow only fails once it calls the
    # unregistered Activity, mid-run, inside an update handler.
    production = (ROOT / "worker/temporal/runtime.py").read_text(encoding="utf-8")
    example = (EXAMPLE_DIR / "temporal_worker.py").read_text(encoding="utf-8")

    assert _registered_activities(example) == _registered_activities(production)


def test_example_commands_are_verifiable_but_not_forgeable_by_the_worker() -> None:
    from example_public_material import (
        EXAMPLE_COMMAND_KEY_ID,
        example_command_public_key,
    )
    from runtime_example_support import example_command_key_ring

    # The worker is given the public half only, so the key it verifies with
    # cannot issue commands.  Pinning the derivation keeps the pair together.
    assert example_command_key_ring().public_keys()[EXAMPLE_COMMAND_KEY_ID] == example_command_public_key()

    worker_source = (EXAMPLE_DIR / "temporal_worker.py").read_text(encoding="utf-8")
    assert "example_command_signing_key" not in worker_source


def test_optional_live_provider_overlay_is_secret_ref_only_and_not_control_plane() -> None:
    profile = json.loads((EXAMPLE_DIR / "live-provider-profile.v1.json").read_text(encoding="utf-8"))
    probe = (EXAMPLE_DIR / "live_provider_probe.py").read_text(encoding="utf-8")
    overlay = (ROOT / "docker/compose-next/compose.workflow-runtime-example.live-provider.yml").read_text(
        encoding="utf-8"
    )

    assert profile["classification"] == "optional_local_live"
    assert profile["langchain"] == {
        "role": "provider_adapter",
        "allowed_roles": ["provider_adapter", "retriever_adapter", "tool_adapter"],
        "control_plane": False,
        "task_queue_owner": False,
        "workflow_scheduler": False,
    }
    assert "RunnableLambda" in probe
    assert "redirect_forbidden" in probe
    assert "credential_file_env" in probe
    assert "workflow-runtime-live-provider-api-key" in overlay
    assert "ANANTA_EXAMPLE_LIVE_PROVIDER_API_KEY_FILE:?" in overlay
    assert 'cap_drop: ["ALL"]' in overlay
    assert "no-new-privileges:true" in overlay
    assert "docker.sock" not in overlay
    assert "sk-" not in overlay.lower()
    assert "task_queue" not in overlay.lower()


def test_ci_executes_full_compose_crash_resume_drill_with_always_teardown() -> None:
    workflow = (ROOT / ".github/workflows/quality-and-docs.yml").read_text(encoding="utf-8")
    job_start = workflow.index("\n  workflow-runtime-example-compose:")
    job_end = workflow.index(
        "\n  workflow-runtime-postgres-contract:",
        job_start,
    )
    example_job = workflow[job_start:job_end]
    ordered = [
        "--mode prepare",
        "kill -s SIGKILL workflow-runtime-example-temporal-worker",
        "--mode resume",
        "--mode validate-evidence",
        "down --volumes --remove-orphans",
    ]

    offsets = [example_job.index(marker) for marker in ordered]
    assert offsets == sorted(offsets)
    assert example_job.count("if: always()") >= 3
    assert "timeout-minutes: 30" in example_job
    assert "timeout 240s" in example_job
    assert "State.ExitCode" in example_job
    assert '"137"' in example_job
