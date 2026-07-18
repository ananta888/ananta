"""Typed, bounded query contract for CodeCompass editor assistance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

EDITOR_QUERY_SCHEMA = "codecompass.editor_query.v1"
MAX_USER_LANGUAGE_CHARS = 600
MAX_QUERY_COMPONENT_CHARS = 1_000
MAX_QUERY_ITEMS = 32

_SPACE = re.compile(r"\s+")


class CodeCompassEditorIntent(StrEnum):
    node_explanation = "node_explanation"
    field_effect = "field_effect"
    io_contract = "io_contract"
    validation_issue = "validation_issue"
    runtime_error = "runtime_error"
    dependency = "dependency"
    safe_change = "safe_change"


class CodeCompassEditorDetailLevel(StrEnum):
    preview = "preview"
    selected = "selected"
    conversation = "conversation"


@dataclass(frozen=True, slots=True)
class CodeCompassEditorQueryInput:
    """Canonical non-content inputs used to form one retrieval query.

    Registry/node/contract/symbol/neighbor fields come from the immutable editor
    context. Natural language is a bounded supplemental signal, never the query's
    sole structural input.
    """

    intent: CodeCompassEditorIntent
    detail_level: CodeCompassEditorDetailLevel
    registry_version: str
    node_kind: str
    field_path: str | None
    backend_contract: str | None
    symbols: tuple[str, ...]
    graph_neighbors: tuple[str, ...]
    user_language: str
    schema: str = EDITOR_QUERY_SCHEMA

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CodeCompassEditorQueryInput":
        raw = dict(value or {})
        supplied_schema = str(raw.get("schema") or "").strip()
        if supplied_schema and supplied_schema != EDITOR_QUERY_SCHEMA:
            raise ValueError("codecompass_editor_query_schema_invalid")
        try:
            intent = CodeCompassEditorIntent(str(raw.get("intent") or "").strip())
        except ValueError as exc:
            raise ValueError("codecompass_editor_intent_invalid") from exc
        try:
            detail_level = CodeCompassEditorDetailLevel(str(raw.get("detail_level") or "conversation").strip().lower())
        except ValueError as exc:
            raise ValueError("codecompass_editor_detail_level_invalid") from exc
        registry_version = _required(raw.get("registry_version"), "registry_version", 160)
        node_kind = _required(raw.get("node_kind"), "node_kind", 200)
        field_path = _optional(raw.get("field_path"), 500)
        backend_contract = _backend_contract(raw.get("backend_contract"))
        symbols = _items(raw.get("symbols"), "symbols")
        graph_neighbors = _items(raw.get("graph_neighbors"), "graph_neighbors")
        if not backend_contract and not symbols:
            raise ValueError("codecompass_editor_backend_contract_or_symbols_required")
        user_language = _text(raw.get("user_language"))[:MAX_USER_LANGUAGE_CHARS]
        return cls(
            intent=intent,
            detail_level=detail_level,
            registry_version=registry_version,
            node_kind=node_kind,
            field_path=field_path,
            backend_contract=backend_contract,
            symbols=symbols,
            graph_neighbors=graph_neighbors,
            user_language=user_language,
        )

    @classmethod
    def from_editor_context(
        cls,
        context: Any,
        *,
        user_language: str,
        detail_level: CodeCompassEditorDetailLevel | str | None = None,
    ) -> "CodeCompassEditorQueryInput":
        """Project an immutable editor snapshot into the retrieval contract.

        Only already-bounded structural context is used.  This projection does
        not resolve files, query CodeCompass, or invoke a model.
        """

        location = getattr(context, "location", None)
        if location is None:
            raise ValueError("codecompass_editor_location_required")
        target_kind = str(getattr(location, "target_kind", "") or "")
        entity_id = str(getattr(location, "entity_id", "") or "")
        role = str(getattr(location, "role", "") or "")
        raw_graph_excerpt = getattr(context, "graph_excerpt", {})
        raw_effective = getattr(context, "effective_configuration", {})
        graph_excerpt = dict(raw_graph_excerpt) if isinstance(raw_graph_excerpt, Mapping) else {}
        effective = dict(raw_effective) if isinstance(raw_effective, Mapping) else {}
        steps = [dict(item) for item in list(graph_excerpt.get("steps") or []) if isinstance(item, Mapping)]
        edges = [dict(item) for item in list(graph_excerpt.get("edges") or []) if isinstance(item, Mapping)]
        focused = next((item for item in steps if str(item.get("id") or "") == entity_id), {})
        raw_metadata = effective.get("step_metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        node_kind = str(effective.get("step_kind") or focused.get("kind") or target_kind).strip()

        backend_contract: Any = None
        for key in ("backend_contract", "io_contract", "contract"):
            candidate = metadata.get(key)
            if isinstance(candidate, (str, Mapping)) and candidate:
                backend_contract = candidate
                break
        if backend_contract is None:
            candidate = focused.get("io")
            if isinstance(candidate, (str, Mapping)) and candidate:
                backend_contract = candidate

        symbol_values: list[Any] = []
        for key in ("symbol", "symbols", "backend_symbol", "backend_symbols"):
            raw_symbols = metadata.get(key)
            if isinstance(raw_symbols, (list, tuple, set, frozenset)):
                symbol_values.extend(raw_symbols)
            elif raw_symbols is not None:
                symbol_values.append(raw_symbols)
        if backend_contract is None and not symbol_values:
            # The registered node kind is a stable index lookup key and keeps
            # natural language from ever becoming the sole query input.
            symbol_values.append(node_kind)

        neighbor_values: list[str] = []
        for step in steps:
            step_id = str(step.get("id") or "").strip()
            if step_id and step_id != entity_id:
                neighbor_values.append(step_id)
        for edge in edges:
            for key in ("source", "target"):
                neighbor_id = str(edge.get(key) or "").strip()
                if neighbor_id and neighbor_id != entity_id:
                    neighbor_values.append(neighbor_id)

        if detail_level is None:
            raw_extensions = getattr(context, "extensions", {})
            raw_budget_value = (
                raw_extensions.get("ananta.context_budget") if isinstance(raw_extensions, Mapping) else None
            )
            raw_budget = dict(raw_budget_value) if isinstance(raw_budget_value, Mapping) else {}
            detail_level = (
                CodeCompassEditorDetailLevel.selected
                if str(raw_budget.get("profile") or "") == "selected"
                else CodeCompassEditorDetailLevel.conversation
            )
        return cls.from_mapping(
            {
                "intent": intent_for_location(target_kind=target_kind, role=role).value,
                "detail_level": (
                    detail_level.value if isinstance(detail_level, CodeCompassEditorDetailLevel) else str(detail_level)
                ),
                "registry_version": getattr(context, "node_registry_version", ""),
                "node_kind": node_kind,
                "field_path": getattr(location, "field_path", None),
                "backend_contract": backend_contract,
                "symbols": symbol_values,
                "graph_neighbors": neighbor_values,
                "user_language": user_language,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "intent": self.intent.value,
            "detail_level": self.detail_level.value,
            "registry_version": self.registry_version,
            "node_kind": self.node_kind,
            "field_path": self.field_path,
            "backend_contract": self.backend_contract,
            "symbols": list(self.symbols),
            "graph_neighbors": list(self.graph_neighbors),
            "user_language": self.user_language,
        }

    def retrieval_query(self) -> str:
        """Render the stable structured search string used by production retrieval."""

        rows = [
            f"intent:{self.intent.value}",
            f"registry_version:{self.registry_version}",
            f"node_kind:{self.node_kind}",
        ]
        if self.field_path:
            rows.append(f"field_path:{self.field_path}")
        if self.backend_contract:
            rows.append(f"backend_contract:{self.backend_contract}")
        if self.symbols:
            rows.append(f"symbols:{' '.join(self.symbols)}")
        if self.graph_neighbors:
            rows.append(f"graph_neighbors:{' '.join(self.graph_neighbors)}")
        if self.user_language:
            rows.append(f"user_language:{self.user_language}")
        return "\n".join(rows)


def intent_for_location(*, target_kind: str, role: str | None = None) -> CodeCompassEditorIntent:
    normalized = str(target_kind or "").strip().lower()
    normalized_role = str(role or "").strip().lower()
    if normalized_role in {"input", "output", "io", "port"}:
        return CodeCompassEditorIntent.io_contract
    if normalized == "field":
        return CodeCompassEditorIntent.field_effect
    if normalized == "validation":
        return CodeCompassEditorIntent.validation_issue
    if normalized == "runtime":
        return CodeCompassEditorIntent.runtime_error
    if normalized == "edge":
        return CodeCompassEditorIntent.dependency
    if normalized in {"canvas", "palette_item"}:
        return CodeCompassEditorIntent.safe_change
    return CodeCompassEditorIntent.node_explanation


def _required(value: Any, field: str, maximum: int) -> str:
    normalized = _text(value)
    if not normalized:
        raise ValueError(f"codecompass_editor_{field}_required")
    if len(normalized) > maximum:
        raise ValueError(f"codecompass_editor_{field}_too_long")
    return normalized


def _optional(value: Any, maximum: int) -> str | None:
    normalized = _text(value)
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ValueError("codecompass_editor_query_component_too_long")
    return normalized


def _backend_contract(value: Any) -> str | None:
    if isinstance(value, Mapping):
        if not value:
            return None
        normalized = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    else:
        normalized = _text(value)
    if not normalized:
        return None
    if len(normalized) > MAX_QUERY_COMPONENT_CHARS:
        raise ValueError("codecompass_editor_backend_contract_too_long")
    return normalized


def _items(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"codecompass_editor_{field}_invalid")
    normalized = sorted({item for raw in value if (item := _text(raw))})
    if len(normalized) > MAX_QUERY_ITEMS:
        raise ValueError(f"codecompass_editor_{field}_too_many")
    if any(len(item) > 200 for item in normalized):
        raise ValueError(f"codecompass_editor_{field}_item_too_long")
    return tuple(normalized)


def _text(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").strip())


__all__ = [
    "CodeCompassEditorDetailLevel",
    "CodeCompassEditorIntent",
    "CodeCompassEditorQueryInput",
    "EDITOR_QUERY_SCHEMA",
    "MAX_USER_LANGUAGE_CHARS",
    "intent_for_location",
]
