"""Authenticated claim, artifact-handle and result ingress for Spreadsheet Workers."""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_execution_queue_ports import (
    SpreadsheetExecutionQueuePort,
    SpreadsheetWorkerLeaseControlPort,
)
from agent.services.spreadsheet_observability_service import SpreadsheetCorrelation, SpreadsheetObservabilityService
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_worker_capability_service import SpreadsheetWorkerCapabilityService
from ananta_contracts.spreadsheet_studio import canonical_digest
from ananta_contracts.spreadsheet_studio_v2 import execution_snapshot


class SpreadsheetWorkerIngressService:
    def __init__(
        self,
        *,
        queue: SpreadsheetExecutionQueuePort,
        saga: SpreadsheetSagaService,
        artifacts: SpreadsheetArtifactStore,
        leases: SpreadsheetWorkerLeaseControlPort,
        capabilities: SpreadsheetWorkerCapabilityService,
        observability: SpreadsheetObservabilityService | None = None,
    ) -> None:
        self._queue = queue
        self._saga = saga
        self._artifacts = artifacts
        self._leases = leases
        self._capabilities = capabilities
        self._observability = observability

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
        if self._observability is not None:
            self._observe(
                operation="queue_wait",
                outcome="completed",
                reason_code="spreadsheet_assignment_claimed",
                correlation=self._correlation(job, assignment, attempt_id=callback_jti),
                duration_seconds=max(0.0, time.time() - float(job["created_at"])),
            )
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
            "snapshot": execution_snapshot(document["snapshot"]).to_dict(),
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
        ingress_started = time.monotonic()
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
            assignment = self._queue.get_assignment(tenant_id=str(claims["tenant_id"]), job_id=job_id)
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
            if self._observability is not None:
                self._observe(
                    operation="result_ingress",
                    outcome="replayed" if failed.get("replayed") else "failed",
                    reason_code=str(body["reason_code"]),
                    correlation=self._correlation(job, assignment, attempt_id=str(claims["jti"])),
                    duration_seconds=time.monotonic() - ingress_started,
                )
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
        timings = execution_result.pop("operation_durations_ms", None) if execution_result is not None else None
        if timings is not None:
            if (
                not isinstance(timings, Mapping)
                or set(timings) != {"render_recalc"}
                or isinstance(timings.get("render_recalc"), bool)
                or not isinstance(timings.get("render_recalc"), (int, float))
                or not 0 <= float(timings["render_recalc"]) <= 300_000
            ):
                raise ValueError("spreadsheet_worker_observability_invalid")
            if self._observability is not None:
                self._observe(
                    operation="render_recalc",
                    outcome="completed",
                    reason_code="spreadsheet_worker_execution_completed",
                    correlation=self._correlation(job, assignment, attempt_id=str(claims["jti"])),
                    duration_seconds=float(timings["render_recalc"]) / 1_000,
                )
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
        if self._observability is not None:
            self._observe(
                operation="result_ingress",
                outcome="completed",
                reason_code="spreadsheet_result_admitted",
                correlation=self._correlation(job, assignment, attempt_id=str(claims["jti"])),
                duration_seconds=time.monotonic() - ingress_started,
            )
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

    @staticmethod
    def _correlation(
        job: Mapping[str, Any],
        assignment: Mapping[str, Any],
        *,
        attempt_id: str,
    ) -> SpreadsheetCorrelation:
        proposal = dict(assignment.get("proposal") or {})
        return SpreadsheetCorrelation(
            task_id=str(job["job_id"]),
            worker_job_id=str(job["worker_job_id"]),
            attempt_id=attempt_id,
            document_id=str(job["document_id"]),
            candidate_id=str(proposal.get("proposal_id") or job["proposal_id"]),
        )

    def _observe(self, **values: Any) -> None:
        if self._observability is None:
            return
        try:
            self._observability.record(**values)
        except (RuntimeError, ValueError):
            # Result admission is authoritative; diagnostics can never reject it.
            return


__all__ = ["SpreadsheetWorkerIngressService"]
