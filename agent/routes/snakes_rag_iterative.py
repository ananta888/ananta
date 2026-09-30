"""RAG-Iterative: retrieve relevant files via CodeCompass, read them fully,
batch them to fit the LLM context window, process each batch independently,
then synthesize all intermediate answers into a final response.

This sits between the single-shot RAG path (all chunks truncated, one LLM call)
and the full_scan path (entire repo scanned). Here only the files that RAG
identified as relevant are used, but read at full length and processed
iteratively when they exceed the context budget.

Module layout: file selection/batching lives in
:mod:`.snakes_rag_iterative_files`, the tool-call mode prompt in
:mod:`.snakes_rag_iterative_tool_mode`. The LLM calls, the config and the
settings object stay looked up on this module (existing test seams).
"""
from __future__ import annotations

import logging
import pathlib as _pl
import re
from dataclasses import dataclass
from time import time as _time
from typing import Any

from agent.config import lookup_model_context_tokens
from agent.config import settings as _cfg_settings
from agent.llm_integration import generate_text
from agent.routes.ai_snake_config import _current_config
from agent.routes.snakes_rag_iterative_files import (  # noqa: F401  (_filter_catalog_* re-exported)
    _expand_python_imports,
    _expand_via_symbol_graph,
    _filter_catalog_to_relevant_modules,
    estimate_batch_tokens,
    filter_initial_chunks,
    plan_batches,
    resolve_batch_file_entries,
)
from agent.routes.snakes_rag_iterative_tool_mode import (
    ToolModeSettings,
    build_tool_mode_prompt,
    catalog_section,
    initial_evidence,
    initial_summary_mode,
    symbol_context_refs,
    tool_calls_enabled,
    tool_loop_done_title,
)
from agent.services.snake_chat_cancellation import is_chat_cancelled

_log = logging.getLogger(__name__)


def _window_chars(value: Any, *, window_tokens: int, share: str, legacy: str, minimum: int) -> int:
    """A snake-RAG character budget from the model window (central policy; 32k keeps the former 20000 chars).
    A configured value other than the stored default still applies, capped by the window's room for material."""
    from agent.context_profile import CHARS_PER_TOKEN, ContextBudgets, budget_override

    budgets = ContextBudgets(int(window_tokens))
    explicit = budget_override(value, legacy=legacy)
    if explicit is None:
        return max(minimum, budgets.chars(share))
    return max(minimum, min(explicit, budgets.available * CHARS_PER_TOKEN))

_SYSTEM_PROMPT = (
    "Du bist ein Code-Assistent fuer Quellcode-Fragen.\n"
    "Regeln (streng):\n"
    "1) Antworte nur auf Basis des bereitgestellten Kontexts und der Nutzerfrage.\n"
    "2) Erfinde keine Produkte, URLs, Features, Befehle oder Fakten.\n"
    "3) Wenn Informationen fehlen oder unsicher sind, sage explizit: "
    "\"Unklar, bitte Kontext pruefen\".\n"
    "4) Gib keine externen Links aus, ausser der Nutzer hat explizit danach gefragt.\n"
    "5) Halte Antworten kurz, konkret, technisch nutzbar.\n"
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


def _build_followup_retrieval_query(
    question: str,
    conversation_history: list[dict[str, str]] | None,
    *,
    max_history_chars: int = 2400,
) -> str:
    """Build a retrieval-only query that preserves follow-up context."""
    current = _retrieval_user_question(question)
    history = list(conversation_history or [])
    if not current or not history:
        return current
    parts = [current]
    used = 0
    for msg in reversed(history[-4:]):
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        remaining = max_history_chars - used
        if remaining <= 0:
            break
        snippet = content[:remaining]
        used += len(snippet)
        parts.append(snippet)
    return "\n\n".join(parts)


def _retrieval_user_question(question: str) -> str:
    """Remove presentation-only UI hints from repository retrieval text."""

    current = str(question or "").strip()
    return re.sub(
        r"^\[UI-Kontext:[^\n]*\]\s*",
        "",
        current,
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _is_codecompass_overview_question(question: str) -> bool:
    normalized = _retrieval_user_question(question).lower()
    if "codecompass" not in normalized:
        return False
    specific_terms = {
        "x86", "malware", "migration", "fehler", "bug", "funktion",
        "klasse", "datei", "symbol", "timeout", "test",
    }
    return not any(term in normalized for term in specific_terms)


@dataclass
class _RagIterativeRun:
    """Inputs and shared state of one rag_iterative answer."""

    question: str
    retrieval_question: str
    provider: str
    model: str | None
    api_base: str | None
    rec: Any | None
    cancel_event: Any | None
    llm_history: list[dict[str, str]]
    budget_instruction: str
    timeout_s: int
    max_chars_per_file: int
    model_context_tokens: int
    max_input_tokens: int
    cfg: dict[str, Any]
    repo_root: _pl.Path
    trace: dict[str, Any]

    def cancelled(self) -> bool:
        if not is_chat_cancelled(self.cancel_event):
            return False
        self.trace["cancelled"] = True
        self.trace["error"] = "cancelled"
        return True

    def event(self, *args, **kwargs) -> None:
        if self.rec:
            self.rec.event(*args, **kwargs)

    def config_int(self, key: str) -> int:
        value = self.cfg.get(key)
        return max(0, int(value if value is not None else getattr(_cfg_settings, key)))


def _retrieve_chunks(run: _RagIterativeRun) -> tuple[list[dict[str, Any]], Any] | None:
    """RAG retrieval via a live RepositoryMapEngine scan; ``None`` ends the run."""
    rag_max = int(_cfg_settings.rag_max_chunks or 40)
    try:
        from agent.hybrid_orchestrator import RepositoryMapEngine

        if run.cancelled():
            return None
        engine = RepositoryMapEngine(run.repo_root)
        raw_chunks = engine.search(run.retrieval_question, top_k=rag_max)
        chunks = [
            {
                "source": ch.source,
                "metadata": {"file_path": ch.source, **dict(getattr(ch, "metadata", None) or {})},
                "score": ch.score,
            }
            for ch in raw_chunks
        ]
        run.trace["retrieval_top_scores"] = [
            {"source": ch.source, "score": round(ch.score, 2)} for ch in raw_chunks[:10]
        ]
        ranking_trace = getattr(engine, "ranking_trace", None)
        if callable(ranking_trace):
            run.trace["source_ranking"] = ranking_trace()
    except Exception as exc:
        _log.warning("rag_iterative: retrieval failed: %s", exc)
        run.trace["error"] = f"retrieval_failed: {exc}"
        return None
    return chunks, engine


def _answer_with_tool_loop(
    run: _RagIterativeRun,
    chunks: list[dict[str, Any]],
    *,
    max_tool_calls_override: int | None,
    max_search_calls_override: int | None,
    final_task_kind: str,
) -> tuple[str, dict[str, Any]]:
    """Tool-call mode: send catalog overview + ranked file list; the LLM loads what it needs."""
    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop
    from agent.services.snake_codecompass_architecture_context_service import (
        get_snake_codecompass_architecture_context_service,
    )

    trace = run.trace
    mode = ToolModeSettings.resolve(
        run.cfg,
        _cfg_settings,
        window_tokens=run.model_context_tokens,
        window_chars=_window_chars,
        max_tool_calls_override=max_tool_calls_override,
        max_search_calls_override=max_search_calls_override,
    )
    catalog_path = run.repo_root / "rag-helper" / "out" / "component-catalog.md"
    catalog = catalog_section(catalog_path, chunks, mode.catalog_max_chars, _filter_catalog_to_relevant_modules)
    architecture_section, architecture_trace = get_snake_codecompass_architecture_context_service().build(
        run.retrieval_question,
        max_chars=min(8000, max(3000, mode.catalog_max_chars // 2)),
    )
    context_budget_chars = run.max_input_tokens * 4
    reserved_chars = (
        sum(len(str(m.get("content") or "")) for m in run.llm_history)
        + len(run.question)
        + len(run.budget_instruction)
        + len(catalog)
        + len(architecture_section)
        + 6000  # file list, instructions, message framing
        + max(4000, int(context_budget_chars * 0.10))
    )
    prompt = build_tool_mode_prompt(
        question=run.question,
        retrieval_question=run.retrieval_question,
        effective_question=_retrieval_user_question(run.question),
        budget_instruction=run.budget_instruction,
        catalog=catalog,
        architecture_section=architecture_section,
        architecture_trace=architecture_trace,
        chunks=chunks,
        repo_root=run.repo_root,
        reserved_chars=reserved_chars,
        context_budget_chars=context_budget_chars,
        settings=mode,
        overview_question=_is_codecompass_overview_question(run.retrieval_question),
    )
    available_files = [ch["source"] for ch in chunks]
    pack = prompt.context_pack
    summary_mode = initial_summary_mode(prompt.symbol_snippets, mode.summarize_reads, pack)
    run.event(
        "rag_iterative_tool_loop_start",
        "Tool-Loop: {:,} Zeichen Katalog + {} Dateien verfügbar".format(len(catalog), len(chunks)),
        status="running",
        details={
            "available_files": available_files,
            "initial_context_files": pack.included_paths,
            "symbol_context_refs": symbol_context_refs(prompt.symbol_snippets, with_relation=False),
            "initial_context_file_budget_chars": pack.file_budget_chars,
            "initial_context_used_file_chars": pack.used_file_chars,
            "initial_context_reserved_chars": pack.reserved_chars,
            "catalog_chars": len(catalog),
            "catalog_loaded": catalog_path.exists(),
            "architecture_context": architecture_trace,
            "max_tool_calls": mode.max_tool_calls,
            "model": run.model,
            "provider": run.provider,
            "summarize_reads": mode.summarize_reads,
            "initial_summary_mode": summary_mode,
        },
    )
    final_answer, tool_loop_trace = run_rag_chat_tool_loop(
        messages=list(run.llm_history) + [{"role": "user", "content": prompt.user_message}],
        provider=run.provider,
        model=run.model,
        api_base=run.api_base,
        repo_root=run.repo_root,
        max_tool_calls=mode.max_tool_calls,
        max_search_calls=mode.max_search_calls,
        max_chars_per_file=mode.tool_chars_per_file,
        config_provider=_current_config,
        timeout=run.timeout_s,
        rec=run.rec,
        initial_files=available_files,
        question=prompt.effective_question,
        architecture_context=architecture_section,
        summarize_reads=mode.summarize_reads,
        max_summary_chars=mode.summary_chars,
        initial_evidence=initial_evidence(pack, mode.summary_chars),
        cancel_event=run.cancel_event,
        final_task_kind=final_task_kind,
        lock_tool_budgets=(max_tool_calls_override is not None or max_search_calls_override is not None),
    )
    trace["tool_loop"] = tool_loop_trace
    trace["available_files"] = available_files
    trace["initial_context_files"] = pack.included_paths
    trace["symbol_context_refs"] = symbol_context_refs(prompt.symbol_snippets, with_relation=True)
    trace["summarize_reads"] = mode.summarize_reads
    trace["initial_summary_mode"] = summary_mode
    trace["initial_context_file_budget_chars"] = pack.file_budget_chars
    trace["initial_context_used_file_chars"] = pack.used_file_chars
    trace["catalog_chars"] = len(catalog)
    trace["architecture_context"] = architecture_trace
    run.event(
        "rag_iterative_tool_loop_done",
        tool_loop_done_title(tool_loop_trace),
        status="completed" if final_answer else "failed",
        details=tool_loop_trace,
        output_preview=final_answer[:500] if final_answer else None,
    )
    return final_answer, trace


def _expand_file_entries(
    run: _RagIterativeRun,
    file_entries: list[dict[str, Any]],
    engine: Any,
    seen_sources: set[str],
) -> list[dict[str, Any]]:
    """Python import-graph expansion, then symbol-graph expansion."""
    trace = run.trace
    import_depth = run.config_int("rag_iterative_import_depth")
    if import_depth > 0:
        before = len(file_entries)
        file_entries = _expand_python_imports(
            file_entries, run.repo_root,
            depth=import_depth, max_chars_per_file=run.max_chars_per_file, seen_sources=seen_sources,
        )
        trace["import_expansion_added"] = len(file_entries) - before
        trace["files_after_expansion"] = len(file_entries)
    symbol_max = run.config_int("rag_iterative_symbol_expand_max")
    if symbol_max > 0 and engine is not None:
        before_symbols = len(file_entries)
        try:
            file_entries = _expand_via_symbol_graph(
                file_entries, engine, run.repo_root,
                max_extra=symbol_max, seen_sources=seen_sources, max_chars_per_file=run.max_chars_per_file,
            )
            trace["symbol_expansion_added"] = len(file_entries) - before_symbols
            trace["files_after_symbol_expansion"] = len(file_entries)
        except Exception as _sym_exc:
            _log.debug("symbol_graph_expand failed: %s", _sym_exc)
            trace["symbol_expansion_error"] = str(_sym_exc)
    return file_entries


def _analyze_batch(
    run: _RagIterativeRun, index: int, batch: list[dict], total: int
) -> tuple[str, str, dict[str, Any]] | None:
    """Ask the model about one batch; returns ``(file_labels, text, meta)`` or ``None`` when cancelled."""
    file_blocks = [f"### {e['path']}\n```{e['lang']}\n{e['content']}\n```" for e in batch]
    batch_prompt = (
        f"Frage: {run.question}\n\n"
        + (f"{run.budget_instruction}\n\n" if run.budget_instruction else "")
        + f"Analysiere die folgenden Dateien (Batch {index}/{total}):\n\n"
        + "\n\n".join(file_blocks)
        + "\n\nExtrahiere alle relevanten Informationen zur Frage aus diesen Dateien. Präzise Zusammenfassung."
    )
    est_tokens = estimate_batch_tokens(batch)
    _log.debug("rag_iterative batch %d/%d: %d files, ~%d tokens", index, total, len(batch), est_tokens)
    file_paths = [e["path"] for e in batch]
    run.event(
        f"rag_iterative_batch_{index}",
        f"Batch {index}/{total}: {len(batch)} Datei(en) → LLM",
        status="running",
        details={
            "batch": index,
            "total_batches": total,
            "files": file_paths,
            "estimated_input_tokens": est_tokens,
            "model": run.model,
            "provider": run.provider,
        },
        input_preview=batch_prompt,
    )
    started = _time()
    try:
        raw = generate_text(
            prompt=batch_prompt,
            provider=run.provider,
            model=run.model,
            history=run.llm_history,
            timeout=run.timeout_s,
        )
        if run.cancelled():
            return None
        text = str(raw or "").strip()
    except Exception as exc:
        _log.warning("rag_iterative batch %d failed: %s", index, exc)
        text = ""
    file_labels = ", ".join(file_paths)
    batch_meta = {"batch": index, "files": file_labels, "estimated_input_tokens": est_tokens, "answer_chars": len(text)}
    run.event(
        f"rag_iterative_batch_{index}_done",
        f"Batch {index}/{total} abgeschlossen",
        status="completed" if text else "failed",
        summary=f"{len(text)} Zeichen Antwort" if text else "Keine Antwort erhalten",
        duration_ms=(_time() - started) * 1000,
        details={**batch_meta, "files_list": file_paths},
        output_preview=text if text else None,
    )
    return file_labels, text, batch_meta


def _synthesize_batches(run: _RagIterativeRun, batch_summaries: list[str]) -> str | None:
    """Combine the batch answers; ``None`` when cancelled."""
    synthesis_prompt = (
        f"Ursprüngliche Frage: {run.question}\n\n"
        + (f"{run.budget_instruction}\n\n" if run.budget_instruction else "")
        + f"Analyse der relevanten Dateien aus {len(batch_summaries)} Batches:\n\n"
        + "\n\n---\n\n".join(batch_summaries)
        + "\n\nErstelle eine vollständige, strukturierte Antwort auf Basis dieser Analyse."
    )
    run.event(
        "rag_iterative_synthesis",
        f"Synthese aus {len(batch_summaries)} Batch-Antworten",
        status="running",
        input_preview=synthesis_prompt,
    )
    started = _time()
    try:
        if run.cancelled():
            return None
        raw = generate_text(
            prompt=synthesis_prompt,
            provider=run.provider,
            model=run.model,
            history=run.llm_history,
            timeout=run.timeout_s,
        )
        if run.cancelled():
            return None
        final_answer = str(raw or "").strip()
    except Exception as exc:
        _log.warning("rag_iterative synthesis failed: %s", exc)
        final_answer = "\n\n".join(s.split("\n", 2)[-1].strip() for s in batch_summaries)
        run.trace["synthesis_error"] = str(exc)
    run.event(
        "rag_iterative_synthesis_done",
        "Synthese abgeschlossen",
        status="completed" if final_answer else "failed",
        duration_ms=(_time() - started) * 1000,
        output_preview=final_answer if final_answer else None,
    )
    return final_answer


def _answer_with_batches(
    run: _RagIterativeRun, chunks: list[dict[str, Any]], engine: Any
) -> tuple[str, dict[str, Any]]:
    """Batch mode: read all files upfront, analyze token-bounded batches, then synthesize."""
    trace = run.trace
    seen_sources: set[str] = set()
    file_entries = resolve_batch_file_entries(
        chunks,
        run.repo_root,
        max_chars_per_file=run.max_chars_per_file,
        seen_sources=seen_sources,
        trace=trace,
        cancelled=run.cancelled,
    )
    if file_entries is None:
        return "", trace
    trace["files_resolved"] = len(file_entries)
    if not file_entries:
        trace["error"] = "no_files_resolved"
        return "", trace
    file_entries = _expand_file_entries(run, file_entries, engine, seen_sources)
    batches = plan_batches(file_entries, run.max_input_tokens)
    trace["batches_planned"] = len(batches)
    trace["files_per_batch"] = [len(b) for b in batches]
    trace["file_list"] = [e["path"] for e in file_entries]
    run.event(
        "rag_iterative_plan",
        f"RAG-Iterativ: {len(file_entries)} Dateien, {len(batches)} Batch(es) geplant",
        status="running",
        details={
            "files": [e["path"] for e in file_entries],
            "batches_planned": len(batches),
            "files_per_batch": [len(b) for b in batches],
            "model_context_tokens": run.model_context_tokens,
            "max_input_tokens": run.max_input_tokens,
        },
    )
    batch_summaries: list[str] = []
    batch_metas: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, start=1):
        if run.cancelled():
            return "", trace
        analyzed = _analyze_batch(run, index, batch, len(batches))
        if analyzed is None:
            return "", trace
        file_labels, text, batch_meta = analyzed
        batch_metas.append(batch_meta)
        if text:
            batch_summaries.append(f"**Batch {index}** [{file_labels}]:\n{text}")
    trace["batches_completed"] = len(batch_summaries)
    trace["batch_metas"] = batch_metas
    if not batch_summaries:
        trace["error"] = "all_batches_empty"
        return "", trace
    # If only one batch, no synthesis needed
    if len(batch_summaries) == 1:
        trace["synthesis"] = "skipped_single_batch"
        return batch_summaries[0].split("\n", 2)[-1].strip(), trace
    final_answer = _synthesize_batches(run, batch_summaries)
    if final_answer is None:
        return "", trace
    trace["synthesis"] = "done"
    return final_answer, trace


def worker_chat_rag_iterative(
    question: str,
    *,
    provider: str = "lmstudio",
    model: str | None = None,
    api_base: str | None = None,
    limits: Any | None = None,
    rec: Any | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    cancel_event: Any | None = None,
    system_prompt: str | None = None,
    max_tool_calls_override: int | None = None,
    max_search_calls_override: int | None = None,
    final_task_kind: str = "repo_analysis",
) -> tuple[str, dict[str, Any]]:
    """Iterative RAG: fetch relevant files, read fully, batch → LLM → synthesize."""
    trace: dict[str, Any] = {"mode": "rag_iterative"}
    effective_system = (system_prompt.strip() if system_prompt and system_prompt.strip() else None) or _SYSTEM_PROMPT
    trace["conversation_history_messages"] = len(conversation_history or [])
    retrieval_question = _build_followup_retrieval_query(question, conversation_history)
    trace["retrieval_query_includes_history"] = retrieval_question != str(question or "").strip()

    cfg = _current_config()
    budget_instruction = _answer_budget_instruction(limits)

    from agent.context_profile import effective_window_tokens

    model_context_tokens = effective_window_tokens(limits={"model": lookup_model_context_tokens(model)},
                                                   probe=False)
    # Reserve ~25% of the context for the LLM's output + framing
    max_input_tokens = max(256, int(model_context_tokens * 0.75))
    trace["model_context_tokens"] = model_context_tokens
    trace["max_input_tokens"] = max_input_tokens
    run = _RagIterativeRun(
        question=question,
        retrieval_question=retrieval_question,
        provider=provider,
        model=model,
        api_base=api_base,
        rec=rec,
        cancel_event=cancel_event,
        llm_history=[{"role": "system", "content": effective_system}, *list(conversation_history or [])],
        budget_instruction=budget_instruction,
        timeout_s=max(60, min(7200, int(float(cfg.get("chat_ask_timeout_s") or 180)))),
        max_chars_per_file=max(1000, min(20000, int(float(cfg.get("chat_full_scan_chars_per_file") or 4000)))),
        model_context_tokens=model_context_tokens,
        max_input_tokens=max_input_tokens,
        cfg=cfg,
        repo_root=_pl.Path(getattr(_cfg_settings, "rag_repo_root", ".")).resolve(),
        trace=trace,
    )

    retrieved = _retrieve_chunks(run)
    if retrieved is None or run.cancelled():
        return "", trace
    chunks, engine = retrieved
    chunks = filter_initial_chunks(chunks)
    trace["rag_chunks_found"] = len(chunks)
    if not chunks:
        trace["error"] = "no_rag_chunks"
        return "", trace

    if tool_calls_enabled(cfg, _cfg_settings):
        return _answer_with_tool_loop(
            run,
            chunks,
            max_tool_calls_override=max_tool_calls_override,
            max_search_calls_override=max_search_calls_override,
            final_task_kind=final_task_kind,
        )
    return _answer_with_batches(run, chunks, engine)
