import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest


class TestHubBenchmarkCore:
    """Tests for hub_benchmark.py core module."""

    @pytest.fixture
    def temp_data_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_load_default_config(self, temp_data_dir):
        from agent.hub_benchmark import DEFAULT_HUB_BENCH_CONFIG, load_hub_benchmark_config

        result = load_hub_benchmark_config(temp_data_dir)
        assert isinstance(result, dict)
        assert result.get("enabled") == DEFAULT_HUB_BENCH_CONFIG["enabled"]
        assert result.get("default_models", {}).get("ollama", [None])[0] == "ananta-default"
        assert "scoring" in result
        assert "retention" in result

    def test_load_custom_config(self, temp_data_dir):
        from agent.hub_benchmark import load_hub_benchmark_config

        config_path = os.path.join(temp_data_dir, "hub_benchmark_config.json")
        custom_config = {"enabled": False, "providers": ["test_provider"]}
        with open(config_path, "w") as f:
            json.dump(custom_config, f)

        result = load_hub_benchmark_config(temp_data_dir)
        assert result["enabled"] is False
        assert result["providers"] == ["test_provider"]
        assert "scoring" in result

    def test_load_custom_config_deep_merges_nested_defaults(self, temp_data_dir):
        from agent.hub_benchmark import load_hub_benchmark_config

        config_path = os.path.join(temp_data_dir, "hub_benchmark_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"scoring": {"weights": {"success_rate": 0.9}}}, f)

        result = load_hub_benchmark_config(temp_data_dir)
        assert result["scoring"]["weights"]["success_rate"] == 0.9
        assert result["scoring"]["weights"]["quality_rate"] == 0.35
        assert result["scoring"]["thresholds"]["min_samples"] == 2
        assert result["hub_config"]["fixed_model"]["model"] == "ananta-default"

    def test_load_invalid_config_raises_visible_error(self, temp_data_dir):
        from agent.hub_benchmark import HubBenchmarkDataError, load_hub_benchmark_config

        config_path = os.path.join(temp_data_dir, "hub_benchmark_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{invalid")

        with pytest.raises(HubBenchmarkDataError):
            load_hub_benchmark_config(temp_data_dir)

    def test_load_invalid_results_raises_visible_error(self, temp_data_dir):
        from agent.hub_benchmark import HubBenchmarkDataError, load_hub_benchmark_results

        results_path = os.path.join(temp_data_dir, "hub_benchmark_results.json")
        with open(results_path, "w", encoding="utf-8") as f:
            f.write("[1,2,3]")

        with pytest.raises(HubBenchmarkDataError):
            load_hub_benchmark_results(temp_data_dir)

    def test_save_and_load_results(self, temp_data_dir):
        from agent.hub_benchmark import load_hub_benchmark_results, save_hub_benchmark_results

        test_data = {"models": {"test:model": {"provider": "test", "model": "model"}}, "updated_at": 12345}
        save_hub_benchmark_results(temp_data_dir, test_data)
        result = load_hub_benchmark_results(temp_data_dir)

        assert result["models"]["test:model"]["provider"] == "test"
        assert result["updated_at"] == 12345

    def test_record_benchmark_sample(self, temp_data_dir):
        from agent.hub_benchmark import record_hub_benchmark_sample

        result = record_hub_benchmark_sample(
            data_dir=temp_data_dir,
            provider="openai",
            model="gpt-4o",
            role_name="coder",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=1500,
            tokens_total=500,
            cost_units=0.01,
        )
        assert result["recorded"] is True
        assert result["model_key"] == "openai:gpt-4o"
        assert result["role_name"] == "coder"

    def test_record_benchmark_sample_invalid(self, temp_data_dir):
        from agent.hub_benchmark import record_hub_benchmark_sample

        result = record_hub_benchmark_sample(
            data_dir=temp_data_dir,
            provider="",
            model="",
            role_name="coder",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=1500,
            tokens_total=500,
        )
        assert result["recorded"] is False

    def test_score_hub_bucket(self, temp_data_dir):
        from agent.hub_benchmark import score_hub_bucket

        bucket = {
            "total": 10,
            "success": 8,
            "quality_pass": 7,
            "latency_ms_total": 10000,
            "tokens_total": 5000,
            "cost_units_total": 0.1,
        }
        result = score_hub_bucket(bucket)

        assert result["total"] == 10
        assert result["success_rate"] == 0.8
        assert result["quality_rate"] == 0.7
        assert result["avg_latency_ms"] == 1000.0
        assert "suitability_score" in result

    def test_score_hub_bucket_empty(self, temp_data_dir):
        from agent.hub_benchmark import score_hub_bucket

        bucket = {}
        result = score_hub_bucket(bucket)

        assert result["total"] == 0
        assert result["success_rate"] == 0.0
        assert result["suitability_score"] >= 0.0

    def test_recommend_hub_model(self, temp_data_dir):
        from agent.hub_benchmark import record_hub_benchmark_sample, recommend_hub_model

        for i in range(5):
            record_hub_benchmark_sample(
                data_dir=temp_data_dir,
                provider="openai",
                model="gpt-4o",
                role_name="coder",
                task_kind="coding",
                success=True,
                quality_gate_passed=True,
                latency_ms=1000 + i * 100,
                tokens_total=500,
            )
        record_hub_benchmark_sample(
            data_dir=temp_data_dir,
            provider="anthropic",
            model="claude-3",
            role_name="coder",
            task_kind="coding",
            success=False,
            quality_gate_passed=False,
            latency_ms=2000,
            tokens_total=300,
        )

        result = recommend_hub_model(data_dir=temp_data_dir, role_name="coder", min_samples=2)

        assert result is not None
        assert result["selection_source"] == "hub_benchmark"
        assert result["model"] == "gpt-4o"

    def test_recommend_hub_model_no_data(self, temp_data_dir):
        from agent.hub_benchmark import recommend_hub_model

        result = recommend_hub_model(data_dir=temp_data_dir, role_name="nonexistent", min_samples=2)
        assert result is None

    def test_hub_benchmark_rows(self, temp_data_dir):
        from agent.hub_benchmark import record_hub_benchmark_sample, hub_benchmark_rows

        record_hub_benchmark_sample(
            data_dir=temp_data_dir,
            provider="openai",
            model="gpt-4o",
            role_name="planner",
            task_kind="planning",
            success=True,
            quality_gate_passed=True,
            latency_ms=1000,
            tokens_total=500,
        )

        rows, db = hub_benchmark_rows(data_dir=temp_data_dir, role_name="planner", top_n=10)

        assert len(rows) > 0
        row = rows[0]
        assert row["provider"] == "openai"
        assert row["model"] == "gpt-4o"
        assert "overall" in row
        assert "roles" in row

    def test_check_auto_trigger_needed_disabled(self, temp_data_dir):
        from agent.hub_benchmark import check_auto_trigger_needed

        config_path = os.path.join(temp_data_dir, "hub_benchmark_config.json")
        with open(config_path, "w") as f:
            json.dump({"enabled": False}, f)

        needed, reason = check_auto_trigger_needed(temp_data_dir)
        assert needed is False
        assert reason == "disabled"

    def test_check_auto_trigger_needed_insufficient_samples(self, temp_data_dir):
        from agent.hub_benchmark import check_auto_trigger_needed, record_hub_benchmark_sample

        record_hub_benchmark_sample(
            data_dir=temp_data_dir,
            provider="test",
            model="test",
            role_name="test",
            task_kind="test",
            success=True,
            quality_gate_passed=True,
            latency_ms=100,
            tokens_total=10,
        )

        needed, reason = check_auto_trigger_needed(temp_data_dir)
        assert needed is False
        assert "insufficient_samples" in reason

    def test_update_benchmark_run_timestamp(self, temp_data_dir):
        from agent.hub_benchmark import update_benchmark_run_timestamp, load_hub_benchmark_results

        update_benchmark_run_timestamp(temp_data_dir, 99999)
        result = load_hub_benchmark_results(temp_data_dir)
        assert result["last_benchmark_run"] == 99999

        update_benchmark_run_timestamp(temp_data_dir)
        result = load_hub_benchmark_results(temp_data_dir)
        assert result["last_benchmark_run"] > 99999


class TestHubBenchmarkService:
    """Tests for hub_benchmark_service.py."""

    @pytest.fixture
    def temp_data_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_service_initialization(self, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        service = HubBenchmarkService(data_dir=temp_data_dir)
        assert service.data_dir == temp_data_dir

    def test_get_config(self, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        service = HubBenchmarkService(data_dir=temp_data_dir)
        config = service.get_config()
        assert isinstance(config, dict)
        assert "enabled" in config

    def test_get_results(self, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        service = HubBenchmarkService(data_dir=temp_data_dir)
        rows, db = service.get_results(role_name="planner", top_n=5)
        assert isinstance(rows, list)
        assert isinstance(db, dict)

    def test_should_auto_trigger(self, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        service = HubBenchmarkService(data_dir=temp_data_dir)
        needed, reason = service.should_auto_trigger()
        assert isinstance(needed, bool)
        assert reason is not None

    def test_get_recommendation(self, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        service = HubBenchmarkService(data_dir=temp_data_dir)
        result = service.get_recommendation(role_name="planner")
        assert "available" in result
        assert "current" in result

    def test_get_hub_model_recommendation_fixed(self, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        service = HubBenchmarkService(data_dir=temp_data_dir)
        result = service.get_hub_model_recommendation_for_task(task_kind="planning")
        assert "current" in result
        assert "available" in result or "model_type" in result

    @patch("agent.services.hub_benchmark_service._generate_text")
    def test_run_single_benchmark_success(self, mock_generate, temp_data_dir):
        from agent.services.hub_benchmark_service import HubBenchmarkService

        mock_result = {
            "choices": [
                {
                    "message": {
                        "content": "1. Create a sprint plan.\n2. Track milestones and dependencies.\n3. Review risks."
                    }
                }
            ]
        }
        mock_generate.return_value = mock_result

        service = HubBenchmarkService(data_dir=temp_data_dir)
        result = service.run_single_benchmark(
            provider="test",
            model="test-model",
            role_name="tester",
            task_kind="testing",
            prompt="Test prompt",
        )
        assert "success" in result
        assert result["success"] is True
        assert "latency_ms" in result
        assert result["quality_score"] >= 55.0


class TestHubBenchmarkConstants:
    """Tests for constants and edge cases."""

    def test_task_kinds_defined(self):
        from agent.hub_benchmark import HUB_BENCH_TASK_KINDS

        assert isinstance(HUB_BENCH_TASK_KINDS, set)
        assert len(HUB_BENCH_TASK_KINDS) > 0
        assert "planning" in HUB_BENCH_TASK_KINDS
        assert "coding" in HUB_BENCH_TASK_KINDS
        assert "research" in HUB_BENCH_TASK_KINDS


def test_hub_benchmark_route_module_resolves_to_single_file():
    import agent.routes.hub_benchmark as hub_benchmark_module

    assert os.path.basename(hub_benchmark_module.__file__) == "hub_benchmark.py"
