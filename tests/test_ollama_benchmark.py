import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import requests


class TestOllamaBenchmarkCore:
    """Tests for ollama_benchmark.py core module."""

    @pytest.fixture
    def temp_data_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_load_default_config(self, temp_data_dir):
        from agent.ollama_benchmark import DEFAULT_OLLAMA_BENCH_CONFIG, load_ollama_bench_config

        result = load_ollama_bench_config(temp_data_dir)
        assert isinstance(result, dict)
        assert result.get("enabled") == DEFAULT_OLLAMA_BENCH_CONFIG["enabled"]
        assert result.get("provider") == "ollama"
        assert result.get("ollama_url") == "http://ollama:11434"
        assert "scoring" in result
        assert "retention" in result

    def test_load_custom_config(self, temp_data_dir):
        from agent.ollama_benchmark import load_ollama_bench_config

        config_path = os.path.join(temp_data_dir, "ollama_benchmark_config.json")
        custom_config = {"enabled": False, "ollama_url": "http://custom:11434"}
        with open(config_path, "w") as f:
            json.dump(custom_config, f)

        result = load_ollama_bench_config(temp_data_dir)
        assert result["enabled"] is False
        assert result["ollama_url"] == "http://custom:11434"
        assert "scoring" in result

    def test_load_custom_config_deep_merges_nested_defaults(self, temp_data_dir):
        from agent.ollama_benchmark import load_ollama_bench_config

        config_path = os.path.join(temp_data_dir, "ollama_benchmark_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"parameter_variations": {"temperature": [0.2]}}, f)

        result = load_ollama_bench_config(temp_data_dir)
        assert result["parameter_variations"]["temperature"] == [0.2]
        assert result["parameter_variations"]["top_p"] == [0.5, 0.9, 0.95, 1.0]
        assert result["scoring"]["thresholds"]["min_samples_per_config"] == 1
        assert "planner" in result["role_benchmarks"]

    def test_load_invalid_config_raises_visible_error(self, temp_data_dir):
        from agent.ollama_benchmark import OllamaBenchmarkDataError, load_ollama_bench_config

        config_path = os.path.join(temp_data_dir, "ollama_benchmark_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{invalid")

        with pytest.raises(OllamaBenchmarkDataError):
            load_ollama_bench_config(temp_data_dir)

    def test_load_invalid_results_raises_visible_error(self, temp_data_dir):
        from agent.ollama_benchmark import OllamaBenchmarkDataError, load_ollama_bench_results

        results_path = os.path.join(temp_data_dir, "ollama_benchmark_results.json")
        with open(results_path, "w", encoding="utf-8") as f:
            f.write("42")

        with pytest.raises(OllamaBenchmarkDataError):
            load_ollama_bench_results(temp_data_dir)

    def test_save_and_load_results(self, temp_data_dir):
        from agent.ollama_benchmark import load_ollama_bench_results, save_ollama_bench_results

        test_data = {"models": {"llama3": {"model": "llama3"}}, "updated_at": 12345}
        save_ollama_bench_results(temp_data_dir, test_data)
        result = load_ollama_bench_results(temp_data_dir)

        assert result["models"]["llama3"]["model"] == "llama3"
        assert result["updated_at"] == 12345

    def test_scrum_role_templates_defined(self):
        from agent.ollama_benchmark import SCRUM_ROLE_TEMPLATES, get_role_template_names, get_scrum_role_templates

        templates = get_scrum_role_templates()
        assert isinstance(templates, dict)
        assert len(templates) > 0
        assert "planner" in templates
        assert "coder" in templates
        assert "reviewer" in templates

        role_names = get_role_template_names()
        assert "planner" in role_names
        assert "coder" in role_names

    def test_role_template_structure(self):
        from agent.ollama_benchmark import SCRUM_ROLE_TEMPLATES

        for role_name, role_cfg in SCRUM_ROLE_TEMPLATES.items():
            assert "description" in role_cfg
            assert "task_kind" in role_cfg
            assert "test_prompts" in role_cfg
            assert isinstance(role_cfg["test_prompts"], list)
            assert len(role_cfg["test_prompts"]) > 0

    def test_record_benchmark_sample(self, temp_data_dir):
        from agent.ollama_benchmark import record_ollama_benchmark_sample

        params = {"temperature": 0.7, "top_p": 0.9, "top_k": 40}
        result = record_ollama_benchmark_sample(
            data_dir=temp_data_dir,
            model="llama3",
            role_name="coder",
            task_kind="coding",
            parameters=params,
            success=True,
            quality_gate_passed=True,
            latency_ms=1500,
            tokens_total=500,
            response_text="Generated code here...",
        )
        assert result["recorded"] is True
        assert result["model"] == "llama3"
        assert result["role_name"] == "coder"

    def test_record_benchmark_sample_invalid(self, temp_data_dir):
        from agent.ollama_benchmark import record_ollama_benchmark_sample

        result = record_ollama_benchmark_sample(
            data_dir=temp_data_dir,
            model="",
            role_name="coder",
            task_kind="coding",
            parameters={},
            success=True,
            quality_gate_passed=True,
            latency_ms=1500,
            tokens_total=500,
        )
        assert result["recorded"] is False

    def test_score_ollama_bucket(self, temp_data_dir):
        from agent.ollama_benchmark import score_ollama_bucket

        bucket = {
            "total": 10,
            "success": 8,
            "quality_pass": 7,
            "latency_ms_total": 10000,
            "tokens_total": 5000,
            "cost_units_total": 0.01,
        }
        result = score_ollama_bucket(bucket)

        assert result["total"] == 10
        assert result["success_rate"] == 0.8
        assert result["quality_rate"] == 0.7
        assert result["avg_latency_ms"] == 1000.0
        assert "suitability_score" in result

    def test_score_ollama_bucket_empty(self, temp_data_dir):
        from agent.ollama_benchmark import score_ollama_bucket

        bucket = {}
        result = score_ollama_bucket(bucket)

        assert result["total"] == 0
        assert result["success_rate"] == 0.0
        assert result["suitability_score"] >= 0.0

    def test_recommend_ollama_model(self, temp_data_dir):
        from agent.ollama_benchmark import record_ollama_benchmark_sample, recommend_ollama_model

        for i in range(3):
            record_ollama_benchmark_sample(
                data_dir=temp_data_dir,
                model="llama3",
                role_name="planner",
                task_kind="planning",
                parameters={"temperature": 0.7, "top_p": 0.9, "top_k": 40},
                success=True,
                quality_gate_passed=True,
                latency_ms=1000 + i * 100,
                tokens_total=500,
            )

        result = recommend_ollama_model(data_dir=temp_data_dir, role_name="planner", min_samples=2)
        assert result is not None
        assert result["selection_source"] == "ollama_benchmark"
        assert result["model"] == "llama3"

    def test_recommend_ollama_model_no_data(self, temp_data_dir):
        from agent.ollama_benchmark import recommend_ollama_model

        result = recommend_ollama_model(data_dir=temp_data_dir, role_name="nonexistent", min_samples=2)
        assert result is None

    def test_ollama_benchmark_rows(self, temp_data_dir):
        from agent.ollama_benchmark import record_ollama_benchmark_sample, ollama_benchmark_rows

        record_ollama_benchmark_sample(
            data_dir=temp_data_dir,
            model="llama3",
            role_name="planner",
            task_kind="planning",
            parameters={"temperature": 0.7},
            success=True,
            quality_gate_passed=True,
            latency_ms=1000,
            tokens_total=500,
        )

        rows, db = ollama_benchmark_rows(data_dir=temp_data_dir, role_name="planner", top_n=10)

        assert len(rows) > 0
        row = rows[0]
        assert row["model"] == "llama3"
        assert "overall" in row
        assert "roles" in row

    def test_aggregate_by_parameters(self, temp_data_dir):
        from agent.ollama_benchmark import record_ollama_benchmark_sample

        params1 = {"temperature": 0.5, "top_p": 0.9}
        params2 = {"temperature": 0.8, "top_p": 0.9}

        for _ in range(3):
            record_ollama_benchmark_sample(
                data_dir=temp_data_dir,
                model="mistral",
                role_name="coder",
                task_kind="coding",
                parameters=params1,
                success=True,
                quality_gate_passed=True,
                latency_ms=1000,
                tokens_total=500,
            )

        for _ in range(2):
            record_ollama_benchmark_sample(
                data_dir=temp_data_dir,
                model="mistral",
                role_name="coder",
                task_kind="coding",
                parameters=params2,
                success=False,
                quality_gate_passed=False,
                latency_ms=2000,
                tokens_total=400,
            )

        from agent.ollama_benchmark import load_ollama_bench_results

        db = load_ollama_bench_results(temp_data_dir)
        entry = db["models"]["mistral"]
        assert "parameters" in entry
        assert len(entry["parameters"]) == 2

    def test_get_best_parameters_returns_dict(self, temp_data_dir):
        from agent.ollama_benchmark import get_best_parameters_for_model, record_ollama_benchmark_sample

        record_ollama_benchmark_sample(
            data_dir=temp_data_dir,
            model="mistral",
            role_name="coder",
            task_kind="coding",
            parameters={"temperature": 0.5, "top_p": 0.9},
            success=True,
            quality_gate_passed=True,
            latency_ms=500,
            tokens_total=100,
            response_text="def add(a, b): return a + b",
        )

        result = get_best_parameters_for_model(data_dir=temp_data_dir, model="mistral", role_name="coder")
        assert result is not None
        assert result["parameters"] == {"temperature": 0.5, "top_p": 0.9}


class TestOllamaBenchmarkService:
    """Tests for ollama_benchmark_service.py."""

    @pytest.fixture
    def temp_data_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_service_initialization(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        assert service.data_dir == temp_data_dir

    def test_get_config(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        config = service.get_config()
        assert isinstance(config, dict)
        assert config.get("provider") == "ollama"

    def test_get_role_templates(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        roles = service.get_role_templates()
        assert isinstance(roles, dict)
        assert "planner" in roles

    def test_get_role_names(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        names = service.get_role_names()
        assert isinstance(names, list)
        assert "planner" in names
        assert "coder" in names

    @patch("agent.services.ollama_benchmark_service.discover_ollama_models")
    def test_discover_available_models(self, mock_discover, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        mock_discover.return_value = [
            {"model": "llama3", "name": "llama3:latest"},
            {"model": "mistral", "name": "mistral:latest"},
        ]

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        models = service.discover_available_models()
        assert len(models) == 2
        assert models[0]["model"] == "llama3"

    @patch("agent.ollama_benchmark.requests.get")
    def test_discover_models_network_failure_returns_empty(self, mock_get, temp_data_dir):
        from agent.ollama_benchmark import discover_ollama_models

        mock_get.side_effect = requests.RequestException("boom")
        assert discover_ollama_models("http://ollama:11434") == []

    def test_get_results(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        rows, db = service.get_results(role_name="planner", top_n=5)
        assert isinstance(rows, list)
        assert isinstance(db, dict)

    @patch("agent.services.ollama_benchmark_service._generate_text")
    def test_run_single_benchmark_success(self, mock_generate, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        mock_result = {
            "choices": [
                {
                    "message": {
                        "content": "```python\ndef add(a, b):\n    return a + b\n```\nAdd pytest assertions for edge cases."
                    }
                }
            ]
        }
        mock_generate.return_value = mock_result

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        result = service.run_single_benchmark(
            model="llama3",
            role_name="coder",
            task_kind="coding",
            prompt="Write a function",
            parameters={"temperature": 0.7, "top_p": 0.9, "top_k": 40},
        )
        assert "success" in result
        assert result["success"] is True
        assert "latency_ms" in result
        assert result["model"] == "llama3"
        assert result["quality_score"] >= 55.0

    def test_run_role_benchmark(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        roles = service.get_role_templates()
        role_cfg = roles.get("planner")
        assert role_cfg is not None
        assert "test_prompts" in role_cfg
        assert len(role_cfg["test_prompts"]) > 0

    def test_get_recommendation(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        result = service.get_recommendation(role_name="planner")
        assert "available" in result

    def test_get_model_comparison(self, temp_data_dir):
        from agent.services.ollama_benchmark_service import OllamaBenchmarkService

        service = OllamaBenchmarkService(data_dir=temp_data_dir)
        result = service.get_model_comparison(role_name="coder", top_n=5)
        assert "rankings" in result
        assert "total_models_benchmarked" in result


class TestOllamaBenchmarkConstants:
    """Tests for constants and edge cases."""

    def test_task_kinds_defined(self):
        from agent.ollama_benchmark import OLLAMA_BENCH_TASK_KINDS

        assert isinstance(OLLAMA_BENCH_TASK_KINDS, set)
        assert len(OLLAMA_BENCH_TASK_KINDS) > 0
        assert "planning" in OLLAMA_BENCH_TASK_KINDS
        assert "coding" in OLLAMA_BENCH_TASK_KINDS
        assert "research" in OLLAMA_BENCH_TASK_KINDS

    def test_all_scrum_roles_have_valid_task_kinds(self):
        from agent.ollama_benchmark import SCRUM_ROLE_TEMPLATES, OLLAMA_BENCH_TASK_KINDS

        for role_name, role_cfg in SCRUM_ROLE_TEMPLATES.items():
            assert role_cfg["task_kind"] in OLLAMA_BENCH_TASK_KINDS, f"Role {role_name} has invalid task_kind"

    def test_parameter_key_generation(self):
        from agent.ollama_benchmark import _parameters_key

        params1 = {"temperature": 0.7, "top_p": 0.9}
        params2 = {"top_p": 0.9, "temperature": 0.7}
        assert _parameters_key(params1) == _parameters_key(params2)

        params3 = {"temperature": 0.8, "top_p": 0.9}
        assert _parameters_key(params1) != _parameters_key(params3)
