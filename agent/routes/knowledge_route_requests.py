"""Request parsing and response payload shaping for the knowledge routes."""

from __future__ import annotations

import time

from flask import g, request

from agent.common.errors import BadRequestError
from agent.models import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionIndexRequest,
    KnowledgeCollectionSearchRequest,
    KnowledgeSourceIndexRequest,
)
from agent.routes.knowledge_wiki_presets import WIKI_IMPORT_PRESETS
from agent.services.file_type_support_service import (
    FileTypeSupportFilter,
    FileTypeSupportFilterError,
    parse_optional_boolean,
)

FILE_TYPE_SUPPORT_QUERY_FIELDS = frozenset(
    {
        "priority",
        "support_level",
        "level",
        "dimension",
        "pipeline",
        "missing_parser",
        "missing_runtime",
        "enabled",
    }
)


def collection_create_request() -> KnowledgeCollectionCreateRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeCollectionCreateRequest.model_validate(payload)


def collection_index_request() -> KnowledgeCollectionIndexRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeCollectionIndexRequest.model_validate(payload)


def collection_search_request() -> KnowledgeCollectionSearchRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeCollectionSearchRequest.model_validate(payload)


def source_index_request() -> KnowledgeSourceIndexRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeSourceIndexRequest.model_validate(payload)


def wiki_import_request() -> dict:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    corpus_path = str(payload.get("corpus_path") or "").strip()
    if not corpus_path:
        raise BadRequestError("corpus_path_required")
    source_id = str(payload.get("source_id") or "").strip() or None
    index_path = str(payload.get("index_path") or "").strip() or None
    import_format = str(payload.get("import_format") or "").strip() or None
    profile_name = str(payload.get("profile_name") or "").strip() or None
    language = str(payload.get("language") or "en").strip().lower() or "en"
    strict = bool(payload.get("strict", False))
    async_mode = bool(payload.get("async", True))
    codecompass_prerender = bool(payload.get("codecompass_prerender", False))
    raw_source_metadata = payload.get("source_metadata") or {}
    if not isinstance(raw_source_metadata, dict):
        raise BadRequestError("invalid_source_metadata")
    source_metadata = dict(raw_source_metadata)
    return {
        "corpus_path": corpus_path,
        "source_id": source_id,
        "index_path": index_path,
        "import_format": import_format,
        "profile_name": profile_name,
        "language": language,
        "strict": strict,
        "async_mode": async_mode,
        "codecompass_prerender": codecompass_prerender,
        "source_metadata": source_metadata,
    }


def wiki_import_url_request() -> dict:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    preset_id = str(payload.get("preset_id") or "").strip()
    corpus_url = str(payload.get("corpus_url") or "").strip()
    index_url = str(payload.get("index_url") or "").strip()
    if not preset_id and not corpus_url:
        raise BadRequestError("wiki_corpus_url_required")
    selected_preset = next((item for item in WIKI_IMPORT_PRESETS if item["id"] == preset_id), None) if preset_id else None
    if preset_id and selected_preset is None:
        raise BadRequestError("invalid_wiki_preset")
    effective_url = str(selected_preset.get("corpus_url") if selected_preset else corpus_url).strip()
    if not effective_url:
        raise BadRequestError("wiki_corpus_url_required")
    source_id = str(payload.get("source_id") or "").strip() or (
        str(selected_preset.get("source_id") or "").strip() if selected_preset else None
    )
    profile_name = str(payload.get("profile_name") or "").strip() or None
    language = str(payload.get("language") or (selected_preset.get("language") if selected_preset else "en")).strip().lower() or "en"
    strict = bool(payload.get("strict", False))
    async_mode = bool(payload.get("async", True))
    codecompass_prerender = bool(payload.get("codecompass_prerender", selected_preset.get("codecompass_prerender", False) if selected_preset else False))
    raw_source_metadata = payload.get("source_metadata") or {}
    if not isinstance(raw_source_metadata, dict):
        raise BadRequestError("invalid_source_metadata")
    source_metadata = dict(raw_source_metadata)
    if selected_preset is not None:
        index_url = str(selected_preset.get("index_url") or index_url).strip()
        if selected_preset.get("supported") is False:
            raise BadRequestError("wiki_preset_not_supported")
        source_metadata.setdefault("preset_id", selected_preset["id"])
        source_metadata.setdefault("preset_label", selected_preset["label"])
        source_metadata.setdefault("import_format", selected_preset.get("import_format"))
    return {
        "corpus_url": effective_url,
        "index_url": index_url or None,
        "source_id": source_id or None,
        "profile_name": profile_name,
        "language": language,
        "strict": strict,
        "async_mode": async_mode,
        "codecompass_prerender": codecompass_prerender,
        "source_metadata": source_metadata,
    }


def current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def file_type_support_filter() -> FileTypeSupportFilter:
    unknown_fields = sorted(set(request.args) - FILE_TYPE_SUPPORT_QUERY_FIELDS)
    if unknown_fields:
        raise BadRequestError(
            "invalid_file_type_support_filter",
            {"unknown_fields": unknown_fields},
        )
    support_levels = [
        *request.args.getlist("support_level"),
        *request.args.getlist("level"),
    ]
    try:
        return FileTypeSupportFilter.build(
            priorities=request.args.getlist("priority"),
            support_levels=support_levels,
            dimensions=request.args.getlist("dimension"),
            pipelines=request.args.getlist("pipeline"),
            missing_parser=parse_optional_boolean(
                request.args.get("missing_parser"),
                field_name="missing_parser",
            ),
            missing_runtime=parse_optional_boolean(
                request.args.get("missing_runtime"),
                field_name="missing_runtime",
            ),
            enabled=parse_optional_boolean(
                request.args.get("enabled"),
                field_name="enabled",
            ),
        )
    except FileTypeSupportFilterError as exc:
        raise BadRequestError("invalid_file_type_support_filter") from exc


def normalize_security_metadata_patch(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise BadRequestError("invalid_security_metadata_patch")
    allowed_keys = {"classification", "source_origin", "sensitivity", "tenancy", "approval_class", "chunk_security_tags"}
    patch: dict = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in allowed_keys:
            continue
        if normalized_key == "chunk_security_tags":
            if not isinstance(value, list):
                raise BadRequestError("invalid_chunk_security_tags")
            patch[normalized_key] = [str(item).strip().lower() for item in value if str(item).strip()]
        else:
            patch[normalized_key] = str(value or "").strip().lower() or None
    if not patch:
        raise BadRequestError("empty_security_metadata_patch")
    return patch


def apply_security_metadata_patch(*, knowledge_index, patch: dict, actor: str):
    metadata = dict(getattr(knowledge_index, "index_metadata", None) or {})
    security_metadata = dict(metadata.get("security_metadata") or {})
    security_metadata.update({key: value for key, value in patch.items() if value is not None})
    metadata["security_metadata"] = security_metadata
    metadata["security_metadata_updated_by"] = actor
    metadata["security_metadata_updated_at"] = time.time()
    knowledge_index.index_metadata = metadata
    return knowledge_index


def knowledge_index_payload(item) -> dict:
    payload = item.model_dump()
    metadata = dict(payload.get("index_metadata") or {})
    payload["security_metadata"] = dict(metadata.get("security_metadata") or {})
    return payload


def model_status(item) -> str:
    direct = getattr(item, "status", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    if hasattr(item, "model_dump"):
        payload = item.model_dump()
        if isinstance(payload, dict):
            value = payload.get("status")
            if isinstance(value, str):
                return value
    return ""


def strict_if_match_version(raw_if_match: str) -> int:
    """Parse a present, strong ``If-Match`` header into a positive lock version."""

    if raw_if_match.startswith("W/"):
        raise BadRequestError("if_match_invalid")
    normalized = raw_if_match[1:-1] if (
        len(raw_if_match) >= 2
        and raw_if_match[0] == raw_if_match[-1] == '"'
    ) else raw_if_match
    try:
        version = int(normalized)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("if_match_invalid") from exc
    if version < 1:
        raise BadRequestError("if_match_invalid")
    return version
