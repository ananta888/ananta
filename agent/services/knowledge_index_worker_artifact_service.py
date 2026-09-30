"""Hub-side admission of worker-produced knowledge-index artifacts.

``KnowledgeIndexWorkerArtifactService`` orchestrates one terminal Worker
result.  It composes narrow collaborators, each overridable through a
keyword-only constructor parameter:

* ``knowledge_index_worker_artifact_contract`` -- pure result/reference rules
* ``knowledge_index_worker_artifact_download`` -- assignment-bound transport
* ``knowledge_index_worker_artifact_staging`` -- staging and byte verification
* ``knowledge_index_worker_graph_artifact_admission`` -- graph admission
* ``knowledge_index_worker_materialization_store`` -- idempotent persistence
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.services.codecompass_artifact_manifest import (
    CodeCompassArtifactManifestProjector,
)
from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementPort,
    get_codecompass_domain_supplement_reader,
)
from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_PROJECTED_STATE,
)
from agent.services.knowledge_index_worker_artifact_contract import (
    JOB_ID_PATTERN,
    MATERIALIZATION_BINDING_METADATA_KEY,
    OUTPUT_FILENAMES,
    PENDING_PROJECTION_STATE,
    PRIMARY_GRAPH_ROLES,
    PUBLIC_ARTIFACT_SCHEMAS,
    materialization_bindings,
    result_units,
    safe_identifier,
    source_scope_of,
    validate_index_source_binding,
    validate_result_reference_contract,
    validate_unit_reference_budget,
)
from agent.services.knowledge_index_worker_artifact_download import (
    HttpKnowledgeIndexWorkerArtifactDownloader,
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexArtifactTransferDeadlinePort as KnowledgeIndexArtifactTransferDeadlinePort,
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexWorkerArtifactDownloaderPort as KnowledgeIndexWorkerArtifactDownloaderPort,
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexWorkerNoRedirectHandler as _KnowledgeIndexWorkerNoRedirectHandler,  # noqa: F401 - re-export
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexWorkerStreamingArtifactDownloaderPort as KnowledgeIndexWorkerStreamingArtifactDownloaderPort,
)
from agent.services.knowledge_index_worker_artifact_staging import (
    KnowledgeIndexWorkerArtifactStager,
    promote_staging,
    strict_json_object,
    verify_downloaded_content,
    verify_staged_file,
)
from agent.services.knowledge_index_worker_graph_artifact_admission import (
    KnowledgeIndexWorkerGraphArtifactAdmission,
)
from agent.services.knowledge_index_worker_materialization_store import (
    KnowledgeIndexWorkerMaterializationStore,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)


class KnowledgeIndexWorkerArtifactService:
    """Verify, materialize and persist one terminal worker index result."""

    def __init__(
        self,
        *,
        downloader: (
            KnowledgeIndexWorkerArtifactDownloaderPort
            | KnowledgeIndexWorkerStreamingArtifactDownloaderPort
            | None
        ) = None,
        knowledge_index_repository: Any | None = None,
        knowledge_index_run_repository: Any | None = None,
        output_root: str | Path | None = None,
        manifest_projector: CodeCompassArtifactManifestProjector | None = None,
        domain_supplement_reader: CodeCompassDomainSupplementPort | None = None,
        artifact_stager: KnowledgeIndexWorkerArtifactStager | None = None,
        graph_artifact_admission: (
            KnowledgeIndexWorkerGraphArtifactAdmission | None
        ) = None,
        materialization_store: (
            KnowledgeIndexWorkerMaterializationStore | None
        ) = None,
    ) -> None:
        self._downloader = downloader or HttpKnowledgeIndexWorkerArtifactDownloader()
        if knowledge_index_repository is None or knowledge_index_run_repository is None:
            from agent.repository import knowledge_index_repo, knowledge_index_run_repo

            knowledge_index_repository = (
                knowledge_index_repository or knowledge_index_repo
            )
            knowledge_index_run_repository = (
                knowledge_index_run_repository or knowledge_index_run_repo
            )
        self._knowledge_index_repository = knowledge_index_repository
        self._knowledge_index_run_repository = knowledge_index_run_repository
        self._output_root = Path(
            output_root or Path(settings.data_dir) / "knowledge_indices"
        ).resolve()
        self._manifest_projector = (
            manifest_projector or CodeCompassArtifactManifestProjector()
        )
        self._domain_supplement_reader = (
            domain_supplement_reader or get_codecompass_domain_supplement_reader()
        )
        self._artifact_stager = artifact_stager or KnowledgeIndexWorkerArtifactStager(
            downloader=self._downloader
        )
        self._graph_artifact_admission = (
            graph_artifact_admission
            or KnowledgeIndexWorkerGraphArtifactAdmission(
                domain_supplement_reader=self._domain_supplement_reader
            )
        )
        self._materialization_store = (
            materialization_store
            or KnowledgeIndexWorkerMaterializationStore(
                knowledge_index_repository=self._knowledge_index_repository,
                knowledge_index_run_repository=(
                    self._knowledge_index_run_repository
                ),
            )
        )

    @staticmethod
    def _checkpoint(
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> None:
        if transfer_deadline is not None:
            transfer_deadline.require_remaining_seconds()

    def materialize(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        task: Mapping[str, Any],
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(transfer_deadline)
        normalized = dict(result)
        if str(normalized.get("status") or "") != "completed":
            return normalized
        context = dict(task.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        if str(envelope.get("job_id") or "") != str(job_id):
            raise ValueError("knowledge_index_worker_artifact_job_mismatch")
        source_scope = source_scope_of(envelope)
        worker_url = str(task.get("assigned_agent_url") or "").strip()
        worker_token = self._worker_token(task, worker_url=worker_url)
        raw_manifest = envelope.get("source_access_enforcement_manifest")
        source_access_manifest = (
            dict(raw_manifest) if isinstance(raw_manifest, Mapping) else None
        )
        bound_v2 = (
            str(envelope.get("schema") or "") == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        )
        authority_binding = dict(envelope.get("authority_binding") or {})
        source_revision_id = str(
            envelope.get("source_revision_id")
            or authority_binding.get("source_revision_id")
            or ""
        ).strip()
        source_revision_digest = str(
            authority_binding.get("source_revision_digest") or ""
        ).strip()
        source_id = f"bound-source:{source_revision_id}" if source_revision_id else ""
        if bound_v2 and source_access_manifest is None:
            raise ValueError("knowledge_index_worker_output_capability_required")
        if source_access_manifest is not None and not JOB_ID_PATTERN.fullmatch(
            str(job_id or "").strip()
        ):
            raise ValueError("knowledge_index_worker_output_capability_required")
        if not worker_url or not (worker_token or source_access_manifest):
            raise ValueError("knowledge_index_worker_artifact_transport_unavailable")

        units = result_units(normalized)
        raw_references = normalized.get("artifact_refs")
        if not isinstance(raw_references, list) or any(
            not isinstance(item, Mapping) for item in raw_references
        ):
            raise ValueError("knowledge_index_worker_artifact_refs_invalid")
        references = [dict(item) for item in raw_references]
        validate_result_reference_contract(
            units=units,
            references=references,
            envelope=envelope,
            bound_v2=bound_v2,
        )
        materialized_units: list[dict[str, Any]] = []
        initial_projection_state = (
            PENDING_PROJECTION_STATE if bound_v2 else KNOWLEDGE_INDEX_PROJECTED_STATE
        )
        for unit in units:
            self._checkpoint(transfer_deadline)
            index_payload = dict(unit["knowledge_index"])
            run_payload = dict(unit["run"])
            index_id = safe_identifier(index_payload.get("id"), field="index_id")
            run_id = safe_identifier(run_payload.get("id"), field="run_id")
            if str(run_payload.get("knowledge_index_id") or index_id) != index_id:
                raise ValueError("knowledge_index_worker_run_binding_mismatch")
            index_binding, run_binding = materialization_bindings(
                job_id=job_id,
                envelope=envelope,
                index_id=index_id,
                run_id=run_id,
                bound_v2=bound_v2,
                projection_state=initial_projection_state,
            )
            self._materialization_store.assert_existing_bindings(
                index_id=index_id,
                run_id=run_id,
                index_binding=index_binding,
                run_binding=run_binding,
            )
            unit_refs = [
                reference
                for reference in references
                if str(reference.get("knowledge_index_id") or "") == index_id
                and str(reference.get("run_id") or "") == run_id
            ]
            by_role = {
                str(reference.get("role") or ""): reference for reference in unit_refs
            }
            if len(by_role) != len(unit_refs) or not {"manifest", "index"}.issubset(
                by_role
            ):
                raise ValueError("knowledge_index_worker_artifacts_incomplete")
            present_primary_graph_roles = PRIMARY_GRAPH_ROLES.intersection(by_role)
            if (
                present_primary_graph_roles
                and present_primary_graph_roles != PRIMARY_GRAPH_ROLES
            ):
                raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
            supplement_present = DOMAIN_SUPPLEMENT_OUTPUT_ROLE in by_role
            if supplement_present and (
                present_primary_graph_roles != PRIMARY_GRAPH_ROLES or not bound_v2
            ):
                raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
            if supplement_present:
                validate_index_source_binding(
                    index_payload=index_payload,
                    source_scope=source_scope,
                    source_id=source_id,
                    source_revision_id=source_revision_id,
                    source_revision_digest=source_revision_digest,
                )
            output_dir = self._output_root / source_scope / index_id / run_id
            validate_unit_reference_budget(by_role)
            replay = self._materialization_store.existing_materialized_unit(
                index_id=index_id,
                run_id=run_id,
                output_dir=output_dir,
                by_role=by_role,
                index_binding=index_binding,
                run_binding=run_binding,
            )
            if replay is not None:
                replay_index = replay[0].model_dump()
                replay_run = replay[1].model_dump()
                if bound_v2:
                    replay_index["status"] = "completed"
                    replay_run["status"] = "completed"
                materialized_units.append(
                    {
                        **unit,
                        "knowledge_index": replay_index,
                        "run": replay_run,
                    }
                )
                continue
            output_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{run_id}.artifacts-",
                    dir=output_dir.parent,
                )
            )
            try:
                staged_paths: dict[str, Path] = {}
                for role, reference in sorted(by_role.items()):
                    destination = staging_dir / OUTPUT_FILENAMES[role]
                    self._artifact_stager.stage_reference(
                        worker_url=worker_url,
                        worker_token=worker_token,
                        source_access_manifest=source_access_manifest,
                        job_id=job_id,
                        reference=reference,
                        destination=destination,
                        transfer_deadline=transfer_deadline,
                    )
                    staged_paths[role] = destination
                self._checkpoint(transfer_deadline)
                graph_binding = (
                    self._graph_artifact_admission.validate(
                        by_role=by_role,
                        staged_paths=staged_paths,
                        knowledge_index_id=index_id,
                        source_scope=source_scope,
                        source_id=source_id,
                        source_revision_id=source_revision_id,
                        source_revision_digest=source_revision_digest,
                        transfer_deadline=transfer_deadline,
                    )
                    if present_primary_graph_roles
                    else None
                )
                self._checkpoint(transfer_deadline)
                public_artifact_manifest: dict[str, Any] | None = None
                if source_revision_id:
                    raw_manifest = strict_json_object(staged_paths["manifest"])
                    raw_coverage = raw_manifest.get("coverage")
                    raw_exclusions = raw_manifest.get("exclusions")
                    if raw_exclusions is not None and (
                        not isinstance(raw_exclusions, list)
                        or any(not isinstance(item, Mapping) for item in raw_exclusions)
                    ):
                        raise ValueError("knowledge_index_worker_exclusions_invalid")
                    public_artifact_manifest = self._manifest_projector.project(
                        knowledge_index_id=index_id,
                        run_id=run_id,
                        source_revision_id=source_revision_id,
                        references=[
                            {
                                **dict(reference),
                                "artifact_schema": str(
                                    reference.get("artifact_schema")
                                    or PUBLIC_ARTIFACT_SCHEMAS.get(role)
                                    or ""
                                ),
                            }
                            for role, reference in by_role.items()
                        ],
                        coverage=(
                            dict(raw_coverage)
                            if isinstance(raw_coverage, Mapping)
                            else {}
                        ),
                        exclusions=(
                            [dict(item) for item in raw_exclusions]
                            if isinstance(raw_exclusions, list)
                            else ()
                        ),
                        graph_schema=(
                            "codecompass_graph_index.v1"
                            if graph_binding is not None
                            else None
                        ),
                        graph_revision=(
                            str(graph_binding.get("graph_revision") or "")
                            if graph_binding is not None
                            else None
                        ),
                        status="completed",
                    ).to_dict()
                self._checkpoint(transfer_deadline)
                promote_staging(
                    staging_dir=staging_dir,
                    output_dir=output_dir,
                    staged_paths=staged_paths,
                )
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
            if graph_binding is not None:
                graph_binding = self._graph_artifact_admission.local_binding(
                    graph_binding,
                    by_role=by_role,
                    output_dir=output_dir,
                )
            manifest_path = output_dir / "manifest.json"
            persisted_status = "pending_verification" if bound_v2 else "completed"
            index_payload.update(
                {
                    "source_scope": source_scope,
                    "status": persisted_status,
                    "output_dir": str(output_dir),
                    "manifest_path": str(manifest_path),
                    "latest_run_id": run_id,
                }
            )
            if supplement_present:
                index_payload["index_metadata"] = {
                    **dict(index_payload.get("index_metadata") or {}),
                    "source_scope": source_scope,
                    "source_id": source_id,
                    "source_revision_id": source_revision_id,
                    "source_revision_digest": source_revision_digest,
                }
            if graph_binding is not None:
                index_payload["index_metadata"] = {
                    **dict(index_payload.get("index_metadata") or {}),
                    "graph_artifacts": graph_binding,
                }
            if public_artifact_manifest is not None:
                index_payload["index_metadata"] = {
                    **dict(index_payload.get("index_metadata") or {}),
                    "artifact_manifest": public_artifact_manifest,
                }
            run_payload.update(
                {
                    "knowledge_index_id": index_id,
                    "status": persisted_status,
                    "output_dir": str(output_dir),
                    "manifest_path": str(manifest_path),
                }
            )
            if graph_binding is not None:
                run_payload["run_metadata"] = {
                    **dict(run_payload.get("run_metadata") or {}),
                    "graph_artifacts": graph_binding,
                }
            if public_artifact_manifest is not None:
                run_payload["run_metadata"] = {
                    **dict(run_payload.get("run_metadata") or {}),
                    "artifact_manifest": public_artifact_manifest,
                }
            index_payload["index_metadata"] = {
                **dict(index_payload.get("index_metadata") or {}),
                MATERIALIZATION_BINDING_METADATA_KEY: index_binding,
            }
            run_payload["run_metadata"] = {
                **dict(run_payload.get("run_metadata") or {}),
                MATERIALIZATION_BINDING_METADATA_KEY: run_binding,
            }
            self._checkpoint(transfer_deadline)
            saved_index = self._materialization_store.save_index(
                index_payload,
                expected_binding=index_binding,
            )
            saved_run = self._materialization_store.save_run(
                run_payload,
                expected_binding=run_binding,
            )
            saved_index_payload = saved_index.model_dump()
            saved_run_payload = saved_run.model_dump()
            if bound_v2:
                # The returned candidate is internal outbox input for the
                # canonical projector.  Its durable local rows remain inert
                # until activate_materialized_result performs the monotone
                # Pending -> Projected transition.
                saved_index_payload["status"] = "completed"
                saved_run_payload["status"] = "completed"
            materialized_units.append(
                {
                    **unit,
                    "knowledge_index": saved_index_payload,
                    "run": saved_run_payload,
                }
            )

        self._checkpoint(transfer_deadline)
        if normalized.get("knowledge_index") is not None:
            normalized["knowledge_index"] = materialized_units[0]["knowledge_index"]
            normalized["run"] = materialized_units[0]["run"]
        else:
            normalized["results"] = materialized_units
        return normalized

    def activate_materialized_result(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        artifact_references: list[Mapping[str, Any]],
        task: Mapping[str, Any],
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> dict[str, Any]:
        """Monotonically expose a Hub-projected v2 result to consumers."""

        self._checkpoint(transfer_deadline)
        normalized = dict(result)
        if str(normalized.get("status") or "") != "completed":
            raise ValueError(
                "knowledge_index_worker_activation_result_invalid"
            )
        context = dict(task.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        if (
            envelope.get("schema")
            != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
            or str(envelope.get("job_id") or "") != str(job_id)
        ):
            raise ValueError(
                "knowledge_index_worker_activation_binding_invalid"
            )
        units = result_units(normalized)
        if any(not isinstance(item, Mapping) for item in artifact_references):
            raise ValueError(
                "knowledge_index_worker_artifact_refs_invalid"
            )
        references = [dict(item) for item in artifact_references]
        validate_result_reference_contract(
            units=units,
            references=references,
            envelope=envelope,
            bound_v2=True,
        )
        source_scope = source_scope_of(envelope)
        activated_units: list[dict[str, Any]] = []
        for unit in units:
            self._checkpoint(transfer_deadline)
            index_payload = dict(unit["knowledge_index"])
            run_payload = dict(unit["run"])
            index_id = safe_identifier(
                index_payload.get("id"),
                field="index_id",
            )
            run_id = safe_identifier(
                run_payload.get("id"),
                field="run_id",
            )
            if str(run_payload.get("knowledge_index_id") or "") != index_id:
                raise ValueError(
                    "knowledge_index_worker_run_binding_mismatch"
                )
            index_binding, run_binding = materialization_bindings(
                job_id=job_id,
                envelope=envelope,
                index_id=index_id,
                run_id=run_id,
                bound_v2=True,
                projection_state=KNOWLEDGE_INDEX_PROJECTED_STATE,
            )
            self._materialization_store.assert_existing_bindings(
                index_id=index_id,
                run_id=run_id,
                index_binding=index_binding,
                run_binding=run_binding,
            )
            by_role = {
                str(reference.get("role") or ""): reference
                for reference in references
                if str(reference.get("knowledge_index_id") or "")
                == index_id
                and str(reference.get("run_id") or "") == run_id
            }
            if not {"manifest", "index"}.issubset(by_role):
                raise ValueError(
                    "knowledge_index_worker_artifacts_incomplete"
                )
            output_dir = self._output_root / source_scope / index_id / run_id
            if output_dir.is_symlink() or not output_dir.is_dir():
                raise ValueError(
                    "knowledge_index_worker_artifact_output_invalid"
                )
            for role, reference in by_role.items():
                verify_staged_file(
                    reference=reference,
                    path=output_dir / OUTPUT_FILENAMES[role],
                )
            manifest_path = output_dir / "manifest.json"
            index_payload.update(
                {
                    "source_scope": source_scope,
                    "status": "completed",
                    "output_dir": str(output_dir),
                    "manifest_path": str(manifest_path),
                    "latest_run_id": run_id,
                    "index_metadata": {
                        **dict(index_payload.get("index_metadata") or {}),
                        MATERIALIZATION_BINDING_METADATA_KEY: index_binding,
                    },
                }
            )
            run_payload.update(
                {
                    "knowledge_index_id": index_id,
                    "status": "completed",
                    "output_dir": str(output_dir),
                    "manifest_path": str(manifest_path),
                    "run_metadata": {
                        **dict(run_payload.get("run_metadata") or {}),
                        MATERIALIZATION_BINDING_METADATA_KEY: run_binding,
                    },
                }
            )
            saved_index = self._materialization_store.save_index(
                index_payload,
                expected_binding=index_binding,
            )
            saved_run = self._materialization_store.save_run(
                run_payload,
                expected_binding=run_binding,
            )
            activated_units.append(
                {
                    **unit,
                    "knowledge_index": saved_index.model_dump(),
                    "run": saved_run.model_dump(),
                }
            )
        self._checkpoint(transfer_deadline)
        if normalized.get("knowledge_index") is not None:
            normalized["knowledge_index"] = activated_units[0][
                "knowledge_index"
            ]
            normalized["run"] = activated_units[0]["run"]
        else:
            normalized["results"] = activated_units
        return normalized

    @staticmethod
    def _worker_token(task: Mapping[str, Any], *, worker_url: str) -> str:
        token = str(task.get("assigned_agent_token") or "").strip()
        try:
            from agent.services.repository_registry import get_repository_registry

            agent = get_repository_registry().agent_repo.get_by_url(worker_url)
            current = str(getattr(agent, "token", "") or "").strip()
            if current:
                token = current
        except Exception:
            pass
        return token


    # Compatibility seams kept for existing callers of the former private API.
    _verify_downloaded_content = staticmethod(verify_downloaded_content)

    def _validate_graph_artifacts(self, **kwargs: Any) -> dict[str, Any]:
        return self._graph_artifact_admission.validate(**kwargs)


__all__ = [
    "HttpKnowledgeIndexWorkerArtifactDownloader",
    "KnowledgeIndexWorkerArtifactDownloaderPort",
    "KnowledgeIndexWorkerStreamingArtifactDownloaderPort",
    "KnowledgeIndexWorkerArtifactService",
]
