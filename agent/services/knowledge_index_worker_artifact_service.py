"""Hub-side admission of worker-produced knowledge-index artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.config import settings
from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.services.codecompass_artifact_manifest import (
    CodeCompassArtifactManifestProjector,
)
from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementBinding,
    CodeCompassDomainSupplementPort,
    get_codecompass_domain_supplement_reader,
)
from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA,
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
    KNOWLEDGE_INDEX_PROJECTED_STATE,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    DOMAIN_SUPPLEMENT_SCHEMA,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)
from ananta_contracts.knowledge_index_worker_output_capability import (
    KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER,
    encode_knowledge_index_output_capability,
)

_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
_MAX_UNIT_ARTIFACT_BYTES = 384 * 1024 * 1024
_MAX_RESULT_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_RESULT_UNITS = 256
_MAX_GRAPH_JSON_BYTES = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_OUTPUT_FILENAMES = {
    "manifest": "manifest.json",
    "index": "index.jsonl",
    "details": "details.jsonl",
    "relations": "relations.jsonl",
    "graph_index": "cc_graph_index.json",
    "graph_visual_metrics": "cc_graph_index.visual_metrics.json",
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_FILENAME,
}
_PRIMARY_GRAPH_ROLES = frozenset({"graph_index", "graph_visual_metrics"})
_GRAPH_ROLES = _PRIMARY_GRAPH_ROLES | frozenset({DOMAIN_SUPPLEMENT_OUTPUT_ROLE})
_GRAPH_MEDIA_TYPES = {
    "graph_index": "application/vnd.ananta.codecompass-graph-index+json",
    "graph_visual_metrics": "application/vnd.ananta.codecompass-graph-visual-metrics+json",
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_MEDIA_TYPE,
}
_PUBLIC_ARTIFACT_SCHEMAS = {
    "manifest": "ananta.knowledge-index.manifest.v1",
    "index": "ananta.knowledge-index.records.v1",
    "details": "ananta.knowledge-index.details.v1",
    "relations": "ananta.knowledge-index.relations.v1",
}
_MAX_REFS_PER_UNIT = len(_OUTPUT_FILENAMES)
_MAX_RESULT_ARTIFACT_REFS = _MAX_RESULT_UNITS * _MAX_REFS_PER_UNIT
_MATERIALIZATION_BINDING_METADATA_KEY = (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY
)
_MATERIALIZATION_BINDING_SCHEMA = (
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA
)
_PENDING_PROJECTION_STATE = "pending"
_JOB_ID = re.compile(r"^knowledge-index-[0-9a-f]{32}$")


class _KnowledgeIndexWorkerNoRedirectHandler(
    urllib.request.HTTPRedirectHandler
):
    """Keep Worker capabilities on the single assigned-Worker request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class KnowledgeIndexWorkerArtifactDownloaderPort(Protocol):
    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: "KnowledgeIndexArtifactTransferDeadlinePort | None" = None,
    ) -> bytes: ...


class KnowledgeIndexWorkerStreamingArtifactDownloaderPort(Protocol):
    """Optional capability for bounded direct-to-staging downloads."""

    def download_to_path(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        destination: Path,
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: "KnowledgeIndexArtifactTransferDeadlinePort | None" = None,
    ) -> None: ...


class KnowledgeIndexArtifactTransferDeadlinePort(Protocol):
    """Trusted Hub wall-clock deadline shared with the Worker POST."""

    def require_remaining_seconds(self) -> float: ...


class HttpKnowledgeIndexWorkerArtifactDownloader:
    """Download one bounded artifact from the assigned worker only."""

    def __init__(self, *, opener: Any | None = None) -> None:
        self._opener = opener or urllib.request.build_opener(
            _KnowledgeIndexWorkerNoRedirectHandler()
        )

    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> bytes:
        request, expected_size, expected_hash = self._request(
            worker_url=worker_url,
            worker_token=worker_token,
            reference=reference,
            source_access_manifest=source_access_manifest,
            job_id=job_id,
        )
        chunks: list[bytes] = []
        received = 0
        with self._open(request, transfer_deadline=transfer_deadline) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_size:
                raise ValueError("knowledge_index_worker_artifact_size_mismatch")
            while True:
                if declared is not None and received == expected_size:
                    break
                chunk = self._read_chunk(
                    response,
                    min(_DOWNLOAD_CHUNK_BYTES, expected_size - received + 1),
                    transfer_deadline=transfer_deadline,
                )
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > expected_size:
                    raise ValueError(
                        "knowledge_index_worker_artifact_size_mismatch"
                    )
        content = b"".join(chunks)
        if len(content) != expected_size:
            raise ValueError("knowledge_index_worker_artifact_size_mismatch")
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError("knowledge_index_worker_artifact_digest_mismatch")
        return content

    def download_to_path(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        destination: Path,
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> None:
        """Stream one verified worker artifact directly into Hub staging."""

        request, expected_size, expected_hash = self._request(
            worker_url=worker_url,
            worker_token=worker_token,
            reference=reference,
            source_access_manifest=source_access_manifest,
            job_id=job_id,
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("knowledge_index_worker_artifact_staging_conflict")
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        hasher = hashlib.sha256()
        try:
            with self._open(
                request,
                transfer_deadline=transfer_deadline,
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) != expected_size:
                    raise ValueError("knowledge_index_worker_artifact_size_mismatch")
                with destination.open("xb") as handle:
                    while True:
                        if declared is not None and written == expected_size:
                            break
                        chunk = self._read_chunk(
                            response,
                            min(
                                _DOWNLOAD_CHUNK_BYTES,
                                expected_size - written + 1,
                            ),
                            transfer_deadline=transfer_deadline,
                        )
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > expected_size:
                            raise ValueError("knowledge_index_worker_artifact_size_mismatch")
                        hasher.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            if written != expected_size:
                raise ValueError("knowledge_index_worker_artifact_size_mismatch")
            if hasher.hexdigest() != expected_hash:
                raise ValueError("knowledge_index_worker_artifact_digest_mismatch")
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def _open(
        self,
        request: urllib.request.Request,
        *,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> Any:
        """Open exactly one URL and reject every redirect response."""

        timeout = 60.0
        if transfer_deadline is not None:
            timeout = min(
                timeout,
                transfer_deadline.require_remaining_seconds(),
            )
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code or 0) < 400:
                exc.close()
                raise ValueError(
                    "knowledge_index_worker_artifact_redirect_forbidden"
                ) from exc
            raise
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else None
        if status is not None and 300 <= int(status) < 400:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            raise ValueError(
                "knowledge_index_worker_artifact_redirect_forbidden"
            )
        if transfer_deadline is not None:
            transfer_deadline.require_remaining_seconds()
        return response

    @staticmethod
    def _read_chunk(
        response: Any,
        maximum_bytes: int,
        *,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> bytes:
        if maximum_bytes <= 0:
            return b""
        if transfer_deadline is not None:
            remaining = transfer_deadline.require_remaining_seconds()
            if not HttpKnowledgeIndexWorkerArtifactDownloader._set_socket_timeout(
                response,
                min(60.0, remaining),
            ):
                raise ValueError(
                    "knowledge_index_worker_artifact_deadline_transport_unsupported"
                )
        reader = getattr(response, "read1", None)
        if not callable(reader):
            reader = response.read
        try:
            chunk = reader(maximum_bytes)
        except TimeoutError:
            if transfer_deadline is not None:
                # Preserve the typed absolute-deadline outcome when the
                # narrowed socket timeout and the shared clock expire together.
                transfer_deadline.require_remaining_seconds()
            raise
        if transfer_deadline is not None:
            transfer_deadline.require_remaining_seconds()
        if not isinstance(chunk, bytes):
            raise ValueError("knowledge_index_worker_artifact_bytes_invalid")
        return chunk

    @staticmethod
    def _set_socket_timeout(response: Any, timeout: float) -> bool:
        """Narrow the production urllib socket before each single raw read."""

        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        candidates = (
            getattr(raw, "_sock", None),
            raw,
            fp,
        )
        for candidate in candidates:
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                setter(timeout)
                return True
        return False

    @staticmethod
    def _request(
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
    ) -> tuple[urllib.request.Request, int, str]:
        parsed = urllib.parse.urlsplit(str(worker_url or "").rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("knowledge_index_worker_url_invalid")
        artifact_id = str(reference.get("artifact_id") or "").strip()
        raw_size = reference.get("size_bytes")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int):
            raise ValueError("knowledge_index_worker_artifact_ref_invalid")
        expected_hash = str(reference.get("sha256") or "").lower()
        if not artifact_id or raw_size < 0 or raw_size > _MAX_ARTIFACT_BYTES:
            raise ValueError("knowledge_index_worker_artifact_ref_invalid")
        if len(expected_hash) != 64 or any(
            char not in "0123456789abcdef" for char in expected_hash
        ):
            raise ValueError("knowledge_index_worker_artifact_digest_invalid")
        base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        encoded_id = urllib.parse.quote(artifact_id, safe="")
        normalized_token = str(worker_token or "").strip()
        normalized_job_id = str(job_id or "").strip()
        capability_requested = (
            source_access_manifest is not None or job_id is not None
        )
        if capability_requested:
            if (
                not isinstance(source_access_manifest, Mapping)
                or not _JOB_ID.fullmatch(normalized_job_id)
            ):
                raise ValueError(
                    "knowledge_index_worker_artifact_transport_unavailable"
                )
            path = (
                "/internal/knowledge-index/output-artifacts/"
                f"{encoded_id}"
            )
            headers = {
                KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER: (
                    encode_knowledge_index_output_capability(
                        source_access_manifest
                    )
                ),
                KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER: normalized_job_id,
                KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER: str(
                    reference.get("knowledge_index_id") or ""
                ),
                KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER: str(
                    reference.get("run_id") or ""
                ),
                KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER: str(
                    reference.get("role") or ""
                ),
                KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER: expected_hash,
                KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER: str(raw_size),
                KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER: str(
                    reference.get("media_type") or ""
                ),
            }
            if normalized_token:
                headers["Authorization"] = (
                    f"Bearer {normalized_token}"
                )
        elif normalized_token:
            path = f"/artifacts/{encoded_id}/content"
            headers = {"Authorization": f"Bearer {normalized_token}"}
        else:
            raise ValueError(
                "knowledge_index_worker_artifact_transport_unavailable"
            )
        return (
            urllib.request.Request(
                f"{base_url}{path}",
                headers=headers,
                method="GET",
            ),
            raw_size,
            expected_hash,
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
        source_scope = self._source_scope(envelope)
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
        if source_access_manifest is not None and not _JOB_ID.fullmatch(
            str(job_id or "").strip()
        ):
            raise ValueError("knowledge_index_worker_output_capability_required")
        if not worker_url or not (worker_token or source_access_manifest):
            raise ValueError("knowledge_index_worker_artifact_transport_unavailable")

        units = self._result_units(normalized)
        raw_references = normalized.get("artifact_refs")
        if not isinstance(raw_references, list) or any(
            not isinstance(item, Mapping) for item in raw_references
        ):
            raise ValueError("knowledge_index_worker_artifact_refs_invalid")
        references = [dict(item) for item in raw_references]
        self._validate_result_reference_contract(
            units=units,
            references=references,
            envelope=envelope,
            bound_v2=bound_v2,
        )
        materialized_units: list[dict[str, Any]] = []
        initial_projection_state = (
            _PENDING_PROJECTION_STATE if bound_v2 else KNOWLEDGE_INDEX_PROJECTED_STATE
        )
        for unit in units:
            self._checkpoint(transfer_deadline)
            index_payload = dict(unit["knowledge_index"])
            run_payload = dict(unit["run"])
            index_id = self._safe_identifier(index_payload.get("id"), field="index_id")
            run_id = self._safe_identifier(run_payload.get("id"), field="run_id")
            if str(run_payload.get("knowledge_index_id") or index_id) != index_id:
                raise ValueError("knowledge_index_worker_run_binding_mismatch")
            index_binding, run_binding = self._materialization_bindings(
                job_id=job_id,
                envelope=envelope,
                index_id=index_id,
                run_id=run_id,
                bound_v2=bound_v2,
                projection_state=initial_projection_state,
            )
            self._assert_existing_materialization_bindings(
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
            present_primary_graph_roles = _PRIMARY_GRAPH_ROLES.intersection(by_role)
            if (
                present_primary_graph_roles
                and present_primary_graph_roles != _PRIMARY_GRAPH_ROLES
            ):
                raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
            supplement_present = DOMAIN_SUPPLEMENT_OUTPUT_ROLE in by_role
            if supplement_present and (
                present_primary_graph_roles != _PRIMARY_GRAPH_ROLES or not bound_v2
            ):
                raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
            if supplement_present:
                self._validate_index_source_binding(
                    index_payload=index_payload,
                    source_scope=source_scope,
                    source_id=source_id,
                    source_revision_id=source_revision_id,
                    source_revision_digest=source_revision_digest,
                )
            output_dir = self._output_root / source_scope / index_id / run_id
            self._validate_unit_reference_budget(by_role)
            replay = self._existing_materialized_unit(
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
                    destination = staging_dir / _OUTPUT_FILENAMES[role]
                    self._stage_reference(
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
                    self._validate_graph_artifacts(
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
                    raw_manifest = self._strict_json_object(staged_paths["manifest"])
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
                                    or _PUBLIC_ARTIFACT_SCHEMAS.get(role)
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
                self._promote_staging(
                    staging_dir=staging_dir,
                    output_dir=output_dir,
                    staged_paths=staged_paths,
                )
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
            if graph_binding is not None:
                graph_binding = self._local_graph_binding(
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
                _MATERIALIZATION_BINDING_METADATA_KEY: index_binding,
            }
            run_payload["run_metadata"] = {
                **dict(run_payload.get("run_metadata") or {}),
                _MATERIALIZATION_BINDING_METADATA_KEY: run_binding,
            }
            self._checkpoint(transfer_deadline)
            saved_index = self._save_index(
                index_payload,
                expected_binding=index_binding,
            )
            saved_run = self._save_run(
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
        units = self._result_units(normalized)
        if any(not isinstance(item, Mapping) for item in artifact_references):
            raise ValueError(
                "knowledge_index_worker_artifact_refs_invalid"
            )
        references = [dict(item) for item in artifact_references]
        self._validate_result_reference_contract(
            units=units,
            references=references,
            envelope=envelope,
            bound_v2=True,
        )
        source_scope = self._source_scope(envelope)
        activated_units: list[dict[str, Any]] = []
        for unit in units:
            self._checkpoint(transfer_deadline)
            index_payload = dict(unit["knowledge_index"])
            run_payload = dict(unit["run"])
            index_id = self._safe_identifier(
                index_payload.get("id"),
                field="index_id",
            )
            run_id = self._safe_identifier(
                run_payload.get("id"),
                field="run_id",
            )
            if str(run_payload.get("knowledge_index_id") or "") != index_id:
                raise ValueError(
                    "knowledge_index_worker_run_binding_mismatch"
                )
            index_binding, run_binding = self._materialization_bindings(
                job_id=job_id,
                envelope=envelope,
                index_id=index_id,
                run_id=run_id,
                bound_v2=True,
                projection_state=KNOWLEDGE_INDEX_PROJECTED_STATE,
            )
            self._assert_existing_materialization_bindings(
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
                self._verify_staged_file(
                    reference=reference,
                    path=output_dir / _OUTPUT_FILENAMES[role],
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
                        _MATERIALIZATION_BINDING_METADATA_KEY: index_binding,
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
                        _MATERIALIZATION_BINDING_METADATA_KEY: run_binding,
                    },
                }
            )
            saved_index = self._save_index(
                index_payload,
                expected_binding=index_binding,
            )
            saved_run = self._save_run(
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
    def _result_units(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        knowledge_index = result.get("knowledge_index")
        run = result.get("run")
        if isinstance(knowledge_index, Mapping) and isinstance(run, Mapping):
            return [{"knowledge_index": dict(knowledge_index), "run": dict(run)}]
        raw_units = result.get("results")
        if not isinstance(raw_units, list) or any(
            not isinstance(item, Mapping) for item in raw_units
        ):
            raise ValueError("knowledge_index_worker_result_units_invalid")
        units = [dict(item) for item in raw_units]
        if not units or any(
            not isinstance(unit.get("knowledge_index"), Mapping) or not isinstance(unit.get("run"), Mapping)
            for unit in units
        ):
            raise ValueError("knowledge_index_worker_result_units_invalid")
        return units

    @classmethod
    def _validate_result_reference_contract(
        cls,
        *,
        units: list[Mapping[str, Any]],
        references: list[Mapping[str, Any]],
        envelope: Mapping[str, Any],
        bound_v2: bool,
    ) -> None:
        maximum_units = 1 if bound_v2 else _MAX_RESULT_UNITS
        if len(units) > maximum_units:
            raise ValueError(
                "knowledge_index_worker_result_unit_limit_exceeded"
            )
        maximum_refs = min(
            _MAX_RESULT_ARTIFACT_REFS,
            len(units) * _MAX_REFS_PER_UNIT,
        )
        if len(references) > maximum_refs:
            raise ValueError(
                "knowledge_index_worker_artifact_ref_limit_exceeded"
            )

        unit_keys: set[tuple[str, str]] = set()
        for unit in units:
            index_payload = unit.get("knowledge_index")
            run_payload = unit.get("run")
            if not isinstance(index_payload, Mapping) or not isinstance(
                run_payload,
                Mapping,
            ):
                raise ValueError(
                    "knowledge_index_worker_result_units_invalid"
                )
            index_id = cls._safe_identifier(
                index_payload.get("id"),
                field="index_id",
            )
            run_id = cls._safe_identifier(
                run_payload.get("id"),
                field="run_id",
            )
            key = (index_id, run_id)
            if key in unit_keys:
                raise ValueError(
                    "knowledge_index_worker_result_unit_duplicate"
                )
            unit_keys.add(key)

        seen_artifact_ids: set[str] = set()
        seen_coordinates: set[tuple[str, str, str]] = set()
        refs_per_unit: dict[tuple[str, str], int] = {
            key: 0 for key in unit_keys
        }
        total_size = 0
        for reference in references:
            artifact_id = cls._safe_identifier(
                reference.get("artifact_id"),
                field="artifact_id",
            )
            role = str(reference.get("role") or "").strip()
            if role not in _OUTPUT_FILENAMES:
                raise ValueError(
                    "knowledge_index_worker_artifact_role_invalid"
                )
            index_id = cls._safe_identifier(
                reference.get("knowledge_index_id"),
                field="index_id",
            )
            run_id = cls._safe_identifier(
                reference.get("run_id"),
                field="run_id",
            )
            unit_key = (index_id, run_id)
            if unit_key not in unit_keys:
                raise ValueError(
                    "knowledge_index_worker_artifact_ref_unreferenced"
                )
            coordinates = (index_id, run_id, role)
            if (
                artifact_id in seen_artifact_ids
                or coordinates in seen_coordinates
            ):
                raise ValueError(
                    "knowledge_index_worker_artifact_ref_duplicate"
                )
            seen_artifact_ids.add(artifact_id)
            seen_coordinates.add(coordinates)
            refs_per_unit[unit_key] += 1
            if refs_per_unit[unit_key] > _MAX_REFS_PER_UNIT:
                raise ValueError(
                    "knowledge_index_worker_artifact_ref_limit_exceeded"
                )
            raw_size = reference.get("size_bytes")
            if (
                isinstance(raw_size, bool)
                or not isinstance(raw_size, int)
                or raw_size < 0
                or raw_size > _MAX_ARTIFACT_BYTES
            ):
                raise ValueError(
                    "knowledge_index_worker_artifact_size_invalid"
                )
            total_size += raw_size

        result_budget = _MAX_RESULT_ARTIFACT_BYTES
        if bound_v2:
            resources = envelope.get("resources")
            max_output_bytes = (
                resources.get("max_output_bytes")
                if isinstance(resources, Mapping)
                else None
            )
            if (
                isinstance(max_output_bytes, bool)
                or not isinstance(max_output_bytes, int)
                or max_output_bytes < 1
                or max_output_bytes > _MAX_RESULT_ARTIFACT_BYTES
            ):
                raise ValueError(
                    "knowledge_index_worker_output_budget_invalid"
                )
            result_budget = max_output_bytes
        if total_size > result_budget:
            raise ValueError(
                "knowledge_index_worker_output_budget_exceeded"
            )

    @classmethod
    def _materialization_bindings(
        cls,
        *,
        job_id: str,
        envelope: Mapping[str, Any],
        index_id: str,
        run_id: str,
        bound_v2: bool,
        projection_state: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_job_id = str(job_id or "").strip()
        if not _JOB_ID.fullmatch(normalized_job_id):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_invalid"
            )
        execution_job_schema = str(
            envelope.get("schema")
            or KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA
        )
        if execution_job_schema not in {
            KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA,
            KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
        } or bound_v2 != (
            execution_job_schema
            == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        ):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_invalid"
            )
        authority = envelope.get("authority_binding")
        assignment = envelope.get("assignment")
        authority_digest = str(
            authority.get("binding_digest")
            if isinstance(authority, Mapping)
            else ""
        ).strip()
        assignment_id = str(
            assignment.get("assignment_id")
            if isinstance(assignment, Mapping)
            else ""
        ).strip()
        if projection_state not in {
            _PENDING_PROJECTION_STATE,
            KNOWLEDGE_INDEX_PROJECTED_STATE,
        }:
            raise ValueError(
                "knowledge_index_worker_materialization_binding_invalid"
            )
        if bound_v2 and (
            len(authority_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in authority_digest
            )
            or not assignment_id
        ):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_invalid"
            )
        common = {
            "schema": _MATERIALIZATION_BINDING_SCHEMA,
            "execution_job_schema": execution_job_schema,
            "job_id": normalized_job_id,
            "authority_binding_digest": authority_digest,
            "assignment_id": assignment_id,
            "projection_state": projection_state,
        }
        return (
            {
                **common,
                "knowledge_index_id": index_id,
            },
            {
                **common,
                "knowledge_index_id": index_id,
                "run_id": run_id,
            },
        )

    def _assert_existing_materialization_bindings(
        self,
        *,
        index_id: str,
        run_id: str,
        index_binding: Mapping[str, Any],
        run_binding: Mapping[str, Any],
    ) -> None:
        existing_index = self._knowledge_index_repository.get_by_id(
            index_id
        )
        if existing_index is not None:
            self._require_existing_binding(
                existing_index,
                metadata_field="index_metadata",
                expected=index_binding,
            )
        existing_run = self._knowledge_index_run_repository.get_by_id(
            run_id
        )
        if existing_run is not None:
            if (
                str(
                    getattr(
                        existing_run,
                        "knowledge_index_id",
                        "",
                    )
                    or ""
                )
                != index_id
            ):
                raise ValueError(
                    "knowledge_index_worker_materialization_binding_conflict"
                )
            self._require_existing_binding(
                existing_run,
                metadata_field="run_metadata",
                expected=run_binding,
            )

    def _existing_materialized_unit(
        self,
        *,
        index_id: str,
        run_id: str,
        output_dir: Path,
        by_role: Mapping[str, Mapping[str, Any]],
        index_binding: Mapping[str, Any],
        run_binding: Mapping[str, Any],
    ) -> tuple[Any, Any] | None:
        """Return one exact local replay without reusing Worker authority."""

        existing_index = self._knowledge_index_repository.get_by_id(
            index_id
        )
        existing_run = self._knowledge_index_run_repository.get_by_id(run_id)
        if existing_index is None or existing_run is None:
            return None
        self._require_existing_binding(
            existing_index,
            metadata_field="index_metadata",
            expected=index_binding,
        )
        self._require_existing_binding(
            existing_run,
            metadata_field="run_metadata",
            expected=run_binding,
        )
        expected_output_dir = str(output_dir)
        expected_manifest_path = str(output_dir / "manifest.json")
        index_projection_state = self._existing_projection_state(
            existing_index,
            metadata_field="index_metadata",
        )
        run_projection_state = self._existing_projection_state(
            existing_run,
            metadata_field="run_metadata",
        )
        expected_status = (
            "completed"
            if index_projection_state == KNOWLEDGE_INDEX_PROJECTED_STATE
            else "pending_verification"
        )
        if (
            index_projection_state != run_projection_state
            or index_projection_state
            not in {
                _PENDING_PROJECTION_STATE,
                KNOWLEDGE_INDEX_PROJECTED_STATE,
            }
            or
            str(getattr(existing_index, "status", "") or "")
            != expected_status
            or str(getattr(existing_run, "status", "") or "")
            != expected_status
            or str(
                getattr(existing_run, "knowledge_index_id", "") or ""
            )
            != index_id
            or str(getattr(existing_index, "latest_run_id", "") or "")
            != run_id
            or str(getattr(existing_index, "output_dir", "") or "")
            != expected_output_dir
            or str(getattr(existing_run, "output_dir", "") or "")
            != expected_output_dir
            or str(
                getattr(existing_index, "manifest_path", "") or ""
            )
            != expected_manifest_path
            or str(getattr(existing_run, "manifest_path", "") or "")
            != expected_manifest_path
        ):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_conflict"
            )
        if output_dir.is_symlink() or not output_dir.is_dir():
            return None
        for role, reference in by_role.items():
            self._verify_staged_file(
                reference=reference,
                path=output_dir / _OUTPUT_FILENAMES[role],
            )
        return existing_index, existing_run

    @staticmethod
    def _require_existing_binding(
        item: Any,
        *,
        metadata_field: str,
        expected: Mapping[str, Any],
    ) -> None:
        metadata = dict(getattr(item, metadata_field, None) or {})
        existing = metadata.get(
            _MATERIALIZATION_BINDING_METADATA_KEY
        )
        if not isinstance(existing, Mapping):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_conflict"
            )
        existing_binding = dict(existing)
        expected_binding = dict(expected)
        existing_state = existing_binding.pop("projection_state", None)
        expected_state = expected_binding.pop("projection_state", None)
        if (
            existing_binding != expected_binding
            or existing_state
            not in {
                _PENDING_PROJECTION_STATE,
                KNOWLEDGE_INDEX_PROJECTED_STATE,
            }
            or expected_state
            not in {
                _PENDING_PROJECTION_STATE,
                KNOWLEDGE_INDEX_PROJECTED_STATE,
            }
        ):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_conflict"
            )

    @staticmethod
    def _existing_projection_state(
        item: Any,
        *,
        metadata_field: str,
    ) -> str:
        metadata = dict(getattr(item, metadata_field, None) or {})
        binding = metadata.get(_MATERIALIZATION_BINDING_METADATA_KEY)
        return str(
            binding.get("projection_state")
            if isinstance(binding, Mapping)
            else ""
        )

    @staticmethod
    def _source_scope(envelope: Mapping[str, Any]) -> str:
        job_type = str(envelope.get("job_type") or "")
        scope = str(envelope.get("source_scope") or "").strip().lower() if job_type == "source_records" else "artifact"
        if scope not in {
            "artifact",
            "wiki",
            "repo_path",
            "registered_workspace",
            "local_directory",
            "github",
            "generic_git",
        }:
            raise ValueError("knowledge_index_worker_source_scope_invalid")
        return scope

    @staticmethod
    def _validate_index_source_binding(
        *,
        index_payload: Mapping[str, Any],
        source_scope: str,
        source_id: str,
        source_revision_id: str,
        source_revision_digest: str,
    ) -> None:
        metadata = index_payload.get("index_metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("knowledge_index_worker_domain_supplement_binding_invalid")
        if (
            len(source_revision_id) != 69
            or not source_revision_id.startswith("srev_")
            or any(
                character not in "0123456789abcdef"
                for character in source_revision_id[5:]
            )
            or len(source_revision_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in source_revision_digest
            )
            or source_id != f"bound-source:{source_revision_id}"
            or str(metadata.get("source_scope") or "") != source_scope
            or str(metadata.get("source_id") or "") != source_id
        ):
            raise ValueError("knowledge_index_worker_domain_supplement_binding_invalid")
        for field, expected in (
            ("source_revision_id", source_revision_id),
            ("source_revision_digest", source_revision_digest),
        ):
            supplied = metadata.get(field)
            if supplied not in {None, "", expected}:
                raise ValueError(
                    "knowledge_index_worker_domain_supplement_binding_invalid"
                )

    @staticmethod
    def _safe_identifier(value: Any, *, field: str) -> str:
        normalized = str(value or "").strip()
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        if not normalized or len(normalized) > 256 or any(char not in allowed for char in normalized):
            raise ValueError(f"knowledge_index_worker_{field}_invalid")
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

    @staticmethod
    def _validate_unit_reference_budget(
        by_role: Mapping[str, Mapping[str, Any]],
    ) -> None:
        total_size = 0
        for role, reference in by_role.items():
            expected_filename = _OUTPUT_FILENAMES.get(role)
            if (
                expected_filename is None
                or str(reference.get("filename") or "") != expected_filename
            ):
                raise ValueError("knowledge_index_worker_artifact_role_invalid")
            raw_size = reference.get("size_bytes")
            if (
                isinstance(raw_size, bool)
                or not isinstance(raw_size, int)
                or raw_size < 0
                or raw_size > _MAX_ARTIFACT_BYTES
            ):
                raise ValueError("knowledge_index_worker_artifact_size_invalid")
            if role in _PRIMARY_GRAPH_ROLES and raw_size > _MAX_GRAPH_JSON_BYTES:
                raise ValueError("knowledge_index_worker_graph_artifact_too_large")
            if (
                role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE
                and raw_size > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES
            ):
                raise ValueError("knowledge_index_worker_domain_supplement_too_large")
            total_size += raw_size
            if total_size > _MAX_UNIT_ARTIFACT_BYTES:
                raise ValueError("knowledge_index_worker_artifact_unit_budget_exceeded")

    def _stage_reference(
        self,
        *,
        worker_url: str,
        worker_token: str,
        source_access_manifest: Mapping[str, Any] | None,
        job_id: str,
        reference: Mapping[str, Any],
        destination: Path,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> None:
        streaming_download = getattr(self._downloader, "download_to_path", None)
        if callable(streaming_download):
            transport = {
                "worker_url": worker_url,
                "worker_token": worker_token,
                "reference": reference,
                "destination": destination,
            }
            if source_access_manifest is not None:
                transport.update(
                    {
                        "source_access_manifest": source_access_manifest,
                        "job_id": job_id,
                    }
                )
            if transfer_deadline is not None:
                transport["transfer_deadline"] = transfer_deadline
            streaming_download(
                **transport,
            )
            self._verify_staged_file(reference=reference, path=destination)
            return

        transport = {
            "worker_url": worker_url,
            "worker_token": worker_token,
            "reference": reference,
        }
        if source_access_manifest is not None:
            transport.update(
                {
                    "source_access_manifest": source_access_manifest,
                    "job_id": job_id,
                }
            )
        if transfer_deadline is not None:
            transport["transfer_deadline"] = transfer_deadline
        content = self._downloader.download(**transport)
        try:
            self._verify_downloaded_content(reference=reference, content=content)
            with destination.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            del content
        self._verify_staged_file(reference=reference, path=destination)

    @classmethod
    def _verify_staged_file(
        cls,
        *,
        reference: Mapping[str, Any],
        path: Path,
    ) -> None:
        if path.is_symlink() or not path.is_file():
            raise ValueError("knowledge_index_worker_artifact_staging_invalid")
        raw_size = reference.get("size_bytes")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int):
            raise ValueError("knowledge_index_worker_artifact_size_invalid")
        if path.stat().st_size != raw_size:
            raise ValueError("knowledge_index_worker_artifact_size_mismatch")
        if cls._file_sha256(path) != str(reference.get("sha256") or "").lower():
            raise ValueError("knowledge_index_worker_artifact_digest_mismatch")

    @classmethod
    def _promote_staging(
        cls,
        *,
        staging_dir: Path,
        output_dir: Path,
        staged_paths: Mapping[str, Path],
    ) -> None:
        if output_dir.exists():
            if output_dir.is_symlink() or not output_dir.is_dir():
                raise ValueError("knowledge_index_worker_artifact_output_invalid")
            for role, staged_path in staged_paths.items():
                target = output_dir / _OUTPUT_FILENAMES[role]
                if (
                    target.is_symlink()
                    or not target.is_file()
                    or target.stat().st_size != staged_path.stat().st_size
                    or cls._file_sha256(target) != cls._file_sha256(staged_path)
                ):
                    raise ValueError("knowledge_index_worker_artifact_conflict")
            return
        os.replace(staging_dir, output_dir)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_DOWNLOAD_CHUNK_BYTES), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _verify_downloaded_content(
        *,
        reference: Mapping[str, Any],
        content: bytes,
    ) -> None:
        raw_size = reference.get("size_bytes")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
            raise ValueError("knowledge_index_worker_artifact_size_invalid")
        expected_size = raw_size
        expected_hash = str(reference.get("sha256") or "").lower()
        if len(content) != expected_size:
            raise ValueError("knowledge_index_worker_artifact_size_mismatch")
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError("knowledge_index_worker_artifact_digest_mismatch")

    def _validate_graph_artifacts(
        self,
        *,
        by_role: Mapping[str, Mapping[str, Any]],
        staged_paths: Mapping[str, Path],
        knowledge_index_id: str,
        source_scope: str,
        source_id: str,
        source_revision_id: str,
        source_revision_digest: str,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> dict[str, Any]:
        graph_reference = by_role["graph_index"]
        metrics_reference = by_role["graph_visual_metrics"]
        for role, reference in (
            ("graph_index", graph_reference),
            ("graph_visual_metrics", metrics_reference),
        ):
            if (
                str(reference.get("media_type") or "").lower()
                != _GRAPH_MEDIA_TYPES[role]
            ):
                raise ValueError(
                    "knowledge_index_worker_graph_artifact_media_type_invalid"
                )
        graph = self._strict_json_object(staged_paths["graph_index"])
        state = graph.get("state")
        if not isinstance(state, Mapping):
            raise ValueError("knowledge_index_worker_graph_artifact_revision_mismatch")
        graph_revision = str(state.get("manifest_hash") or "")
        graph_schema = str(state.get("schema") or "")
        del graph

        metrics = self._strict_json_object(staged_paths["graph_visual_metrics"])
        if (
            graph_schema != "codecompass_graph_index.v1"
            or str(graph_reference.get("artifact_schema") or "")
            != "codecompass_graph_index.v1"
            or str(metrics.get("schema") or "") != "graph_visual_metrics.v1"
            or str(metrics_reference.get("artifact_schema") or "")
            != "graph_visual_metrics.v1"
            or not graph_revision
            or str(metrics.get("graph_revision") or "") != graph_revision
            or str(graph_reference.get("graph_revision") or "") != graph_revision
            or str(metrics_reference.get("graph_revision") or "") != graph_revision
            or not graph_revision.startswith("sha256:")
            or len(graph_revision) != 71
        ):
            raise ValueError("knowledge_index_worker_graph_artifact_revision_mismatch")

        graph_file_hash = "sha256:" + self._file_sha256(staged_paths["graph_index"])
        if str(graph_reference.get("graph_content_hash") or "") != graph_file_hash:
            raise ValueError(
                "knowledge_index_worker_graph_artifact_content_hash_mismatch"
            )
        unsigned_metrics = {
            key: value for key, value in metrics.items() if key != "content_hash"
        }
        try:
            canonical_metrics = json.dumps(
                unsigned_metrics,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "knowledge_index_worker_graph_visual_metrics_invalid"
            ) from exc
        metrics_content_hash = "sha256:" + hashlib.sha256(canonical_metrics).hexdigest()
        if (
            str(metrics.get("content_hash") or "") != metrics_content_hash
            or str(metrics_reference.get("graph_content_hash") or "")
            != metrics_content_hash
        ):
            raise ValueError(
                "knowledge_index_worker_graph_visual_metrics_hash_mismatch"
            )
        binding: dict[str, Any] = {
            "schema": "codecompass_graph_artifact_binding.v1",
            "graph_revision": graph_revision,
        }
        supplement_reference = by_role.get(DOMAIN_SUPPLEMENT_OUTPUT_ROLE)
        if supplement_reference is None:
            return binding
        if DOMAIN_SUPPLEMENT_OUTPUT_ROLE not in staged_paths:
            raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
        logical_content_hash = str(supplement_reference.get("graph_content_hash") or "")
        artifact_sha256 = str(supplement_reference.get("sha256") or "").lower()
        if (
            str(supplement_reference.get("media_type") or "").lower()
            != DOMAIN_SUPPLEMENT_MEDIA_TYPE
            or str(supplement_reference.get("artifact_schema") or "")
            != DOMAIN_SUPPLEMENT_SCHEMA
            or str(supplement_reference.get("graph_revision") or "") != graph_revision
            or str(supplement_reference.get("source_revision_id") or "")
            != source_revision_id
            or str(supplement_reference.get("source_revision_digest") or "")
            != source_revision_digest
            or not logical_content_hash.startswith("sha256:")
            or len(logical_content_hash) != 71
            or any(
                character not in "0123456789abcdef"
                for character in logical_content_hash[7:]
            )
            or len(artifact_sha256) != 64
            or any(character not in "0123456789abcdef" for character in artifact_sha256)
        ):
            raise ValueError("knowledge_index_worker_domain_supplement_binding_invalid")
        validation_options = (
            {"checkpoint": transfer_deadline.require_remaining_seconds}
            if transfer_deadline is not None
            else {}
        )
        catalog = self._domain_supplement_reader.validate_artifact(
            path=staged_paths[DOMAIN_SUPPLEMENT_OUTPUT_ROLE],
            binding=CodeCompassDomainSupplementBinding(
                knowledge_index_id=knowledge_index_id,
                source_revision_id=source_revision_id,
                source_revision_digest=source_revision_digest,
                graph_revision=graph_revision,
                artifact_sha256=artifact_sha256,
                logical_content_hash=logical_content_hash,
                source_scope=source_scope,
                source_id=source_id,
            ),
            **validation_options,
        )
        expected_counts = {
            "domain_count": len(catalog.domains),
            "semantic_node_count": sum(
                domain.semantic_node_count for domain in catalog.domains
            ),
            "semantic_edge_count": sum(
                domain.semantic_edge_count for domain in catalog.domains
            ),
            "declaration_edge_count": sum(
                domain.declaration_edge_count for domain in catalog.domains
            ),
        }
        if any(
            isinstance(supplement_reference.get(field), bool)
            or supplement_reference.get(field) != expected
            for field, expected in expected_counts.items()
        ):
            raise ValueError("knowledge_index_worker_domain_supplement_count_mismatch")
        binding["domain_supplement"] = {
            "artifact_schema": DOMAIN_SUPPLEMENT_SCHEMA,
            "media_type": DOMAIN_SUPPLEMENT_MEDIA_TYPE,
            "graph_revision": graph_revision,
            "content_hash": logical_content_hash,
            "source_scope": source_scope,
            "source_id": source_id,
            "source_revision_id": source_revision_id,
            "source_revision_digest": source_revision_digest,
            **expected_counts,
        }
        return binding

    @staticmethod
    def _strict_json_object(path: Path) -> dict[str, Any]:
        def reject_constant(_value: str) -> None:
            raise ValueError("non_finite_json_number")

        if path.is_symlink() or not path.is_file():
            raise ValueError("knowledge_index_worker_graph_artifact_staging_invalid")
        with path.open("rb") as handle:
            content = handle.read(_MAX_GRAPH_JSON_BYTES + 1)
        if len(content) > _MAX_GRAPH_JSON_BYTES:
            raise ValueError("knowledge_index_worker_graph_artifact_too_large")
        try:
            payload = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("knowledge_index_worker_graph_artifact_json_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("knowledge_index_worker_graph_artifact_json_invalid")
        return dict(payload)

    @staticmethod
    def _local_graph_binding(
        binding: Mapping[str, Any],
        *,
        by_role: Mapping[str, Mapping[str, Any]],
        output_dir: Path,
    ) -> dict[str, Any]:
        def artifact(role: str) -> dict[str, Any]:
            reference = by_role[role]
            projected = {
                "artifact_id": str(reference.get("artifact_id") or ""),
                "artifact_schema": str(reference.get("artifact_schema") or ""),
                "sha256": str(reference.get("sha256") or ""),
                "content_hash": str(reference.get("graph_content_hash") or ""),
                "filename": _OUTPUT_FILENAMES[role],
                "local_path": str(output_dir / _OUTPUT_FILENAMES[role]),
            }
            if role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE:
                projected.update(
                    {
                        "media_type": str(reference.get("media_type") or ""),
                        "graph_revision": str(reference.get("graph_revision") or ""),
                        "source_scope": str(
                            reference.get("source_scope") or "repo_path"
                        ),
                        "source_id": str(reference.get("source_id") or ""),
                        "source_revision_id": str(
                            reference.get("source_revision_id") or ""
                        ),
                        "source_revision_digest": str(
                            reference.get("source_revision_digest") or ""
                        ),
                        "domain_count": reference.get("domain_count"),
                        "semantic_node_count": reference.get("semantic_node_count"),
                        "semantic_edge_count": reference.get("semantic_edge_count"),
                        "declaration_edge_count": reference.get(
                            "declaration_edge_count"
                        ),
                    }
                )
            return projected

        local_binding = {
            **dict(binding),
            "graph_index": artifact("graph_index"),
            "visual_metrics": artifact("graph_visual_metrics"),
        }
        if DOMAIN_SUPPLEMENT_OUTPUT_ROLE in by_role:
            supplement = artifact(DOMAIN_SUPPLEMENT_OUTPUT_ROLE)
            admitted = binding.get("domain_supplement")
            if isinstance(admitted, Mapping):
                supplement.update(dict(admitted))
            local_binding["domain_supplement"] = supplement
        return local_binding

    def _save_index(
        self,
        payload: Mapping[str, Any],
        *,
        expected_binding: Mapping[str, Any],
    ) -> KnowledgeIndexDB:
        allowed = set(KnowledgeIndexDB.model_fields)
        values = {key: value for key, value in payload.items() if key in allowed}
        candidate = KnowledgeIndexDB.model_validate(values)
        existing = self._knowledge_index_repository.get_by_id(candidate.id)
        if existing is not None:
            self._require_existing_binding(
                existing,
                metadata_field="index_metadata",
                expected=expected_binding,
            )
            if (
                self._existing_projection_state(
                    existing,
                    metadata_field="index_metadata",
                )
                == KNOWLEDGE_INDEX_PROJECTED_STATE
                and expected_binding.get("projection_state")
                == _PENDING_PROJECTION_STATE
            ):
                candidate.status = existing.status
                candidate.index_metadata = dict(
                    existing.index_metadata or {}
                )
            for field in allowed - {"id"}:
                setattr(existing, field, getattr(candidate, field))
            candidate = existing
        return self._knowledge_index_repository.save(candidate)

    def _save_run(
        self,
        payload: Mapping[str, Any],
        *,
        expected_binding: Mapping[str, Any],
    ) -> KnowledgeIndexRunDB:
        allowed = set(KnowledgeIndexRunDB.model_fields)
        values = {key: value for key, value in payload.items() if key in allowed}
        candidate = KnowledgeIndexRunDB.model_validate(values)
        existing = self._knowledge_index_run_repository.get_by_id(candidate.id)
        if existing is not None:
            if existing.knowledge_index_id != candidate.knowledge_index_id:
                raise ValueError(
                    "knowledge_index_worker_materialization_binding_conflict"
                )
            self._require_existing_binding(
                existing,
                metadata_field="run_metadata",
                expected=expected_binding,
            )
            if (
                self._existing_projection_state(
                    existing,
                    metadata_field="run_metadata",
                )
                == KNOWLEDGE_INDEX_PROJECTED_STATE
                and expected_binding.get("projection_state")
                == _PENDING_PROJECTION_STATE
            ):
                candidate.status = existing.status
                candidate.run_metadata = dict(existing.run_metadata or {})
            for field in allowed - {"id"}:
                setattr(existing, field, getattr(candidate, field))
            candidate = existing
        return self._knowledge_index_run_repository.save(candidate)


__all__ = [
    "HttpKnowledgeIndexWorkerArtifactDownloader",
    "KnowledgeIndexWorkerArtifactDownloaderPort",
    "KnowledgeIndexWorkerStreamingArtifactDownloaderPort",
    "KnowledgeIndexWorkerArtifactService",
]
