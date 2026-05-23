from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_THREE_WORKER_RUN_CONFIG_PATH = "config/runs/three-worker-local-planning.yaml"


class ThreeWorkerRunConfigError(ValueError):
    pass


class ThreeWorkerRunConfigService:
    """Resolve and validate the three-worker comparison run config.

    The important invariant is intentionally simple and hard:
    planning must be local-only, using either LM Studio or Ollama. Hermes may use
    OpenRouter for its governed read-only worker track, but never for planning.
    """

    def __init__(self, *, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root or Path.cwd()).resolve()

    def load(self, path: str | Path | None = None) -> dict[str, Any]:
        cfg_path = self._resolve_path(path or os.getenv("ANANTA_THREE_WORKER_RUN_CONFIG") or DEFAULT_THREE_WORKER_RUN_CONFIG_PATH)
        if not cfg_path.exists():
            raise ThreeWorkerRunConfigError(f"three_worker_run_config_missing:{cfg_path}")
        with cfg_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ThreeWorkerRunConfigError("three_worker_run_config_must_be_mapping")
        return data

    def resolve(self, path: str | Path | None = None, *, env: dict[str, str] | None = None) -> dict[str, Any]:
        raw = self.load(path)
        resolved = self._resolve_planning(raw, env=dict(env or os.environ))
        self.validate(resolved)
        return resolved

    def validate(self, cfg: dict[str, Any]) -> None:
        planning = cfg.get("planning") if isinstance(cfg.get("planning"), dict) else {}
        if not bool(planning.get("force_local")):
            raise ThreeWorkerRunConfigError("planning_must_force_local")
        provider = str((planning.get("resolved") or {}).get("provider") or planning.get("default_provider") or "").strip().lower()
        if provider not in {"lmstudio", "ollama"}:
            raise ThreeWorkerRunConfigError(f"planning_provider_must_be_local:{provider or '<empty>'}")

        expectations = cfg.get("routing_expectations") if isinstance(cfg.get("routing_expectations"), dict) else {}
        planning_expectations = expectations.get("planning") if isinstance(expectations.get("planning"), dict) else {}
        forbidden = {str(item).strip().lower() for item in planning_expectations.get("must_not_use", []) if str(item).strip()}
        if provider in forbidden:
            raise ThreeWorkerRunConfigError(f"planning_provider_forbidden:{provider}")

        tracks = cfg.get("tracks") if isinstance(cfg.get("tracks"), list) else []
        track_ids = {str(track.get("id") or "").strip() for track in tracks if isinstance(track, dict)}
        required = {"hermes", "opencode-local", "ananta-worker-local"}
        missing = sorted(required - track_ids)
        if missing:
            raise ThreeWorkerRunConfigError(f"three_worker_tracks_missing:{','.join(missing)}")

        for track in tracks:
            if not isinstance(track, dict) or not bool(track.get("enabled", True)):
                continue
            track_id = str(track.get("id") or "").strip()
            planning_provider = str(track.get("planning_provider") or "").strip().lower()
            if planning_provider != "local":
                raise ThreeWorkerRunConfigError(f"track_planning_must_be_local:{track_id}")
            if track_id == "hermes" and not str(track.get("config_ref") or "").strip():
                raise ThreeWorkerRunConfigError("hermes_track_requires_config_ref")
            if track_id in {"opencode-local", "ananta-worker-local"}:
                execution_provider = str(track.get("execution_provider") or "").strip().lower()
                if execution_provider != "local":
                    raise ThreeWorkerRunConfigError(f"local_track_execution_must_be_local:{track_id}")

    def build_provider_entries(self, cfg: dict[str, Any]) -> list[str]:
        """Return provider entries compatible with existing compare provider syntax."""
        tracks = cfg.get("tracks") if isinstance(cfg.get("tracks"), list) else []
        entries: list[str] = []
        for track in tracks:
            if not isinstance(track, dict) or not bool(track.get("enabled", True)):
                continue
            backend = str(track.get("requested_backend") or track.get("worker_type") or "").strip()
            model = str(((cfg.get("planning") or {}).get("resolved") or {}).get("model") or "").strip()
            if backend:
                entries.append(f"{backend}:{model}" if model else backend)
        return entries

    def _resolve_planning(self, cfg: dict[str, Any], *, env: dict[str, str]) -> dict[str, Any]:
        resolved = dict(cfg)
        planning = dict(resolved.get("planning") or {})
        provider_env = str(planning.get("provider_env") or "PLANNING_PROVIDER")
        provider = str(env.get(provider_env) or planning.get("default_provider") or "lmstudio").strip().lower()
        provider_cfg = planning.get(provider) if isinstance(planning.get(provider), dict) else {}
        if provider not in {"lmstudio", "ollama"}:
            planning["resolved"] = {"provider": provider, "error": "unsupported_local_planning_provider"}
        else:
            base_url_env = str(provider_cfg.get("base_url_env") or "")
            model_env = str(provider_cfg.get("model_env") or "")
            api_key_env = str(provider_cfg.get("api_key_env") or "")
            planning["resolved"] = {
                "provider": provider,
                "base_url": env.get(base_url_env) or provider_cfg.get("default_base_url"),
                "model": env.get(model_env) or provider_cfg.get("default_model"),
                "api_key_env": api_key_env or None,
                "api_key": env.get(api_key_env) or provider_cfg.get("default_api_key"),
                "cloud_allowed": False,
                "source": provider_env if env.get(provider_env) else "config_default",
            }
        resolved["planning"] = planning
        return resolved

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return (self.repo_root / candidate).resolve()


def get_three_worker_run_config_service() -> ThreeWorkerRunConfigService:
    return ThreeWorkerRunConfigService()
