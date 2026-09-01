"""Minimal authenticated HTTP runtime for one isolated spreadsheet worker."""

from __future__ import annotations

import base64
import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ananta_contracts.spreadsheet_studio import SpreadsheetProposalV1, WorkbookSnapshotV1, canonical_digest
from worker.spreadsheet.libreoffice_executor import LibreOfficeSpreadsheetExecutor, SpreadsheetExecutionError

_BASE_PATH = "/internal/v1/spreadsheet"
_CONTRACT = "ananta.spreadsheet-worker.v1"
_MAX_REQUEST_BYTES = 64 * 1024 * 1024


class SpreadsheetWorkerApplication:
    def __init__(self, *, executor: LibreOfficeSpreadsheetExecutor, token: str) -> None:
        if len(token) < 24 or any(character.isspace() for character in token):
            raise RuntimeError("spreadsheet_worker_token_invalid")
        self.executor = executor
        self.token = token

    def handler(self):
        application = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AnantaSpreadsheetWorker/1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                if self.path == f"{_BASE_PATH}/capabilities":
                    self._json(HTTPStatus.OK, {"contract": _CONTRACT, **dict(application.executor.capability)})
                    return
                if self.path == "/health":
                    self._json(HTTPStatus.OK, {"status": "ready", "auth_configured": True})
                    return
                self._error(HTTPStatus.NOT_FOUND, "spreadsheet_worker_route_not_found", retryable=False)

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                if self.path not in {f"{_BASE_PATH}/dry-runs", f"{_BASE_PATH}/imports"}:
                    self._error(HTTPStatus.NOT_FOUND, "spreadsheet_worker_route_not_found", retryable=False)
                    return
                try:
                    body = self._body()
                    self._validate_envelope(body)
                    expected_operation = "dry_run" if self.path.endswith("/dry-runs") else "import_document"
                    if body["operation"] != expected_operation:
                        raise ValueError("spreadsheet_worker_operation_route_mismatch")
                    if body["operation"] == "dry_run":
                        snapshot = WorkbookSnapshotV1.from_mapping(body["snapshot"])
                        actions = SpreadsheetProposalV1.from_mapping(
                            {
                                "schema": SpreadsheetProposalV1.SCHEMA,
                                "proposal_id": "worker-envelope",
                                "document_id": "worker-document",
                                "expected_version": 1,
                                "base_snapshot_digest": snapshot.digest,
                                "actions": body["actions"],
                                "validators": [],
                                "automatic_promotion": False,
                            }
                        ).actions
                        result = application.executor.dry_run(snapshot=snapshot.to_dict(), actions=actions)
                    else:
                        try:
                            content = base64.b64decode(body["content_base64"], validate=True)
                        except (ValueError, TypeError) as exc:
                            raise ValueError("spreadsheet_worker_content_base64_invalid") from exc
                        result = application.executor.import_document(
                            content=content,
                            filename=body["filename"],
                            media_type=body["media_type"],
                            document_version_id=body["document_version_id"],
                        )
                except (KeyError, TypeError, ValueError) as exc:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc), retryable=False)
                    return
                except SpreadsheetExecutionError as exc:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc), retryable=True)
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "contract": _CONTRACT,
                        "request_digest": body["request_digest"],
                        "status": "succeeded",
                        "result": result,
                    },
                )

            def _validate_envelope(self, body: dict[str, Any]) -> None:
                fields_by_operation = {
                    "dry_run": {"contract", "operation", "snapshot", "actions", "request_digest"},
                    "import_document": {
                        "contract",
                        "operation",
                        "filename",
                        "media_type",
                        "document_version_id",
                        "content_base64",
                        "request_digest",
                    },
                }
                expected = fields_by_operation.get(str(body.get("operation")))
                if expected is None or set(body) != expected:
                    raise ValueError("spreadsheet_worker_envelope_fields_invalid")
                unsigned = {key: body[key] for key in expected if key != "request_digest"}
                if (
                    body["contract"] != _CONTRACT
                    or body["request_digest"] != canonical_digest(unsigned)
                ):
                    raise ValueError("spreadsheet_worker_envelope_binding_invalid")

            def _authorized(self) -> bool:
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {application.token}"
                if not hmac.compare_digest(supplied, expected):
                    self._error(HTTPStatus.UNAUTHORIZED, "spreadsheet_worker_unauthorized", retryable=False)
                    return False
                return True

            def _body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ValueError("spreadsheet_worker_content_length_invalid") from exc
                if not 1 <= length <= _MAX_REQUEST_BYTES:
                    raise ValueError("spreadsheet_worker_request_size_invalid")
                if "application/json" not in self.headers.get("Content-Type", "").lower():
                    raise ValueError("spreadsheet_worker_content_type_invalid")
                value = json.loads(self.rfile.read(length).decode())
                if not isinstance(value, dict):
                    raise ValueError("spreadsheet_worker_request_invalid")
                return value

            def _error(self, status: HTTPStatus, code: str, *, retryable: bool) -> None:
                self._json(status, {"error": {"code": code, "retryable": retryable}})

            def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
                encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
                self.send_response(status.value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

        return Handler


def create_application() -> SpreadsheetWorkerApplication:
    token = str(os.getenv("ANANTA_SPREADSHEET_WORKER_TOKEN") or "").strip()
    return SpreadsheetWorkerApplication(
        executor=LibreOfficeSpreadsheetExecutor(
            timeout_seconds=int(os.getenv("ANANTA_SPREADSHEET_WORKER_JOB_TIMEOUT_SECONDS", "90")),
            memory_bytes=int(os.getenv("ANANTA_SPREADSHEET_WORKER_MEMORY_BYTES", str(2 * 1024**3))),
            file_bytes=int(os.getenv("ANANTA_SPREADSHEET_WORKER_FILE_BYTES", str(256 * 1024**2))),
            network_isolated=str(os.getenv("ANANTA_SPREADSHEET_NETWORK_ISOLATED") or "").lower() == "true",
        ),
        token=token,
    )


def main() -> None:
    application = create_application()
    host = str(os.getenv("ANANTA_SPREADSHEET_WORKER_HOST") or "0.0.0.0")
    port = int(os.getenv("ANANTA_SPREADSHEET_WORKER_PORT", "8097"))
    ThreadingHTTPServer((host, port), application.handler()).serve_forever()


if __name__ == "__main__":
    main()
