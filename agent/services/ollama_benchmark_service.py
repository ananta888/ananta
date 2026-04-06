from __future__ import annotations

import time
from typing import Any

from flask import current_app

from agent.ollama_benchmark import (
    SCRUM_ROLE_TEMPLATES,
    discover_ollama_models,
    get_scrum_role_templates,
    load_ollama_bench_config,
    ollama_benchmark_rows,
    record_ollama_benchmark_sample,
    recommend_ollama_model,
    recommend_ollama_models,
    update_ollama_benchmark_run_timestamp,
)
from agent.llm_integration import extract_llm_text_and_usage, generate_text as _generate_text


class OllamaBenchmarkService:
    """Service for running Ollama model benchmarks with dynamic discovery and parameter variation."""

    def __init__(self, data_dir: str | None = None):
        self._data_dir = data_dir

    @property
    def data_dir(self) -> str:
        if self._data_dir:
            return self._data_dir
        try:
            return current_app.config.get("DATA_DIR") or "data"
        except RuntimeError:
            return "data"

    def get_config(self) -> dict[str, Any]:
        return load_ollama_bench_config(self.data_dir)

    def get_role_templates(self) -> dict[str, dict[str, Any]]:
        return get_scrum_role_templates()

    def get_role_names(self) -> list[str]:
        return list(SCRUM_ROLE_TEMPLATES.keys())

    def discover_available_models(self) -> list[dict[str, Any]]:
        cfg = self.get_config()
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        timeout = int(cfg.get("timeout", 10))
        return discover_ollama_models(ollama_url, timeout=timeout)

    def get_results(
        self, role_name: str | None = None, model_name: str | None = None, top_n: int | None = None
    ) -> tuple[list[dict], dict]:
        return ollama_benchmark_rows(
            data_dir=self.data_dir,
            role_name=role_name,
            model_name=model_name,
            top_n=top_n,
        )

    def run_single_benchmark(
        self,
        *,
        model: str,
        role_name: str,
        task_kind: str,
        prompt: str,
        parameters: dict[str, Any] | None = None,
        base_url: str | None = None,
        timeout: int = 120,
    ) -> dict[str, Any]:
        cfg = self.get_config()
        effective_base_url = base_url or cfg.get("ollama_url", "http://localhost:11434")
        effective_params = parameters or {}
        temperature = effective_params.get("temperature", 0.7)
        top_p = effective_params.get("top_p", 0.9)
        top_k = effective_params.get("top_k", 40)
        start_time = time.time()
        try:
            result = _generate_text(
                prompt=prompt,
                provider="ollama",
                model=model,
                base_url=effective_base_url,
                temperature=temperature,
                timeout=timeout,
                options={
                    "top_p": top_p,
                    "top_k": top_k,
                },
            )
            latency_ms = int((time.time() - start_time) * 1000)
            text, usage, _ = extract_llm_text_and_usage(result)
            tokens_total = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            success = len(text.strip()) > 0
            quality_passed = success and len(text) > 50
            record_ollama_benchmark_sample(
                data_dir=self.data_dir,
                model=model,
                role_name=role_name,
                task_kind=task_kind,
                parameters=effective_params,
                success=success,
                quality_gate_passed=quality_passed,
                latency_ms=latency_ms,
                tokens_total=tokens_total,
                cost_units=0.0,
                response_text=text,
            )
            return {
                "success": success,
                "quality_passed": quality_passed,
                "latency_ms": latency_ms,
                "tokens_total": tokens_total,
                "text_length": len(text),
                "model": model,
                "role_name": role_name,
                "task_kind": task_kind,
                "parameters": effective_params,
            }
        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            record_ollama_benchmark_sample(
                data_dir=self.data_dir,
                model=model,
                role_name=role_name,
                task_kind=task_kind,
                parameters=effective_params,
                success=False,
                quality_gate_passed=False,
                latency_ms=latency_ms,
                tokens_total=0,
                cost_units=0.0,
                response_text=None,
            )
            return {
                "success": False,
                "quality_passed": False,
                "latency_ms": latency_ms,
                "tokens_total": 0,
                "error": str(exc),
                "model": model,
                "role_name": role_name,
                "task_kind": task_kind,
                "parameters": effective_params,
            }

    def run_role_benchmark(
        self,
        *,
        model: str,
        role_name: str,
        parameters: dict[str, Any] | None = None,
        base_url: str | None = None,
        timeout: int = 120,
    ) -> list[dict[str, Any]]:
        cfg = self.get_config()
        role_templates = cfg.get("role_benchmarks") or SCRUM_ROLE_TEMPLATES
        role_cfg = role_templates.get(role_name) or SCRUM_ROLE_TEMPLATES.get(role_name)
        if not role_cfg:
            return [{"error": f"Unknown role: {role_name}"}]
        task_kind = role_cfg.get("task_kind", "analysis")
        test_prompts = role_cfg.get("test_prompts", [])
        results = []
        for prompt in test_prompts:
            result = self.run_single_benchmark(
                model=model,
                role_name=role_name,
                task_kind=task_kind,
                prompt=prompt,
                parameters=parameters,
                base_url=base_url,
                timeout=timeout,
            )
            results.append(result)
        return results

    def run_parameter_variation_benchmark(
        self,
        *,
        model: str,
        role_name: str,
        base_url: str | None = None,
        timeout: int = 120,
    ) -> list[dict[str, Any]]:
        cfg = self.get_config()
        variations = cfg.get("parameter_variations", {})
        temperature_options = variations.get("temperature", [0.1, 0.5, 0.8, 1.0])
        top_p_options = variations.get("top_p", [0.5, 0.9, 0.95, 1.0])
        top_k_options = variations.get("top_k", [20, 40, 80])
        results = []
        for temp in temperature_options:
            for top_p in top_p_options:
                for top_k in top_k_options:
                    params = {"temperature": temp, "top_p": top_p, "top_k": top_k}
                    result = self.run_role_benchmark(
                        model=model,
                        role_name=role_name,
                        parameters=params,
                        base_url=base_url,
                        timeout=timeout,
                    )
                    for r in result:
                        r["parameters"] = params
                    results.extend(result)
        return results

    def run_full_benchmark(
        self,
        *,
        models: list[str] | None = None,
        roles: list[str] | None = None,
        parameter_variations: bool = False,
        max_execution_minutes: int = 60,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        cfg = self.get_config()
        if not cfg.get("enabled", True):
            return {"status": "disabled", "message": "Ollama benchmark is disabled"}
        if not models:
            discovered = self.discover_available_models()
            models = [m.get("model") or m.get("name", "") for m in discovered if m.get("model") or m.get("name")]
        if not models:
            return {"status": "error", "message": "No models available. Make sure Ollama is running."}
        role_templates = cfg.get("role_benchmarks") or SCRUM_ROLE_TEMPLATES
        if not roles:
            roles = list(role_templates.keys())
        all_results = []
        start_time = time.time()
        max_duration = max_execution_minutes * 60
        summary = {"total_tests": 0, "successful": 0, "failed": 0, "models_tested": 0, "roles_tested": 0}
        for model in models:
            if time.time() - start_time > max_duration:
                all_results.append({"model": model, "status": "skipped", "reason": "timeout"})
                continue
            for role in roles:
                if time.time() - start_time > max_duration:
                    break
                if parameter_variations:
                    results = self.run_parameter_variation_benchmark(
                        model=model,
                        role_name=role,
                        base_url=base_url,
                        timeout=120,
                    )
                else:
                    results = self.run_role_benchmark(
                        model=model,
                        role_name=role,
                        base_url=base_url,
                        timeout=120,
                    )
                for r in results:
                    summary["total_tests"] += 1
                    if r.get("success"):
                        summary["successful"] += 1
                    else:
                        summary["failed"] += 1
                all_results.extend(results)
            summary["models_tested"] += 1
            summary["roles_tested"] = len(roles)
        update_ollama_benchmark_run_timestamp(self.data_dir)
        return {
            "status": "completed",
            "duration_seconds": int(time.time() - start_time),
            "models_tested": summary["models_tested"],
            "roles_tested": summary["roles_tested"],
            "summary": summary,
            "results": all_results,
        }

    def get_recommendation(
        self,
        *,
        role_name: str | None = None,
        task_kind: str | None = None,
        preferred_parameters: dict[str, Any] | None = None,
        min_samples: int = 1,
    ) -> dict[str, Any]:
        recommendation = recommend_ollama_model(
            data_dir=self.data_dir,
            role_name=role_name,
            task_kind=task_kind,
            min_samples=min_samples,
            preferred_parameters=preferred_parameters,
        )
        if not recommendation:
            return {
                "available": False,
                "message": "No benchmark data available for this role/model combination",
            }
        best_params = get_best_parameters_for_model(
            data_dir=self.data_dir,
            model=recommendation.get("model", ""),
            role_name=role_name,
        )
        return {
            "available": True,
            "recommended": {
                "model": recommendation.get("model"),
                "role": recommendation.get("role_name"),
                "score": recommendation.get("score", {}),
                "best_parameters": best_params.get("parameters") if best_params else None,
            },
        }

    def get_model_comparison(
        self,
        *,
        role_name: str | None = None,
        task_kind: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        rows, db = self.get_results(role_name=role_name, top_n=top_n)
        available_models = self.discover_available_models()
        available_names = {m.get("model") or m.get("name", "") for m in available_models}
        return {
            "role_name": role_name,
            "task_kind": task_kind,
            "total_models_benchmarked": len(db.get("models", {})),
            "available_models_in_ollama": len(available_models),
            "models_online": list(available_names),
            "rankings": rows,
        }


ollama_benchmark_service = OllamaBenchmarkService()


def get_ollama_benchmark_service(data_dir: str | None = None) -> OllamaBenchmarkService:
    if data_dir:
        return OllamaBenchmarkService(data_dir)
    return ollama_benchmark_service
