from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import pytest
import yaml

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.conformance import RuntimeObservation
from agent.services.workflow_runtime.reference_workflows import load_reference_workflows
from agent.services.workflow_runtime.release_gate import (
    EVIDENCE_CHAIN_GENESIS,
    PROOF_CATEGORIES,
    RuntimeEvidenceBinding,
    RuntimeEvidenceSink,
    RuntimeRunEvidence,
    VerificationCommandResult,
    WorkflowRuntimeGateEvidenceVerifier,
    WorkflowRuntimeReleaseAdmission,
    WorkflowRuntimeReleaseGate,
    load_runtime_release_evidence,
    load_runtime_release_evidence_jsonl,
    load_workflow_release_gate_config,
    release_verification_command_hash,
)

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_PATH = ROOT / "config" / "workflow_runtime" / "release_evidence.v1.json"


REVISION = "revision-test"
BUILD_ID = "build-test"


def _proofs(*passed: str) -> dict[str, str]:
    proofs = {category: "not_applicable" for category in PROOF_CATEGORIES}
    for category in passed:
        proofs[category] = "passed"
    return proofs


def _hash_record(record: RuntimeRunEvidence) -> RuntimeRunEvidence:
    return replace(record, record_hash=sha256_json(record.to_dict(include_record_hash=False)))


def _rechain(records: Iterable[RuntimeRunEvidence]) -> tuple[RuntimeRunEvidence, ...]:
    grouped: dict[str, list[RuntimeRunEvidence]] = {}
    for record in records:
        grouped.setdefault(record.command_id, []).append(record)
    result: list[RuntimeRunEvidence] = []
    for command_id in sorted(grouped):
        previous_hash = EVIDENCE_CHAIN_GENESIS
        for chain_index, record in enumerate(grouped[command_id], start=1):
            chained = _hash_record(
                replace(
                    record,
                    chain_index=chain_index,
                    previous_record_hash=previous_hash,
                    record_hash="",
                )
            )
            result.append(chained)
            previous_hash = chained.record_hash
    return tuple(result)


def _explicit_test_evidence(gate: WorkflowRuntimeReleaseGate) -> tuple[RuntimeRunEvidence, ...]:
    scenarios = {scenario.scenario_id: scenario for scenario in load_reference_workflows()}
    commands = {
        command.evidence_runtime_ids[0]: command
        for command in gate.config.verification_commands
        if command.evidence_runtime_ids
    }
    records: list[RuntimeRunEvidence] = []
    for requirement in gate.config.runtimes:
        command = commands[requirement.runtime_id]
        for scenario_id in requirement.required_scenarios:
            scenario = scenarios[scenario_id]
            durable = scenario_id in requirement.durable_scenarios
            for iteration in range(1, 11):
                event_types = set(scenario.invariants.required_event_types)
                passed_proofs = {"port", "security", "event", "artifact"}
                if scenario.invariants.required_gates:
                    passed_proofs.add("approval")
                if scenario.invariants.side_effect_operations:
                    passed_proofs.add("ledger")
                if iteration == 1 and (
                    (
                        requirement.runtime_id in {"native", "langgraph"}
                        and scenario_id == "research"
                    )
                    or (
                        requirement.runtime_id == "temporal"
                        and scenario_id == "long-running-resume"
                    )
                ):
                    passed_proofs.update({"checkpoint", "recovery"})
                    if not durable:
                        event_types.update(
                            {
                                "workflow.checkpoint.hub_persisted",
                                "workflow.checkpoint.hub_restored",
                            }
                        )
                records.append(
                    RuntimeRunEvidence(
                        runtime_id=requirement.runtime_id,
                        runtime_version=requirement.runtime_version,
                        scenario_id=scenario_id,
                        iteration=iteration,
                        contract_hash=gate.contract_hash,
                        capabilities=requirement.capabilities,
                        durable=durable,
                        observation=RuntimeObservation(
                            runtime_id=requirement.runtime_id,
                            terminal_status="completed",
                            capabilities=requirement.capabilities,
                            event_types=tuple(sorted(event_types)),
                            artifact_ids=scenario.invariants.required_artifacts,
                            gate_ids=scenario.invariants.required_gates,
                            side_effect_operations=scenario.invariants.side_effect_operations,
                            policy_decisions=scenario.invariants.required_policy_decisions,
                            budget_usage={"attempts": 1, "tokens": 0, "cost_micros": 0},
                        ),
                        proofs=_proofs(*sorted(passed_proofs)),
                        revision=REVISION,
                        build_id=BUILD_ID,
                        command_id=command.command_id,
                        command_hash=release_verification_command_hash(command, gate.contract_hash),
                        run_id=f"{requirement.runtime_id}-{scenario_id}-{iteration}",
                    )
                )
    return _rechain(records)


def _gate_and_evidence():
    gate = WorkflowRuntimeReleaseGate()
    evidence = _explicit_test_evidence(gate)
    return gate, evidence


def _green_commands(
    gate: WorkflowRuntimeReleaseGate,
    evidence: Iterable[RuntimeRunEvidence],
) -> tuple[VerificationCommandResult, ...]:
    records = tuple(evidence)
    results: list[VerificationCommandResult] = []
    for command in gate.config.verification_commands:
        selected = tuple(item for item in records if item.command_id == command.command_id)
        results.append(
            VerificationCommandResult(
                command_id=command.command_id,
                argv=command.argv,
                returncode=0,
                tests=1,
                revision=REVISION,
                build_id=BUILD_ID,
                command_hash=release_verification_command_hash(command, gate.contract_hash),
                evidence_records=len(selected),
                evidence_chain_head=selected[-1].record_hash if selected else "",
            )
        )
    return tuple(results)


def test_gate_passes_common_native_langgraph_and_all_durable_temporal_variants() -> None:
    gate, evidence = _gate_and_evidence()

    result = gate.evaluate(evidence, _green_commands(gate, evidence))
    artifact = result.to_dict()

    assert result.status == "passed"
    assert result.deviations == ()
    assert len(evidence) == 130
    for runtime_id in ("native", "langgraph"):
        scenarios = artifact["scenario_matrix"][runtime_id]
        assert all(
            scenarios[scenario_id]["status"] == "passed"
            for scenario_id in ("research", "code-analysis", "approval", "bounded-parallel-merge")
        )
        assert scenarios["long-running-resume"]["status"] == "incompatible"
        assert scenarios["long-running-resume"]["iterations"] == 0
    temporal = artifact["scenario_matrix"]["temporal"]
    assert all(item["status"] == "passed" and item["durable"] is True for item in temporal.values())
    assert all(item["iterations"] == 10 for item in temporal.values())
    assert all(status == "passed" for runtime in artifact["invariant_matrix"].values() for status in runtime.values())
    for runtime_id in ("native", "langgraph", "temporal"):
        assert artifact["invariant_evidence"][runtime_id]["checkpoint"]
        assert artifact["invariant_evidence"][runtime_id]["recovery"]


def test_gate_artifact_is_byte_stable_and_has_versions_matrix_deviations_and_commands() -> None:
    gate, evidence = _gate_and_evidence()

    first = gate.evaluate(evidence, _green_commands(gate, evidence)).to_dict()
    second = gate.evaluate(
        tuple(reversed(evidence)),
        tuple(reversed(_green_commands(gate, evidence))),
    ).to_dict()

    assert first == second
    assert json.dumps(first, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
        second, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert first["versions"]["reference_catalog_sha256"]
    assert first["versions"]["runtimes"] == {
        "langgraph": "1.0.0",
        "native": "1.0.0",
        "temporal": "1.0.0",
    }
    assert first["deviations"] == []
    assert len(first["reproduce"]) == len(gate.config.verification_commands)
    assert {item["command_id"] for item in first["reproduce"]} == {
        command.command_id for command in gate.config.verification_commands
    }
    assert len(first["artifact_hash"]) == 64


def test_faulty_reference_adapter_semantic_drift_blocks_gate() -> None:
    gate, evidence = _gate_and_evidence()
    target = next(
        item
        for item in evidence
        if item.runtime_id == "native" and item.scenario_id == "research" and item.iteration == 1
    )
    faulty_observation = RuntimeObservation(
        **{
            **target.observation.__dict__,
            "event_types": ("workflow.run.started", "workflow.run.completed"),
        }
    )
    faulty = replace(target, observation=faulty_observation)
    mutated = _rechain(faulty if item is target else item for item in evidence)

    result = gate.evaluate(mutated, _green_commands(gate, mutated))

    assert result.status == "failed"
    assert any(
        item.code == "runtime_conformance_failed"
        and "required_events_missing" in item.details
        and item.runtime_id == "native"
        for item in result.deviations
    )


def test_critical_case_requires_at_least_ten_contiguous_deterministic_iterations() -> None:
    gate, evidence = _gate_and_evidence()
    shortened = _rechain(
        item
        for item in evidence
        if not (item.runtime_id == "langgraph" and item.scenario_id == "approval" and item.iteration == 10)
    )

    result = gate.evaluate(shortened, _green_commands(gate, shortened))

    assert result.status == "failed"
    assert any(
        item.code == "critical_iterations_invalid" and item.runtime_id == "langgraph" and item.scenario_id == "approval"
        for item in result.deviations
    )


def test_critical_case_accepts_more_than_ten_contiguous_iterations() -> None:
    gate, evidence = _gate_and_evidence()
    source = next(
        item
        for item in evidence
        if item.runtime_id == "langgraph" and item.scenario_id == "approval" and item.iteration == 10
    )

    expanded = _rechain((*evidence, replace(source, iteration=11, run_id="langgraph-approval-11")))
    result = gate.evaluate(expanded, _green_commands(gate, expanded))

    assert result.status == "passed"
    assert result.scenario_matrix["langgraph"]["approval"]["iterations"] == 11


def test_unsupported_runtime_scenario_is_incompatible_and_never_synthetic_success() -> None:
    gate, evidence = _gate_and_evidence()
    source = next(item for item in evidence if item.runtime_id == "native")
    synthetic = replace(
        source,
        scenario_id="long-running-resume",
        iteration=1,
        run_id="native-incompatible-long-running-resume-1",
    )
    mutated = _rechain((*evidence, synthetic))

    result = gate.evaluate(mutated, _green_commands(gate, mutated))

    assert result.status == "failed"
    assert result.scenario_matrix["native"]["long-running-resume"]["status"] == "incompatible"
    assert any(item.code == "incompatible_scenario_has_success_evidence" for item in result.deviations)


def test_research_name_does_not_invent_checkpoint_or_recovery_proofs() -> None:
    gate, evidence = _gate_and_evidence()
    stripped = _rechain(
        replace(
            item,
            proofs={
                **item.proofs,
                "checkpoint": "not_applicable",
                "recovery": "not_applicable",
            },
        )
        if item.runtime_id == "native"
        else item
        for item in evidence
    )

    result = gate.evaluate(stripped, _green_commands(gate, stripped))

    assert result.status == "failed"
    assert {
        item.details
        for item in result.deviations
        if item.runtime_id == "native" and item.code == "reported_capability_not_covered"
    } >= {("checkpoint", "checkpoint"), ("resume", "recovery")}
    assert result.invariant_matrix["native"]["checkpoint"] == "failed"
    assert result.invariant_matrix["native"]["recovery"] == "failed"


def test_durable_flag_does_not_invent_checkpoint_or_recovery_proofs() -> None:
    gate, evidence = _gate_and_evidence()
    stripped = _rechain(
        replace(
            item,
            proofs={
                **item.proofs,
                "checkpoint": "not_applicable",
                "recovery": "not_applicable",
            },
        )
        if item.runtime_id == "temporal"
        else item
        for item in evidence
    )

    result = gate.evaluate(stripped, _green_commands(gate, stripped))

    assert result.status == "failed"
    assert any(
        item.runtime_id == "temporal"
        and item.code == "reported_capability_not_covered"
        and item.details == ("durability", "recovery")
        for item in result.deviations
    )


def test_non_durable_local_ephemeral_events_cannot_source_production_proofs() -> None:
    gate, evidence = _gate_and_evidence()
    target = next(
        item
        for item in evidence
        if item.runtime_id == "langgraph"
        and item.scenario_id == "research"
        and item.iteration == 1
    )
    event_types = (
        set(target.observation.event_types)
        - {
            "workflow.checkpoint.hub_persisted",
            "workflow.checkpoint.hub_restored",
        }
    ) | {
        "workflow.checkpoint.created",
        "workflow.run.resumed",
    }
    local_ephemeral = replace(
        target,
        observation=replace(
            target.observation,
            event_types=tuple(sorted(event_types)),
        ),
    )
    mutated = _rechain(local_ephemeral if item is target else item for item in evidence)

    result = gate.evaluate(mutated, _green_commands(gate, mutated))

    assert result.status == "failed"
    assert {
        item.details
        for item in result.deviations
        if item.runtime_id == "langgraph" and item.code == "proof_source_missing"
    } >= {("checkpoint",), ("recovery",)}
    assert result.invariant_matrix["langgraph"]["checkpoint"] == "failed"
    assert result.invariant_matrix["langgraph"]["recovery"] == "failed"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("status", "failed", "release_gate_not_green"),
        ("contract_hash", "0" * 64, "release_gate_contract_hash_mismatch"),
        ("artifact_hash", "0" * 64, "release_gate_artifact_hash_mismatch"),
    ],
)
def test_productive_selection_requires_green_current_untampered_gate_evidence(
    field: str,
    value: str,
    reason: str,
) -> None:
    gate, evidence = _gate_and_evidence()
    artifact = gate.evaluate(evidence, _green_commands(gate, evidence)).to_dict()
    verifier = WorkflowRuntimeGateEvidenceVerifier()
    valid, valid_reason = verifier.verify(
        artifact,
        expected_contract_hash=gate.contract_hash,
        runtime_id="native",
        required_capabilities=frozenset({"approval", "tool_calling"}),
        required_scenarios=("research", "approval"),
    )
    assert (valid, valid_reason) == (True, "release_gate_verified")

    artifact[field] = value
    allowed, actual_reason = verifier.verify(
        artifact,
        expected_contract_hash=gate.contract_hash,
        runtime_id="native",
        required_capabilities=frozenset({"approval", "tool_calling"}),
        required_scenarios=("research", "approval"),
    )

    assert allowed is False
    assert actual_reason == reason


def test_skipped_or_missing_verification_is_not_release_evidence() -> None:
    gate, evidence = _gate_and_evidence()
    commands = list(_green_commands(gate, evidence))
    commands[0] = replace(commands[0], skipped=1)

    skipped = gate.evaluate(evidence, commands)
    missing = gate.evaluate(evidence, commands[1:])

    assert skipped.status == "failed"
    assert missing.status == "failed"
    assert any(item.code == "verification_command_failed" for item in skipped.deviations)
    assert any(item.code == "verification_command_missing" for item in missing.deviations)


@pytest.mark.parametrize(
    "result",
    (
        VerificationCommandResult("optional-runtime", ("python", "-m", "pytest"), 0, tests=1, skipped=1),
        VerificationCommandResult("optional-runtime", ("python", "-m", "pytest"), 0, tests=0),
        VerificationCommandResult("optional-runtime", ("python", "-m", "pytest"), 5, tests=0),
        VerificationCommandResult("optional-runtime", ("python", "-m", "pytest"), 1, tests=1, errors=1),
    ),
)
def test_optional_runtime_skip_absence_or_collection_failure_is_never_green(
    result: VerificationCommandResult,
) -> None:
    assert result.passed is False
    assert result.to_dict()["status"] == "failed"


def test_reference_probes_are_bound_to_release_verification_commands() -> None:
    config = load_workflow_release_gate_config()
    commands = {command.command_id: command.argv for command in config.verification_commands}

    assert "tests/workflow_runtime/release_gate/test_native_reference_probes.py" in commands["native-runtime"]
    assert "tests/workflow_runtime/release_gate/test_temporal_reference_probes.py" in commands["temporal-runtime"]
    assert (
        "tests/workflow_runtime/release_gate/test_release_gate_script.py" in commands["runtime-contract-and-security"]
    )
    assert {
        "tests/test_run_control_service.py",
        "tests/test_workflow_control_persistence.py",
    }.issubset(commands["runtime-contract-and-security"])
    assert {
        "tests/test_workflow_runtime_operations_api.py",
        "tests/test_agent_token_file_auth.py",
        "tests/test_user_session_tokens.py",
        "tests/test_user_token_scope.py",
        "tests/test_auth_scenarios.py",
        "tests/test_chat_process_binding.py",
        "tests/test_oidc_identity_link_routes.py",
        "tests/test_oidc_identity_link_service.py",
        "tests/test_oidc_identity_provisioning_repository.py",
        "tests/test_ws_terminal.py",
        "tests/test_todo_tasks.py",
        "tests/test_control_center_api_contracts.py",
        "tests/test_workflow_runtime_test_support.py",
    }.issubset(commands["runtime-user-session-security"])


def test_ci_job_installs_all_runtimes_runs_gate_and_uploads_structured_result() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/quality-and-docs.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["workflow-runtime-release-gate"]
    steps = job["steps"]
    install = next(
        step["run"] for step in steps if step.get("name") == "Install all optional workflow-runtime dependencies"
    )
    run = next(step["run"] for step in steps if step.get("name") == "Run mandatory workflow-runtime release gate")
    upload = next(step for step in steps if step.get("name") == "Upload workflow-runtime release evidence")

    assert "-r requirements.lock" in install
    assert "-r requirements-dev.lock" in install
    assert "-r docker/compose-next/requirements.langgraph-worker.lock" in install
    assert "temporalio==1.30.0" in install
    assert "scripts/run-workflow-runtime-release-gate.py" in run
    assert "--output ci-artifacts/workflow-runtime-release-gate/result.json" in run
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "ci-artifacts/workflow-runtime-release-gate/result.json"
    assert upload["with"]["if-no-files-found"] == "error"


def test_static_v1_fixture_is_never_accepted_as_release_evidence() -> None:
    gate = WorkflowRuntimeReleaseGate()

    with pytest.raises(ValueError, match="fixture_only"):
        load_runtime_release_evidence(EVIDENCE_PATH, expected_contract_hash=gate.contract_hash)


def test_v1_iteration_templates_are_not_expanded_into_runtime_runs(tmp_path: Path) -> None:
    gate = WorkflowRuntimeReleaseGate()
    payload = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    payload.pop("fixture_only")
    payload["evidence_origin"] = "runtime_execution"
    payload["contract_hash"] = gate.contract_hash
    template = tmp_path / "template.json"
    template.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="version_unsupported"):
        load_runtime_release_evidence(template, expected_contract_hash=gate.contract_hash)


def test_explicit_manifest_is_bound_to_current_contract_hash(tmp_path: Path) -> None:
    gate, evidence = _gate_and_evidence()
    payload = {
        "schema": "ananta.workflow_runtime_release_input.v1",
        "evidence_version": "2.0.0",
        "contract_hash": "f" * 64,
        "records": [item.to_dict() for item in evidence],
    }
    wrong = tmp_path / "evidence.json"
    wrong.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="contract_hash_mismatch"):
        load_runtime_release_evidence(wrong, expected_contract_hash=gate.contract_hash)


def test_runtime_evidence_sink_appends_a_bound_hash_chain(tmp_path: Path) -> None:
    gate, evidence = _gate_and_evidence()
    source, second = tuple(item for item in evidence if item.runtime_id == "native")[:2]
    command = next(item for item in gate.config.verification_commands if item.command_id == "native-runtime")
    path = tmp_path / "native.jsonl"
    sink = RuntimeEvidenceSink(
        RuntimeEvidenceBinding(
            path=path,
            contract_hash=gate.contract_hash,
            revision=REVISION,
            build_id=BUILD_ID,
            command_id=command.command_id,
            command_hash=release_verification_command_hash(command, gate.contract_hash),
            runtime_id="native",
        )
    )
    for template in (source, second):
        sink.append(
            runtime_id=template.runtime_id,
            runtime_version=template.runtime_version,
            scenario_id=template.scenario_id,
            iteration=template.iteration,
            run_id=template.run_id,
            capabilities=template.capabilities,
            durable=template.durable,
            observation=template.observation,
            proofs=template.proofs,
        )

    loaded = load_runtime_release_evidence_jsonl(
        path,
        expected_contract_hash=gate.contract_hash,
        expected_revision=REVISION,
        expected_build_id=BUILD_ID,
        expected_command_id=command.command_id,
        expected_command_hash=release_verification_command_hash(command, gate.contract_hash),
    )

    assert [item.chain_index for item in loaded] == [1, 2]
    assert loaded[0].previous_record_hash == EVIDENCE_CHAIN_GENESIS
    assert loaded[1].previous_record_hash == loaded[0].record_hash


def test_runtime_evidence_sink_rejects_duplicate_run_ids(tmp_path: Path) -> None:
    gate, evidence = _gate_and_evidence()
    source = next(item for item in evidence if item.runtime_id == "native")
    command = next(item for item in gate.config.verification_commands if item.command_id == "native-runtime")
    sink = RuntimeEvidenceSink(
        RuntimeEvidenceBinding(
            path=tmp_path / "native.jsonl",
            contract_hash=gate.contract_hash,
            revision=REVISION,
            build_id=BUILD_ID,
            command_id=command.command_id,
            command_hash=release_verification_command_hash(command, gate.contract_hash),
            runtime_id="native",
        )
    )
    values = {
        "runtime_id": source.runtime_id,
        "runtime_version": source.runtime_version,
        "scenario_id": source.scenario_id,
        "iteration": source.iteration,
        "run_id": source.run_id,
        "capabilities": source.capabilities,
        "durable": source.durable,
        "observation": source.observation,
        "proofs": source.proofs,
    }
    sink.append(**values)

    with pytest.raises(ValueError, match="run_id_duplicate"):
        sink.append(**values)


def test_tampered_jsonl_record_is_rejected_before_gate_evaluation(tmp_path: Path) -> None:
    gate, evidence = _gate_and_evidence()
    source = next(item for item in evidence if item.runtime_id == "native")
    path = tmp_path / "tampered.jsonl"
    payload = source.to_dict()
    payload["observation"]["terminal_status"] = "completed-but-tampered"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="record_hash_mismatch"):
        load_runtime_release_evidence_jsonl(
            path,
            expected_contract_hash=gate.contract_hash,
        )


def test_selection_admission_binds_alias_version_contract_capabilities_and_scenarios() -> None:
    gate, evidence = _gate_and_evidence()
    artifact = gate.evaluate(evidence, _green_commands(gate, evidence)).to_dict()
    admission = WorkflowRuntimeReleaseAdmission(
        artifact=artifact,
        expected_contract_hash=gate.contract_hash,
        runtime_aliases={"ananta-native": "native"},
    )
    artifact["status"] = "failed"
    research, *_, long_running = load_reference_workflows()

    assert admission.evaluate(
        plan=research.plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"retrieval", "structured_output", "audit", "authorization", "policy"}),
    ) == (True, "runtime_release_gate_verified")
    assert admission.evaluate(
        plan=long_running.plan,
        runtime_id="temporal",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"checkpoint", "durability", "resume"}),
    ) == (True, "runtime_release_gate_verified")
    assert admission.evaluate(
        plan=long_running.plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"checkpoint", "resume"}),
    ) == (False, "runtime_release_gate_scenario_not_covered")


def test_selection_admission_fails_closed_for_stale_hash_version_or_unknown_capability() -> None:
    gate, evidence = _gate_and_evidence()
    artifact = gate.evaluate(evidence, _green_commands(gate, evidence)).to_dict()
    research = load_reference_workflows()[0]

    stale = WorkflowRuntimeReleaseAdmission(
        artifact=artifact,
        expected_contract_hash="0" * 64,
        runtime_aliases={"ananta-native": "native"},
    )
    assert stale.evaluate(
        plan=research.plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"retrieval"}),
    ) == (False, "runtime_release_gate_contract_hash_mismatch")

    current = WorkflowRuntimeReleaseAdmission(
        artifact=artifact,
        expected_contract_hash=gate.contract_hash,
        runtime_aliases={"ananta-native": "native"},
    )
    assert current.evaluate(
        plan=research.plan,
        runtime_id="ananta-native",
        runtime_version="2.0.0",
        required_capabilities=frozenset({"retrieval"}),
    ) == (False, "runtime_release_gate_runtime_version_mismatch")
    assert current.evaluate(
        plan=research.plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"unproven-capability"}),
    ) == (False, "runtime_release_gate_capability_not_covered")


def test_selection_admission_requires_scenario_evidence_for_profile_capabilities() -> None:
    gate, evidence = _gate_and_evidence()
    artifact = gate.evaluate(evidence, _green_commands(gate, evidence)).to_dict()
    artifact["scenario_matrix"]["native"]["bounded-parallel-merge"]["status"] = "failed"
    hash_payload = dict(artifact)
    hash_payload.pop("artifact_hash")
    artifact["artifact_hash"] = sha256_json(hash_payload)
    admission = WorkflowRuntimeReleaseAdmission(
        artifact=artifact,
        expected_contract_hash=gate.contract_hash,
        runtime_aliases={"ananta-native": "native"},
    )
    research = load_reference_workflows()[0]

    assert admission.evaluate(
        plan=research.plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"retrieval", "bounded_parallel"}),
    ) == (False, "runtime_release_gate_scenario_incompatible")
