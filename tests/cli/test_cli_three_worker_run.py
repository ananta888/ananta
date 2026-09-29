from __future__ import annotations

from dataclasses import dataclass

from agent.cli.main import _run_ananta_run


@dataclass
class _DummyResult:
    payload: dict

    def as_dict(self):
        return self.payload


class _DummyRunner:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return _DummyResult(
            {
                "status": "ok",
                "run_id": "r1",
                "planning": {"provider": "lmstudio", "model": "google/gemma-4-e4b"},
                "tracks": [],
                "summary": {},
            }
        )


def test_cli_three_worker_default_uses_dry_run(monkeypatch) -> None:
    runner = _DummyRunner()

    import agent.services.three_worker_comparison_runner as runner_mod
    import agent.services.three_worker_track_executor as exec_mod

    monkeypatch.setattr(runner_mod, "get_three_worker_comparison_runner", lambda: runner)
    monkeypatch.setattr(exec_mod, "get_three_worker_track_executor", lambda: object())

    rc = _run_ananta_run(["three-worker", "--prompt", "test"]) 

    assert rc == 0
    assert len(runner.calls) == 1
    assert runner.calls[0]["track_executor"] is None


def test_cli_three_worker_execute_wires_track_executor(monkeypatch) -> None:
    runner = _DummyRunner()
    sentinel = object()

    import agent.services.three_worker_comparison_runner as runner_mod
    import agent.services.three_worker_track_executor as exec_mod

    monkeypatch.setattr(runner_mod, "get_three_worker_comparison_runner", lambda: runner)
    monkeypatch.setattr(exec_mod, "get_three_worker_track_executor", lambda: sentinel)

    rc = _run_ananta_run(["three-worker", "--prompt", "test", "--execute"]) 

    assert rc == 0
    assert len(runner.calls) == 1
    assert runner.calls[0]["track_executor"] is sentinel
