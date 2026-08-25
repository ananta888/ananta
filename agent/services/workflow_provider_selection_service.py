"""Hub-owned provider/model decisions shared by workflow runtimes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent.services.model_routing_contract import (
    MODEL_ROUTING_METADATA_KEY,
    ModelRoutingConfig,
    ModelRoutingContractError,
)
from agent.services.model_selection_service import (
    EffectiveModelRoutingService,
    ModelConsumerRegistry,
)
from ananta_contracts.model_selection import (
    ModelRoutingConfiguration,
    ModelRoutingDryRunCommand,
)
from ananta_contracts.provider_endpoint_policy import (
    normalize_provider_endpoint_identity,
)
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderExecutionBindingError,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)


@dataclass(frozen=True)
class WorkflowProviderRequirement:
    tenant_id: str
    workflow_id: str
    step_id: str
    task_type: str
    runtime_kind: str
    requires_provider: bool
    required_capabilities: tuple[str, ...] = ("text_generation",)
    model_routing: ModelRoutingConfig | None = None


@dataclass(frozen=True)
class WorkflowProviderDecision:
    status: str
    reason_code: str
    binding: ProviderExecutionBinding | None = None
    primary_profile_id: str = ""
    profile_bindings: tuple[ProviderProfileExecutionBinding, ...] = ()
    profile_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...] = ()
    maximum_provider_attempts: int = 0


class WorkflowProviderDecisionPort(Protocol):
    """Small Hub-side seam; implementations return data, never runtimes."""

    def decide(
        self, requirement: WorkflowProviderRequirement
    ) -> WorkflowProviderDecision: ...


class HubConfiguredWorkflowProviderDecisionService:
    """Resolve the immutable choice from Hub configuration/profile data.

    The runtime kind is deliberately not part of provider selection.  Native,
    LangChain, and LangGraph therefore receive the same decision for the same
    Hub configuration instead of consulting container-local registries.
    """

    def __init__(
        self,
        config_loader: Callable[[], Mapping[str, Any]],
        routing_configuration_loader: Callable[[], ModelRoutingConfiguration] | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._routing_configuration_loader = routing_configuration_loader

    def decide(
        self, requirement: WorkflowProviderRequirement
    ) -> WorkflowProviderDecision:
        if not requirement.requires_provider:
            return WorkflowProviderDecision(
                status="not_required",
                reason_code="provider_transport_not_required",
            )
        if (
            not requirement.tenant_id
            or not requirement.workflow_id
            or not requirement.step_id
            or not requirement.task_type
        ):
            return WorkflowProviderDecision(
                status="denied",
                reason_code="provider_requirement_binding_missing",
            )
        config = dict(self._config_loader() or {})
        profiles_path = str(
            config.get("model_profiles_path")
            or config.get("MODEL_PROFILES_PATH")
            or ""
        ).strip()
        routing_path = str(
            config.get("model_routing_path")
            or config.get("MODEL_ROUTING_PATH")
            or config.get("ANANTA_MODEL_ROUTING_PATH")
            or ""
        ).strip()
        if profiles_path or routing_path:
            return self._decide_profile_chain(
                requirement,
                profiles_path=profiles_path,
                routing_path=routing_path,
            )
        return self._decide_legacy_config(config)

    @staticmethod
    def _decide_legacy_config(
        config: Mapping[str, Any],
    ) -> WorkflowProviderDecision:
        workflow_runtime = config.get("workflow_runtime")
        workflow_runtime = (
            dict(workflow_runtime) if isinstance(workflow_runtime, Mapping) else {}
        )
        configured = workflow_runtime.get("provider_selection")
        configured = dict(configured) if isinstance(configured, Mapping) else {}
        llm_config = config.get("llm_config")
        llm_config = dict(llm_config) if isinstance(llm_config, Mapping) else {}
        provider_id = str(
            configured.get("provider_id")
            or configured.get("provider")
            or llm_config.get("provider")
            or config.get("default_provider")
            or ""
        ).strip().lower()
        model_id = str(
            configured.get("model_id")
            or configured.get("model")
            or llm_config.get("model")
            or config.get("default_model")
            or ""
        ).strip()
        source = (
            "hub_profile.workflow_runtime.provider_selection"
            if configured
            else "hub_config.llm_config"
            if llm_config.get("provider") or llm_config.get("model")
            else "hub_config.defaults"
        )
        raw_endpoint = str(
            configured.get("endpoint_url")
            or configured.get("base_url")
            or llm_config.get("endpoint_url")
            or llm_config.get("base_url")
            or ""
        ).strip()
        try:
            binding = ProviderExecutionBinding(
                provider_id=provider_id,
                model_id=model_id,
                source=source,
                reason_code="hub_provider_policy_selected",
                endpoint_identity=(
                    normalize_provider_endpoint_identity(
                        provider_id=provider_id,
                        endpoint_url=raw_endpoint,
                    )
                    if raw_endpoint
                    else ""
                ),
            )
            binding.validate()
        except ProviderExecutionBindingError as exc:
            return WorkflowProviderDecision(
                status="denied",
                reason_code=exc.reason_code,
            )
        return WorkflowProviderDecision(
            status="selected",
            reason_code="hub_provider_policy_selected",
            binding=binding,
        )

    def _decide_profile_chain(
        self,
        requirement: WorkflowProviderRequirement,
        *,
        profiles_path: str,
        routing_path: str,
    ) -> WorkflowProviderDecision:
        """Resolve every allowed profile binding inside the Hub control plane."""

        if not profiles_path:
            return WorkflowProviderDecision(
                status="denied",
                reason_code="model_profiles_path_required_for_configured_routing",
            )
        try:
            from agent.services.model_profile_loader import ModelProfileLoader
            from agent.services.model_profile_resolver import (
                ModelProfileResolver,
                RoutingContext,
                RoutingRules,
                SecurityPolicyChecker,
            )

            loaded = ModelProfileLoader().load_file(Path(profiles_path))
            if not loaded.ok or not loaded.profiles:
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="configured_model_profiles_invalid",
                )

            raw_routing: dict[str, Any] = {}
            if routing_path:
                routing_file = Path(routing_path)
                if not routing_file.exists():
                    return WorkflowProviderDecision(
                        status="denied",
                        reason_code="configured_model_routing_file_not_found",
                    )
                decoded = json.loads(routing_file.read_text(encoding="utf-8"))
                if not isinstance(decoded, dict):
                    return WorkflowProviderDecision(
                        status="denied",
                        reason_code="configured_model_routing_invalid",
                    )
                from jsonschema import Draft202012Validator

                schema_path = (
                    Path(__file__).resolve().parents[2]
                    / "config"
                    / "schemas"
                    / "model_routing.schema.json"
                )
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                Draft202012Validator(schema).validate(decoded)
                raw_routing = decoded

            raw_security = raw_routing.get("security_policy")
            raw_security = (
                dict(raw_security) if isinstance(raw_security, Mapping) else {}
            )
            style_ranking = None
            try:
                from agent.services.cognitive_style_service import (
                    get_cognitive_style_ranking_policy,
                )

                style_ranking = get_cognitive_style_ranking_policy()
            except Exception:
                style_ranking = None
            resolver = ModelProfileResolver(
                profiles=loaded.profiles,
                security_policy=SecurityPolicyChecker(
                    block_cloud_with_secrets=bool(
                        raw_security.get("block_cloud_with_secrets", True)
                    ),
                    allowed_cloud_providers=[
                        str(value)
                        for value in raw_security.get(
                            "allowed_cloud_providers", ()
                        )
                    ],
                ),
                routing_rules=RoutingRules.from_dict(
                    raw_routing,
                    strict=True,
                ),
                style_ranking=style_ranking,
            )
            capabilities = {
                str(value).strip().lower()
                for value in requirement.required_capabilities
                if str(value).strip()
            }
            task_type = str(requirement.task_type or "").strip().lower()
            routing = requirement.model_routing
            if routing is not None and routing.allow_cloud:
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_cloud_egress_not_authorized",
                )
            if (
                routing is not None
                and routing.fallback_group_id
                and routing.fallback_group_id
                not in resolver.rules.fallback_groups
            ):
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_fallback_group_not_found",
                )
            if routing is not None:
                capabilities.update(
                    str(value).strip().lower()
                    for value in routing.required_capabilities
                    if str(value).strip()
                )
            routing_context = RoutingContext(
                model_role=(
                    str(
                        (
                            routing.model_role
                            or routing.default_model_role
                        )
                        if routing is not None
                        else ""
                    ).strip()
                    or (
                        "reasoning"
                        if "reasoning" in capabilities
                        or "reasoning" in task_type
                        else "any"
                    )
                ),
                task_kind=task_type or None,
                request_profile_id=(
                    routing.preferred_profile_id
                    if routing is not None
                    else None
                ),
                requires_tools=bool(
                    capabilities & {"tool_calling", "tools"}
                    or (routing is not None and routing.requires_tools)
                ),
                requires_json=bool(
                    capabilities & {"json", "structured_output"}
                    or (routing is not None and routing.requires_json)
                ),
                requires_streaming="streaming" in capabilities,
                fallback_group_id=(
                    routing.fallback_group_id
                    if routing is not None
                    else None
                ),
                # Delegated workflow contexts are local-only unless a
                # future Hub policy explicitly introduces an egress grant.
                allow_cloud=False,
                max_estimated_cost_per_step=(
                    routing.max_estimated_cost
                    if routing is not None
                    else None
                ),
                metadata=(
                    routing.as_metadata()
                    if routing is not None
                    else {}
                ),
            )
            result, candidates = resolver.resolve_candidate_chain(
                routing_context
            )
            central_assignment_mode = "inherit"
            central_max_total_retries: int | None = None
            primary_profile_id = (
                str(result.profile.profile_id) if result.profile is not None else ""
            )
            consumer_id = self._consumer_id(requirement.task_type)
            if self._routing_configuration_loader is not None and consumer_id:
                central_configuration = self._routing_configuration_loader()
                effective_service = EffectiveModelRoutingService(
                    repository=_ReadOnlyRoutingConfigurationRepository(
                        central_configuration
                    ),
                    consumers=ModelConsumerRegistry.defaults(),
                    resolver=resolver,
                )
                effective_route, candidates = effective_service.resolve_route(
                    ModelRoutingDryRunCommand(
                        consumer_id=consumer_id,
                        organization_id=requirement.tenant_id,
                        workflow_id=requirement.workflow_id,
                        step_id=requirement.step_id,
                        task_kind=requirement.task_type,
                        requires_tools=bool(
                            capabilities & {"tool_calling", "tools"}
                        ),
                        requires_json=bool(
                            capabilities & {"json", "structured_output"}
                        ),
                        requires_streaming="streaming" in capabilities,
                        allow_cloud=False,
                    ),
                    base_context=routing_context,
                )
                if not effective_route.executable:
                    return WorkflowProviderDecision(
                        status="denied",
                        reason_code="provider_central_route_unavailable",
                    )
                central_assignment_mode = effective_route.assignment_mode
                central_max_total_retries = (
                    effective_route.maximum_total_retries
                )
                primary_profile_id = str(
                    effective_route.resolved_profile_id or ""
                )
            if not primary_profile_id or not candidates:
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_profile_chain_unavailable",
                )
            if (
                routing is not None
                and routing.preferred_profile_id
                and primary_profile_id != routing.preferred_profile_id
                and central_assignment_mode == "inherit"
            ):
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_preferred_profile_unavailable",
                )
            if len(candidates) > 8:
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_profile_binding_limit_exceeded",
                )
            candidates = candidates[:8]
            profile_binding_values: list[
                ProviderProfileExecutionBinding
            ] = []
            for profile in candidates:
                endpoint_identity = (
                    normalize_provider_endpoint_identity(
                        provider_id=profile.provider_id,
                        endpoint_url=profile.base_url,
                    )
                    if profile.base_url
                    else ""
                )
                profile_binding_values.append(
                    ProviderProfileExecutionBinding(
                        profile_id=profile.profile_id,
                        binding=ProviderExecutionBinding(
                            provider_id=profile.provider_id,
                            model_id=profile.model,
                            source="hub_model_profile_routing",
                            reason_code="hub_provider_profile_selected",
                            endpoint_identity=endpoint_identity,
                        ),
                    )
                )
            profile_bindings = tuple(profile_binding_values)
            for profile_binding in profile_bindings:
                profile_binding.validate()
            primary = next(
                (
                    item.binding
                    for item in profile_bindings
                    if item.profile_id == primary_profile_id
                ),
                None,
            )
            if primary is None:
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_primary_profile_binding_missing",
                )
            fallback_group = resolver.fallback_group_rule_for_context(
                routing_context,
                primary_profile_id,
            )
            remaining_group_retries = (
                max(0, int(central_max_total_retries))
                if central_max_total_retries is not None
                else max(0, int(fallback_group.max_total_retries))
                if fallback_group is not None
                else sum(
                    max(0, int(profile.retry_budget))
                    for profile in candidates
                )
            )
            binding_by_profile = {
                item.profile_id: item
                for item in profile_bindings
            }
            profile_attempt_plan_values: list[
                ProviderProfileAttemptPlanEntry
            ] = []
            for profile in candidates:
                profile_binding = binding_by_profile[profile.profile_id]
                retries = min(
                    max(0, int(profile.retry_budget)),
                    remaining_group_retries,
                )
                remaining_group_retries -= retries
                profile_attempt_plan_values.append(
                    ProviderProfileAttemptPlanEntry.from_profile_binding(
                        profile_binding,
                        maximum_attempts=1 + retries,
                        allowed_error_types=tuple(
                            str(value or "").strip()
                            for value in profile.extra.get(
                                "central_fallback_triggers", ()
                            )
                            if str(value or "").strip()
                        ),
                    )
                )
            profile_attempt_plan = tuple(profile_attempt_plan_values)
            maximum_provider_attempts = sum(
                item.maximum_attempts
                for item in profile_attempt_plan
            )
            if not 1 <= maximum_provider_attempts <= 33:
                return WorkflowProviderDecision(
                    status="denied",
                    reason_code="provider_profile_retry_budget_invalid",
                )
            return WorkflowProviderDecision(
                status="selected",
                reason_code="hub_provider_profile_chain_selected",
                binding=primary,
                primary_profile_id=primary_profile_id,
                profile_bindings=profile_bindings,
                profile_attempt_plan=profile_attempt_plan,
                maximum_provider_attempts=maximum_provider_attempts,
            )
        except Exception:
            return WorkflowProviderDecision(
                status="denied",
                reason_code="configured_model_routing_invalid",
            )

    @staticmethod
    def _consumer_id(task_type: str) -> str | None:
        normalized = str(task_type or "").strip().lower().replace("-", "_")
        return {
            "planning": "task.planning",
            "coding": "task.coding",
            "debugging": "task.debugging",
            "review": "task.review",
            "research": "task.research",
            "repo_analysis": "task.repo_analysis",
        }.get(normalized)


class _ReadOnlyRoutingConfigurationRepository:
    def __init__(self, value: ModelRoutingConfiguration) -> None:
        self._value = value

    def load(self) -> ModelRoutingConfiguration:
        return self._value

    def save_if_revision(
        self,
        expected_revision: int,
        value: ModelRoutingConfiguration,
    ) -> bool:
        return False


def load_current_hub_provider_config() -> Mapping[str, Any]:
    """Read request-local Hub config, falling back to typed process settings."""

    from flask import current_app, has_app_context

    from agent.config import settings

    if has_app_context():
        configured = current_app.config.get("AGENT_CONFIG")
        if isinstance(configured, Mapping):
            values = dict(configured)
        else:
            values = {}
    else:
        values = {}
    values.setdefault("default_provider", settings.default_provider)
    values.setdefault("default_model", settings.default_model)
    values.setdefault(
        "model_profiles_path",
        str(os.environ.get("MODEL_PROFILES_PATH") or "").strip(),
    )
    values.setdefault(
        "model_routing_path",
        str(
            os.environ.get("MODEL_ROUTING_PATH")
            or os.environ.get("ANANTA_MODEL_ROUTING_PATH")
            or ""
        ).strip(),
    )
    return values


def build_workflow_provider_decision_service() -> WorkflowProviderDecisionPort:
    return HubConfiguredWorkflowProviderDecisionService(
        load_current_hub_provider_config,
        load_current_model_routing_configuration,
    )


def load_current_model_routing_configuration() -> ModelRoutingConfiguration:
    from agent.repositories.model_routing_configuration import (
        SqlModelRoutingConfigurationRepository,
    )

    return SqlModelRoutingConfigurationRepository().load()


def trusted_model_routing_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> ModelRoutingConfig | None:
    """Read only the strict, Hub-compiled node routing sub-contract."""

    if not isinstance(metadata, Mapping):
        return None
    if MODEL_ROUTING_METADATA_KEY not in metadata:
        return None
    raw = metadata.get(MODEL_ROUTING_METADATA_KEY)
    if not isinstance(raw, Mapping):
        raise ModelRoutingContractError("model_routing_mapping_required")
    return ModelRoutingConfig.assert_runtime_mapping(raw)


__all__ = [
    "HubConfiguredWorkflowProviderDecisionService",
    "WorkflowProviderDecision",
    "WorkflowProviderDecisionPort",
    "ProviderProfileExecutionBinding",
    "WorkflowProviderRequirement",
    "build_workflow_provider_decision_service",
    "load_current_hub_provider_config",
    "load_current_model_routing_configuration",
    "trusted_model_routing_from_metadata",
]
