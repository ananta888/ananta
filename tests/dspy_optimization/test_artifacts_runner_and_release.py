from __future__ import annotations

from dataclasses import replace

from agent.services.dspy_program_artifact_store import DspyProgramArtifactStore
from agent.services.dspy_release_gate import DspyReleaseGate
from tests.dspy_optimization.helpers import program, spec
from worker.optimization.dspy.job_runner import DspyOptimizationJobRunner


class FakeEngine:
    def optimize(self, _spec, baseline, _records):
        return replace(baseline, program_id="planning-candidate")


def test_headless_worker_e2e_produces_tenant_scoped_json_artifact_without_delegation(tmp_path) -> None:
    runner = DspyOptimizationJobRunner(
        FakeEngine(), DspyProgramArtifactStore(tmp_path / "artifacts"), authorization_verifier=lambda job: job["ok"]
    )
    result = runner.run(
        job={"ok": True, "tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        baseline=program(),
        records=[{"goal": "ship", "constraints": []}],
    )
    assert result["state"] == "completed"
    assert result["hub_task_created"] is False
    assert result["worker_delegation_performed"] is False
    assert result["artifact"]["artifact_ref"].startswith("dspy-program:tenant-1:run-1:")


def test_cancelled_worker_stops_before_optimization_without_human_wait(tmp_path) -> None:
    result = DspyOptimizationJobRunner(
        FakeEngine(), DspyProgramArtifactStore(tmp_path / "artifacts"), authorization_verifier=lambda _job: True
    ).run(
        job={"tenant_id": "tenant-1", "run_id": "run-1", "spec": spec().to_dict()},
        baseline=program(),
        records=[],
        cancelled=lambda: True,
    )
    assert result["state"] == "cancelled"
    assert result["human_intervention_required"] is False


def test_release_gate_never_invents_or_promotes_missing_evidence() -> None:
    result = DspyReleaseGate().evaluate(
        local_gates={key: True for key in DspyReleaseGate.REQUIRED}, source_refs=[], run_refs=[]
    )
    assert result["release_allowed"] is False
    assert result["source_refs"] == []
    assert result["run_refs"] == []
