"""Hub-controlled boundary for Unsloth Studio reads and mutation task submission."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.unsloth_studio_transport import UnslothStudioTransport

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


class UnslothStudioWorkerAdapterError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class UnslothHubTaskCommand:
    command_type: str
    tenant_id: str
    actor_id: str
    idempotency_key: str
    payload: Mapping[str, Any]
    schema: str = "ananta.unsloth_hub_task_command.v1"


class HubTaskCommandPort(Protocol):
    """Focused Hub port; implementations enqueue work and own deduplication."""

    def submit(self, command: UnslothHubTaskCommand) -> Mapping[str, Any]:
        ...


class HubTaskSubmissionPort(Protocol):
    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        ...


class HubTaskSubmissionCommandAdapter:
    """Adapts the generic Hub queue port to Studio's focused command port."""

    def __init__(self, submission_port: HubTaskSubmissionPort) -> None:
        self._submission_port = submission_port

    def submit(self, command: UnslothHubTaskCommand) -> Mapping[str, Any]:
        payload = {
            **dict(command.payload),
            "schema": command.schema,
            "actor_id": command.actor_id,
        }
        task_id = self._submission_port.submit(
            task_type=command.command_type,
            tenant_id=command.tenant_id,
            payload=payload,
            idempotency_key=command.idempotency_key,
        )
        if _IDENTIFIER_RE.fullmatch(str(task_id or "")) is None:
            raise UnslothStudioWorkerAdapterError(
                "unsloth_hub_task_receipt_invalid"
            )
        return {
            "task_id": str(task_id),
            "status": "queued",
            "correlation_id": payload.get("correlation_id"),
        }


class UnslothStudioWorkerAdapter:
    """Never executes mutations; it submits them to the injected Hub task port."""

    def __init__(
        self,
        *,
        transport: UnslothStudioTransport,
        hub_task_commands: HubTaskCommandPort,
        allowed_mutations: tuple[str, ...],
    ) -> None:
        normalized = frozenset(_validated_identifier(value, "mutation") for value in allowed_mutations)
        if not normalized:
            raise ValueError("unsloth_mutation_allowlist_required")
        self._transport = transport
        self._hub_task_commands = hub_task_commands
        self._allowed_mutations = normalized

    def health(self) -> Mapping[str, Any]:
        return self.probe()

    def capabilities(self) -> Mapping[str, Any]:
        probe = dict(self.probe())
        return {
            "schema_version": "ananta.unsloth-studio-capabilities.v1",
            "available": True,
            "reason_code": None,
            "studio_version": probe.get("studio_version"),
            "unsloth_version": probe.get("unsloth_version"),
            "operations": ["health", "status"],
        }

    def probe(self) -> Mapping[str, Any]:
        return self._transport.probe()

    def submit_mutation(
        self,
        *,
        mutation: str,
        tenant_id: str,
        actor_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        normalized_mutation = _validated_identifier(mutation, "mutation")
        if normalized_mutation not in self._allowed_mutations:
            raise UnslothStudioWorkerAdapterError(
                "unsloth_mutation_not_allowlisted"
            )
        normalized_tenant = _validated_identifier(tenant_id, "tenant")
        normalized_actor = _validated_identifier(actor_id, "actor")
        if _IDEMPOTENCY_KEY_RE.fullmatch(str(idempotency_key or "")) is None:
            raise UnslothStudioWorkerAdapterError(
                "unsloth_mutation_idempotency_key_invalid"
            )
        command = UnslothHubTaskCommand(
            command_type=f"unsloth.mcp.{normalized_mutation}",
            tenant_id=normalized_tenant,
            actor_id=normalized_actor,
            idempotency_key=str(idempotency_key),
            payload=dict(payload),
        )
        receipt = self._hub_task_commands.submit(command)
        if not isinstance(receipt, Mapping):
            raise UnslothStudioWorkerAdapterError(
                "unsloth_hub_task_receipt_invalid"
            )
        return dict(receipt)


def _validated_identifier(value: str, kind: str) -> str:
    normalized = str(value or "").strip()
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise UnslothStudioWorkerAdapterError(f"unsloth_{kind}_id_invalid")
    return normalized


__all__ = [
    "HubTaskCommandPort",
    "HubTaskSubmissionCommandAdapter",
    "HubTaskSubmissionPort",
    "UnslothHubTaskCommand",
    "UnslothStudioWorkerAdapter",
    "UnslothStudioWorkerAdapterError",
]
