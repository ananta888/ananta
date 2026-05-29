"""Loads and serves internal system prompts from config/system_prompts.json.

System prompts are identified by names starting with "system." and are stored
in TemplateDB with is_seed=True like role-prompt templates.

Usage in services:
    from agent.services.system_prompt_catalog import get_system_prompt

    # Static prompt (no runtime variables):
    PROMPT = get_system_prompt("system.json_normalization")

    # Dynamic prompt (with runtime variables):
    template = get_system_prompt("system.llm_repair")
    prompt = template.format(previous_raw=..., validation_errors=..., ...)
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# Module-level cache — populated on first call to get_system_prompt()
_PROMPTS: dict[str, str] | None = None


class SystemPromptCatalog:
    def __init__(
        self,
        *,
        catalog_path: Path | None = None,
        schema_path: Path | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.catalog_path = catalog_path or (self.repository_root / "config" / "system_prompts.json")
        self.schema_path = schema_path or (
            self.repository_root / "schemas" / "system_prompt_catalog.v1.json"
        )
        self._catalog: dict[str, Any] | None = None
        self.load_error: str | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def get_prompt(self, name: str) -> str | None:
        """Return the prompt_template string for the given system prompt name, or None."""
        if not self._ensure_loaded():
            return None
        entry = next((p for p in (self._catalog.get("prompts") or []) if p.get("name") == name), None)
        return str(entry["prompt_template"]) if entry else None

    def get_prompt_with_fallback(self, name: str, fallback: str) -> str:
        """Return prompt_template or fallback if not found."""
        return self.get_prompt(name) or fallback

    def get_all_prompts(self) -> list[dict[str, Any]]:
        """Return all system prompt entries (name, label, description, service, variables, prompt_template)."""
        if not self._ensure_loaded():
            return []
        return copy.deepcopy(list(self._catalog.get("prompts") or []))

    def known_names(self) -> list[str]:
        if not self._ensure_loaded():
            return []
        return [str(p.get("name") or "") for p in (self._catalog.get("prompts") or []) if p.get("name")]

    # ── internals ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> bool:
        try:
            self._load()
            return True
        except (OSError, ValueError) as exc:
            self.load_error = str(exc)
            return False

    def _load(self) -> None:
        if self._catalog is not None:
            return
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        try:
            from jsonschema import Draft202012Validator
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
            errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
            if errors:
                msgs = "; ".join(f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
                raise ValueError(f"invalid system prompt catalog {self.catalog_path}: {msgs}")
        except ImportError:
            pass
        self._catalog = payload


_catalog = SystemPromptCatalog()


def get_system_prompt_catalog() -> SystemPromptCatalog:
    return _catalog


def get_system_prompt(name: str, fallback: str = "") -> str:
    """Convenience: return prompt_template for name, or fallback string."""
    return _catalog.get_prompt_with_fallback(name, fallback)
