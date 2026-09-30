"""File-type policy, run contract and processing-limit support for rag-helper runs."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.services.rag_helper_file_type_policy import RagHelperFileTypePolicy
from ananta_contracts import FileTypeRolloutPolicy, load_file_type_support_registry

_LOGGER = logging.getLogger("agent.services.rag_helper_index_service")


def csv_setting_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def build_rag_helper_file_type_policy(
    helper_modules: dict[str, Any],
    *,
    contract_root: Path,
) -> RagHelperFileTypePolicy:
    registry = load_file_type_support_registry(contract_root)
    rollout = FileTypeRolloutPolicy.build(
        registry,
        priorities=csv_setting_values(settings.codecompass_file_type_priorities),
        enabled_format_ids=csv_setting_values(settings.codecompass_enabled_formats),
        disabled_format_ids=csv_setting_values(settings.codecompass_disabled_formats),
    )
    return RagHelperFileTypePolicy(
        registry=registry,
        rollout=rollout,
        runtime_dispatch_keys=getattr(helper_modules["codecompass"], "DEFAULT_EXTENSIONS", set()),
        dispatch_key_resolver=helper_modules["effective_extension"],
    )


def rag_helper_file_type_run_contract(
    policy: RagHelperFileTypePolicy,
    *,
    profile: dict[str, Any],
    dispatch_keys: set[str],
) -> tuple[dict[str, Any], str]:
    contract = {
        **policy.as_dict(),
        "profile_name": profile["name"],
        "profile_extensions": list(profile.get("extensions") or []),
        "effective_dispatch_keys": sorted(dispatch_keys),
        "effective_format_ids": sorted(policy.effective_format_ids(dispatch_keys)),
    }
    digest = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return contract, digest


def narrow_rag_helper_processing_limits(helper_modules: dict[str, Any], profile: dict[str, Any]) -> Any:
    """Narrow profile settings with the shared Hub safety ceilings."""

    values = dict(profile["limits"])

    def narrow(name: str, ceiling: int) -> None:
        current = values.get(name)
        values[name] = ceiling if current is None else min(int(current), ceiling)

    narrow("max_file_size_bytes", settings.codecompass_max_file_bytes)
    narrow("max_parser_lines", settings.codecompass_max_lines)
    narrow("parser_timeout_ms", settings.codecompass_parser_timeout_ms)
    narrow("max_parser_records_per_file", settings.codecompass_max_output_records)
    narrow("max_records_per_file", settings.codecompass_max_output_records)
    narrow("max_relation_records_per_file", settings.codecompass_max_output_records)
    narrow("max_xml_nodes", settings.codecompass_max_xml_nodes)
    narrow("max_xml_depth", settings.codecompass_max_xml_depth)
    narrow("max_yaml_aliases", settings.codecompass_max_yaml_aliases)
    narrow("max_notebook_cells", settings.codecompass_max_notebook_cells)
    narrow("max_notebook_cell_chars", settings.codecompass_max_notebook_cell_chars)
    narrow(
        "max_notebook_output_bytes",
        settings.codecompass_max_notebook_output_bytes,
    )
    narrow("max_tabular_rows", settings.codecompass_max_csv_rows)
    narrow("max_tabular_columns", settings.codecompass_max_csv_columns)
    return helper_modules["ProcessingLimits"](**values)


def observe_rag_helper_file_type_metrics(manifest: dict[str, Any]) -> None:
    """Emit non-functional telemetry without changing an index outcome."""

    try:
        from agent.services.file_type_metrics_service import get_file_type_metrics_service

        get_file_type_metrics_service().observe_rag_helper_manifest(manifest)
    except Exception as exc:
        _LOGGER.warning(
            "CodeCompass rag-helper file-type metrics could not be recorded: %s",
            exc,
        )


def enrich_rag_helper_file_type_manifest(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Persist additive capability evidence in the existing manifest."""

    try:
        from agent.services.file_type_manifest_service import get_file_type_manifest_service

        enriched = get_file_type_manifest_service().enrich_rag_helper_manifest(manifest)
        manifest_path.write_text(
            json.dumps(enriched, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return enriched
    except Exception as exc:
        _LOGGER.warning(
            "CodeCompass rag-helper manifest could not be enriched with file-type evidence: %s",
            exc,
        )
        return manifest
