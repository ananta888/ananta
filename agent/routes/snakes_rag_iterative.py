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

from agent.config import lookup_model_context_tokens, settings as _cfg_settings
from agent.llm_integration import generate_text
from agent.routes.ai_snake_config import _current_config

_log = logging.getLogger(__name__)

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


def worker_chat_rag_iterative(
    question: str,
    *,
    provider: str = "lmstudio",
    model: str | None = None,
    limits: Any | None = None,
    rec: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    """Iterative RAG: fetch relevant files, read fully, batch → LLM → synthesize."""
    trace: dict[str, Any] = {"mode": "rag_iterative"}

    cfg = _current_config()
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
            f"Analysiere die folgenden Dateien (Batch {i}/{len(batches)}):\n\n"
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
                input_preview=batch_prompt[:800],
            )

        t_batch = _time()
        try:
            raw = generate_text(
                prompt=batch_prompt,
                provider=provider,
                model=model,
                history=[{"role": "system", "content": _SYSTEM_PROMPT}],
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
                output_preview=text[:600] if text else None,
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
        f"Analyse der relevanten Dateien aus {len(batch_summaries)} Batches:\n\n"
        + combined
        + "\n\nErstelle eine vollständige, strukturierte Antwort auf Basis dieser Analyse."
    )

    if rec:
        rec.event(
            "rag_iterative_synthesis",
            f"Synthese aus {len(batch_summaries)} Batch-Antworten",
            status="running",
            input_preview=synthesis_prompt[:600],
        )

    t_syn = _time()
    try:
        raw = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=[{"role": "system", "content": _SYSTEM_PROMPT}],
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
            output_preview=final_answer[:600] if final_answer else None,
        )

    trace["synthesis"] = "done"
    return final_answer, trace
