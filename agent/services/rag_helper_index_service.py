from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
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
    INTERNAL_PROFILE_CATALOG = {
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
    PROFILE_SECTION_KEYS = {"filters", "limits", "modes", "resolution", "cache", "output", "flags"}
    PROFILE_KEY_ALIASES = {
        "include_globs": "include_glob",
        "exclude_globs": "exclude_glob",
        "generated_comment_markers": "generated_comment_marker",
    }
    ALLOWED_OVERRIDE_KEYS = {
        "max_workers",
        "max_xml_nodes",
        "max_records_per_file",
        "max_relation_records_per_file",
        "max_methods_per_class",
        "xml_mode",
        "xml_index_mode",
        "xml_relation_mode",
        "embedding_text_mode",
        "java_detail_mode",
        "java_relation_mode",
        "retrieval_output_mode",
        "context_output_mode",
        "output_compaction_mode",
        "gem_partition_mode",
        "xml_overview_mode",
        "manifest_output_mode",
        "relation_output_mode",
        "output_partition_mode",
        "importance_scoring_mode",
        "graph_export_mode",
        "benchmark_mode",
        "duplicate_detection_mode",
        "specialized_chunker_mode",
        "output_bundle_mode",
        "include_code_snippets",
        "exclude_trivial_methods",
        "include_xml_node_details",
        "incremental",
        "resume",
        "progress",
    }
    BOOL_OVERRIDE_KEYS = {
        "include_code_snippets",
        "exclude_trivial_methods",
        "include_xml_node_details",
        "incremental",
        "resume",
        "progress",
    }

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _rag_helper_root(self) -> Path:
        return self._repo_root() / "rag-helper"

    def _knowledge_output_root(self) -> Path:
        output_root = Path(settings.data_dir) / "knowledge_indices"
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root

    def _profile_files(self) -> list[Path]:
        helper_root = self._rag_helper_root()
        if not helper_root.exists():
            return []
        return sorted(helper_root.glob("spring-large-project-profile*.json"))

    def _normalize_profile_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in raw.items():
            target_key = self.PROFILE_KEY_ALIASES.get(key, key)
            if key in self.PROFILE_SECTION_KEYS and isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    normalized[self.PROFILE_KEY_ALIASES.get(nested_key, nested_key)] = nested_value
                continue
            normalized[target_key] = value
        return normalized

    def _label_for_profile_name(self, name: str) -> str:
        label = name.replace("spring-large-project-profile-", "").replace("-", " ")
        label = label.replace("xml", "XML").replace("xsd", "XSD")
        return " ".join(part.capitalize() if part not in {"XML", "XSD"} else part for part in label.split())

    def _description_for_external_profile(self, name: str, config: dict[str, Any]) -> str:
        extensions = ", ".join(str(ext) for ext in list(config.get("extensions") or [])[:4]) or "artifact scope"
        xml_overview = str(config.get("xml_overview_mode") or "off")
        compaction = str(config.get("output_compaction_mode") or "off")
        return (
            f"Aus dem rag-helper geladene Profildatei ({name}) mit Extensions {extensions}, "
            f"Output-Compaction {compaction} und XML-Overview {xml_overview}."
        )

    def _external_profile_catalog(self) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        for path in self._profile_files():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            config = self._normalize_profile_config(raw)
            name = path.stem
            profiles[name] = {
                "label": self._label_for_profile_name(name),
                "description": self._description_for_external_profile(name, config),
                "config_path": str(path),
                "config": config,
                "source": "rag_helper_file",
            }
        return profiles

    def _resolve_runtime_path(self, configured: str | None, *, output_dir: Path, fallback: Path | None) -> Path | None:
        if configured is None:
            return fallback
        value = str(configured).strip()
        if not value:
            return None
        return Path(value.replace("{out}", str(output_dir))).resolve()

    def list_profiles(self) -> list[dict[str, Any]]:
        items = []
        for name, profile in self.INTERNAL_PROFILE_CATALOG.items():
            items.append(
                {
                    "name": name,
                    "label": profile["label"],
                    "description": profile["description"],
                    "limits": dict(profile["limits"]),
                    "options": dict(profile["options"]),
                    "flags": {"incremental": False, "resume": False, "progress": False},
                    "source": "built_in",
                    "is_default": name == self.DEFAULT_PROFILE_NAME,
                }
            )
        for name, profile in self._external_profile_catalog().items():
            config = dict(profile.get("config") or {})
            items.append(
                {
                    "name": name,
                    "label": profile["label"],
                    "description": profile["description"],
                    "limits": {
                        key: config[key]
                        for key in (
                            "max_workers",
                            "max_xml_nodes",
                            "max_records_per_file",
                            "max_relation_records_per_file",
                            "max_methods_per_class",
                            "xml_mode",
                            "xml_index_mode",
                            "xml_relation_mode",
                            "embedding_text_mode",
                            "java_detail_mode",
                            "java_relation_mode",
                            "retrieval_output_mode",
                            "context_output_mode",
                            "output_compaction_mode",
                            "gem_partition_mode",
                            "xml_overview_mode",
                            "manifest_output_mode",
                            "relation_output_mode",
                            "output_partition_mode",
                            "importance_scoring_mode",
                            "graph_export_mode",
                            "benchmark_mode",
                            "duplicate_detection_mode",
                            "specialized_chunker_mode",
                            "output_bundle_mode",
                        )
                        if key in config
                    },
                    "options": {
                        "include_code_snippets": not bool(config.get("no_code_snippets", False)),
                        "exclude_trivial_methods": bool(config.get("exclude_trivial_methods", False)),
                        "include_xml_node_details": not bool(config.get("no_xml_node_details", False)),
                    },
                    "flags": {
                        "incremental": bool(config.get("incremental", False)),
                        "resume": bool(config.get("resume", False)),
                        "progress": bool(config.get("progress", False)),
                    },
                    "source": str(profile.get("source") or "rag_helper_file"),
                    "config_path": profile.get("config_path"),
                    "is_default": False,
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

    def _artifact_source_metadata(self, artifact_id: str) -> tuple[Path, str, set[str], dict[str, Any], Any]:
        artifact = artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            raise ValueError("artifact_not_found")
        if not artifact.latest_version_id:
            raise ValueError("artifact_version_not_found")
        version = artifact_version_repo.get_by_id(artifact.latest_version_id)
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

    def _load_partitioned_jsonl_preview(
        self,
        output_dir: Path,
        files: list[str] | None,
        *,
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        preview: dict[str, list[dict[str, Any]]] = {}
        for relative_path in files or []:
            path = output_dir / relative_path
            preview[path.stem] = self._load_jsonl_preview(path, limit=limit)
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
        base = self.INTERNAL_PROFILE_CATALOG.get(selected_name)
        normalized_overrides = {
            key: value for key, value in dict(overrides or {}).items() if key in self.ALLOWED_OVERRIDE_KEYS
        }
        if base is not None:
            merged_limits = {**base["limits"]}
            merged_options = {**base["options"]}
            merged_flags = {"incremental": False, "resume": False, "progress": False}
            runtime_paths = {}
            runtime_extensions: set[str] | None = None
            runtime_filters = {"include_globs": [], "exclude_globs": []}
            profile_source = "built_in"
            config_path = None
        else:
            external = self._external_profile_catalog().get(selected_name)
            if external is None:
                raise ValueError("invalid_profile_name")
            config = dict(external.get("config") or {})
            merged_limits = {
                key: config[key]
                for key in (
                    "max_workers",
                    "max_xml_nodes",
                    "max_records_per_file",
                    "max_relation_records_per_file",
                    "max_methods_per_class",
                    "xml_mode",
                    "xml_index_mode",
                    "xml_relation_mode",
                    "embedding_text_mode",
                    "java_detail_mode",
                    "java_relation_mode",
                    "retrieval_output_mode",
                    "context_output_mode",
                    "output_compaction_mode",
                    "gem_partition_mode",
                    "xml_overview_mode",
                    "manifest_output_mode",
                    "relation_output_mode",
                    "output_partition_mode",
                    "importance_scoring_mode",
                    "graph_export_mode",
                    "benchmark_mode",
                    "duplicate_detection_mode",
                    "specialized_chunker_mode",
                    "output_bundle_mode",
                )
                if key in config
            }
            merged_options = {
                "include_code_snippets": not bool(config.get("no_code_snippets", False)),
                "exclude_trivial_methods": bool(config.get("exclude_trivial_methods", False)),
                "include_xml_node_details": not bool(config.get("no_xml_node_details", False)),
            }
            merged_flags = {
                "incremental": bool(config.get("incremental", False)),
                "resume": bool(config.get("resume", False)),
                "progress": bool(config.get("progress", False)),
            }
            runtime_paths = {
                "cache_file": config.get("cache_file"),
                "error_log_file": config.get("error_log_file"),
            }
            runtime_extensions = {
                str(ext).strip().lower()
                for ext in list(config.get("extensions") or [])
                if str(ext).strip()
            } or None
            runtime_filters = {
                "include_globs": list(config.get("include_glob") or []),
                "exclude_globs": list(config.get("exclude_glob") or []),
            }
            profile_source = str(external.get("source") or "rag_helper_file")
            config_path = external.get("config_path")
            base = {
                "label": external["label"],
                "description": external["description"],
            }
        for key, value in normalized_overrides.items():
            normalized_value = bool(value) if key in self.BOOL_OVERRIDE_KEYS else value
            if key in merged_limits:
                merged_limits[key] = normalized_value
            elif key in merged_options:
                merged_options[key] = normalized_value
            elif key in merged_flags:
                merged_flags[key] = normalized_value
        max_workers = int(merged_limits.get("max_workers", 1) or 1)
        merged_limits["max_workers"] = max(1, min(max_workers, 4))
        for key in ("include_code_snippets", "exclude_trivial_methods", "include_xml_node_details"):
            merged_options[key] = bool(merged_options.get(key))
        for key in ("incremental", "resume", "progress"):
            merged_flags[key] = bool(merged_flags.get(key))
        return {
            "name": selected_name,
            "label": base["label"],
            "description": base["description"],
            "limits": merged_limits,
            "options": merged_options,
            "flags": merged_flags,
            "paths": runtime_paths,
            "extensions": sorted(runtime_extensions) if runtime_extensions else None,
            "filters": runtime_filters,
            "overrides": normalized_overrides,
            "source": profile_source,
            "config_path": config_path,
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
        source_path, source_filename, extensions, source_metadata, version = self._artifact_source_metadata(artifact_id)
        profile = self._resolve_profile(profile_name, profile_overrides)
        runtime_extensions = set(extensions)
        if profile.get("extensions"):
            runtime_extensions = {ext for ext in extensions if ext in set(profile["extensions"] or [])}
            if not runtime_extensions:
                raise ValueError("artifact_extension_not_supported_by_profile")
        knowledge_index = self._build_or_create_index(artifact_id=artifact_id, created_by=created_by)

        run = knowledge_index_run_repo.save(
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

        output_dir = self._knowledge_output_root() / knowledge_index.id / run.id
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
        }
        knowledge_index = knowledge_index_repo.save(knowledge_index)

        started = time.perf_counter()
        try:
            limits = helper_modules["ProcessingLimits"](**profile["limits"])
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
                "available_outputs": manifest.get("partitioned_outputs", {}),
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
        manifest = self._load_manifest(manifest_path)
        partitioned_outputs = manifest.get("partitioned_outputs") or {}
        return {
            "knowledge_index": knowledge_index.model_dump(),
            "manifest": manifest,
            "available_outputs": partitioned_outputs,
            "preview": {
                "index": self._load_jsonl_preview(output_dir / "index.jsonl", limit=limit),
                "details": self._load_jsonl_preview(output_dir / "details.jsonl", limit=limit),
                "relations": self._load_jsonl_preview(output_dir / "relations.jsonl", limit=limit),
                "xml_overview": self._load_jsonl_preview(output_dir / "xml_overview.jsonl", limit=limit),
                "gems_by_domain": self._load_partitioned_jsonl_preview(
                    output_dir,
                    partitioned_outputs.get("gems"),
                    limit=limit,
                ),
                "xsd_full": {
                    "index": self._load_jsonl_preview(output_dir / "xsd_full" / "index.jsonl", limit=limit),
                    "details": self._load_jsonl_preview(output_dir / "xsd_full" / "details.jsonl", limit=limit),
                    "relations": self._load_jsonl_preview(output_dir / "xsd_full" / "relations.jsonl", limit=limit),
                },
            },
        }


rag_helper_index_service = RagHelperIndexService()


def get_rag_helper_index_service() -> RagHelperIndexService:
    return rag_helper_index_service
