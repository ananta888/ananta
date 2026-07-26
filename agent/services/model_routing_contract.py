"""Typed, allowlisted model-routing metadata for Hub-to-Worker transport.

The Hub may attach this declarative configuration to an execution node.  It is
data only: workers may use it to select an already delegated model invocation,
but it does not grant orchestration or task-creation authority.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

if TYPE_CHECKING:
    from agent.services.model_profile_resolver import RoutingContext

MODEL_ROUTING_METADATA_KEY = "model_routing"
ContextRecoveryStrategy = Literal[
    "compact_context",
    "segment_planning",
    "propose_task_plan",
    "require_approval",
    "stop",
]
CONTEXT_RECOVERY_STRATEGY_ORDER = (
    "compact_context",
    "segment_planning",
    "propose_task_plan",
    "require_approval",
    "stop",
)


class ModelRoutingContractError(ValueError):
    """Stable validation failure raised at a runtime-contract boundary."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(
            self.reason_code
            if not self.detail
            else f"{self.reason_code}:{self.detail}"
        )


class ModelRoutingConfig(BaseModel):
    """Declarative routing override stored under ``metadata.model_routing``.

    Unknown fields are intentionally ignored during compilation.  Callers must
    serialize with :meth:`as_metadata`, so only the explicit allowlist crosses
    the runtime boundary.
    """

    strategy: str = "local_first"
    model_role: Optional[str] = None
    preferred_profile_id: Optional[str] = None
    fallback_group_id: Optional[str] = None
    required_capabilities: list[str] = Field(default_factory=list)
    requires_json: Optional[StrictBool] = None
    requires_tools: Optional[StrictBool] = None
    tool_calling_mode: Optional[
        Literal["native_tools", "prompt_json", "both", "none"]
    ] = None
    allow_cloud: StrictBool = False
    max_estimated_cost: Optional[float] = None
    max_estimated_cost_per_run: Optional[float] = None
    default_model_role: Optional[str] = None
    require_approval_on_cloud_escalation: StrictBool = False
    require_approval_above_estimated_cost: Optional[float] = None
    # Hub instructions only. Workers do not create or orchestrate follow-up
    # work from these values.
    context_recovery_strategies: list[ContextRecoveryStrategy] = Field(
        default_factory=list
    )
    require_approval_for_generated_plan: StrictBool = True

    # Ignore additive designer fields while compiling, then serialize only this
    # model's fields. This preserves graph compatibility without transporting
    # an unreviewed field to a Worker.
    model_config = {"extra": "ignore"}

    @field_validator(
        "strategy",
        "model_role",
        "preferred_profile_id",
        "fallback_group_id",
        "default_model_role",
    )
    @classmethod
    def _validate_bounded_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or len(normalized) > 256 or "\x00" in normalized:
            raise ValueError("model_routing_text_invalid")
        return normalized

    @field_validator("required_capabilities")
    @classmethod
    def _validate_capabilities(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = str(value).strip()
            if not item or len(item) > 128 or "\x00" in item:
                raise ValueError("model_routing_capability_invalid")
            if item not in normalized:
                normalized.append(item)
        if len(normalized) > 64:
            raise ValueError("model_routing_capabilities_too_large")
        return normalized

    @field_validator(
        "max_estimated_cost",
        "max_estimated_cost_per_run",
        "require_approval_above_estimated_cost",
        mode="before",
    )
    @classmethod
    def _validate_cost(cls, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("model_routing_cost_invalid")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized < 0:
            raise ValueError("model_routing_cost_invalid")
        return normalized

    @model_validator(mode="after")
    def _validate_context_recovery(self) -> "ModelRoutingConfig":
        strategies = list(self.context_recovery_strategies)
        positions = {
            strategy: index
            for index, strategy in enumerate(
                CONTEXT_RECOVERY_STRATEGY_ORDER
            )
        }
        if "stop" in strategies and strategies[-1] != "stop":
            raise ValueError("context_recovery_stop_must_be_last")
        if len(set(strategies)) != len(strategies):
            raise ValueError("context_recovery_strategies_must_be_unique")
        if [
            positions[strategy] for strategy in strategies
        ] != sorted(positions[strategy] for strategy in strategies):
            raise ValueError("context_recovery_strategy_order_invalid")
        generated_plan_requested = bool(
            {"segment_planning", "propose_task_plan"}.intersection(
                strategies
            )
        )
        if generated_plan_requested:
            if "require_approval" not in strategies:
                raise ValueError("generated_task_plan_approval_strategy_required")
            plan_strategy_index = min(
                strategies.index(strategy)
                for strategy in (
                    "segment_planning",
                    "propose_task_plan",
                )
                if strategy in strategies
            )
            if strategies.index("require_approval") < plan_strategy_index:
                raise ValueError("generated_task_plan_approval_order_invalid")
        if (
            generated_plan_requested
            and not self.require_approval_for_generated_plan
        ):
            raise ValueError("generated_task_plan_requires_approval")
        return self

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any] | ModelRoutingConfig
    ) -> "ModelRoutingConfig":
        if isinstance(value, cls):
            return value.model_copy(deep=True)
        if not isinstance(value, Mapping):
            raise ModelRoutingContractError("model_routing_mapping_required")
        return cls.model_validate(dict(value))

    @classmethod
    def from_metadata(
        cls, metadata: Mapping[str, Any] | None
    ) -> Optional["ModelRoutingConfig"]:
        if not isinstance(metadata, Mapping):
            return None
        if MODEL_ROUTING_METADATA_KEY not in metadata:
            return None
        raw = metadata.get(MODEL_ROUTING_METADATA_KEY)
        if raw is None:
            return None
        return cls.from_mapping(raw)

    @classmethod
    def assert_runtime_mapping(
        cls, value: Mapping[str, Any]
    ) -> "ModelRoutingConfig":
        """Validate an already compiled runtime value without field smuggling."""

        if not isinstance(value, Mapping):
            raise ModelRoutingContractError("model_routing_mapping_required")
        unknown = sorted(
            str(key) for key in value.keys() if key not in cls.model_fields
        )
        if unknown:
            raise ModelRoutingContractError(
                "model_routing_field_not_allowed", ",".join(unknown)
            )
        try:
            return cls.from_mapping(value)
        except ModelRoutingContractError:
            raise
        except Exception as exc:
            raise ModelRoutingContractError(
                "model_routing_invalid", type(exc).__name__
            ) from exc

    def as_metadata(self) -> dict[str, Any]:
        """Return a sparse canonical override; extras can never survive.

        Defaults are applied when the effective merged contract is parsed, not
        serialized into each step.  This preserves graph-level inheritance.
        """

        return self.model_dump(mode="json", exclude_none=True, exclude_unset=True)


def sanitize_model_routing_metadata(
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Copy metadata and canonicalize only its model-routing sub-contract."""

    sanitized = dict(metadata or {})
    if MODEL_ROUTING_METADATA_KEY not in sanitized:
        return sanitized
    routing = ModelRoutingConfig.from_metadata(sanitized)
    if routing is None:
        sanitized.pop(MODEL_ROUTING_METADATA_KEY, None)
    else:
        sanitized[MODEL_ROUTING_METADATA_KEY] = routing.as_metadata()
    return sanitized


def _model_routing_candidates(task: Any) -> list[Any]:
    """Return declarations in precedence order without parsing their values."""

    execution_context = _as_mapping(
        _value(task, "worker_execution_context")
    )
    native_commands = (
        _value(execution_context, "native_node_command"),
        _value(task, "native_node_command"),
    )
    candidates: list[Any] = []
    for command in native_commands:
        node = _value(command, "node")
        metadata = _as_mapping(_value(node, "metadata"))
        if MODEL_ROUTING_METADATA_KEY in metadata:
            candidates.append(metadata.get(MODEL_ROUTING_METADATA_KEY))

    workflow_runtime = _value(execution_context, "workflow_runtime")
    runtime_node = _value(workflow_runtime, "node")
    runtime_metadata = _as_mapping(_value(runtime_node, "metadata"))
    if MODEL_ROUTING_METADATA_KEY in runtime_metadata:
        candidates.append(runtime_metadata.get(MODEL_ROUTING_METADATA_KEY))

    workflow_step = _value(execution_context, "workflow_step")
    step_metadata = _as_mapping(_value(workflow_step, "metadata"))
    if MODEL_ROUTING_METADATA_KEY in step_metadata:
        candidates.append(step_metadata.get(MODEL_ROUTING_METADATA_KEY))
    if _has_value(workflow_step, MODEL_ROUTING_METADATA_KEY):
        candidates.append(_value(workflow_step, MODEL_ROUTING_METADATA_KEY))

    if _has_value(execution_context, MODEL_ROUTING_METADATA_KEY):
        candidates.append(_value(execution_context, MODEL_ROUTING_METADATA_KEY))

    task_metadata = _as_mapping(_value(task, "metadata"))
    if MODEL_ROUTING_METADATA_KEY in task_metadata:
        candidates.append(task_metadata.get(MODEL_ROUTING_METADATA_KEY))
    if _has_value(task, MODEL_ROUTING_METADATA_KEY):
        candidates.append(_value(task, MODEL_ROUTING_METADATA_KEY))
    return candidates


def has_model_routing_declaration(task: Any) -> bool:
    """Return whether any supported task boundary declares model routing."""

    return bool(_model_routing_candidates(task))


def extract_model_routing_from_task(task: Any) -> ModelRoutingConfig | None:
    """Extract routing from a task without trusting arbitrary outer overrides.

    A Native execution task carries the Hub-compiled node inside
    ``worker_execution_context.native_node_command``. That fenced node wins over
    legacy task metadata. For non-Native tasks, workflow-step and direct
    metadata shapes remain supported for backward compatibility.

    Invalid higher-precedence data returns ``None`` instead of falling through
    to a lower-precedence value. Callers that must distinguish invalid input
    from an absent declaration use :func:`has_model_routing_declaration`.
    """

    for candidate in _model_routing_candidates(task):
        try:
            return ModelRoutingConfig.from_mapping(candidate)
        except Exception:
            return None
    return None


def build_model_routing_context(
    task: Any,
    *,
    context_text: str = "",
    requires_json: bool = False,
    requires_tools: bool = False,
) -> RoutingContext | None:
    """Build the resolver input for a task's validated routing declaration."""

    routing = extract_model_routing_from_task(task)
    if routing is None:
        if has_model_routing_declaration(task):
            raise ModelRoutingContractError("model_routing_invalid")
        return None

    # Lazy import keeps this transport contract independent from resolver
    # construction and prevents a service-layer import cycle.
    from agent.services.model_profile_resolver import RoutingContext

    execution_context = _as_mapping(_value(task, "worker_execution_context"))
    workflow_step = _value(execution_context, "workflow_step")
    task_kind = str(
        _value(task, "task_kind")
        or _value(workflow_step, "task_kind")
        or ""
    ).strip() or None
    capabilities = set(routing.required_capabilities)
    requires_json = bool(
        requires_json
        or routing.requires_json
        or {"json", "supports_json"}.intersection(capabilities)
    )
    requires_tools = bool(
        requires_tools
        or routing.requires_tools
        or {"tools", "supports_tools"}.intersection(capabilities)
    )
    return RoutingContext(
        model_role=str(
            routing.model_role or routing.default_model_role or "any"
        ),
        blueprint_id=_optional_task_text(task, "blueprint_id"),
        template_id=_optional_task_text(task, "template_id"),
        team_id=_optional_task_text(task, "team_id"),
        task_kind=task_kind,
        risk_class=_optional_task_text(task, "risk_class"),
        context_text=str(context_text or ""),
        request_profile_id=routing.preferred_profile_id,
        requires_tools=requires_tools,
        requires_json=requires_json,
        step_kind=task_kind,
        fallback_group_id=routing.fallback_group_id,
        allow_cloud=routing.allow_cloud,
        max_estimated_cost_per_step=routing.max_estimated_cost,
        metadata=routing.as_metadata(),
    )


def model_routing_policy_failure_metadata(
    error: ModelRoutingContractError,
) -> dict[str, Any]:
    """Return a bounded terminal policy fact for an invalid routing contract."""

    return {
        "fallback_decisions": [
            {
                "reason": str(error.reason_code or "model_routing_invalid")[:160],
                "previous_profile_id": None,
                "next_profile_id": None,
                "trigger": "policy_blocked",
                "terminal": True,
            }
        ]
    }


def _value(container: Any, key: str) -> Any:
    if isinstance(container, Mapping):
        return container.get(key)
    if container is None:
        return None
    return getattr(container, key, None)


def _has_value(container: Any, key: str) -> bool:
    if isinstance(container, Mapping):
        return key in container
    return container is not None and hasattr(container, key)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_task_text(task: Any, key: str) -> str | None:
    value = str(_value(task, key) or "").strip()
    return value or None


MODEL_ROUTING_JSON_SCHEMA: dict[str, Any] = ModelRoutingConfig.model_json_schema()
MODEL_ROUTING_JSON_SCHEMA["additionalProperties"] = False
