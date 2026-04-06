from __future__ import annotations

import time
from typing import Any

from flask import current_app

from agent.hub_benchmark import (
    check_auto_trigger_needed,
    hub_benchmark_rows,
    load_hub_benchmark_config,
    recommend_hub_model,
    record_hub_benchmark_sample,
    update_benchmark_run_timestamp,
)
from agent.llm_integration import extract_llm_text_and_usage, generate_text as _generate_text


class HubBenchmarkService:
    """Service for running hub model benchmarks and providing recommendations."""

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
        return load_hub_benchmark_config(self.data_dir)

    def get_results(self, role_name: str | None = None, top_n: int | None = None) -> tuple[list[dict], dict]:
        return hub_benchmark_rows(data_dir=self.data_dir, role_name=role_name, top_n=top_n)

    def should_auto_trigger(self) -> tuple[bool, str | None]:
        return check_auto_trigger_needed(self.data_dir)

    def run_single_benchmark(
        self,
        *,
        provider: str,
        model: str,
        role_name: str,
        task_kind: str,
        prompt: str,
        base_url: str | None = None,
        temperature: float | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        start_time = time.time()
        try:
            result = _generate_text(
                prompt=prompt,
                provider=provider,
                model=model,
                base_url=base_url,
                temperature=temperature,
                timeout=timeout,
            )
            latency_ms = int((time.time() - start_time) * 1000)
            text, usage, _ = extract_llm_text_and_usage(result)
            tokens_total = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            success = len(text.strip()) > 0
            quality_passed = success and len(text) > 50
            record_hub_benchmark_sample(
                data_dir=self.data_dir,
                provider=provider,
                model=model,
                role_name=role_name,
                task_kind=task_kind,
                success=success,
                quality_gate_passed=quality_passed,
                latency_ms=latency_ms,
                tokens_total=tokens_total,
                cost_units=0.0,
            )
            return {
                "success": success,
                "quality_passed": quality_passed,
                "latency_ms": latency_ms,
                "tokens_total": tokens_total,
                "text_length": len(text),
                "provider": provider,
                "model": model,
                "role_name": role_name,
            }
        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            record_hub_benchmark_sample(
                data_dir=self.data_dir,
                provider=provider,
                model=model,
                role_name=role_name,
                task_kind=task_kind,
                success=False,
                quality_gate_passed=False,
                latency_ms=latency_ms,
                tokens_total=0,
                cost_units=0.0,
            )
            return {
                "success": False,
                "quality_passed": False,
                "latency_ms": latency_ms,
                "tokens_total": 0,
                "error": str(exc),
                "provider": provider,
                "model": model,
                "role_name": role_name,
            }

    def run_full_benchmark(
        self,
        *,
        roles: list[str] | None = None,
        providers: list[str] | None = None,
        max_execution_minutes: int = 30,
    ) -> dict[str, Any]:
        cfg = self.get_config()
        if not cfg.get("enabled", True):
            return {"status": "disabled", "message": "Hub benchmark is disabled"}
        role_benchmarks = cfg.get("role_benchmarks", {})
        if roles:
            role_benchmarks = {k: v for k, v in role_benchmarks.items() if k in roles}
        all_providers = providers or list(cfg.get("providers", []))
        default_models = cfg.get("default_models", {})
        results = []
        start_time = time.time()
        max_duration = max_execution_minutes * 60

        for role_name, role_cfg in role_benchmarks.items():
            if time.time() - start_time > max_duration:
                results.append({"role_name": role_name, "status": "skipped", "reason": "timeout"})
                continue
            test_prompts = role_cfg.get("test_prompts", [])
            task_kind = role_cfg.get("task_kind", "analysis")
            for provider in all_providers:
                models = default_models.get(provider, [])
                if not models:
                    continue
                for model in models:
                    for prompt in test_prompts:
                        if time.time() - start_time > max_duration:
                            break
                        result = self.run_single_benchmark(
                            provider=provider,
                            model=model,
                            role_name=role_name,
                            task_kind=task_kind,
                            prompt=prompt,
                        )
                        results.append(result)

        update_benchmark_run_timestamp(self.data_dir)
        return {
            "status": "completed",
            "total_tests": len(results),
            "successful": sum(1 for r in results if r.get("success")),
            "failed": sum(1 for r in results if not r.get("success")),
            "duration_seconds": int(time.time() - start_time),
            "results": results,
        }

    def get_recommendation(
        self,
        *,
        role_name: str | None = None,
        task_kind: str | None = None,
        current_provider: str | None = None,
        current_model: str | None = None,
        min_samples: int = 2,
    ) -> dict[str, Any]:
        recommendation = recommend_hub_model(
            data_dir=self.data_dir,
            role_name=role_name,
            task_kind=task_kind,
            min_samples=min_samples,
        )
        if not recommendation:
            return {
                "available": False,
                "message": "No benchmark data available",
                "current": {"provider": current_provider, "model": current_model},
            }
        return {
            "available": True,
            "recommended": {
                "provider": recommendation.get("provider"),
                "model": recommendation.get("model"),
                "score": recommendation.get("score", {}),
            },
            "current": {"provider": current_provider, "model": current_model},
            "replacement_suggested": not (
                current_provider == recommendation.get("provider") and current_model == recommendation.get("model")
            ),
        }

    def get_hub_model_recommendation_for_task(
        self,
        *,
        task_kind: str,
        current_provider: str | None = None,
        current_model: str | None = None,
    ) -> dict[str, Any]:
        cfg = self.get_config()
        hub_cfg = cfg.get("hub_config", {})
        fixed = hub_cfg.get("fixed_model", {})
        if fixed.get("provider") and fixed.get("model"):
            return {
                "model_type": "fixed",
                "provider": fixed["provider"],
                "model": fixed["model"],
                "fallback_enabled": hub_cfg.get("fallback_enabled", True),
                "current": {"provider": current_provider, "model": current_model},
            }
        return self.get_recommendation(
            task_kind=task_kind,
            current_provider=current_provider,
            current_model=current_model,
        )


hub_benchmark_service = HubBenchmarkService()


def get_hub_benchmark_service(data_dir: str | None = None) -> HubBenchmarkService:
    if data_dir:
        return HubBenchmarkService(data_dir)
    return hub_benchmark_service
