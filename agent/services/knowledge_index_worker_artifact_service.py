"""Hub-side admission of worker-produced knowledge-index artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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

_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
_MAX_UNIT_ARTIFACT_BYTES = 384 * 1024 * 1024
_MAX_GRAPH_JSON_BYTES = 32 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_OUTPUT_FILENAMES = {
    "manifest": "manifest.json",
    "index": "index.jsonl",
    "details": "details.jsonl",
    "relations": "relations.jsonl",
    "graph_index": "cc_graph_index.json",
    "graph_visual_metrics": "cc_graph_index.visual_metrics.json",
}
_GRAPH_ROLES = frozenset({"graph_index", "graph_visual_metrics"})
_GRAPH_MEDIA_TYPES = {
    "graph_index": "application/vnd.ananta.codecompass-graph-index+json",
    "graph_visual_metrics": "application/vnd.ananta.codecompass-graph-visual-metrics+json",
}


class KnowledgeIndexWorkerArtifactDownloaderPort(Protocol):
    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
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
    ) -> None: ...


class HttpKnowledgeIndexWorkerArtifactDownloader:
    """Download one bounded artifact from the assigned worker only."""

    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
    ) -> bytes:
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
        expected_size = raw_size
        expected_hash = str(reference.get("sha256") or "").lower()
        if not artifact_id or expected_size < 0 or expected_size > _MAX_ARTIFACT_BYTES:
            raise ValueError("knowledge_index_worker_artifact_ref_invalid")
        if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
            raise ValueError("knowledge_index_worker_artifact_digest_invalid")
        base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        encoded_id = urllib.parse.quote(artifact_id, safe="")
        headers = {"Authorization": f"Bearer {worker_token}"} if worker_token else {}
        request = urllib.request.Request(
            f"{base_url}/artifacts/{encoded_id}/content",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_size:
                raise ValueError("knowledge_index_worker_artifact_size_mismatch")
            content = response.read(expected_size + 1)
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
    ) -> None:
        """Stream one verified worker artifact directly into Hub staging."""

        request, expected_size, expected_hash = self._request(
            worker_url=worker_url,
            worker_token=worker_token,
            reference=reference,
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("knowledge_index_worker_artifact_staging_conflict")
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        hasher = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) != expected_size:
                    raise ValueError("knowledge_index_worker_artifact_size_mismatch")
                with destination.open("xb") as handle:
                    while True:
                        chunk = response.read(
                            min(_DOWNLOAD_CHUNK_BYTES, expected_size - written + 1)
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

    @staticmethod
    def _request(
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
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
        headers = {"Authorization": f"Bearer {worker_token}"} if worker_token else {}
        return (
            urllib.request.Request(
                f"{base_url}/artifacts/{encoded_id}/content",
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
    ) -> None:
        self._downloader = downloader or HttpKnowledgeIndexWorkerArtifactDownloader()
        if knowledge_index_repository is None or knowledge_index_run_repository is None:
            from agent.repository import knowledge_index_repo, knowledge_index_run_repo

            knowledge_index_repository = knowledge_index_repository or knowledge_index_repo
            knowledge_index_run_repository = knowledge_index_run_repository or knowledge_index_run_repo
        self._knowledge_index_repository = knowledge_index_repository
        self._knowledge_index_run_repository = knowledge_index_run_repository
        self._output_root = Path(output_root or Path(settings.data_dir) / "knowledge_indices").resolve()
        self._manifest_projector = (
            manifest_projector or CodeCompassArtifactManifestProjector()
        )

    def materialize(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> dict[str, Any]:
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
        if not worker_url or not worker_token:
            raise ValueError("knowledge_index_worker_artifact_transport_unavailable")

        units = self._result_units(normalized)
        references = [dict(item) for item in list(normalized.get("artifact_refs") or [])]
        materialized_units: list[dict[str, Any]] = []
        for unit in units:
            index_payload = dict(unit["knowledge_index"])
            run_payload = dict(unit["run"])
            index_id = self._safe_identifier(index_payload.get("id"), field="index_id")
            run_id = self._safe_identifier(run_payload.get("id"), field="run_id")
            if str(run_payload.get("knowledge_index_id") or index_id) != index_id:
                raise ValueError("knowledge_index_worker_run_binding_mismatch")
            unit_refs = [
                reference
                for reference in references
                if str(reference.get("knowledge_index_id") or "") == index_id
                and str(reference.get("run_id") or "") == run_id
            ]
            by_role = {str(reference.get("role") or ""): reference for reference in unit_refs}
            if len(by_role) != len(unit_refs) or not {"manifest", "index"}.issubset(by_role):
                raise ValueError("knowledge_index_worker_artifacts_incomplete")
            present_graph_roles = _GRAPH_ROLES.intersection(by_role)
            if present_graph_roles and present_graph_roles != _GRAPH_ROLES:
                raise ValueError("knowledge_index_worker_graph_artifacts_incomplete")
            output_dir = self._output_root / source_scope / index_id / run_id
            self._validate_unit_reference_budget(by_role)
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
                        reference=reference,
                        destination=destination,
                    )
                    staged_paths[role] = destination
                graph_binding = (
                    self._validate_graph_artifacts(
                        by_role=by_role,
                        staged_paths=staged_paths,
                    )
                    if present_graph_roles
                    else None
                )
                source_revision_id = str(
                    envelope.get("source_revision_id") or ""
                ).strip()
                public_artifact_manifest: dict[str, Any] | None = None
                if source_revision_id:
                    raw_manifest = self._strict_json_object(
                        staged_paths["manifest"]
                    )
                    raw_coverage = raw_manifest.get("coverage")
                    raw_exclusions = raw_manifest.get("exclusions")
                    if raw_exclusions is not None and (
                        not isinstance(raw_exclusions, list)
                        or any(
                            not isinstance(item, Mapping)
                            for item in raw_exclusions
                        )
                    ):
                        raise ValueError(
                            "knowledge_index_worker_exclusions_invalid"
                        )
                    public_artifact_manifest = self._manifest_projector.project(
                        knowledge_index_id=index_id,
                        run_id=run_id,
                        source_revision_id=source_revision_id,
                        references=list(by_role.values()),
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
            index_payload.update(
                {
                    "source_scope": source_scope,
                    "status": "completed",
                    "output_dir": str(output_dir),
                    "manifest_path": str(manifest_path),
                    "latest_run_id": run_id,
                }
            )
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
                    "status": "completed",
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
            saved_index = self._save_index(index_payload)
            saved_run = self._save_run(run_payload)
            materialized_units.append(
                {
                    **unit,
                    "knowledge_index": saved_index.model_dump(),
                    "run": saved_run.model_dump(),
                }
            )

        if normalized.get("knowledge_index") is not None:
            normalized["knowledge_index"] = materialized_units[0]["knowledge_index"]
            normalized["run"] = materialized_units[0]["run"]
        else:
            normalized["results"] = materialized_units
        return normalized

    @staticmethod
    def _result_units(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        knowledge_index = result.get("knowledge_index")
        run = result.get("run")
        if isinstance(knowledge_index, Mapping) and isinstance(run, Mapping):
            return [{"knowledge_index": dict(knowledge_index), "run": dict(run)}]
        units = [dict(item) for item in list(result.get("results") or []) if isinstance(item, Mapping)]
        if not units or any(
            not isinstance(unit.get("knowledge_index"), Mapping) or not isinstance(unit.get("run"), Mapping)
            for unit in units
        ):
            raise ValueError("knowledge_index_worker_result_units_invalid")
        return units

    @staticmethod
    def _source_scope(envelope: Mapping[str, Any]) -> str:
        job_type = str(envelope.get("job_type") or "")
        scope = str(envelope.get("source_scope") or "").strip().lower() if job_type == "source_records" else "artifact"
        if scope not in {"artifact", "wiki", "repo_path"}:
            raise ValueError("knowledge_index_worker_source_scope_invalid")
        return scope

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
            if expected_filename is None or str(reference.get("filename") or "") != expected_filename:
                raise ValueError("knowledge_index_worker_artifact_role_invalid")
            raw_size = reference.get("size_bytes")
            if (
                isinstance(raw_size, bool)
                or not isinstance(raw_size, int)
                or raw_size < 0
                or raw_size > _MAX_ARTIFACT_BYTES
            ):
                raise ValueError("knowledge_index_worker_artifact_size_invalid")
            if role in _GRAPH_ROLES and raw_size > _MAX_GRAPH_JSON_BYTES:
                raise ValueError("knowledge_index_worker_graph_artifact_too_large")
            total_size += raw_size
            if total_size > _MAX_UNIT_ARTIFACT_BYTES:
                raise ValueError("knowledge_index_worker_artifact_unit_budget_exceeded")

    def _stage_reference(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        destination: Path,
    ) -> None:
        streaming_download = getattr(self._downloader, "download_to_path", None)
        if callable(streaming_download):
            streaming_download(
                worker_url=worker_url,
                worker_token=worker_token,
                reference=reference,
                destination=destination,
            )
            self._verify_staged_file(reference=reference, path=destination)
            return

        content = self._downloader.download(
            worker_url=worker_url,
            worker_token=worker_token,
            reference=reference,
        )
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

    @classmethod
    def _validate_graph_artifacts(
        cls,
        *,
        by_role: Mapping[str, Mapping[str, Any]],
        staged_paths: Mapping[str, Path],
    ) -> dict[str, Any]:
        graph_reference = by_role["graph_index"]
        metrics_reference = by_role["graph_visual_metrics"]
        for role, reference in (
            ("graph_index", graph_reference),
            ("graph_visual_metrics", metrics_reference),
        ):
            if str(reference.get("media_type") or "").lower() != _GRAPH_MEDIA_TYPES[role]:
                raise ValueError("knowledge_index_worker_graph_artifact_media_type_invalid")
        graph = cls._strict_json_object(staged_paths["graph_index"])
        state = graph.get("state")
        if not isinstance(state, Mapping):
            raise ValueError("knowledge_index_worker_graph_artifact_revision_mismatch")
        graph_revision = str(state.get("manifest_hash") or "")
        graph_schema = str(state.get("schema") or "")
        del graph

        metrics = cls._strict_json_object(staged_paths["graph_visual_metrics"])
        if (
            graph_schema != "codecompass_graph_index.v1"
            or str(graph_reference.get("artifact_schema") or "") != "codecompass_graph_index.v1"
            or str(metrics.get("schema") or "") != "graph_visual_metrics.v1"
            or str(metrics_reference.get("artifact_schema") or "") != "graph_visual_metrics.v1"
            or not graph_revision
            or str(metrics.get("graph_revision") or "") != graph_revision
            or str(graph_reference.get("graph_revision") or "") != graph_revision
            or str(metrics_reference.get("graph_revision") or "") != graph_revision
            or not graph_revision.startswith("sha256:")
            or len(graph_revision) != 71
        ):
            raise ValueError("knowledge_index_worker_graph_artifact_revision_mismatch")

        graph_file_hash = "sha256:" + cls._file_sha256(staged_paths["graph_index"])
        if str(graph_reference.get("graph_content_hash") or "") != graph_file_hash:
            raise ValueError("knowledge_index_worker_graph_artifact_content_hash_mismatch")
        unsigned_metrics = {key: value for key, value in metrics.items() if key != "content_hash"}
        try:
            canonical_metrics = json.dumps(
                unsigned_metrics,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("knowledge_index_worker_graph_visual_metrics_invalid") from exc
        metrics_content_hash = "sha256:" + hashlib.sha256(canonical_metrics).hexdigest()
        if (
            str(metrics.get("content_hash") or "") != metrics_content_hash
            or str(metrics_reference.get("graph_content_hash") or "") != metrics_content_hash
        ):
            raise ValueError("knowledge_index_worker_graph_visual_metrics_hash_mismatch")
        return {
            "schema": "codecompass_graph_artifact_binding.v1",
            "graph_revision": graph_revision,
        }

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
            return {
                "artifact_id": str(reference.get("artifact_id") or ""),
                "artifact_schema": str(reference.get("artifact_schema") or ""),
                "sha256": str(reference.get("sha256") or ""),
                "content_hash": str(reference.get("graph_content_hash") or ""),
                "filename": _OUTPUT_FILENAMES[role],
                "local_path": str(output_dir / _OUTPUT_FILENAMES[role]),
            }

        return {
            **dict(binding),
            "graph_index": artifact("graph_index"),
            "visual_metrics": artifact("graph_visual_metrics"),
        }

    def _save_index(self, payload: Mapping[str, Any]) -> KnowledgeIndexDB:
        allowed = set(KnowledgeIndexDB.model_fields)
        values = {key: value for key, value in payload.items() if key in allowed}
        candidate = KnowledgeIndexDB.model_validate(values)
        existing = self._knowledge_index_repository.get_by_id(candidate.id)
        if existing is not None:
            for field in allowed - {"id"}:
                setattr(existing, field, getattr(candidate, field))
            candidate = existing
        return self._knowledge_index_repository.save(candidate)

    def _save_run(self, payload: Mapping[str, Any]) -> KnowledgeIndexRunDB:
        allowed = set(KnowledgeIndexRunDB.model_fields)
        values = {key: value for key, value in payload.items() if key in allowed}
        candidate = KnowledgeIndexRunDB.model_validate(values)
        existing = self._knowledge_index_run_repository.get_by_id(candidate.id)
        if existing is not None:
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
