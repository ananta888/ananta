from __future__ import annotations

import copy
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

from agent.config import settings
from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.metrics import KNOWLEDGE_INDEX_DURATION_SECONDS, KNOWLEDGE_INDEX_RUNS_TOTAL
from agent.repository import artifact_repo, artifact_version_repo, knowledge_index_repo, knowledge_index_run_repo
from agent.services._rag_helper_profile_catalog import (
    ALLOWED_OVERRIDE_KEYS,
    BOOL_OVERRIDE_KEYS,
    INTERNAL_PROFILE_CATALOG,
    PROFILE_KEY_ALIASES,
    PROFILE_SECTION_KEYS,
)
from agent.services.rag_helper_file_type_migration import plan_rag_helper_cache_migration
from agent.services.rag_helper_file_type_policy import RagHelperFileTypePolicy
from agent.services.rag_helper_file_type_run_support import (
    build_rag_helper_file_type_policy,
    csv_setting_values,
    enrich_rag_helper_file_type_manifest,
    narrow_rag_helper_processing_limits,
    observe_rag_helper_file_type_metrics,
    rag_helper_file_type_run_contract,
)
from agent.services.rag_helper_index_preview_reader import (
    build_knowledge_index_preview,
    load_index_manifest,
    load_jsonl_preview,
    load_partitioned_jsonl_preview,
)
from agent.services.rag_helper_index_run_recorder import KnowledgeIndexRunRecorder
from agent.services.rag_helper_module_loader import load_rag_helper_modules
from agent.services.rag_helper_profile_resolver import RagHelperProfileCatalog
from agent.services.rag_helper_repository_graph_port import (
    RepositoryGraphBuilderUnavailableError,
    RepositoryGraphOutputBuilderPort,
)
from agent.services.rag_helper_source_record_manifest import (
    manifest_summary_metadata,
    normalize_source_records,
    record_file_type_metrics_snapshot,
    source_record_files,
    source_record_manifest,
    wiki_codecompass_manifest,
)
from agent.services.rag_index_chunker import index_wiki_records_with_codecompass
from ananta_contracts import (  # noqa: F401 - historic exports of this module
    FileTypeRolloutPolicy,
    load_file_type_support_registry,
)

_LOGGER = logging.getLogger(__name__)


class RagHelperExecutionDeadlinePort(Protocol):
    """Minimal Worker-supplied deadline; Hub callers may omit it."""

    def checkpoint(self) -> None: ...


def _checkpoint(
    execution_deadline: RagHelperExecutionDeadlinePort | None,
) -> None:
    if execution_deadline is not None:
        execution_deadline.checkpoint()


class RagHelperIndexService:
    """Owns controlled rag-helper execution and persistence for artifact-backed indices."""

    DEFAULT_PROFILE_NAME = "default"
    VALID_SOURCE_SCOPES = {"artifact", "wiki", "repo_path"}
    INTERNAL_PROFILE_CATALOG = INTERNAL_PROFILE_CATALOG
    PROFILE_SECTION_KEYS = PROFILE_SECTION_KEYS
    PROFILE_KEY_ALIASES = PROFILE_KEY_ALIASES
    ALLOWED_OVERRIDE_KEYS = ALLOWED_OVERRIDE_KEYS
    BOOL_OVERRIDE_KEYS = BOOL_OVERRIDE_KEYS

    def __init__(
        self,
        *,
        knowledge_index_repository: Any | None = None,
        knowledge_index_run_repository: Any | None = None,
        artifact_repository: Any | None = None,
        artifact_version_repository: Any | None = None,
        profile_catalog: RagHelperProfileCatalog | None = None,
        repository_graph_builder: RepositoryGraphOutputBuilderPort | None = None,
    ) -> None:
        # Worker-composed (DIP): the Hub never imports the CodeCompass bridge.
        self._repository_graph_builder = repository_graph_builder
        self._knowledge_index_repo = (
            knowledge_index_repo if knowledge_index_repository is None else knowledge_index_repository
        )
        self._knowledge_index_run_repo = (
            knowledge_index_run_repo if knowledge_index_run_repository is None else knowledge_index_run_repository
        )
        self._artifact_repo = artifact_repo if artifact_repository is None else artifact_repository
        self._artifact_version_repo = (
            artifact_version_repo if artifact_version_repository is None else artifact_version_repository
        )
        self._profiles = profile_catalog or RagHelperProfileCatalog(
            helper_root=lambda: self._rag_helper_root(),
        )

    def with_repository_graph_builder(self, builder: RepositoryGraphOutputBuilderPort) -> "RagHelperIndexService":
        """Return a copy of this service that exports repository graphs through ``builder``."""
        bound = copy.copy(self)
        bound._repository_graph_builder = builder
        return bound

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _rag_helper_root(self) -> Path:
        return self._repo_root() / "rag-helper"

    def _file_type_contract_root(self) -> Path:
        """Return the immutable source root even when a scan root is injected."""

        return Path(__file__).resolve().parents[2]

    _csv_values = staticmethod(csv_setting_values)
    _file_type_run_contract = staticmethod(rag_helper_file_type_run_contract)
    _processing_limits = staticmethod(narrow_rag_helper_processing_limits)
    _observe_rag_helper_file_type_metrics = staticmethod(observe_rag_helper_file_type_metrics)
    _enrich_rag_helper_file_type_manifest = staticmethod(enrich_rag_helper_file_type_manifest)
    _load_manifest = staticmethod(load_index_manifest)
    _load_jsonl_preview = staticmethod(load_jsonl_preview)
    _load_partitioned_jsonl_preview = staticmethod(load_partitioned_jsonl_preview)

    def _rag_helper_file_type_policy(
        self,
        helper_modules: dict[str, Any],
    ) -> RagHelperFileTypePolicy:
        return build_rag_helper_file_type_policy(
            helper_modules,
            contract_root=self._file_type_contract_root(),
        )

    def _normalize_source_scope(self, source_scope: str | None) -> str:
        normalized = str(source_scope or "artifact").strip().lower() or "artifact"
        if normalized not in self.VALID_SOURCE_SCOPES:
            raise ValueError("invalid_source_scope")
        return normalized

    def _knowledge_output_root(self, *, source_scope: str = "artifact") -> Path:
        normalized_scope = self._normalize_source_scope(source_scope)
        output_root = Path(settings.data_dir) / "knowledge_indices" / normalized_scope
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root

    def _resolve_runtime_path(self, configured: str | None, *, output_dir: Path, fallback: Path | None) -> Path | None:
        if configured is None:
            return fallback
        value = str(configured).strip()
        if not value:
            return None
        return Path(value.replace("{out}", str(output_dir))).resolve()

    def list_profiles(self) -> list[dict[str, Any]]:
        return self._profiles.list_profiles()

    def suggest_profile_name(
        self,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        required_context_scope: str | None = None,
    ) -> str:
        return self._profiles.suggest_profile_name(
            task_kind=task_kind,
            retrieval_intent=retrieval_intent,
            required_context_scope=required_context_scope,
        )

    def _resolve_profile(self, profile_name: str | None, overrides: dict[str, Any] | None) -> dict[str, Any]:
        return self._profiles.resolve_profile(profile_name, overrides)

    def _ensure_helper_imports(self) -> dict[str, Any]:
        return load_rag_helper_modules(self._rag_helper_root())

    def _artifact_source_metadata(self, artifact_id: str) -> tuple[Path, str, set[str], dict[str, Any], Any]:
        artifact = self._artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            raise ValueError("artifact_not_found")
        if not artifact.latest_version_id:
            raise ValueError("artifact_version_not_found")
        version = self._artifact_version_repo.get_by_id(artifact.latest_version_id)
        if version is None:
            raise ValueError("artifact_version_not_found")

        raw_storage_path = Path(version.storage_path)
        source_path = raw_storage_path if raw_storage_path.is_absolute() else (self._repo_root() / raw_storage_path)
        source_path = source_path.resolve()
        if not source_path.exists():
            raise ValueError("artifact_storage_not_found")
        if not source_path.is_file():
            raise ValueError("artifact_storage_not_file")

        filename = Path(version.original_filename or source_path.name).name
        stored_filename = source_path.name
        ext = Path(filename).suffix.lower().lstrip(".") or source_path.suffix.lower().lstrip(".")
        extensions = {ext} if ext else {"md"}
        metadata = {
            "artifact_id": artifact.id,
            "artifact_version_id": version.id,
            "filename": filename,
            "stored_filename": stored_filename,
            "storage_path": str(source_path),
            "extensions": sorted(extensions),
        }
        return source_path, filename, extensions, metadata, version

    def _build_or_create_index(
        self,
        *,
        source_scope: str,
        scope_id: str,
        created_by: str | None,
        artifact_id: str | None = None,
        collection_id: str | None = None,
    ) -> KnowledgeIndexDB:
        normalized_scope = self._normalize_source_scope(source_scope)
        existing = self._knowledge_index_repo.get_by_scope(source_scope=normalized_scope, scope_id=scope_id)
        if existing is not None:
            return existing
        return self._knowledge_index_repo.save(
            KnowledgeIndexDB(
                artifact_id=artifact_id if normalized_scope == "artifact" else None,
                collection_id=collection_id if normalized_scope != "artifact" else None,
                source_scope=normalized_scope,
                profile_name=self.DEFAULT_PROFILE_NAME,
                status="pending",
                created_by=created_by,
                index_metadata={"source_id": scope_id},
            )
        )

    def index_artifact(
        self,
        artifact_id: str,
        *,
        created_by: str | None,
        profile_name: str | None = None,
        profile_overrides: dict[str, Any] | None = None,
        execution_deadline: RagHelperExecutionDeadlinePort | None = None,
    ) -> tuple[KnowledgeIndexDB, KnowledgeIndexRunDB]:
        _checkpoint(execution_deadline)
        helper_modules = self._ensure_helper_imports()
        source_path, source_filename, extensions, source_metadata, version = self._artifact_source_metadata(artifact_id)
        profile = self._resolve_profile(profile_name, profile_overrides)
        file_type_policy = self._rag_helper_file_type_policy(helper_modules)
        classification = file_type_policy.classify_file(
            source_path,
            relative_path=source_filename,
        )
        runtime_extensions = (
            set(file_type_policy.descriptor_dispatch_keys(classification.descriptor))
            if classification is not None
            else set()
        )
        if not runtime_extensions:
            raise ValueError("artifact_file_type_disabled_or_unsupported")
        if profile.get("extensions"):
            runtime_extensions &= set(profile["extensions"] or [])
            if not runtime_extensions:
                raise ValueError("artifact_extension_not_supported_by_profile")
        file_type_contract, file_type_signature = self._file_type_run_contract(
            file_type_policy,
            profile=profile,
            dispatch_keys=runtime_extensions,
        )
        source_metadata = {
            **source_metadata,
            "detected_file_type": classification.as_dict(),
            "file_type_contract": file_type_contract,
            "file_type_contract_signature": file_type_signature,
        }
        knowledge_index = self._build_or_create_index(
            source_scope="artifact",
            scope_id=artifact_id,
            artifact_id=artifact_id,
            created_by=created_by,
        )
        source_scope = self._normalize_source_scope(getattr(knowledge_index, "source_scope", "artifact"))

        run = self._knowledge_index_run_repo.save(
            KnowledgeIndexRunDB(
                knowledge_index_id=knowledge_index.id,
                artifact_id=artifact_id,
                profile_name=profile["name"],
                status="running",
                source_path=str(source_path),
                run_metadata={"requested_by": created_by, "profile": profile, **source_metadata},
                started_at=time.time(),
            )
        )

        output_dir = self._knowledge_output_root(source_scope=source_scope) / knowledge_index.id / run.id
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "manifest.json"
        cache_file = self._resolve_runtime_path(
            profile.get("paths", {}).get("cache_file"),
            output_dir=output_dir,
            fallback=output_dir / ".cache" / "code_to_rag_cache.json",
        )
        error_log_file = self._resolve_runtime_path(
            profile.get("paths", {}).get("error_log_file"),
            output_dir=output_dir,
            fallback=output_dir / ".errors" / "errors.jsonl",
        )
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
        if error_log_file is not None:
            error_log_file.parent.mkdir(parents=True, exist_ok=True)
        incremental = bool(profile.get("flags", {}).get("incremental", False))
        resume = bool(profile.get("flags", {}).get("resume", False))
        show_progress = bool(profile.get("flags", {}).get("progress", False))
        rebuild = not incremental and not resume

        knowledge_index.status = "running"
        knowledge_index.profile_name = profile["name"]
        knowledge_index.latest_run_id = run.id
        knowledge_index.output_dir = str(output_dir)
        knowledge_index.manifest_path = str(manifest_path)
        knowledge_index.updated_at = time.time()
        knowledge_index.index_metadata = {
            **(knowledge_index.index_metadata or {}),
            "artifact_version_id": version.id,
            "last_requested_by": created_by,
            "profile": profile,
            "file_type_contract": file_type_contract,
            "file_type_contract_signature": file_type_signature,
        }
        knowledge_index = self._knowledge_index_repo.save(knowledge_index)

        started = time.perf_counter()
        try:
            _checkpoint(execution_deadline)
            limits = self._processing_limits(helper_modules, profile)
            with tempfile.TemporaryDirectory(prefix="ananta-rag-helper-") as staging_dir:
                staging_root = Path(staging_dir)
                staged_path = staging_root / source_filename
                shutil.copy2(source_path, staged_path)
                helper_modules["process_project"](
                    root=staging_root,
                    out_dir=output_dir,
                    extensions=runtime_extensions,
                    excludes=getattr(helper_modules["codecompass"], "DEFAULT_EXCLUDES", set()),
                    include_code_snippets=profile["options"]["include_code_snippets"],
                    exclude_trivial_methods=profile["options"]["exclude_trivial_methods"],
                    include_xml_node_details=profile["options"]["include_xml_node_details"],
                    include_globs=[staged_path.name],
                    exclude_globs=list(profile.get("filters", {}).get("exclude_globs") or []),
                    limits=limits,
                    java_extractor_cls=helper_modules["codecompass"].JavaExtractor,
                    adoc_extractor_cls=helper_modules["codecompass"].AdocExtractor,
                    xml_extractor_cls=helper_modules["codecompass"].XmlExtractor,
                    xsd_extractor_cls=helper_modules["codecompass"].XsdExtractor,
                    text_extractor_cls=helper_modules["codecompass"].TextFileExtractor,
                    incremental=incremental,
                    rebuild=rebuild,
                    resume=resume,
                    cache_file=cache_file,
                    dry_run=False,
                    show_progress=show_progress,
                    error_log_file=error_log_file,
                    file_inclusion_predicate=file_type_policy.allows_file,
                    execution_checkpoint=(
                        execution_deadline.checkpoint
                        if execution_deadline is not None
                        else None
                    ),
                )
            _checkpoint(execution_deadline)
            manifest = self._enrich_rag_helper_file_type_manifest(
                manifest_path,
                self._load_manifest(manifest_path),
            )
            knowledge_index, run = self._run_recorder().completed(
                knowledge_index,
                run,
                manifest=manifest,
                index_metadata={
                    "artifact_version_id": version.id,
                    "profile": profile,
                    **manifest_summary_metadata(manifest),
                },
                started=started,
                output_dir=output_dir,
                manifest_path=manifest_path,
                source_scope=source_scope,
                profile_name=profile["name"],
            )
            self._observe_rag_helper_file_type_metrics(manifest)
            _checkpoint(execution_deadline)
            return knowledge_index, run
        except Exception as exc:
            knowledge_index, run = self._run_recorder().failed(
                knowledge_index,
                run,
                exc=exc,
                started=started,
                output_dir=output_dir,
                manifest_path=manifest_path,
                source_scope=source_scope,
                profile_name=profile["name"],
            )
            if isinstance(exc, TimeoutError):
                raise
            return knowledge_index, run

    def index_repo_path(
        self,
        path: str,
        *,
        created_by: str | None = None,
        profile_name: str | None = None,
    ) -> tuple[KnowledgeIndexDB, KnowledgeIndexRunDB]:
        """Index an arbitrary path within the repo root directly (no artifact required)."""
        raw = Path(str(path or "").strip())
        safe_path = raw.resolve() if raw.is_absolute() else (self._repo_root() / raw).resolve()
        repo_root = self._repo_root().resolve()
        if safe_path != repo_root and repo_root not in safe_path.parents:
            raise ValueError("path_outside_repo")
        if not safe_path.exists():
            raise ValueError("path_not_found")
        helper_modules = self._ensure_helper_imports()

        scope_id = str(safe_path.relative_to(repo_root))
        profile = self._resolve_profile(profile_name, None)
        file_type_policy = self._rag_helper_file_type_policy(helper_modules)
        runtime_extensions = set(file_type_policy.dispatch_keys())
        if profile.get("extensions"):
            runtime_extensions &= set(profile["extensions"] or [])
            if not runtime_extensions:
                raise ValueError("repo_path_file_types_not_supported_by_profile")
        file_type_contract, file_type_signature = self._file_type_run_contract(
            file_type_policy,
            profile=profile,
            dispatch_keys=runtime_extensions,
        )
        fingerprint = helper_modules["build_source_fingerprint"](
            root=safe_path,
            extensions=runtime_extensions,
            excludes=getattr(helper_modules["codecompass"], "DEFAULT_EXCLUDES", set()),
            include_globs=[],
            exclude_globs=list(profile.get("filters", {}).get("exclude_globs") or []),
            file_inclusion_predicate=file_type_policy.allows_file,
            max_file_bytes=settings.codecompass_max_file_bytes,
        )
        knowledge_index = self._build_or_create_index(
            source_scope="repo_path",
            scope_id=scope_id,
            created_by=created_by,
            collection_id=scope_id,
        )
        previous_signature = (knowledge_index.index_metadata or {}).get(
            "file_type_contract_signature"
        )
        previous_fingerprint = (knowledge_index.index_metadata or {}).get(
            "source_fingerprint"
        )
        if (
            knowledge_index.status == "completed"
            and previous_signature == file_type_signature
            and previous_fingerprint == fingerprint.digest
        ):
            dummy_run = self._knowledge_index_run_repo.save(
                KnowledgeIndexRunDB(
                    knowledge_index_id=knowledge_index.id,
                    profile_name=profile["name"],
                    status="skipped",
                    source_path=str(safe_path),
                    run_metadata={
                        "reason": "already_completed",
                        "file_type_contract_signature": file_type_signature,
                        "source_fingerprint": fingerprint.digest,
                    },
                    started_at=time.time(),
                    finished_at=time.time(),
                )
            )
            return knowledge_index, dummy_run

        previous_contract = dict(
            (knowledge_index.index_metadata or {}).get("file_type_contract") or {}
        )
        previous_manifest = self._load_manifest(
            Path(knowledge_index.manifest_path)
            if knowledge_index.manifest_path
            else Path("__missing_previous_manifest__")
        )
        cache_file = (
            self._knowledge_output_root(source_scope="repo_path")
            / knowledge_index.id
            / ".cache"
            / "code_to_rag_cache.json"
        )
        migration_plan = plan_rag_helper_cache_migration(
            previous_contract=previous_contract,
            current_contract=file_type_contract,
            previous_manifest=previous_manifest,
            repository_path=safe_path,
            policy=file_type_policy,
        )
        cache_migration = helper_modules["invalidate_incremental_cache_paths"](
            cache_file,
            migration_plan.affected_paths,
        )
        cache_migration = {**migration_plan.as_dict(), **cache_migration}

        run = self._knowledge_index_run_repo.save(
            KnowledgeIndexRunDB(
                knowledge_index_id=knowledge_index.id,
                profile_name=profile["name"],
                status="running",
                source_path=str(safe_path),
                run_metadata={
                    "requested_by": created_by,
                    "profile": profile,
                    "repo_path": scope_id,
                    "file_type_contract": file_type_contract,
                    "file_type_contract_signature": file_type_signature,
                    "source_fingerprint": fingerprint.digest,
                    "source_file_count": fingerprint.file_count,
                    "cache_migration": cache_migration,
                },
                started_at=time.time(),
            )
        )
        output_dir = self._knowledge_output_root(source_scope="repo_path") / knowledge_index.id / run.id
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "manifest.json"

        knowledge_index.status = "running"
        knowledge_index.profile_name = profile["name"]
        knowledge_index.latest_run_id = run.id
        knowledge_index.output_dir = str(output_dir)
        knowledge_index.updated_at = time.time()
        knowledge_index.index_metadata = {
            **(knowledge_index.index_metadata or {}),
            "repo_path": scope_id,
            "last_requested_by": created_by,
            "profile": profile,
            "file_type_contract": file_type_contract,
            "file_type_contract_signature": file_type_signature,
            "source_fingerprint": fingerprint.digest,
            "source_file_count": fingerprint.file_count,
            "cache_migration": cache_migration,
        }
        knowledge_index = self._knowledge_index_repo.save(knowledge_index)

        started = time.perf_counter()
        try:
            limits = self._processing_limits(helper_modules, profile)
            helper_modules["process_project"](
                root=safe_path,
                out_dir=output_dir,
                extensions=runtime_extensions,
                excludes=getattr(helper_modules["codecompass"], "DEFAULT_EXCLUDES", set()),
                include_code_snippets=profile["options"]["include_code_snippets"],
                exclude_trivial_methods=profile["options"]["exclude_trivial_methods"],
                include_xml_node_details=profile["options"]["include_xml_node_details"],
                include_globs=[],
                exclude_globs=list(profile.get("filters", {}).get("exclude_globs") or []),
                limits=limits,
                java_extractor_cls=helper_modules["codecompass"].JavaExtractor,
                adoc_extractor_cls=helper_modules["codecompass"].AdocExtractor,
                xml_extractor_cls=helper_modules["codecompass"].XmlExtractor,
                xsd_extractor_cls=helper_modules["codecompass"].XsdExtractor,
                text_extractor_cls=helper_modules["codecompass"].TextFileExtractor,
                incremental=True,
                rebuild=False,
                resume=False,
                cache_file=cache_file,
                dry_run=False,
                show_progress=False,
                error_log_file=None,
                file_inclusion_predicate=file_type_policy.allows_file,
            )
            manifest = self._enrich_rag_helper_file_type_manifest(
                manifest_path,
                self._load_manifest(manifest_path),
            )
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            run.status = "completed"
            run.output_dir = str(output_dir)
            run.manifest_path = str(manifest_path)
            run.duration_ms = duration_ms
            run.finished_at = time.time()
            run.run_metadata = {**(run.run_metadata or {}), "manifest": manifest}
            run = self._knowledge_index_run_repo.save(run)
            knowledge_index.status = "completed"
            knowledge_index.latest_run_id = run.id
            knowledge_index.output_dir = str(output_dir)
            knowledge_index.manifest_path = str(manifest_path)
            knowledge_index.updated_at = time.time()
            knowledge_index.index_metadata = {
                **(knowledge_index.index_metadata or {}),
                "manifest_summary": {
                    "file_count": manifest.get("file_count", 0),
                    "index_record_count": manifest.get("index_record_count", 0),
                },
            }
            KNOWLEDGE_INDEX_RUNS_TOTAL.labels(
                scope="repo_path", status="completed", profile=profile["name"]
            ).inc()
            KNOWLEDGE_INDEX_DURATION_SECONDS.labels(
                scope="repo_path", profile=profile["name"]
            ).observe(duration_ms / 1000.0)
            self._observe_rag_helper_file_type_metrics(manifest)
            return self._knowledge_index_repo.save(knowledge_index), run
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            run.status = "failed"
            run.error_message = str(exc)[:500]
            run.duration_ms = duration_ms
            run.finished_at = time.time()
            self._knowledge_index_run_repo.save(run)
            knowledge_index.status = "failed"
            knowledge_index.updated_at = time.time()
            knowledge_index.index_metadata = {**(knowledge_index.index_metadata or {}), "last_error": str(exc)[:500]}
            self._knowledge_index_repo.save(knowledge_index)
            KNOWLEDGE_INDEX_RUNS_TOTAL.labels(
                scope="repo_path", status="failed", profile=profile["name"]
            ).inc()
            KNOWLEDGE_INDEX_DURATION_SECONDS.labels(
                scope="repo_path", profile=profile["name"]
            ).observe(duration_ms / 1000.0)
            raise

    def index_source_records(
        self,
        *,
        source_scope: str,
        source_id: str,
        records: list[dict[str, Any]],
        created_by: str | None,
        profile_name: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        codecompass_prerender: bool = False,
        links_path=None,
        records_path=None,
        execution_deadline: RagHelperExecutionDeadlinePort | None = None,
        persist_control_plane_records: bool = True,
    ) -> tuple[KnowledgeIndexDB, KnowledgeIndexRunDB]:
        """Build source-record outputs and optionally persist Hub projections.

        ``persist_control_plane_records`` only controls the two Hub-owned
        database projections; output generation and returned models are
        unchanged. Worker execution always disables that persistence because
        the Hub is their sole owner. The default preserves the Hub behavior
        for direct callers.
        """
        from pathlib import Path as _Path
        _checkpoint(execution_deadline)
        normalized_scope = self._normalize_source_scope(source_scope)
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            raise ValueError("source_id_required")

        # Streaming path: wiki+codecompass with a JSONL file → skip in-memory load
        _records_path = _Path(records_path) if records_path else None
        _streaming = (
            normalized_scope == "wiki"
            and codecompass_prerender
            and _records_path is not None
            and _records_path.exists()
        )
        normalized_records = (
            []
            if _streaming
            else normalize_source_records(
                normalized_scope=normalized_scope, source_id=normalized_source_id, records=records
            )
        )

        profile = self._resolve_profile(profile_name, None)
        index_artifact_id = normalized_source_id if normalized_scope == "artifact" else None
        index_collection_id = normalized_source_id if normalized_scope != "artifact" else None
        if persist_control_plane_records:
            knowledge_index = self._build_or_create_index(
                source_scope=normalized_scope,
                scope_id=normalized_source_id,
                artifact_id=index_artifact_id,
                collection_id=index_collection_id,
                created_by=created_by,
            )
        else:
            knowledge_index = KnowledgeIndexDB(
                artifact_id=index_artifact_id,
                collection_id=index_collection_id,
                source_scope=normalized_scope,
                profile_name=self.DEFAULT_PROFILE_NAME,
                status="pending",
                created_by=created_by,
                index_metadata={"source_id": normalized_source_id},
            )

        run = KnowledgeIndexRunDB(
            knowledge_index_id=knowledge_index.id,
            artifact_id=index_artifact_id,
            collection_id=index_collection_id,
            profile_name=profile["name"],
            status="running",
            source_path=normalized_source_id,
            run_metadata={
                "requested_by": created_by,
                "profile": profile,
                "source_scope": normalized_scope,
                "source_id": normalized_source_id,
                **(source_metadata or {}),
            },
            started_at=time.time(),
        )
        if persist_control_plane_records:
            run = self._knowledge_index_run_repo.save(run)

        output_dir = self._knowledge_output_root(source_scope=normalized_scope) / knowledge_index.id / run.id
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "manifest.json"
        index_path = output_dir / "index.jsonl"

        knowledge_index.status = "running"
        knowledge_index.profile_name = profile["name"]
        knowledge_index.latest_run_id = run.id
        knowledge_index.output_dir = str(output_dir)
        knowledge_index.manifest_path = str(manifest_path)
        knowledge_index.updated_at = time.time()
        knowledge_index.index_metadata = {
            **(knowledge_index.index_metadata or {}),
            "source_scope": normalized_scope,
            "source_id": normalized_source_id,
            "profile": profile,
            "last_requested_by": created_by,
            **(source_metadata or {}),
        }
        if persist_control_plane_records:
            knowledge_index = self._knowledge_index_repo.save(knowledge_index)

        started = time.perf_counter()
        try:
            serialized = []
            for record in normalized_records:
                _checkpoint(execution_deadline)
                serialized.append(
                    json.dumps(
                        dict(record),
                        sort_keys=True,
                        ensure_ascii=True,
                    )
                )
            serialized.sort()
            _checkpoint(execution_deadline)
            source_files = source_record_files(normalized_records)
            if normalized_scope == "wiki" and codecompass_prerender:
                manifest = index_wiki_records_with_codecompass(
                    records=normalized_records if not _streaming else None,
                    records_path=_records_path if _streaming else None,
                    output_dir=output_dir,
                    profile=profile,
                    links_path=links_path,
                )
                manifest = wiki_codecompass_manifest(
                    manifest,
                    normalized_scope=normalized_scope,
                    records=records,
                    normalized_records=normalized_records,
                    streaming=_streaming,
                )
            else:
                index_path.write_text("\n".join(serialized) + ("\n" if serialized else ""), encoding="utf-8")
                graph_export_mode = str(
                    profile.get("limits", {}).get("graph_export_mode")
                    or "off"
                ).strip().lower()
                graph_manifest: dict[str, Any] = {}
                if normalized_scope == "repo_path" and graph_export_mode != "off":
                    if self._repository_graph_builder is None:
                        raise RepositoryGraphBuilderUnavailableError()
                    graph_manifest = self._repository_graph_builder.build_outputs(
                        source_id=normalized_source_id,
                        records=normalized_records,
                        output_dir=output_dir,
                        execution_deadline=execution_deadline,
                    )
                manifest = source_record_manifest(
                    normalized_scope=normalized_scope,
                    source_id=normalized_source_id,
                    profile=profile,
                    records=records,
                    normalized_records=normalized_records,
                    serialized_count=len(serialized),
                    source_files=source_files,
                    graph_manifest=graph_manifest,
                    graph_export_mode=graph_export_mode,
                )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            _checkpoint(execution_deadline)
            knowledge_index, run = self._run_recorder().completed(
                knowledge_index,
                run,
                manifest=manifest,
                index_metadata=manifest_summary_metadata(manifest),
                started=started,
                output_dir=output_dir,
                manifest_path=manifest_path,
                source_scope=normalized_scope,
                profile_name=profile["name"],
                persist=persist_control_plane_records,
            )
            record_file_type_metrics_snapshot(source_metadata)
            _checkpoint(execution_deadline)
            return knowledge_index, run
        except Exception as exc:
            knowledge_index, run = self._run_recorder().failed(
                knowledge_index,
                run,
                exc=exc,
                started=started,
                output_dir=output_dir,
                manifest_path=manifest_path,
                source_scope=normalized_scope,
                profile_name=profile["name"],
                persist=persist_control_plane_records,
            )
            if isinstance(exc, TimeoutError):
                raise
            return knowledge_index, run

    def _run_recorder(self) -> KnowledgeIndexRunRecorder:
        return KnowledgeIndexRunRecorder(
            knowledge_index_repository=self._knowledge_index_repo,
            knowledge_index_run_repository=self._knowledge_index_run_repo,
        )

    def get_artifact_status(self, artifact_id: str) -> tuple[KnowledgeIndexDB | None, list[KnowledgeIndexRunDB]]:
        knowledge_index = self._knowledge_index_repo.get_by_artifact(artifact_id)
        if knowledge_index is None:
            return None, []
        runs = self._knowledge_index_run_repo.get_by_knowledge_index(knowledge_index.id)
        return knowledge_index, runs

    def get_artifact_preview(self, artifact_id: str, *, limit: int = 5) -> dict[str, Any] | None:
        knowledge_index = self._knowledge_index_repo.get_by_artifact(artifact_id)
        if knowledge_index is None or not knowledge_index.output_dir:
            return None
        return build_knowledge_index_preview(knowledge_index, limit=limit)


rag_helper_index_service = RagHelperIndexService()


def get_rag_helper_index_service() -> RagHelperIndexService:
    return rag_helper_index_service
