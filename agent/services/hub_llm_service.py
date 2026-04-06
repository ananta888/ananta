from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from agent.hub_benchmark import HubBenchmarkDataError
from agent.llm_integration import extract_llm_text_and_usage, generate_text as _generate_text


class HubLLMService:
    """Shared hub-owned adapter for all outward-facing LLM entry points."""

    _COPILOT_ALLOWED_STRATEGY_MODES = {"planning_only", "planning_and_routing"}

    def generate_text(self, **kwargs) -> Any:
        return _generate_text(**kwargs)

    def generate_text_and_usage(self, **kwargs) -> tuple[str, dict[str, int], Any]:
        result = self.generate_text(**kwargs)
        text, usage = extract_llm_text_and_usage(result)
        return text, usage, result

    def _get_benchmark_recommended_model(self, task_kind: str | None = None) -> dict[str, Any] | None:
        if not has_app_context():
            return None

        data_dir = current_app.config.get("DATA_DIR") or "data"
        from agent.services.hub_benchmark_service import get_hub_benchmark_service

        service = get_hub_benchmark_service(data_dir)
        try:
            cfg = service.get_config()
            hub_cfg = cfg.get("hub_config", {})
            fixed = hub_cfg.get("fixed_model", {})
            if fixed.get("provider") and fixed.get("model"):
                return {"provider": fixed["provider"], "model": fixed["model"], "source": "benchmark_fixed_config"}
            recommendation = service.get_recommendation(task_kind=task_kind, min_samples=2)
        except HubBenchmarkDataError as exc:
            current_app.logger.warning("Hub benchmark recommendation unavailable: %s", exc)
            return None
        except (KeyError, TypeError, ValueError) as exc:
            current_app.logger.warning("Hub benchmark recommendation payload invalid: %s", exc)
            return None

        if recommendation.get("available"):
            return {
                "provider": recommendation["recommended"]["provider"],
                "model": recommendation["recommended"]["model"],
                "source": "benchmark_recommendation",
            }
        return None

    def resolve_copilot_config(
        self, overrides: dict[str, Any] | None = None, task_kind: str | None = None
    ) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        raw_cfg = dict(agent_cfg.get("hub_copilot") or {})
        if overrides:
            raw_cfg.update({key: value for key, value in overrides.items() if value is not None})

        llm_cfg = dict(agent_cfg.get("llm_config") or {})
        requested_provider = str(raw_cfg.get("provider") or "").strip().lower() or None
        requested_model = str(raw_cfg.get("model") or "").strip() or None
        requested_base_url = str(raw_cfg.get("base_url") or "").strip() or None
        requested_strategy_mode = (
            str(raw_cfg.get("strategy_mode") or "planning_only").strip().lower() or "planning_only"
        )
        strategy_mode = (
            requested_strategy_mode
            if requested_strategy_mode in self._COPILOT_ALLOWED_STRATEGY_MODES
            else "planning_only"
        )

        requested_temperature = raw_cfg.get("temperature")
        try:
            requested_temperature = float(requested_temperature) if requested_temperature is not None else None
        except (TypeError, ValueError):
            requested_temperature = None
        if requested_temperature is not None:
            requested_temperature = max(0.0, min(2.0, requested_temperature))

        effective_provider = (
            requested_provider
            or str(llm_cfg.get("provider") or "").strip().lower()
            or str(agent_cfg.get("default_provider") or "").strip().lower()
            or None
        )
        effective_model = (
            requested_model
            or str(llm_cfg.get("model") or "").strip()
            or str(agent_cfg.get("default_model") or "").strip()
            or None
        )
        benchmark_recommended = None
        if not effective_provider or not effective_model:
            benchmark_recommended = self._get_benchmark_recommended_model(task_kind=task_kind)
            if benchmark_recommended:
                if not effective_provider:
                    effective_provider = benchmark_recommended.get("provider")
                if not effective_model:
                    effective_model = benchmark_recommended.get("model")
        effective_base_url = requested_base_url or str(llm_cfg.get("base_url") or "").strip() or None
        effective_temperature = requested_temperature
        if effective_temperature is None:
            fallback_temperature = llm_cfg.get("temperature")
            try:
                effective_temperature = float(fallback_temperature) if fallback_temperature is not None else None
            except (TypeError, ValueError):
                effective_temperature = None
            if effective_temperature is not None:
                effective_temperature = max(0.0, min(2.0, effective_temperature))

        provider_source = (
            "hub_copilot.provider"
            if requested_provider
            else "agent_config.llm_config.provider"
            if llm_cfg.get("provider")
            else "agent_config.default_provider"
            if agent_cfg.get("default_provider")
            else ("hub_benchmark." + benchmark_recommended["source"] if benchmark_recommended else "unknown")
        )
        model_source = (
            "hub_copilot.model"
            if requested_model
            else "agent_config.llm_config.model"
            if llm_cfg.get("model")
            else "agent_config.default_model"
            if agent_cfg.get("default_model")
            else ("hub_benchmark." + benchmark_recommended["source"] if benchmark_recommended else "unknown")
        )
        base_url_source = (
            "hub_copilot.base_url"
            if requested_base_url
            else ("agent_config.llm_config.base_url" if llm_cfg.get("base_url") else None)
        )
        temperature_source = (
            "hub_copilot.temperature"
            if requested_temperature is not None
            else "agent_config.llm_config.temperature"
            if llm_cfg.get("temperature") is not None
            else None
        )

        enabled = bool(raw_cfg.get("enabled", False))
        supports_planning = strategy_mode in {"planning_only", "planning_and_routing"}
        supports_routing = strategy_mode == "planning_and_routing"
        active = enabled and bool(effective_provider) and bool(effective_model)

        return {
            "enabled": enabled,
            "active": active,
            "strategy_mode": strategy_mode,
            "supports_planning": supports_planning,
            "supports_routing": supports_routing,
            "requested": {
                "provider": requested_provider,
                "model": requested_model,
                "base_url": requested_base_url,
                "temperature": requested_temperature,
            },
            "effective": {
                "provider": effective_provider,
                "model": effective_model,
                "base_url": effective_base_url,
                "temperature": effective_temperature,
            },
            "source": {
                "provider": provider_source,
                "model": model_source,
                "base_url": base_url_source,
                "temperature": temperature_source,
            },
        }

    def plan_with_copilot(self, *, prompt: str, timeout: int | None = None) -> dict[str, Any]:
        config = self.resolve_copilot_config()
        if not config["enabled"]:
            raise RuntimeError("hub_copilot_disabled")
        if not config["supports_planning"]:
            raise RuntimeError("hub_copilot_planning_not_allowed")
        if not config["active"]:
            raise RuntimeError("hub_copilot_not_configured")

        text, usage, result = self.generate_text_and_usage(
            prompt=prompt,
            provider=config["effective"]["provider"],
            model=config["effective"]["model"],
            base_url=config["effective"]["base_url"],
            temperature=config["effective"]["temperature"],
            timeout=timeout,
        )
        return {"text": text, "usage": usage, "result": result, "config": config}

    def route_with_copilot(self, *, prompt: str, timeout: int | None = None) -> dict[str, Any]:
        config = self.resolve_copilot_config()
        if not config["enabled"]:
            raise RuntimeError("hub_copilot_disabled")
        if not config["supports_routing"]:
            raise RuntimeError("hub_copilot_routing_not_allowed")
        if not config["active"]:
            raise RuntimeError("hub_copilot_not_configured")

        text, usage, result = self.generate_text_and_usage(
            prompt=prompt,
            provider=config["effective"]["provider"],
            model=config["effective"]["model"],
            base_url=config["effective"]["base_url"],
            temperature=config["effective"]["temperature"],
            timeout=timeout,
        )
        return {"text": text, "usage": usage, "result": result, "config": config}


hub_llm_service = HubLLMService()


def get_hub_llm_service() -> HubLLMService:
    return hub_llm_service


def generate_text(**kwargs) -> Any:
    return get_hub_llm_service().generate_text(**kwargs)


def generate_text_and_usage(**kwargs) -> tuple[str, dict[str, int], Any]:
    return get_hub_llm_service().generate_text_and_usage(**kwargs)


def resolve_copilot_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_hub_llm_service().resolve_copilot_config(overrides=overrides)
