"""Execution-only Spreadsheet Worker that polls the Hub-owned task queue."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ananta_contracts.spreadsheet_studio import canonical_digest
from worker.spreadsheet.libreoffice_executor import LibreOfficeSpreadsheetExecutor

_ASSIGNMENT_FIELDS = {
    "schema",
    "job_id",
    "worker_job_id",
    "slot_lease_id",
    "assignment_digest",
    "snapshot",
    "actions",
    "callback_token",
    "human_intervention_required",
}


class SpreadsheetQueueWorkerError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise SpreadsheetQueueWorkerError("spreadsheet_hub_redirect_forbidden")


class SpreadsheetQueueWorker:
    def __init__(
        self,
        *,
        hub_endpoint: str,
        worker_id: str,
        worker_token: str,
        executor: LibreOfficeSpreadsheetExecutor,
        opener: Any | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(hub_endpoint or "").strip().rstrip("/"))
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api/spreadsheet-studio/internal"
        ):
            raise SpreadsheetQueueWorkerError("spreadsheet_hub_endpoint_invalid")
        if len(str(worker_token or "")) < 24 or any(character.isspace() for character in worker_token):
            raise SpreadsheetQueueWorkerError("spreadsheet_worker_token_invalid")
        if not str(worker_id or "").strip():
            raise SpreadsheetQueueWorkerError("spreadsheet_worker_id_invalid")
        self._base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self._worker_id = str(worker_id).strip()
        self._worker_token = str(worker_token)
        self._executor = executor
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        self._timeout = float(timeout_seconds)
        self._pending_callback: tuple[str, str, dict[str, Any]] | None = None

    def run_once(self) -> bool:
        if self._pending_callback is not None:
            self._submit_pending_callback()
            return True
        status, payload = self._json_request(
            "POST",
            "/jobs/claim",
            token=self._worker_token,
            body={"worker_id": self._worker_id},
        )
        if status == 204:
            return False
        if status != 200 or not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
            raise SpreadsheetQueueWorkerError("spreadsheet_claim_response_invalid")
        assignment = dict(payload["data"])
        allowed = set(_ASSIGNMENT_FIELDS)
        if "source_artifact_handle" in assignment:
            allowed.add("source_artifact_handle")
        if set(assignment) != allowed or assignment.get("schema") != "ananta.spreadsheet-worker-assignment.v1":
            raise SpreadsheetQueueWorkerError("spreadsheet_worker_assignment_invalid")
        try:
            execution_started = time.monotonic()
            source_input = self._source_artifact(assignment)
            result = dict(
                self._executor.dry_run(
                    snapshot=assignment["snapshot"],
                    actions=tuple(assignment["actions"]),
                    **({"source_artifact": source_input} if source_input is not None else {}),
                )
            )
            result["operation_durations_ms"] = {
                "render_recalc": round((time.monotonic() - execution_started) * 1_000, 3)
            }
        except Exception:
            callback = {
                "status": "failed",
                "assignment_digest": assignment["assignment_digest"],
                "result": None,
                "result_digest": None,
                "reason_code": "spreadsheet_worker_execution_failed",
            }
        else:
            callback = {
                "status": "completed",
                "assignment_digest": assignment["assignment_digest"],
                "result": result,
                "result_digest": canonical_digest(result),
                "reason_code": None,
            }
        self._pending_callback = (
            str(assignment["job_id"]),
            str(assignment["callback_token"]),
            callback,
        )
        self._submit_pending_callback()
        return True

    def _submit_pending_callback(self) -> None:
        if self._pending_callback is None:
            return
        job_id, token, callback = self._pending_callback
        callback_status, callback_payload = self._json_request(
            "POST",
            f"/jobs/{job_id}/result",
            token=token,
            body=callback,
        )
        if callback_status != 200 or not isinstance(callback_payload, Mapping):
            raise SpreadsheetQueueWorkerError("spreadsheet_callback_rejected")
        self._pending_callback = None

    def _source_artifact(self, assignment: Mapping[str, Any]) -> dict[str, Any] | None:
        raw = assignment.get("source_artifact_handle")
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or set(raw) != {
            "token",
            "sha256",
            "format",
            "media_type",
            "filename",
        }:
            raise SpreadsheetQueueWorkerError("spreadsheet_artifact_handle_invalid")
        status, content, headers = self._binary_request(
            "GET",
            f"/jobs/{assignment['job_id']}/artifact",
            token=str(raw["token"]),
        )
        digest = hashlib.sha256(content).hexdigest()
        if status != 200 or digest != raw.get("sha256") or headers.get("X-Content-SHA256") != digest:
            raise SpreadsheetQueueWorkerError("spreadsheet_artifact_handle_digest_invalid")
        return {
            "content": content,
            "filename": raw["filename"],
            "media_type": raw["media_type"],
            "sha256": digest,
        }

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        body: Mapping[str, Any],
    ) -> tuple[int, Any]:
        encoded = json.dumps(dict(body), separators=(",", ":"), allow_nan=False).encode()
        request = urllib.request.Request(
            self._base + path,
            data=encoded,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        status = int(response.status)
        if status == 204:
            return status, None
        if "application/json" not in str(response.headers.get("Content-Type") or ""):
            raise SpreadsheetQueueWorkerError("spreadsheet_hub_response_invalid")
        raw = response.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise SpreadsheetQueueWorkerError("spreadsheet_hub_response_too_large")
        try:
            return status, json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise SpreadsheetQueueWorkerError("spreadsheet_hub_response_invalid") from exc

    def _binary_request(self, method: str, path: str, *, token: str) -> tuple[int, bytes, Mapping[str, str]]:
        request = urllib.request.Request(
            self._base + path,
            method=method,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"},
        )
        try:
            response = self._opener.open(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        content = response.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise SpreadsheetQueueWorkerError("spreadsheet_artifact_response_too_large")
        return int(response.status), content, response.headers


def create_worker() -> SpreadsheetQueueWorker:
    return SpreadsheetQueueWorker(
        hub_endpoint=str(os.getenv("ANANTA_SPREADSHEET_HUB_ENDPOINT") or ""),
        worker_id=str(os.getenv("ANANTA_SPREADSHEET_WORKER_ID") or "spreadsheet-worker"),
        worker_token=str(os.getenv("ANANTA_SPREADSHEET_WORKER_TOKEN") or ""),
        executor=LibreOfficeSpreadsheetExecutor(
            timeout_seconds=int(os.getenv("ANANTA_SPREADSHEET_WORKER_JOB_TIMEOUT_SECONDS", "90")),
            memory_bytes=int(os.getenv("ANANTA_SPREADSHEET_WORKER_MEMORY_BYTES", str(2 * 1024**3))),
            file_bytes=int(os.getenv("ANANTA_SPREADSHEET_WORKER_FILE_BYTES", str(256 * 1024**2))),
            network_isolated=str(os.getenv("ANANTA_SPREADSHEET_NETWORK_ISOLATED") or "").lower() == "true",
        ),
    )


def main() -> None:
    worker = create_worker()
    Path("/tmp/ananta-spreadsheet-ready").write_text("ready\n")
    while True:
        try:
            worked = worker.run_once()
        except Exception:
            worked = False
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()


__all__ = ["SpreadsheetQueueWorker", "SpreadsheetQueueWorkerError", "create_worker"]
