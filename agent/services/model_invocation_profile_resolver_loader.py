"""Profile-resolver loading for ModelInvocationService.

``ModelProfileResolverLoader`` builds the ``ModelProfileResolver`` from the
configured profile and routing files once and caches it on the loader
instance. The shared ``ModelInvocationService`` default instance owns one
loader, so the cache stays process-wide in production while every
explicitly constructed service (for example in tests) gets its own cache.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from agent.services.model_invocation_errors import ModelRoutingConfigurationError
from agent.services.model_invocation_routing_policy import (
    model_routing_configuration_requested,
)

# Shares the historical logger channel so log routing/filters stay unchanged.
logger = logging.getLogger("agent.services.model_invocation_service")


class ModelProfileResolverLoader:
    """Lazily load and cache the configured ``ModelProfileResolver``.

    Returns ``None`` only when no model-routing configuration was requested;
    a requested but broken configuration raises
    ``ModelRoutingConfigurationError``.
    """

    def __init__(self) -> None:
        self._resolver: Any = None
        self._lock = threading.Lock()

    def __call__(self) -> Any:
        return self.load()

    def reset(self) -> None:
        """Drop the cached resolver so the next call reloads the configuration."""
        with self._lock:
            self._resolver = None

    def load(self) -> Any:
        if self._resolver is not None:
            return self._resolver
        with self._lock:
            if self._resolver is not None:
                return self._resolver
            try:
                resolver = self._build_resolver()
            except ModelRoutingConfigurationError:
                raise
            except Exception as exc:
                logger.warning("model_invocation: resolver init failed: %s", exc)
                if model_routing_configuration_requested():
                    raise ModelRoutingConfigurationError("configured_model_routing_initialization_failed") from exc
                return None
            if resolver is not None:
                self._resolver = resolver
            return resolver

    def _build_resolver(self) -> Any:
        from agent.services.model_master_default_service import get_global_master_default_service
        from agent.services.model_profile_loader import ModelProfileLoader
        from agent.services.model_profile_resolver import (
            ModelProfileResolver,
            SecurityPolicyChecker,
        )

        profiles_path_env = os.environ.get("MODEL_PROFILES_PATH", "").strip()
        routing_path_str = (
            os.environ.get("MODEL_ROUTING_PATH", "").strip() or os.environ.get("ANANTA_MODEL_ROUTING_PATH", "").strip()
        )
        if not profiles_path_env:
            if routing_path_str:
                raise ModelRoutingConfigurationError("model_profiles_path_required_for_configured_routing")
            return None
        path = Path(profiles_path_env)
        if not path.exists():
            raise ModelRoutingConfigurationError("configured_model_profiles_file_not_found")
        result = ModelProfileLoader().load_file(path)
        if not result.ok or not result.profiles:
            logger.warning("model_invocation: profile load errors: %s", result.errors)
            raise ModelRoutingConfigurationError("configured_model_profiles_invalid")

        logger.info("model_invocation: loaded %d profiles from %s", len(result.profiles), path)

        routing_rules = self._routing_rules(routing_path_str)

        master_svc = get_global_master_default_service()
        master_profile = master_svc.get_master_profile()

        resolver = ModelProfileResolver(
            profiles=result.profiles,
            security_policy=SecurityPolicyChecker(),
            routing_rules=routing_rules,
            master_default_profile=master_profile,
            style_ranking=self._style_ranking(),
        )

        if master_profile:
            logger.info(
                "model_invocation: global master default active: provider=%s model=%s",
                master_profile.provider_id,
                master_profile.model,
            )

        # AMR-020: log deprecation warning if legacy env vars are still set
        if os.environ.get("DEFAULT_PROVIDER") or os.environ.get("DEFAULT_MODEL"):
            logger.warning(
                "model_invocation: DEFAULT_PROVIDER/DEFAULT_MODEL env vars are set but "
                "MODEL_PROFILES_PATH is also configured. Profile-based routing takes "
                "precedence. Remove DEFAULT_PROVIDER/DEFAULT_MODEL to silence this warning."
            )
        return resolver

    @staticmethod
    def _routing_rules(routing_path_str: str) -> Any:
        from agent.services.model_profile_resolver import RoutingRules

        if not routing_path_str:
            logger.debug("model_invocation: no MODEL_ROUTING_PATH set — using empty rules")
            return RoutingRules()
        rp = Path(routing_path_str)
        if not rp.exists():
            raise ModelRoutingConfigurationError("configured_model_routing_file_not_found")
        try:
            from jsonschema import Draft202012Validator

            raw_routing = json.loads(rp.read_text(encoding="utf-8"))
            if not isinstance(raw_routing, dict):
                raise ValueError("model_routing_root_must_be_object")
            schema_path = Path(__file__).resolve().parents[2] / "config" / "schemas" / "model_routing.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(raw_routing)
            routing_rules = RoutingRules.from_dict(
                raw_routing,
                strict=True,
            )
            logger.info("model_invocation: loaded routing rules from %s", rp)
            return routing_rules
        except Exception as exc:
            logger.warning(
                "model_invocation: configured routing load failed for %s: %s",
                rp,
                exc,
            )
            raise ModelRoutingConfigurationError("configured_model_routing_invalid") from exc

    @staticmethod
    def _style_ranking() -> Any:
        try:
            from agent.services.cognitive_style_service import (
                get_cognitive_style_ranking_policy,
            )

            return get_cognitive_style_ranking_policy(
                weight=float(os.environ.get("COGNITIVE_STYLE_ROUTING_WEIGHT", ".25"))
            )
        except Exception as exc:
            logger.warning(
                "model_invocation: cognitive style ranking unavailable: %s",
                type(exc).__name__,
            )
            return None
