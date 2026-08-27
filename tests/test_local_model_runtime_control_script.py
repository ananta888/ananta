from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.repositories.local_model_runtime_decision import SqliteLocalRuntimeDecisionRepository
from agent.services.local_model_runtime_lifecycle_service import LocalRuntimeLifecycleService
from agent.services.local_multi_model_runtime import GiB, ResourceSnapshot, rtx3080_local_model_capabilities

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "local_model_runtime_control_script",
    ROOT / "scripts/local-model-runtime-control.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Resources:
    def __init__(self, free_vram=10 * GiB):
        self.free_vram = free_vram

    def snapshot(self):
        return ResourceSnapshot(10 * GiB, self.free_vram, 64 * GiB)


def _decision(tmp_path, *, free_vram=10 * GiB):
    return LocalRuntimeLifecycleService(
        resources=Resources(free_vram),
        decisions=SqliteLocalRuntimeDecisionRepository(tmp_path / "runtime.sqlite3"),
        clock=lambda: datetime(2026, 8, 27, tzinfo=UTC),
    ).evaluate(request_id="bridge-request", capabilities=rtx3080_local_model_capabilities())


def test_bridge_recomputes_decision_digest_and_rejects_tampering(tmp_path):
    decision = _decision(tmp_path)
    action, validated = MODULE.validate_control_request(
        {
            "action": "activate",
            "decision": decision.to_wire(),
        }
    )
    tampered = decision.to_wire()
    tampered["free_vram_bytes"] -= 1

    assert action == "activate"
    assert validated.decision_id == decision.decision_id
    with pytest.raises(ValueError, match="digest_mismatch"):
        MODULE.validate_control_request({"action": "activate", "decision": tampered})


def test_bridge_executes_only_fixed_systemd_argument_vectors(tmp_path):
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    decision = _decision(tmp_path)
    MODULE.run_control_action(
        "restart",
        run,
        decision=decision,
        state_dir=tmp_path / "state",
    )

    assert captured["command"] == ["systemctl", "--user", "restart", "ananta-local-model-runtime.service"]
    assert captured["kwargs"]["timeout"] == 90
    contexts = (tmp_path / "state/active-contexts.env").read_text()
    assert "ANANTA_LFM_CTX=32768" in contexts
    assert f"ANANTA_LOCAL_RUNTIME_DECISION_DIGEST={decision.decision_digest}" in contexts
    with pytest.raises(ValueError, match="action_invalid"):
        MODULE.run_control_action("shell", run)


def test_bridge_replays_completed_action_without_second_restart(tmp_path):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    decision = _decision(tmp_path)
    repository = MODULE.LocalRuntimeControlActionRepository(tmp_path / "actions.sqlite3")
    lock = MODULE.threading.RLock()

    first = MODULE.apply_control_action(
        "restart",
        run,
        decision=decision,
        repository=repository,
        lock=lock,
        state_dir=tmp_path,
    )
    replay = MODULE.apply_control_action(
        "restart",
        run,
        decision=decision,
        repository=repository,
        lock=lock,
        state_dir=tmp_path,
    )

    assert replay == first
    assert len(calls) == 1


def test_failed_restart_restores_previous_effective_contexts(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    context_path = state_dir / "active-contexts.env"
    context_path.write_text("previous-safe-context\n", encoding="ascii")

    def fail_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["systemctl"])

    with pytest.raises(RuntimeError, match="command_failed"):
        MODULE.run_control_action(
            "restart",
            fail_run,
            decision=_decision(tmp_path),
            state_dir=state_dir,
        )

    assert context_path.read_text(encoding="ascii") == "previous-safe-context\n"


def test_pressure_decision_reaches_runtime_as_lfm_16k_context(tmp_path):
    decision = _decision(tmp_path, free_vram=8 * GiB)

    MODULE.run_control_action(
        "activate",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
        decision=decision,
        state_dir=tmp_path / "state",
    )

    assert decision.admitted is True
    assert "ANANTA_LFM_CTX=16384" in (tmp_path / "state/active-contexts.env").read_text()


def test_bridge_resource_payload_is_bounded_and_content_free(tmp_path):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "10240, 2048\n", "")

    payload = MODULE.resource_payload(run, state_dir=tmp_path)

    assert payload["total_vram_bytes"] == 10 * GiB
    assert payload["free_vram_bytes"] == 2 * GiB
    assert "prompt" not in json.dumps(payload)


def test_bridge_attributes_bounded_process_resources_by_fixed_pid_files(tmp_path, monkeypatch):
    (tmp_path / "kat.pid").write_text("123\n", encoding="ascii")
    (tmp_path / "needle.pid").write_text("456\n", encoding="ascii")
    monkeypatch.setattr(MODULE, "_process_rss_bytes", lambda path: 10 if path.parent.name == "123" else 20)
    monkeypatch.setattr(
        MODULE,
        "_descendant_pids",
        lambda pid: frozenset({123, 124}) if pid == 123 else frozenset({456}),
    )

    def run(command, **kwargs):
        del kwargs
        assert command[1] == "--query-compute-apps=pid,used_memory"
        return subprocess.CompletedProcess(command, 0, "124, 512\n999, 9000\n", "")

    usage = MODULE.runtime_usage_payload(run, state_dir=tmp_path)

    assert usage == {
        "kat": {"vram_used_bytes": 512 * 1024 * 1024, "ram_used_bytes": 30},
        "needle": {"vram_used_bytes": 0, "ram_used_bytes": 20},
    }
