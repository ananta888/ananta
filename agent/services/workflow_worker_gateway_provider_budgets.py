"""Provider budget reserve/reconcile commands of the workflow worker gateway.

``WorkflowWorkerProviderBudgetCommands`` is a collaborator composed by
``WorkflowWorkerGatewayService``; it owns the provider budget store and
depends on the authority and event recorder ports only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime import (
    ProviderAttemptScope,
    ProviderBudgetError,
    ProviderBudgetLimits,
    ProviderBudgetStore,
    ProviderProfileAttemptReservation,
    ProviderScopedBudgetReservation,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_worker_gateway_ports import (
    WorkflowWorkerAuthorityPort,
    WorkflowWorkerEventRecorderPort,
    WorkflowWorkerGatewayError,
)
from agent.services.workflow_worker_gateway_support import bounded_identifier, optional_bounded_text
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding


class WorkflowWorkerProviderBudgetCommands:
    """Hub-owned provider budget reservation and reconciliation."""

    def __init__(
        self,
        *,
        store: ProviderBudgetStore | None,
        authority: WorkflowWorkerAuthorityPort,
        events: WorkflowWorkerEventRecorderPort,
    ) -> None:
        self._store = store
        self._authority = authority
        self._events = events

    def reserve(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store()
        reservation_id = bounded_identifier(
            raw.get("reservation_id"),
            "provider_budget_reservation_id_invalid",
        )
        try:
            requested = ProviderBudgetLimits(
                maximum_attempts=int(str(raw.get("maximum_attempts"))),
                maximum_tokens=int(str(raw.get("maximum_tokens"))),
                maximum_cost_micros=int(str(raw.get("maximum_cost_micros"))),
            )
            reserved_tokens = int(str(raw.get("reserved_tokens")))
            reserved_cost = int(str(raw.get("reserved_cost_micros")))
            requested.assert_valid()
            if requested.maximum_attempts < 1:
                raise ProviderBudgetError(
                    "provider_budget_limits_invalid"
                )
        except (TypeError, ValueError, ProviderBudgetError) as exc:
            raise WorkflowWorkerGatewayError(
                "provider_budget_reservation_invalid",
                status_code=422,
            ) from exc
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        envelope = self._authority.verify(
            binding,
            raw,
            requested_budget={
                **(
                    {"tokens": requested.maximum_tokens}
                    if requested.maximum_tokens
                    else {}
                ),
                **(
                    {"cost_micros": requested.maximum_cost_micros}
                    if requested.maximum_cost_micros
                    else {}
                ),
            },
        )
        plan_entry = self._authorized_provider_plan_entry(
            envelope,
            raw,
        )
        self._assert_provider_reservation_within_node_cap(
            envelope=envelope,
            requested=requested,
            reserved_tokens=reserved_tokens,
            reserved_cost_micros=reserved_cost,
        )
        attempt_budget_name = (
            "provider_attempts"
            if "provider_attempts" in envelope.budgets
            else "attempts"
        )
        raw_attempt_limit = envelope.budgets.get(attempt_budget_name)
        if (
            raw_attempt_limit is None
            or isinstance(raw_attempt_limit, bool)
            or requested.maximum_attempts > int(raw_attempt_limit)
        ):
            raise WorkflowWorkerGatewayError(
                "authorization_budget_exceeded",
                status_code=403,
            )
        signed_attempt_limit = int(raw_attempt_limit)
        profile_attempt = None
        aggregate_reservation_id = reservation_id
        if plan_entry is not None:
            profile_attempt = self._provider_profile_attempt_reservation(
                envelope=envelope,
                binding=binding,
                attempt_id=attempt_id,
                current_profile_id=plan_entry.profile_id,
                reservation_id=reservation_id,
            )
            aggregate_reservation_id = (
                self._aggregate_provider_reservation_id(
                    binding=binding,
                    attempt_id=attempt_id,
                    profile_id=plan_entry.profile_id,
                    provider_binding_id=plan_entry.binding_id,
                    reservation_id=reservation_id,
                )
            )
        limits = ProviderBudgetLimits(
            # A signed profile route owns call-count enforcement in the
            # step/attempt/profile-scoped retry ledger above. The provider
            # store remains run-aggregate for tokens and cost only.
            maximum_attempts=(
                0 if plan_entry is not None else signed_attempt_limit
            ),
            maximum_tokens=int(
                envelope.budgets.get(
                    "provider_run_tokens",
                    envelope.budgets.get("tokens", 0),
                )
            ),
            maximum_cost_micros=int(
                envelope.budgets.get(
                    "provider_run_cost_micros",
                    envelope.budgets.get("cost_micros", 0),
                )
            ),
        )
        scoped_budget = self._provider_node_budget_reservation(
            envelope=envelope,
            binding=binding,
            attempt_id=attempt_id,
        )
        try:
            snapshot = store.reserve(
                tenant_id=binding.tenant_id,
                run_id=binding.run_id,
                policy_version=binding.policy_version,
                reservation_id=aggregate_reservation_id,
                limits=limits,
                reserved_tokens=reserved_tokens,
                reserved_cost_micros=reserved_cost,
                profile_attempt=profile_attempt,
                scoped_budget=scoped_budget,
            )
        except ProviderBudgetError as exc:
            raise WorkflowWorkerGatewayError(exc.reason_code, status_code=409) from exc
        self._events.append(
            binding,
            event_type="workflow.budget.provider_reserved",
            dedupe_key=f"provider-budget-reserve:{reservation_id}",
            causation_id=reservation_id,
            payload={
                "reservation_id": reservation_id,
                "attempt_id": attempt_id,
                "fencing_token": fencing_token,
                "attempts": (
                    snapshot.profile_attempts
                    if snapshot.profile_attempts is not None
                    else snapshot.attempts
                ),
                "tokens": snapshot.tokens,
                "cost_micros": snapshot.cost_micros,
            },
        )
        return self._provider_budget_receipt(
            snapshot,
            reservation_id=reservation_id,
        )

    def reconcile(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        store = self._require_store()
        reservation_id = bounded_identifier(
            raw.get("reservation_id"),
            "provider_budget_reservation_id_invalid",
        )
        try:
            actual_total_tokens = int(str(raw.get("actual_total_tokens")))
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerGatewayError(
                "provider_budget_actual_tokens_invalid",
                status_code=422,
            ) from exc
        attempt_id, fencing_token = self._authority.ownership_binding(binding, raw)
        envelope = self._authority.verify(binding, raw)
        plan_entry = self._authorized_provider_plan_entry(
            envelope,
            raw,
        )
        aggregate_reservation_id = reservation_id
        profile_attempt = None
        if plan_entry is not None:
            profile_attempt = self._provider_profile_attempt_reservation(
                envelope=envelope,
                binding=binding,
                attempt_id=attempt_id,
                current_profile_id=plan_entry.profile_id,
                reservation_id=reservation_id,
            )
            aggregate_reservation_id = (
                self._aggregate_provider_reservation_id(
                    binding=binding,
                    attempt_id=attempt_id,
                    profile_id=plan_entry.profile_id,
                    provider_binding_id=plan_entry.binding_id,
                    reservation_id=reservation_id,
                )
            )
        scoped_budget = self._provider_node_budget_reservation(
            envelope=envelope,
            binding=binding,
            attempt_id=attempt_id,
        )
        try:
            snapshot = store.reconcile(
                tenant_id=binding.tenant_id,
                run_id=binding.run_id,
                policy_version=binding.policy_version,
                reservation_id=aggregate_reservation_id,
                actual_total_tokens=actual_total_tokens,
                profile_attempt=profile_attempt,
                scoped_budget=scoped_budget,
            )
        except ProviderBudgetError as exc:
            raise WorkflowWorkerGatewayError(exc.reason_code, status_code=409) from exc
        self._events.append(
            binding,
            event_type="workflow.budget.provider_reconciled",
            dedupe_key=f"provider-budget-reconcile:{reservation_id}",
            causation_id=reservation_id,
            payload={
                "reservation_id": reservation_id,
                "attempt_id": attempt_id,
                "fencing_token": fencing_token,
                "tokens": snapshot.tokens,
                "cost_micros": snapshot.cost_micros,
                "reason_code": snapshot.reason_code,
                "scoped_budget_overrun": (
                    snapshot.scoped_budget_overrun
                ),
            },
        )
        return self._provider_budget_receipt(
            snapshot,
            reservation_id=reservation_id,
        )

    def _authorized_provider_plan_entry(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        raw: Mapping[str, Any],
    ):
        """Match only an exact provider identity signed by the Hub.

        Empty allowlists are the explicit compatibility path for envelopes
        issued before provider identities became part of the signature.
        """

        plan_entry = None
        if envelope.allowed_provider_bindings:
            binding_id = bounded_identifier(
                raw.get("provider_binding_id"),
                "provider_authorization_binding_required",
            )
            provider_id = bounded_identifier(
                raw.get("provider_id"),
                "provider_authorization_binding_required",
            )
            model_id = bounded_identifier(
                raw.get("model_id"),
                "provider_authorization_binding_required",
            )
            endpoint_identity = optional_bounded_text(
                raw.get("provider_endpoint_identity"),
                "provider_authorization_endpoint_invalid",
                maximum=1024,
            )
            signed_binding = next(
                (
                    item
                    for item in envelope.allowed_provider_bindings
                    if item.binding_id == binding_id
                    and item.provider_id == provider_id
                    and item.model_id == model_id
                    and item.endpoint_identity == endpoint_identity
                ),
                None,
            )
            if signed_binding is None:
                raise WorkflowWorkerGatewayError(
                    "provider_authorization_binding_denied",
                    status_code=403,
                )
            if envelope.provider_attempt_plan:
                profile_id = bounded_identifier(
                    raw.get("provider_profile_id"),
                    "provider_authorization_profile_required",
                )
                plan_entry = next(
                    (
                        item
                        for item in envelope.provider_attempt_plan
                        if item.profile_id == profile_id
                    ),
                    None,
                )
                if (
                    plan_entry is None
                    or plan_entry.binding_id != binding_id
                    or plan_entry.provider_id != provider_id
                    or plan_entry.model_id != model_id
                    or plan_entry.endpoint_identity != endpoint_identity
                ):
                    raise WorkflowWorkerGatewayError(
                        "provider_authorization_profile_denied",
                        status_code=403,
                    )
        return plan_entry

    def _provider_profile_attempt_reservation(
        self,
        *,
        envelope: RuntimeAuthorizationEnvelope,
        binding: WorkflowWorkerBinding,
        attempt_id: str,
        current_profile_id: str,
        reservation_id: str,
    ) -> ProviderProfileAttemptReservation:
        predecessors: list[ProviderAttemptScope] = []
        for profile_index, entry in enumerate(
            envelope.provider_attempt_plan
        ):
            if entry.profile_id == current_profile_id:
                value = ProviderProfileAttemptReservation(
                    current=ProviderAttemptScope(
                        scope_id=self._provider_attempt_scope(
                            binding=binding,
                            attempt_id=attempt_id,
                            profile_id=entry.profile_id,
                            profile_index=profile_index,
                            provider_binding_id=entry.binding_id,
                            maximum_attempts=entry.maximum_attempts,
                        ),
                        maximum_attempts=entry.maximum_attempts,
                    ),
                    reservation_id=reservation_id,
                    predecessors=tuple(predecessors),
                )
                value.assert_valid()
                return value
            predecessors.append(
                ProviderAttemptScope(
                    scope_id=self._provider_attempt_scope(
                        binding=binding,
                        attempt_id=attempt_id,
                        profile_id=entry.profile_id,
                        profile_index=profile_index,
                        provider_binding_id=entry.binding_id,
                        maximum_attempts=entry.maximum_attempts,
                    ),
                    maximum_attempts=entry.maximum_attempts,
                )
            )
        raise WorkflowWorkerGatewayError(
            "provider_authorization_profile_denied",
            status_code=403,
        )

    @staticmethod
    def _assert_provider_reservation_within_node_cap(
        *,
        envelope: RuntimeAuthorizationEnvelope,
        requested: ProviderBudgetLimits,
        reserved_tokens: int,
        reserved_cost_micros: int,
    ) -> None:
        if min(reserved_tokens, reserved_cost_micros) < 0:
            raise WorkflowWorkerGatewayError(
                "provider_budget_reservation_invalid",
                status_code=422,
            )
        signed_tokens = envelope.budgets.get("tokens")
        signed_cost = envelope.budgets.get("cost_micros")
        if (
            (
                signed_tokens is not None
                and not isinstance(signed_tokens, bool)
                and reserved_tokens > int(signed_tokens)
            )
            or (
                signed_cost is not None
                and not isinstance(signed_cost, bool)
                and reserved_cost_micros > int(signed_cost)
            )
            or (
                requested.maximum_tokens
                and reserved_tokens > requested.maximum_tokens
            )
            or (
                requested.maximum_cost_micros
                and reserved_cost_micros
                > requested.maximum_cost_micros
            )
        ):
            raise WorkflowWorkerGatewayError(
                "authorization_budget_exceeded",
                status_code=403,
            )

    @staticmethod
    def _provider_attempt_scope(
        *,
        binding: WorkflowWorkerBinding,
        attempt_id: str,
        profile_id: str,
        profile_index: int,
        provider_binding_id: str,
        maximum_attempts: int,
    ) -> str:
        rendered = json.dumps(
            [
                binding.run_id,
                binding.step_id,
                binding.plan_hash,
                binding.policy_version,
                attempt_id,
                profile_id,
                profile_index,
                provider_binding_id,
                maximum_attempts,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "provider-attempt-scope:"
            + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        )

    @staticmethod
    def _provider_node_budget_reservation(
        *,
        envelope: RuntimeAuthorizationEnvelope,
        binding: WorkflowWorkerBinding,
        attempt_id: str,
    ) -> ProviderScopedBudgetReservation:
        rendered = json.dumps(
            [
                binding.run_id,
                binding.step_id,
                binding.plan_hash,
                binding.policy_version,
                attempt_id,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        value = ProviderScopedBudgetReservation(
            scope_id=(
                "provider-node-budget:"
                + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            ),
            limits=ProviderBudgetLimits(
                maximum_attempts=0,
                maximum_tokens=int(envelope.budgets.get("tokens", 0)),
                maximum_cost_micros=int(
                    envelope.budgets.get("cost_micros", 0)
                ),
            ),
        )
        value.assert_valid()
        return value

    @staticmethod
    def _aggregate_provider_reservation_id(
        *,
        binding: WorkflowWorkerBinding,
        attempt_id: str,
        profile_id: str,
        provider_binding_id: str,
        reservation_id: str,
    ) -> str:
        rendered = json.dumps(
            [
                binding.run_id,
                binding.step_id,
                attempt_id,
                profile_id,
                provider_binding_id,
                reservation_id,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "provider-reservation:"
            + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        )

    @staticmethod
    def _provider_budget_receipt(
        snapshot,
        *,
        reservation_id: str,
    ) -> dict[str, Any]:
        payload = dict(snapshot.to_dict())
        payload["reservation_id"] = reservation_id
        if snapshot.profile_attempts is not None:
            payload.update(
                {
                    "attempts": snapshot.profile_attempts,
                    "maximum_attempts": (
                        snapshot.profile_maximum_attempts
                    ),
                    "remaining_attempts": max(
                        0,
                        int(snapshot.profile_maximum_attempts or 0)
                        - int(snapshot.profile_attempts),
                    ),
                }
            )
        return payload

    def _require_store(self) -> ProviderBudgetStore:
        if self._store is None:
            raise WorkflowWorkerGatewayError(
                "provider_budget_store_unavailable",
                status_code=503,
            )
        return self._store
