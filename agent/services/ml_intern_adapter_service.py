from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from agent.routes.config.shared import normalize_ml_intern_spike_config


class MlInternAdapterService:
    """Bounded external-worker adapter for optional ml-intern backend spikes."""

    @staticmethod
    def _resolve_config(agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = dict(agent_cfg or {})
        return normalize_ml_intern_spike_config(cfg.get("ml_intern_spike") if isinstance(cfg.get("ml_intern_spike"), dict) else {})

    @staticmethod
    def _resolve_working_dir(value: str | None) -> Path:
        repo_root = Path.cwd().resolve()
        requested = str(value or "").strip()
        if not requested:
            return repo_root
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            candidate.relative_to(repo_root)
        except Exception:
            return repo_root
        if candidate.exists() and candidate.is_dir():
            return candidate
        return repo_root

    @staticmethod
    def _bounded_env(env_allowlist: list[str]) -> dict[str, str]:
        base_keys = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER"}
        env: dict[str, str] = {}
        for key in base_keys:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        for key in env_allowlist:
            value = os.environ.get(str(key))
            if value is not None:
                env[str(key)] = value
        return env

    def invoke_spike(
        self,
        *,
        prompt: str,
        agent_cfg: dict[str, Any] | None,
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config(agent_cfg)
        if not bool(config.get("enabled")):
            return {"ok": False, "error": "ml_intern_spike_disabled", "config": config}
        command_template = str(config.get("command_template") or "").strip()
        if not command_template:
            return {"ok": False, "error": "ml_intern_command_template_missing", "config": config}

        max_prompt_chars = int(config.get("max_prompt_chars") or 6000)
        if len(prompt) > max_prompt_chars:
            return {
                "ok": False,
                "error": "ml_intern_prompt_too_large",
                "details": {"max_prompt_chars": max_prompt_chars, "actual_prompt_chars": len(prompt)},
            }

        effective_timeout = int(timeout_seconds or config.get("timeout_seconds") or 180)
        effective_timeout = max(10, min(effective_timeout, 900))
        max_output_chars = int(config.get("max_output_chars") or 8000)
        working_dir = self._resolve_working_dir(config.get("working_dir"))
        env = self._bounded_env(list(config.get("env_allowlist") or []))
        started_at = time.time()

        prompt_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(working_dir),
                prefix="ml-intern-spike-",
                suffix=".prompt.txt",
                delete=False,
            ) as handle:
                handle.write(prompt)
                prompt_file = str(handle.name)

            tokens = shlex.split(command_template)
            args = [
                token.format(
                    prompt=prompt,
                    prompt_file=prompt_file,
                    model=str(model or ""),
                )
                for token in tokens
            ]
            completed = subprocess.run(
                args,
                cwd=str(working_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
            stdout = str(completed.stdout or "")
            stderr = str(completed.stderr or "")
            return {
                "ok": completed.returncode == 0,
                "backend": "ml_intern",
                "returncode": int(completed.returncode),
                "stdout": stdout[:max_output_chars],
                "stderr": stderr[:max_output_chars],
                "stdout_truncated": len(stdout) > max_output_chars,
                "stderr_truncated": len(stderr) > max_output_chars,
                "bounded_execution": {
                    "timeout_seconds": effective_timeout,
                    "max_prompt_chars": max_prompt_chars,
                    "max_output_chars": max_output_chars,
                    "working_dir": str(working_dir),
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "backend": "ml_intern",
                "error": "ml_intern_timeout",
                "bounded_execution": {
                    "timeout_seconds": effective_timeout,
                    "max_prompt_chars": max_prompt_chars,
                    "max_output_chars": max_output_chars,
                    "working_dir": str(working_dir),
                    "duration_ms": int((time.time() - started_at) * 1000),
                },
            }
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            return {
                "ok": False,
                "backend": "ml_intern",
                "error": "ml_intern_invocation_failed",
                "details": {"message": str(exc)},
            }
        finally:
            if prompt_file:
                try:
                    Path(prompt_file).unlink(missing_ok=True)
                except OSError:
                    pass


ml_intern_adapter_service = MlInternAdapterService()


def get_ml_intern_adapter_service() -> MlInternAdapterService:
    return ml_intern_adapter_service
