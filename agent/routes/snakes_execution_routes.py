"""Snake execution endpoints — chat API, ask, worker-context."""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, has_app_context, jsonify, request

from agent.config import settings
from agent.llm_integration import generate_text
from agent.services.rag_service import get_rag_service

from .snakes import (
    _MAX_CHAT_MSGS,
    _MAX_ROOM_MSGS,
    _SCAN_CANCELS,
    _VALID_CHANNEL_TYPES,
    _VALID_VISIBILITY,
    _chat_messages,
    _is_local_request,
    _optional_user_auth,
    _request_device_id,
    _room_messages,
    _snake_bound_to_auth,
    _snakes,
    snakes_bp,
)

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

_SNAKE_RETRIEVAL_CONFIG_KEYS = frozenset({
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
})


@dataclass(frozen=True, slots=True)
class SnakeAskLimits:
    context_chars: int = 4000
    answer_chars: int = 2200
    max_tokens: int | None = None
    rag_top_k: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SnakeAskLimits":
        return cls(
            context_chars=_bounded_optional_int(payload.get("context_chars"), default=4000, minimum=500, maximum=20000),
            answer_chars=_bounded_optional_int(payload.get("answer_chars"), default=2200, minimum=600, maximum=12000),
            max_tokens=_bounded_optional_int(payload.get("max_tokens"), default=None, minimum=100, maximum=8000),
            rag_top_k=_bounded_optional_int(payload.get("rag_top_k"), default=None, minimum=1, maximum=120),
        )


def _bounded_optional_int(value: Any, *, default: int | None, minimum: int, maximum: int) -> int | None:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _background_threads_disabled() -> bool:
    return bool(
        (has_app_context() and bool(getattr(current_app, "testing", False)))
        or str(getattr(settings, "role", "")).strip().lower() == "test"
        or os.environ.get("PYTEST_CURRENT_TEST")
        or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}
    )


def _resolve_ai_snake_chat_provider() -> tuple[str, str | None]:
    provider = "lmstudio"
    model: str | None = None
    try:
        from agent.routes.ai_snake_config import _current_config

        cfg = _current_config()
        backend = str(cfg.get("chat_backend") or "").strip().lower()
        fallback = str(cfg.get("chat_backend_fallback") or "").strip().lower()
        configured_model = str(cfg.get("chat_backend_model") or "").strip() or None
        if configured_model:
            model = configured_model
        if backend == "lmstudio":
            provider = "lmstudio"
        elif backend in {"ananta-worker", "opencode", "hermes"}:
            provider = "lmstudio" if fallback in {"", "none", "lmstudio"} else "lmstudio"
    except Exception:
        pass
    return provider, model


def _snake_retrieval_config_overrides(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("retrieval_config")
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, Any] = {}
    for key in _SNAKE_RETRIEVAL_CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            overrides[key] = value
        elif isinstance(value, str):
            overrides[key] = value.strip()
    return overrides


def _build_local_repo_fallback_context(prompt: str) -> str:
    text = str(prompt or "").lower()
    repo_root = Path(getattr(settings, "rag_repo_root", ".")).resolve()
    if "_build_grounded_snake_prompt" in text or "snakes.py" in text:
        snakes_file = repo_root / "agent" / "routes" / "snakes.py"
        if snakes_file.exists():
            try:
                lines = snakes_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return ""
            for idx, line in enumerate(lines):
                if "def _build_grounded_snake_prompt" in line:
                    start = max(0, idx - 4)
                    end = min(len(lines), idx + 24)
                    return "\n".join(lines[start:end]).strip()
    if "agent/routes" in text or "routes" in text:
        routes_dir = repo_root / "agent" / "routes"
        if routes_dir.exists() and routes_dir.is_dir():
            names = sorted(path.name for path in routes_dir.glob("*.py") if path.is_file())
            if names:
                return "Dateien in agent/routes:\n" + "\n".join(f"- {name}" for name in names[:24])
    return ""


def _build_grounded_snake_prompt(
    user_text: str,
    *,
    limits: SnakeAskLimits | None = None,
    retrieval_config_overrides: dict[str, Any] | None = None,
) -> tuple[str, bool, str]:
    prompt = str(user_text or "").strip()
    if not prompt:
        return prompt
    effective_limits = limits or SnakeAskLimits()
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))

        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None

        profile = resolve_profile(prompt, cfg, domain_hint=domain_hint, feature_flag=feature_flag)

        bundle, grounded = get_rag_service().build_execution_context(
            prompt,
            task_kind="research",
            retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
            source_types=profile.source_types or None,
            max_chunks=effective_limits.rag_top_k,
            retrieval_profile=profile.as_dict(),
        )
        chunks = list(bundle.get("chunks") or [])
        if chunks:
            src_type_counts: dict[str, int] = {}
            for chunk in chunks:
                metadata = dict((chunk or {}).get("metadata") or {})
                st = str(metadata.get("source_type") or (chunk or {}).get("engine") or "unknown").strip().lower() or "unknown"
                src_type_counts[st] = int(src_type_counts.get(st, 0)) + 1
            logging.getLogger(__name__).info(
                "ai_snake_retrieval_profile_selected profile_id=%s domain=%s intent=%s feature_flag=%s source_type_counts=%s warnings=%s",
                profile.profile_id,
                profile.domain,
                profile.intent,
                profile.feature_flag,
                src_type_counts,
                list(profile.warnings),
            )
            summary_parts = [f"{k}:{v}" for k, v in sorted(src_type_counts.items())]
            summary = f"Kontext: {len(chunks)} Treffer ({', '.join(summary_parts)}) [{profile.profile_id}]"
            return grounded, True, summary
    except Exception as exc:
        logging.getLogger(__name__).debug("ai_snake_retrieval_profile_failed: %s", exc)
        pass
    local_fallback = _build_local_repo_fallback_context(prompt)
    if local_fallback:
        grounded = (
            f"{prompt}\n\n"
            "Lokaler Projektkontext (Fallback, wenn RAG leer ist):\n"
            f"{local_fallback}"
        )
        return grounded, True, "Kontext: 1 Treffer (repo_fallback:1)"
    return prompt, False, "Kontext: 0 Treffer"


def _resolve_snake_retrieval_profile_trace(
    user_text: str,
    *,
    retrieval_config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None
        profile = resolve_profile(str(user_text or ""), cfg, domain_hint=domain_hint, feature_flag=feature_flag)
        return {
            "profile_id": profile.profile_id,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "output_intent": profile.output_intent,
            "coverage_policy": profile.coverage_policy,
            "summary_policy": profile.summary_policy,
            "source_types": list(profile.source_types),
            "source_type_weights": dict(profile.source_type_weights),
            "feature_flag": profile.feature_flag,
            "trigger_mode": str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower(),
            "selected_by": profile.selected_by,
            "reasons": list(profile.reasons),
            "negative_source_patterns": list(profile.negative_source_patterns),
            "warnings": list(profile.warnings),
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _snake_retrieval_dry_run(
    question: str,
    *,
    retrieval_config_overrides: dict[str, Any] | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    """RWY-005: run retrieval for *question* without calling an LLM.

    Returns profile metadata, resolver scope flags, candidate counts per
    source type, top-5 candidate paths, and preset hints.
    """
    result: dict[str, Any] = {}
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile
        from worker.retrieval.codecompass_candidate_resolver import ResolverConfig

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None

        profile = resolve_profile(question, cfg, domain_hint=domain_hint, feature_flag=feature_flag)
        result["retrieval_profile"] = {
            "profile_id": profile.profile_id,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "feature_flag": profile.feature_flag,
            "trigger_mode": str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower(),
            "selected_by": profile.selected_by,
            "reasons": list(profile.reasons),
            "source_types": list(profile.source_types),
            "source_type_weights": dict(profile.source_type_weights),
            "negative_source_patterns": list(profile.negative_source_patterns),
            "warnings": list(profile.warnings),
        }

        scope = ResolverConfig.from_env()
        result["resolver_scope"] = {
            "include_source": scope.include_source,
            "include_test_paths": scope.include_test_paths,
            "include_docs": scope.include_docs,
            "include_workflows": scope.include_workflows,
            "include_third_party": scope.include_third_party,
            "include_xml_nodes": scope.include_xml_nodes,
        }

        try:
            bundle, _ = get_rag_service().build_execution_context(
                question,
                task_kind="research",
                retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
                source_types=profile.source_types or None,
                max_chunks=max(8, min(top_k, 40)),
                retrieval_profile=profile.as_dict(),
            )
            chunks = list(bundle.get("chunks") or [])
            src_counts: dict[str, int] = {}
            top_sources: list[dict[str, Any]] = []
            for ch in chunks:
                meta = dict((ch or {}).get("metadata") or {})
                st = str(meta.get("source_type") or (ch or {}).get("engine") or "unknown").lower()
                src_counts[st] = src_counts.get(st, 0) + 1
                if len(top_sources) < 5:
                    path = str(meta.get("file_path") or meta.get("path") or (ch or {}).get("path") or "").strip()
                    score = float((ch or {}).get("score") or meta.get("score") or 0.0)
                    if path:
                        top_sources.append({"path": path, "source_type": st, "score": round(score, 3)})
            result["candidate_counts"] = {
                "total": len(chunks),
                "by_source_type": src_counts,
            }
            result["top_sources"] = top_sources
            result["degraded_channels"] = []
        except Exception as exc:
            result["retrieval_error"] = str(exc)[:200]
            result["candidate_counts"] = {"total": 0, "by_source_type": {}}
            result["top_sources"] = []
            result["degraded_channels"] = [str(exc)[:120]]

        q_lower = question.lower()
        hints: list[str] = []
        if any(w in q_lower for w in ("readme", "docs", "doku", "architektur", "todo", "notiz")) and not scope.include_docs:
            hints.append("Tipp: docs/artifact deaktiviert \u2192 :config chat_retrieval_profile docs_first")
        if any(w in q_lower for w in ("test", "spec", "pytest", "unittest")) and not scope.include_test_paths:
            hints.append("Tipp: tests deaktiviert \u2192 ANANTA_CODECOMPASS_INCLUDE_TEST_PATHS=1 oder :config code_with_tests")
        if any(w in q_lower for w in ("workflow", "blueprint", "ops", "runbook")) and not scope.include_workflows:
            hints.append("Tipp: workflows deaktiviert \u2192 ANANTA_CODECOMPASS_INCLUDE_WORKFLOWS=1 oder :config ops")
        result["preset_hints"] = hints

    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def _append_room_ai_message(*, text: str) -> None:
    if not text:
        return
    msg: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "channel_id": "room:main",
        "channel_type": "room",
        "sender_id": "ai-snake",
        "sender_kind": "assistant",
        "target_ids": [],
        "text": text[:6000],
        "visibility": "room",
        "delivery_state": "received",
        "policy_decision_ref": None,
    }
    global _room_messages
    _room_messages.append(msg)
    if len(_room_messages) > _MAX_ROOM_MSGS:
        _room_messages = _room_messages[-_MAX_ROOM_MSGS:]


def _worker_chat_full_scan(
    question: str,
    *,
    provider: str = "lmstudio",
    model: str | None = None,
    limits: "SnakeAskLimits | None" = None,
    cancel_key: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Multi-batch source-code-only analysis for chat full_scan mode.

    Reads Python source files from the repo root in batches, sends each
    batch to the LLM for analysis, then synthesises the results into one answer.

    Per-batch prompt size is sized to fit the resolved model context window
    (model_info.context_length \u2192 settings.lmstudio_model_contexts lookup
    \u2192 settings.lmstudio_max_context_tokens fallback). When the configured
    ``files_per_batch`` would overflow the window, the function auto-shrinks
    the batch down to the largest file count that fits. The original
    configuration is preserved in the trace so the caller can surface a
    hint about what to change in user.json.
    """
    import pathlib as _pl
    from agent.common.sgpt import _resolve_repo_root
    from agent.config import lookup_model_context_tokens
    from agent.routes.ai_snake_config import _current_config

    effective_limits = limits or SnakeAskLimits()
    cfg = _current_config()
    trace: dict[str, Any] = {"mode": "full_scan_chat"}

    cancel_event = threading.Event()
    if cancel_key:
        _SCAN_CANCELS[cancel_key] = cancel_event

    try:
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

    from agent.config import settings as _cfg_settings
    model_context_tokens = lookup_model_context_tokens(model) or int(
        _cfg_settings.lmstudio_max_context_tokens
    )
    if cfg_max_input_tokens and cfg_max_input_tokens > 0:
        effective_max_input_tokens = min(cfg_max_input_tokens, max(model_context_tokens - 256, 256))
    else:
        effective_max_input_tokens = max(model_context_tokens - 256, 256)

    trace["model"] = model or ""
    trace["model_context_tokens"] = int(model_context_tokens)
    trace["max_input_tokens"] = int(effective_max_input_tokens)
    trace["chars_per_file_cfg"] = int(chars_per_file)
    trace["files_per_batch_cfg"] = int(files_per_batch_cfg)

    repo_root = _resolve_repo_root()
    if not repo_root:
        trace["error"] = "no_repo_root"
        return "", trace

    _SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
                  ".tox", "dist", "build", ".eggs", "project-workspaces", "tests", "test",
                  ".claude", ".idea", ".vscode"}
    exts = (".py",) if source_only else (".py", ".ts")

    all_files: list[_pl.Path] = []
    for ext in exts:
        for f in repo_root.rglob(f"*{ext}"):
            if not any(part in _SKIP_DIRS for part in f.parts):
                all_files.append(f)

    _STOPWORDS = {"bitte", "mir", "den", "die", "das", "der", "und", "oder", "wie", "was",
                  "ist", "sind", "in", "im", "mit", "von", "zu", "an", "auf", "f\u00fcr", "the",
                  "a", "an", "and", "or", "how", "what", "is", "please", "explain", "me"}
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

    import concurrent.futures as _cf

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
            f"Analysiere Quellcode-Batch {step}/{len(batches)} [{file_labels}]:\n\n"
            + "\n\n".join(file_blocks)
            + "\n\nExtrahiere alle relevanten Erkenntnisse zur Frage aus diesem Quellcode-Batch. Kurze, pr\u00e4zise Antwort."
        )
        try:
            answer = generate_text(
                prompt=batch_prompt,
                provider=provider,
                model=model,
                history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
                timeout=timeout_s,
            )
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
        f"Quellcode-Analyse aus {len(batch_summaries)} Batches "
        f"({len(selected)} Dateien, nur {exts[0]}-Quellcode):\n\n"
        + combined
        + "\n\nErstelle eine vollst\u00e4ndige, strukturierte Antwort basierend ausschlie\u00dflich auf dem analysierten Quellcode."
    )
    try:
        final_answer = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
            timeout=timeout_s,
        )
        final_answer = str(final_answer or "").strip()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "full_scan synthesis failed: %s", exc, exc_info=False
        )
        final_answer = ""

    return final_answer, trace


def _spawn_ai_chat_reply(*, user_text: str) -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        try:
            provider, model = _resolve_ai_snake_chat_provider()

            try:
                from agent.routes.ai_snake_config import _current_config
                from agent.services.retrieval_profile_service import _is_full_scan_intent
                _cfg = _current_config()
                if _is_full_scan_intent(prompt, "", _cfg):
                    answer, scan_trace = _worker_chat_full_scan(prompt, provider=provider, model=model, cancel_key="room")
                    files_found = scan_trace.get("files_found", 0)
                    batches_done = scan_trace.get("batches_completed", 0)
                    scan_summary = f"full_scan: {batches_done} Batches, {files_found} Dateien"
                    if not answer:
                        answer = "Full-Scan ergab keine Antwort."
                    if len(answer) > 5800:
                        answer = answer[:5800].rstrip() + "\n\n[gekuerzt]"
                    _append_room_ai_message(text=f"{answer}\n\n[{scan_summary}]")
                    return
            except Exception as exc:
                logging.getLogger(__name__).debug("full_scan check failed, falling back: %s", exc)

            grounded_prompt, has_context, context_summary = _build_grounded_snake_prompt(prompt)
            q = prompt.lower()
            asks_for_concrete_local_facts = any(
                token in q for token in (
                    "konkret", "datei", "dateien", "artefakt", "artefakte", "welche", "verfuegbar", "verf\u00fcgbar"
                )
            )
            if asks_for_concrete_local_facts and not has_context:
                _append_room_ai_message(text=f"Unklar, bitte Kontext pruefen.\n\n[{context_summary}]")
                return
            answer = generate_text(
                prompt=grounded_prompt,
                provider=provider,
                model=model,
                history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
                timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
            )
            text = str(answer or "").strip()
            asked_for_link = any(token in prompt.lower() for token in ("link", "url", "quelle", "source"))
            if text and not asked_for_link:
                text = text.replace("http://", "").replace("https://", "")
            if len(text) > 2200:
                text = text[:2200].rstrip() + "\n\n[gekuerzt]"
            if not text:
                text = "AI-Snake konnte gerade keine Antwort erzeugen."
            text = f"{text}\n\n[{context_summary}]"
            _append_room_ai_message(text=text)
        except Exception as exc:
            logging.getLogger(__name__).warning("ai-snake-chat-reply failed: %s", exc)
            _append_room_ai_message(text="AI-Snake Fehler: Antwort konnte nicht erzeugt werden.")

    thread = threading.Thread(target=_runner, name="snake-chat-reply", daemon=True)
    thread.start()


def _auth_token(snake_id: str) -> str | None:
    """Extract Bearer token from Authorization header. Returns None if missing."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _verify_token(snake_id: str) -> bool:
    snake = _snakes.get(snake_id)
    if not snake or not snake.get("active"):
        return False
    token = _auth_token(snake_id)
    return token is not None and secrets.compare_digest(str(snake.get("token") or ""), token)


def _pick_worker_for_ask() -> tuple[str, str | None]:
    """Return (worker_url, token) for the first online worker, or ("", None)."""
    try:
        from agent.services.agent_registry_service import get_agent_registry_service
        from agent.services.repository_registry import get_repository_registry

        agents = get_agent_registry_service().get_online_agents()
        if not agents:
            return "", None
        agent = agents[0]
        worker_url = str(getattr(agent, "url", "") or "").strip()
        if not worker_url:
            return "", None
        token: str | None = None
        try:
            db_agent = get_repository_registry().agent_repo.get_by_url(worker_url)
            token = str(getattr(db_agent, "token", "") or "").strip() or None
        except Exception:
            pass
        return worker_url, token
    except Exception:
        return "", None


def _resolve_lmstudio_model_for_worker(configured: str | None) -> str | None:
    """Resolve an actual LMStudio model ID, bypassing smoke/placeholder names."""
    try:
        from agent.llm_integration import _list_lmstudio_candidates, _select_best_lmstudio_model, _prepare_lmstudio_history
        from agent.config import settings as _s

        base_url = str(getattr(_s, "lmstudio_url", "") or "").rstrip("/")
        if not base_url:
            return configured
        candidates = _list_lmstudio_candidates(base_url, timeout=5)
        if not candidates:
            return configured
        if configured and "smoke" not in configured.lower() and "ananta" not in configured.lower():
            from agent.llm_integration import _find_matching_lmstudio_candidate
            matched = _find_matching_lmstudio_candidate(configured, candidates)
            if matched:
                return str(matched.get("id") or configured)
        history = _prepare_lmstudio_history(candidates)
        best = _select_best_lmstudio_model(candidates, history)
        return str((best or candidates[0]).get("id") or "")
    except Exception:
        return configured


def _worker_propose(
    grounded_prompt: str,
    model: str | None,
    *,
    limits: SnakeAskLimits | None = None,
    retrieval_profile_trace: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Forward prompt to worker /step/propose. Returns (answer, trace)."""
    from agent.services.task_runtime_service import forward_to_worker

    effective_limits = limits or SnakeAskLimits()
    trace: dict[str, Any] = {}
    worker_url, token = _pick_worker_for_ask()
    trace["worker_url"] = worker_url
    if not worker_url:
        trace["error"] = "no_online_worker"
        return "", trace

    resolved_model = _resolve_lmstudio_model_for_worker(model)
    trace["model_requested"] = model
    trace["model_resolved"] = resolved_model
    payload: dict[str, Any] = {
        "prompt": grounded_prompt,
        "provider": "lmstudio",
        "temperature": 0.3,
        "max_context_chars": effective_limits.context_chars,
    }
    if resolved_model:
        payload["model"] = resolved_model
    if effective_limits.max_tokens is not None:
        payload["max_tokens"] = effective_limits.max_tokens
    trace["prompt_chars"] = len(grounded_prompt)
    trace["prompt_preview"] = grounded_prompt[:300]
    trace["limits"] = {
        "context_chars": effective_limits.context_chars,
        "answer_chars": effective_limits.answer_chars,
        "max_tokens": effective_limits.max_tokens,
        "rag_top_k": effective_limits.rag_top_k,
    }
    if retrieval_profile_trace:
        analysis_mode = str(retrieval_profile_trace.get("analysis_mode") or "standard")
        trace["full_scan"] = {
            "status": "delegated_to_worker" if analysis_mode == "architecture_full_scan" else "not_requested",
            "analysis_mode": analysis_mode,
            "profile_id": retrieval_profile_trace.get("profile_id"),
            "output_intent": retrieval_profile_trace.get("output_intent"),
            "coverage_policy": retrieval_profile_trace.get("coverage_policy"),
            "plan_id": None,
            "artifact_paths": {},
        }

    try:
        result = forward_to_worker(worker_url, "/step/propose", payload, token=token)
        if result is None and token:
            result = forward_to_worker(worker_url, "/step/propose", payload, token=None)
    except Exception as exc:
        logging.getLogger(__name__).debug("snake-ask worker forward failed: %s", exc)
        trace["error"] = str(exc)[:120]
        return "", trace

    trace["worker_raw_response"] = str(result)[:500] if result else None
    if not isinstance(result, dict):
        trace["error"] = "non_dict_response"
        return "", trace
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        trace["error"] = "no_data_field"
        return "", trace
    text = str(data.get("reason") or data.get("raw") or data.get("answer") or "").strip()
    if len(text) > effective_limits.answer_chars:
        text = text[:effective_limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
    trace["answer_chars"] = len(text)
    return text, trace


# ── Route endpoints ────────────────────────────────────────────────────────────


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["POST"])
def chat_send(snake_id: str):
    """POST /snakes/<id>/chat/messages -- ChatMessage-v1 senden."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
    auth = _optional_user_auth()
    if not auth and not _is_local_request():
        return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401
    snake = _snakes.get(snake_id) or {}
    if auth and not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_identity_mismatch"}), 403

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    channel_type = str(body.get("channel_type") or "room")
    visibility = str(body.get("visibility") or "room")
    text = str(body.get("text") or "").strip()[:500]

    if not text:
        return jsonify({"error": "text erforderlich"}), 400

    if visibility == "local_only":
        return jsonify({"error": "local_only Nachrichten werden am Hub abgelehnt"}), 422

    if channel_type not in _VALID_CHANNEL_TYPES:
        return jsonify({"error": f"ung\u00fcltiger channel_type: {channel_type}"}), 422

    msg: dict[str, Any] = {
        "id": str(body.get("id") or str(uuid.uuid4())),
        "created_at": time.time(),
        "channel_id": f"{channel_type}:main" if channel_type == "room" else f"{channel_type}:{snake_id}",
        "channel_type": channel_type,
        "sender_id": snake_id,
        "sender_kind": "user",
        "target_ids": list(body.get("target_ids") or []),
        "text": text,
        "visibility": visibility,
        "delivery_state": "received",
        "policy_decision_ref": None,
    }

    if channel_type == "room":
        global _room_messages  # noqa: PLW0602
        existing_ids = {m["id"] for m in _room_messages}
        if msg["id"] not in existing_ids:
            _room_messages.append(msg)
            if len(_room_messages) > _MAX_ROOM_MSGS:
                _room_messages = _room_messages[-_MAX_ROOM_MSGS:]
            _spawn_ai_chat_reply(user_text=text)
    elif channel_type == "direct":
        target_ids = msg["target_ids"]
        if not target_ids:
            return jsonify({"error": "target_ids erforderlich f\u00fcr direct"}), 422
        target_id = str(target_ids[0])
        if target_id not in _snakes:
            return jsonify({"error": f"Ziel-Snake unbekannt: {target_id}"}), 422
        inbox = _chat_messages.setdefault(target_id, [])
        existing_ids = {m["id"] for m in inbox}
        if msg["id"] not in existing_ids:
            inbox.append(msg)
            if len(inbox) > _MAX_CHAT_MSGS:
                _chat_messages[target_id] = inbox[-_MAX_CHAT_MSGS:]
    else:
        return jsonify({"error": f"channel_type {channel_type} nicht unterst\u00fctzt"}), 422

    return jsonify({"ok": True, "id": msg["id"]}), 202


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["GET"])
def chat_receive(snake_id: str):
    """GET /snakes/<id>/chat/messages?since=<cursor> -- Chat-Nachrichten abrufen."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404

    since_str = request.args.get("since", "")
    since: float = float(since_str) if since_str else 0.0

    direct = [m for m in _chat_messages.get(snake_id, []) if float(m.get("created_at") or 0) > since]
    room = [m for m in _room_messages if float(m.get("created_at") or 0) > since and m.get("sender_id") != snake_id]

    all_msgs = sorted(direct + room, key=lambda m: float(m.get("created_at") or 0))

    if direct:
        delivered_ids = {m["id"] for m in direct}
        _chat_messages[snake_id] = [m for m in _chat_messages.get(snake_id, []) if m["id"] not in delivered_ids]

    new_cursor = str(time.time()) if all_msgs else since_str

    return jsonify({"messages": all_msgs, "cursor": new_cursor}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/cancel", methods=["POST"])
def chat_cancel(snake_id: str):
    """POST /snakes/<id>/chat/cancel -- Laufenden full_scan abbrechen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
    cancelled = False
    for key in ("room", "snake_ask", snake_id):
        event = _SCAN_CANCELS.get(key)
        if event:
            event.set()
            cancelled = True
    return jsonify({"ok": True, "cancelled": cancelled}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/ack", methods=["POST"])
def chat_ack(snake_id: str):
    """POST /snakes/<id>/chat/ack -- Gelesene Nachrichten best\u00e4tigen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_ids: list[str] = [str(i) for i in (body.get("message_ids") or [])]
    return jsonify({"ok": True, "acked": len(message_ids)}), 200


@snakes_bp.route("/worker-context", methods=["POST"])
def worker_context():
    """POST /worker-context -- CWFH-009: Build WorkerContextHandoffV3 from a question.

    Accepts:
      {
        "question": str,
        "output_dir": str,
        "memory_context": str?,
        "manifest_hash": str?,
        "depth": str?,
        "workspace_root": str?,
        "max_candidates": int?
      }

    Returns WorkerContextHandoffV3 dict with candidate_files + context_files.
    """
    if not _is_local_request():
        auth = _optional_user_auth()
        if not auth:
            return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    question = str(body.get("question") or "").strip()[:2000]
    output_dir = str(body.get("output_dir") or "").strip()
    memory_context = str(body.get("memory_context") or "").strip() or None
    manifest_hash = str(body.get("manifest_hash") or "").strip() or None
    depth = str(body.get("depth") or "").strip() or None
    workspace_root = str(body.get("workspace_root") or "").strip() or None
    max_candidates = int(body.get("max_candidates") or 40)

    if not question:
        return jsonify({"error": "question required"}), 400
    if not output_dir:
        return jsonify({"error": "output_dir required"}), 400

    try:
        from worker.retrieval.codecompass_candidate_resolver import (
            CodeCompassCandidateResolver, ResolverConfig,
        )
        from agent.services.context_file_reader_service import (
            ContextFileReaderService, FileReadPolicy,
        )
        from agent.services.worker_contract_service import get_worker_contract_service
        from agent.services.worker_context_handoff_diagnostics_service import (
            get_worker_context_handoff_diagnostics_service,
        )

        resolver = CodeCompassCandidateResolver(max_candidates=max(1, min(max_candidates, 100)))
        mode = ResolverConfig.from_env()
        candidates = resolver.resolve(
            question=question,
            output_dir=output_dir,
            memory_context=memory_context,
            manifest_hash=manifest_hash,
            mode=mode,
        )

        policy = FileReadPolicy(workspace_root=workspace_root or output_dir)
        reader = ContextFileReaderService(policy=policy)
        context_files = reader.read_required_files(candidates)

        handoff = get_worker_contract_service().build_worker_context_handoff_v3(
            question=question,
            candidate_files=candidates,
            context_files=context_files,
            depth=depth,
            memory_context=memory_context,
            manifest_hash=manifest_hash,
        )
        handoff["diagnostics"] = get_worker_context_handoff_diagnostics_service().summarize(handoff)
        return jsonify(handoff), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("worker-context failed: %s", exc, exc_info=True)
        return jsonify({"error": f"worker-context error: {str(exc)[:200]}"}), 500


@snakes_bp.route("/snake/ask", methods=["POST"])
def snake_ask():
    """POST /snake/ask -- Synchrone AI-Antwort f\u00fcr den TUI ananta-worker Modus.

    Akzeptiert v1 ({question, context, depth}) und v2 ({question, context, depth, memory_context}).
    Optionales Feld "debug": true gibt trace-Infos zur\u00fcck.
    Antwortet mit {"answer": "..."}. Routet \u00fcber einen registrierten Worker-Prozess;
    f\u00e4llt auf direkten LMStudio-Aufruf zur\u00fcck falls kein Worker verf\u00fcgbar.
    """
    if not _is_local_request():
        auth = _optional_user_auth()
        if not auth:
            return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    question = str(body.get("question") or "").strip()[:1000]
    debug = bool(body.get("debug"))
    trace_only = bool(body.get("trace_only"))
    limits = SnakeAskLimits.from_payload(body)
    retrieval_config_overrides = _snake_retrieval_config_overrides(body)
    request_model = str(body.get("model") or "").strip() or None
    if not question:
        return jsonify({"error": "question erforderlich"}), 400

    if trace_only:
        dry = _snake_retrieval_dry_run(
            question,
            retrieval_config_overrides=retrieval_config_overrides,
            top_k=limits.rag_top_k,
        )
        return jsonify({"trace_only": True, "rag_why": dry}), 200

    rag_trace: dict[str, Any] = {}
    context = str(body.get("context") or "").strip()[:limits.context_chars]
    if context:
        grounded_prompt = f"{question}\n\nKontext:\n{context}"
        rag_trace["source"] = "client_provided"
        rag_trace["context_chars"] = len(context)
        if debug or retrieval_config_overrides:
            rag_trace["retrieval_profile"] = _resolve_snake_retrieval_profile_trace(
                question,
                retrieval_config_overrides=retrieval_config_overrides,
            )
    else:
        grounded_prompt, has_context, context_summary = _build_grounded_snake_prompt(
            question,
            limits=limits,
            retrieval_config_overrides=retrieval_config_overrides,
        )
        rag_trace["source"] = "hub_rag"
        rag_trace["has_context"] = has_context
        rag_trace["summary"] = context_summary
        if debug or retrieval_config_overrides:
            rag_trace["retrieval_profile"] = _resolve_snake_retrieval_profile_trace(
                question,
                retrieval_config_overrides=retrieval_config_overrides,
            )
    rag_trace["limits"] = {
        "context_chars": limits.context_chars,
        "answer_chars": limits.answer_chars,
        "max_tokens": limits.max_tokens,
        "rag_top_k": limits.rag_top_k,
    }

    provider, hub_model = _resolve_ai_snake_chat_provider()
    model = request_model or hub_model

    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import _is_full_scan_intent

        _eff_cfg = _current_config()
        _eff_cfg.update(dict(retrieval_config_overrides or {}))
        if _is_full_scan_intent(question, "", _eff_cfg):
            answer, worker_trace = _worker_chat_full_scan(question, provider=provider, model=model, limits=limits, cancel_key="snake_ask")
            if answer:
                files_found = worker_trace.get("files_found", 0)
                batches_done = worker_trace.get("batches_completed", 0)
                summary = f"full_scan: {batches_done} Batches, {files_found} Quelldateien"
                if len(answer) > limits.answer_chars:
                    answer = answer[:limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
                resp: dict[str, Any] = {"answer": answer, "path": "full_scan", "context_summary": summary}
                if debug:
                    resp["trace"] = {"rag": rag_trace, "worker": worker_trace}
                elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
                    resp["trace"] = {"rag": rag_trace}
                return jsonify(resp), 200
    except Exception as exc:
        logging.getLogger(__name__).debug("full_scan routing failed, falling back: %s", exc)

    answer, worker_trace = _worker_propose(
        grounded_prompt,
        model,
        limits=limits,
        retrieval_profile_trace=rag_trace.get("retrieval_profile") if isinstance(rag_trace.get("retrieval_profile"), dict) else None,
    )
    if answer:
        resp = {"answer": answer, "path": "worker"}
        if debug:
            resp["trace"] = {"rag": rag_trace, "worker": worker_trace}
        elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
            resp["trace"] = {"rag": rag_trace}
        return jsonify(resp), 200

    try:
        provider, _ = _resolve_ai_snake_chat_provider()
        timeout = min(int(getattr(settings, "http_timeout", 120) or 120), 180)
        raw = generate_text(
            prompt=grounded_prompt,
            provider=provider,
            model=model,
            history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
            max_output_tokens=limits.max_tokens,
            timeout=timeout,
        )
        text = str(raw or "").strip()
        if len(text) > limits.answer_chars:
            text = text[:limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
        if not text:
            return jsonify({"error": "Keine Antwort generiert"}), 503
        resp = {"answer": text, "path": "hub_direct"}
        if debug:
            resp["trace"] = {
                "rag": rag_trace,
                "worker": worker_trace,
                "fallback_reason": "worker_empty",
                "full_scan": {
                    "status": "not_run",
                    "reason": "hub_direct_fallback",
                    "analysis_mode": (rag_trace.get("retrieval_profile") or {}).get("analysis_mode"),
                },
            }
        elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
            resp["trace"] = {
                "rag": rag_trace,
                "fallback_reason": "worker_empty",
            }
        return jsonify(resp), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("snake-ask failed: %s", exc)
        return jsonify({"error": f"LLM-Fehler: {str(exc)[:120]}"}), 503
