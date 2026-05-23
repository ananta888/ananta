from __future__ import annotations

from agent.services.three_worker_comparison_runner import ThreeWorkerComparisonRunner
from agent.services.three_worker_run_config_service import ThreeWorkerRunConfigService


def test_three_worker_runner_dry_run(tmp_path) -> None:
    cfg_file = tmp_path / "run.yaml"
    cfg_file.write_text(
        """
run:
  id: demo
planning:
  force_local: true
  default_provider: ollama
  ollama:
    default_base_url: http://localhost:11434/v1
    default_model: qwen2.5-coder:14b
tracks:
  - id: hermes
    enabled: true
    planning_provider: local
    requested_backend: hermes
    worker_type: hermes
    config_ref: config/workers/hermes.openrouter.yaml
  - id: opencode-local
    enabled: true
    planning_provider: local
    execution_provider: local
    requested_backend: opencode
    worker_type: opencode
  - id: ananta-worker-local
    enabled: true
    planning_provider: local
    execution_provider: local
    requested_backend: ananta-worker
    worker_type: ananta-worker
""",
        encoding="utf-8",
    )
    runner = ThreeWorkerComparisonRunner(config_service=ThreeWorkerRunConfigService(repo_root=tmp_path))
    result = runner.run(prompt="analyze commits", config_path=str(cfg_file), env={})
    data = result.as_dict()

    assert data["status"] == "ok"
    assert data["planning"]["provider"] == "ollama"
    assert data["summary"]["track_count"] == 3
    assert data["tracks"][0]["result"]["requested_backend"] == "hermes"
