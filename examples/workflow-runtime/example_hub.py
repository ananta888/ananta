"""Isolated deterministic Hub-port emulator for the executable example.

This process is intentionally not a production Hub.  It owns the example task
receipts and side-effect decisions so the Temporal worker can exercise the real
HTTP Activity gateway without calling another worker or a network provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from example_public_material import (
    EXAMPLE_KEY_ID,
    HUB_EVENTS_FILE,
    example_bearer,
    example_signing_key,
)
from fake_provider import DeterministicFakeProvider

from agent.services.workflow_runtime.security import HmacKeyRing, RuntimeAuthorizationEnvelope
from ananta_contracts.hub_task_gateway import HubTaskReceipt, RetryBudgetReceipt


class ExampleHubState:
    """Thread-safe task/ledger state owned by the example Hub process."""

    def __init__(
        self,
        evidence_dir: Path,
        *,
        provider: DeterministicFakeProvider | None = None,
    ) -> None:
        self._evidence_dir = evidence_dir
        self._lock = threading.RLock()
        self._attempts: dict[str, int] = {}
        self._receipts: dict[str, HubTaskReceipt] = {}
        self._retry_ids: set[str] = set()
        self._key_ring = HmacKeyRing(
            {EXAMPLE_KEY_ID: example_signing_key()},
            active_key_id=EXAMPLE_KEY_ID,
        )
        self._provider = provider or DeterministicFakeProvider.from_default_fixture()

    def submit(self, payload: dict[str, Any]) -> HubTaskReceipt:
        self._validate_command(payload)
        operation_id = str(payload["operation_id"])
        workflow_id = str(payload["workflow_id"])
        step_id = str(payload["step_id"])
        with self._lock:
            attempt = self._attempts.get(operation_id, 0) + 1
            self._attempts[operation_id] = attempt
            if "cancel" in workflow_id and step_id == "draft":
                receipt = _receipt(
                    _task_id(operation_id),
                    operation_id,
                    step_id=step_id,
                    status="running",
                )
            else:
                response = (
                    self._provider.next_response(
                        step_id,
                        operation_scope=operation_id,
                    )
                    if "failure" in workflow_id
                    else self._provider.successful_response(step_id)
                )
                if response["status"] == "failed":
                    reason_code = str(response["reason_code"])
                    self._record(
                        "task_submission_failed",
                        workflow_id=workflow_id,
                        step_id=step_id,
                        operation_id=operation_id,
                        attempt=attempt,
                        reason_code=reason_code,
                    )
                    raise ExampleHubError(reason_code, HTTPStatus.SERVICE_UNAVAILABLE)
                receipt = _receipt(
                    _task_id(operation_id),
                    operation_id,
                    step_id=step_id,
                    status="completed",
                    artifact_ref=str(response["artifact_ref"]),
                )
            task_id = receipt.hub_task_id
            self._receipts[task_id] = receipt
            self._record(
                "task_submitted",
                workflow_id=workflow_id,
                step_id=step_id,
                operation_id=operation_id,
                task_id=task_id,
                attempt=attempt,
                ledger_state=receipt.ledger_state,
            )
            return receipt

    def status(self, task_id: str, operation_id: str) -> HubTaskReceipt:
        with self._lock:
            receipt = self._receipts.get(task_id)
            if receipt is None or receipt.operation_id != operation_id:
                raise ExampleHubError("example_task_not_found", HTTPStatus.NOT_FOUND)
            return receipt

    def cancel(self, task_id: str, operation_id: str, reason: str) -> HubTaskReceipt:
        with self._lock:
            current = self.status(task_id, operation_id)
            cancelled = HubTaskReceipt(
                hub_task_id=current.hub_task_id,
                operation_id=current.operation_id,
                status="cancelled",
                authorization_state="valid",
                ledger_state="failed",
                canonical_event_refs=current.canonical_event_refs,
                reason_code="example_cancel_acknowledged",
            )
            self._receipts[task_id] = cancelled
            self._record(
                "task_cancelled",
                task_id=task_id,
                operation_id=operation_id,
                reason_code=str(reason or "temporal_activity_cancelled")[:128],
            )
            return cancelled

    def consume_retry(self, payload: dict[str, Any]) -> RetryBudgetReceipt:
        self._validate_command(payload)
        retry_id = str(payload.get("retry_id") or "")
        category = str(payload.get("retry_category") or "")
        maximum = int(payload.get("retry_budget_maximum") or 0)
        with self._lock:
            self._retry_ids.add(retry_id)
            used = min(len(self._retry_ids), maximum)
            receipt = RetryBudgetReceipt(
                retry_id=retry_id,
                category=category,
                used=used,
                maximum=maximum,
                remaining=max(0, maximum - used),
            )
            receipt.validate()
            self._record(
                "retry_consumed",
                workflow_id=str(payload["workflow_id"]),
                step_id=str(payload["step_id"]),
                retry_id=retry_id,
                category=category,
                remaining=receipt.remaining,
            )
            return receipt

    def _validate_command(self, payload: dict[str, Any]) -> None:
        required = (
            "operation_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "plan_hash",
            "authorization_envelope",
        )
        if any(not payload.get(field) for field in required):
            raise ExampleHubError("example_hub_command_incomplete", HTTPStatus.BAD_REQUEST)
        authorization = RuntimeAuthorizationEnvelope.from_mapping(dict(payload["authorization_envelope"]))
        authorization.verify(
            key_ring=self._key_ring,
            tenant_id=str(payload["tenant_id"]),
            workflow_id=str(payload["workflow_id"]),
            run_id=str(payload["run_id"]),
            step_id=str(payload["step_id"]),
            plan_hash=str(payload["plan_hash"]),
            # The production HTTP Activity command carries policy_version in
            # the signed envelope rather than duplicating it at top level.
            policy_version=authorization.policy_version,
            now=time.time(),
        )

    def _record(self, event: str, **values: Any) -> None:
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "ananta.workflow-runtime-example-hub-event.v1",
            "event": event,
            **{key: values[key] for key in sorted(values)},
        }
        with (self._evidence_dir / HUB_EVENTS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


class ExampleHubError(RuntimeError):
    def __init__(self, reason_code: str, status: HTTPStatus) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status = status


class ExampleHubHandler(BaseHTTPRequestHandler):
    server: "ExampleHubServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        if self.path == "/health":
            self._write(HTTPStatus.OK, {"status": "healthy", "classification": "example_only"})
            return
        self._write(HTTPStatus.NOT_FOUND, {"reason_code": "example_route_not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
        try:
            self._authorize()
            payload = self._read_json()
            if self.path == "/api/internal/workflow-runtime/tasks":
                receipt = self.server.state.submit(payload)
                self._write(HTTPStatus.OK, {"data": receipt.to_dict()})
                return
            if self.path == "/api/internal/workflow-runtime/retries":
                receipt = self.server.state.consume_retry(payload)
                self._write(HTTPStatus.OK, {"data": receipt.to_dict()})
                return
            prefix = "/api/internal/workflow-runtime/tasks/"
            suffix = "/commands"
            if self.path.startswith(prefix) and self.path.endswith(suffix):
                task_id = self.path[len(prefix) : -len(suffix)]
                operation_id = str(payload.get("operation_id") or "")
                if payload.get("command") == "status":
                    receipt = self.server.state.status(task_id, operation_id)
                elif payload.get("command") == "cancel":
                    receipt = self.server.state.cancel(
                        task_id,
                        operation_id,
                        str(payload.get("reason") or ""),
                    )
                else:
                    raise ExampleHubError("example_command_unsupported", HTTPStatus.BAD_REQUEST)
                self._write(HTTPStatus.OK, {"data": receipt.to_dict()})
                return
            raise ExampleHubError("example_route_not_found", HTTPStatus.NOT_FOUND)
        except ExampleHubError as exc:
            self._write(exc.status, {"data": {"reason_code": exc.reason_code}})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._write(
                HTTPStatus.BAD_REQUEST,
                {"data": {"reason_code": "example_hub_command_invalid"}},
            )

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _authorize(self) -> None:
        expected = f"Bearer {example_bearer()}"
        if self.headers.get("Authorization") != expected:
            raise ExampleHubError("example_hub_bearer_invalid", HTTPStatus.UNAUTHORIZED)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ExampleHubError("example_content_length_invalid", HTTPStatus.BAD_REQUEST) from exc
        if not 0 < length <= 1_048_576:
            raise ExampleHubError("example_request_size_invalid", HTTPStatus.BAD_REQUEST)
        decoded = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ExampleHubError("example_request_invalid", HTTPStatus.BAD_REQUEST)
        return decoded

    def _write(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ExampleHubServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state: ExampleHubState) -> None:
        self.state = state
        super().__init__(address, ExampleHubHandler)


def _task_id(operation_id: str) -> str:
    return "example-task-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:20]


def _receipt(
    task_id: str,
    operation_id: str,
    *,
    step_id: str,
    status: str,
    artifact_ref: str = "",
) -> HubTaskReceipt:
    terminal = status == "completed"
    artifacts = (
        (
            {
                "artifact_id": step_id,
                "artifact_ref": artifact_ref,
                "kind": "example_generated",
            },
        )
        if terminal
        else ()
    )
    return HubTaskReceipt(
        hub_task_id=task_id,
        operation_id=operation_id,
        status=status,
        authorization_state="valid",
        ledger_state="completed" if terminal else "started",
        artifact_refs=artifacts,
        canonical_event_refs=(f"example-event-{task_id}",),
        checkpoint_ref=f"example-checkpoint-{task_id}",
    )


def main() -> None:
    evidence_dir = Path(os.getenv("ANANTA_EXAMPLE_EVIDENCE_DIR", "/evidence"))
    host = str(os.getenv("ANANTA_EXAMPLE_HUB_HOST") or "0.0.0.0")
    port = int(os.getenv("ANANTA_EXAMPLE_HUB_PORT") or "8080")
    server = ExampleHubServer((host, port), ExampleHubState(evidence_dir))
    server.serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()


__all__ = ["ExampleHubError", "ExampleHubServer", "ExampleHubState", "main"]
