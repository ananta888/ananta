"""HeuristicRegistry — lädt und verwaltet HeuristicDefinitions aus dem
heuristics/ Storage Layout.

Nur Heuristiken mit status=active dürfen zur Laufzeit genutzt werden.
Versionierung und Rollback werden über den Ordner active/ / archive/ gesteuert.

Policy-Referenz: docs/security/heuristic-runtime-policy.md
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

_VALID_STATUSES = frozenset({"active", "candidate", "rejected", "quarantined", "deprecated", "archived"})

# Capabilities die für snake domains verboten sind
_SNAKE_FORBIDDEN_CAPS = frozenset({"file_write", "network_access", "secret_access", "send_to_worker", "request_context_extension"})
# Capabilities die für chat erlaubt sind (als Obermenge)
_CHAT_ALLOWED_CAPS = frozenset({
    "read_local_context", "read_artifact_refs", "read_active_task",
    "write_local_notes", "send_to_chat",
})
_SNAKE_ALLOWED_CAPS = frozenset({
    "read_local_context", "read_artifact_refs", "read_active_task",
})


@dataclass(frozen=True)
class HeuristicDefinition:
    heuristic_id: str
    version: str
    domain: str
    strategy_kind: str
    description: str
    deterministic: bool
    safety_class: str
    capabilities: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    parameters: dict[str, Any]
    status: str = "active"

    @staticmethod
    def from_dict(data: dict[str, Any], *, status: str = "active") -> "HeuristicDefinition":
        return HeuristicDefinition(
            heuristic_id=str(data["heuristic_id"]).strip(),
            version=str(data["version"]).strip(),
            domain=str(data["domain"]).strip(),
            strategy_kind=str(data.get("strategy_kind") or "").strip(),
            description=str(data.get("description") or "").strip(),
            deterministic=bool(data.get("deterministic", True)),
            safety_class=str(data.get("safety_class") or "bounded").strip(),
            capabilities=tuple(str(c) for c in (data.get("capabilities") or [])),
            inputs=tuple(str(i) for i in (data.get("inputs") or [])),
            outputs=tuple(str(o) for o in (data.get("outputs") or [])),
            parameters=dict(data.get("parameters") or {}),
            status=status,
        )

    def has_capability_violation(self) -> list[str]:
        violations: list[str] = []
        caps = set(self.capabilities)
        domain = self.domain
        if domain in ("snake_tui", "snake_eclipse"):
            forbidden = caps & _SNAKE_FORBIDDEN_CAPS
            for cap in sorted(forbidden):
                violations.append(f"capability_violation:{cap}:not_allowed_for_{domain}")
            if not self.deterministic:
                violations.append("deterministic_required_for_snake_domain")
        elif domain == "chat_codecompass":
            forbidden = caps - _CHAT_ALLOWED_CAPS - frozenset({"write_local_notes"})
            elevated_ok = self.safety_class == "elevated"
            if not elevated_ok:
                for cap in sorted(forbidden):
                    violations.append(f"capability_violation:{cap}:not_allowed_for_{domain}")
        return violations


class HeuristicRegistryError(RuntimeError):
    pass


class HeuristicNotFound(HeuristicRegistryError):
    def __init__(self, heuristic_id: str, domain: str | None = None):
        self.heuristic_id = heuristic_id
        super().__init__(f"heuristic_not_found:{heuristic_id}:{domain or 'any'}")


class HeuristicRegistry:
    """Lädt HeuristicDefinitions aus heuristics/ und stellt sie bereit.

    Heuristiken werden lazy beim ersten Zugriff oder explizit per load_all() geladen.
    Nur active Heuristiken werden für die Runtime bereitgestellt.
    """

    def __init__(self, base_path: str | None = None) -> None:
        self._base_path = base_path or self._default_base_path()
        self._definitions: dict[str, HeuristicDefinition] = {}  # heuristic_id -> active def
        self._all: list[HeuristicDefinition] = []
        self._loaded = False

    @staticmethod
    def _default_base_path() -> str:
        here = os.path.dirname(__file__)
        return os.path.normpath(os.path.join(here, "..", "..", "..", "heuristics"))

    def load_all(self) -> None:
        self._definitions.clear()
        self._all.clear()

        index_path = os.path.join(self._base_path, "index.json")
        if not os.path.exists(index_path):
            self._loaded = True
            return

        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)

        for entry in index.get("heuristics") or []:
            status = str(entry.get("status") or "active").strip().lower()
            file_rel = str(entry.get("file") or "").strip()
            if not file_rel:
                continue
            file_path = os.path.join(self._base_path, file_rel)
            if not os.path.exists(file_path):
                continue
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            hdef = HeuristicDefinition.from_dict(data, status=status)
            self._all.append(hdef)
            if status == "active":
                self._definitions[hdef.heuristic_id] = hdef

        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_all()

    def get_active(self, domain: str) -> list[HeuristicDefinition]:
        self._ensure_loaded()
        domain = str(domain).strip().lower()
        return [h for h in self._definitions.values() if h.domain == domain]

    def get_by_id(self, heuristic_id: str, *, version: str | None = None) -> HeuristicDefinition:
        self._ensure_loaded()
        hid = str(heuristic_id).strip()
        if version:
            for h in self._all:
                if h.heuristic_id == hid and h.version == version:
                    return h
            raise HeuristicNotFound(hid)
        h = self._definitions.get(hid)
        if h is None:
            raise HeuristicNotFound(hid)
        return h

    def list_all(self) -> list[HeuristicDefinition]:
        self._ensure_loaded()
        return list(self._all)

    def list_by_status(self, status: str) -> list[HeuristicDefinition]:
        self._ensure_loaded()
        s = str(status).strip().lower()
        return [h for h in self._all if h.status == s]

    def register_in_memory(self, hdef: HeuristicDefinition) -> None:
        """Fügt eine Heuristik zur In-Memory-Registry hinzu (nur für Tests)."""
        self._ensure_loaded()
        self._all.append(hdef)
        if hdef.status == "active":
            self._definitions[hdef.heuristic_id] = hdef

    def reload(self) -> None:
        self._loaded = False
        self.load_all()


_REGISTRY: HeuristicRegistry | None = None


def get_heuristic_registry() -> HeuristicRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = HeuristicRegistry()
    return _REGISTRY
