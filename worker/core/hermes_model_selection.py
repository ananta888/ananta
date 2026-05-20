from __future__ import annotations

from pydantic import BaseModel, Field

from worker.core.hermes_adapter_config import HermesAdapterConfig


class HermesRoutingContext(BaseModel):
    task_kind: str | None = None
    execution_mode: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    forbidden_capabilities: list[str] = Field(default_factory=list)
    risk_class: str | None = None
    mutation_allowed: bool = False
    worker_role: str | None = None
    preferred_model: str | None = None
    unavailable_models: list[str] = Field(default_factory=list)
    previous_failure_reason: str | None = None
    required_response_format: str | None = None


class HermesModelSelectionResult(BaseModel):
    selected_model: str | None = None
    source: str = "rejected"
    fallback_used: bool = False
    rejected_models: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    policy_decision: str = "rejected"
    read_only_enforced: bool = True
    mutation_allowed: bool = False
    selected_response_profile: dict[str, object] | None = None

    def as_metadata(self) -> dict[str, object]:
        return {
            "selected_model": self.selected_model,
            "model_selection_source": self.source,
            "fallback_used": self.fallback_used,
            "model_selection_reason_codes": list(self.reason_codes),
            "read_only_enforced": self.read_only_enforced,
            "mutation_allowed": self.mutation_allowed,
        }


class HermesModelSelectionService:
    _MUTATION_TASK_KINDS = frozenset({
        "patch_apply", "command_execute", "shell_execute", "shell_execution",
        "service_mutation", "config_mutation", "workspace_mutation", "file_mutation",
    })

    def select_model(
        self,
        *,
        config: HermesAdapterConfig,
        context: HermesRoutingContext,
    ) -> HermesModelSelectionResult:
        task_kind = str(context.task_kind or context.execution_mode or "").strip()
        unavailable = {m.strip() for m in context.unavailable_models if str(m).strip()}
        blocked = {m.strip() for m in config.blocked_models if str(m).strip()}
        policy = config.model_selection_policy
        blocked_task_kinds = set(config.blocked_task_kinds) | set(policy.blocked_task_kinds)
        mutation_task_kinds = set(policy.mutation_task_kinds) | set(self._MUTATION_TASK_KINDS)

        if policy.reject_mutation_tasks_for_hermes and (context.mutation_allowed or task_kind in mutation_task_kinds):
            return HermesModelSelectionResult(
                source="rejected",
                rejected_models=[],
                reason_codes=["MODEL_REJECTED_MUTATION_TASK", "READ_ONLY_ENFORCED"],
                policy_decision="reject_mutation_task_for_hermes",
                read_only_enforced=True,
                mutation_allowed=bool(context.mutation_allowed),
            )
        if task_kind and task_kind in blocked_task_kinds:
            return HermesModelSelectionResult(
                source="rejected",
                reason_codes=["MODEL_REJECTED_MUTATION_TASK", "READ_ONLY_ENFORCED"],
                policy_decision="reject_blocked_task_kind",
                read_only_enforced=True,
                mutation_allowed=False,
            )
        if task_kind and policy.allowed_task_kinds_for_hermes and task_kind not in set(policy.allowed_task_kinds_for_hermes):
            return HermesModelSelectionResult(
                source="rejected",
                reason_codes=["MODEL_REJECTED_MUTATION_TASK", "READ_ONLY_ENFORCED"],
                policy_decision="reject_task_kind_not_allowed",
                read_only_enforced=True,
                mutation_allowed=False,
            )

        def _reject(code: str, model_id: str, rejected: list[str]) -> None:
            if model_id:
                rejected.append(model_id)
            if code not in reason_codes:
                reason_codes.append(code)

        def _accept(model_id: str, source: str, fallback_used: bool) -> HermesModelSelectionResult:
            selected = model_id.strip()
            result_codes = list(reason_codes)
            if source == "task_kind_models":
                result_codes.append("TASK_KIND_MODEL_SELECTED")
            elif source == "preferred_model":
                result_codes.append("PREFERRED_MODEL_SELECTED")
            elif source == "default_model":
                result_codes.append("DEFAULT_MODEL_SELECTED")
            elif source == "fallback_free_models":
                result_codes.append("FALLBACK_MODEL_SELECTED")
            return HermesModelSelectionResult(
                selected_model=selected,
                source=source,
                fallback_used=fallback_used,
                rejected_models=list(rejected_models),
                reason_codes=result_codes,
                policy_decision="selected",
                read_only_enforced=True,
                mutation_allowed=False,
            )

        def _is_valid_model(model_id: str, *, treat_unavailable_as_reject: bool = True) -> bool:
            candidate = str(model_id or "").strip()
            if not candidate:
                _reject("MODEL_REJECTED_EMPTY", candidate, rejected_models)
                return False
            if policy.reject_blocked_models and candidate in blocked:
                _reject("MODEL_REJECTED_BLOCKED", candidate, rejected_models)
                return False
            if policy.require_free_model_suffix and not candidate.endswith(":free"):
                _reject("MODEL_REJECTED_NON_FREE", candidate, rejected_models)
                return False
            if treat_unavailable_as_reject and candidate in unavailable:
                _reject("MODEL_REJECTED_UNAVAILABLE", candidate, rejected_models)
                return False
            return True

        reason_codes: list[str] = ["READ_ONLY_ENFORCED"]
        rejected_models: list[str] = []

        preferred_model = str(context.preferred_model or "").strip()
        if preferred_model and _is_valid_model(preferred_model):
            return _accept(preferred_model, "preferred_model", False)

        if policy.prefer_task_specific_model and task_kind:
            task_model = str(config.task_kind_models.get(task_kind) or "").strip()
            if task_model and _is_valid_model(task_model):
                return _accept(task_model, "task_kind_models", False)

        default_model = str(config.default_model or "").strip()
        if default_model and _is_valid_model(default_model):
            return _accept(default_model, "default_model", False)

        if policy.allow_fallback_on_unavailable:
            fallback_models = config.fallback_models_for_task_kind(task_kind)
            if not fallback_models and isinstance(config.fallback_free_models, dict):
                fallback_models = config.fallback_models_for_task_kind("default")
            for fallback_model in fallback_models:
                if _is_valid_model(fallback_model):
                    return _accept(fallback_model, "fallback_free_models", True)

        reason_codes.append("NO_MODEL_AVAILABLE")
        return HermesModelSelectionResult(
            source="rejected",
            rejected_models=rejected_models,
            reason_codes=reason_codes,
            policy_decision="no_model_available",
            read_only_enforced=True,
            mutation_allowed=False,
        )
