from __future__ import annotations

from agent.services.three_worker_run_config_service import ThreeWorkerRunConfigService


def test_three_worker_run_config_resolves_local_lmstudio(tmp_path) -> None:
    cfg_file = tmp_path / "run.yaml"
    cfg_file.write_text(
        """
planning:
  force_local: true
  provider_env: PLANNING_PROVIDER
  default_provider: lmstudio
  lmstudio:
    default_base_url: http://localhost:1234/v1
    default_model: qwen2.5-coder-14b-instruct
tracks:
  - id: hermes
    enabled: true
    planning_provider: local
    requested_backend: hermes
    config_ref: config/workers/hermes.openrouter.yaml
  - id: opencode-local
    enabled: true
    planning_provider: local
    execution_provider: local
    requested_backend: opencode
  - id: ananta-worker-local
    enabled: true
    planning_provider: local
    execution_provider: local
    requested_backend: ananta-worker
routing_expectations:
  planning:
    must_not_use:
      - openrouter
""",
        encoding="utf-8",
    )
    svc = ThreeWorkerRunConfigService(repo_root=tmp_path)
    resolved = svc.resolve(cfg_file, env={})

    assert resolved["planning"]["resolved"]["provider"] == "lmstudio"
    assert resolved["planning"]["resolved"]["cloud_allowed"] is False
    assert svc.build_provider_entries(resolved) == [
        "hermes:qwen2.5-coder-14b-instruct",
        "opencode:qwen2.5-coder-14b-instruct",
        "ananta-worker:qwen2.5-coder-14b-instruct",
    ]


def test_three_worker_run_config_rejects_non_local_planning(tmp_path) -> None:
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(
        """
planning:
  force_local: false
tracks:
  - id: hermes
    planning_provider: local
    config_ref: x
  - id: opencode-local
    planning_provider: local
    execution_provider: local
  - id: ananta-worker-local
    planning_provider: local
    execution_provider: local
""",
        encoding="utf-8",
    )
    svc = ThreeWorkerRunConfigService(repo_root=tmp_path)
    try:
        svc.resolve(cfg_file, env={})
        assert False, "expected validation failure"
    except ValueError as exc:
        assert "planning_must_force_local" in str(exc)
