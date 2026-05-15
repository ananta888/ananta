import json
from pathlib import Path

import jsonschema

from agent.config_defaults import build_default_agent_config, apply_env_config_overrides
from agent.services.worker_pool_scheduler_service import WorkerPoolSchedulerService


ROOT = Path(__file__).resolve().parents[1]


def _load_schema_and_config():
    schema = json.loads((ROOT / "schemas/runtime/worker_parallelism_config.v1.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "config/worker_parallelism.default.json").read_text(encoding="utf-8"))
    return schema, config


def test_worker_parallelism_default_config_validates():
    schema, config = _load_schema_and_config()
    jsonschema.validate(config, schema)
    assert config["ollama"]["model_defaults"]["max_parallel_requests"] == 4
    assert config["worker_pool"]["minimum_local_worker_containers"] >= 2
    assert config["worker_pool"]["kinds"]["native_ananta_worker"]["max_parallel_tasks_per_container"] == 4
    assert config["worker_pool"]["kinds"]["native_ananta_worker"]["subworkers"]["max_children_per_parent"] == 4


def test_worker_parallelism_invalid_values_rejected():
    schema, config = _load_schema_and_config()
    bad = json.loads(json.dumps(config))
    bad["ollama"]["model_defaults"]["max_parallel_requests"] = 0
    with __import__("pytest").raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_env_overrides_and_effective_concurrency_rule(monkeypatch):
    cfg = build_default_agent_config()
    monkeypatch.setenv("ANANTA_OLLAMA_MAX_PARALLEL", "7")
    monkeypatch.setenv("ANANTA_WORKER_MAX_PARALLEL_TASKS", "5")
    monkeypatch.setenv("ANANTA_SUBWORKER_MAX_CHILDREN", "3")
    monkeypatch.setenv("ANANTA_WORKER_POOL_ENABLED", "true")
    apply_env_config_overrides(cfg)

    wp = cfg["worker_parallelism"]
    assert wp["enabled"] is True
    assert wp["ollama"]["model_defaults"]["max_parallel_requests"] == 7
    assert wp["worker_pool"]["worker_defaults"]["max_parallel_tasks"] == 5
    assert wp["worker_pool"]["kinds"]["native_ananta_worker"]["subworkers"]["max_children_per_parent"] == 3

    assert WorkerPoolSchedulerService.compute_effective_concurrency_cap(2, 4, 8, 6) == 2
