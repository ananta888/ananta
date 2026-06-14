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
) -> tuple[str, dict[str, Any]]:
    """Iterative RAG: fetch relevant files, read fully, batch → LLM → synthesize."""
    trace: dict[str, Any] = {"mode": "rag_iterative"}
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

    # --- Step 1: RAG retrieval to find relevant file sources ---
    try:
        from agent.services.rag_service import get_rag_service
        from agent.services.retrieval_profile_service import resolve_profile

        profile = resolve_profile(question, cfg, domain_hint=None, feature_flag=str(cfg.get("chat_retrieval_profile") or "auto"))
        bundle, _ = get_rag_service().build_execution_context(
            question,
            task_kind="research",
            retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
            source_types=profile.source_types or None,
            max_chunks=40,
            retrieval_profile=profile.as_dict(),
        )
        chunks = list(bundle.get("chunks") or [])
    except Exception as exc:
        _log.warning("rag_iterative: retrieval failed: %s", exc)
        trace["error"] = f"retrieval_failed: {exc}"
        return "", trace

    trace["rag_chunks_found"] = len(chunks)
    if not chunks:
        trace["error"] = "no_rag_chunks"
        return "", trace

    # --- Step 2: Resolve full file paths and read complete content ---
    repo_root = _pl.Path(getattr(_cfg_settings, "rag_repo_root", ".")).resolve()
    file_entries: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    for ch in chunks:
        meta = dict((ch or {}).get("metadata") or {})
        source = str(meta.get("file_path") or meta.get("path") or ch.get("source") or "").strip()
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)

        # Resolve absolute or repo-relative path
        candidate = _pl.Path(source) if source.startswith("/") else repo_root / source
        if not candidate.exists() or not candidate.is_file():
            # Try stripping /app prefix (Docker path)
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

    # --- Step 2b: Python import-graph expansion (optional) ---
    import_depth = max(0, int(
        cfg.get("rag_iterative_import_depth") if cfg.get("rag_iterative_import_depth") is not None
        else _cfg_settings.rag_iterative_import_depth
    ))
    if import_depth > 0:
        before = len(file_entries)
        file_entries = _expand_python_imports(
            file_entries,
            repo_root,
            depth=import_depth,
            max_chars_per_file=max_chars_per_file,
            seen_sources=seen_sources,
        )
        trace["import_expansion_added"] = len(file_entries) - before
        trace["files_after_expansion"] = len(file_entries)

    # --- Step 2c: Tool-call loop (optional, replaces batch+synthesis when enabled) ---
    _tc_enabled_cfg = cfg.get("rag_iterative_tool_calls_enabled")
    if _tc_enabled_cfg is None:
        tool_calls_enabled = bool(_cfg_settings.rag_iterative_tool_calls_enabled)
    else:
        tool_calls_enabled = str(_tc_enabled_cfg).lower() not in {"false", "0", "off", "no", ""}
    if tool_calls_enabled:
        from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

        max_tool_calls = max(0, int(
            cfg.get("rag_iterative_max_tool_calls") if cfg.get("rag_iterative_max_tool_calls") is not None
            else _cfg_settings.rag_iterative_max_tool_calls
        ))
        # Build context block from all resolved files (truncated to fit)
        file_blocks = []
        for e in file_entries:
            file_blocks.append(f"### {e['path']}\n```{e['lang']}\n{e['content']}\n```")
        context_text = "\n\n".join(file_blocks)

        user_message = (
            f"Frage: {question}\n\n"
            + (f"{budget_instruction}\n\n" if budget_instruction else "")
            + f"Initialer Kontext ({len(file_entries)} Datei(en) aus CodeCompass):\n\n"
            + context_text
            + "\n\nBeantworte die Frage. Falls du weitere Dateien benötigst, "
            + "nutze die verfügbaren Tools (read_file, search_codebase)."
        )

        messages = list(llm_history) + [{"role": "user", "content": user_message}]

        if rec:
            rec.event(
                "rag_iterative_tool_loop_start",
                f"Tool-Loop: {len(file_entries)} Datei(en), max {max_tool_calls} Tool-Calls",
                status="running",
                details={
                    "files": [e["path"] for e in file_entries],
                    "max_tool_calls": max_tool_calls,
                    "model": model,
                    "provider": provider,
                },
            )

        final_answer, tl_trace = run_rag_chat_tool_loop(
            messages=messages,
            provider=provider,
            model=model,
            repo_root=repo_root,
            max_tool_calls=max_tool_calls,
            max_chars_per_file=max_chars_per_file,
            timeout=timeout_s,
            rec=rec,
        )
        trace["tool_loop"] = tl_trace
        trace["file_list"] = [e["path"] for e in file_entries]
        if rec:
            rec.event(
                "rag_iterative_tool_loop_done",
                f"Tool-Loop abgeschlossen ({tl_trace.get('tool_calls_made', 0)} Tool-Calls)",
                status="completed" if final_answer else "failed",
                details=tl_trace,
                output_preview=final_answer[:500] if final_answer else None,
            )
        return final_answer, trace

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
        raw = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=llm_history,
            timeout=timeout_s,
        )
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
