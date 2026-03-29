from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.metrics import KNOWLEDGE_INDEX_DURATION_SECONDS, KNOWLEDGE_INDEX_RUNS_TOTAL
from agent.repository import artifact_repo, artifact_version_repo, knowledge_index_repo, knowledge_index_run_repo


class RagHelperIndexService:
    """Owns controlled rag-helper execution and persistence for artifact-backed indices."""

    DEFAULT_PROFILE_NAME = "default"
    PROFILE_CATALOG = {
        "default": {
            "label": "Default",
            "description": "Ausgewogen fuer allgemeine Artefakte und Retrieval.",
            "limits": {
                "max_workers": 1,
                "embedding_text_mode": "compact",
                "retrieval_output_mode": "both",
                "graph_export_mode": "off",
                "benchmark_mode": "basic",
                "duplicate_detection_mode": "basic",
                "specialized_chunker_mode": "basic",
                "output_bundle_mode": "off",
            },
            "options": {
                "include_code_snippets": False,
                "exclude_trivial_methods": False,
                "include_xml_node_details": False,
            },
        },
        "fast_docs": {
            "label": "Fast Docs",
            "description": "Schneller, dokumentzentrierter Lauf mit wenig Zusatzmaterial.",
            "limits": {
                "max_workers": 1,
                "embedding_text_mode": "compact",
                "retrieval_output_mode": "split",
                "graph_export_mode": "off",
                "benchmark_mode": "off",
                "duplicate_detection_mode": "off",
                "specialized_chunker_mode": "off",
                "output_bundle_mode": "off",
            },
            "options": {
                "include_code_snippets": False,
                "exclude_trivial_methods": True,
                "include_xml_node_details": False,
            },
        },
        "deep_code": {
            "label": "Deep Code",
            "description": "Reichhaltigere Code- und Struktur-Extraktion fuer technische Artefakte.",
            "limits": {
                "max_workers": 1,
                "embedding_text_mode": "compact",
                "retrieval_output_mode": "both",
                "graph_export_mode": "jsonl",
                "benchmark_mode": "basic",
                "duplicate_detection_mode": "basic",
                "specialized_chunker_mode": "basic",
                "output_bundle_mode": "zip",
            },
            "options": {
                "include_code_snippets": True,
                "exclude_trivial_methods": False,
                "include_xml_node_details": True,
            },
        },
    }
    ALLOWED_OVERRIDE_KEYS = {
        "max_workers",
        "embedding_text_mode",
        "retrieval_output_mode",
        "graph_export_mode",
        "benchmark_mode",
        "duplicate_detection_mode",
        "specialized_chunker_mode",
        "output_bundle_mode",
        "include_code_snippets",
        "exclude_trivial_methods",
        "include_xml_node_details",
    }

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _rag_helper_root(self) -> Path:
        return self._repo_root() / "rag-helper"

    def _knowledge_output_root(self) -> Path:
        output_root = Path(settings.data_dir) / "knowledge_indices"
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root

    def list_profiles(self) -> list[dict[str, Any]]:
        items = []
        for name, profile in self.PROFILE_CATALOG.items():
            items.append(
                {
                    "name": name,
                    "label": profile["label"],
                    "description": profile["description"],
                    "limits": dict(profile["limits"]),
                    "options": dict(profile["options"]),
                    "is_default": name == self.DEFAULT_PROFILE_NAME,
                }
            )
        return items

    def _ensure_helper_imports(self) -> dict[str, Any]:
        helper_root = self._rag_helper_root().resolve()
        if not helper_root.exists():
            raise RuntimeError("rag_helper_not_found")
        helper_root_str = str(helper_root)
        if helper_root_str not in sys.path:
            sys.path.insert(0, helper_root_str)
        try:
            codecompass = importlib.import_module("codecompass_rag")
            processing_limits = importlib.import_module("rag_helper.application.processing_limits")
            project_processor = importlib.import_module("rag_helper.application.project_processor")
        except Exception as exc:
            raise RuntimeError(f"rag_helper_import_failed:{exc}") from exc
        return {
            "codecompass": codecompass,
            "ProcessingLimits": processing_limits.ProcessingLimits,
            "process_project": project_processor.process_project,
        }

    def _artifact_index_root_and_globs(self, artifact_id: str) -> tuple[Path, list[str], set[str], dict[str, Any], Any]:
        artifact = artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            raise ValueError("artifact_not_found")
        if not artifact.latest_version_id:
            raise ValueError("artifact_version_not_found")
        version = artifact_version_repo.get_by_id(artifact.latest_version_id)
        if version is None:
            raise ValueError("artifact_version_not_found")

        source_path = Path(version.storage_path).resolve()
        if not source_path.exists():
            raise ValueError("artifact_storage_not_found")
        if not source_path.is_file():
            raise ValueError("artifact_storage_not_file")

        filename = Path(version.original_filename or source_path.name).name
        stored_filename = source_path.name
        ext = source_path.suffix.lower().lstrip(".")
        extensions = {ext} if ext else {"md"}
        include_globs = [stored_filename]
        if filename != stored_filename:
            include_globs.append(filename)
        metadata = {
            "artifact_id": artifact.id,
            "artifact_version_id": version.id,
            "filename": filename,
            "stored_filename": stored_filename,
            "storage_path": str(source_path),
            "extensions": sorted(extensions),
            "include_globs": include_globs,
        }
        return source_path.parent, include_globs, extensions, metadata, version

    def _load_manifest(self, manifest_path: Path) -> dict[str, Any]:
        if not manifest_path.exists():
            return {}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_jsonl_preview(self, path: Path, *, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        preview: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    preview.append(payload)
                if len(preview) >= limit:
                    break
        except Exception:
            return []
        return preview

    def _build_or_create_index(self, *, artifact_id: str, created_by: str | None) -> KnowledgeIndexDB:
        existing = knowledge_index_repo.get_by_artifact(artifact_id)
        if existing is not None:
            return existing
        return knowledge_index_repo.save(
            KnowledgeIndexDB(
                artifact_id=artifact_id,
                source_scope="artifact",
                profile_name=self.DEFAULT_PROFILE_NAME,
                status="pending",
                created_by=created_by,
            )
        )

    def _resolve_profile(self, profile_name: str | None, overrides: dict[str, Any] | None) -> dict[str, Any]:
        selected_name = str(profile_name or self.DEFAULT_PROFILE_NAME).strip() or self.DEFAULT_PROFILE_NAME
        base = self.PROFILE_CATALOG.get(selected_name)
        if base is None:
            raise ValueError("invalid_profile_name")
        normalized_overrides = {
            key: value for key, value in dict(overrides or {}).items() if key in self.ALLOWED_OVERRIDE_KEYS
        }
        merged_limits = {**base["limits"]}
        merged_options = {**base["options"]}
        for key, value in normalized_overrides.items():
            if key in merged_limits:
                merged_limits[key] = value
            elif key in merged_options:
                merged_options[key] = value
        max_workers = int(merged_limits.get("max_workers", 1) or 1)
        merged_limits["max_workers"] = max(1, min(max_workers, 4))
        for key in ("include_code_snippets", "exclude_trivial_methods", "include_xml_node_details"):
            merged_options[key] = bool(merged_options.get(key))
        return {
            "name": selected_name,
            "label": base["label"],
            "description": base["description"],
            "limits": merged_limits,
            "options": merged_options,
            "overrides": normalized_overrides,
        }

    def index_artifact(
        self,
        artifact_id: str,
        *,
        created_by: str | None,
        profile_name: str | None = None,
        profile_overrides: dict[str, Any] | None = None,
    ) -> tuple[KnowledgeIndexDB, KnowledgeIndexRunDB]:
        helper_modules = self._ensure_helper_imports()
        root, include_globs, extensions, source_metadata, version = self._artifact_index_root_and_globs(artifact_id)
        profile = self._resolve_profile(profile_name, profile_overrides)
        knowledge_index = self._build_or_create_index(artifact_id=artifact_id, created_by=created_by)

        run = knowledge_index_run_repo.save(
            KnowledgeIndexRunDB(
                knowledge_index_id=knowledge_index.id,
                artifact_id=artifact_id,
                profile_name=profile["name"],
                status="running",
                source_path=str(root),
                run_metadata={"requested_by": created_by, "profile": profile, **source_metadata},
                started_at=time.time(),
            )
        )

        output_dir = self._knowledge_output_root() / knowledge_index.id / run.id
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "manifest.json"

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
        }
        knowledge_index = knowledge_index_repo.save(knowledge_index)

        started = time.perf_counter()
        try:
            limits = helper_modules["ProcessingLimits"](**profile["limits"])
            helper_modules["process_project"](
                root=root,
                out_dir=output_dir,
                extensions=extensions,
                excludes=getattr(helper_modules["codecompass"], "DEFAULT_EXCLUDES", set()),
                include_code_snippets=profile["options"]["include_code_snippets"],
                exclude_trivial_methods=profile["options"]["exclude_trivial_methods"],
                include_xml_node_details=profile["options"]["include_xml_node_details"],
                include_globs=include_globs,
                exclude_globs=None,
                limits=limits,
                java_extractor_cls=helper_modules["codecompass"].JavaExtractor,
                adoc_extractor_cls=helper_modules["codecompass"].AdocExtractor,
                xml_extractor_cls=helper_modules["codecompass"].XmlExtractor,
                xsd_extractor_cls=helper_modules["codecompass"].XsdExtractor,
                text_extractor_cls=helper_modules["codecompass"].TextFileExtractor,
                incremental=False,
                rebuild=True,
                resume=False,
                cache_file=output_dir / ".code_to_rag_cache.json",
                dry_run=False,
                show_progress=False,
                error_log_file=output_dir / "errors.jsonl",
            )
            manifest = self._load_manifest(manifest_path)
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            run.status = "completed"
            run.output_dir = str(output_dir)
            run.manifest_path = str(manifest_path)
            run.duration_ms = duration_ms
            run.finished_at = time.time()
            run.run_metadata = {**(run.run_metadata or {}), "manifest": manifest}
            run = knowledge_index_run_repo.save(run)

            knowledge_index.status = "completed"
            knowledge_index.latest_run_id = run.id
            knowledge_index.output_dir = str(output_dir)
            knowledge_index.manifest_path = str(manifest_path)
            knowledge_index.updated_at = time.time()
            knowledge_index.index_metadata = {
                **(knowledge_index.index_metadata or {}),
                "artifact_version_id": version.id,
                "profile": profile,
                "manifest_summary": {
                    "file_count": manifest.get("file_count", 0),
                    "index_record_count": manifest.get("index_record_count", 0),
                    "detail_record_count": manifest.get("detail_record_count", 0),
                    "relation_record_count": manifest.get("relation_record_count", 0),
                    "error_count": manifest.get("error_count", 0),
                },
            }
            knowledge_index = knowledge_index_repo.save(knowledge_index)
            KNOWLEDGE_INDEX_RUNS_TOTAL.labels(scope="artifact", status="completed", profile=profile["name"]).inc()
            KNOWLEDGE_INDEX_DURATION_SECONDS.labels(scope="artifact", profile=profile["name"]).observe(
                duration_ms / 1000.0
            )
            return knowledge_index, run
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            run.status = "failed"
            run.output_dir = str(output_dir)
            run.manifest_path = str(manifest_path)
            run.duration_ms = duration_ms
            run.error_message = str(exc)
            run.finished_at = time.time()
            run = knowledge_index_run_repo.save(run)

            knowledge_index.status = "failed"
            knowledge_index.latest_run_id = run.id
            knowledge_index.output_dir = str(output_dir)
            knowledge_index.manifest_path = str(manifest_path)
            knowledge_index.updated_at = time.time()
            knowledge_index.index_metadata = {
                **(knowledge_index.index_metadata or {}),
                "last_error": str(exc),
            }
            knowledge_index = knowledge_index_repo.save(knowledge_index)
            KNOWLEDGE_INDEX_RUNS_TOTAL.labels(scope="artifact", status="failed", profile=profile["name"]).inc()
            KNOWLEDGE_INDEX_DURATION_SECONDS.labels(scope="artifact", profile=profile["name"]).observe(
                duration_ms / 1000.0
            )
            return knowledge_index, run

    def get_artifact_status(self, artifact_id: str) -> tuple[KnowledgeIndexDB | None, list[KnowledgeIndexRunDB]]:
        knowledge_index = knowledge_index_repo.get_by_artifact(artifact_id)
        if knowledge_index is None:
            return None, []
        runs = knowledge_index_run_repo.get_by_knowledge_index(knowledge_index.id)
        return knowledge_index, runs

    def get_artifact_preview(self, artifact_id: str, *, limit: int = 5) -> dict[str, Any] | None:
        knowledge_index = knowledge_index_repo.get_by_artifact(artifact_id)
        if knowledge_index is None or not knowledge_index.output_dir:
            return None
        output_dir = Path(knowledge_index.output_dir)
        if not output_dir.exists():
            return None
        manifest_path = Path(knowledge_index.manifest_path) if knowledge_index.manifest_path else (output_dir / "manifest.json")
        return {
            "knowledge_index": knowledge_index.model_dump(),
            "manifest": self._load_manifest(manifest_path),
            "preview": {
                "index": self._load_jsonl_preview(output_dir / "index.jsonl", limit=limit),
                "details": self._load_jsonl_preview(output_dir / "details.jsonl", limit=limit),
                "relations": self._load_jsonl_preview(output_dir / "relations.jsonl", limit=limit),
            },
        }


rag_helper_index_service = RagHelperIndexService()


def get_rag_helper_index_service() -> RagHelperIndexService:
    return rag_helper_index_service
