"""File selection helpers of the rag_iterative chat path.

Import-graph and symbol-graph expansion, catalog filtering, initial chunk
filtering, batch-mode file resolution and token-bounded batch planning.
"""
from __future__ import annotations

import ast as _ast
import logging
import pathlib as _pl
from typing import Any, Callable

from agent.services.rag_context_packer import should_skip_initial_pack

_log = logging.getLogger("agent.routes.snakes_rag_iterative")

# Raw CodeCompass data files — large machine-readable JSONL/JSON blobs that are
# useless for the LLM (component-catalog.md is loaded separately as overview).
CODECOMPASS_DATA_FILES = frozenset({
    "rag-helper/out/context.jsonl",
    "rag-helper/out/details.jsonl",
    "rag-helper/out/embedding.jsonl",
    "rag-helper/out/graph_edges.jsonl",
    "rag-helper/out/graph_nodes.jsonl",
    "rag-helper/out/index.jsonl",
    "rag-helper/out/manifest.json",
    "rag-helper/out/relations.jsonl",
    "rag-helper/out/component-catalog.md",  # already included as catalog overview
})
_BATCH_FRAMING_OVERHEAD_CHARS = 200  # system prompt + question + batch header
_CHARS_PER_TOKEN = 4


def _expand_python_imports(
    file_entries: list[dict],
    repo_root: _pl.Path,
    *,
    depth: int,
    max_chars_per_file: int,
    seen_sources: set[str],
) -> list[dict]:
    """BFS import expansion: for each .py file in file_entries, follow local imports up to `depth` levels."""
    if depth <= 0:
        return file_entries

    def _local_imports(path: _pl.Path) -> list[_pl.Path]:
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except Exception:
            return []
        candidates: list[_pl.Path] = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    mod_parts = alias.name.split(".")
                    for n in range(len(mod_parts), 0, -1):
                        candidate = repo_root.joinpath(*mod_parts[:n]).with_suffix(".py")
                        if candidate.exists() and candidate.is_file():
                            candidates.append(candidate)
                            break
                        pkg = repo_root.joinpath(*mod_parts[:n], "__init__.py")
                        if pkg.exists():
                            candidates.append(pkg)
                            break
            elif isinstance(node, _ast.ImportFrom):
                if node.level and node.level > 0:
                    # relative import — resolve from current file's package
                    base = path.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    if node.module:
                        target = base.joinpath(*node.module.split(".")).with_suffix(".py")
                        if target.exists():
                            candidates.append(target)
                elif node.module:
                    mod_parts = node.module.split(".")
                    for n in range(len(mod_parts), 0, -1):
                        candidate = repo_root.joinpath(*mod_parts[:n]).with_suffix(".py")
                        if candidate.exists() and candidate.is_file():
                            candidates.append(candidate)
                            break
        return candidates

    frontier = [
        repo_root / e["path"]
        for e in file_entries
        if e["path"].endswith(".py")
    ]
    added = list(file_entries)

    for _level in range(depth):
        next_frontier: list[_pl.Path] = []
        for py_file in frontier:
            for dep in _local_imports(py_file):
                rel = str(dep.relative_to(repo_root)) if dep.is_relative_to(repo_root) else str(dep)
                if rel in seen_sources:
                    continue
                seen_sources.add(rel)
                try:
                    content = dep.read_text(encoding="utf-8", errors="replace")[:max_chars_per_file]
                except OSError:
                    continue
                lang = dep.suffix.lstrip(".") or "text"
                added.append({"path": rel, "lang": lang, "content": content})
                next_frontier.append(dep)
        frontier = next_frontier
        if not frontier:
            break

    return added

def _expand_via_symbol_graph(
    file_entries: list[dict],
    engine: Any,
    repo_root: _pl.Path,
    *,
    max_extra: int,
    seen_sources: set[str],
    max_chars_per_file: int,
) -> list[dict]:
    """Expand file set by searching for distinctive symbols from found files in the orchestrator
    symbol index — this follows method-call and cross-file-reference relationships."""
    if max_extra <= 0:
        return file_entries

    sym_graph: dict[str, list[str]] = getattr(engine, "_symbol_graph", {})
    if not sym_graph:
        return file_entries

    # Collect distinctive symbols: CamelCase class names or long underscore names (likely specific)
    key_symbols: list[str] = []
    for entry in file_entries:
        for sym in sym_graph.get(entry["path"], []):
            if (sym[0].isupper() and len(sym) >= 5) or (len(sym) >= 12 and "_" in sym):
                key_symbols.append(sym)

    if not key_symbols:
        return file_entries

    seen_sym: set[str] = set()
    unique_syms: list[str] = []
    for s in key_symbols:
        if s.lower() not in seen_sym:
            seen_sym.add(s.lower())
            unique_syms.append(s)
    unique_syms = unique_syms[:10]

    _log.debug("symbol_graph_expand: searching %d symbols: %s", len(unique_syms), unique_syms[:5])

    added = list(file_entries)
    remaining = max_extra

    for sym in unique_syms:
        if remaining <= 0:
            break
        try:
            results = engine.search(sym, top_k=5)
        except Exception:
            continue
        for chunk in results:
            if chunk.source in seen_sources or remaining <= 0:
                continue
            seen_sources.add(chunk.source)
            candidate = repo_root / chunk.source
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")[:max_chars_per_file]
            except OSError:
                continue
            lang = candidate.suffix.lstrip(".") or "text"
            added.append({"path": chunk.source, "lang": lang, "content": content})
            remaining -= 1

    return added


def _filter_catalog_to_relevant_modules(catalog_text: str, source_paths: list[str]) -> str:
    """Keep only catalog sections whose module is a parent/sibling of retrieved sources."""
    import re as _re

    top_prefixes: set[str] = set()
    for src in source_paths:
        normalized = src.replace("\\", "/").replace("-", "_")
        parts = normalized.split("/")
        if parts:
            top_prefixes.add(parts[0])
            if len(parts) > 1:
                top_prefixes.add(".".join(parts[:2]))

    def _relevant(module_name: str) -> bool:
        mn = module_name.replace("-", "_")
        top = mn.split(".")[0]
        return top in top_prefixes or any(
            mn == p or mn.startswith(p + ".") or p.startswith(mn + ".")
            for p in top_prefixes
        )

    parts = _re.split(r"(?=\n### `)", catalog_text)
    if len(parts) <= 1:
        return catalog_text
    kept = [parts[0]]
    for section in parts[1:]:
        m = _re.match(r"\n### `([^`]+)`", section)
        if m and _relevant(m.group(1)):
            kept.append(section)
    return "".join(kept) if len(kept) > 1 else catalog_text


def filter_initial_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ch for ch in chunks
        if ch["source"] not in CODECOMPASS_DATA_FILES
        and not should_skip_initial_pack(str(ch.get("source") or ""))
    ]


def _resolve_candidate(source: str, repo_root: _pl.Path) -> _pl.Path | None:
    candidate = _pl.Path(source) if source.startswith("/") else repo_root / source
    if candidate.exists() and candidate.is_file():
        return candidate
    if source.startswith("/app/"):
        candidate = repo_root / source[5:]
    return candidate if candidate.exists() and candidate.is_file() else None


def resolve_batch_file_entries(
    chunks: list[dict[str, Any]],
    repo_root: _pl.Path,
    *,
    max_chars_per_file: int,
    seen_sources: set[str],
    trace: dict[str, Any],
    cancelled: Callable[[], bool],
) -> list[dict[str, Any]] | None:
    """Read each distinct chunk source once; ``None`` when the run was cancelled."""
    file_entries: list[dict[str, Any]] = []
    for ch in chunks:
        if cancelled():
            return None
        meta = dict((ch or {}).get("metadata") or {})
        source = str(meta.get("file_path") or meta.get("path") or ch.get("source") or "").strip()
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)
        candidate = _resolve_candidate(source, repo_root)
        if candidate is None:
            trace.setdefault("skipped_sources", []).append(source)
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")[:max_chars_per_file]
        except OSError as exc:
            _log.debug("rag_iterative: cannot read %s: %s", candidate, exc)
            continue
        lang = candidate.suffix.lstrip(".") or "text"
        rel = str(candidate.relative_to(repo_root)) if candidate.is_relative_to(repo_root) else str(candidate)
        file_entries.append({"path": rel, "lang": lang, "content": content})
    return file_entries


def estimate_batch_tokens(entries: list[dict]) -> int:
    total_chars = _BATCH_FRAMING_OVERHEAD_CHARS + sum(len(e["content"]) + len(e["path"]) + 20 for e in entries)
    return max(1, total_chars // _CHARS_PER_TOKEN)


def plan_batches(file_entries: list[dict], max_input_tokens: int) -> list[list[dict]]:
    """Greedy batching: fill each batch up to ``max_input_tokens``."""
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    for entry in file_entries:
        test = current_batch + [entry]
        if current_batch and estimate_batch_tokens(test) > max_input_tokens:
            batches.append(current_batch)
            current_batch = [entry]
        else:
            current_batch = test
    if current_batch:
        batches.append(current_batch)
    return batches
