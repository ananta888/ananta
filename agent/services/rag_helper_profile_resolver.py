"""Built-in and rag-helper file based index profile catalog and resolution."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.services._rag_helper_profile_catalog import (
    ALLOWED_OVERRIDE_KEYS,
    BOOL_OVERRIDE_KEYS,
    INTERNAL_PROFILE_CATALOG,
    PROFILE_KEY_ALIASES,
    PROFILE_SECTION_KEYS,
)


class RagHelperProfileCatalog:
    """List, suggest and resolve rag-helper index profiles.

    ``helper_root`` is a provider so the owning service can keep its
    overridable rag-helper root as the single source of the profile directory.
    """

    DEFAULT_PROFILE_NAME = "default"
    INTERNAL_PROFILE_CATALOG = INTERNAL_PROFILE_CATALOG
    PROFILE_SECTION_KEYS = PROFILE_SECTION_KEYS
    PROFILE_KEY_ALIASES = PROFILE_KEY_ALIASES
    ALLOWED_OVERRIDE_KEYS = ALLOWED_OVERRIDE_KEYS
    BOOL_OVERRIDE_KEYS = BOOL_OVERRIDE_KEYS

    def __init__(self, *, helper_root: Callable[[], Path]) -> None:
        self._helper_root = helper_root

    def _profile_files(self) -> list[Path]:
        helper_root = self._helper_root()
        if not helper_root.exists():
            return []
        spring_profiles = list(helper_root.glob("spring-large-project-profile*.json"))
        wiki_profiles = list(helper_root.glob("wiki-rag-profile*.json"))
        return sorted(spring_profiles + wiki_profiles)

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

    def external_profile_catalog(self) -> dict[str, dict[str, Any]]:
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
                    "task_kinds": list(profile.get("task_kinds") or []),
                    "retrieval_intents": list(profile.get("retrieval_intents") or []),
                    "flags": {"incremental": False, "resume": False, "progress": False},
                    "source": "built_in",
                    "is_default": name == self.DEFAULT_PROFILE_NAME,
                }
            )
        for name, profile in self.external_profile_catalog().items():
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
                    "task_kinds": list(config.get("task_kinds") or []),
                    "retrieval_intents": list(config.get("retrieval_intent") or config.get("retrieval_intents") or []),
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

    def suggest_profile_name(
        self,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        required_context_scope: str | None = None,
    ) -> str:
        normalized_kind = str(task_kind or "").strip().lower()
        normalized_intent = str(retrieval_intent or "").strip().lower()
        normalized_scope = str(required_context_scope or "").strip().lower()
        if normalized_kind in {"bugfix", "testing", "test"} or any(
            token in normalized_intent for token in ("bug", "failure", "fix")
        ):
            return "subtask_bugfix_local"
        if normalized_kind in {"architecture", "analysis", "doc", "research"} or any(
            token in normalized_intent for token in ("architecture", "decision", "overview")
        ):
            return "subtask_architecture_review"
        if normalized_kind in {"config", "xml", "ops"} or any(
            token in normalized_scope for token in ("config", "integration", "runtime")
        ):
            return "subtask_config_integration"
        if normalized_kind in {"refactor", "implement", "coding"} or any(
            token in normalized_intent for token in ("dependency", "symbol", "execution")
        ):
            return "subtask_refactor_navigation"
        return self.DEFAULT_PROFILE_NAME
    def resolve_profile(self, profile_name: str | None, overrides: dict[str, Any] | None) -> dict[str, Any]:
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
            external = self.external_profile_catalog().get(selected_name)
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
