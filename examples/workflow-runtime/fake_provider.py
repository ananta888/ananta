"""Deterministic, credential-free provider used by the executable example.

The fixture is the source of every example response.  It never performs
network I/O and scopes attempts by operation so concurrent scenarios cannot
consume one another's retry sequence.
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any


class FakeProviderContractError(RuntimeError):
    """The checked-in provider fixture is invalid or exhausted."""


class DeterministicFakeProvider:
    def __init__(self, fixture: dict[str, Any]) -> None:
        if fixture.get("schema") != "ananta.fake_workflow_provider.v1":
            raise FakeProviderContractError("example_fake_provider_schema_invalid")
        if fixture.get("network_access") is not False:
            raise FakeProviderContractError("example_fake_provider_network_forbidden")
        raw_responses = fixture.get("responses")
        if not isinstance(raw_responses, dict) or not raw_responses:
            raise FakeProviderContractError("example_fake_provider_responses_missing")
        self._responses = {
            str(node_id): self._validated_sequence(str(node_id), values) for node_id, values in raw_responses.items()
        }
        self._attempts: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_default_fixture(cls) -> "DeterministicFakeProvider":
        path = Path(__file__).with_name("fake-provider.v1.json")
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def next_response(self, node_id: str, *, operation_scope: str) -> dict[str, Any]:
        normalized_node = str(node_id).strip()
        normalized_scope = str(operation_scope).strip()
        if not normalized_scope:
            raise FakeProviderContractError("example_fake_provider_scope_required")
        with self._lock:
            key = (normalized_scope, normalized_node)
            attempt = self._attempts.get(key, 0) + 1
            response = self.response_for(normalized_node, attempt=attempt)
            self._attempts[key] = attempt
            return response

    def response_for(self, node_id: str, *, attempt: int) -> dict[str, Any]:
        sequence = self._responses.get(str(node_id).strip())
        if sequence is None:
            raise FakeProviderContractError("example_fake_provider_node_unknown")
        if isinstance(attempt, bool) or attempt < 1 or attempt > len(sequence):
            raise FakeProviderContractError("example_fake_provider_attempt_exhausted")
        return deepcopy(sequence[attempt - 1])

    def successful_response(self, node_id: str) -> dict[str, Any]:
        sequence = self._responses.get(str(node_id).strip())
        if sequence is None:
            raise FakeProviderContractError("example_fake_provider_node_unknown")
        response = next(
            (value for value in sequence if value.get("status") == "completed"),
            None,
        )
        if response is None:
            raise FakeProviderContractError("example_fake_provider_success_missing")
        return deepcopy(response)

    @staticmethod
    def _validated_sequence(node_id: str, raw: object) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, list) or not raw:
            raise FakeProviderContractError("example_fake_provider_sequence_invalid")
        values: list[dict[str, Any]] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict) or item.get("attempt") != index:
                raise FakeProviderContractError("example_fake_provider_attempt_unstable")
            status = str(item.get("status") or "")
            if status not in {"completed", "failed"}:
                raise FakeProviderContractError("example_fake_provider_status_invalid")
            if status == "failed" and not str(item.get("reason_code") or ""):
                raise FakeProviderContractError("example_fake_provider_reason_required")
            if status == "completed" and not str(item.get("artifact_ref") or ""):
                raise FakeProviderContractError(f"example_fake_provider_artifact_required:{node_id}")
            values.append(deepcopy(item))
        return tuple(values)


__all__ = ["DeterministicFakeProvider", "FakeProviderContractError"]
