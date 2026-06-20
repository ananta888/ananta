import concurrent.futures as _cf
import logging
import pathlib as _pl
import threading
import time
from typing import Any

from agent.cli_backends.architecture_scan import _resolve_repo_root
from agent.config import lookup_model_context_tokens, settings as _cfg_settings
from agent.llm_integration import generate_text
from agent.routes.ai_snake_config import _current_config

_SNAKE_CHAT_PROMPT = (
    "Du bist AI-Snake im Ananta Hub.\n"
    "Regeln (streng):\n"
    "1) Antworte nur auf Basis des Ananta-Kontexts und der Nutzerfrage.\n"
    "2) Erfinde keine Produkte, URLs, Features, Befehle oder Fakten.\n"
    "3) Wenn Informationen fehlen oder unsicher sind, sage explizit: "
    "\"Unklar, bitte Kontext pruefen\".\n"
    "4) Gib keine externen Links aus, ausser der Nutzer hat explizit danach gefragt.\n"
    "5) Halte Antworten kurz, konkret, technisch nutzbar, auf Deutsch.\n"
    "6) Wenn Schrittfolge noetig ist, gib maximal 5 nummerierte Schritte.\n"
)

_SCAN_CANCELS: dict[str, threading.Event] = {}

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
              ".tox", "dist", "build", ".eggs", "project-workspaces", "tests", "test",
              ".claude", ".idea", ".vscode"}
_STOPWORDS = {"bitte", "mir", "den", "die", "das", "der", "und", "oder", "wie", "was",
              "ist", "sind", "in", "im", "mit", "von", "zu", "an", "auf", "f\u00fcr", "the",
              "a", "an", "and", "or", "how", "what", "is", "please", "explain", "me"}


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


def worker_chat_full_scan(
    question: str,
    *,
    provider: str = "lmstudio",
    model: str | None = None,
    limits: "Any | None" = None,
    cancel_key: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    cancel_event = threading.Event()
    if cancel_key:
        _SCAN_CANCELS[cancel_key] = cancel_event

    try:
        cfg = _current_config()
        max_batches = max(1, min(16, int(float(cfg.get("chat_full_scan_max_batches") or 8))))
        files_per_batch_cfg = max(1, min(10, int(float(cfg.get("chat_full_scan_files_per_batch") or 3))))
        parallel_batches = max(1, min(8, int(float(cfg.get("chat_full_scan_parallel_batches") or 4))))
        timeout_s = max(60, min(7200, int(float(cfg.get("chat_full_scan_timeout_s") or 1800))))
        source_only_val = cfg.get("chat_full_scan_source_only")
        source_only = source_only_val if isinstance(source_only_val, bool) else True
        try:
            chars_per_file = max(100, min(20000, int(float(cfg.get("chat_full_scan_chars_per_file") or 600))))
        except (TypeError, ValueError):
            chars_per_file = 600
        try:
            cfg_max_in = cfg.get("chat_full_scan_max_input_tokens")
            cfg_max_input_tokens = int(float(cfg_max_in)) if cfg_max_in not in (None, "") else None
        except (TypeError, ValueError):
            cfg_max_input_tokens = None
    except (TypeError, ValueError):
        max_batches, files_per_batch_cfg, parallel_batches, timeout_s = 8, 3, 4, 1800
        source_only = True
        chars_per_file = 600
        cfg_max_input_tokens = None
    budget_instruction = _answer_budget_instruction(limits)

    model_context_tokens = lookup_model_context_tokens(model) or 4096
    if cfg_max_input_tokens and cfg_max_input_tokens > 0:
        effective_max_input_tokens = min(cfg_max_input_tokens, max(model_context_tokens - 256, 256))
    else:
        effective_max_input_tokens = max(model_context_tokens - 256, 256)

    llm_history = [{"role": "system", "content": _SNAKE_CHAT_PROMPT}, *list(conversation_history or [])]
    trace: dict[str, Any] = {"mode": "full_scan_chat"}
    trace["conversation_history_messages"] = len(conversation_history or [])
    trace["model"] = model or ""
    trace["model_context_tokens"] = int(model_context_tokens)
    trace["max_input_tokens"] = int(effective_max_input_tokens)
    trace["chars_per_file_cfg"] = int(chars_per_file)
    trace["files_per_batch_cfg"] = int(files_per_batch_cfg)

    repo_root = _resolve_repo_root()
    if not repo_root:
        trace["error"] = "no_repo_root"
        return "", trace

    exts = (".py", ".jsonl") if source_only else (".py", ".ts", ".jsonl")

    all_files: list[_pl.Path] = []
    for ext in exts:
        for f in repo_root.rglob(f"*{ext}"):
            if not any(part in _SKIP_DIRS for part in f.parts):
                all_files.append(f)

    _keywords = [w.lower() for w in question.replace("/", " ").split()
                 if len(w) >= 3 and w.lower() not in _STOPWORDS]

    _RANK_CONTENT_PREFIX = 2000
    _file_content_cache: dict[str, str] = {}

    def _read_prefix(f: _pl.Path) -> str:
        cached = _file_content_cache.get(str(f))
        if cached is not None:
            return cached
        try:
            text = f.read_text(encoding="utf-8", errors="replace")[:_RANK_CONTENT_PREFIX]
        except OSError:
            text = ""
        _file_content_cache[str(f)] = text
        return text

    def _score(f: _pl.Path) -> int:
        rel = str(f.relative_to(repo_root)).lower()
        name = f.name.lower()
        content = _read_prefix(f).lower()
        s = 0
        for kw in _keywords:
            if kw in name:
                s += 3
            if kw in rel:
                s += 2
            if kw in content:
                s += min(5, content.count(kw))
        return s

    all_files.sort(key=lambda f: (-_score(f), str(f.relative_to(repo_root))))

    trace["files_found"] = len(all_files)
    trace["ranking_keywords"] = list(_keywords)
    if all_files:
        top = all_files[: min(5, len(all_files))]
        trace["ranking_top_files"] = [
            {"path": str(f.relative_to(repo_root)), "score": _score(f)}
            for f in top
        ]
        trace["ranking_files_with_hits"] = sum(1 for f in all_files if _score(f) > 0)
    if not all_files:
        trace["error"] = "no_source_files"
        return "", trace

    def _estimate_batch_tokens(batch: list) -> int:
        framing_per_file = 40
        system_overhead = 400
        total_chars = system_overhead + sum(
            chars_per_file + framing_per_file for _ in batch
        )
        return max(1, total_chars // 4)

    effective_files_per_batch = files_per_batch_cfg
    while (
        effective_files_per_batch > 1
        and _estimate_batch_tokens(all_files[:effective_files_per_batch]) > effective_max_input_tokens
    ):
        effective_files_per_batch -= 1
    if effective_files_per_batch < files_per_batch_cfg:
        trace["files_per_batch_auto_shrunk_from"] = int(files_per_batch_cfg)
        trace["files_per_batch_auto_shrunk_reason"] = "context_budget"

    max_files = max_batches * effective_files_per_batch
    selected = all_files[:max_files]
    batches = [selected[i:i + effective_files_per_batch] for i in range(0, len(selected), effective_files_per_batch)]
    trace["batches_planned"] = len(batches)
    trace["files_selected"] = len(selected)
    trace["files_per_batch_used"] = int(effective_files_per_batch)
    trace["timeout_per_batch_s"] = timeout_s

    def _run_batch(args: tuple[int, list]) -> tuple[int, str, str, dict[str, Any]]:
        step, batch = args
        file_blocks: list[str] = []
        for f in batch:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:chars_per_file]
                rel = str(f.relative_to(repo_root))
                lang = f.suffix.lstrip(".") or "text"
                file_blocks.append(f"### {rel}\n```{lang}\n{content}\n```")
            except OSError:
                pass
        if not file_blocks:
            return step, "", "", {"error": "no_file_blocks"}
        file_labels = ", ".join(str(f.relative_to(repo_root)) for f in batch)
        batch_prompt = (
            f"Frage: {question}\n\n"
            + (f"{budget_instruction}\n\n" if budget_instruction else "")
            + f"Analysiere Quellcode-Batch {step}/{len(batches)} [{file_labels}]:\n\n"
            + "\n\n".join(file_blocks)
            + "\n\nExtrahiere alle relevanten Erkenntnisse zur Frage aus diesem Quellcode-Batch. Kurze, pr\u00e4zise Antwort."
        )
        try:
            answer = generate_text(
                prompt=batch_prompt,
                provider=provider,
                model=model,
                history=llm_history,
                timeout=timeout_s,
            )
            if isinstance(answer, dict):
                text = str(answer.get("text") or "").strip()
            else:
                text = str(answer or "").strip()
            batch_meta: dict[str, Any] = {
                "estimated_input_tokens": _estimate_batch_tokens(batch),
                "chars_in_prompt": len(batch_prompt),
            }
            if not text:
                try:
                    from agent.llm_integration import extract_llm_call_metadata
                    meta = extract_llm_call_metadata(answer) if isinstance(answer, dict) else {}
                    if meta:
                        batch_meta["empty_reason"] = meta.get("empty_reason")
                        if meta.get("context_limit"):
                            batch_meta["context_limit"] = int(meta["context_limit"])
                        if meta.get("model_id"):
                            batch_meta["model_id"] = str(meta["model_id"])
                except Exception:
                    pass
            return step, file_labels, text, batch_meta
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "full_scan batch %d failed: %s", step, exc, exc_info=False
            )
            return step, file_labels, "", {"error": str(exc), "error_type": type(exc).__name__}

    batch_summaries: list[str] = []
    results = [None] * len(batches)
    batch_metas: list[dict[str, Any]] = []
    try:
        with _cf.ThreadPoolExecutor(max_workers=parallel_batches) as pool:
            futures = {pool.submit(_run_batch, (i + 1, b)): i for i, b in enumerate(batches)}
            for fut in _cf.as_completed(futures):
                if cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    trace["cancelled"] = True
                    break
                step, file_labels, batch_answer, batch_meta = fut.result()
                results[step - 1] = (step, file_labels, batch_answer)
                batch_metas.append({"step": step, **batch_meta})
    finally:
        if cancel_key:
            _SCAN_CANCELS.pop(cancel_key, None)
    for r in results:
        if r:
            step, file_labels, batch_answer = r
            if batch_answer:
                batch_summaries.append(f"**Batch {step}** [{file_labels}]:\n{batch_answer}")

    trace["batches_completed"] = len(batch_summaries)
    trace["batch_metas"] = batch_metas

    if not batch_summaries:
        overflow_count = sum(
            1 for m in batch_metas if m.get("empty_reason") == "context_overflow_likely"
        )
        if overflow_count and overflow_count == len(batch_metas):
            trace["error"] = "context_overflow"
            trace["error_hint"] = (
                "Alle Batches haben leere Antworten wegen wahrscheinlichem "
                "Context-Overflow. Reduziere chat_full_scan_files_per_batch "
                "oder chat_full_scan_chars_per_file, oder verwende ein Modell "
                "mit groesserem Context-Window."
            )
        elif any(m.get("error_type") for m in batch_metas):
            trace["error"] = "all_batches_failed_with_exception"
        else:
            trace["error"] = "all_batches_empty"
        return "", trace

    combined = "\n\n---\n\n".join(batch_summaries)
    synthesis_prompt = (
        f"Urspr\u00fcngliche Frage: {question}\n\n"
        + (f"{budget_instruction}\n\n" if budget_instruction else "")
        + f"Quellcode-Analyse aus {len(batch_summaries)} Batches "
        f"({len(selected)} Dateien, nur {exts[0]}-Quellcode):\n\n"
        + combined
        + "\n\nErstelle eine vollst\u00e4ndige, strukturierte Antwort basierend ausschlie\u00dflich auf dem analysierten Quellcode."
    )
    try:
        final_answer = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=llm_history,
            timeout=timeout_s,
        )
        final_answer = str(final_answer or "").strip()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "full_scan synthesis failed: %s", exc, exc_info=False
        )
        final_answer = ""

    return final_answer, trace
