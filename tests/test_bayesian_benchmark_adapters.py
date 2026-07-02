"""
Tests for agent/services/bayesian_benchmark_adapters.py.

Verifies that adapters extract evidence whose count matches the sample
count used by corresponding recommend_* functions, and that all three
benchmark systems' sample shapes are handled correctly.

Covers BAYES-009 and the adapter portion of BAYES-015.
"""
import pytest

from agent.services.bayesian_benchmark_adapters import (
    extract_hub_evidence,
    extract_ollama_evidence,
    extract_llm_benchmark_evidence,
    enrich_hub_score_with_bayes,
    enrich_ollama_score_with_bayes,
    enrich_llm_benchmark_score_with_bayes,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _hub_sample(success: bool = True, quality_passed: bool = True, role: str = "planner", task: str = "planning") -> dict:
    return {
        "ts": 1700000000,
        "role_name": role,
        "task_kind": task,
        "success": success,
        "quality_passed": quality_passed,
        "latency_ms": 400,
        "tokens_total": 200,
        "cost_units": 0.002,
    }


def _ollama_sample(success: bool = True, quality_passed: bool = True, params: dict | None = None) -> dict:
    s = {
        "ts": 1700000000,
        "role_name": "coder",
        "task_kind": "coding",
        "success": success,
        "quality_passed": quality_passed,
        "latency_ms": 800,
        "tokens_total": 300,
        "cost_units": 0.0,
        "response_length": 512,
    }
    if params is not None:
        s["parameters"] = params
    return s


def _llm_sample(success: bool = True, quality_passed: bool = True, role: str = "researcher", template: str = "default") -> dict:
    return {
        "ts": 1700000000,
        "success": success,
        "quality_passed": quality_passed,
        "latency_ms": 600,
        "tokens_total": 250,
        "cost_units": 0.003,
        "context": {"role_name": role, "template_name": template},
    }


def _hub_bucket(samples: list[dict]) -> dict:
    return {"samples": samples, "total": len(samples)}


# ── extract_hub_evidence ───────────────────────────────────────────────────────

class TestExtractHubEvidence:
    def test_basic_extraction(self):
        bucket = _hub_bucket([_hub_sample(), _hub_sample(success=False, quality_passed=False)])
        ev = extract_hub_evidence(bucket, provider="lmstudio", model="llama3")
        assert len(ev) == 2

    def test_source_and_provider_set(self):
        bucket = _hub_bucket([_hub_sample()])
        ev = extract_hub_evidence(bucket, provider="lmstudio", model="llama3")
        assert ev[0]["source"] == "hub"
        assert ev[0]["provider"] == "lmstudio"
        assert ev[0]["model"] == "llama3"

    def test_role_filter_reduces_count(self):
        bucket = _hub_bucket([
            _hub_sample(role="planner"),
            _hub_sample(role="coder"),
            _hub_sample(role="planner"),
        ])
        ev = extract_hub_evidence(bucket, role_name_filter="planner")
        assert len(ev) == 2

    def test_task_kind_filter(self):
        bucket = _hub_bucket([
            _hub_sample(task="planning"),
            _hub_sample(task="coding"),
        ])
        ev = extract_hub_evidence(bucket, task_kind_filter="planning")
        assert len(ev) == 1

    def test_combined_filter_matches_recommend_logic(self):
        samples = [
            _hub_sample(role="planner", task="planning"),
            _hub_sample(role="planner", task="coding"),
            _hub_sample(role="coder", task="planning"),
        ]
        bucket = _hub_bucket(samples)
        # Only planner+planning should match
        ev = extract_hub_evidence(bucket, role_name_filter="planner", task_kind_filter="planning")
        assert len(ev) == 1

    def test_malformed_samples_skipped(self):
        bucket = {"samples": [None, "bad", 42, _hub_sample()]}
        ev = extract_hub_evidence(bucket)
        assert len(ev) == 1

    def test_empty_bucket_returns_empty(self):
        assert extract_hub_evidence({}) == []
        assert extract_hub_evidence({"samples": []}) == []
        assert extract_hub_evidence(None) == []  # type: ignore[arg-type]

    def test_quality_passed_preserved(self):
        bucket = _hub_bucket([_hub_sample(quality_passed=False)])
        ev = extract_hub_evidence(bucket)
        assert ev[0]["quality_passed"] is False

    def test_evidence_count_matches_filtered_sample_count(self):
        samples = [_hub_sample(role="planner") for _ in range(7)] + [_hub_sample(role="coder") for _ in range(3)]
        bucket = _hub_bucket(samples)
        ev = extract_hub_evidence(bucket, role_name_filter="planner")
        assert len(ev) == 7


# ── extract_ollama_evidence ───────────────────────────────────────────────────

class TestExtractOllamaEvidence:
    def test_basic_extraction(self):
        bucket = _hub_bucket([_ollama_sample(), _ollama_sample(success=False, quality_passed=False)])
        ev = extract_ollama_evidence(bucket, model="qwen2.5-coder:7b")
        assert len(ev) == 2

    def test_source_set_to_ollama(self):
        bucket = _hub_bucket([_ollama_sample()])
        ev = extract_ollama_evidence(bucket, model="mistral:7b")
        assert ev[0]["source"] == "ollama"
        assert ev[0]["provider"] is None

    def test_parameters_preserved(self):
        params = {"temperature": 0.7, "top_k": 40, "top_p": 0.9}
        bucket = _hub_bucket([_ollama_sample(params=params)])
        ev = extract_ollama_evidence(bucket)
        assert ev[0]["parameters"] == params

    def test_parameter_filter(self):
        samples = [
            _ollama_sample(params={"temperature": 0.7}),
            _ollama_sample(params={"temperature": 0.3}),
            _ollama_sample(params={"temperature": 0.7}),
        ]
        bucket = _hub_bucket(samples)
        ev = extract_ollama_evidence(bucket, parameter_filter={"temperature": 0.7})
        assert len(ev) == 2

    def test_parameter_filter_matches_recommend_logic(self):
        samples = [
            _ollama_sample(params={"temperature": 0.7, "top_k": 40}),
            _ollama_sample(params={"temperature": 0.7, "top_k": 80}),
            _ollama_sample(params={"temperature": 0.3, "top_k": 40}),
        ]
        bucket = _hub_bucket(samples)
        ev = extract_ollama_evidence(bucket, parameter_filter={"temperature": 0.7, "top_k": 40})
        assert len(ev) == 1

    def test_role_filter(self):
        samples = [
            _ollama_sample(),  # role=coder
            {**_ollama_sample(), "role_name": "planner"},
        ]
        bucket = _hub_bucket(samples)
        ev = extract_ollama_evidence(bucket, role_name_filter="coder")
        assert len(ev) == 1


# ── extract_llm_benchmark_evidence ────────────────────────────────────────────

class TestExtractLlmBenchmarkEvidence:
    def test_basic_extraction(self):
        bucket = _hub_bucket([_llm_sample(), _llm_sample(success=False, quality_passed=False)])
        ev = extract_llm_benchmark_evidence(bucket, provider="openai", model="gpt-4")
        assert len(ev) == 2

    def test_source_set(self):
        bucket = _hub_bucket([_llm_sample()])
        ev = extract_llm_benchmark_evidence(bucket)
        assert ev[0]["source"] == "llm_benchmark"

    def test_role_filter_from_context(self):
        samples = [
            _llm_sample(role="researcher"),
            _llm_sample(role="planner"),
            _llm_sample(role="researcher"),
        ]
        bucket = _hub_bucket(samples)
        ev = extract_llm_benchmark_evidence(bucket, role_name_filter="researcher")
        assert len(ev) == 2

    def test_template_filter(self):
        samples = [
            _llm_sample(template="default"),
            _llm_sample(template="strict"),
        ]
        bucket = _hub_bucket(samples)
        ev = extract_llm_benchmark_evidence(bucket, template_name_filter="default")
        assert len(ev) == 1

    def test_role_name_flattened_from_context(self):
        bucket = _hub_bucket([_llm_sample(role="analyst")])
        ev = extract_llm_benchmark_evidence(bucket)
        assert ev[0]["role_name"] == "analyst"

    def test_evidence_count_matches_recommend_logic(self):
        samples = [_llm_sample(role="researcher") for _ in range(5)] + [_llm_sample(role="planner") for _ in range(3)]
        bucket = _hub_bucket(samples)
        ev = extract_llm_benchmark_evidence(bucket, role_name_filter="researcher")
        assert len(ev) == 5


# ── enrich_*_score_with_bayes ────────────────────────────────────────────────

class TestEnrichScoreWithBayes:
    def _score(self) -> dict:
        return {"total": 10, "success_rate": 0.8, "suitability_score": 75.0}

    def test_enrich_hub_adds_bayesian_estimate(self):
        score = self._score()
        samples = [_hub_sample() for _ in range(5)]
        enriched = enrich_hub_score_with_bayes(score, samples, provider="lmstudio", model="llama3")
        assert "bayesian_estimate" in enriched
        assert "posterior_success_probability" in enriched["bayesian_estimate"]

    def test_enrich_does_not_mutate_original(self):
        score = self._score()
        samples = [_hub_sample()]
        enrich_hub_score_with_bayes(score, samples)
        assert "bayesian_estimate" not in score

    def test_enrich_ollama_source_set(self):
        score = self._score()
        enriched = enrich_ollama_score_with_bayes(score, [_ollama_sample()], model="llama")
        assert enriched["bayesian_estimate"]["estimate_status"] in ("active", "prior_only")

    def test_enrich_llm_benchmark(self):
        score = self._score()
        enriched = enrich_llm_benchmark_score_with_bayes(score, [_llm_sample()], provider="openai", model="gpt-4")
        assert "bayesian_estimate" in enriched

    def test_existing_score_keys_preserved(self):
        score = self._score()
        enriched = enrich_hub_score_with_bayes(score, [], provider="x", model="y")
        for key in score:
            assert key in enriched
