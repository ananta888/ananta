import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context

from agent.config import settings
from agent.common.audit import log_audit
from agent.research_backend_browser import (
    BROWSER_OBSERVABILITY,
    BROWSER_POLICY_VERSION,
    CAMOFOX_OBSERVABILITY,
    BrowserUseResearchRunner,
    CamofoxResearchRunner,
)
from agent.services.browser_camofox_adapter import build_camofox_adapter
from agent.services.browser_use_adapter import get_browser_use_execution_adapter

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
    "browser_use": {
        "display_name": "browser_use",
        "default_mode": "native",
        "default_command": "",
        "default_result_format": "json",
        "default_enabled": False,
        "supports_model": False,
        "install_hint": "Enable browser_use in research_backend.providers.browser_use with allowed domains and action limits.",
        "verify_command": "configure research_backend.providers.browser_use",
        "job_prefix": "bu",
    },
    "camofox": {
        "display_name": "Camofox",
        "default_mode": "native",
        "default_command": "",
        "default_result_format": "json",
        "default_enabled": False,
        "supports_model": False,
        "install_hint": "Start the Camoufox REST server (default: http://localhost:9377) and enable camofox in research_backend.providers.camofox.",
        "verify_command": "configure research_backend.providers.camofox with camofox_url",
        "job_prefix": "cf",
    },
}
RESEARCH_BACKEND_PROVIDERS: tuple[str, ...] = tuple(RESEARCH_BACKEND_SPECS.keys())
_RESEARCH_JOBS: dict[str, dict[str, dict[str, Any]]] = {provider: {} for provider in RESEARCH_BACKEND_PROVIDERS}
# Compatibility aliases: the native browser backends live in ``research_backend_browser``.
_BROWSER_OBSERVABILITY = BROWSER_OBSERVABILITY
_CAMOFOX_OBSERVABILITY = CAMOFOX_OBSERVABILITY
_BROWSER_POLICY_VERSION = BROWSER_POLICY_VERSION


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
    provider_mode = str(provider_cfg.get("provider_mode") or "oss").strip().lower()
    command = str(provider_cfg.get("command") or spec["default_command"]).strip()
    working_dir = str(provider_cfg.get("working_dir") or "").strip() or None
    timeout_seconds = max(30, int(provider_cfg.get("timeout_seconds") or getattr(settings, "command_timeout", 60) or 60))
    result_format = str(provider_cfg.get("result_format") or spec["default_result_format"]).strip().lower()
    enabled_default = bool(spec["default_enabled"]) if selected else False
    enabled = bool(provider_cfg.get("enabled", enabled_default))
    docker_binary = str(provider_cfg.get("docker_binary") or getattr(settings, "research_sandbox_docker_path", "docker") or "docker").strip()
    sandbox_image = str(provider_cfg.get("sandbox_image") or "").strip() or None
    sandbox_network = str(provider_cfg.get("sandbox_network") or "none").strip() or "none"
    sandbox_workdir = str(provider_cfg.get("sandbox_workdir") or "/workspace").strip() or "/workspace"
    sandbox_mount_repo = bool(provider_cfg.get("sandbox_mount_repo", True))
    sandbox_read_only = bool(provider_cfg.get("sandbox_read_only", True))
    sandbox_tmp_dir = str(provider_cfg.get("sandbox_tmp_dir") or "/tmp/ananta-research").strip() or "/tmp/ananta-research"

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
        "provider_mode": provider_mode,
        "command": command,
        "command_tokens": command_tokens,
        "binary_path": binary,
        "working_dir": working_dir,
        "working_dir_exists": bool(working_dir and os.path.isdir(working_dir)),
        "timeout_seconds": timeout_seconds,
        "result_format": result_format,
        "docker_binary": docker_binary,
        "docker_available": bool(shutil.which(docker_binary)),
        "sandbox_image": sandbox_image,
        "sandbox_network": sandbox_network,
        "sandbox_workdir": sandbox_workdir,
        "sandbox_mount_repo": sandbox_mount_repo,
        "sandbox_read_only": sandbox_read_only,
        "sandbox_tmp_dir": sandbox_tmp_dir,
        "install_hint": spec["install_hint"],
        "verify_command": spec["verify_command"],
        "supports_model": bool(spec["supports_model"]),
        "configured": bool(command and (mode != "sandbox" or sandbox_image)),
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
            "provider_mode": cfg.get("provider_mode"),
            "command": cfg["command"],
            "binary_path": cfg["binary_path"],
            "binary_available": bool(cfg["binary_path"]),
            "working_dir": cfg["working_dir"],
            "working_dir_exists": bool(cfg["working_dir_exists"]),
            "timeout_seconds": int(cfg["timeout_seconds"]),
            "result_format": cfg["result_format"],
            "docker_binary": cfg["docker_binary"],
            "docker_available": bool(cfg["docker_available"]),
            "sandbox_image": cfg["sandbox_image"],
            "sandbox_network": cfg["sandbox_network"],
            "sandbox_workdir": cfg["sandbox_workdir"],
            "sandbox_mount_repo": bool(cfg["sandbox_mount_repo"]),
            "sandbox_read_only": bool(cfg["sandbox_read_only"]),
            "supports_model": bool(cfg["supports_model"]),
            "install_hint": cfg["install_hint"],
            "verify_command": cfg["verify_command"],
        }
        if provider == "browser_use":
            entries[provider]["observability"] = dict(_BROWSER_OBSERVABILITY)
            entries[provider]["health"] = get_browser_backend_health(cfg)
        elif provider == "camofox":
            entries[provider]["observability"] = dict(_CAMOFOX_OBSERVABILITY)
            entries[provider]["health"] = get_camofox_backend_health(cfg)
    return entries


def get_browser_backend_diagnostics() -> dict[str, Any]:
    return {
        "provider": "browser_use",
        "calls": int(_BROWSER_OBSERVABILITY.get("calls", 0)),
        "actions": int(_BROWSER_OBSERVABILITY.get("actions", 0)),
        "last_failure_class": _BROWSER_OBSERVABILITY.get("last_failure_class"),
        "last_latency_ms": int(_BROWSER_OBSERVABILITY.get("last_latency_ms", 0)),
        "camofox": {
            "calls": int(_CAMOFOX_OBSERVABILITY.get("calls", 0)),
            "actions": int(_CAMOFOX_OBSERVABILITY.get("actions", 0)),
            "last_failure_class": _CAMOFOX_OBSERVABILITY.get("last_failure_class"),
            "last_latency_ms": int(_CAMOFOX_OBSERVABILITY.get("last_latency_ms", 0)),
        },
    }


def get_camofox_backend_health(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = dict(cfg or resolve_research_backend_config(provider_override="camofox"))
    if not resolved.get("enabled"):
        return {
            "provider": "camofox",
            "ready": False,
            "reason": "camofox_disabled",
            "mode": str(resolved.get("mode") or ""),
            "enabled": False,
        }
    browser_cfg = {"camofox_url": resolved.get("camofox_url") or "http://localhost:9377"}
    adapter = build_camofox_adapter(browser_cfg)
    health = adapter.health_check()
    return {
        "provider": "camofox",
        "ready": bool(health.get("healthy")),
        "reason": "ok" if health.get("healthy") else health.get("error", "camofox_server_unavailable"),
        "mode": str(resolved.get("mode") or "native"),
        "enabled": bool(resolved.get("enabled")),
        "configured": bool(resolved.get("configured")),
    }


def get_browser_backend_health(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = dict(cfg or resolve_research_backend_config(provider_override="browser_use"))
    provider_mode = str(resolved.get("provider_mode") or "oss").strip().lower()
    if provider_mode not in {"oss", "cloud"}:
        return {
            "provider": "browser_use",
            "ready": False,
            "reason": "browser_backend_invalid_provider_mode",
            "mode": str(resolved.get("mode") or ""),
            "enabled": bool(resolved.get("enabled")),
            "configured": bool(resolved.get("configured")),
            "provider_mode": provider_mode,
        }
    adapter = get_browser_use_execution_adapter()
    preflight = adapter.preflight(
        {
            "enabled": bool(resolved.get("enabled")),
            "command": str(resolved.get("command") or ""),
        }
    )
    return {
        "provider": "browser_use",
        "ready": bool(preflight.ready),
        "reason": preflight.reason,
        "mode": str(resolved.get("mode") or ""),
        "enabled": bool(resolved.get("enabled")),
        "configured": bool(resolved.get("configured")),
        "provider_mode": provider_mode,
    }


def _build_command_args(
    cfg: dict[str, Any],
    *,
    prompt: str,
    model: str | None,
    temperature: float | None = None,
    context_file: str | None = None,
) -> list[str]:
    args: list[str] = []
    injected_prompt = False
    injected_model = False
    injected_temperature = False
    injected_context_file = False
    for token in cfg["command_tokens"]:
        if "{prompt}" in token:
            token = token.replace("{prompt}", prompt)
            injected_prompt = True
        if "{model}" in token:
            token = token.replace("{model}", str(model or ""))
            injected_model = True
        if "{temperature}" in token:
            token = token.replace("{temperature}", "" if temperature is None else str(float(temperature)))
            injected_temperature = True
        if "{context_file}" in token:
            token = token.replace("{context_file}", str(context_file or ""))
            injected_context_file = True
        args.append(token)
    if not injected_prompt:
        args.append(prompt)
    if model and not injected_model and "{model}" in cfg["command"]:
        args.append(model)
    if temperature is not None and not injected_temperature and "{temperature}" in cfg["command"]:
        args.append(str(float(temperature)))
    if context_file and not injected_context_file and "{context_file}" in cfg["command"]:
        args.append(context_file)
    return args


def _render_research_context_section(research_context: dict[str, Any] | None) -> str:
    section = str((research_context or {}).get("prompt_section") or "").strip()
    if not section:
        return ""
    return f"\n\nResearch context:\n{section}"


def _compose_research_prompt(prompt: str, research_context: dict[str, Any] | None) -> str:
    return f"{prompt}{_render_research_context_section(research_context)}"


def _execute_research_backend_sandbox(
    *,
    prompt: str,
    provider: str,
    timeout: int | None = None,
    model: str | None = None,
    temperature: float | None = None,
    research_context: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    cfg = resolve_research_backend_config(provider_override=provider)
    if not cfg["sandbox_image"]:
        return -1, "", f"{cfg['display_name']} sandbox_image is not configured"
    docker_binary = shutil.which(str(cfg.get("docker_binary") or "docker"))
    if not docker_binary:
        return -1, "", "Docker is not available for research sandbox execution"
    mounted_repo = cfg["working_dir"] or str(Path(__file__).resolve().parents[2])
    combined_prompt = _compose_research_prompt(prompt, research_context)
    context_mount_dir = "/tmp/ananta-research-context"
    with tempfile.TemporaryDirectory(prefix=f"{provider}-sandbox-") as temp_dir:
        context_path = Path(temp_dir) / "research-context.json"
        context_path.write_text(json.dumps(dict(research_context or {}), ensure_ascii=False, indent=2), encoding="utf-8")
        args = [docker_binary, "run", "--rm", "--network", str(cfg["sandbox_network"])]
        if cfg["sandbox_read_only"]:
            args.extend(["--read-only", "--tmpfs", str(cfg["sandbox_tmp_dir"])])
        args.extend(["--security-opt", "no-new-privileges:true", "--cap-drop", "ALL"])
        if cfg["sandbox_mount_repo"] and mounted_repo:
            args.extend(["-v", f"{mounted_repo}:{cfg['sandbox_workdir']}:ro", "-w", str(cfg["sandbox_workdir"])])
        args.extend(["-v", f"{temp_dir}:{context_mount_dir}:rw"])
        args.append(str(cfg["sandbox_image"]))
        args.extend(
            _build_command_args(
                cfg,
                prompt=combined_prompt,
                model=model,
                temperature=temperature,
                context_file=f"{context_mount_dir.rstrip('/')}/research-context.json",
            )
        )
        try:
            env = os.environ.copy()
            if temperature is not None:
                env["ANANTA_LLM_TEMPERATURE"] = str(float(temperature))
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=max(30, int(timeout or cfg["timeout_seconds"])),
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        except Exception as exc:
            logging.exception("%s Sandbox-Fehler: %s", cfg["display_name"], exc)
            return -1, "", str(exc)


def _execute_research_backend_cli(
    *,
    prompt: str,
    provider: str,
    timeout: int | None = None,
    model: str | None = None,
    temperature: float | None = None,
    research_context: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    cfg = resolve_research_backend_config(provider_override=provider)
    if cfg["provider"] == "browser_use":
        browser_cfg = dict((research_context or {}).get("browser_config") or {})
        if "enabled" in browser_cfg:
            cfg["enabled"] = bool(browser_cfg.get("enabled"))
    if not cfg["enabled"]:
        return -1, "", f"{cfg['display_name']} research backend is disabled"
    if cfg["mode"] == "native" and cfg["provider"] == "browser_use":
        return BrowserUseResearchRunner(audit=log_audit).run(cfg, timeout=timeout, research_context=research_context)
    if cfg["mode"] == "native" and cfg["provider"] == "camofox":
        return CamofoxResearchRunner(audit=log_audit).run(cfg, timeout=timeout, research_context=research_context)
    if cfg["mode"] == "sandbox":
        return _execute_research_backend_sandbox(
            prompt=prompt,
            provider=provider,
            timeout=timeout,
            model=model,
            temperature=temperature,
            research_context=research_context,
        )
    return _execute_research_backend_command(
        cfg, prompt=prompt, model=model, temperature=temperature, timeout=timeout, research_context=research_context
    )


def _cli_configuration_error(cfg: dict[str, Any]) -> str | None:
    if cfg["mode"] != "cli":
        return f"Unsupported {cfg['display_name']} mode '{cfg['mode']}'"
    if not cfg["command_tokens"]:
        return f"{cfg['display_name']} command is not configured"
    if not cfg["binary_path"]:
        return cfg["install_hint"]
    if cfg["working_dir"] and not cfg["working_dir_exists"]:
        return f"Configured {cfg['display_name']} working_dir does not exist: {cfg['working_dir']}"
    return None


def _execute_research_backend_command(
    cfg: dict[str, Any],
    *,
    prompt: str,
    model: str | None,
    temperature: float | None,
    timeout: int | None,
    research_context: dict[str, Any] | None,
) -> tuple[int, str, str]:
    """Run the configured CLI research backend (mode ``cli``) as a subprocess without a shell."""
    configuration_error = _cli_configuration_error(cfg)
    if configuration_error is not None:
        return -1, "", configuration_error

    args = _build_command_args(
        cfg,
        prompt=_compose_research_prompt(prompt, research_context),
        model=model,
        temperature=temperature,
    )
    cwd = cfg["working_dir"] or None
    env = os.environ.copy()
    if temperature is not None:
        env["ANANTA_LLM_TEMPERATURE"] = str(float(temperature))
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
        temperature: float | None = None,
        task_id: str | None = None,
        research_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = resolve_research_backend_config(provider_override=self.provider)
        prefix = str(_provider_spec(self.provider).get("job_prefix") or self.provider[:2] or "rb")
        job_id = f"{prefix}-{uuid.uuid4()}"
        started_at = time.time()
        rc, out, err = _execute_research_backend_cli(
            prompt=prompt,
            provider=self.provider,
            timeout=timeout,
            model=model,
            temperature=temperature,
            research_context=research_context,
        )
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
                "sandbox_image": cfg["sandbox_image"],
                "sandbox_network": cfg["sandbox_network"],
            },
            "research_context": dict(research_context or {}),
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
        from agent.research_artifact import normalize_research_artifact
        artifact = normalize_research_artifact(
            result.get("stdout") or "",
            backend=self.provider,
            task_id=record.get("task_id"),
            cli_result={
                "returncode": result.get("returncode"),
                "stderr_preview": str(result.get("stderr") or "")[:240],
                "job_id": job_id,
            },
            research_context=record.get("research_context"),
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
        temperature: float | None = None,
        task_id: str | None = None,
        research_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.submit_job(
            prompt=prompt,
            timeout=timeout,
            model=model,
            temperature=temperature,
            task_id=task_id,
            research_context=research_context,
        )
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
    if normalized == "browser_use":
        return ResearchBackendAdapter(provider="browser_use")
    if normalized == "camofox":
        return ResearchBackendAdapter(provider="camofox")
    return DeerFlowAdapter()


def run_research_backend_command(
    prompt: str,
    timeout: int | None = None,
    model: str | None = None,
    temperature: float | None = None,
    provider: str | None = None,
    research_context: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    effective_provider = _normalize_provider_name(provider or resolve_research_backend_config().get("provider"))
    result = get_research_backend_adapter(effective_provider).run_sync(
        prompt=prompt,
        timeout=timeout,
        model=model,
        temperature=temperature,
        research_context=research_context,
    )
    payload = result.get("result") or {}
    return int(payload.get("returncode") or 0), str(payload.get("stdout") or ""), str(payload.get("stderr") or "")


def run_deerflow_command(
    prompt: str,
    timeout: int | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> tuple[int, str, str]:
    return run_research_backend_command(
        prompt=prompt,
        timeout=timeout,
        model=model,
        temperature=temperature,
        provider="deerflow",
    )
