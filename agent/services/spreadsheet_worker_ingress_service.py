"""Authenticated claim, artifact-handle and result ingress for Spreadsheet Workers."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_execution_queue_ports import (
    SpreadsheetExecutionQueuePort,
    SpreadsheetWorkerLeaseControlPort,
)
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_worker_capability_service import SpreadsheetWorkerCapabilityService
from ananta_contracts.spreadsheet_studio import canonical_digest


class SpreadsheetWorkerIngressService:
    def __init__(
        self,
        *,
        queue: SpreadsheetExecutionQueuePort,
        saga: SpreadsheetSagaService,
        artifacts: SpreadsheetArtifactStore,
        leases: SpreadsheetWorkerLeaseControlPort,
        capabilities: SpreadsheetWorkerCapabilityService,
    ) -> None:
        self._queue = queue
        self._saga = saga
        self._artifacts = artifacts
        self._leases = leases
        self._capabilities = capabilities

    def claim(self, *, worker_id: str) -> dict[str, Any] | None:
        callback_jti = f"cap-{secrets.token_urlsafe(24)}"
        artifact_jti = f"cap-{secrets.token_urlsafe(24)}"
        claimed = self._queue.claim(
            worker_id=worker_id,
            callback_jti=callback_jti,
            artifact_handle_jti=artifact_jti,
        )
        if claimed is None:
            return None
        job, assignment = claimed
        try:
            self._leases.claim(job)
        except RuntimeError as exc:
            self._queue.fail_claim(
                tenant_id=str(assignment["tenant_id"]),
                job_id=str(job["job_id"]),
                reason_code=str(exc),
            )
            raise
        callback_token = self._capabilities.issue(
            scope="spreadsheet.result.submit",
            tenant_id=str(assignment["tenant_id"]),
            job=job,
            jti=callback_jti,
        )
        proposal = dict(assignment["proposal"])
        document = dict(assignment["document"])
        worker_assignment: dict[str, Any] = {
            "schema": "ananta.spreadsheet-worker-assignment.v1",
            "job_id": job["job_id"],
            "worker_job_id": job["worker_job_id"],
            "slot_lease_id": job["slot_lease_id"],
            "assignment_digest": job["assignment_digest"],
            "snapshot": document["snapshot"],
            "actions": proposal["actions"],
            "callback_token": callback_token,
            "human_intervention_required": False,
        }
        source = document.get("source_artifact")
        if isinstance(source, Mapping):
            worker_assignment["source_artifact_handle"] = {
                "token": self._capabilities.issue(
                    scope="spreadsheet.artifact.read",
                    tenant_id=str(assignment["tenant_id"]),
                    job=job,
                    jti=artifact_jti,
                ),
                "sha256": source.get("sha256"),
                "format": source.get("format"),
                "media_type": source.get("media_type"),
                "filename": f"{proposal['document_id']}.{source.get('format')}",
            }
        return worker_assignment

    def read_source_artifact(self, *, job_id: str, token: str) -> tuple[bytes, dict[str, Any]]:
        claims = self._capabilities.verify(token, scope="spreadsheet.artifact.read", job_id=job_id)
        job = self._queue.get(tenant_id=str(claims["tenant_id"]), job_id=job_id)
        self._require_claim_binding(claims, job)
        self._leases.require_live(job)
        assignment = self._queue.consume_artifact_handle(
            tenant_id=str(claims["tenant_id"]),
            job_id=job_id,
            jti=str(claims["jti"]),
        )
        source = dict(dict(assignment["document"]).get("source_artifact") or {})
        if not source:
            raise KeyError("spreadsheet_source_artifact_not_found")
        content = self._artifacts.read(
            tenant_id=str(claims["tenant_id"]),
            sha256=str(source.get("sha256") or ""),
            format=str(source.get("format") or ""),
        )
        return content, source

    def accept_result(self, *, job_id: str, token: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        if set(body) != {"status", "assignment_digest", "result", "result_digest", "reason_code"}:
            raise ValueError("spreadsheet_callback_fields_invalid")
        status = str(body.get("status") or "")
        if status not in {"completed", "failed"}:
            raise ValueError("spreadsheet_callback_status_invalid")
        execution_result: dict[str, Any] | None = None
        if status == "completed":
            if not isinstance(body.get("result"), Mapping) or body.get("reason_code") is not None:
                raise ValueError("spreadsheet_callback_result_invalid")
            execution_result = dict(body["result"])
            if canonical_digest(execution_result) != body.get("result_digest"):
                raise ValueError("spreadsheet_callback_result_digest_invalid")
        elif (
            body.get("result") is not None
            or body.get("result_digest") is not None
            or body.get("reason_code") != "spreadsheet_worker_execution_failed"
        ):
            raise ValueError("spreadsheet_callback_failure_invalid")
        claims = self._capabilities.verify(token, scope="spreadsheet.result.submit", job_id=job_id)
        job = self._queue.get(tenant_id=str(claims["tenant_id"]), job_id=job_id)
        self._require_claim_binding(claims, job)
        if body["assignment_digest"] != job["assignment_digest"]:
            raise ValueError("spreadsheet_callback_assignment_digest_invalid")
        callback_payload_digest = canonical_digest(body)
        if status == "failed":
            if job["status"] != "failed":
                self._leases.require_live(job)
            failed = self._queue.fail_execution(
                tenant_id=str(claims["tenant_id"]),
                job_id=job_id,
                callback_jti=str(claims["jti"]),
                reason_code=str(body["reason_code"]),
                callback_payload_digest=callback_payload_digest,
            )
            if not failed.get("replayed"):
                self._leases.finish(job, status="failed")
            return failed
        if job["status"] == "completed":
            return self._queue.complete(
                tenant_id=str(claims["tenant_id"]),
                job_id=job_id,
                callback_jti=str(claims["jti"]),
                result=dict(job["result"]),
                callback_payload_digest=callback_payload_digest,
            )
        self._leases.require_live(job)
        assignment = self._queue.get_assignment(tenant_id=str(claims["tenant_id"]), job_id=job_id)
        final_result = self._saga.finalize_proposal_execution(
            tenant_id=str(claims["tenant_id"]),
            prepared=assignment,
            execution=execution_result or {},
        )
        completed = self._queue.complete(
            tenant_id=str(claims["tenant_id"]),
            job_id=job_id,
            callback_jti=str(claims["jti"]),
            result=final_result,
            callback_payload_digest=callback_payload_digest,
        )
        self._leases.finish(job, status="completed")
        return completed

    @staticmethod
    def _require_claim_binding(claims: Mapping[str, Any], job: Mapping[str, Any]) -> None:
        if any(
            str(claims.get(field) or "") != str(job.get(field) or "")
            for field in (
                "job_id",
                "worker_job_id",
                "slot_lease_id",
                "worker_id",
                "assignment_digest",
            )
        ):
            raise ValueError("spreadsheet_capability_job_binding_invalid")


__all__ = ["SpreadsheetWorkerIngressService"]
