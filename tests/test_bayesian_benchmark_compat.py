"""
Backward-compatibility tests for existing benchmark payloads after Bayesian
integration.

Verifies that:
- All previously-existing keys in recommend_* and *_rows return values are
  still present when include_bayesian=False (default).
- Bayesian keys appear only when include_bayesian=True.
- Malformed or legacy sample dicts do not crash existing consumers.
- No existing benchmark JSON fixture needs destructive migration.

Covers BAYES-015.
"""
import json
import os
import tempfile
import time

import pytest

import agent.hub_benchmark as hub
import agent.ollama_benchmark as ollama
import agent.llm_benchmarks as llm


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _hub_db(provider: str = "lmstudio", model: str = "llama3", n_samples: int = 5) -> dict:
    now = int(time.time())
    samples = [
        {
            "ts": now - i * 60,
            "role_name": "planner",
            "task_kind": "planning",
            "success": True,
            "quality_passed": True,
            "latency_ms": 400,
            "tokens_total": 200,
            "cost_units": 0.002,
        }
        for i in range(n_samples)
    ]
    bucket = {
        "total": n_samples,
        "success": n_samples,
        "failed": 0,
        "quality_pass": n_samples,
        "quality_fail": 0,
        "latency_ms_total": 400 * n_samples,
        "tokens_total": 200 * n_samples,
        "cost_units_total": 0.002 * n_samples,
        "last_seen": now,
        "samples": samples,
    }
    return {
        "models": {
            f"{provider}:{model}": {
                "provider": provider,
                "model": model,
                "overall": bucket,
                "roles": {"planner": bucket},
            }
        },
        "updated_at": now,
    }


def _ollama_db(model: str = "llama3.2:3b", n_samples: int = 4) -> dict:
    now = int(time.time())
    samples = [
        {
            "ts": now - i * 60,
            "role_name": "coder",
            "task_kind": "coding",
            "success": True,
            "quality_passed": True,
            "latency_ms": 800,
            "tokens_total": 300,
            "cost_units": 0.0,
            "parameters": {"temperature": 0.7},
        }
        for i in range(n_samples)
    ]
    bucket = {
        "total": n_samples,
        "success": n_samples,
        "failed": 0,
        "quality_pass": n_samples,
        "quality_fail": 0,
        "latency_ms_total": 800 * n_samples,
        "tokens_total": 300 * n_samples,
        "cost_units_total": 0.0,
        "last_seen": now,
        "samples": samples,
    }
    return {
        "models": {
            model: {
                "model": model,
                "overall": bucket,
                "roles": {"coder": bucket},
                "parameters": {},
            }
        },
        "updated_at": now,
    }


def _llm_db(provider: str = "openai", model: str = "gpt-4o-mini", n_samples: int = 5) -> dict:
    now = int(time.time())
    samples = [
        {
            "ts": now - i * 60,
            "task_kind": "analysis",
            "success": True,
            "quality_passed": True,
            "latency_ms": 500,
            "tokens_total": 200,
            "cost_units": 0.001,
            "context": {"role_name": "analyst", "template_name": "default"},
        }
        for i in range(n_samples)
    ]
    bucket = {
        "total": n_samples,
        "success": n_samples,
        "failed": 0,
        "quality_pass": n_samples,
        "quality_fail": 0,
        "latency_ms_total": 500 * n_samples,
        "tokens_total": 200 * n_samples,
        "cost_units_total": 0.001 * n_samples,
        "last_seen": now,
        "samples": samples,
    }
    return {
        "models": {
            f"{provider}:{model}": {
                "provider": provider,
                "model": model,
                "overall": bucket,
                "task_kinds": {"analysis": bucket},
            }
        },
        "updated_at": now,
    }


# ── Hub backward compatibility ────────────────────────────────────────────────

class TestHubBenchmarkCompat:
    @pytest.fixture()
    def data_dir(self, tmp_path):
        db = _hub_db()
        _write_json(str(tmp_path / "hub_benchmark_results.json"), db)
        return str(tmp_path)

    def test_recommend_hub_models_default_keys_present(self, data_dir):
        results = hub.recommend_hub_models(data_dir=data_dir, min_samples=1)
        assert results, "expected at least one recommendation"
        candidate = results[0]
        for key in ("provider", "model", "sample_count", "score"):
            assert key in candidate, f"missing key: {key}"

    def test_recommend_hub_models_no_bayesian_by_default(self, data_dir):
        results = hub.recommend_hub_models(data_dir=data_dir, min_samples=1)
        assert "bayesian_estimate" not in results[0]
        assert "low_confidence" not in results[0]

    def test_recommend_hub_models_with_bayesian_adds_keys(self, data_dir):
        results = hub.recommend_hub_models(data_dir=data_dir, min_samples=1, include_bayesian=True)
        assert results
        c = results[0]
        assert "bayesian_estimate" in c
        assert "low_confidence" in c
        assert "estimated_attempts_for_50_percent" in c
        assert "estimated_attempts_for_80_percent" in c
        assert "estimated_attempts_for_95_percent" in c

    def test_recommend_hub_models_existing_score_keys_unchanged(self, data_dir):
        without = hub.recommend_hub_models(data_dir=data_dir, min_samples=1)
        with_b = hub.recommend_hub_models(data_dir=data_dir, min_samples=1, include_bayesian=True)
        # score dict must have same keys in both cases
        assert set(without[0]["score"].keys()) == set(with_b[0]["score"].keys())

    def test_recommend_hub_model_singular_with_bayesian(self, data_dir):
        result = hub.recommend_hub_model(data_dir=data_dir, min_samples=1, include_bayesian=True)
        assert result is not None
        assert "bayesian_estimate" in result
        assert result["selection_source"] == "hub_benchmark"

    def test_hub_benchmark_rows_no_bayesian_by_default(self, data_dir):
        rows, _ = hub.hub_benchmark_rows(data_dir=data_dir)
        assert rows
        assert "bayesian_estimate" not in rows[0]

    def test_hub_benchmark_rows_with_bayesian(self, data_dir):
        rows, _ = hub.hub_benchmark_rows(data_dir=data_dir, include_bayesian=True)
        assert rows
        assert "bayesian_estimate" in rows[0]

    def test_legacy_malformed_samples_do_not_crash(self, data_dir):
        """Samples without quality_passed are handled as legacy — success only."""
        db = _hub_db()
        for entry in db["models"].values():
            for sample in entry["overall"]["samples"]:
                del sample["quality_passed"]
        _write_json(os.path.join(data_dir, "hub_benchmark_results.json"), db)

        results = hub.recommend_hub_models(data_dir=data_dir, min_samples=1, include_bayesian=True)
        assert results
        b = results[0]["bayesian_estimate"]
        assert b["primary_signal"] == "success"
        assert b["posterior_quality_probability"] is None


# ── Ollama backward compatibility ──────────────────────────────────────────────

class TestOllamaBenchmarkCompat:
    @pytest.fixture()
    def data_dir(self, tmp_path):
        db = _ollama_db()
        _write_json(str(tmp_path / "ollama_benchmark_results.json"), db)
        return str(tmp_path)

    def test_recommend_ollama_models_default_keys_present(self, data_dir):
        results = ollama.recommend_ollama_models(data_dir=data_dir, min_samples=1)
        assert results
        for key in ("model", "sample_count", "score", "parameter_performance"):
            assert key in results[0], f"missing key: {key}"

    def test_recommend_ollama_models_no_bayesian_by_default(self, data_dir):
        results = ollama.recommend_ollama_models(data_dir=data_dir, min_samples=1)
        assert "bayesian_estimate" not in results[0]

    def test_recommend_ollama_models_with_bayesian(self, data_dir):
        results = ollama.recommend_ollama_models(data_dir=data_dir, min_samples=1, include_bayesian=True)
        assert results
        c = results[0]
        assert "bayesian_estimate" in c
        assert "estimated_attempts_for_80_percent" in c

    def test_ollama_rows_no_bayesian_by_default(self, data_dir):
        rows, _ = ollama.ollama_benchmark_rows(data_dir=data_dir)
        assert rows
        assert "bayesian_estimate" not in rows[0]

    def test_ollama_rows_with_bayesian(self, data_dir):
        rows, _ = ollama.ollama_benchmark_rows(data_dir=data_dir, include_bayesian=True)
        assert rows
        assert "bayesian_estimate" in rows[0]

    def test_parameter_performance_key_still_present_with_bayesian(self, data_dir):
        results = ollama.recommend_ollama_models(data_dir=data_dir, min_samples=1, include_bayesian=True)
        assert "parameter_performance" in results[0]


# ── llm_benchmarks backward compatibility ────────────────────────────────────

class TestLlmBenchmarkCompat:
    @pytest.fixture()
    def data_dir(self, tmp_path):
        db = _llm_db()
        _write_json(str(tmp_path / "llm_model_benchmarks.json"), db)
        return str(tmp_path)

    def test_recommend_models_for_context_default_keys(self, data_dir):
        results = llm.recommend_models_for_context(
            data_dir=data_dir, task_kind="analysis", min_samples=1
        )
        assert results
        for key in ("provider", "model", "task_kind", "sample_count", "score"):
            assert key in results[0], f"missing key: {key}"

    def test_recommend_models_for_context_no_bayesian_by_default(self, data_dir):
        results = llm.recommend_models_for_context(
            data_dir=data_dir, task_kind="analysis", min_samples=1
        )
        assert "bayesian_estimate" not in results[0]

    def test_recommend_models_for_context_with_bayesian(self, data_dir):
        results = llm.recommend_models_for_context(
            data_dir=data_dir, task_kind="analysis", min_samples=1, include_bayesian=True
        )
        assert results
        assert "bayesian_estimate" in results[0]

    def test_recommend_model_for_context_singular(self, data_dir):
        result = llm.recommend_model_for_context(
            data_dir=data_dir, task_kind="analysis", min_samples=1, include_bayesian=True
        )
        assert result is not None
        assert "bayesian_estimate" in result
        assert result["selection_source"] == "benchmark_context_learning"

    def test_benchmark_rows_no_bayesian_by_default(self, data_dir):
        rows, _ = llm.benchmark_rows(data_dir=data_dir)
        assert rows
        assert "bayesian_estimate" not in rows[0]

    def test_benchmark_rows_with_bayesian(self, data_dir):
        rows, _ = llm.benchmark_rows(data_dir=data_dir, include_bayesian=True)
        assert rows
        assert "bayesian_estimate" in rows[0]

    def test_existing_score_keys_unchanged_with_bayesian(self, data_dir):
        without = llm.recommend_models_for_context(data_dir=data_dir, task_kind="analysis", min_samples=1)
        with_b = llm.recommend_models_for_context(data_dir=data_dir, task_kind="analysis", min_samples=1, include_bayesian=True)
        assert set(without[0]["score"].keys()) == set(with_b[0]["score"].keys())


# ── Cross-system: malformed payloads ──────────────────────────────────────────

class TestMalformedPayloadHandling:
    def test_hub_null_samples_in_db(self, tmp_path):
        db = _hub_db()
        for entry in db["models"].values():
            entry["overall"]["samples"] = None
        _write_json(str(tmp_path / "hub_benchmark_results.json"), db)
        # Should not crash, just skip malformed samples
        rows, _ = hub.hub_benchmark_rows(data_dir=str(tmp_path), include_bayesian=True)
        for row in rows:
            b = row.get("bayesian_estimate")
            if b:
                assert b["estimate_status"] in ("prior_only", "active")

    def test_ollama_empty_models(self, tmp_path):
        db = {"models": {}, "updated_at": int(time.time())}
        _write_json(str(tmp_path / "ollama_benchmark_results.json"), db)
        results = ollama.recommend_ollama_models(data_dir=str(tmp_path), min_samples=1, include_bayesian=True)
        assert results == []

    def test_hub_no_quality_field_legacy(self, tmp_path):
        """Legacy samples without quality_passed must not crash and use success signal."""
        db = _hub_db(n_samples=6)
        for entry in db["models"].values():
            for s in entry["overall"]["samples"]:
                s.pop("quality_passed", None)
            for role_data in entry.get("roles", {}).values():
                for s in role_data.get("samples", []):
                    s.pop("quality_passed", None)
        _write_json(str(tmp_path / "hub_benchmark_results.json"), db)

        results = hub.recommend_hub_models(data_dir=str(tmp_path), min_samples=1, include_bayesian=True)
        assert results
        b = results[0]["bayesian_estimate"]
        assert b["primary_signal"] == "success"
        assert b["posterior_quality_probability"] is None
