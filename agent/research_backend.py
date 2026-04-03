import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

from agent.config import settings

DEERFLOW_INSTALL_HINT = (
    "Clone deer-flow and configure research_backend.command plus research_backend.working_dir, "
    "for example with 'uv run main.py {prompt}'."
)
ANANTA_RESEARCH_INSTALL_HINT = (
    "Install or clone ananta_research and configure research_backend.command plus "
    "research_backend.working_dir. A thin wrapper command that accepts {prompt} is recommended."
)

RESEARCH_BACKEND_SPECS: dict[str, dict[str, Any]] = {
    "deerflow": {
        "display_name": "DeerFlow",
        "default_mode": "cli",
        "default_command": "python main.py {prompt}",
        "default_result_format": "markdown",
        "default_enabled": False,
        "supports_model": False,
        "install_hint": DEERFLOW_INSTALL_HINT,
        "verify_command": "python main.py --help",
        "job_prefix": "df",
    },
    "ananta_research": {
        "display_name": "ananta_research",
        "default_mode": "cli",
        "default_command": "",
        "default_result_format": "markdown",
        "default_enabled": False,
        "supports_model": False,
        "install_hint": ANANTA_RESEARCH_INSTALL_HINT,
        "verify_command": "configure research_backend.command",
        "job_prefix": "ar",
    },
}
RESEARCH_BACKEND_PROVIDERS: tuple[str, ...] = tuple(RESEARCH_BACKEND_SPECS.keys())
_RESEARCH_JOBS: dict[str, dict[str, dict[str, Any]]] = {provider: {} for provider in RESEARCH_BACKEND_PROVIDERS}


def _get_agent_config() -> dict:
    if has_app_context():
        return current_app.config.get("AGENT_CONFIG", {}) or {}
    return {}


def _normalize_provider_name(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    if value in RESEARCH_BACKEND_SPECS:
        return value
    return "deerflow"


def is_research_backend(provider: str | None) -> bool:
    return str(provider or "").strip().lower() in RESEARCH_BACKEND_SPECS


def _provider_spec(provider: str | None) -> dict[str, Any]:
    return RESEARCH_BACKEND_SPECS[_normalize_provider_name(provider)]


def _provider_overrides(cfg: dict[str, Any], provider: str) -> dict[str, Any]:
    providers_cfg = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    override = providers_cfg.get(provider) if isinstance(providers_cfg, dict) else {}
    return dict(override or {}) if isinstance(override, dict) else {}


def resolve_research_backend_config(
    provider_override: str | None = None,
    *,
    agent_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    container_cfg = agent_cfg if isinstance(agent_cfg, dict) else _get_agent_config()
    raw_cfg = container_cfg.get("research_backend") if isinstance(container_cfg.get("research_backend"), dict) else {}
    active_provider = _normalize_provider_name(raw_cfg.get("provider") or "deerflow")
    provider = _normalize_provider_name(provider_override or active_provider)
    spec = _provider_spec(provider)
    selected = provider == active_provider

    provider_cfg = _provider_overrides(raw_cfg, provider)
    if selected:
        top_level_cfg = {k: v for k, v in raw_cfg.items() if k != "providers"}
        provider_cfg.update(top_level_cfg)

    mode = str(provider_cfg.get("mode") or spec["default_mode"]).strip().lower()
    command = str(provider_cfg.get("command") or spec["default_command"]).strip()
    working_dir = str(provider_cfg.get("working_dir") or "").strip() or None
    timeout_seconds = max(30, int(provider_cfg.get("timeout_seconds") or getattr(settings, "command_timeout", 60) or 60))
    result_format = str(provider_cfg.get("result_format") or spec["default_result_format"]).strip().lower()
    enabled_default = bool(spec["default_enabled"]) if selected else False
    enabled = bool(provider_cfg.get("enabled", enabled_default))

    command_tokens = shlex.split(command) if command else []
    binary = None
    if command_tokens:
        executable = command_tokens[0]
        if os.path.isabs(executable) and os.path.exists(executable):
            binary = executable
        else:
            binary = shutil.which(executable)

    return {
        "provider": provider,
        "display_name": spec["display_name"],
        "selected_provider": active_provider,
        "selected": selected,
        "enabled": enabled,
        "mode": mode,
        "command": command,
        "command_tokens": command_tokens,
        "binary_path": binary,
        "working_dir": working_dir,
        "working_dir_exists": bool(working_dir and os.path.isdir(working_dir)),
        "timeout_seconds": timeout_seconds,
        "result_format": result_format,
        "install_hint": spec["install_hint"],
        "verify_command": spec["verify_command"],
        "supports_model": bool(spec["supports_model"]),
        "configured": bool(command),
        "supported_providers": list(RESEARCH_BACKEND_PROVIDERS),
    }


def get_research_backend_preflight(*, agent_cfg: dict[str, Any] | None = None) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for provider in RESEARCH_BACKEND_PROVIDERS:
        cfg = resolve_research_backend_config(provider_override=provider, agent_cfg=agent_cfg)
        entries[provider] = {
            "provider": cfg["provider"],
            "display_name": cfg["display_name"],
            "selected": bool(cfg["selected"]),
            "selected_provider": cfg["selected_provider"],
            "enabled": bool(cfg["enabled"]),
            "configured": bool(cfg["configured"]),
            "mode": cfg["mode"],
            "command": cfg["command"],
            "binary_path": cfg["binary_path"],
            "binary_available": bool(cfg["binary_path"]),
            "working_dir": cfg["working_dir"],
            "working_dir_exists": bool(cfg["working_dir_exists"]),
            "timeout_seconds": int(cfg["timeout_seconds"]),
            "result_format": cfg["result_format"],
            "supports_model": bool(cfg["supports_model"]),
            "install_hint": cfg["install_hint"],
            "verify_command": cfg["verify_command"],
        }
    return entries


def _build_command_args(cfg: dict[str, Any], *, prompt: str, model: str | None) -> list[str]:
    args: list[str] = []
    injected_prompt = False
    injected_model = False
    for token in cfg["command_tokens"]:
        if "{prompt}" in token:
            token = token.replace("{prompt}", prompt)
            injected_prompt = True
        if "{model}" in token:
            token = token.replace("{model}", str(model or ""))
            injected_model = True
        args.append(token)
    if not injected_prompt:
        args.append(prompt)
    if model and not injected_model and "{model}" in cfg["command"]:
        args.append(model)
    return args


def _execute_research_backend_cli(
    *,
    prompt: str,
    provider: str,
    timeout: int | None = None,
    model: str | None = None,
) -> tuple[int, str, str]:
    cfg = resolve_research_backend_config(provider_override=provider)
    if not cfg["enabled"]:
        return -1, "", f"{cfg['display_name']} research backend is disabled"
    if cfg["mode"] != "cli":
        return -1, "", f"Unsupported {cfg['display_name']} mode '{cfg['mode']}'"
    if not cfg["command_tokens"]:
        return -1, "", f"{cfg['display_name']} command is not configured"
    if not cfg["binary_path"]:
        return -1, "", cfg["install_hint"]
    if cfg["working_dir"] and not cfg["working_dir_exists"]:
        return -1, "", f"Configured {cfg['display_name']} working_dir does not exist: {cfg['working_dir']}"

    args = _build_command_args(cfg, prompt=prompt, model=model)
    cwd = cfg["working_dir"] or None
    env = os.environ.copy()
    actual_timeout = max(30, int(timeout or cfg["timeout_seconds"]))
    try:
        logging.info("%s-Aufruf: %s (cwd=%s)", cfg["display_name"], args, cwd)
        result = subprocess.run(  # noqa: S603 - executable resolved from configured command, no shell invocation
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd,
            timeout=actual_timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        logging.error("%s Timeout", cfg["display_name"])
        return -1, "", "Timeout"
    except Exception as exc:
        logging.exception("%s Fehler: %s", cfg["display_name"], exc)
        return -1, "", str(exc)


@dataclass
class ResearchBackendAdapter:
    provider: str

    def submit_job(
        self,
        prompt: str,
        timeout: int | None = None,
        model: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        cfg = resolve_research_backend_config(provider_override=self.provider)
        prefix = str(_provider_spec(self.provider).get("job_prefix") or self.provider[:2] or "rb")
        job_id = f"{prefix}-{uuid.uuid4()}"
        started_at = time.time()
        rc, out, err = _execute_research_backend_cli(prompt=prompt, provider=self.provider, timeout=timeout, model=model)
        status = "completed" if rc == 0 or bool(out) else "failed"
        record = {
            "job_id": job_id,
            "provider": self.provider,
            "task_id": task_id,
            "status": status,
            "started_at": started_at,
            "finished_at": time.time(),
            "config": {
                "mode": cfg["mode"],
                "command": cfg["command"],
                "working_dir": cfg["working_dir"],
                "timeout_seconds": int(timeout or cfg["timeout_seconds"]),
                "result_format": cfg["result_format"],
                "selected": bool(cfg["selected"]),
            },
            "result": {
                "returncode": rc,
                "stdout": out,
                "stderr": err,
            },
        }
        _RESEARCH_JOBS.setdefault(self.provider, {})[job_id] = record
        return record

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        record = (_RESEARCH_JOBS.get(self.provider) or {}).get(job_id) or {}
        return {
            "job_id": job_id,
            "provider": self.provider,
            "status": record.get("status") or "not_found",
            "task_id": record.get("task_id"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        }

    def fetch_job_result(self, job_id: str) -> dict[str, Any]:
        record = (_RESEARCH_JOBS.get(self.provider) or {}).get(job_id)
        if not record:
            return {"job_id": job_id, "provider": self.provider, "status": "not_found", "result": None}
        result = record.get("result") or {}
        artifact = normalize_research_artifact(
            result.get("stdout") or "",
            backend=self.provider,
            task_id=record.get("task_id"),
            cli_result={
                "returncode": result.get("returncode"),
                "stderr_preview": str(result.get("stderr") or "")[:240],
                "job_id": job_id,
            },
        )
        return {
            "job_id": job_id,
            "provider": self.provider,
            "status": record.get("status"),
            "task_id": record.get("task_id"),
            "result": result,
            "artifact": artifact,
        }

    def run_sync(
        self,
        prompt: str,
        timeout: int | None = None,
        model: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.submit_job(prompt=prompt, timeout=timeout, model=model, task_id=task_id)
        return self.fetch_job_result(record["job_id"])


@dataclass
class DeerFlowAdapter(ResearchBackendAdapter):
    provider: str = "deerflow"


@dataclass
class AnantaResearchAdapter(ResearchBackendAdapter):
    provider: str = "ananta_research"


def get_research_backend_adapter(provider: str | None = None) -> ResearchBackendAdapter:
    normalized = _normalize_provider_name(provider)
    if normalized == "ananta_research":
        return AnantaResearchAdapter()
    return DeerFlowAdapter()


def run_research_backend_command(
    prompt: str,
    timeout: int | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> tuple[int, str, str]:
    effective_provider = _normalize_provider_name(provider or resolve_research_backend_config().get("provider"))
    result = get_research_backend_adapter(effective_provider).run_sync(
        prompt=prompt,
        timeout=timeout,
        model=model,
    )
    payload = result.get("result") or {}
    return int(payload.get("returncode") or 0), str(payload.get("stdout") or ""), str(payload.get("stderr") or "")


def run_deerflow_command(prompt: str, timeout: int | None = None, model: str | None = None) -> tuple[int, str, str]:
    return run_research_backend_command(prompt=prompt, timeout=timeout, model=model, provider="deerflow")


def _extract_source_records(text: str) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    sources: list[dict[str, Any]] = []
    for match in re.findall(r"https?://[^\s)\]>\"']+", text):
        url = match.rstrip(".,;:")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({"title": url, "url": url, "kind": "web", "confidence": 0.5})
    return sources


def _extract_citation_records(text: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    known_urls = {str(source.get("url") or ""): source for source in sources}
    for line in text.splitlines():
        snippet = line.strip()
        if not snippet:
            continue
        for match in re.findall(r"https?://[^\s)\]>\"']+", snippet):
            url = match.rstrip(".,;:")
            source = known_urls.get(url) or {}
            citations.append(
                {
                    "label": str(source.get("title") or url),
                    "excerpt": snippet[:400],
                    "url": url,
                    "source_title": source.get("title"),
                    "kind": source.get("kind") or "web",
                    "confidence": source.get("confidence"),
                }
            )
    return citations


def normalize_research_artifact(
    raw_text: str,
    backend: str = "deerflow",
    task_id: str | None = None,
    cli_result: dict | None = None,
    trace: dict | None = None,
) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    summary = (paragraphs[0] if paragraphs else text).strip()
    if len(summary) > 400:
        summary = summary[:397].rstrip() + "..."

    cfg = resolve_research_backend_config(provider_override=backend) if is_research_backend(backend) else {
        "provider": backend,
        "display_name": backend,
        "mode": "cli",
        "command": "",
        "working_dir": None,
        "result_format": "markdown",
        "selected": False,
        "selected_provider": None,
    }
    sources = _extract_source_records(text)
    citations = _extract_citation_records(text, sources)
    trace_payload = dict(trace or {})
    trace_payload.setdefault("provider", backend)
    trace_payload.setdefault(
        "artifact_extraction",
        {
            "source_count": len(sources),
            "citation_count": len(citations),
            "result_format": cfg.get("result_format") or "markdown",
        },
    )

    artifact = {
        "kind": "research_report",
        "summary": summary,
        "report_markdown": text,
        "sources": sources,
        "citations": citations,
        "trace": trace_payload,
        "verification": {
            "ready": True,
            "has_sources": bool(sources),
            "has_citations": bool(citations),
            "source_count": len(sources),
            "citation_count": len(citations),
        },
        "backend_metadata": {
            "backend": backend,
            "display_name": cfg.get("display_name") or backend,
            "task_id": task_id,
            "generated_at": int(time.time()),
            "source_count": len(sources),
            "citation_count": len(citations),
            "mode": cfg.get("mode"),
            "command": cfg.get("command"),
            "working_dir": cfg.get("working_dir"),
            "result_format": cfg.get("result_format") or "markdown",
            "selected_provider": cfg.get("selected_provider"),
            "selected": bool(cfg.get("selected")),
            "cli_result": cli_result or {},
        },
    }
    return artifact
