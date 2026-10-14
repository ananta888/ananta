"""Headless container containment and Hub-owned evidence ingress."""

import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.harness_benchmark import BenchmarkSuite, BenchmarkTask
from agent.services.harness_gates import GateEngine, security_gate
from agent.services.harness_runtime import baseline_genome
from agent.services.harness_sandbox_service import HarnessSandboxService
from agent.services.hub_evidence_gate_service import EvidenceGateRequest, HubEvidenceGateService
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from ananta_contracts.harness_sandbox import HarnessSandboxLimits, HarnessSandboxManifest
from worker.core.bounded_process import BoundedProcessResult, BoundedProcessRunner
from worker.harness.sandbox import HarnessContainerSandbox

IMAGE = "python@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba"


def manifest():
    genome = baseline_genome(
        code_hash="a" * 64,
        config_hash="b" * 64,
        provider_id="offline",
        model_id="synthetic",
        gate_refs=("gate://security",),
        promotion_ref="gate://promotion",
    )
    suite = BenchmarkSuite.create(
        version="test-v1",
        tasks=[
            BenchmarkTask("explore", "coding", "synthetic", required_metrics=("task_correctness", "token_input")),
            BenchmarkTask("holdout", "security", "synthetic", holdout=True),
        ],
    )
    source = """import json, os, pathlib, socket
assert os.getuid() == 65532
assert not pathlib.Path('/var/run/docker.sock').exists()
assert 'ANANTA_TEST_SECRET' not in os.environ
try:
    pathlib.Path('/input/genome.json').write_text('mutated')
except OSError:
    pass
else:
    raise AssertionError('input is writable')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=0.1)
except OSError:
    pass
else:
    raise AssertionError('network is available')
print(json.dumps({'metrics': {'task_correctness': 1.0, 'token_input': 0},
                  'gate_inputs': {'security_ok': True}, 'completed_task_ids': ['explore', 'holdout']}))
"""
    return HarnessSandboxManifest(
        IMAGE, "1" * 40, genome.to_json(), suite.suite_id, ("explore", "holdout"), source
    ), suite


@pytest.fixture
def runtime():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        engine, tables=[HubRunEvidenceIdentityDB.__table__, HubSourceEvidenceIdentityDB.__table__]
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    return HubEvidenceGateService(registry), registry


def request(*, synthetic=True, scope="test", key="harness-test-assignment-0001"):
    return EvidenceGateRequest(
        "tenant",
        "project",
        "task",
        "assignment",
        "lease",
        "1" * 40,
        "a" * 64,
        "a" * 64,
        "a" * 64,
        scope,
        "local",
        key,
        (),
        synthetic,
    )


class ProcessDouble:
    def __init__(self, reason="completed", cleanup=True):
        self.calls = []
        self.reason = reason
        self.cleanup = cleanup
        self.staged = None

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1] == "run":
            self.staged = kwargs["cwd"]
            assert {p.name for p in self.staged.iterdir()} == {
                "genome.json",
                "manifest.json",
                "assignment.json",
                "benchmark.py",
            }
            self.projection = json.loads((self.staged / "assignment.json").read_text())
            return BoundedProcessResult(
                0,
                json.dumps(
                    {
                        "metrics": {"task_correctness": 0.9, "token_input": 0},
                        "gate_inputs": {"security_ok": True},
                        "completed_task_ids": ["explore", "holdout"],
                    }
                ),
                self.reason,
            )
        return BoundedProcessResult(0 if self.cleanup else 125, "unavailable", "completed")


def service(runtime, worker, suite):
    return HarnessSandboxService(
        evidence=runtime[0],
        worker=worker,
        suite=suite,
        gates=GateEngine([security_gate("security:scope", "security_ok")]),
    )


def test_manifest_roundtrip_and_budget_mutation_change_digest():
    spec, _ = manifest()
    assert HarnessSandboxManifest.from_mapping(spec.to_mapping()) == spec
    assert replace(spec, limits=replace(spec.limits, cpus=2)).digest != spec.digest
    assert replace(spec, benchmark_source="print('different')").digest != spec.digest


@pytest.mark.parametrize(
    "changes",
    [
        {"image": "python:latest"},
        {"repository_revision": "HEAD"},
        {"seed": True},
        {"task_ids": ("explore", "explore")},
        {"benchmark_source": ""},
        {"limits": HarnessSandboxLimits(memory_bytes=1)},
        {"limits": HarnessSandboxLimits(timeout_seconds=0)},
    ],
)
def test_manifest_rejects_unpinned_or_invalid_inputs(changes):
    spec, _ = manifest()
    with pytest.raises(ValueError):
        replace(spec, **changes).assert_valid()


def test_hub_reserves_before_dispatch_and_verifies_exact_result(runtime):
    spec, suite = manifest()
    process = ProcessDouble()
    result = service(runtime, HarnessContainerSandbox(process=process), suite).evaluate(spec, request=request())
    assert result.evidence.passed and not result.evidence.verified
    assert process.projection["run_id"] == result.evidence.run_id
    assert process.projection["source_ids"] == sorted(result.evidence.source_ids)
    assert not process.staged.exists()
    command, settings = process.calls[0]
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--memory") + 1] == command[command.index("--memory-swap") + 1]
    assert command[command.index("--user") + 1] == "65532:65532"
    assert "--read-only" in command and "--cap-drop" in command and "--gpus" not in command
    assert set(settings["env"]) == {"PATH", "LANG"}
    assert process.calls[-1][0][1:3] == ["rm", "--force"]


@pytest.mark.parametrize("reason", ["timed_out", "output_limit", "cancelled", "runtime_unavailable"])
def test_failures_are_contained_and_never_verified(runtime, reason):
    spec, suite = manifest()
    process = ProcessDouble(reason)
    result = service(runtime, HarnessContainerSandbox(process=process), suite).evaluate(spec, request=request())
    assert not result.evidence.passed and not result.evidence.verified
    assert result.benchmark is None
    assert process.calls[-1][0][1:3] == ["rm", "--force"]
    assert not process.staged.exists()


def test_cleanup_failure_is_not_success(runtime):
    spec, suite = manifest()
    process = ProcessDouble(cleanup=False)
    result = service(runtime, HarnessContainerSandbox(process=process), suite).evaluate(spec, request=request())
    assert not result.evidence.passed
    assert process.calls[-1][0][1:3] == ["container", "inspect"]


def test_synthetic_evidence_never_verifies_as_release_evidence(runtime):
    spec, suite = manifest()
    result = service(runtime, HarnessContainerSandbox(process=ProcessDouble()), suite).evaluate(
        spec, request=request(synthetic=True, scope="test")
    )
    assert result.evidence.passed and not result.evidence.verified


def test_mutated_or_stale_worker_assignment_is_rejected(runtime):
    spec, suite = manifest()

    class StaleWorker:
        def execute(self, manifest, assignment):
            return {
                "passed": True,
                "assignment": {**assignment, "dispatch_lease_id": "stale"},
                "manifest_digest": manifest.digest,
                "genome_id": manifest.genome.genome_id,
                "suite_id": manifest.suite_id,
            }

    with pytest.raises(ValueError, match="harness_sandbox_worker_binding_mismatch"):
        service(runtime, StaleWorker(), suite).evaluate(spec, request=request())


@pytest.mark.parametrize("reason", ["output_limit", "timed_out", "cancelled"])
def test_real_process_runner_is_bounded(tmp_path, reason):
    command = [
        sys.executable,
        "-c",
        "print('x'*2000)" if reason == "output_limit" else "import time;time.sleep(10)",
    ]
    result = BoundedProcessRunner().run(
        command,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=0.1,
        max_output_bytes=1024,
        cancelled=lambda: reason == "cancelled",
    )
    assert result.reason == reason
    assert len(result.output.encode()) <= 1024


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ANANTA_HARNESS_LIVE_SANDBOX") != "1", reason="explicit offline container test")
def test_real_container_isolation_and_timeout_cleanup(runtime, monkeypatch):
    monkeypatch.setenv("ANANTA_TEST_SECRET", "must-not-be-forwarded")
    spec, suite = manifest()
    result = service(runtime, HarnessContainerSandbox(), suite).evaluate(spec, request=request())
    assert result.evidence.passed and not result.evidence.verified
    slow = replace(spec, benchmark_source="import time; time.sleep(60)", limits=replace(spec.limits, timeout_seconds=1))
    result = service(runtime, HarnessContainerSandbox(), suite).evaluate(
        slow, request=request(key="harness-timeout-test-0002")
    )
    assert not result.evidence.passed
    containers = subprocess.run(
        ["docker", "ps", "--all", "--filter", "name=ananta-harness-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert not containers.stdout.strip()
