"""Loads seed role-prompt templates and role-profile defaults from config files.

Primary file: config/blueprints/standard/templates.json
Optional fragments: config/blueprints/standard/templates.d/*.json
Schema: schemas/blueprints/seed_template_catalog.v1.json

Appendix references ({{appendix:name}}) in prompt_template are expanded during load.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

_APPENDIX_RE = re.compile(r"\{\{appendix:([^}]+)\}\}")


class SeedTemplateCatalog:
    def __init__(
        self,
        *,
        catalog_path: Path | None = None,
        schema_path: Path | None = None,
        repository_root: Path | None = None,
        fragments_dir: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.catalog_path = catalog_path or (
            self.repository_root / "config" / "blueprints" / "standard" / "templates.json"
        )
        self.fragments_dir = fragments_dir or self.catalog_path.parent / "templates.d"
        self.schema_path = schema_path or (
            self.repository_root / "schemas" / "blueprints" / "seed_template_catalog.v1.json"
        )
        self._catalog: dict[str, Any] | None = None
        self.load_error: str | None = None

    # ── public API ────────────────────────────────────────────────────────────
    def get_templates_for_team_type(self, team_type: str) -> list[dict[str, str]]:
        """Return list of {name, description, prompt_template} for a team type (appendixes expanded)."""
        if not self._ensure_loaded():
            return []
        result = []
        for tpl in self._catalog.get("templates") or []:
            if str(tpl.get("team_type") or "") == team_type:
                result.append({
                    "name": str(tpl["name"]),
                    "description": str(tpl.get("description") or ""),
                    "prompt_template": self._expand(str(tpl.get("prompt_template") or "")),
                })
        return result

    def get_role_specs_for_team_type(self, team_type: str) -> list[dict[str, str]]:
        """Return list of {name, description, template_name} for a team type."""
        if not self._ensure_loaded():
            return []
        tt = (self._catalog.get("team_types") or {}).get(team_type) or {}
        return copy.deepcopy(list(tt.get("roles") or []))

    def get_role_profile_defaults(self, team_type: str, role_name: str) -> dict[str, Any]:
        """Return capability/risk/verification defaults for a role within a team type."""
        if not self._ensure_loaded():
            return {}
        tt = (self._catalog.get("team_types") or {}).get(team_type) or {}
        defaults = (tt.get("role_profile_defaults") or {}).get(role_name) or {}
        return copy.deepcopy(dict(defaults))

    def get_all_templates(self) -> list[dict[str, str]]:
        """Return all seed templates with appendixes expanded."""
        if not self._ensure_loaded():
            return []
        result = []
        for tpl in self._catalog.get("templates") or []:
            result.append({
                "name": str(tpl["name"]),
                "description": str(tpl.get("description") or ""),
                "team_type": str(tpl.get("team_type") or ""),
                "prompt_template": self._expand(str(tpl.get("prompt_template") or "")),
            })
        return result

    def known_team_types(self) -> list[str]:
        if not self._ensure_loaded():
            return []
        return list((self._catalog.get("team_types") or {}).keys())

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
        payload = self._load_merged_payload()
        try:
            from jsonschema import Draft202012Validator
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
            errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
            if errors:
                msgs = "; ".join(f"{'.'.join(map(str,e.path)) or '<root>'}: {e.message}" for e in errors)
                raise ValueError(f"invalid seed template catalog {self.catalog_path}: {msgs}")
        except ImportError:
            pass  # jsonschema not available — skip validation

        # Validate appendix references in templates
        appendixes: dict[str, str] = dict(payload.get("appendixes") or {})
        for tpl in payload.get("templates") or []:
            for ref in _APPENDIX_RE.findall(str(tpl.get("prompt_template") or "")):
                if ref not in appendixes:
                    raise ValueError(f"template '{tpl.get('name')}' references unknown appendix: '{ref}'")

        self._catalog = payload

    def _load_merged_payload(self) -> dict[str, Any]:
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        merged: dict[str, Any] = {
            "schema": payload.get("schema"),
            "version": payload.get("version"),
            "appendixes": dict(payload.get("appendixes") or {}),
            "team_types": copy.deepcopy(dict(payload.get("team_types") or {})),
            "templates": copy.deepcopy(list(payload.get("templates") or [])),
        }
        if self.fragments_dir.exists():
            for fragment_path in sorted(self.fragments_dir.glob("*.json")):
                fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
                merged["appendixes"].update(dict(fragment.get("appendixes") or {}))
                for team_type, spec in dict(fragment.get("team_types") or {}).items():
                    if team_type in merged["team_types"]:
                        raise ValueError(f"duplicate seed template team_type {team_type!r} in {fragment_path}")
                    merged["team_types"][team_type] = copy.deepcopy(spec)
                merged["templates"].extend(copy.deepcopy(list(fragment.get("templates") or [])))
        seen_templates: set[str] = set()
        for template in merged["templates"]:
            name = str((template or {}).get("name") or "").strip()
            if not name:
                continue
            if name.lower() in seen_templates:
                raise ValueError(f"duplicate seed template name: {name}")
            seen_templates.add(name.lower())
        return merged

    def _expand(self, text: str) -> str:
        appendixes: dict[str, str] = dict((self._catalog or {}).get("appendixes") or {})
        def _replace(m: re.Match) -> str:
            return appendixes.get(m.group(1), m.group(0))
        return _APPENDIX_RE.sub(_replace, text)


_catalog = SeedTemplateCatalog()


def get_seed_template_catalog() -> SeedTemplateCatalog:
    return _catalog
