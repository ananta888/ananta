from __future__ import annotations

import time
import uuid
from typing import Any

from agent.db_models import EvolutionProposalDB
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.critical_workflow_state_service import WorkflowTransitionError, get_critical_workflow_state_service
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.repository_registry import get_repository_registry
from agent.services.evolution.context_builder import EvolutionContextBuilder
from agent.services.evolution.payload_redaction import bounded_payload as _bounded_payload
from agent.services.evolution.models import (
    ApplyResult,
    EvolutionCapability,
    EvolutionContext,
    EvolutionPolicy,
    EvolutionProposal,
    EvolutionResult,
    EvolutionTrigger,
    EvolutionTriggerType,
    PersistedEvolutionAnalysis,
    ValidationResult,
)


def _extract_mutation_approval_artifact(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    artifact = payload.get("mutation_approval_artifact")
    if isinstance(artifact, dict):
        return dict(artifact)
    return None


def _audit_details(
    provider_name: str,
    context: EvolutionContext,
    trigger: EvolutionTrigger,
) -> dict[str, Any]:
    return {
        "provider_name": provider_name,
        "task_id": context.task_id,
        "goal_id": context.goal_id,
        "trace_id": context.trace_id,
        "plan_id": context.plan_id,
        "trigger_type": trigger.trigger_type.value,
        "trigger_source": trigger.source,
        "actor": trigger.actor,
        "reason": trigger.reason,
    }


def _capability_entry(
    capability: EvolutionCapability,
    *,
    supported: bool,
    policy_allowed: bool,
    configured: bool,
    fail_closed_reason: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    available = bool(supported and policy_allowed and configured)
    payload = {
        "capability": capability.value,
        "supported": bool(supported),
        "policy_allowed": bool(policy_allowed),
        "configured": bool(configured),
        "available": available,
    }
    if fail_closed_reason and not available:
        payload["fail_closed_reason"] = fail_closed_reason
    if note:
        payload["note"] = note
    return payload


def _with_capability_matrix(
    descriptor: dict[str, Any],
    *,
    policy: EvolutionPolicy,
    config: dict[str, Any] | None = None,
    provider_config_fn=None,
) -> dict[str, Any]:
    provider_name = str(descriptor.get("provider_name") or "").strip()
    capabilities = set()
    for item in descriptor.get("capabilities") or []:
        try:
            capabilities.add(EvolutionCapability(str(item)))
        except ValueError:
            continue
    provider_cfg = provider_config_fn(provider_name, config=config) if provider_config_fn else {}
    force_analyze_only = bool(provider_cfg.get("force_analyze_only", False))
    matrix = {
        "analyze": _capability_entry(
            EvolutionCapability.ANALYZE,
            supported=EvolutionCapability.ANALYZE in capabilities,
            policy_allowed=policy.enabled,
            configured=True,
        ),
        "propose": _capability_entry(
            EvolutionCapability.PROPOSE,
            supported=EvolutionCapability.PROPOSE in capabilities,
            policy_allowed=policy.enabled,
            configured=True,
            note="Analyze may still return reviewable proposals when provider supports provider-specific proposal output.",
        ),
        "validate": _capability_entry(
            EvolutionCapability.VALIDATE,
            supported=EvolutionCapability.VALIDATE in capabilities,
            policy_allowed=policy.enabled and policy.validate_allowed and not force_analyze_only,
            configured=not force_analyze_only,
            fail_closed_reason="evolution_provider_analyze_only" if force_analyze_only else None,
        ),
        "apply": _capability_entry(
            EvolutionCapability.APPLY,
            supported=EvolutionCapability.APPLY in capabilities,
            policy_allowed=policy.enabled and policy.apply_allowed and not force_analyze_only,
            configured=not force_analyze_only,
            fail_closed_reason=(
                "evolution_provider_analyze_only"
                if force_analyze_only
                else ("evolution_apply_disabled" if not policy.apply_allowed else None)
            ),
        ),
        "risk_scoring": _capability_entry(
            EvolutionCapability.RISK_SCORING,
            supported=EvolutionCapability.RISK_SCORING in capabilities,
            policy_allowed=policy.enabled,
            configured=True,
        ),
        "review_hints": _capability_entry(
            EvolutionCapability.REVIEW_HINTS,
            supported=EvolutionCapability.REVIEW_HINTS in capabilities,
            policy_allowed=policy.enabled,
            configured=True,
        ),
    }
    enriched = dict(descriptor)
    enriched["capability_matrix"] = matrix
    enriched["fail_closed"] = any(
        entry["supported"] and not entry["available"]
        for entry in matrix.values()
    )
    return enriched


def _with_health_capability_matrix(
    health: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    policy: EvolutionPolicy | None = None,
    provider_config_fn=None,
) -> dict[str, Any]:
    payload = dict(health or {})
    payload["providers"] = [
        _with_capability_matrix(dict(provider), config=config, policy=policy, provider_config_fn=provider_config_fn)
        for provider in payload.get("providers", [])
        if isinstance(provider, dict)
    ]
    return payload


def _provider_config(provider_name: str, *, config: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((config or {}).get("evolution") or config or {})
    overrides = raw.get("provider_overrides") if isinstance(raw.get("provider_overrides"), dict) else {}
    provider_cfg = overrides.get(str(provider_name or "").strip().lower())
    return dict(provider_cfg or {})


class EvolutionProposalService:

    def __init__(
        self,
        *,
        repositories=None,
        audit_fn=None,
        context_builder=None,
        validate_fn=None,
        apply_fn=None,
    ):
        self._repositories = repositories
        self._audit_fn = audit_fn
        self._context_builder = context_builder or EvolutionContextBuilder()
        self._validate_fn = validate_fn
        self._apply_fn = apply_fn

    def _audit(self, action: str, details: dict[str, Any]) -> None:
        if self._audit_fn is not None:
            self._audit_fn(action, details)

    def _bounded_payload(self, payload: Any, *, policy: EvolutionPolicy) -> Any:
        return _bounded_payload(payload, policy=policy)

    def _summarize_target_refs(self, target_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in list(target_refs or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("file") or item.get("file_path") or item.get("target_path") or "").strip()
            artifact_id = str(item.get("artifact_id") or "").strip()
            entry = {
                "path": path or None,
                "artifact_id": artifact_id or None,
                "type": str(item.get("type") or "").strip() or None,
            }
            if entry["path"] or entry["artifact_id"]:
                result.append(entry)
        return result

    def _build_evolver_mutation_tool_calls(
        self,
        *,
        proposal_id: str,
        task_id: str | None,
        goal_id: str | None,
        target_refs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": "evolution_apply",
                "args": {
                    "proposal_id": proposal_id,
                    "task_id": task_id,
                    "goal_id": goal_id,
                    "target_refs": list(target_refs or []),
                },
            }
        ]

    def _build_review_context(
        self,
        *,
        task_payload: dict[str, Any],
        proposal_id: str,
        task_id: str | None,
        goal_id: str | None,
        target_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        gate_service = get_mutation_gate_service()
        tool_calls = self._build_evolver_mutation_tool_calls(
            proposal_id=proposal_id,
            task_id=task_id,
            goal_id=goal_id,
            target_refs=target_refs,
        )
        normalized_target = gate_service.normalize_target(command=None, tool_calls=tool_calls, task=task_payload)
        affected_targets = self._summarize_target_refs(target_refs)
        return {
            "operation_class": "patch_apply",
            "target_count": len(affected_targets),
            "affected_targets": affected_targets,
            "normalized_target": normalized_target,
        }

    def _issue_mutation_approval_artifact(
        self,
        *,
        task_payload: dict[str, Any],
        proposal_id: str,
        task_id: str | None,
        goal_id: str | None,
        trace_id: str | None,
        actor: str,
        review_context: dict[str, Any],
        target_refs: list[dict[str, Any]],
        now: float,
    ) -> dict[str, Any]:
        ttl_seconds = 600.0
        normalized_target = dict(review_context.get("normalized_target") or {})
        return {
            "approval_id": f"evo-{uuid.uuid4()}",
            "status": "approved",
            "issued_at": now,
            "expires_at": now + ttl_seconds,
            "issued_by": actor,
            "task_id": task_id,
            "goal_id": goal_id,
            "trace_id": trace_id,
            "proposal_id": proposal_id,
            "mutation_class": "patch_apply",
            "mutation_classes": ["patch_apply"],
            "target_fingerprint": str(normalized_target.get("target_fingerprint") or "").strip() or None,
            "target_preview": {
                "target_type": normalized_target.get("target_type"),
                "path": normalized_target.get("path"),
                "artifact_id": normalized_target.get("artifact_id"),
            },
            "target_refs": self._summarize_target_refs(target_refs),
        }

    def _build_mutation_approval_scope(
        self,
        *,
        approval_artifact: dict[str, Any] | None,
        task_id: str,
        trace_id: str | None,
        actor: str,
        proposal_id: str,
        normalized_target: dict[str, Any],
        require_scoped_approval: bool,
    ) -> dict[str, Any]:
        if isinstance(approval_artifact, dict):
            status = str(approval_artifact.get("status") or "").strip().lower()
            if status not in {"approved", "granted"}:
                raise PermissionError("evolution_apply_invalid_mutation_approval:status")
            if str(approval_artifact.get("proposal_id") or "").strip() != str(proposal_id):
                raise PermissionError("evolution_apply_invalid_mutation_approval:proposal")
            if str(approval_artifact.get("task_id") or "").strip() != str(task_id):
                raise PermissionError("evolution_apply_invalid_mutation_approval:task")
            artifact_trace = str(approval_artifact.get("trace_id") or "").strip()
            if trace_id and artifact_trace and artifact_trace != str(trace_id):
                raise PermissionError("evolution_apply_invalid_mutation_approval:trace")
            try:
                expires_at = float(approval_artifact.get("expires_at"))
            except (TypeError, ValueError):
                raise PermissionError("evolution_apply_invalid_mutation_approval:expires") from None
            if expires_at <= time.time():
                raise PermissionError("evolution_apply_invalid_mutation_approval:expired")
            expected_fingerprint = str(approval_artifact.get("target_fingerprint") or "").strip()
            actual_fingerprint = str(normalized_target.get("target_fingerprint") or "").strip()
            if expected_fingerprint and actual_fingerprint and expected_fingerprint != actual_fingerprint:
                raise PermissionError("evolution_apply_invalid_mutation_approval:target")
            return {
                "task_id": task_id,
                "trace_id": trace_id,
                "actor": str(approval_artifact.get("issued_by") or actor or "system"),
                "mutation_classes": list(approval_artifact.get("mutation_classes") or ["patch_apply"]),
                "target_fingerprint": actual_fingerprint or expected_fingerprint,
                "expires_at": expires_at,
                "approval_id": str(approval_artifact.get("approval_id") or "").strip() or None,
            }
        if require_scoped_approval:
            raise PermissionError("evolution_apply_missing_mutation_approval")
        return {
            "task_id": task_id,
            "trace_id": trace_id,
            "actor": actor,
            "mutation_classes": ["patch_apply"],
            "target_fingerprint": str(normalized_target.get("target_fingerprint") or "").strip() or None,
            "expires_at": time.time() + 600,
        }

    def _enforce_mutation_gate_for_apply(
        self,
        *,
        context: EvolutionContext,
        proposal: EvolutionProposal,
        config: dict[str, Any] | None,
        actor: str,
        source: str,
        mutation_approval_artifact: dict[str, Any] | None = None,
        require_scoped_approval: bool = False,
    ) -> None:
        repos = self._repositories or get_repository_registry()
        task_row = repos.task_repo.get_by_id(context.task_id)
        task_payload = task_row.model_dump() if task_row is not None else {"id": context.task_id}
        trace_id = str(context.trace_id or task_payload.get("goal_trace_id") or "").strip() or None
        mutation_tool_calls = self._build_evolver_mutation_tool_calls(
            proposal_id=proposal.proposal_id,
            task_id=context.task_id,
            goal_id=context.goal_id,
            target_refs=list(proposal.target_refs or []),
        )
        gate_service = get_mutation_gate_service()
        normalized_target = gate_service.normalize_target(command=None, tool_calls=mutation_tool_calls, task=task_payload)
        approval_payload_obj = get_approval_policy_service().evaluate(
            command=None,
            tool_calls=mutation_tool_calls,
            task={**task_payload, "approval_confirmed": False},
            agent_cfg=config or {},
        )
        approval_payload = approval_payload_obj.as_dict()
        if require_scoped_approval and approval_payload.get("classification") == "allow":
            approval_payload = {
                **approval_payload,
                "classification": "confirm_required",
                "reason_code": "approval_confirmation_required:mutation",
                "required_confirmation_level": "operator",
            }
        risk_payload = evaluate_execution_risk(
            command=None,
            tool_calls=mutation_tool_calls,
            task=task_payload,
            agent_cfg=config or {},
        )
        mutation_approval_scope = self._build_mutation_approval_scope(
            approval_artifact=mutation_approval_artifact,
            task_id=context.task_id,
            trace_id=trace_id,
            actor=actor,
            proposal_id=proposal.proposal_id,
            normalized_target=normalized_target,
            require_scoped_approval=require_scoped_approval,
        )
        mutation_gate_task = {
            **task_payload,
            "mutation_approval": mutation_approval_scope,
        }
        mutation_gate = gate_service.evaluate(
            command=None,
            tool_calls=mutation_tool_calls,
            task=mutation_gate_task,
            agent_cfg=config or {},
            approval_decision=approval_payload,
            risk_decision=risk_payload,
            trace_id=trace_id,
            actor=actor,
        ).as_dict()
        get_execution_audit_service().emit(
            operation_type="mutation_gate_decision",
            outcome=str(mutation_gate.get("classification") or "unknown"),
            trace_id=trace_id,
            goal_id=context.goal_id,
            task_id=context.task_id,
            actor_role="hub",
            details={
                "reason_code": mutation_gate.get("reason_code"),
                "mutation_class": mutation_gate.get("mutation_class"),
                "normalized_target": mutation_gate.get("normalized_target"),
                "approval_scope": mutation_gate.get("approval_scope"),
                "source": source,
                "proposal_id": proposal.proposal_id,
                "approval_artifact_id": str((mutation_approval_artifact or {}).get("approval_id") or "").strip() or None,
                "approval_policy_classification": approval_payload.get("classification"),
                "approval_policy_operation_class": approval_payload.get("operation_class"),
                "prewrite_scope_required": bool(require_scoped_approval),
            },
        )
        if mutation_gate.get("classification") in {"blocked", "confirm_required"}:
            raise PermissionError(f"mutation_gate_blocked:{mutation_gate.get('reason_code')}")

    def _proposal_db(
        self,
        run,
        context: EvolutionContext,
        result: EvolutionResult,
        proposal: EvolutionProposal,
        *,
        policy: EvolutionPolicy,
    ):
        review_context = self._build_review_context(
            task_payload={"id": context.task_id, "goal_id": context.goal_id},
            proposal_id=proposal.proposal_id,
            task_id=context.task_id,
            goal_id=context.goal_id,
            target_refs=list(proposal.target_refs or []),
        )
        workflow_state = get_critical_workflow_state_service().initialize(
            "evolution_proposal",
            state="review_required" if bool(proposal.requires_review) else "approved",
        )
        return EvolutionProposalDB(
            id=proposal.proposal_id,
            run_id=run.id,
            provider_name=result.provider_name,
            task_id=context.task_id,
            goal_id=context.goal_id,
            trace_id=context.trace_id,
            proposal_type=proposal.proposal_type,
            title=proposal.title,
            description=proposal.description,
            rationale=proposal.rationale,
            risk_level=proposal.risk_level,
            confidence=proposal.confidence,
            requires_review=proposal.requires_review,
            target_refs=list(proposal.target_refs or []),
            proposal_metadata={
                "context_id": context.context_id,
                "review_context": review_context,
                "workflow_state": workflow_state,
            },
            provider_metadata=_bounded_payload(proposal.provider_metadata or {}, policy=policy),
            raw_payload=_bounded_payload(proposal.raw_payload, policy=policy),
        )

    def proposal_read_model(self, proposal) -> dict[str, Any]:
        proposal_metadata = dict(proposal.proposal_metadata or {})
        workflow_service = get_critical_workflow_state_service()
        workflow_state = workflow_service.materialize_record(
            proposal_metadata.get("workflow_state"),
            workflow_type="evolution_proposal",
        )
        workflow_replay = workflow_service.replay(workflow_state, workflow_type="evolution_proposal")
        workflow_timeout = workflow_service.inspect_timeout(workflow_state, workflow_type="evolution_proposal")
        review = dict(proposal_metadata.get("review") or {})
        validations = list(proposal_metadata.get("validations") or [])
        applies = list(proposal_metadata.get("applies") or [])
        approval_artifact = _extract_mutation_approval_artifact(proposal_metadata)
        review_context = dict(proposal_metadata.get("review_context") or {})
        return {
            "proposal_id": proposal.id,
            "run_id": proposal.run_id,
            "provider_name": proposal.provider_name,
            "proposal_type": proposal.proposal_type,
            "title": proposal.title,
            "description": proposal.description,
            "rationale": proposal.rationale,
            "risk_level": proposal.risk_level,
            "confidence": proposal.confidence,
            "requires_review": proposal.requires_review,
            "status": proposal.status,
            "target_refs": list(proposal.target_refs or []),
            "artifact_refs": list(proposal.artifact_refs or []),
            "review": {
                "required": bool(proposal.requires_review),
                "status": str(review.get("status") or ("pending" if proposal.requires_review else "not_required")),
                "reviewed_by": review.get("reviewed_by"),
                "reviewed_at": review.get("reviewed_at"),
                "comment": review.get("comment"),
                "review_context": dict(review.get("review_context") or review_context),
                "approval_artifact_id": review.get("approval_artifact_id"),
            },
            "review_context": review_context,
            "validation_summary": {
                "count": len(validations),
                "last_result": proposal_metadata.get("last_validation"),
            },
            "apply_summary": {
                "count": len(applies),
                "last_result": proposal_metadata.get("last_apply"),
                "rollback_hints": list(proposal_metadata.get("rollback_hints") or []),
            },
            "history": list(proposal_metadata.get("history") or []),
            "workflow": {
                "state": workflow_state.get("state"),
                "transition_count": int(workflow_state.get("transition_count") or 0),
                "recovery_attempts": int(workflow_state.get("recovery_attempts") or 0),
                "timeout_seconds": int(workflow_state.get("timeout_seconds") or 0),
                "last_transition_at": workflow_state.get("last_transition_at"),
                "replay": workflow_replay,
                "timeout": workflow_timeout,
            },
            "mutation_approval": {
                "approval_id": (approval_artifact or {}).get("approval_id"),
                "status": (approval_artifact or {}).get("status"),
                "issued_by": (approval_artifact or {}).get("issued_by"),
                "issued_at": (approval_artifact or {}).get("issued_at"),
                "expires_at": (approval_artifact or {}).get("expires_at"),
                "mutation_class": (approval_artifact or {}).get("mutation_class"),
            },
            "provider_metadata": dict(proposal.provider_metadata or {}),
            "proposal_metadata": proposal_metadata,
            "created_at": proposal.created_at,
            "updated_at": proposal.updated_at,
        }

    def review_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        action: str,
        actor: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        repos = self._repositories or get_repository_registry()
        persisted = repos.evolution_proposal_repo.get_by_id(proposal_id)
        if persisted is None or str(persisted.task_id or "") != str(task_id):
            raise KeyError("evolution_proposal_not_found")
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"approve", "reject"}:
            raise ValueError("invalid_review_action")

        review_status = "approved" if normalized_action == "approve" else "rejected"
        proposal_metadata = dict(persisted.proposal_metadata or {})
        history = list(proposal_metadata.get("history") or [])
        workflow_service = get_critical_workflow_state_service()
        workflow_state = proposal_metadata.get("workflow_state")
        task_row = repos.task_repo.get_by_id(task_id)
        task_payload = task_row.model_dump() if task_row is not None else {"id": task_id, "goal_id": persisted.goal_id}
        review_context = self._build_review_context(
            task_payload=task_payload,
            proposal_id=persisted.id,
            task_id=task_id,
            goal_id=persisted.goal_id,
            target_refs=list(persisted.target_refs or []),
        )
        approval_artifact: dict[str, Any] | None = None
        now = time.time()
        if review_status == "approved":
            approval_artifact = self._issue_mutation_approval_artifact(
                task_payload=task_payload,
                proposal_id=persisted.id,
                task_id=task_id,
                goal_id=persisted.goal_id,
                trace_id=str(persisted.trace_id or task_payload.get("goal_trace_id") or "").strip() or None,
                actor=str(actor or "system"),
                review_context=review_context,
                target_refs=list(persisted.target_refs or []),
                now=now,
            )
            proposal_metadata["mutation_approval_artifact"] = approval_artifact
        else:
            proposal_metadata.pop("mutation_approval_artifact", None)
        review = {
            "required": bool(persisted.requires_review),
            "status": review_status,
            "reviewed_by": str(actor or "system"),
            "reviewed_at": now,
            "comment": str(comment or "").strip() or None,
            "review_context": review_context,
            "approval_artifact_id": (approval_artifact or {}).get("approval_id"),
        }
        history.append(
            {
                "event_type": "proposal_review",
                "action": normalized_action,
                "status": review_status,
                "actor": review["reviewed_by"],
                "comment": review["comment"],
                "timestamp": review["reviewed_at"],
                "review_context": review_context,
                "approval_artifact_id": (approval_artifact or {}).get("approval_id"),
            }
        )
        try:
            workflow_state = workflow_service.transition(
                workflow_state,
                workflow_type="evolution_proposal",
                to_state=review_status,
                reason=f"review_{normalized_action}",
                actor=review["reviewed_by"],
                task_id=task_id,
                goal_id=persisted.goal_id,
                trace_id=str(persisted.trace_id or "").strip() or None,
                details={"proposal_id": proposal_id},
            )
        except WorkflowTransitionError as exc:
            raise ValueError(exc.code) from None
        proposal_metadata["workflow_state"] = workflow_state
        proposal_metadata["review"] = review
        proposal_metadata["review_context"] = review_context
        proposal_metadata["history"] = history[-20:]
        persisted.proposal_metadata = proposal_metadata
        persisted.status = "approved" if review_status == "approved" else "rejected"
        saved = repos.evolution_proposal_repo.save(persisted)
        self._audit(
            "evolution_proposal_reviewed",
            {
                "task_id": task_id,
                "proposal_id": proposal_id,
                "action": normalized_action,
                "status": review_status,
                "actor": review["reviewed_by"],
                "approval_artifact_id": (approval_artifact or {}).get("approval_id"),
            },
        )
        return self.proposal_read_model(saved)

    def validate_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ValidationResult:
        repos = self._repositories or get_repository_registry()
        persisted = repos.evolution_proposal_repo.get_by_id(proposal_id)
        if persisted is None or str(persisted.task_id or "") != str(task_id):
            raise KeyError("evolution_proposal_not_found")
        context = self._context_builder.build_for_task(task_id)
        proposal = EvolutionProposal(
            proposal_id=persisted.id,
            title=persisted.title,
            description=persisted.description,
            proposal_type=persisted.proposal_type,
            target_refs=list(persisted.target_refs or []),
            rationale=persisted.rationale,
            risk_level=persisted.risk_level,
            confidence=persisted.confidence,
            requires_review=persisted.requires_review,
            provider_metadata=dict(persisted.provider_metadata or {}),
            raw_payload=persisted.raw_payload,
        )
        result = self._validate_fn(
            context,
            proposal,
            provider_name=provider_name or persisted.provider_name,
            config=config,
            trigger=trigger,
        )
        proposal_metadata = dict(persisted.proposal_metadata or {})
        history = list(proposal_metadata.get("history") or [])
        validations = list(proposal_metadata.get("validations") or [])
        validation_entry = result.model_dump(mode="json")
        validations.append(validation_entry)
        history.append(
            {
                "event_type": "proposal_validation",
                "status": result.status,
                "valid": bool(result.valid),
                "validation_id": result.validation_id,
                "timestamp": time.time(),
            }
        )
        proposal_metadata["last_validation"] = validation_entry
        proposal_metadata["validations"] = validations[-20:]
        proposal_metadata["history"] = history[-20:]
        persisted.proposal_metadata = proposal_metadata
        persisted.status = "validated" if result.valid else "validation_failed"
        repos.evolution_proposal_repo.save(persisted)
        return result

    def apply_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ApplyResult:
        repos = self._repositories or get_repository_registry()
        persisted = repos.evolution_proposal_repo.get_by_id(proposal_id)
        if persisted is None or str(persisted.task_id or "") != str(task_id):
            raise KeyError("evolution_proposal_not_found")
        proposal_metadata = dict(persisted.proposal_metadata or {})
        workflow_service = get_critical_workflow_state_service()
        workflow_state = proposal_metadata.get("workflow_state")
        review = dict(proposal_metadata.get("review") or {})
        trace_id = str(persisted.trace_id or "").strip() or None

        workflow_state = workflow_service.handle_timeout(
            workflow_state,
            workflow_type="evolution_proposal",
            reason="apply_timeout_precheck",
            actor="hub",
            trace_id=trace_id,
            task_id=task_id,
            goal_id=persisted.goal_id,
        )
        proposal_metadata["workflow_state"] = workflow_state

        if bool(persisted.requires_review) and str(review.get("status") or "").strip().lower() != "approved":
            workflow_state = workflow_service.apply_fallback(
                workflow_state,
                workflow_type="evolution_proposal",
                reason="apply_prerequisite_blocked",
                cause="evolution_apply_requires_approved_review",
                actor="hub",
                trace_id=trace_id,
                task_id=task_id,
                goal_id=persisted.goal_id,
                details={"proposal_id": proposal_id},
            )
            proposal_metadata["workflow_state"] = workflow_state
            persisted.proposal_metadata = proposal_metadata
            persisted.status = str(workflow_state.get("state") or persisted.status)
            repos.evolution_proposal_repo.save(persisted)
            raise PermissionError("evolution_apply_requires_approved_review")
        mutation_approval_artifact = _extract_mutation_approval_artifact(proposal_metadata)
        if bool(persisted.requires_review) and mutation_approval_artifact is None:
            workflow_state = workflow_service.apply_fallback(
                workflow_state,
                workflow_type="evolution_proposal",
                reason="apply_prerequisite_blocked",
                cause="evolution_apply_missing_mutation_approval",
                actor="hub",
                trace_id=trace_id,
                task_id=task_id,
                goal_id=persisted.goal_id,
                details={"proposal_id": proposal_id},
            )
            proposal_metadata["workflow_state"] = workflow_state
            persisted.proposal_metadata = proposal_metadata
            persisted.status = str(workflow_state.get("state") or persisted.status)
            repos.evolution_proposal_repo.save(persisted)
            raise PermissionError("evolution_apply_missing_mutation_approval")

        try:
            workflow_state = workflow_service.transition(
                workflow_state,
                workflow_type="evolution_proposal",
                to_state="apply_requested",
                reason="apply_requested",
                actor="hub",
                trace_id=trace_id,
                task_id=task_id,
                goal_id=persisted.goal_id,
                details={"proposal_id": proposal_id},
            )
            workflow_state = workflow_service.transition(
                workflow_state,
                workflow_type="evolution_proposal",
                to_state="apply_in_progress",
                reason="apply_execution_started",
                actor="hub",
                trace_id=trace_id,
                task_id=task_id,
                goal_id=persisted.goal_id,
                details={"proposal_id": proposal_id},
            )
        except WorkflowTransitionError as exc:
            raise PermissionError(exc.code) from None
        proposal_metadata["workflow_state"] = workflow_state
        context = self._context_builder.build_for_task(task_id)
        provider_metadata = dict(persisted.provider_metadata or {})
        if mutation_approval_artifact is not None:
            provider_metadata["mutation_approval_artifact"] = mutation_approval_artifact
        proposal = EvolutionProposal(
            proposal_id=persisted.id,
            title=persisted.title,
            description=persisted.description,
            proposal_type=persisted.proposal_type,
            target_refs=list(persisted.target_refs or []),
            rationale=persisted.rationale,
            risk_level=persisted.risk_level,
            confidence=persisted.confidence,
            requires_review=persisted.requires_review,
            provider_metadata=provider_metadata,
            raw_payload=persisted.raw_payload,
        )
        try:
            result = self._apply_fn(
                context,
                proposal,
                provider_name=provider_name or persisted.provider_name,
                config=config,
                trigger=trigger,
            )
        except Exception as exc:
            error_code = str(exc).strip() or type(exc).__name__
            try:
                if "timeout" in error_code.lower():
                    workflow_state = workflow_service.handle_timeout(
                        workflow_state,
                        workflow_type="evolution_proposal",
                        reason="apply_execution_timeout",
                        actor="hub",
                        trace_id=trace_id,
                        task_id=task_id,
                        goal_id=persisted.goal_id,
                    )
                else:
                    workflow_state = workflow_service.apply_fallback(
                        workflow_state,
                        workflow_type="evolution_proposal",
                        reason="apply_execution_fallback",
                        cause=error_code,
                        actor="hub",
                        trace_id=trace_id,
                        task_id=task_id,
                        goal_id=persisted.goal_id,
                        details={"proposal_id": proposal_id},
                    )
                proposal_metadata["workflow_state"] = workflow_state
                proposal_metadata["last_fallback"] = {"reason": "apply_execution_fallback", "cause": error_code, "timestamp": time.time()}
                persisted.proposal_metadata = proposal_metadata
                persisted.status = str(workflow_state.get("state") or persisted.status)
                repos.evolution_proposal_repo.save(persisted)
            except WorkflowTransitionError:
                pass
            raise
        applies = list(proposal_metadata.get("applies") or [])
        history = list(proposal_metadata.get("history") or [])
        apply_entry = result.model_dump(mode="json")
        applies.append(apply_entry)
        history.append(
            {
                "event_type": "proposal_apply",
                "status": result.status,
                "applied": bool(result.applied),
                "apply_id": result.apply_id,
                "timestamp": time.time(),
            }
        )
        rollback_hints = list(proposal_metadata.get("rollback_hints") or [])
        if not rollback_hints:
            rollback_hints = [
                "Apply bleibt hub-gesteuert; pruefe Audit-Trace, Artefakt-Referenzen und betroffene Targets vor Rollback.",
                "Rollback darf keine Worker-zu-Worker-Orchestrierung ausloesen.",
            ]
        try:
            workflow_state = workflow_service.transition(
                workflow_state,
                workflow_type="evolution_proposal",
                to_state="applied" if result.applied else "apply_prepared",
                reason="apply_execution_completed",
                actor="hub",
                trace_id=trace_id,
                task_id=task_id,
                goal_id=persisted.goal_id,
                details={"proposal_id": proposal_id, "apply_id": result.apply_id, "status": result.status},
            )
        except WorkflowTransitionError as exc:
            raise PermissionError(exc.code) from None
        proposal_metadata["workflow_state"] = workflow_state
        proposal_metadata["last_apply"] = apply_entry
        proposal_metadata["applies"] = applies[-20:]
        proposal_metadata["history"] = history[-20:]
        proposal_metadata["rollback_hints"] = rollback_hints
        persisted.proposal_metadata = proposal_metadata
        persisted.artifact_refs = list(result.artifact_refs or [])
        persisted.status = "applied" if result.applied else "apply_prepared"
        repos.evolution_proposal_repo.save(persisted)
        return result
