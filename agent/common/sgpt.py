import contextlib
import hashlib
import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

from agent.config import settings
from agent.llm_integration import (
    _find_matching_lmstudio_candidate,
    _find_matching_ollama_candidate,
    probe_lmstudio_runtime,
    probe_ollama_runtime,
    resolve_ollama_model,
)
from agent.local_llm_backends import get_local_openai_backends, resolve_local_openai_backend
from agent.model_selection import normalize_legacy_model_name
from agent.research_backend import (
    RESEARCH_BACKEND_PROVIDERS,
    get_research_backend_preflight,
    is_research_backend,
    resolve_research_backend_config,
    run_research_backend_command,
)

_SEMAPHORE_LOCK = threading.Lock()
_BACKEND_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}
_DEFAULT_BACKEND_PARALLEL_LIMITS: dict[str, int] = {
    "sgpt": 4,
    "ananta-worker": 4,
    "codex": 4,
    "opencode": 4,
    "aider": 1,
    "mistral_code": 1,
}


@dataclass(frozen=True)
class _SemaphoreTicket:
    backend: str
    acquired: bool
    limit: int


def _resolve_backend_parallel_limit(backend: str) -> int:
    agent_cfg = _get_agent_config()
    routing_cfg = dict(agent_cfg.get("sgpt_routing") or {})
    backend_limits = dict(routing_cfg.get("backend_parallel_limits") or {})
    configured = backend_limits.get(backend)
    if configured is None:
        configured = _DEFAULT_BACKEND_PARALLEL_LIMITS.get(backend, 1)
    try:
        return max(1, min(int(configured), 16))
    except Exception:
        return 1


def _get_backend_semaphore(backend: str, limit: int) -> threading.BoundedSemaphore:
    key = f"{backend}:{limit}"
    with _SEMAPHORE_LOCK:
        sem = _BACKEND_SEMAPHORES.get(key)
        if sem is None:
            sem = threading.BoundedSemaphore(limit)
            _BACKEND_SEMAPHORES[key] = sem
        return sem


@contextlib.contextmanager
def _acquire_backend_permit(backend: str, *, timeout: int):
    limit = _resolve_backend_parallel_limit(backend)
    sem = _get_backend_semaphore(backend, limit)
    acquired = sem.acquire(timeout=max(1, int(timeout)))
    if not acquired:
        logging.warning("Backend semaphore exhausted backend=%s limit=%s timeout=%ss", backend, limit, timeout)
        yield _SemaphoreTicket(backend=backend, acquired=False, limit=limit)
        return
    try:
        yield _SemaphoreTicket(backend=backend, acquired=True, limit=limit)
    finally:
        sem.release()

SUPPORTED_CLI_BACKENDS = {"sgpt", "ananta-worker", "codex", "opencode", "aider", "mistral_code", *RESEARCH_BACKEND_PROVIDERS}
CLI_BACKEND_INSTALL_HINTS = {
    "sgpt": "python -m pip install shell-gpt",
    "ananta-worker": "python -m pip install shell-gpt",
    "codex": "npm i -g @openai/codex",
    "opencode": "npm i -g opencode-ai",
    "aider": "python -m pip install aider-chat",
    "mistral_code": "npm i -g mistral-code",
    "deerflow": "Clone deer-flow and configure research_backend.command plus research_backend.working_dir.",
    "ananta_research": "Install or clone ananta_research and configure research_backend.command plus research_backend.working_dir.",
}
CLI_BACKEND_VERIFY_COMMANDS = {
    "sgpt": "python -m sgpt --help",
    "ananta-worker": "python -m sgpt --help",
    "codex": "codex --help",
    "opencode": "opencode --help",
    "aider": "aider --help",
    "mistral_code": "mistral-code --help",
    "deerflow": "python main.py --help",
    "ananta_research": "configure research_backend.command",
}
CLI_BACKEND_CAPABILITIES = {
    "sgpt": {
        "display_name": "ShellGPT",
        "supports_model": True,
        "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "ananta-worker": {
        "display_name": "Ananta Worker (internal)",
        "supports_model": True,
        "supported_flags": ["--shell", "--md", "--no-interaction", "--cache", "--no-cache"],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "codex": {
        "display_name": "OpenAI Codex CLI",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "opencode": {
        "display_name": "OpenCode",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "aider": {
        "display_name": "Aider",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "mistral_code": {
        "display_name": "Mistral Code",
        "supports_model": True,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "deerflow": {
        "display_name": "DeerFlow",
        "supports_model": False,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "ananta_research": {
        "display_name": "ananta_research",
        "supports_model": False,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
    "browser_use": {
        "display_name": "browser_use",
        "supports_model": False,
        "supported_flags": [],
        "supports_temperature": False,
        "supports_top_p": False,
    },
}

_BACKEND_RUNTIME: dict[str, dict] = {
    name: {
        "last_success_at": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "cooldown_until": 0.0,
        "total_success": 0,
        "total_failures": 0,
        "last_error": "",
        "last_rc": None,
        "last_latency_ms": None,
    }
    for name in SUPPORTED_CLI_BACKENDS
}


def _resolve_backend_binary(backend: str) -> str | None:
    if is_research_backend(backend):
        return resolve_research_backend_config(provider_override=backend).get("binary_path")
    if backend in {"sgpt", "ananta-worker"}:
        return sys.executable if sys.executable else None
    if backend == "codex":
        return shutil.which(settings.codex_path or "codex")
    if backend == "opencode":
        return shutil.which(settings.opencode_path or "opencode")
    if backend == "aider":
        return shutil.which(settings.aider_path or "aider")
    if backend == "mistral_code":
        return shutil.which(settings.mistral_code_path or "mistral-code")
    return None


def _configured_backend_command(backend: str) -> str:
    if is_research_backend(backend):
        return str(resolve_research_backend_config(provider_override=backend).get("command") or "")
    if backend in {"sgpt", "ananta-worker"}:
        return f"{sys.executable} -m sgpt" if sys.executable else "python -m sgpt"
    if backend == "codex":
        return settings.codex_path or "codex"
    if backend == "opencode":
        return settings.opencode_path or "opencode"
    if backend == "aider":
        return settings.aider_path or "aider"
    if backend == "mistral_code":
        return settings.mistral_code_path or "mistral-code"
    return ""


def _classify_runtime_target(url: str | None) -> str | None:
    raw = (url or "").strip().lower()
    if not raw:
        return None
    if "host.docker.internal" in raw:
        return "docker_host"
    if "localhost" in raw or "127.0.0.1" in raw:
        return "loopback"
    if any(marker in raw for marker in ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        return "private_network"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "remote"
    return "unknown"


def _health_score(backend: str) -> int:
    rt = _BACKEND_RUNTIME.get(backend, {})
    score = 100
    if not _resolve_backend_binary(backend):
        score -= 80
    score -= min(40, int(rt.get("consecutive_failures", 0)) * 10)
    cooldown_until = float(rt.get("cooldown_until") or 0.0)
    if cooldown_until > time.time():
        score -= 20
    if rt.get("last_latency_ms") and rt["last_latency_ms"] > 30000:
        score -= 10
    return max(0, min(100, score))


def get_cli_backend_runtime_status() -> dict[str, dict]:
    now = time.time()
    data: dict[str, dict] = {}
    for name in sorted(SUPPORTED_CLI_BACKENDS):
        rt = dict(_BACKEND_RUNTIME.get(name, {}))
        cooldown_until = float(rt.get("cooldown_until") or 0.0)
        runtime_entry = {
            "binary_path": _resolve_backend_binary(name),
            "binary_available": bool(_resolve_backend_binary(name)),
            "health_score": _health_score(name),
            "cooldown_active": cooldown_until > now,
            "cooldown_until": cooldown_until,
            **rt,
        }
        if name == "codex":
            codex_runtime = resolve_codex_runtime_config()
            runtime_entry["target_base_url"] = codex_runtime["base_url"]
            runtime_entry["target_provider"] = codex_runtime["target_provider"]
            runtime_entry["target_base_url_source"] = codex_runtime["base_url_source"]
            runtime_entry["target_is_local"] = codex_runtime["is_local"]
            runtime_entry["target_kind"] = codex_runtime.get("target_kind")
            runtime_entry["target_provider_type"] = codex_runtime.get("target_provider_type")
            runtime_entry["remote_hub"] = bool(codex_runtime.get("remote_hub"))
            runtime_entry["instance_id"] = codex_runtime.get("instance_id")
            runtime_entry["max_hops"] = codex_runtime.get("max_hops")
            runtime_entry["api_key_configured"] = bool(codex_runtime["api_key"])
            runtime_entry["api_key_source"] = codex_runtime["api_key_source"]
            runtime_entry["prefer_lmstudio"] = codex_runtime["prefer_lmstudio"]
            runtime_entry["diagnostics"] = list(codex_runtime.get("diagnostics") or [])
        if name == "opencode":
            opencode_runtime = resolve_opencode_runtime_config()
            runtime_entry["target_base_url"] = opencode_runtime.get("base_url")
            runtime_entry["target_provider"] = opencode_runtime.get("target_provider")
            runtime_entry["target_base_url_source"] = opencode_runtime.get("base_url_source")
            runtime_entry["target_kind"] = opencode_runtime.get("target_kind")
            runtime_entry["target_provider_type"] = opencode_runtime.get("target_provider_type")
            runtime_entry["diagnostics"] = list(opencode_runtime.get("diagnostics") or [])
        data[name] = runtime_entry
    return data


def get_cli_backend_preflight(*, runtime_scope: str = "full") -> dict[str, dict]:
    scope = str(runtime_scope or "full").strip().lower() or "full"
    worker_scope = scope in {"worker", "worker_only", "execution"}
    provider_urls = _get_runtime_provider_urls()
    lmstudio_base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
    from agent.llm_integration import _normalize_ollama_base_url

    ollama_base_url = _normalize_ollama_base_url(provider_urls.get("ollama") or getattr(settings, "ollama_url", None))
    codex_runtime = resolve_codex_runtime_config()
    agent_cfg = _get_agent_config()

    cli_backends: dict[str, dict] = {}
    for name in sorted(SUPPORTED_CLI_BACKENDS):
        resolved = _resolve_backend_binary(name)
        cli_backends[name] = {
            "command": _configured_backend_command(name),
            "binary_path": resolved,
            "binary_available": bool(resolved),
            "install_hint": CLI_BACKEND_INSTALL_HINTS.get(name),
            "verify_command": CLI_BACKEND_VERIFY_COMMANDS.get(name),
        }

    lmstudio_probe = {
        "ok": False,
        "status": "not_configured" if not lmstudio_base_url else "unknown",
        "models_url": f"{lmstudio_base_url}/models" if lmstudio_base_url else None,
        "candidate_count": 0,
        "candidates": [],
    }
    if lmstudio_base_url and not worker_scope:
        from agent.llm_integration import probe_lmstudio_runtime

        try:
            lmstudio_probe = probe_lmstudio_runtime(
                lmstudio_base_url,
                timeout=min(getattr(settings, "http_timeout", 5.0), 2.0),
            )
        except Exception:
            lmstudio_probe = {
                "ok": False,
                "status": "error",
                "models_url": f"{lmstudio_base_url}/models",
                "candidate_count": 0,
                "candidates": [],
            }

    ollama_probe = {
        "ok": False,
        "status": "not_configured" if not ollama_base_url else "unknown",
        "tags_url": f"{ollama_base_url}/api/tags" if ollama_base_url else None,
        "candidate_count": 0,
        "models": [],
    }
    ollama_activity = {
        "ok": False,
        "status": "not_configured" if not ollama_base_url else "unknown",
        "ps_url": f"{ollama_base_url}/api/ps" if ollama_base_url else None,
        "active_count": 0,
        "gpu_active": False,
        "executor_summary": {"gpu": 0, "cpu": 0, "unknown": 0},
        "active_models": [],
    }
    if ollama_base_url and not worker_scope:
        from agent.llm_integration import probe_ollama_activity, probe_ollama_runtime

        try:
            ollama_probe = probe_ollama_runtime(
                ollama_base_url,
                timeout=min(getattr(settings, "http_timeout", 5.0), 2.0),
            )
        except Exception:
            ollama_probe = {
                "ok": False,
                "status": "error",
                "tags_url": f"{ollama_base_url}/api/tags",
                "candidate_count": 0,
                "models": [],
            }
        try:
            ollama_activity = probe_ollama_activity(
                ollama_base_url,
                timeout=min(getattr(settings, "http_timeout", 5.0), 2.0),
            )
        except Exception:
            ollama_activity = {
                "ok": False,
                "status": "error",
                "ps_url": f"{ollama_base_url}/api/ps",
                "active_count": 0,
                "gpu_active": False,
                "executor_summary": {"gpu": 0, "cpu": 0, "unknown": 0},
                "active_models": [],
            }

    local_provider_entries = []
    for backend in get_local_openai_backends(
        agent_cfg=agent_cfg,
        provider_urls=provider_urls,
        default_provider=_get_runtime_default_provider(),
        default_model=str(agent_cfg.get("default_model") or ""),
    ):
        local_provider_entries.append(
            {
                "provider": backend["provider"],
                "name": backend["name"],
                "base_url": backend.get("base_url"),
                "supports_tool_calls": bool(backend.get("supports_tool_calls")),
                "transport_provider": backend.get("transport_provider"),
                "api_key_profile": backend.get("api_key_profile"),
                "provider_type": backend.get("provider_type") or "local_openai_compatible",
                "remote_hub": bool(backend.get("remote_hub")),
                "instance_id": backend.get("instance_id"),
                "max_hops": backend.get("max_hops"),
            }
        )

    return {
        "cli_backends": cli_backends,
        "research_backends": get_research_backend_preflight(),
        "providers": {
            "lmstudio": {
                "configured": bool(lmstudio_base_url),
                "base_url": lmstudio_base_url,
                "host_kind": _classify_runtime_target(lmstudio_base_url),
                "is_local": _is_probably_local_base_url(lmstudio_base_url),
                "status": lmstudio_probe.get("status"),
                "reachable": bool(lmstudio_probe.get("ok")),
                "models_url": lmstudio_probe.get("models_url"),
                "candidate_count": int(lmstudio_probe.get("candidate_count") or 0),
                "candidates": list(lmstudio_probe.get("candidates") or []),
                "runtime_scope": scope,
                "probe_skipped": bool(worker_scope),
            },
            "ollama": {
                "configured": bool(ollama_base_url),
                "base_url": ollama_base_url,
                "host_kind": _classify_runtime_target(ollama_base_url),
                "is_local": _is_probably_local_base_url(ollama_base_url),
                "status": ollama_probe.get("status"),
                "reachable": bool(ollama_probe.get("ok")),
                "tags_url": ollama_probe.get("tags_url"),
                "candidate_count": int(ollama_probe.get("candidate_count") or 0),
                "models": list(ollama_probe.get("models") or []),
                "activity": {
                    "status": ollama_activity.get("status"),
                    "reachable": bool(ollama_activity.get("ok")),
                    "ps_url": ollama_activity.get("ps_url"),
                    "active_count": int(ollama_activity.get("active_count") or 0),
                    "gpu_active": bool(ollama_activity.get("gpu_active")),
                    "executor_summary": dict(ollama_activity.get("executor_summary") or {"gpu": 0, "cpu": 0, "unknown": 0}),
                    "active_models": list(ollama_activity.get("active_models") or []),
                },
                "runtime_scope": scope,
                "probe_skipped": bool(worker_scope),
            },
            "codex": {
                "configured": bool(codex_runtime.get("base_url")),
                "base_url": codex_runtime.get("base_url"),
                "target_provider": codex_runtime.get("target_provider"),
                "base_url_source": codex_runtime.get("base_url_source"),
                "host_kind": _classify_runtime_target(codex_runtime.get("base_url")),
                "is_local": bool(codex_runtime.get("is_local")),
                "api_key_configured": bool(codex_runtime.get("api_key")),
                "api_key_source": codex_runtime.get("api_key_source"),
                "prefer_lmstudio": bool(codex_runtime.get("prefer_lmstudio")),
                "target_kind": codex_runtime.get("target_kind"),
                "target_provider_type": codex_runtime.get("target_provider_type"),
                "remote_hub": bool(codex_runtime.get("remote_hub")),
                "instance_id": codex_runtime.get("instance_id"),
                "max_hops": codex_runtime.get("max_hops"),
                "diagnostics": list(codex_runtime.get("diagnostics") or []),
            },
            "local_openai": local_provider_entries,
        },
    }


def get_cli_backend_capabilities() -> dict[str, dict]:
    return {k: dict(v) for k, v in CLI_BACKEND_CAPABILITIES.items()}


def _prioritize_code_backends(candidates: list[str]) -> list[str]:
    code_pref = ["ananta-worker", "sgpt", "codex", "aider", "opencode", "mistral_code", "deerflow", "ananta_research", "browser_use"]
    ordered = [c for c in code_pref if c in candidates]
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _split_cooldown_candidates(candidates: list[str], now: float) -> tuple[list[str], list[str]]:
    active: list[str] = []
    cooled: list[str] = []
    for candidate in candidates:
        until = float(_BACKEND_RUNTIME.get(candidate, {}).get("cooldown_until") or 0.0)
        if until > now and len(candidates) > 1:
            cooled.append(candidate)
        else:
            active.append(candidate)
    return active, cooled


def _choose_candidates(
    requested: str,
    prompt: str,
    routing_policy: dict | None = None,
) -> list[str]:
    policy = routing_policy or {}
    allowed = [b for b in (policy.get("allowed_backends") or []) if b in SUPPORTED_CLI_BACKENDS]
    if requested == "auto":
        preferred = (settings.sgpt_execution_backend or "ananta-worker").strip().lower()
        if preferred == "auto" or preferred not in SUPPORTED_CLI_BACKENDS:
            preferred = "ananta-worker"
        candidates = [preferred]
        for name in sorted(SUPPORTED_CLI_BACKENDS):
            if name not in candidates:
                candidates.append(name)
    else:
        candidates = [requested]

    if allowed:
        candidates = [c for c in candidates if c in allowed]

    p = (prompt or "").lower()
    code_like = any(k in p for k in ["refactor", "code", "patch", "test", "bug", "fix"])
    if code_like:
        candidates = _prioritize_code_backends(candidates)

    active, cooled = _split_cooldown_candidates(candidates, time.time())
    return active + cooled


def normalize_backend_flags(backend: str, options: list | None) -> tuple[list[str], list[str]]:
    """
    Gibt (valid_flags, rejected_flags) für das gewählte Backend zurück.
    """
    requested = backend.strip().lower()
    if requested not in CLI_BACKEND_CAPABILITIES:
        return [], options or []
    supported = set(CLI_BACKEND_CAPABILITIES[requested]["supported_flags"])
    valid = []
    rejected = []
    for opt in options or []:
        if opt in supported:
            valid.append(opt)
        else:
            rejected.append(opt)
    return valid, rejected


_EXT_LANG: dict[str, str] = {
    "py": "python", "ts": "typescript", "tsx": "typescript",
    "js": "javascript", "jsx": "javascript",
    "yaml": "yaml", "yml": "yaml", "json": "json",
    "md": "markdown", "html": "html", "css": "css",
    "sh": "bash", "bash": "bash",
}

# CCSH-004: Accepted alias names for line-range and snippet fields
_LR_START_ALIASES: tuple[str, ...] = ("start_line", "line_start", "start", "from_line")
_LR_END_ALIASES: tuple[str, ...] = ("end_line", "line_end", "end", "to_line")
_SNIPPET_FIELD_ALIASES: tuple[str, ...] = ("snippet", "content", "excerpt")

_MAX_LINE_SPAN: int = 5000   # refuse spans wider than this
_MAX_LINE_WINDOW: int = 200  # cap context_lines to this


def _get_ref_alias(ref: dict, aliases: tuple[str, ...]) -> object:
    for k in aliases:
        v = ref.get(k)
        if v is not None:
            return v
    return None


def _normalize_line_range(ref: dict) -> "tuple[int, int] | None":
    start = _get_ref_alias(ref, _LR_START_ALIASES)
    end = _get_ref_alias(ref, _LR_END_ALIASES)
    if start is None or end is None:
        return None
    try:
        s, e = int(start), int(end)
    except (TypeError, ValueError):
        return None
    if s < 1 or e < s or (e - s) > _MAX_LINE_SPAN:
        return None
    return (s, e)


def _read_line_window(
    full_path: pathlib.Path,
    start: int,
    end: int,
    context_lines: int,
    per_file_chars: int,
) -> "tuple[str, int, int]":
    """Read lines [start..end] + context_lines margin from file (1-indexed).

    Returns (content, actual_start_line, actual_end_line), or ("", 0, 0) on failure.
    """
    try:
        raw = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0, 0
    lines = raw.splitlines()
    total = len(lines)
    if total == 0:
        return "", 0, 0
    context_lines = max(0, min(context_lines, _MAX_LINE_WINDOW))
    lo = max(0, start - 1 - context_lines)
    hi = min(total, end + context_lines)
    excerpt = "\n".join(lines[lo:hi])
    if len(excerpt) > per_file_chars:
        excerpt = excerpt[:per_file_chars].rstrip() + "\n# [… gekürzt]"
    return excerpt, lo + 1, min(hi, total)


def _get_worker_context_cfg() -> dict:
    """Return ananta_worker_context_* settings from AGENT_CONFIG or settings."""
    agent_cfg = _get_agent_config() if has_app_context() else {}
    ctx_cfg = dict(agent_cfg.get("ananta_worker_context") or {})
    return ctx_cfg


def _bounded_worker_int(key: str, default: int, lo: int, hi: int) -> int:
    ctx_cfg = _get_worker_context_cfg()
    raw = ctx_cfg.get(key)
    if raw is not None:
        try:
            return max(lo, min(hi, int(raw)))
        except (TypeError, ValueError):
            pass
    return max(lo, min(hi, getattr(settings, key, default)))


def _resolve_repo_root() -> pathlib.Path | None:
    """Return the configured project/repo root generically via settings.rag_repo_root."""
    if has_app_context():
        raw = str(getattr(settings, "rag_repo_root", "") or "").strip()
        if raw and raw != ".":
            p = pathlib.Path(raw)
            if p.is_dir():
                return p.resolve()
    # Fallback: /app (Docker) or cwd
    for candidate in (pathlib.Path("/app"), pathlib.Path.cwd()):
        if candidate.is_dir() and (candidate / "agent").is_dir():
            return candidate.resolve()
    return None


def _load_source_file_batches(
    workdir: str | None,
    *,
    files_per_batch: int = 3,
    per_file_chars: int = 4_000,
    max_files: int = 30,
    context_lines: int = 5,
    max_snippet_chars: int = 8_000,
) -> "list[list[dict]]":
    """Load relevant source files from workspace and split into batches.

    Reads rag_helper/research-context.json for repo_scope_refs, resolves each
    ref against settings.rag_repo_root using this priority (CCSH-004/005):
      1. path + start_line/end_line  → read line-range window from file
      2. ref.chunks[]                → use pre-built chunk content blocks
      3. path only                   → read file beginning (legacy fallback)
      4. snippet without valid path  → embed snippet text directly
      5. .ananta/hub-context.md      → when no refs are usable at all

    Returns batches of context-block dicts with keys:
      rel_path, lang, content, source_kind, start_line, end_line, score, reason, symbol
    """
    batches: list[list[dict]] = []
    if not workdir:
        return batches
    root = pathlib.Path(workdir)
    if not root.is_dir():
        return batches

    repo_root = _resolve_repo_root()
    research_json = root / "rag_helper" / "research-context.json"

    blocks: list[dict] = []
    seen_keys: set[str] = set()  # dedup by (rel_path, start, end[, content_hash]) — CCSH-005

    def _dedup_key(rel: str, s: "int | None", e: "int | None", content: str = "") -> str:
        # When both line coords are None (e.g. chunks without metadata), include a
        # content hash so two different chunks from the same source are not collapsed.
        if s is None and e is None and content:
            import hashlib
            suffix = hashlib.md5(content[:200].encode(), usedforsecurity=False).hexdigest()[:8]
            return f"{rel}:h:{suffix}"
        return f"{rel}:{s}:{e}"

    if research_json.exists() and repo_root is not None:
        try:
            data = json.loads(research_json.read_text(encoding="utf-8", errors="replace"))
            profile = dict(data.get("retrieval_profile") or {})
            full_scan = str(profile.get("analysis_mode") or data.get("analysis_mode") or "").strip() == "architecture_full_scan"
            architecture_scope = dict(data.get("architecture_scope") or {})
            raw_refs = architecture_scope.get("refs") if full_scan and architecture_scope.get("refs") else data.get("repo_scope_refs")
            refs = [dict(r or {}) for r in list(raw_refs or []) if r]
            resolved_root = repo_root.resolve()

            for ref in refs:
                rel_path = str(ref.get("path") or "").strip()
                score_raw = ref.get("score")
                score = float(score_raw) if score_raw is not None else None
                reason = str(ref.get("reason") or "").strip() or None
                symbol = str(ref.get("symbol") or "").strip() or None
                snippet_raw = _get_ref_alias(ref, _SNIPPET_FIELD_ALIASES)
                line_range = _normalize_line_range(ref)

                # Resolve path safely against rag_repo_root
                full: pathlib.Path | None = None
                if rel_path:
                    try:
                        candidate = (repo_root / rel_path).resolve()
                        candidate.relative_to(resolved_root)
                        if candidate.is_file():
                            full = candidate
                    except (ValueError, OSError):
                        pass

                # Priority 1: path + line-range → read window from current file
                if full is not None and line_range is not None:
                    content, actual_start, actual_end = _read_line_window(
                        full, line_range[0], line_range[1], context_lines, per_file_chars
                    )
                    if content:
                        dk = _dedup_key(rel_path, actual_start, actual_end)
                        if dk not in seen_keys:
                            seen_keys.add(dk)
                            lang = _EXT_LANG.get(full.suffix.lstrip("."), full.suffix.lstrip(".") or "text")
                            blocks.append({
                                "rel_path": rel_path,
                                "lang": lang,
                                "content": content,
                                "source_kind": "line_range",
                                "start_line": actual_start,
                                "end_line": actual_end,
                                "score": score,
                                "reason": reason,
                                "symbol": symbol,
                            })
                        continue

                # Priority 2: ref.chunks[] — use embedded chunk content
                ref_chunks = [dict(c or {}) for c in list(ref.get("chunks") or []) if c]
                if ref_chunks:
                    for chunk in ref_chunks:
                        chunk_content = str(chunk.get("content") or chunk.get("excerpt") or "").strip()
                        if not chunk_content:
                            continue
                        chunk_source = str(chunk.get("source") or rel_path or "").strip()
                        chunk_meta = dict(chunk.get("metadata") or {})
                        c_start = chunk_meta.get("start_line")
                        c_end = chunk_meta.get("end_line")
                        try:
                            c_start = int(c_start) if c_start is not None else None
                            c_end = int(c_end) if c_end is not None else None
                        except (TypeError, ValueError):
                            c_start = c_end = None
                        dk = _dedup_key(chunk_source, c_start, c_end, chunk_content)
                        if dk in seen_keys:
                            continue
                        seen_keys.add(dk)
                        ext = pathlib.Path(chunk_source).suffix.lstrip(".")
                        lang = _EXT_LANG.get(ext, ext or "text")
                        c_score_raw = chunk.get("score")
                        c_score = float(c_score_raw) if c_score_raw is not None else score
                        chunk_content_clipped = chunk_content[:per_file_chars]
                        if len(chunk_content) > per_file_chars:
                            chunk_content_clipped = chunk_content_clipped.rstrip() + "\n# [… gekürzt]"
                        blocks.append({
                            "rel_path": chunk_source,
                            "lang": lang,
                            "content": chunk_content_clipped,
                            "source_kind": "chunk",
                            "start_line": c_start,
                            "end_line": c_end,
                            "score": c_score,
                            "reason": reason,
                            "symbol": symbol,
                        })
                    continue

                # Priority 3: path only → file beginning (legacy fallback)
                if full is not None:
                    try:
                        raw = full.read_text(encoding="utf-8", errors="replace").strip()
                    except OSError:
                        raw = ""
                    if raw:
                        dk = _dedup_key(rel_path, None, None)
                        if dk not in seen_keys:
                            seen_keys.add(dk)
                            content = raw[:per_file_chars]
                            if len(raw) > per_file_chars:
                                content = content.rstrip() + "\n# [… gekürzt]"
                            lang = _EXT_LANG.get(full.suffix.lstrip("."), full.suffix.lstrip(".") or "text")
                            blocks.append({
                                "rel_path": rel_path,
                                "lang": lang,
                                "content": content,
                                "source_kind": "file_excerpt",
                                "start_line": None,
                                "end_line": None,
                                "score": score,
                                "reason": reason,
                                "symbol": symbol,
                            })
                        continue

                # Priority 4: snippet without valid path
                if snippet_raw:
                    snippet_text = str(snippet_raw).strip()[:max_snippet_chars]
                    if snippet_text:
                        s_start = line_range[0] if line_range else None
                        s_end = line_range[1] if line_range else None
                        dk = _dedup_key(rel_path or "(snippet)", s_start, s_end)
                        if dk not in seen_keys:
                            seen_keys.add(dk)
                            ext = pathlib.Path(rel_path).suffix.lstrip(".") if rel_path else ""
                            lang = _EXT_LANG.get(ext, ext or "text")
                            blocks.append({
                                "rel_path": rel_path or "(codecompass_snippet)",
                                "lang": lang,
                                "content": snippet_text,
                                "source_kind": "codecompass_snippet",
                                "start_line": s_start,
                                "end_line": s_end,
                                "score": score,
                                "reason": reason,
                                "symbol": symbol,
                            })
        except Exception:
            pass

    # Sort by score descending (stable, keeps insertion order when score is None/equal)
    blocks.sort(key=lambda b: -(b["score"] or 0.0))

    # Apply max_files budget AFTER sorting so highest-scored blocks survive (CCSH-013)
    if len(blocks) > max_files:
        omitted = len(blocks) - max_files
        logging.debug(
            "ananta-worker context budget: keeping top %s/%s blocks, omitting %s lower-scored",
            max_files, len(blocks), omitted,
        )
        blocks = blocks[:max_files]

    # Split into batches
    for i in range(0, len(blocks), files_per_batch):
        batches.append(blocks[i : i + files_per_batch])

    # Priority 5: hub-context.md fallback when nothing else loaded
    if not batches:
        hub_path = root / ".ananta" / "hub-context.md"
        if hub_path.exists():
            try:
                content = hub_path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    batches.append([{
                        "rel_path": "hub-context.md",
                        "lang": "markdown",
                        "content": content[:12_000],
                        "source_kind": "hub_context",
                        "start_line": None,
                        "end_line": None,
                        "score": None,
                        "reason": None,
                        "symbol": None,
                    }])
            except OSError:
                pass

    return batches


def _format_block_header(block: dict) -> str:
    """Build the ### header for a context block (CCSH-002)."""
    rel_path = block.get("rel_path") or ""
    source_kind = block.get("source_kind") or "file_excerpt"
    start_line = block.get("start_line")
    end_line = block.get("end_line")
    score = block.get("score")
    symbol = block.get("symbol")

    # path:start-end when line range is known
    if start_line is not None and end_line is not None:
        location = f"{rel_path}:{start_line}-{end_line}"
    else:
        location = rel_path

    # annotation tag
    tag_parts = [source_kind]
    if symbol:
        tag_parts.append(f"symbol={symbol}")
    if score is not None:
        tag_parts.append(f"score={score:.2f}")
    tag = " ".join(tag_parts)
    return f"### {location} [{tag}]"


def _build_iteration_prompt(
    original_prompt: str,
    *,
    batch: "list[dict]",
    progress_so_far: str,
    step: int,
    total_steps: int,
    is_synthesis: bool = False,
) -> str:
    """Assemble the prompt for one iteration step of the ananta-worker loop."""
    parts: list[str] = [original_prompt.rstrip(), "\n\n---\n\n"]

    if progress_so_far:
        # Keep progress concise — truncate oldest content if very large
        prog = progress_so_far if len(progress_so_far) <= 6_000 else "…\n" + progress_so_far[-6_000:]
        parts.append(f"**Bisheriger Arbeitsfortschritt:**\n\n{prog}\n\n---\n\n")

    if is_synthesis:
        parts.append(
            "Alle relevanten Quelldateien wurden analysiert. "
            "Erstelle jetzt das vollständige, abschließende Ergebnis "
            "basierend auf dem gesamten Arbeitsfortschritt oben. "
            "Antworte direkt ohne weitere Schritte anzukündigen."
        )
    else:
        if not progress_so_far:
            header = (
                f"**Schritt {step}/{total_steps}** — "
                "Analysiere die folgenden Quelldateien und halte deine "
                "Teilergebnisse und Erkenntnisse strukturiert fest. "
                "Antworte nur mit deinem Fortschritt, noch nicht dem Endergebnis."
            )
        else:
            header = (
                f"**Schritt {step}/{total_steps}** — "
                "Analysiere die weiteren Quelldateien und ergänze deinen Fortschritt."
            )
        parts.append(header + "\n\n")
        for block in batch:
            h = _format_block_header(block)
            lang = block.get("lang") or "text"
            content = block.get("content") or ""
            parts.append(f"{h}\n```{lang}\n{content}\n```\n\n")

    return "".join(parts)


def _read_research_context(workdir: str | None) -> dict:
    if not workdir:
        return {}
    path = pathlib.Path(workdir) / "rag_helper" / "research-context.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return dict(data or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_architecture_full_scan_context(ctx: dict) -> bool:
    profile = dict((ctx or {}).get("retrieval_profile") or {})
    return str(profile.get("analysis_mode") or (ctx or {}).get("analysis_mode") or "").strip() == "architecture_full_scan"


def _write_json(path: pathlib.Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def _summary_empty(plan: dict) -> dict:
    return {
        "schema": "architecture_analysis_summary.v1",
        "status": "in_progress",
        "plan_id": plan.get("plan_id"),
        "components": [],
        "edges": [],
        "entrypoints": [],
        "data_flows": [],
        "security_boundaries": [],
        "configuration_points": [],
        "runtime_paths": [],
        "open_questions": [],
        "source_evidence": [],
        "coverage": {
            "planned_refs": int((plan.get("coverage") or {}).get("planned_refs") or 0),
            "processed_refs": 0,
            "omitted_refs": int((plan.get("coverage") or {}).get("excluded_refs") or 0),
            "processed_source_kinds": {},
            "omitted_reasons": {},
        },
    }


def _extract_json_object(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
        return dict(obj) if isinstance(obj, dict) else None
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start:end + 1])
                return dict(obj) if isinstance(obj, dict) else None
            except Exception:
                return None
    return None


def _append_unique(rows: list, incoming: list, key_fields: tuple[str, ...]) -> None:
    def _row_key(item: Any) -> tuple[str, ...]:
        row = dict(item or {}) if isinstance(item, dict) else {"value": str(item)}
        key = tuple(str(row.get(k) or "") for k in key_fields)
        if not any(key):
            key = tuple(str(row.get("value") or "") for _ in key_fields)
        return key

    seen = {_row_key(row) for row in rows}
    for item in incoming:
        row = dict(item or {}) if isinstance(item, dict) else {"value": str(item)}
        key = _row_key(row)
        if key not in seen:
            seen.add(key)
            rows.append(row)


def _merge_batch_analysis(summary: dict, batch_result: dict | None, *, batch: list[dict], raw_output: str) -> dict:
    result = dict(batch_result or {})
    if result.get("schema") != "architecture_batch_analysis.v1":
        result = {
            "schema": "architecture_batch_analysis.v1",
            "status": "degraded",
            "analyzed_refs": [
                {"path": b.get("rel_path"), "source_kind": b.get("source_kind"), "start_line": b.get("start_line"), "end_line": b.get("end_line")}
                for b in batch
            ],
            "source_evidence": [
                {"source": b.get("rel_path"), "source_kind": b.get("source_kind"), "note": str(raw_output or "")[:500]}
                for b in batch
            ],
            "unresolved_questions": ["batch_output_not_valid_json"],
        }
        summary["status"] = "degraded"

    _append_unique(summary["components"], list(result.get("components") or []), ("name", "source"))
    _append_unique(summary["edges"], list(result.get("edges") or []), ("from", "to", "relation"))
    _append_unique(summary["entrypoints"], list(result.get("entrypoints") or []), ("path", "symbol"))
    _append_unique(summary["data_flows"], list(result.get("data_flows") or []), ("from", "to", "description"))
    _append_unique(summary["security_boundaries"], list(result.get("security_notes") or result.get("security_boundaries") or []), ("source", "description"))
    _append_unique(summary["configuration_points"], list(result.get("config_notes") or result.get("configuration_points") or []), ("source", "key"))
    _append_unique(summary["runtime_paths"], list(result.get("runtime_paths") or []), ("name", "source"))
    _append_unique(summary["open_questions"], list(result.get("unresolved_questions") or result.get("open_questions") or []), ("question",))
    _append_unique(summary["source_evidence"], list(result.get("source_evidence") or []), ("source", "source_kind", "note"))

    coverage = dict(summary.get("coverage") or {})
    processed = int(coverage.get("processed_refs") or 0)
    for block in batch:
        source_kind = str(block.get("source_kind") or "unknown")
        counts = dict(coverage.get("processed_source_kinds") or {})
        counts[source_kind] = int(counts.get(source_kind) or 0) + 1
        coverage["processed_source_kinds"] = counts
    coverage["processed_refs"] = processed + len(batch)
    summary["coverage"] = coverage
    return summary


def _summary_for_prompt(summary: dict, max_chars: int) -> str:
    compact = {
        "schema": summary.get("schema"),
        "status": summary.get("status"),
        "components": list(summary.get("components") or [])[:40],
        "edges": list(summary.get("edges") or [])[:80],
        "entrypoints": list(summary.get("entrypoints") or [])[:40],
        "data_flows": list(summary.get("data_flows") or [])[:40],
        "security_boundaries": list(summary.get("security_boundaries") or [])[:30],
        "configuration_points": list(summary.get("configuration_points") or [])[:30],
        "runtime_paths": list(summary.get("runtime_paths") or [])[:30],
        "open_questions": list(summary.get("open_questions") or [])[:30],
        "source_evidence": list(summary.get("source_evidence") or [])[:120],
        "coverage": summary.get("coverage") or {},
    }
    text = json.dumps(compact, indent=2, ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n..."
    return text


def _build_architecture_batch_prompt(original_prompt: str, *, batch: list[dict], summary: dict, step: int, total_steps: int, max_summary_chars: int) -> str:
    parts = [
        original_prompt.rstrip(),
        "\n\n---\n\n",
        "Du arbeitest im Architektur-Full-Scan-Modus. Antworte mit JSON, optional fenced, mit schema='architecture_batch_analysis.v1'.\n",
        "Erwartete Felder: analyzed_refs, components, edges, data_flows, security_notes, config_notes, runtime_paths, unresolved_questions, source_evidence, confidence.\n",
        "Erfinde keine konkreten Dateipfade. Nutze nur die unten aufgeführten Quellen als Evidence.\n\n",
        f"Schritt {step}/{total_steps}\n\n",
        "Aktuelle strukturierte Summary:\n",
        _summary_for_prompt(summary, max_summary_chars),
        "\n\nQuellen dieses Batches:\n\n",
    ]
    for block in batch:
        h = _format_block_header(block)
        lang = block.get("lang") or "text"
        content = block.get("content") or ""
        parts.append(f"{h}\n```{lang}\n{content}\n```\n\n")
    return "".join(parts)


def _build_architecture_synthesis_prompt(original_prompt: str, *, plan: dict, summary: dict, output_intent: str, max_summary_chars: int) -> str:
    diagram_instruction = {
        "mermaid_sequence_diagram": "Erzeuge ein Mermaid sequenceDiagram fuer die wichtigsten Runtime-Flows.",
        "mermaid_component_diagram": "Erzeuge ein Mermaid flowchart fuer Komponenten und Abhaengigkeiten.",
        "dependency_map": "Erzeuge ein Mermaid flowchart als Dependency Map.",
    }.get(output_intent, "Erzeuge eine strukturierte Architekturuebersicht; wenn passend, mit Mermaid flowchart.")
    return (
        f"{original_prompt.rstrip()}\n\n---\n\n"
        "Alle geplanten Quellen wurden verarbeitet oder als ausgelassen dokumentiert.\n"
        f"{diagram_instruction}\n"
        "Die finale Antwort muss Diagramm/Markdown, kurze Erklärung, Quellenliste und Coverage-Hinweis enthalten.\n"
        "Jede sichere Komponente und Kante muss durch source_evidence oder processed_refs gedeckt sein; inferred Elemente separat markieren.\n\n"
        f"Plan:\n```json\n{json.dumps({'plan_id': plan.get('plan_id'), 'coverage': plan.get('coverage'), 'output_intent': output_intent}, indent=2, ensure_ascii=False)}\n```\n\n"
        f"Strukturierte Summary:\n```json\n{_summary_for_prompt(summary, max_summary_chars)}\n```"
    )


def _run_architecture_full_scan(
    prompt: str,
    workdir: str,
    *,
    options: list,
    timeout: int,
    model: str | None,
    research_context: dict,
) -> tuple[int, str, str]:
    from agent.services.architecture_analysis_planner_service import get_architecture_analysis_planner

    profile = dict(research_context.get("retrieval_profile") or {})
    full_scan_enabled = bool(getattr(settings, "ananta_worker_full_scan_enabled", True))
    if not full_scan_enabled:
        return run_sgpt_command(prompt=prompt, options=options, timeout=timeout, model=model, workdir=workdir)

    ctx_cfg = _get_worker_context_cfg()

    def _setting(name: str, default: int, lo: int, hi: int) -> int:
        raw = ctx_cfg.get(name)
        if raw is None:
            raw = getattr(settings, name, default)
        try:
            return max(lo, min(hi, int(raw)))
        except (TypeError, ValueError):
            return default

    budgets = dict(profile.get("budgets") or {})
    budgets.setdefault("max_batches", _setting("ananta_worker_full_scan_max_batches", 8, 1, 64))
    budgets.setdefault("files_per_batch", _setting("ananta_worker_full_scan_files_per_batch", 3, 1, 20))
    budgets.setdefault("max_ref_chars", _setting("ananta_worker_full_scan_max_ref_chars", 4000, 500, 40_000))
    budgets.setdefault("max_summary_chars", _setting("ananta_worker_full_scan_summary_chars", 12000, 1000, 80_000))
    budgets.setdefault("max_total_ref_count", _setting("ananta_worker_full_scan_max_total_ref_count", 120, 1, 500))
    profile["budgets"] = budgets
    research_context = dict(research_context)
    research_context["retrieval_profile"] = profile

    planner = get_architecture_analysis_planner()
    plan = planner.build_plan(query=prompt, research_context=research_context, retrieval_profile=profile)
    rag_dir = pathlib.Path(workdir) / "rag_helper"
    plan_path = rag_dir / "architecture-plan.json"
    progress_json_path = rag_dir / "architecture-progress.json"
    summary_path = rag_dir / "architecture-summary.json"
    diagrams_path = rag_dir / "architecture-diagrams.md"
    progress_md_path = rag_dir / "progress.md"
    _write_json(plan_path, plan)

    existing_progress = {}
    if progress_json_path.exists():
        try:
            existing_progress = json.loads(progress_json_path.read_text(encoding="utf-8"))
        except Exception:
            existing_progress = {}
    processed_batch_ids = set()
    if existing_progress.get("plan_id") == plan.get("plan_id"):
        processed_batch_ids = {str(item) for item in list(existing_progress.get("processed_batch_ids") or [])}

    if summary_path.exists() and existing_progress.get("plan_id") == plan.get("plan_id"):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = _summary_empty(plan)
    else:
        summary = _summary_empty(plan)

    files_per_batch = int((plan.get("budget") or {}).get("files_per_batch") or 3)
    max_batches = int((plan.get("budget") or {}).get("max_batches") or 8)
    max_ref_chars = int((plan.get("budget") or {}).get("max_ref_chars") or 4000)
    max_summary_chars = int((plan.get("budget") or {}).get("max_summary_chars") or 12000)
    batches = _load_source_file_batches(
        workdir,
        files_per_batch=files_per_batch,
        per_file_chars=max_ref_chars,
        max_files=files_per_batch * max_batches,
        context_lines=_bounded_worker_int("ananta_worker_context_line_window", 5, 0, _MAX_LINE_WINDOW),
        max_snippet_chars=max_ref_chars,
    )

    progress_parts: list[str] = []
    if progress_md_path.exists() and processed_batch_ids:
        try:
            existing_md = progress_md_path.read_text(encoding="utf-8", errors="replace").strip()
            if existing_md:
                progress_parts.append(existing_md)
        except OSError:
            pass

    total = min(len(batches), max_batches)
    last_rc, last_out, last_err = 0, "", ""
    processed_ids = list(processed_batch_ids)

    for step, batch in enumerate(batches[:max_batches], start=1):
        planned_batch = (plan.get("batches") or [{}])[step - 1] if step - 1 < len(plan.get("batches") or []) else {}
        batch_id = str(planned_batch.get("batch_id") or f"batch:{step}")
        if batch_id in processed_batch_ids:
            continue
        iter_prompt = _build_architecture_batch_prompt(
            prompt,
            batch=batch,
            summary=summary,
            step=step,
            total_steps=total,
            max_summary_chars=max_summary_chars,
        )
        rc, out, err = run_sgpt_command(prompt=iter_prompt, options=options, timeout=timeout, model=model, workdir=workdir)
        last_rc, last_err = rc, err
        if out:
            last_out = out
        parsed = _extract_json_object(out)
        summary = _merge_batch_analysis(summary, parsed, batch=batch, raw_output=out)
        source_labels = []
        for block in batch:
            label = str(block.get("rel_path") or "")
            if block.get("start_line") is not None and block.get("end_line") is not None:
                label = f"{label}:{block.get('start_line')}-{block.get('end_line')}"
            source_labels.append(f"{label} [{block.get('source_kind') or 'unknown'}]")
        progress_parts.append(f"## Architektur-Batch {step} — {', '.join(source_labels)}\n\n{str(out or '').strip()}")
        processed_batch_ids.add(batch_id)
        processed_ids.append(batch_id)
        progress_payload = {
            "schema": "architecture_analysis_progress.v1",
            "status": "partial" if rc != 0 else "in_progress",
            "plan_id": plan.get("plan_id"),
            "processed_batch_ids": processed_ids,
            "processed_batches": len(processed_batch_ids),
            "batch_count": total,
            "last_successful_batch": step if rc == 0 else max(0, step - 1),
            "artifact_paths": {
                "plan": str(plan_path),
                "progress": str(progress_json_path),
                "summary": str(summary_path),
                "diagrams": str(diagrams_path),
            },
        }
        _write_json(progress_json_path, progress_payload)
        _write_json(summary_path, summary)
        try:
            progress_md_path.parent.mkdir(parents=True, exist_ok=True)
            progress_md_path.write_text("\n\n---\n\n".join(progress_parts), encoding="utf-8")
        except OSError:
            pass
        if rc != 0 and not out:
            summary["status"] = "partial"
            _write_json(summary_path, summary)
            break

    summary["status"] = "done" if int((summary.get("coverage") or {}).get("processed_refs") or 0) >= int((summary.get("coverage") or {}).get("planned_refs") or 0) else summary.get("status", "partial")
    _write_json(summary_path, summary)

    output_intent = str(plan.get("output_intent") or profile.get("output_intent") or "architecture_overview")
    synthesis_prompt = _build_architecture_synthesis_prompt(
        prompt,
        plan=plan,
        summary=summary,
        output_intent=output_intent,
        max_summary_chars=max_summary_chars,
    )
    rc, out, err = run_sgpt_command(prompt=synthesis_prompt, options=options, timeout=timeout, model=model, workdir=workdir)
    if out:
        last_rc, last_out, last_err = rc, out, err
        try:
            diagrams_path.parent.mkdir(parents=True, exist_ok=True)
            diagrams_path.write_text(out.strip() + "\n", encoding="utf-8")
            progress_md_path.write_text(
                "\n\n---\n\n".join(progress_parts) + f"\n\n---\n\n## Finales Ergebnis\n\n{out.strip()}",
                encoding="utf-8",
            )
        except OSError:
            pass
    progress_payload = {
        "schema": "architecture_analysis_progress.v1",
        "status": "done" if last_rc == 0 else "partial",
        "plan_id": plan.get("plan_id"),
        "processed_batch_ids": processed_ids,
        "processed_batches": len(processed_batch_ids),
        "batch_count": total,
        "processed_refs": int((summary.get("coverage") or {}).get("processed_refs") or 0),
        "omitted_refs": int((summary.get("coverage") or {}).get("omitted_refs") or 0),
        "summary_hash": hashlib.sha1(json.dumps(summary, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16],
        "artifact_paths": {
            "plan": str(plan_path),
            "progress": str(progress_json_path),
            "summary": str(summary_path),
            "diagrams": str(diagrams_path),
        },
    }
    _write_json(progress_json_path, progress_payload)
    return last_rc, last_out, last_err


def _run_ananta_worker_iterative(
    prompt: str,
    workdir: str | None,
    *,
    options: list,
    timeout: int,
    model: str | None,
    files_per_batch: int = 3,
    per_file_chars: int = 4_000,
    max_iterations: int = 8,
) -> tuple[int, str, str]:
    """Iterative execution loop for ananta-worker.

    Mirrors how OpenCode works with its workdir:
    - Source files are loaded in batches from CodeCompass repo_scope_refs
    - Each sgpt call processes one batch and writes its output to
      rag_helper/progress.md in the workspace (= persisted intermediate state)
    - Subsequent calls receive the accumulated progress as context
    - A final synthesis call assembles the complete result from all steps
    - Falls back to a single-shot call when no batches / workdir not available
    """
    # Read configurable limits (CCSH-006)
    files_per_batch = _bounded_worker_int("ananta_worker_context_files_per_batch", files_per_batch, 1, 20)
    per_file_chars = _bounded_worker_int("ananta_worker_context_per_file_chars", per_file_chars, 500, 40_000)
    max_iterations = _bounded_worker_int("ananta_worker_context_max_iterations", max_iterations, 1, 32)
    context_lines = _bounded_worker_int("ananta_worker_context_line_window", 5, 0, _MAX_LINE_WINDOW)
    max_snippet_chars = _bounded_worker_int("ananta_worker_context_max_snippet_chars", 8_000, 200, 40_000)
    research_context = _read_research_context(workdir)
    if workdir and _is_architecture_full_scan_context(research_context):
        return _run_architecture_full_scan(
            prompt,
            workdir,
            options=options,
            timeout=timeout,
            model=model,
            research_context=research_context,
        )

    batches = _load_source_file_batches(
        workdir,
        files_per_batch=files_per_batch,
        per_file_chars=per_file_chars,
        max_files=max_iterations * files_per_batch,
        context_lines=context_lines,
        max_snippet_chars=max_snippet_chars,
    )

    # No workspace context → plain single-shot call
    if not batches:
        return run_sgpt_command(prompt=prompt, options=options, timeout=timeout, model=model, workdir=workdir)

    # Single batch → embed files directly, no iteration overhead
    if len(batches) == 1:
        batch = batches[0]
        file_blocks = "\n\n".join(
            f"{_format_block_header(b)}\n```{b.get('lang', 'text')}\n{b.get('content', '')}\n```"
            for b in batch
        )
        enriched = f"{prompt.rstrip()}\n\n---\n\n{file_blocks}"
        return run_sgpt_command(prompt=enriched, options=options, timeout=timeout, model=model, workdir=workdir)

    # Multiple batches → iterative loop
    capped = batches[:max_iterations]
    total = len(capped)
    progress_path = (pathlib.Path(workdir) / "rag_helper" / "progress.md") if workdir else None
    progress_parts: list[str] = []
    last_rc, last_out, last_err = 0, "", ""

    for step, batch in enumerate(capped, start=1):
        iter_prompt = _build_iteration_prompt(
            original_prompt=prompt,
            batch=batch,
            progress_so_far="\n\n---\n\n".join(progress_parts),
            step=step,
            total_steps=total,
            is_synthesis=False,
        )
        rc, out, err = run_sgpt_command(
            prompt=iter_prompt, options=options, timeout=timeout, model=model, workdir=workdir
        )
        last_rc, last_err = rc, err
        if out:
            last_out = out
            # CCSH-007: progress header shows source kind + path:range for each block
            source_labels = []
            for b in batch:
                sk = b.get("source_kind") or "file_excerpt"
                rp = b.get("rel_path") or ""
                s, e = b.get("start_line"), b.get("end_line")
                if s is not None and e is not None:
                    source_labels.append(f"{rp}:{s}-{e} [{sk}]")
                else:
                    source_labels.append(f"{rp} [{sk}]")
            step_header = f"## Schritt {step} — {', '.join(source_labels)}"
            progress_parts.append(f"{step_header}\n\n{out.strip()}")
            if progress_path:
                try:
                    progress_path.parent.mkdir(parents=True, exist_ok=True)
                    progress_path.write_text(
                        "\n\n---\n\n".join(progress_parts), encoding="utf-8"
                    )
                except OSError:
                    pass
        if rc != 0 and not out:
            logging.warning("ananta-worker iteration %s/%s failed (rc=%s), stopping early", step, total, rc)
            break

    # Synthesis call: assemble final result from all intermediate steps
    if progress_parts:
        synthesis_prompt = _build_iteration_prompt(
            original_prompt=prompt,
            batch=[],
            progress_so_far="\n\n---\n\n".join(progress_parts),
            step=total + 1,
            total_steps=total + 1,
            is_synthesis=True,
        )
        rc, out, err = run_sgpt_command(
            prompt=synthesis_prompt, options=options, timeout=timeout, model=model, workdir=workdir
        )
        if out:
            last_rc, last_out, last_err = rc, out, err
            if progress_path:
                try:
                    final_text = (
                        "\n\n---\n\n".join(progress_parts)
                        + f"\n\n---\n\n## Finales Ergebnis\n\n{out.strip()}"
                    )
                    progress_path.write_text(final_text, encoding="utf-8")
                except OSError:
                    pass

    return last_rc, last_out, last_err


def run_sgpt_command(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    model: str | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """
    Führt einen SGPT-Befehl zentral aus, inkl. korrekter Environment-Injektion.
    Gibt (returncode, stdout, stderr) zurück.
    """
    options = options or []
    if "--no-interaction" not in options:
        options.append("--no-interaction")

    agent_cfg = _get_agent_config()
    selected_model = (
        str(
            model
            or agent_cfg.get("sgpt_default_model")
            or agent_cfg.get("default_model")
            or agent_cfg.get("model")
            or settings.sgpt_default_model
            or ""
        ).strip()
        or None
    )
    args = (["--model", selected_model] if selected_model else []) + options + [prompt]

    with _acquire_backend_permit("sgpt", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'sgpt' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()

        runtime_provider = _get_runtime_default_provider()
        provider_urls = _get_runtime_provider_urls()

        base_url = None
        if runtime_provider == "ollama":
            base_url = _normalize_ollama_openai_base_url(provider_urls.get("ollama") or settings.ollama_url)
        elif runtime_provider == "lmstudio":
            base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
        elif runtime_provider == "openai":
            base_url = _normalize_openai_base_url(provider_urls.get("openai") or settings.openai_url)

        if base_url:
            env["OPENAI_API_BASE"] = base_url
            # Newer OpenAI clients (used by shell-gpt) honor OPENAI_BASE_URL.
            env["OPENAI_BASE_URL"] = base_url
        else:
            env.pop("OPENAI_API_BASE", None)
            env.pop("OPENAI_BASE_URL", None)

        if not env.get("OPENAI_API_KEY"):
            configured_api_key = (
                _resolve_profile_api_key(str(agent_cfg.get("openai_api_key_profile") or "").strip())
                or str(settings.openai_api_key or "").strip()
                or None
            )
            if configured_api_key:
                env["OPENAI_API_KEY"] = configured_api_key
            elif runtime_provider in {"lmstudio", "ollama"} or _is_probably_local_base_url(base_url):
                env["OPENAI_API_KEY"] = "sk-no-key-needed"

        try:
            logging.info(f"Zentraler SGPT-Aufruf: {args}")
            cwd = workdir if (workdir and pathlib.Path(workdir).is_dir()) else None
            result = subprocess.run(  # noqa: S603 - args are constructed in-process; no shell=True
                [sys.executable, "-m", "sgpt"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
                cwd=cwd,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("SGPT Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"SGPT Fehler: {e}")
            return -1, "", str(e)


def run_opencode_command(
    prompt: str,
    model: str | None = None,
    timeout: int = 60,
    session: dict | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """
    Führt einen OpenCode-CLI-Aufruf aus.
    Gibt (returncode, stdout, stderr) zurück.
    """
    opencode_bin = settings.opencode_path or "opencode"
    opencode_resolved = shutil.which(opencode_bin)
    if opencode_resolved is None:
        return -1, "", (f"OpenCode binary '{opencode_bin}' not found. Install with: npm i -g opencode-ai")

    session_meta = (session or {}).get("metadata") if isinstance((session or {}).get("metadata"), dict) else {}
    runtime_meta = (
        session_meta.get("opencode_runtime")
        if isinstance(session_meta.get("opencode_runtime"), dict)
        else {}
    )
    if session and str(runtime_meta.get("kind") or "").strip().lower() == "native_server":
        from agent.services.opencode_runtime_service import get_opencode_runtime_service

        return get_opencode_runtime_service().run_session_turn(
            session,
            prompt=prompt,
            timeout=timeout,
            model=model,
        )
    opencode_execution_mode = str(session_meta.get("opencode_execution_mode") or "").strip().lower()
    if session and opencode_execution_mode == "live_terminal":
        from agent.services.live_terminal_session_service import get_live_terminal_session_service

        return get_live_terminal_session_service().run_opencode_turn(
            session,
            prompt=prompt,
            timeout=timeout,
            model=model,
            workdir=workdir,
        )
    if session and opencode_execution_mode == "interactive_terminal":
        from agent.services.live_terminal_session_service import get_live_terminal_session_service

        terminal_service = get_live_terminal_session_service()
        session_info = terminal_service.ensure_session_for_cli(session, workdir=workdir) or {}
        rc, out, err, command_label = _run_opencode_subprocess(
            prompt=prompt,
            model=model,
            timeout=timeout,
            workdir=workdir,
            output_format=None,
        )
        terminal_session_id = str(session_info.get("terminal_session_id") or session.get("id") or "").strip()
        if terminal_session_id:
            terminal_service.append_output(terminal_session_id, f"$ {command_label}\n")
            if out:
                terminal_service.append_output(terminal_session_id, f"{out}\n")
            if err:
                terminal_service.append_output(terminal_session_id, f"{err}\n")
        return rc, out, err
    rc, out, err, _ = _run_opencode_subprocess(
        prompt=prompt,
        model=model,
        timeout=timeout,
        workdir=workdir,
        output_format=None,
    )
    return rc, out, err


def _run_opencode_subprocess(
    *,
    prompt: str,
    model: str | None,
    timeout: int,
    workdir: str | None,
    output_format: str | None,
) -> tuple[int, str, str, str]:
    opencode_bin = settings.opencode_path or "opencode"
    opencode_resolved = shutil.which(opencode_bin)
    if opencode_resolved is None:
        hint = f"OpenCode binary '{opencode_bin}' not found. Install with: npm i -g opencode-ai"
        return -1, "", hint, opencode_bin

    with _acquire_backend_permit("opencode", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'opencode' ist ausgelastet (semaphore_exhausted)", opencode_bin
        env = os.environ.copy()
        runtime_cfg = resolve_opencode_runtime_config(model=model)
        args = [opencode_resolved, "run"]
        selected_model = str(runtime_cfg.get("model") or "").strip()
        if selected_model:
            args.extend(["--model", selected_model])
        if output_format:
            args.extend(["--format", str(output_format)])
        try:
            diagnostics = list(runtime_cfg.get("diagnostics") or [])
            if diagnostics:
                logging.warning("OpenCode runtime diagnostics: %s", ",".join(diagnostics))
            temp_dir_ctx = tempfile.TemporaryDirectory() if runtime_cfg.get("provider_config") else contextlib.nullcontext(None)
            with temp_dir_ctx as tmp_dir:
                if runtime_cfg.get("provider_config") and tmp_dir:
                    config_dir = os.path.join(tmp_dir, "opencode")
                    os.makedirs(config_dir, exist_ok=True)
                    config_path = os.path.join(config_dir, "config.json")
                    with open(config_path, "w", encoding="utf-8") as handle:
                        json.dump(runtime_cfg["provider_config"], handle, ensure_ascii=True)
                    env["XDG_CONFIG_HOME"] = tmp_dir
                    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(runtime_cfg["provider_config"], ensure_ascii=True)
                env_prefix = " ".join(
                    f"{key}={shlex.quote(value)}" for key, value in env.items() if key in {"XDG_CONFIG_HOME"}
                )
                visible_command = " ".join(
                    [segment for segment in [env_prefix.strip(), " ".join(shlex.quote(part) for part in args)] if segment]
                )
                logging.info(f"Zentraler OpenCode-Aufruf: {args}")
                result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                    args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=timeout,
                    cwd=workdir or None,
                    input=str(prompt or ""),
                )
                return result.returncode, result.stdout, result.stderr, visible_command
        except subprocess.TimeoutExpired:
            logging.error("OpenCode Timeout")
            return -1, "", "Timeout", " ".join(shlex.quote(part) for part in args)
        except Exception as e:
            logging.exception(f"OpenCode Fehler: {e}")
            return -1, "", str(e), " ".join(shlex.quote(part) for part in args)


def _resolve_openai_compatible_base_url() -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    provider = _get_runtime_default_provider()
    provider_urls = _get_runtime_provider_urls()
    if provider == "lmstudio":
        return _normalize_lmstudio_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
    elif provider in {"openai", "codex"}:
        raw_url = provider_urls.get("openai") or provider_urls.get("codex") or settings.openai_url
    else:
        raw_url = (
            provider_urls.get("openai")
            or provider_urls.get("codex")
            or provider_urls.get("lmstudio")
            or settings.openai_url
            or settings.lmstudio_url
        )

    if not raw_url:
        return None

    normalized = raw_url.strip()
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _normalize_openai_base_url(url: str | None) -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    raw_url = str(url or "").strip()
    if not raw_url:
        return None
    normalized_lmstudio = _normalize_lmstudio_base_url(raw_url)
    if normalized_lmstudio:
        return normalized_lmstudio
    normalized = raw_url
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _get_agent_config() -> dict:
    if has_app_context():
        return (current_app.config.get("AGENT_CONFIG", {}) or {})
    return {}


def _get_runtime_provider_urls() -> dict:
    defaults = {
        "ollama": getattr(settings, "ollama_url", None),
        "lmstudio": getattr(settings, "lmstudio_url", None),
        "openai": getattr(settings, "openai_url", None),
        "anthropic": getattr(settings, "anthropic_url", None),
        "mock": getattr(settings, "mock_url", None),
    }
    if not has_app_context():
        return defaults
    configured = current_app.config.get("PROVIDER_URLS", {}) or {}
    if not isinstance(configured, dict):
        return defaults
    return {
        **defaults,
        **{key: value for key, value in configured.items() if value},
    }


def _get_runtime_default_provider() -> str:
    agent_cfg = _get_agent_config()
    return str(agent_cfg.get("default_provider") or settings.default_provider or "").strip().lower()


def _resolve_profile_api_key(profile_name: str | None) -> str | None:
    profile_name = str(profile_name or "").strip()
    if not profile_name:
        return None
    agent_cfg = _get_agent_config()
    profiles = agent_cfg.get("llm_api_key_profiles") or {}
    if not isinstance(profiles, dict):
        return None
    selected = profiles.get(profile_name)
    if isinstance(selected, str):
        return selected.strip() or None
    if isinstance(selected, dict):
        return str(selected.get("api_key") or "").strip() or None
    return None


def _is_probably_local_base_url(url: str | None) -> bool:
    raw = (url or "").strip().lower()
    if not raw:
        return False
    local_markers = (
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
        "192.168.",
        "10.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
    )
    return any(marker in raw for marker in local_markers)


def _build_codex_runtime_diagnostics(*, base_url: str | None, api_key: str | None, is_local: bool) -> list[str]:
    diagnostics: list[str] = []
    if not base_url:
        diagnostics.append("codex_runtime_missing_base_url")
    if not api_key and not is_local:
        diagnostics.append("codex_runtime_missing_api_key_for_remote_target")
    if base_url and _classify_runtime_target(base_url) == "unknown":
        diagnostics.append("codex_runtime_target_host_kind_unknown")
    return diagnostics


def _split_cli_model_identifier(model: str | None) -> tuple[str | None, str | None]:
    raw = str(model or "").strip()
    if not raw:
        return None, None
    if "/" not in raw:
        return None, raw
    provider_name, model_name = raw.split("/", 1)
    provider_name = provider_name.strip().lower() or None
    model_name = model_name.strip() or None
    return provider_name, model_name


def _infer_local_opencode_target(
    model: str | None,
    *,
    provider_urls: dict[str, object],
    preferred_provider: str | None,
    timeout: int,
) -> tuple[str | None, str | None]:
    raw_model = str(model or "").strip()
    if not raw_model:
        return None, None
    provider_hint = str(preferred_provider or "").strip().lower()
    local_candidates = [provider_hint] if provider_hint in {"ollama", "lmstudio"} else []
    for candidate in ("ollama", "lmstudio"):
        if candidate not in local_candidates:
            local_candidates.append(candidate)

    for candidate in local_candidates:
        if candidate == "ollama":
            base_url = _normalize_ollama_openai_base_url(str(provider_urls.get("ollama") or "").strip())
            if not base_url:
                continue
            try:
                probe = probe_ollama_runtime(base_url, timeout=timeout)
            except Exception:
                continue
            matched = _find_matching_ollama_candidate(raw_model, list(probe.get("models") or [])) if isinstance(probe, dict) else None
            if matched:
                resolved_model = resolve_ollama_model(raw_model, base_url, timeout=timeout) or raw_model
                return "ollama", str(resolved_model).strip() or raw_model
        elif candidate == "lmstudio":
            base_url = str(provider_urls.get("lmstudio") or "").strip()
            if not base_url:
                continue
            try:
                probe = probe_lmstudio_runtime(base_url, timeout=timeout)
            except Exception:
                continue
            matched = _find_matching_lmstudio_candidate(raw_model, list(probe.get("candidates") or [])) if isinstance(probe, dict) else None
            if matched:
                resolved_model = str((matched or {}).get("id") or "").strip() or raw_model
                return "lmstudio", resolved_model
    return None, None


def _normalize_ollama_openai_base_url(url: str | None) -> str | None:
    from agent.llm_integration import _normalize_ollama_base_url

    normalized = _normalize_ollama_base_url(url)
    if not normalized:
        return None
    normalized = normalized.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _build_opencode_runtime_diagnostics(*, base_url: str | None) -> list[str]:
    diagnostics: list[str] = []
    if not base_url:
        diagnostics.append("opencode_runtime_missing_base_url")
    elif _classify_runtime_target(base_url) == "unknown":
        diagnostics.append("opencode_runtime_target_host_kind_unknown")
    return diagnostics


def _build_opencode_theless_agent_config() -> dict[str, object]:
    return {
        "description": "Toolless worker for structured JSON replies",
        "prompt": "Return concise structured answers. Never call tools.",
        "tools": {
            "bash": False,
            "read": False,
            "glob": False,
            "grep": False,
            "edit": False,
            "write": False,
            "task": False,
            "webfetch": False,
            "todowrite": False,
            "question": False,
            "skill": False,
        },
    }


def _normalize_opencode_tool_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"toolless", "readonly", "full"}:
        return mode
    return "full"


def _normalize_opencode_execution_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"backend", "live_terminal", "interactive_terminal"}:
        return mode
    return "live_terminal"


def resolve_opencode_runtime_config(model: str | None = None) -> dict[str, object]:
    agent_cfg = _get_agent_config()
    provider_urls = _get_runtime_provider_urls()
    opencode_runtime_cfg = agent_cfg.get("opencode_runtime") if isinstance(agent_cfg.get("opencode_runtime"), dict) else {}
    tool_mode = _normalize_opencode_tool_mode(opencode_runtime_cfg.get("tool_mode"))
    execution_mode = _normalize_opencode_execution_mode(opencode_runtime_cfg.get("execution_mode"))
    target_profile = str(opencode_runtime_cfg.get("target_profile") or "").strip() or None
    forced_target_provider = str(opencode_runtime_cfg.get("target_provider") or "").strip().lower() or None
    forced_target_model = str(opencode_runtime_cfg.get("target_model") or opencode_runtime_cfg.get("model") or "").strip() or None
    resolved_profile = None
    if target_profile:
        try:
            from agent.services.model_invocation_service import ModelInvocationService

            resolver = ModelInvocationService._get_resolver()
            resolved_profile = (getattr(resolver, "_by_id", {}) or {}).get(target_profile) if resolver is not None else None
            if resolved_profile is not None:
                forced_target_provider = str(getattr(resolved_profile, "provider_id", "") or "").strip().lower() or forced_target_provider
                forced_target_model = str(getattr(resolved_profile, "model", "") or "").strip() or forced_target_model
        except Exception:
            resolved_profile = None
    # "native" or any OpenCode built-in provider name → let OpenCode use its own auth (e.g. Big Pickle).
    # "ollama" / "lmstudio" → inject a local OpenAI-compatible provider config.
    # anything else → discard (unknown provider).
    _native_passthrough = {"opencode", "anthropic", "openai", "gemini", "groq", "openrouter", "bedrock", "azure", "vertexai", "copilot", "native"}
    if forced_target_provider in _native_passthrough:
        # Signal native mode: OpenCode will use its own stored credentials.
        forced_target_provider = "__native__"
    elif forced_target_provider not in {"ollama", "lmstudio"}:
        forced_target_provider = None
    configured_default_model = (
        forced_target_model
        or str(agent_cfg.get("opencode_default_model") or "").strip()
        or str(agent_cfg.get("default_model") or agent_cfg.get("model") or "").strip()
        or str(settings.opencode_default_model or "").strip()
    )
    default_provider = str(agent_cfg.get("default_provider") or _get_runtime_default_provider() or "").strip() or None
    raw_model = normalize_legacy_model_name(
        str(model or configured_default_model or "").strip() or None,
        provider=forced_target_provider or default_provider,
    )
    explicit_provider, explicit_model = _split_cli_model_identifier(raw_model)
    if forced_target_provider and explicit_provider in {"ollama", "lmstudio"} and explicit_provider != forced_target_provider:
        raw_model = str(explicit_model or raw_model or "").strip() or None
        explicit_provider = None
        explicit_model = None
    inference_timeout = max(1, min(int(getattr(settings, "http_timeout", 120) or 120), 5))
    inferred_provider, inferred_model = (None, None)
    if not explicit_provider and raw_model:
        inferred_provider, inferred_model = _infer_local_opencode_target(
            raw_model,
            provider_urls=provider_urls,
            preferred_provider=forced_target_provider or default_provider or _get_runtime_default_provider(),
            timeout=inference_timeout,
        )
    built_in_providers = {
        "opencode",
        "openai",
        "anthropic",
        "gemini",
        "groq",
        "openrouter",
        "bedrock",
        "azure",
        "vertexai",
        "copilot",
        "lmstudio",
    }

    target_provider = explicit_provider or forced_target_provider or inferred_provider or default_provider
    target_model = explicit_model if explicit_provider else (inferred_model or raw_model)
    target_model = normalize_legacy_model_name(target_model, provider=target_provider)
    base_url = None
    base_url_source = None
    target_provider_type = None
    target_kind = None
    local_target = None

    if target_provider == "__native__":
        # Native OpenCode mode: no provider config injected.
        # OpenCode uses its own stored credentials (e.g. Big Pickle).
        return {
            "model": None,
            "target_provider": None,
            "target_model": None,
            "base_url": None,
            "base_url_source": None,
            "target_kind": "native_opencode",
            "target_provider_type": None,
            "tool_mode": tool_mode,
            "execution_mode": execution_mode,
            "provider_config": None,
            "diagnostics": [],
        }
    elif target_provider == "ollama":
        if tool_mode != "toolless":
            tool_mode = "toolless"
        base_url = _normalize_ollama_openai_base_url(provider_urls.get("ollama") or getattr(settings, "ollama_url", None))
        base_url_source = "ollama_url"
        target_provider_type = "local_openai_compatible"
        target_kind = "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
        if target_model and base_url:
            try:
                resolve_timeout = float(getattr(settings, "http_timeout", 120) or 120)
            except (TypeError, ValueError):
                resolve_timeout = 120.0
            target_model = resolve_ollama_model(target_model, base_url, timeout=min(resolve_timeout, 10.0))
    elif target_provider == "lmstudio":
        base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or getattr(settings, "lmstudio_url", None))
        base_url_source = "lmstudio_url"
        target_provider_type = "local_openai_compatible"
        target_kind = "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
    elif target_provider and target_provider not in built_in_providers:
        local_target = resolve_local_openai_backend(
            target_provider,
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=_get_runtime_default_provider(),
            default_model=str(agent_cfg.get("default_model") or ""),
        )
        if local_target and local_target.get("base_url"):
            base_url = _normalize_openai_base_url(local_target.get("base_url"))
            base_url_source = f"local_openai.{target_provider}"
            target_provider_type = str(local_target.get("provider_type") or "local_openai_compatible")
            target_kind = "remote_ananta_hub" if bool(local_target.get("remote_hub")) else (
                "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
            )

    provider_config = None
    cli_model = raw_model
    if target_provider and target_model and base_url:
        provider_entry = {
            "npm": "@ai-sdk/openai-compatible",
            "models": {str(target_model): {}},
            "options": {"baseURL": base_url},
        }
        provider_config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {str(target_provider): provider_entry},
            "agent": {},
            "mode": {},
            "plugin": [],
            "command": {},
        }
        provider_config["model"] = f"{target_provider}/{target_model}"
        provider_config["small_model"] = f"{target_provider}/{target_model}"
        if target_provider == "ollama" and tool_mode == "toolless":
            provider_config["agent"]["ananta-worker"] = _build_opencode_theless_agent_config()
            provider_config["default_agent"] = "ananta-worker"
        cli_model = f"{target_provider}/{target_model}"
    elif target_provider and target_model and target_provider in built_in_providers:
        # Hosted providers (e.g. OpenAI) require provider/model notation for opencode.
        cli_model = f"{target_provider}/{target_model}"

    diagnostics = _build_opencode_runtime_diagnostics(base_url=base_url) if (target_provider in {"ollama", "lmstudio"} or local_target) else []
    return {
        "model": cli_model,
        "target_profile": target_profile,
        "target_provider": target_provider,
        "target_model": target_model,
        "base_url": base_url,
        "base_url_source": base_url_source,
        "target_kind": target_kind,
        "target_provider_type": target_provider_type,
        "tool_mode": tool_mode,
        "execution_mode": execution_mode,
        "provider_config": provider_config,
        "diagnostics": diagnostics,
    }


def resolve_codex_runtime_config() -> dict:
    agent_cfg = _get_agent_config()
    provider_urls = _get_runtime_provider_urls()
    codex_cfg = agent_cfg.get("codex_cli") or {}
    if not isinstance(codex_cfg, dict):
        codex_cfg = {}

    explicit_base_url = _normalize_openai_base_url(codex_cfg.get("base_url"))
    prefer_lmstudio = codex_cfg.get("prefer_lmstudio")
    target_provider = str(codex_cfg.get("target_provider") or "").strip().lower() or None
    if prefer_lmstudio is None:
        prefer_lmstudio = _get_runtime_default_provider() == "lmstudio"
    local_target = resolve_local_openai_backend(target_provider, agent_cfg=agent_cfg, provider_urls=provider_urls) if target_provider else None

    if explicit_base_url:
        base_url = explicit_base_url
        base_url_source = "codex_cli.base_url"
    elif local_target and local_target.get("base_url"):
        base_url = _normalize_openai_base_url(local_target.get("base_url"))
        base_url_source = f"codex_cli.target_provider:{local_target['provider']}"
    elif prefer_lmstudio:
        base_url = _normalize_openai_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
        base_url_source = "lmstudio_url"
    else:
        base_url = _resolve_openai_compatible_base_url()
        base_url_source = "default_provider"

    api_key = str(codex_cfg.get("api_key") or "").strip() or None
    api_key_source = "codex_cli.api_key" if api_key else None
    if not api_key:
        api_key = _resolve_profile_api_key(codex_cfg.get("api_key_profile"))
        if api_key:
            api_key_source = "codex_cli.api_key_profile"
    if not api_key and local_target:
        api_key = str(local_target.get("api_key") or "").strip() or None
        if api_key:
            api_key_source = f"local_openai.{local_target['provider']}"
        elif local_target.get("api_key_profile"):
            api_key = _resolve_profile_api_key(local_target.get("api_key_profile"))
            if api_key:
                api_key_source = f"local_openai.{local_target['provider']}.api_key_profile"
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY") or settings.openai_api_key
        if api_key:
            api_key_source = "openai_api_key"
    if not api_key and _is_probably_local_base_url(base_url):
        api_key = "sk-no-key-needed"
        api_key_source = "local_dummy"
    is_local = _is_probably_local_base_url(base_url)
    target_kind = "local_openai"
    if local_target and bool(local_target.get("remote_hub")):
        target_kind = "remote_ananta_hub"
    elif not is_local:
        target_kind = "remote_openai_compatible"
    diagnostics = _build_codex_runtime_diagnostics(base_url=base_url, api_key=api_key, is_local=is_local)
    return {
        "base_url": base_url,
        "api_key": api_key,
        "target_provider": target_provider or ("lmstudio" if prefer_lmstudio else None),
        "base_url_source": base_url_source if base_url else None,
        "api_key_source": api_key_source,
        "is_local": is_local,
        "prefer_lmstudio": bool(prefer_lmstudio),
        "target_kind": target_kind,
        "target_provider_type": (local_target or {}).get("provider_type") if isinstance(local_target, dict) else None,
        "remote_hub": bool((local_target or {}).get("remote_hub")) if isinstance(local_target, dict) else False,
        "instance_id": (local_target or {}).get("instance_id") if isinstance(local_target, dict) else None,
        "max_hops": (local_target or {}).get("max_hops") if isinstance(local_target, dict) else None,
        "diagnostics": diagnostics,
    }


def run_codex_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Fuehrt einen OpenAI Codex CLI exec-Aufruf aus.

    Der Backend-Pfad ist explizit opt-in und setzt fuer lokale OpenAI-kompatible
    Runtimes (z. B. LM Studio) sowohl OPENAI_BASE_URL als auch OPENAI_API_BASE.
    """
    codex_bin = settings.codex_path or "codex"
    codex_resolved = shutil.which(codex_bin)
    if codex_resolved is None:
        return -1, "", (f"Codex binary '{codex_bin}' not found. Install with: npm i -g @openai/codex")

    args = [codex_resolved, "exec", "--skip-git-repo-check"]
    selected_model = model or settings.codex_default_model
    if selected_model:
        args.extend(["--model", selected_model])
    args.append(prompt)

    with _acquire_backend_permit("codex", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'codex' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()
        runtime_cfg = resolve_codex_runtime_config()
        base_url = runtime_cfg["base_url"]
        api_key = runtime_cfg["api_key"]
        diagnostics = list(runtime_cfg.get("diagnostics") or [])
        if not base_url:
            return -1, "", "Codex runtime target is not configured: missing OpenAI-compatible base_url"
        if not api_key and not bool(runtime_cfg.get("is_local")):
            return -1, "", "Codex runtime target requires API key for remote endpoint"
        if base_url:
            env["OPENAI_BASE_URL"] = base_url
            env["OPENAI_API_BASE"] = base_url

        if api_key:
            env["OPENAI_API_KEY"] = api_key
        if diagnostics:
            logging.warning("Codex runtime diagnostics: %s", ",".join(diagnostics))

        try:
            logging.info(f"Zentraler Codex-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("Codex Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"Codex Fehler: {e}")
            return -1, "", str(e)


def run_aider_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Führt einen Aider-CLI-Aufruf aus (non-interactive).
    """
    aider_bin = settings.aider_path or "aider"
    aider_resolved = shutil.which(aider_bin)
    if aider_resolved is None:
        return -1, "", (f"Aider binary '{aider_bin}' not found. Install with: pip install aider-chat")

    args = [aider_resolved, "--message", prompt, "--yes-always"]
    selected_model = model or settings.aider_default_model
    if selected_model:
        args.extend(["--model", selected_model])

    with _acquire_backend_permit("aider", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'aider' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()
        try:
            logging.info(f"Zentraler Aider-Aufruf: {args}")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("Aider Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"Aider Fehler: {e}")
            return -1, "", str(e)


def run_mistral_code_command(prompt: str, model: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """
    Führt einen Mistral-Code-CLI-Aufruf aus.
    """
    mistral_bin = settings.mistral_code_path or "mistral-code"
    mistral_resolved = shutil.which(mistral_bin)
    if mistral_resolved is None:
        return -1, "", (f"Mistral Code binary '{mistral_bin}' not found. Install with: npm i -g mistral-code")

    args = [mistral_resolved]

    with _acquire_backend_permit("mistral_code", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'mistral_code' ist ausgelastet (semaphore_exhausted)"
        env = os.environ.copy()
        if not env.get("MISTRAL_API_KEY") and getattr(settings, "mistral_api_key", None):
            env["MISTRAL_API_KEY"] = settings.mistral_api_key
        try:
            logging.info(f"Zentraler Mistral-Code-Aufruf: {args}")
            input_lines = [prompt]
            if model or settings.mistral_code_default_model:
                input_lines.append(f"/model {(model or settings.mistral_code_default_model)}")
            input_lines.append("exit")
            result = subprocess.run(  # noqa: S603 - executable resolved via shutil.which, args list-only
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
                input="\n".join(input_lines) + "\n",
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logging.error("Mistral Code Timeout")
            return -1, "", "Timeout"
        except Exception as e:
            logging.exception(f"Mistral Code Fehler: {e}")
            return -1, "", str(e)


def run_llm_cli_command(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    backend: str = "ananta-worker",
    model: str | None = None,
    temperature: float | None = None,
    routing_policy: dict | None = None,
    research_context: dict | None = None,
    session: dict | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str, str]:
    """
    Führt den konfigurierten CLI-Backend-Aufruf aus.
    Rückgabe: (returncode, stdout, stderr, backend_used)
    """
    requested = (backend or "ananta-worker").strip().lower()
    candidates = _choose_candidates(requested=requested, prompt=prompt, routing_policy=routing_policy)

    def _normalize_opencode_model_identifier(value: str | None) -> str | None:
        normalized = str(value or "").strip() or None
        if not normalized or "/" in normalized:
            return normalized
        provider = _get_runtime_default_provider()
        if provider in {"openai", "anthropic", "gemini", "groq", "openrouter", "bedrock", "azure", "vertexai", "copilot"}:
            return f"{provider}/{normalized}"
        return normalized

    last_error = ""
    now = time.time()
    for name in candidates:
        started = time.time()
        if name == "sgpt":
            rc, out, err = run_sgpt_command(prompt=prompt, options=options or [], timeout=timeout, model=model, workdir=workdir)
        elif name == "ananta-worker":
            rc, out, err = _run_ananta_worker_iterative(
                prompt=prompt,
                workdir=workdir,
                options=options or [],
                timeout=timeout,
                model=model,
            )
        elif name == "codex":
            rc, out, err = run_codex_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "opencode":
            rc, out, err = run_opencode_command(
                prompt=prompt,
                model=_normalize_opencode_model_identifier(model),
                timeout=timeout,
                session=session,
                workdir=workdir,
            )
        elif name == "aider":
            rc, out, err = run_aider_command(prompt=prompt, model=model, timeout=timeout)
        elif name == "mistral_code":
            rc, out, err = run_mistral_code_command(prompt=prompt, model=model, timeout=timeout)
        elif is_research_backend(name):
            rc, out, err = run_research_backend_command(
                prompt=prompt,
                model=model,
                temperature=temperature,
                timeout=timeout,
                provider=name,
                research_context=research_context,
            )
        else:
            continue

        rt = _BACKEND_RUNTIME.setdefault(name, {})
        rt["last_rc"] = rc
        rt["last_latency_ms"] = int((time.time() - started) * 1000)
        if rc == 0 or out:
            rt["last_success_at"] = now
            rt["consecutive_failures"] = 0
            rt["cooldown_until"] = 0.0
            rt["total_success"] = int(rt.get("total_success", 0)) + 1
            rt["last_error"] = ""
            return rc, out, err, name
        rt["last_failure_at"] = now
        rt["consecutive_failures"] = int(rt.get("consecutive_failures", 0)) + 1
        rt["total_failures"] = int(rt.get("total_failures", 0)) + 1
        rt["last_error"] = err or f"{name} failed with exit code {rc}"
        # Adaptive cooldown to prevent immediate repeated failures.
        cooldown = min(120, 10 * (2 ** max(0, rt["consecutive_failures"] - 1)))
        rt["cooldown_until"] = time.time() + cooldown
        last_error = err or f"{name} failed with exit code {rc}"

    return -1, "", last_error or "No CLI backend succeeded", candidates[-1] if candidates else requested
