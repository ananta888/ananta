import concurrent.futures as _cf
import logging
import pathlib as _pl
import threading
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _FullScanSettings:
    max_batches: int = 8
    files_per_batch: int = 3
    parallel_batches: int = 4
    timeout_s: int = 1800
    source_only: bool = True
    chars_per_file: int = 600
    max_input_tokens: int | None = None


def _full_scan_settings(cfg: dict[str, Any]) -> _FullScanSettings:
    try:
        source_only_val = cfg.get("chat_full_scan_source_only")
        try:
            chars_per_file = max(100, min(20000, int(float(cfg.get("chat_full_scan_chars_per_file") or 600))))
        except (TypeError, ValueError):
            chars_per_file = 600
        try:
            cfg_max_in = cfg.get("chat_full_scan_max_input_tokens")
            max_input_tokens = int(float(cfg_max_in)) if cfg_max_in not in (None, "") else None
        except (TypeError, ValueError):
            max_input_tokens = None
        return _FullScanSettings(
            max_batches=max(1, min(16, int(float(cfg.get("chat_full_scan_max_batches") or 8)))),
            files_per_batch=max(1, min(10, int(float(cfg.get("chat_full_scan_files_per_batch") or 3)))),
            parallel_batches=max(1, min(8, int(float(cfg.get("chat_full_scan_parallel_batches") or 4)))),
            timeout_s=max(60, min(7200, int(float(cfg.get("chat_full_scan_timeout_s") or 1800)))),
            source_only=source_only_val if isinstance(source_only_val, bool) else True,
            chars_per_file=chars_per_file,
            max_input_tokens=max_input_tokens,
        )
    except (TypeError, ValueError):
        return _FullScanSettings()


def _collect_source_files(repo_root: _pl.Path, exts: tuple[str, ...]) -> list[_pl.Path]:
    files: list[_pl.Path] = []
    for ext in exts:
        for f in repo_root.rglob(f"*{ext}"):
            if not any(part in _SKIP_DIRS for part in f.parts):
                files.append(f)
    return files


def _question_keywords(question: str) -> list[str]:
    return [w.lower() for w in question.replace("/", " ").split() if len(w) >= 3 and w.lower() not in _STOPWORDS]


class _FileRelevance:
    """Keyword relevance of a file (name, path and the first 2000 content chars)."""

    _CONTENT_PREFIX = 2000

    def __init__(self, repo_root: _pl.Path, keywords: list[str]) -> None:
        self._repo_root = repo_root
        self._keywords = keywords
        self._content_cache: dict[str, str] = {}

    def _read_prefix(self, f: _pl.Path) -> str:
        cached = self._content_cache.get(str(f))
        if cached is not None:
            return cached
        try:
            text = f.read_text(encoding="utf-8", errors="replace")[: self._CONTENT_PREFIX]
        except OSError:
            text = ""
        self._content_cache[str(f)] = text
        return text

    def score(self, f: _pl.Path) -> int:
        rel = str(f.relative_to(self._repo_root)).lower()
        name = f.name.lower()
        content = self._read_prefix(f).lower()
        s = 0
        for kw in self._keywords:
            if kw in name:
                s += 3
            if kw in rel:
                s += 2
            if kw in content:
                s += min(5, content.count(kw))
        return s


def _estimate_batch_tokens(batch: list, chars_per_file: int) -> int:
    framing_per_file = 40
    system_overhead = 400
    total_chars = system_overhead + sum(chars_per_file + framing_per_file for _ in batch)
    return max(1, total_chars // 4)


def _fit_files_per_batch(files: list, settings: _FullScanSettings, max_input_tokens: int) -> int:
    """Shrink the batch size until one batch fits the model's input budget."""
    files_per_batch = settings.files_per_batch
    while (
        files_per_batch > 1
        and _estimate_batch_tokens(files[:files_per_batch], settings.chars_per_file) > max_input_tokens
    ):
        files_per_batch -= 1
    return files_per_batch


def _empty_answer_metadata(answer: Any) -> dict[str, Any]:
    try:
        from agent.llm_integration import extract_llm_call_metadata
        meta = extract_llm_call_metadata(answer) if isinstance(answer, dict) else {}
    except Exception:
        return {}
    if not meta:
        return {}
    result: dict[str, Any] = {"empty_reason": meta.get("empty_reason")}
    if meta.get("context_limit"):
        result["context_limit"] = int(meta["context_limit"])
    if meta.get("model_id"):
        result["model_id"] = str(meta["model_id"])
    return result


@dataclass(frozen=True)
class _BatchAnalyzer:
    """Ask the model about one batch of source files."""

    question: str
    budget_instruction: str
    repo_root: _pl.Path
    total_batches: int
    chars_per_file: int
    provider: str
    model: str | None
    llm_history: list[dict[str, str]]
    timeout_s: int

    def _file_blocks(self, batch: list) -> list[str]:
        blocks: list[str] = []
        for f in batch:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[: self.chars_per_file]
                rel = str(f.relative_to(self.repo_root))
                lang = f.suffix.lstrip(".") or "text"
                blocks.append(f"### {rel}\n```{lang}\n{content}\n```")
            except OSError:
                pass
        return blocks

    def __call__(self, args: tuple[int, list]) -> tuple[int, str, str, dict[str, Any]]:
        step, batch = args
        file_blocks = self._file_blocks(batch)
        if not file_blocks:
            return step, "", "", {"error": "no_file_blocks"}
        file_labels = ", ".join(str(f.relative_to(self.repo_root)) for f in batch)
        batch_prompt = (
            f"Frage: {self.question}\n\n"
            + (f"{self.budget_instruction}\n\n" if self.budget_instruction else "")
            + f"Analysiere Quellcode-Batch {step}/{self.total_batches} [{file_labels}]:\n\n"
            + "\n\n".join(file_blocks)
            + "\n\nExtrahiere alle relevanten Erkenntnisse zur Frage aus diesem Quellcode-Batch."
            " Kurze, präzise Antwort."
        )
        try:
            answer = generate_text(
                prompt=batch_prompt,
                provider=self.provider,
                model=self.model,
                history=self.llm_history,
                timeout=self.timeout_s,
            )
            text = str(answer.get("text") or "").strip() if isinstance(answer, dict) else str(answer or "").strip()
            batch_meta: dict[str, Any] = {
                "estimated_input_tokens": _estimate_batch_tokens(batch, self.chars_per_file),
                "chars_in_prompt": len(batch_prompt),
            }
            if not text:
                batch_meta.update(_empty_answer_metadata(answer))
            return step, file_labels, text, batch_meta
        except Exception as exc:
            logging.getLogger(__name__).warning("full_scan batch %d failed: %s", step, exc, exc_info=False)
            return step, file_labels, "", {"error": str(exc), "error_type": type(exc).__name__}


def _run_batches(
    analyze: _BatchAnalyzer,
    batches: list[list],
    *,
    parallel_batches: int,
    cancel_event: threading.Event,
    cancel_key: str | None,
    trace: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    results: list = [None] * len(batches)
    batch_metas: list[dict[str, Any]] = []
    try:
        with _cf.ThreadPoolExecutor(max_workers=parallel_batches) as pool:
            futures = {pool.submit(analyze, (i + 1, b)): i for i, b in enumerate(batches)}
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
    summaries = [
        f"**Batch {step}** [{file_labels}]:\n{batch_answer}"
        for step, file_labels, batch_answer in (r for r in results if r)
        if batch_answer
    ]
    return summaries, batch_metas


def _record_empty_scan_error(trace: dict[str, Any], batch_metas: list[dict[str, Any]]) -> None:
    overflow_count = sum(1 for m in batch_metas if m.get("empty_reason") == "context_overflow_likely")
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


def _effective_max_input_tokens(model: str | None, settings: _FullScanSettings) -> tuple[int, int]:
    model_context_tokens = lookup_model_context_tokens(model) or 4096
    budget = max(model_context_tokens - 256, 256)
    if settings.max_input_tokens and settings.max_input_tokens > 0:
        budget = min(settings.max_input_tokens, budget)
    return model_context_tokens, budget


def _synthesize(
    question, budget_instruction, batch_summaries, selected, exts, *, provider, model, llm_history, timeout_s
):
    synthesis_prompt = (
        f"Ursprüngliche Frage: {question}\n\n"
        + (f"{budget_instruction}\n\n" if budget_instruction else "")
        + f"Quellcode-Analyse aus {len(batch_summaries)} Batches "
        f"({len(selected)} Dateien, nur {exts[0]}-Quellcode):\n\n"
        + "\n\n---\n\n".join(batch_summaries)
        + "\n\nErstelle eine vollständige, strukturierte Antwort basierend ausschließlich auf dem"
        " analysierten Quellcode."
    )
    try:
        final_answer = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=llm_history,
            timeout=timeout_s,
        )
        return str(final_answer or "").strip()
    except Exception as exc:
        logging.getLogger(__name__).warning("full_scan synthesis failed: %s", exc, exc_info=False)
        return ""


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

    scan_settings = _full_scan_settings(_current_config())
    budget_instruction = _answer_budget_instruction(limits)
    model_context_tokens, effective_max_input_tokens = _effective_max_input_tokens(model, scan_settings)

    llm_history = [{"role": "system", "content": _SNAKE_CHAT_PROMPT}, *list(conversation_history or [])]
    trace: dict[str, Any] = {
        "mode": "full_scan_chat",
        "conversation_history_messages": len(conversation_history or []),
        "model": model or "",
        "model_context_tokens": int(model_context_tokens),
        "max_input_tokens": int(effective_max_input_tokens),
        "chars_per_file_cfg": int(scan_settings.chars_per_file),
        "files_per_batch_cfg": int(scan_settings.files_per_batch),
    }

    repo_root = _resolve_repo_root()
    if not repo_root:
        trace["error"] = "no_repo_root"
        return "", trace

    exts = (".py", ".jsonl") if scan_settings.source_only else (".py", ".ts", ".jsonl")
    all_files = _collect_source_files(repo_root, exts)
    keywords = _question_keywords(question)
    relevance = _FileRelevance(repo_root, keywords)
    all_files.sort(key=lambda f: (-relevance.score(f), str(f.relative_to(repo_root))))

    trace["files_found"] = len(all_files)
    trace["ranking_keywords"] = list(keywords)
    if not all_files:
        trace["error"] = "no_source_files"
        return "", trace
    trace["ranking_top_files"] = [
        {"path": str(f.relative_to(repo_root)), "score": relevance.score(f)} for f in all_files[:5]
    ]
    trace["ranking_files_with_hits"] = sum(1 for f in all_files if relevance.score(f) > 0)

    files_per_batch = _fit_files_per_batch(all_files, scan_settings, effective_max_input_tokens)
    if files_per_batch < scan_settings.files_per_batch:
        trace["files_per_batch_auto_shrunk_from"] = int(scan_settings.files_per_batch)
        trace["files_per_batch_auto_shrunk_reason"] = "context_budget"

    selected = all_files[: scan_settings.max_batches * files_per_batch]
    batches = [selected[i:i + files_per_batch] for i in range(0, len(selected), files_per_batch)]
    trace["batches_planned"] = len(batches)
    trace["files_selected"] = len(selected)
    trace["files_per_batch_used"] = int(files_per_batch)
    trace["timeout_per_batch_s"] = scan_settings.timeout_s

    analyze = _BatchAnalyzer(
        question=question,
        budget_instruction=budget_instruction,
        repo_root=repo_root,
        total_batches=len(batches),
        chars_per_file=scan_settings.chars_per_file,
        provider=provider,
        model=model,
        llm_history=llm_history,
        timeout_s=scan_settings.timeout_s,
    )
    batch_summaries, batch_metas = _run_batches(
        analyze,
        batches,
        parallel_batches=scan_settings.parallel_batches,
        cancel_event=cancel_event,
        cancel_key=cancel_key,
        trace=trace,
    )
    trace["batches_completed"] = len(batch_summaries)
    trace["batch_metas"] = batch_metas
    if not batch_summaries:
        _record_empty_scan_error(trace, batch_metas)
        return "", trace

    final_answer = _synthesize(
        question,
        budget_instruction,
        batch_summaries,
        selected,
        exts,
        provider=provider,
        model=model,
        llm_history=llm_history,
        timeout_s=scan_settings.timeout_s,
    )
    return final_answer, trace
