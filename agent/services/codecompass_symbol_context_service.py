"""Build bounded symbol-level context from CodeCompass detail and relation outputs."""
from __future__ import annotations

import json
import pathlib as _pl
import re
from dataclasses import dataclass
from typing import Any


_DETAIL_KINDS = (
    "python_function",
    "python_method",
    "typescript_function",
    "typescript_method",
    "typescript_constructor",
    "java_method",
    "java_constructor",
)

_RELATION_TYPES = (
    "calls_probable_target",
    "calls",
    "declares_method",
    "contains_method",
)

_STOPWORDS = {
    "bitte", "mir", "den", "die", "das", "der", "und", "oder", "wie", "was",
    "ist", "sind", "im", "in", "mit", "von", "zu", "auf", "fuer", "für",
    "erkläre", "erklaere", "code", "the", "and", "or", "what", "how",
}


@dataclass(frozen=True)
class CodeCompassSymbolSnippet:
    path: str
    symbol: str
    kind: str
    line_start: int
    line_end: int
    score: float
    content: str
    source: str
    node_id: str | None = None
    relation: str | None = None


def _iter_jsonl(path: _pl.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _tokens(value: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(value or ""))
        if token.lower() not in _STOPWORDS
    }


def _symbol_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(record.get(key) or "")
        for key in ("file", "name", "class_name", "kind", "target_resolved", "target")
    )


def _score_record(record: dict[str, Any], *, query_tokens: set[str], source_scores: dict[str, float]) -> float:
    text = _symbol_text(record).lower()
    score = source_scores.get(str(record.get("file") or ""), 0.0)
    for token in query_tokens:
        if token in text:
            score += 10.0
    if str(record.get("name") or "").lower() in query_tokens:
        score += 20.0
    return score


def _read_range(repo_root: _pl.Path, path: str, line_start: int, line_end: int) -> str:
    candidate = repo_root / path
    if not candidate.exists() or not candidate.is_file():
        return ""
    try:
        lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(1, int(line_start or 1))
    end = max(start, int(line_end or start))
    excerpt = lines[start - 1:end]
    return "\n".join(f"{idx}: {line}" for idx, line in enumerate(excerpt, start=start))


def _detail_output_dir(repo_root: _pl.Path) -> _pl.Path:
    return repo_root / "rag-helper" / "out"


def build_codecompass_symbol_context(
    *,
    repo_root: _pl.Path,
    query: str,
    ranked_sources: list[dict[str, Any]],
    max_snippets: int = 8,
    max_lines_per_snippet: int = 80,
) -> list[CodeCompassSymbolSnippet]:
    """Return bounded method/function snippets plus direct graph neighbors.

    The service is deterministic and local-only. It consumes CodeCompass output
    artifacts and source files, but it does not orchestrate workers or call an LLM.
    """
    out_dir = _detail_output_dir(repo_root)
    details_dir = out_dir / "details_by_kind"
    relations_dir = out_dir / "relations_by_type"
    query_tokens = _tokens(query)
    source_scores = {
        str(row.get("source") or row.get("path") or ""): float(row.get("score") or 0.0)
        for row in ranked_sources
        if isinstance(row, dict)
    }
    ranked_paths = set(source_scores)

    detail_records: list[dict[str, Any]] = []
    for kind in _DETAIL_KINDS:
        for record in _iter_jsonl(details_dir / f"{kind}.jsonl"):
            file_path = str(record.get("file") or "").strip()
            if not file_path:
                continue
            score = _score_record(record, query_tokens=query_tokens, source_scores=source_scores)
            if file_path not in ranked_paths and score <= 0:
                continue
            detail_records.append({**record, "_cc_score": score})

    if not detail_records:
        return []

    by_id = {str(record.get("id") or ""): record for record in detail_records if str(record.get("id") or "")}
    selected: dict[str, dict[str, Any]] = {}
    for record in sorted(detail_records, key=lambda row: (-float(row.get("_cc_score") or 0.0), str(row.get("file") or ""), int(row.get("line") or 1))):
        record_id = str(record.get("id") or "")
        if record_id and record_id not in selected:
            selected[record_id] = {**record, "_cc_source": "codecompass_symbol"}
        if len(selected) >= max(1, max_snippets):
            break

    relation_budget = max(0, max_snippets - len(selected))
    if selected:
        seed_ids = set(selected)
        for rel_type in _RELATION_TYPES:
            for relation in _iter_jsonl(relations_dir / f"{rel_type}.jsonl"):
                source_id = str(relation.get("source_id") or relation.get("from") or "").strip()
                target_id = str(relation.get("target_id") or relation.get("to") or "").strip()
                if source_id in selected and target_id in selected and not selected[target_id].get("_cc_relation"):
                    selected[target_id] = {**selected[target_id], "_cc_relation": rel_type}
                    continue
                neighbor_id = target_id if source_id in seed_ids else source_id if target_id in seed_ids else ""
                if not neighbor_id or neighbor_id in selected:
                    continue
                if relation_budget <= 0:
                    continue
                neighbor = by_id.get(neighbor_id)
                if not neighbor:
                    continue
                selected[neighbor_id] = {
                    **neighbor,
                    "_cc_source": "codecompass_graph_neighbor",
                    "_cc_relation": rel_type,
                    "_cc_score": float(neighbor.get("_cc_score") or 0.0) + 1.0,
                }
                relation_budget -= 1
                if len(selected) >= max_snippets:
                    break
            if len(selected) >= max_snippets:
                break

    file_records: dict[str, list[dict[str, Any]]] = {}
    for record in detail_records:
        file_records.setdefault(str(record.get("file") or ""), []).append(record)
    for records in file_records.values():
        records.sort(key=lambda item: int(item.get("line") or 1))

    snippets: list[CodeCompassSymbolSnippet] = []
    for record in sorted(selected.values(), key=lambda row: (-float(row.get("_cc_score") or 0.0), str(row.get("file") or ""), int(row.get("line") or 1))):
        path = str(record.get("file") or "").strip()
        start = max(1, int(record.get("line") or 1))
        next_line = None
        for sibling in file_records.get(path, []):
            line = int(sibling.get("line") or 0)
            if line > start:
                next_line = line
                break
        end = start + max(1, max_lines_per_snippet) - 1
        if next_line is not None:
            end = min(end, max(start, next_line - 1))
        content = _read_range(repo_root, path, start, end)
        if not content:
            continue
        snippets.append(
            CodeCompassSymbolSnippet(
                path=path,
                symbol=str(record.get("name") or record.get("class_name") or "").strip(),
                kind=str(record.get("kind") or "").strip(),
                line_start=start,
                line_end=end,
                score=float(record.get("_cc_score") or 0.0),
                content=content,
                source=str(record.get("_cc_source") or "codecompass_symbol"),
                node_id=str(record.get("id") or "").strip() or None,
                relation=str(record.get("_cc_relation") or "").strip() or None,
            )
        )
        if len(snippets) >= max_snippets:
            break
    return snippets


def format_symbol_context_section(snippets: list[CodeCompassSymbolSnippet]) -> str:
    if not snippets:
        return ""
    blocks = ["=== CodeCompass Symbol-/Graph-Kontext ==="]
    for idx, snippet in enumerate(snippets, 1):
        relation = f", relation: {snippet.relation}" if snippet.relation else ""
        blocks.append(
            f"{idx}. {snippet.path}:{snippet.line_start}-{snippet.line_end} "
            f"({snippet.kind} {snippet.symbol}, score: {snippet.score:.1f}, source: {snippet.source}{relation})\n"
            f"```\n{snippet.content}\n```"
        )
    return "\n\n".join(blocks)
