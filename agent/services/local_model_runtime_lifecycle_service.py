"""Hub-owned local runtime admission and activation lifecycle."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Callable, Protocol, Sequence, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from agent.repositories.local_model_runtime_decision import LocalRuntimeDecisionRepository
from agent.services.local_model_runtime_status_service import LocalResourceSnapshotPort
from agent.services.local_multi_model_runtime import LocalModelCapability, LocalModelPlacementPolicy
from ananta_contracts.local_model_runtime import (
    LocalRuntimeActivationDecision,
    LocalRuntimeControlReceipt,
)


class LocalRuntimeControlPort(Protocol):
    def apply(
        self,
        decision: LocalRuntimeActivationDecision,
        *,
        action: str,
    ) -> LocalRuntimeControlReceipt: ...


class _ReadableHttpResponse(Protocol):
    status: int

    def read(self) -> bytes: ...


class LocalRuntimeLifecycleService:
    """Serializes Hub admission and delegates only a digest-bound action."""

    def __init__(
        self,
        *,
        resources: LocalResourceSnapshotPort,
        decisions: LocalRuntimeDecisionRepository,
        capabilities: Sequence[LocalModelCapability] | None = None,
        control: LocalRuntimeControlPort | None = None,
        policy: LocalModelPlacementPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        audit_sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._resources = resources
        self._decisions = decisions
        self._capabilities = tuple(capabilities or ())
        self._control = control
        self._policy = policy or LocalModelPlacementPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit_sink = audit_sink or (lambda _action, _facts: None)
        self._lock = threading.RLock()

    def evaluate(
        self,
        *,
        request_id: str,
        capabilities: Sequence[LocalModelCapability],
    ) -> LocalRuntimeActivationDecision:
        normalized_request_id = str(request_id or "").strip().lower()
        with self._lock:
            existing = self._decisions.get_by_request_id(normalized_request_id)
            if existing is not None:
                return existing
            resources = self._resources.snapshot()
            placement = self._policy.decide(capabilities, resources)
            created_at = self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
            unsigned = {
                "request_id": normalized_request_id,
                "revision": 1,
                "admitted": placement.admitted,
                "reason_code": placement.reason_code,
                "start_order": list(placement.start_order),
                "effective_contexts": [
                    {"runtime_id": runtime_id, "context_tokens": context_tokens}
                    for runtime_id, context_tokens in sorted(placement.effective_contexts.items())
                ],
                "total_vram_bytes": resources.total_vram_bytes,
                "free_vram_bytes": resources.free_vram_bytes,
                "available_ram_bytes": resources.available_ram_bytes,
                "required_vram_bytes": placement.required_vram_bytes,
                "reserve_vram_bytes": placement.reserve_vram_bytes,
                "created_at": created_at,
            }
            digest = _digest(unsigned)
            decision = LocalRuntimeActivationDecision(
                decision_id=f"lrd-{digest[:32]}",
                decision_digest=digest,
                **unsigned,
            )
            persisted = self._decisions.save(decision)
            self._audit_sink(
                "local_runtime_activation_evaluated",
                {
                    "decision_id": persisted.decision_id,
                    "request_id": persisted.request_id,
                    "admitted": persisted.admitted,
                    "reason_code": persisted.reason_code,
                    "decision_digest": persisted.decision_digest,
                },
            )
            return persisted

    def apply(self, *, decision_id: str, action: str = "activate") -> LocalRuntimeControlReceipt:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"activate", "deactivate", "restart"}:
            raise ValueError("local_runtime_control_action_invalid")
        with self._lock, self._decisions.control_transaction():
            decision = self._decisions.get(str(decision_id or "").strip().lower())
            if decision is None:
                raise ValueError("local_runtime_decision_not_found")
            if normalized_action in {"activate", "restart"} and not decision.admitted:
                raise ValueError("local_runtime_activation_not_admitted")
            replay = self._decisions.get_control_receipt(decision.decision_id, normalized_action)
            if replay is not None:
                if replay.decision_digest != decision.decision_digest:
                    raise RuntimeError("local_runtime_control_receipt_binding_invalid")
                return replay
            if normalized_action in {"activate", "restart"}:
                self._revalidate_activation(decision)
            if self._control is None:
                raise RuntimeError("local_runtime_control_unconfigured")
            receipt = self._control.apply(decision, action=normalized_action)
            if receipt.decision_id != decision.decision_id or receipt.decision_digest != decision.decision_digest:
                raise RuntimeError("local_runtime_control_receipt_binding_invalid")
            if receipt.status != "completed":
                raise RuntimeError(f"local_runtime_control_{receipt.status}")
            self._decisions.save_control_receipt(receipt)
            self._audit_sink(
                "local_runtime_control_applied",
                {
                    "decision_id": decision.decision_id,
                    "action": normalized_action,
                    "status": receipt.status,
                    "reason_code": receipt.reason_code,
                },
            )
            return receipt

    def _revalidate_activation(self, decision: LocalRuntimeActivationDecision) -> None:
        if not self._capabilities:
            raise RuntimeError("local_runtime_capabilities_unconfigured")
        resources = self._resources.snapshot()
        current = self._policy.decide(self._capabilities, resources)
        current_contexts = dict(current.effective_contexts)
        decision_contexts = {item.runtime_id: item.context_tokens for item in decision.effective_contexts}
        if not current.admitted:
            self._audit_sink(
                "local_runtime_activation_revalidation_denied",
                {
                    "decision_id": decision.decision_id,
                    "reason_code": current.reason_code,
                },
            )
            raise ValueError(f"local_runtime_activation_revalidation_denied:{current.reason_code}")
        if current_contexts != decision_contexts:
            self._audit_sink(
                "local_runtime_activation_revalidation_denied",
                {
                    "decision_id": decision.decision_id,
                    "reason_code": "local_runtime_decision_stale",
                },
            )
            raise ValueError("local_runtime_decision_stale")

    def latest_decision(self) -> LocalRuntimeActivationDecision | None:
        return self._decisions.latest()


class HttpLocalRuntimeControl:
    """Least-authority adapter to an operator bridge with fixed actions only."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        from agent.services.local_model_runtime_status_service import _internal_base_url

        self._base_url = _internal_base_url(base_url)
        if len(str(token or "")) < 24:
            raise ValueError("local_runtime_control_token_invalid")
        self._token = str(token)
        self._opener = opener

    def apply(
        self,
        decision: LocalRuntimeActivationDecision,
        *,
        action: str,
    ) -> LocalRuntimeControlReceipt:
        if action not in {"activate", "deactivate", "restart"}:
            raise ValueError("local_runtime_control_action_invalid")
        body = json.dumps(
            {"action": action, "decision": decision.to_wire()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self._base_url}/v1/control",
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = cast(_ReadableHttpResponse, self._opener(request, timeout=120))
            if int(getattr(response, "status", 200) or 200) != 200:
                raise RuntimeError("local_runtime_control_failed")
            return cast(
                LocalRuntimeControlReceipt,
                LocalRuntimeControlReceipt.model_validate_json(response.read()),
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValidationError, ValueError, TypeError) as exc:
            raise RuntimeError("local_runtime_control_failed") from exc


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["HttpLocalRuntimeControl", "LocalRuntimeControlPort", "LocalRuntimeLifecycleService"]
