from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.services.three_worker_track_executor import ThreeWorkerTrackExecutor


class _DummyResult:
    def __init__(self) -> None:
        self.status = SimpleNamespace(value="ok")
        self.summary = "ok"
        self.artifacts = []
        self.warnings = []
        self.policy_observations = []
        self.no_side_effects_confirmed = True


class _CapturingHermesAdapter:
    last_config = None

    def __init__(self, *, config) -> None:
        _CapturingHermesAdapter.last_config = config

    def plan_only(self, _envelope, *, context_blocks):
        assert context_blocks
        return _DummyResult()


def test_hermes_track_uses_config_ref_overrides(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "hermes.openrouter.yaml"
    cfg.write_text(
        """
provider:
  base_url: https://openrouter.example/api/v1
  api_key_env: OPENROUTER_API_KEY
models:
  primary:
    model: google/gemini-2.5-flash
  coding_fallback:
    model: deepseek/deepseek-v4-flash
  cheap_fallback:
    model: google/gemini-2.5-flash-lite
runtime:
  request_timeout_seconds: 222
  api_max_retries: 7
fallback_policy:
  order:
    - google/gemini-2.5-flash
    - deepseek/deepseek-v4-flash
""",
        encoding="utf-8",
    )

    import agent.services.three_worker_track_executor as mod

    monkeypatch.setattr(mod, "HermesAdapter", _CapturingHermesAdapter)

    executor = ThreeWorkerTrackExecutor(agent_cfg={})
    track = {
        "id": "hermes",
        "requested_backend": "hermes",
        "worker_type": "hermes",
        "config_ref": str(cfg),
    }
    out = executor(track, {"prompt": "analyze", "run_id": "r1"})

    assert out["status"] == "ok"
    used = _CapturingHermesAdapter.last_config
    assert used is not None
    assert used.base_url == "https://openrouter.example/api/v1"
    assert used.api_key_env == "OPENROUTER_API_KEY"
    assert used.default_model == "google/gemini-2.5-flash"
    assert used.timeout_seconds == 222
    assert used.max_retries == 7


def test_non_hermes_track_returns_handoff() -> None:
    executor = ThreeWorkerTrackExecutor(
        agent_cfg={},
        task_scoped_runner=lambda _track, _ctx: {
            "status": "pending_integration",
            "reason": "task_scoped_runner_injected",
        },
    )
    out = executor(
        {"id": "opencode-local", "requested_backend": "opencode", "worker_type": "opencode", "execution_provider": "local"},
        {"planning": {"provider": "lmstudio", "model": "google/gemma-4-e4b"}},
    )
    assert out["status"] == "pending_integration"
    assert out["reason"] == "task_scoped_runner_injected"
