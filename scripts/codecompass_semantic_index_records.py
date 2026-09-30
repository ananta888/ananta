"""Semantic-translation graph records for ``scripts/setup_codecompass_index.py``.

Runs the registered semantic adapters over the selected files and returns the
bounded graph records plus a summary. The repository root is an explicit
input so callers (and tests) decide which tree relative paths refer to.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path


def build_semantic_translation_records(files: list[Path], *, root: Path) -> tuple[list[dict], dict]:
    from agent.codecompass.semantic_translation.config import load_semantic_translation_config
    from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRuleRegistry
    from agent.codecompass.semantic_translation.registry import get_semantic_adapter_registry

    config = load_semantic_translation_config()
    if not config.enabled:
        return [], {"enabled": False, "warnings": list(config.diagnostics)}
    registry = get_semantic_adapter_registry()
    allowed_languages = set(config.source_languages)
    analyze_all = "all" in allowed_languages or "*" in allowed_languages
    records: list[dict] = []
    diagnostics: list[dict] = []
    analyzed_files = 0
    analyzed_by_language: dict[str, int] = defaultdict(int)
    parser_strategies: dict[str, str] = {}
    limit_reached = False
    for path in files:
        rel = str(path.relative_to(root))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            diagnostics.append({"code": "semantic_file_read_failed", "path": rel, "message": str(exc)})
            continue
        adapter = registry.find(rel, content)
        if adapter is None:
            continue
        language_aliases = {adapter.language}
        if adapter.language == "typescript" and path.suffix.lower() in {".js", ".jsx"}:
            language_aliases.add("javascript")
        if not analyze_all and not language_aliases.intersection(allowed_languages):
            continue
        analyzed_files += 1
        analyzed_by_language[adapter.language] += 1
        parser_strategies[adapter.language] = adapter.parser_strategy
        emitted = registry.emit_graph_records(rel, content)
        batch = [*emitted["nodes"], *emitted["edges"]]
        remaining = max(0, config.max_graph_records - len(records))
        records.extend(batch[:remaining])
        diagnostics.extend(emitted["diagnostics"])
        if len(batch) > remaining or len(records) >= config.max_graph_records:
            diagnostics.append({"code": "semantic_graph_record_limit_reached", "path": rel})
            limit_reached = True
            break
    if not limit_reached:
        rules = EquivalenceRuleRegistry().records()
        remaining = max(0, config.max_graph_records - len(records))
        records.extend(rules[:remaining])
        if len(rules) > remaining:
            diagnostics.append({"code": "semantic_graph_record_limit_reached", "path": ""})
    summary = {
        "enabled": True,
        "analyzed_files": analyzed_files,
        "analyzed_by_language": dict(sorted(analyzed_by_language.items())),
        "recognized_languages": sorted(analyzed_by_language),
        "parser_strategies": dict(sorted(parser_strategies.items())),
        "record_count": len(records),
        "node_count": sum(
            1
            for row in records
            if (row.get("_provenance") or {}).get("output_kind") == "semantic_nodes"
        ),
        "edge_count": sum(
            1
            for row in records
            if (row.get("_provenance") or {}).get("output_kind") == "semantic_edges"
        ),
        "rule_count": sum(
            1
            for row in records
            if (row.get("_provenance") or {}).get("output_kind") == "equivalence_rules"
        ),
        "warnings": [str(row.get("code") or row) for row in diagnostics],
        "diagnostics": diagnostics,
    }
    return records, summary
