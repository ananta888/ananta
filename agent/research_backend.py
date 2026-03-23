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
_DEERFLOW_JOBS: dict[str, dict[str, Any]] = {}


@dataclass
class DeerFlowAdapter:
    provider: str = "deerflow"

    def submit_job(self, prompt: str, timeout: int | None = None, model: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        cfg = resolve_research_backend_config()
        job_id = f"df-{uuid.uuid4()}"
        started_at = time.time()
        rc, out, err = _execute_deerflow_cli(prompt=prompt, timeout=timeout, model=model)
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
            },
            "result": {
                "returncode": rc,
                "stdout": out,
                "stderr": err,
            },
        }
        _DEERFLOW_JOBS[job_id] = record
        return record

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        record = _DEERFLOW_JOBS.get(job_id) or {}
        return {
            "job_id": job_id,
            "provider": self.provider,
            "status": record.get("status") or "not_found",
            "task_id": record.get("task_id"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        }

    def fetch_job_result(self, job_id: str) -> dict[str, Any]:
        record = _DEERFLOW_JOBS.get(job_id)
        if not record:
            return {"job_id": job_id, "provider": self.provider, "status": "not_found", "result": None}
        result = record.get("result") or {}
        artifact = normalize_research_artifact(
            result.get("stdout") or "",
            backend=self.provider,
            task_id=record.get("task_id"),
            cli_result={"returncode": result.get("returncode"), "stderr_preview": str(result.get("stderr") or "")[:240], "job_id": job_id},
        )
        return {
            "job_id": job_id,
            "provider": self.provider,
            "status": record.get("status"),
            "task_id": record.get("task_id"),
            "result": result,
            "artifact": artifact,
        }

    def run_sync(self, prompt: str, timeout: int | None = None, model: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        record = self.submit_job(prompt=prompt, timeout=timeout, model=model, task_id=task_id)
        return self.fetch_job_result(record["job_id"])


def _get_agent_config() -> dict:
    if has_app_context():
        return current_app.config.get("AGENT_CONFIG", {}) or {}
    return {}


def resolve_research_backend_config() -> dict[str, Any]:
    cfg = (_get_agent_config().get("research_backend") or {}) if isinstance(_get_agent_config(), dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}

    provider = str(cfg.get("provider") or "deerflow").strip().lower()
    mode = str(cfg.get("mode") or "cli").strip().lower()
    command = str(cfg.get("command") or "python main.py {prompt}").strip()
    working_dir = str(cfg.get("working_dir") or "").strip() or None
    timeout_seconds = max(30, int(cfg.get("timeout_seconds") or getattr(settings, "command_timeout", 60) or 60))
    result_format = str(cfg.get("result_format") or "markdown").strip().lower()
    enabled = bool(cfg.get("enabled", True))

    binary = None
    command_tokens = shlex.split(command) if command else []
    if command_tokens:
        executable = command_tokens[0]
        if os.path.isabs(executable) and os.path.exists(executable):
            binary = executable
        else:
            binary = shutil.which(executable)

    return {
        "provider": provider,
        "mode": mode,
        "enabled": enabled,
        "command": command,
        "command_tokens": command_tokens,
        "binary_path": binary,
        "working_dir": working_dir,
        "working_dir_exists": bool(working_dir and os.path.isdir(working_dir)),
        "timeout_seconds": timeout_seconds,
        "result_format": result_format,
        "install_hint": DEERFLOW_INSTALL_HINT,
    }


def get_research_backend_preflight() -> dict[str, dict]:
    cfg = resolve_research_backend_config()
    return {
        cfg["provider"]: {
            "provider": cfg["provider"],
            "enabled": bool(cfg["enabled"]),
            "configured": bool(cfg["command"]),
            "mode": cfg["mode"],
            "command": cfg["command"],
            "binary_path": cfg["binary_path"],
            "binary_available": bool(cfg["binary_path"]),
            "working_dir": cfg["working_dir"],
            "working_dir_exists": bool(cfg["working_dir_exists"]),
            "timeout_seconds": int(cfg["timeout_seconds"]),
            "result_format": cfg["result_format"],
            "install_hint": cfg["install_hint"],
        }
    }


def _execute_deerflow_cli(prompt: str, timeout: int | None = None, model: str | None = None) -> tuple[int, str, str]:
    cfg = resolve_research_backend_config()
    if not cfg["enabled"]:
        return -1, "", "DeerFlow research backend is disabled"
    if cfg["mode"] != "cli":
        return -1, "", f"Unsupported DeerFlow mode '{cfg['mode']}'"
    if not cfg["command_tokens"]:
        return -1, "", "DeerFlow command is not configured"
    if not cfg["binary_path"]:
        return -1, "", DEERFLOW_INSTALL_HINT
    if cfg["working_dir"] and not cfg["working_dir_exists"]:
        return -1, "", f"Configured DeerFlow working_dir does not exist: {cfg['working_dir']}"

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

    cwd = cfg["working_dir"] or None
    env = os.environ.copy()
    actual_timeout = max(30, int(timeout or cfg["timeout_seconds"]))
    try:
        logging.info(f"DeerFlow-Aufruf: {args} (cwd={cwd})")
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
        logging.error("DeerFlow Timeout")
        return -1, "", "Timeout"
    except Exception as e:
        logging.exception(f"DeerFlow Fehler: {e}")
        return -1, "", str(e)


def run_deerflow_command(prompt: str, timeout: int | None = None, model: str | None = None) -> tuple[int, str, str]:
    result = DeerFlowAdapter().run_sync(prompt=prompt, timeout=timeout, model=model)
    payload = result.get("result") or {}
    return int(payload.get("returncode") or 0), str(payload.get("stdout") or ""), str(payload.get("stderr") or "")


def normalize_research_artifact(
    raw_text: str,
    backend: str = "deerflow",
    task_id: str | None = None,
    cli_result: dict | None = None,
) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    summary = (paragraphs[0] if paragraphs else text).strip()
    if len(summary) > 400:
        summary = summary[:397].rstrip() + "..."

    seen_urls: set[str] = set()
    sources: list[dict[str, Any]] = []
    for match in re.findall(r"https?://[^\s)\]>\"']+", text):
        url = match.rstrip(".,;:")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({"title": url, "url": url, "kind": "web", "confidence": 0.5})

    artifact = {
        "kind": "research_report",
        "summary": summary,
        "report_markdown": text,
        "sources": sources,
        "backend_metadata": {
            "backend": backend,
            "task_id": task_id,
            "generated_at": int(time.time()),
            "source_count": len(sources),
            "cli_result": cli_result or {},
        },
    }
    return artifact
