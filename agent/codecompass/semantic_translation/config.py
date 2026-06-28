from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SemanticTranslationConfig:
    enabled: bool = False
    source_languages: tuple[str, ...] = ("java",)
    target_languages: tuple[str, ...] = ("typescript", "kotlin")
    adapters: tuple[str, ...] = ("java-regex",)
    output_dir: str = "semantic-translation"
    max_graph_records: int = 5000
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


def load_semantic_translation_config(env: dict[str, str] | None = None) -> SemanticTranslationConfig:
    source = env if env is not None else os.environ
    diagnostics: list[str] = []
    enabled = str(source.get("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    source_languages = _csv(source.get("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_LANGUAGES"), ("java",), diagnostics)
    target_languages = _csv(source.get("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_TARGETS"), ("typescript", "kotlin"), diagnostics)
    adapters = _csv(source.get("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ADAPTERS"), ("java-regex",), diagnostics)
    output_dir = str(source.get("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_OUTPUT_DIR") or "semantic-translation").strip()
    max_graph_records = _int(source.get("ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_MAX_GRAPH_RECORDS"), 5000, diagnostics)
    return SemanticTranslationConfig(
        enabled=enabled,
        source_languages=source_languages,
        target_languages=target_languages,
        adapters=adapters,
        output_dir=output_dir or "semantic-translation",
        max_graph_records=max_graph_records,
        diagnostics=tuple(diagnostics),
    )


def _csv(raw: str | None, default: tuple[str, ...], diagnostics: list[str]) -> tuple[str, ...]:
    if raw is None or not str(raw).strip():
        return default
    values = tuple(item.strip().lower() for item in str(raw).split(",") if item.strip())
    if not values:
        diagnostics.append("invalid_empty_csv_config")
        return default
    return values


def _int(raw: str | None, default: int, diagnostics: list[str]) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        diagnostics.append("invalid_integer_config")
        return default
    if value <= 0:
        diagnostics.append("non_positive_integer_config")
        return default
    return min(value, 100_000)
