"""Hub-side admission of worker-produced knowledge-index artifacts."""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.config import settings
from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB

_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
_OUTPUT_FILENAMES = {
    "manifest": "manifest.json",
    "index": "index.jsonl",
    "details": "details.jsonl",
    "relations": "relations.jsonl",
}


class KnowledgeIndexWorkerArtifactDownloaderPort(Protocol):
    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
    ) -> bytes: ...


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
        expected_size = int(reference.get("size_bytes") or -1)
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


class KnowledgeIndexWorkerArtifactService:
    """Verify, materialize and persist one terminal worker index result."""

    def __init__(
        self,
        *,
        downloader: KnowledgeIndexWorkerArtifactDownloaderPort | None = None,
        knowledge_index_repository: Any | None = None,
        knowledge_index_run_repository: Any | None = None,
        output_root: str | Path | None = None,
    ) -> None:
        self._downloader = downloader or HttpKnowledgeIndexWorkerArtifactDownloader()
        if knowledge_index_repository is None or knowledge_index_run_repository is None:
            from agent.repository import knowledge_index_repo, knowledge_index_run_repo

            knowledge_index_repository = knowledge_index_repository or knowledge_index_repo
            knowledge_index_run_repository = knowledge_index_run_repository or knowledge_index_run_repo
        self._knowledge_index_repository = knowledge_index_repository
        self._knowledge_index_run_repository = knowledge_index_run_repository
        self._output_root = Path(output_root or Path(settings.data_dir) / "knowledge_indices").resolve()

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
            output_dir = self._output_root / source_scope / index_id / run_id
            output_dir.mkdir(parents=True, exist_ok=True)
            for role, reference in sorted(by_role.items()):
                expected_filename = _OUTPUT_FILENAMES.get(role)
                if expected_filename is None or str(reference.get("filename") or "") != expected_filename:
                    raise ValueError("knowledge_index_worker_artifact_role_invalid")
                content = self._downloader.download(
                    worker_url=worker_url,
                    worker_token=worker_token,
                    reference=reference,
                )
                self._write_verified(output_dir / expected_filename, content)
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
            run_payload.update(
                {
                    "knowledge_index_id": index_id,
                    "status": "completed",
                    "output_dir": str(output_dir),
                    "manifest_path": str(manifest_path),
                }
            )
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
    def _write_verified(path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError("knowledge_index_worker_artifact_conflict")
            return
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

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
    "KnowledgeIndexWorkerArtifactService",
]
