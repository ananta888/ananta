"""RAG-Iterative: retrieve relevant files via CodeCompass, read them fully,
batch them to fit the LLM context window, process each batch independently,
then synthesize all intermediate answers into a final response.

This sits between the single-shot RAG path (all chunks truncated, one LLM call)
and the full_scan path (entire repo scanned). Here only the files that RAG
identified as relevant are used, but read at full length and processed
iteratively when they exceed the context budget.
"""
from __future__ import annotations

import logging
import pathlib as _pl
from time import time as _time
from typing import Any

import ast as _ast

from agent.config import lookup_model_context_tokens, settings as _cfg_settings
from agent.llm_integration import generate_text
from agent.routes.ai_snake_config import _current_config
from agent.services.rag_context_packer import (
    build_rag_context_pack,
    format_packed_files_section,
    packed_file_memory_summary,
    should_skip_initial_pack,
)
from agent.services.codecompass_symbol_context_service import (
    build_codecompass_symbol_context,
    format_symbol_context_section,
)
from agent.services.snake_chat_cancellation import is_chat_cancelled

_log = logging.getLogger(__name__)


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


_SYSTEM_PROMPT = (
    "Du bist AI-Snake im Ananta Hub.\n"
    "Regeln (streng):\n"
    "1) Antworte nur auf Basis des bereitgestellten Kontexts und der Nutzerfrage.\n"
    "2) Erfinde keine Produkte, URLs, Features, Befehle oder Fakten.\n"
    "3) Wenn Informationen fehlen oder unsicher sind, sage explizit: "
    "\"Unklar, bitte Kontext pruefen\".\n"
    "4) Gib keine externen Links aus, ausser der Nutzer hat explizit danach gefragt.\n"
    "5) Halte Antworten kurz, konkret, technisch nutzbar, auf Deutsch.\n"
    "6) Wenn Schrittfolge noetig ist, gib maximal 5 nummerierte Schritte.\n"
)


def _answer_budget_instruction(limits: Any | None) -> str:
    policy = str(getattr(limits, "answer_overflow_policy", "") or "").strip().lower()
    if not policy:
        policy = str(_current_config().get("chat_answer_overflow_policy") or "allow").strip().lower()
    if policy not in {"allow", "summarize", "truncate"}:
        policy = "allow"
    if policy == "allow":
        return ""
    try:
        limit = int(getattr(limits, "answer_chars", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        try:
            limit = int(float(_current_config().get("chat_answer_chars") or 12000))
        except (TypeError, ValueError):
            limit = 12000
    limit = max(600, min(50000, limit))
    action = "priorisiere die wichtigsten Punkte und fasse zusammen" if policy == "summarize" else "halte die Antwort strikt kurz"
    return (
        f"Antwort-Budget: maximal {limit} Zeichen. "
        f"Wenn mehr Details vorhanden sind, {action}."
    )


def worker_chat_rag_iterative(
    question: str,
    *,
    provider: str = "lmstudio",
    model: str | None = None,
    limits: Any | None = None,
    rec: Any | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    cancel_event: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    """Iterative RAG: fetch relevant files, read fully, batch → LLM → synthesize."""
    trace: dict[str, Any] = {"mode": "rag_iterative"}

    def _cancelled() -> bool:
        if not is_chat_cancelled(cancel_event):
            return False
        trace["cancelled"] = True
        trace["error"] = "cancelled"
        return True

    llm_history = [{"role": "system", "content": _SYSTEM_PROMPT}, *list(conversation_history or [])]
    trace["conversation_history_messages"] = len(conversation_history or [])

    cfg = _current_config()
    budget_instruction = _answer_budget_instruction(limits)
    timeout_s = max(60, min(7200, int(float(cfg.get("chat_ask_timeout_s") or 180))))
    max_chars_per_file = max(1000, min(20000, int(float(cfg.get("chat_full_scan_chars_per_file") or 4000))))

    model_context_tokens = lookup_model_context_tokens(model) or int(
        _cfg_settings.lmstudio_max_context_tokens
    )
    # Reserve ~25% of the context for the LLM's output + framing
    max_input_tokens = max(256, int(model_context_tokens * 0.75))
    trace["model_context_tokens"] = model_context_tokens
    trace["max_input_tokens"] = max_input_tokens

    # --- Step 1: RAG retrieval via live RepositoryMapEngine scan ---
    repo_root = _pl.Path(getattr(_cfg_settings, "rag_repo_root", ".")).resolve()
    _rag_max = int(_cfg_settings.rag_max_chunks or 40)
    _engine: Any = None
    try:
        from agent.hybrid_orchestrator import RepositoryMapEngine

        if _cancelled():
            return "", trace
        _engine = RepositoryMapEngine(repo_root)
        raw_chunks = _engine.search(question, top_k=_rag_max)
        chunks = [
            {"source": ch.source, "metadata": {"file_path": ch.source}, "score": ch.score}
            for ch in raw_chunks
        ]
        trace["retrieval_top_scores"] = [
            {"source": ch.source, "score": round(ch.score, 2)} for ch in raw_chunks[:10]
        ]
    except Exception as exc:
        _log.warning("rag_iterative: retrieval failed: %s", exc)
        trace["error"] = f"retrieval_failed: {exc}"
        return "", trace

    if _cancelled():
        return "", trace

    # Filter out raw CodeCompass data files — large machine-readable JSONL/JSON blobs
    # that are useless for the LLM (component-catalog.md is loaded separately as overview).
    _CODECOMPASS_DATA_FILES = {
        "rag-helper/out/context.jsonl",
        "rag-helper/out/details.jsonl",
        "rag-helper/out/embedding.jsonl",
        "rag-helper/out/graph_edges.jsonl",
        "rag-helper/out/graph_nodes.jsonl",
        "rag-helper/out/index.jsonl",
        "rag-helper/out/manifest.json",
        "rag-helper/out/relations.jsonl",
        "rag-helper/out/component-catalog.md",  # already included as catalog overview
    }
    chunks = [
        ch for ch in chunks
        if ch["source"] not in _CODECOMPASS_DATA_FILES
        and not should_skip_initial_pack(str(ch.get("source") or ""))
    ]
    trace["rag_chunks_found"] = len(chunks)
    if not chunks:
        trace["error"] = "no_rag_chunks"
        return "", trace

    # --- Step 2: Mode check — tool-call path vs. batch path ---
    _tc_enabled_cfg = cfg.get("rag_iterative_tool_calls_enabled")
    if _tc_enabled_cfg is None:
        tool_calls_enabled = bool(_cfg_settings.rag_iterative_tool_calls_enabled)
    else:
        tool_calls_enabled = str(_tc_enabled_cfg).lower() not in {"false", "0", "off", "no", ""}

    if tool_calls_enabled:
        # --- Tool-call mode: send catalog overview + ranked file list; LLM loads what it needs ---
        from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

        max_tool_calls = max(0, int(
            cfg.get("rag_iterative_max_tool_calls") if cfg.get("rag_iterative_max_tool_calls") is not None
            else _cfg_settings.rag_iterative_max_tool_calls
        ))
        _tool_chars_per_file = max(4000, min(200000, int(
            cfg.get("rag_iterative_tool_chars_per_file") if cfg.get("rag_iterative_tool_chars_per_file") is not None
            else _cfg_settings.rag_iterative_tool_chars_per_file
        )))
        _catalog_max_chars = max(5000, min(60000, int(
            cfg.get("rag_iterative_catalog_chars") if cfg.get("rag_iterative_catalog_chars") is not None
            else getattr(_cfg_settings, "rag_iterative_catalog_chars", 20000)
        )))
        _summarize_reads_cfg = cfg.get("rag_iterative_summarize_reads")
        _summarize_reads = (
            str(_summarize_reads_cfg).lower() not in {"false", "0", "off", "no", ""}
            if _summarize_reads_cfg is not None
            else bool(getattr(_cfg_settings, "rag_iterative_summarize_reads", False))
        )
        _summary_chars = max(200, min(2000, int(
            cfg.get("rag_iterative_summary_chars") if cfg.get("rag_iterative_summary_chars") is not None
            else getattr(_cfg_settings, "rag_iterative_summary_chars", 600)
        )))
        _initial_min_files = max(0, min(5, int(
            cfg.get("rag_iterative_initial_min_files") if cfg.get("rag_iterative_initial_min_files") is not None
            else getattr(_cfg_settings, "rag_iterative_initial_min_files", 3)
        )))
        _initial_max_files = max(_initial_min_files, min(16, int(
            cfg.get("rag_iterative_initial_max_files") if cfg.get("rag_iterative_initial_max_files") is not None
            else getattr(_cfg_settings, "rag_iterative_initial_max_files", 8)
        )))
        _symbol_max_snippets = max(0, min(24, int(
            cfg.get("rag_iterative_symbol_context_max_snippets") if cfg.get("rag_iterative_symbol_context_max_snippets") is not None
            else getattr(_cfg_settings, "rag_iterative_symbol_context_max_snippets", 8)
        )))
        _symbol_max_lines = max(5, min(160, int(
            cfg.get("rag_iterative_symbol_context_max_lines") if cfg.get("rag_iterative_symbol_context_max_lines") is not None
            else getattr(_cfg_settings, "rag_iterative_symbol_context_max_lines", 80)
        )))

        # Load CodeCompass component catalog as codebase overview
        _catalog_section = ""
        _catalog_path = repo_root / "rag-helper" / "out" / "component-catalog.md"
        if _catalog_path.exists():
            _catalog_text = _catalog_path.read_text(encoding="utf-8", errors="replace")
            _truncated = len(_catalog_text) > _catalog_max_chars
            _catalog_section = (
                "=== CodeCompass Codebase-Übersicht ===\n"
                + _catalog_text[:_catalog_max_chars]
                + ("\n[... abgeschnitten nach {:,} Zeichen ...]\n".format(_catalog_max_chars) if _truncated else "\n")
            )

        _context_budget_chars = max_input_tokens * 4
        _history_chars = sum(len(str(m.get("content") or "")) for m in llm_history)
        _reserved_chars = (
            _history_chars
            + len(question)
            + len(budget_instruction)
            + len(_catalog_section)
            + 6000  # file list, instructions, message framing
            + max(4000, int(_context_budget_chars * 0.10))
        )
        _symbol_snippets = build_codecompass_symbol_context(
            repo_root=repo_root,
            query=question,
            ranked_sources=chunks,
            max_snippets=_symbol_max_snippets,
            max_lines_per_snippet=_symbol_max_lines,
        )
        _symbol_context_section = format_symbol_context_section(_symbol_snippets)
        _pack_min_files = 0 if _symbol_snippets else _initial_min_files
        _pack_max_files = 0 if _symbol_snippets else _initial_max_files
        _context_pack = build_rag_context_pack(
            chunks=chunks,
            repo_root=repo_root,
            context_budget_chars=_context_budget_chars,
            reserved_chars=_reserved_chars + len(_symbol_context_section),
            max_chars_per_file=_tool_chars_per_file,
            min_initial_files=_pack_min_files,
            max_initial_files=_pack_max_files,
        )
        _packed_files_section = format_packed_files_section(_context_pack)

        # Build scored file list; files already packed into the prompt are marked as read.
        _packed_paths = set(_context_pack.included_paths)
        _file_list_lines = [
            "{:3d}. {}  (relevanz: {:.1f}{})".format(
                i,
                ch["source"],
                ch.get("score", 0),
                ", bereits im Initialkontext" if ch["source"] in _packed_paths else "",
            )
            for i, ch in enumerate(chunks, 1)
        ]
        _file_list_section = (
            "=== Verfügbare Dateien ({} gefunden, nach Relevanz) ===\n".format(len(chunks))
            + "\n".join(_file_list_lines)
        )

        user_message = (
            "Frage: {}\n\n".format(question)
            + ("{}\n\n".format(budget_instruction) if budget_instruction else "")
            + _catalog_section
            + "\n"
            + (_symbol_context_section + "\n\n" if _symbol_context_section else "")
            + (_packed_files_section + "\n\n" if _packed_files_section else "")
            + _file_list_section
            + "\n\n"
            "Anweisung:\n"
            "1. Nutze zuerst den CodeCompass Symbol-/Graph-Kontext; er ist präziser als ganze Dateien.\n"
            "2. Die als 'bereits im Initialkontext' markierten Top-Treffer gelten als gelesen.\n"
            "3. Nutze EXAKT die Pfade wie in der Dateiliste angegeben (z.B. 'worker/retrieval/...' nicht 'agent/services/...').\n"
            "4. Wenn eine Datei nicht gefunden wird: Nutze den im Fehler angezeigten korrekten Pfad, "
            "oder versuche die nächste Datei aus der Liste — gib NICHT auf.\n"
            "5. Nutze search_codebase() NUR für Dateien die NICHT in der Liste stehen.\n"
            "6. Jede Folgeaktion muss an den bisherigen Recherche-Stand anschließen."
        )

        available_files = [ch["source"] for ch in chunks]
        messages = list(llm_history) + [{"role": "user", "content": user_message}]

        if rec:
            rec.event(
                "rag_iterative_tool_loop_start",
                "Tool-Loop: {:,} Zeichen Katalog + {} Dateien verfügbar".format(
                    len(_catalog_section), len(chunks)
                ),
                status="running",
                details={
                    "available_files": available_files,
                    "initial_context_files": _context_pack.included_paths,
                    "symbol_context_refs": [
                        {
                            "path": item.path,
                            "symbol": item.symbol,
                            "kind": item.kind,
                            "line_start": item.line_start,
                            "line_end": item.line_end,
                            "source": item.source,
                        }
                        for item in _symbol_snippets
                    ],
                    "initial_context_file_budget_chars": _context_pack.file_budget_chars,
                    "initial_context_used_file_chars": _context_pack.used_file_chars,
                    "initial_context_reserved_chars": _context_pack.reserved_chars,
                    "catalog_chars": len(_catalog_section),
                    "catalog_loaded": _catalog_path.exists(),
                    "max_tool_calls": max_tool_calls,
                    "model": model,
                    "provider": provider,
                    "summarize_reads": _summarize_reads,
                    "initial_summary_mode": (
                        "skipped_symbol_context_primary"
                        if _symbol_snippets
                        else "enabled" if _summarize_reads and _context_pack.included_files else "not_applicable"
                    ),
                },
            )

        final_answer, tl_trace = run_rag_chat_tool_loop(
            messages=messages,
            provider=provider,
            model=model,
            repo_root=repo_root,
            max_tool_calls=max_tool_calls,
            max_chars_per_file=_tool_chars_per_file,
            timeout=timeout_s,
            rec=rec,
            initial_files=available_files,
            question=question,
            summarize_reads=_summarize_reads,
            max_summary_chars=_summary_chars,
            initial_evidence=[
                {
                    "path": item.path,
                    "summary": packed_file_memory_summary(item, max_chars=_summary_chars),
                    "content": item.content,
                    "chars": item.chars_included,
                    "score": item.score,
                    "source": "initial_context",
                }
                for item in _context_pack.included_files
            ],
            cancel_event=cancel_event,
        )
        trace["tool_loop"] = tl_trace
        trace["available_files"] = available_files
        trace["initial_context_files"] = _context_pack.included_paths
        trace["symbol_context_refs"] = [
            {
                "path": item.path,
                "symbol": item.symbol,
                "kind": item.kind,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "source": item.source,
                "relation": item.relation,
            }
            for item in _symbol_snippets
        ]
        trace["summarize_reads"] = _summarize_reads
        trace["initial_summary_mode"] = (
            "skipped_symbol_context_primary"
            if _symbol_snippets
            else "enabled" if _summarize_reads and _context_pack.included_files else "not_applicable"
        )
        trace["initial_context_file_budget_chars"] = _context_pack.file_budget_chars
        trace["initial_context_used_file_chars"] = _context_pack.used_file_chars
        trace["catalog_chars"] = len(_catalog_section)
        if rec:
            rec.event(
                "rag_iterative_tool_loop_done",
                "Tool-Loop abgeschlossen ({} Tool-Calls)".format(tl_trace.get("tool_calls_made", 0)),
                status="completed" if final_answer else "failed",
                details=tl_trace,
                output_preview=final_answer[:500] if final_answer else None,
            )
        return final_answer, trace

    # --- Batch mode: resolve and read all file contents upfront ---
    file_entries: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    for ch in chunks:
        if _cancelled():
            return "", trace
        meta = dict((ch or {}).get("metadata") or {})
        source = str(meta.get("file_path") or meta.get("path") or ch.get("source") or "").strip()
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)

        candidate = _pl.Path(source) if source.startswith("/") else repo_root / source
        if not candidate.exists() or not candidate.is_file():
            if source.startswith("/app/"):
                candidate = repo_root / source[5:]
            if not candidate.exists() or not candidate.is_file():
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

    trace["files_resolved"] = len(file_entries)
    if not file_entries:
        trace["error"] = "no_files_resolved"
        return "", trace

    # --- Step 2b: Python import-graph expansion ---
    import_depth = max(0, int(
        cfg.get("rag_iterative_import_depth") if cfg.get("rag_iterative_import_depth") is not None
        else _cfg_settings.rag_iterative_import_depth
    ))
    if import_depth > 0:
        before = len(file_entries)
        file_entries = _expand_python_imports(
            file_entries, repo_root,
            depth=import_depth, max_chars_per_file=max_chars_per_file, seen_sources=seen_sources,
        )
        trace["import_expansion_added"] = len(file_entries) - before
        trace["files_after_expansion"] = len(file_entries)

    # --- Step 2c: Symbol-graph expansion ---
    _sym_max = max(0, int(
        cfg.get("rag_iterative_symbol_expand_max") if cfg.get("rag_iterative_symbol_expand_max") is not None
        else _cfg_settings.rag_iterative_symbol_expand_max
    ))
    if _sym_max > 0 and _engine is not None:
        _before_sym = len(file_entries)
        try:
            file_entries = _expand_via_symbol_graph(
                file_entries, _engine, repo_root,
                max_extra=_sym_max, seen_sources=seen_sources, max_chars_per_file=max_chars_per_file,
            )
            trace["symbol_expansion_added"] = len(file_entries) - _before_sym
            trace["files_after_symbol_expansion"] = len(file_entries)
        except Exception as _sym_exc:
            _log.debug("symbol_graph_expand failed: %s", _sym_exc)
            trace["symbol_expansion_error"] = str(_sym_exc)

    # --- Step 3: Estimate tokens and split into batches ---
    framing_overhead = 200  # system prompt + question + batch header
    chars_per_token = 4

    def _estimate_tokens(entries: list[dict]) -> int:
        total_chars = framing_overhead + sum(
            len(e["content"]) + len(e["path"]) + 20 for e in entries
        )
        return max(1, total_chars // chars_per_token)

    # Greedy batching: fill each batch up to max_input_tokens
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    for entry in file_entries:
        test = current_batch + [entry]
        if current_batch and _estimate_tokens(test) > max_input_tokens:
            batches.append(current_batch)
            current_batch = [entry]
        else:
            current_batch = test
    if current_batch:
        batches.append(current_batch)

    trace["batches_planned"] = len(batches)
    trace["files_per_batch"] = [len(b) for b in batches]
    trace["file_list"] = [e["path"] for e in file_entries]

    if rec:
        rec.event(
            "rag_iterative_plan",
            f"RAG-Iterativ: {len(file_entries)} Dateien, {len(batches)} Batch(es) geplant",
            status="running",
            details={
                "files": [e["path"] for e in file_entries],
                "batches_planned": len(batches),
                "files_per_batch": [len(b) for b in batches],
                "model_context_tokens": model_context_tokens,
                "max_input_tokens": max_input_tokens,
            },
        )

    # --- Step 4: Process each batch ---
    batch_summaries: list[str] = []
    batch_metas: list[dict] = []

    for i, batch in enumerate(batches, start=1):
        if _cancelled():
            return "", trace
        file_blocks = []
        for e in batch:
            file_blocks.append(f"### {e['path']}\n```{e['lang']}\n{e['content']}\n```")

        batch_prompt = (
            f"Frage: {question}\n\n"
            + (f"{budget_instruction}\n\n" if budget_instruction else "")
            + f"Analysiere die folgenden Dateien (Batch {i}/{len(batches)}):\n\n"
            + "\n\n".join(file_blocks)
            + "\n\nExtrahiere alle relevanten Informationen zur Frage aus diesen Dateien. Präzise Zusammenfassung."
        )

        est_tokens = _estimate_tokens(batch)
        _log.debug("rag_iterative batch %d/%d: %d files, ~%d tokens", i, len(batches), len(batch), est_tokens)

        file_paths_in_batch = [e["path"] for e in batch]
        if rec:
            rec.event(
                f"rag_iterative_batch_{i}",
                f"Batch {i}/{len(batches)}: {len(batch)} Datei(en) → LLM",
                status="running",
                details={
                    "batch": i,
                    "total_batches": len(batches),
                    "files": file_paths_in_batch,
                    "estimated_input_tokens": est_tokens,
                    "model": model,
                    "provider": provider,
                },
                input_preview=batch_prompt,
            )

        t_batch = _time()
        try:
            raw = generate_text(
                prompt=batch_prompt,
                provider=provider,
                model=model,
                history=llm_history,
                timeout=timeout_s,
            )
            if _cancelled():
                return "", trace
            text = str(raw or "").strip()
        except Exception as exc:
            _log.warning("rag_iterative batch %d failed: %s", i, exc)
            text = ""

        batch_ms = (_time() - t_batch) * 1000
        file_labels = ", ".join(file_paths_in_batch)
        batch_meta = {
            "batch": i,
            "files": file_labels,
            "estimated_input_tokens": est_tokens,
            "answer_chars": len(text),
        }
        batch_metas.append(batch_meta)

        if rec:
            rec.event(
                f"rag_iterative_batch_{i}_done",
                f"Batch {i}/{len(batches)} abgeschlossen",
                status="completed" if text else "failed",
                summary=f"{len(text)} Zeichen Antwort" if text else "Keine Antwort erhalten",
                duration_ms=batch_ms,
                details={**batch_meta, "files_list": file_paths_in_batch},
                output_preview=text if text else None,
            )

        if text:
            batch_summaries.append(f"**Batch {i}** [{file_labels}]:\n{text}")

    trace["batches_completed"] = len(batch_summaries)
    trace["batch_metas"] = batch_metas

    if not batch_summaries:
        trace["error"] = "all_batches_empty"
        return "", trace

    # If only one batch, no synthesis needed
    if len(batch_summaries) == 1:
        final_answer = batch_summaries[0].split("\n", 2)[-1].strip()
        trace["synthesis"] = "skipped_single_batch"
        return final_answer, trace

    # --- Step 5: Synthesis ---
    combined = "\n\n---\n\n".join(batch_summaries)
    synthesis_prompt = (
        f"Ursprüngliche Frage: {question}\n\n"
        + (f"{budget_instruction}\n\n" if budget_instruction else "")
        + f"Analyse der relevanten Dateien aus {len(batch_summaries)} Batches:\n\n"
        + combined
        + "\n\nErstelle eine vollständige, strukturierte Antwort auf Basis dieser Analyse."
    )

    if rec:
        rec.event(
            "rag_iterative_synthesis",
            f"Synthese aus {len(batch_summaries)} Batch-Antworten",
            status="running",
            input_preview=synthesis_prompt,
        )

    t_syn = _time()
    try:
        if _cancelled():
            return "", trace
        raw = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=llm_history,
            timeout=timeout_s,
        )
        if _cancelled():
            return "", trace
        final_answer = str(raw or "").strip()
    except Exception as exc:
        _log.warning("rag_iterative synthesis failed: %s", exc)
        final_answer = "\n\n".join(
            s.split("\n", 2)[-1].strip() for s in batch_summaries
        )
        trace["synthesis_error"] = str(exc)

    if rec:
        rec.event(
            "rag_iterative_synthesis_done",
            "Synthese abgeschlossen",
            status="completed" if final_answer else "failed",
            duration_ms=(_time() - t_syn) * 1000,
            output_preview=final_answer if final_answer else None,
        )

    trace["synthesis"] = "done"
    return final_answer, trace
