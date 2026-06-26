"""UserConfigService — canonical reader for user.json as global config dict.

Single source of truth for reading the user-level config file that the
Config Graph Editor writes to. Other services (HybridOrchestrator,
RestrictedInferenceConfigService, ...) should obtain the global config
from here rather than reading user.json directly.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class UserConfigService:
    """Reads and caches the user.json config file from repo root."""

    def __init__(self, repo_root: str | Path) -> None:
        self._path = Path(repo_root).resolve() / "user.json"
        self._cache: dict[str, Any] | None = None

    @property
    def config(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def refresh(self) -> None:
        self._cache = None

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            log.warning("UserConfigService: cannot read %s: %s", self._path, exc)
            return {}


_service: UserConfigService | None = None


def get_user_config_service(repo_root: str | Path | None = None) -> UserConfigService:
    global _service
    if _service is None:
        from agent.config import settings
        root = repo_root or getattr(settings, "rag_repo_root", None) or Path(".")
        _service = UserConfigService(root)
    return _service
