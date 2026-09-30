"""Adapt the worker-local rag-helper index service to the execution port."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from worker.retrieval.knowledge_index_artifact_publisher import (
    WorkerKnowledgeIndexArtifactPublisher,
)
from worker.retrieval.knowledge_index_execution_guard import (
    KnowledgeIndexExecutionDeadlinePort,
)
from worker.retrieval.knowledge_index_job_contract import (
    BOUND_JOB_SCHEMA,
    MAX_PAYLOAD_BYTES,
    PAYLOAD_MEDIA_TYPE,
    SOURCE_ACCESS_MANIFEST_FIELD,
    KnowledgeIndexArtifactPublisherPort,
    KnowledgeIndexGraphArtifactMaterializerPort,
    KnowledgeIndexPayloadLoaderPort,
)
from worker.retrieval.knowledge_index_payload_loader import (
    HubArtifactKnowledgeIndexPayloadLoader,
)


class RagHelperKnowledgeIndexExecution:
    """Adapt the worker-local rag-helper service to the narrow execution port."""

    def __init__(
        self,
        index_service: Any,
        *,
        payload_loader: KnowledgeIndexPayloadLoaderPort | None = None,
        artifact_publisher: KnowledgeIndexArtifactPublisherPort | None = None,
        graph_artifact_materializer: KnowledgeIndexGraphArtifactMaterializerPort | None = None,
    ) -> None:
        self._index_service = index_service
        self._payload_loader = payload_loader
        self._artifact_publisher = artifact_publisher
        self._graph_artifact_materializer = graph_artifact_materializer

    def execute(
        self,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> Mapping[str, Any]:
        self._checkpoint(execution_deadline)
        payload = self._resolve_payload(
            job,
            execution_deadline=execution_deadline,
        )
        self._checkpoint(execution_deadline)
        deadline_kwargs = (
            {"execution_deadline": execution_deadline}
            if execution_deadline is not None
            else {}
        )
        job_type = str(job.get("job_type") or "")
        if job_type == "artifact":
            knowledge_index, run = self._index_service.index_artifact(
                str(payload.get("artifact_id") or ""),
                created_by=self._created_by(job),
                profile_name=self._profile_name(job),
                profile_overrides=dict(payload.get("profile_overrides") or {}),
                **deadline_kwargs,
            )
            self._checkpoint(execution_deadline)
            return self._single_result(
                job,
                payload,
                knowledge_index,
                run,
                execution_deadline=execution_deadline,
            )
        if job_type == "collection":
            results: list[dict[str, Any]] = []
            artifact_refs: list[dict[str, Any]] = []
            overall_status = "completed"
            for artifact_id in list(payload.get("artifact_ids") or []):
                self._checkpoint(execution_deadline)
                knowledge_index, run = self._index_service.index_artifact(
                    str(artifact_id),
                    created_by=self._created_by(job),
                    profile_name=self._profile_name(job),
                    profile_overrides=dict(payload.get("profile_overrides") or {}),
                    **deadline_kwargs,
                )
                self._checkpoint(execution_deadline)
                index_payload = self._model_dump(knowledge_index)
                run_payload = self._model_dump(run)
                results.append(
                    {
                        "artifact_id": str(artifact_id),
                        "knowledge_index": index_payload,
                        "run": run_payload,
                    }
                )
                artifact_refs.extend(
                    self._publish_outputs(
                        job=job,
                        payload=payload,
                        knowledge_index=index_payload,
                        run=run_payload,
                        execution_deadline=execution_deadline,
                    )
                )
                if self._is_failed(index_payload, run_payload):
                    overall_status = "failed"
            return {
                "status": overall_status,
                "results": results,
                "artifact_refs": artifact_refs,
                "reason_code": "knowledge_index_run_failed" if overall_status == "failed" else None,
            }
        if job_type == "source_records":
            # Workers publish artifacts only. Hub materialization owns
            # KnowledgeIndexDB/Run rows and their binding metadata. Sharing a
            # database with persist=True collides with that admission check.
            knowledge_index, run = self._index_service.index_source_records(
                source_scope=str(payload.get("source_scope") or ""),
                source_id=str(payload.get("source_id") or ""),
                records=[dict(item) for item in list(payload.get("records") or [])],
                created_by=self._created_by(job),
                profile_name=self._profile_name(job),
                source_metadata=dict(payload.get("source_metadata") or {}),
                codecompass_prerender=bool(payload.get("codecompass_prerender", False)),
                persist_control_plane_records=False,
                **deadline_kwargs,
            )
            self._checkpoint(execution_deadline)
            return self._single_result(
                job,
                payload,
                knowledge_index,
                run,
                execution_deadline=execution_deadline,
            )
        raise ValueError("knowledge_index_job_type_invalid")

    @staticmethod
    def _checkpoint(
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> None:
        if execution_deadline is not None:
            execution_deadline.checkpoint()

    def _resolve_payload(
        self,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> dict[str, Any]:
        payload = dict(job.get("payload") or {})
        raw_reference = payload.get("payload_artifact_ref")
        if raw_reference is None:
            return payload
        if not isinstance(raw_reference, Mapping) or set(raw_reference) != {
            "artifact_id",
            "sha256",
            "size_bytes",
            "media_type",
            "encoding",
        }:
            raise ValueError("knowledge_index_payload_artifact_ref_invalid")
        reference = dict(raw_reference)
        if str(reference.get("encoding") or "") != "json":
            raise ValueError("knowledge_index_payload_artifact_encoding_invalid")
        if str(reference.get("media_type") or "").lower() != PAYLOAD_MEDIA_TYPE:
            raise ValueError("knowledge_index_payload_artifact_media_type_invalid")
        size_bytes = int(reference.get("size_bytes") or -1)
        if size_bytes < 0 or size_bytes > MAX_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_payload_artifact_size_invalid")
        loader = self._payload_loader or HubArtifactKnowledgeIndexPayloadLoader()
        authorized_load = getattr(loader, "load_authorized", None)
        source_access_manifest = job.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if str(job.get("schema") or "") == BOUND_JOB_SCHEMA:
            if not isinstance(source_access_manifest, Mapping):
                raise ValueError(
                    "knowledge_index_payload_capability_required"
                )
            if not callable(authorized_load):
                raise ValueError(
                    "knowledge_index_authorized_payload_loader_required"
                )
            authorized_load_kwargs: dict[str, Any] = {
                "source_access_manifest": source_access_manifest,
            }
            if execution_deadline is not None:
                authorized_load_kwargs["execution_deadline"] = (
                    execution_deadline
                )
            content = authorized_load(reference, **authorized_load_kwargs)
        else:
            content = loader.load(reference)
        if len(content) != size_bytes:
            raise ValueError("knowledge_index_payload_artifact_size_mismatch")
        if hashlib.sha256(content).hexdigest() != str(reference.get("sha256") or ""):
            raise ValueError("knowledge_index_payload_artifact_digest_mismatch")
        try:
            decoded = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("knowledge_index_payload_artifact_json_invalid") from exc
        if not isinstance(decoded, dict) or "payload_artifact_ref" in decoded:
            raise ValueError("knowledge_index_payload_artifact_payload_invalid")
        return dict(decoded)

    @staticmethod
    def _created_by(job: Mapping[str, Any]) -> str | None:
        value = str(job.get("created_by") or "").strip()
        return value or None

    @staticmethod
    def _profile_name(job: Mapping[str, Any]) -> str | None:
        value = str(job.get("profile_name") or "").strip()
        return value or None

    @staticmethod
    def _model_dump(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            payload = value.model_dump()
        elif isinstance(value, Mapping):
            payload = dict(value)
        else:
            raise TypeError("knowledge_index_worker_model_invalid")
        return dict(payload)

    @staticmethod
    def _is_failed(knowledge_index: Mapping[str, Any], run: Mapping[str, Any]) -> bool:
        return any(
            str(value or "").strip().lower() == "failed"
            for value in (knowledge_index.get("status"), run.get("status"))
        )

    def _single_result(
        self,
        job: Mapping[str, Any],
        payload: Mapping[str, Any],
        knowledge_index: Any,
        run: Any,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> dict[str, Any]:
        index_payload = self._model_dump(knowledge_index)
        run_payload = self._model_dump(run)
        failed = self._is_failed(index_payload, run_payload)
        return {
            "status": "failed" if failed else "completed",
            "reason_code": "knowledge_index_run_failed" if failed else None,
            "knowledge_index": index_payload,
            "run": run_payload,
            "artifact_refs": self._publish_outputs(
                job=job,
                payload=payload,
                knowledge_index=index_payload,
                run=run_payload,
                execution_deadline=execution_deadline,
            ),
            "error": str(run_payload.get("error_message") or "") or None,
        }

    def _publish_outputs(
        self,
        *,
        job: Mapping[str, Any],
        payload: Mapping[str, Any],
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> list[dict[str, Any]]:
        self._checkpoint(execution_deadline)
        if self._is_failed(knowledge_index, run):
            return []
        # A custom publisher without a graph materializer is the intentional
        # compatibility adapter for pre-graph integrations. Production uses the
        # defaults and therefore always requires both graph artifacts.
        graph_artifacts_required = (
            self._artifact_publisher is None or self._graph_artifact_materializer is not None
        )
        revision_binding = self._revision_binding(
            job=job,
            knowledge_index=knowledge_index,
        )
        domain_supplement_required = False
        if graph_artifacts_required:
            materializer = self._graph_artifact_materializer
            if materializer is None:
                from worker.retrieval.codecompass_graph_artifact_materializer import (
                    WorkerCodeCompassGraphArtifactMaterializer,
                )

                materializer = WorkerCodeCompassGraphArtifactMaterializer()
            raw_options = payload.get("graph_visual_metrics")
            if raw_options is not None and not isinstance(raw_options, Mapping):
                raise ValueError("graph_visual_options_invalid")
            materializer_kwargs = (
                {"execution_deadline": execution_deadline}
                if execution_deadline is not None
                else {}
            )
            if revision_binding is not None and self._graph_artifact_materializer is None:
                materializer_kwargs["revision_binding"] = revision_binding
            materialization = materializer.materialize(
                knowledge_index=knowledge_index,
                run=run,
                options=raw_options,
                **materializer_kwargs,
            )
            domain_supplement_required = bool(
                isinstance(materialization, Mapping)
                and isinstance(
                    materialization.get("domain_supplement"), Mapping
                )
            )
            self._checkpoint(execution_deadline)

        publisher = self._artifact_publisher or WorkerKnowledgeIndexArtifactPublisher()
        publisher_kwargs = (
            {"execution_deadline": execution_deadline}
            if execution_deadline is not None
            else {}
        )
        if revision_binding is not None and self._artifact_publisher is None:
            publisher_kwargs["revision_binding"] = revision_binding
        references = publisher.publish(
            job_id=str(job.get("job_id") or ""),
            knowledge_index=knowledge_index,
            run=run,
            **publisher_kwargs,
        )
        self._checkpoint(execution_deadline)
        roles = {str(item.get("role") or "") for item in references}
        required_roles = {"manifest", "index"}
        if graph_artifacts_required:
            required_roles.update({"graph_index", "graph_visual_metrics"})
        if domain_supplement_required:
            required_roles.add(DOMAIN_SUPPLEMENT_OUTPUT_ROLE)
        if not required_roles.issubset(roles):
            raise RuntimeError("knowledge_index_output_artifacts_incomplete")
        return references

    @staticmethod
    def _revision_binding(
        *,
        job: Mapping[str, Any],
        knowledge_index: Mapping[str, Any],
    ) -> dict[str, str] | None:
        if str(job.get("schema") or "") != BOUND_JOB_SCHEMA:
            return None
        authority = job.get("authority_binding")
        metadata = knowledge_index.get("index_metadata")
        if not isinstance(authority, Mapping) or not isinstance(metadata, Mapping):
            raise ValueError("knowledge_index_revision_binding_missing")
        return {
            "source_scope": str(metadata.get("source_scope") or "").strip(),
            "source_id": str(metadata.get("source_id") or "").strip(),
            "source_revision_id": str(
                authority.get("source_revision_id") or ""
            ).strip(),
            "source_revision_digest": str(
                authority.get("source_revision_digest") or ""
            ).strip(),
        }


__all__ = ["RagHelperKnowledgeIndexExecution"]
