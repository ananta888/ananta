"""Tests for agent.services.di — DI-adapter-layer with call-time repository lookups.

SOLID checks: tests verify DIP (call-time lookup, not module-import cache),
LSP (substitutability of factory return values), SRP (factory functions
only — no domain logic).
"""
from __future__ import annotations


def test_di_module_imports():
    from agent.services import di  # noqa: F401
    assert hasattr(di, "get_memory_entry_repository")


def test_get_memory_entry_repository_returns_singleton():
    from agent.services.di import get_memory_entry_repository
    repo_a = get_memory_entry_repository()
    repo_b = get_memory_entry_repository()
    assert repo_a is repo_b  # idempotent call-time lookup


def test_get_memory_entry_repository_uses_late_binding(monkeypatch):
    """The factory must look up the symbol at call time, not at import time.
    Patching `agent.services.di.memory_entry_repo` must affect subsequent
    factory calls — this is the DIP guarantee.
    """
    from agent.services import di

    class _FakeRepo:
        pass

    fake = _FakeRepo()
    monkeypatch.setattr(di, "memory_entry_repo", fake)
    assert di.get_memory_entry_repository() is fake


def test_factory_completeness_for_service_level_imports():
    """For every repo imported at module level in agent/services/, the di module
    must expose a corresponding factory. This enforces OCP at the di-layer
    boundary — adding a new repo requires adding a factory."""
    import re
    from pathlib import Path

    from agent.services import di

    services_dir = Path("agent/services")
    imported: set[str] = set()
    pattern = re.compile(r"^from agent\.repository import ([^\n]+)$", re.MULTILINE)
    for py in services_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in pattern.finditer(text):
            payload = match.group(1)
            for raw in payload.split(","):
                name = raw.strip().split(" as ")[0].strip()
                if name and name.endswith("_repo"):
                    imported.add(name)

    # Subset: nur die, die wir aktuell abdecken (memory_entry_repo + alle 59)
    covered = {
        name
        for name in imported
        if hasattr(di, f"get_{name[:-5]}_repository")
    }
    missing = imported - covered
    assert not missing, f"di.py is missing factories for: {sorted(missing)}"


def test_factory_substitutability(monkeypatch):
    """LSP: any object that has the right protocol can stand in. We patch
    memory_entry_repo to a MagicMock and confirm the factory returns it
    without error."""
    from unittest.mock import MagicMock

    from agent.services import di

    fake = MagicMock()
    monkeypatch.setattr(di, "memory_entry_repo", fake)
    result = di.get_memory_entry_repository()
    assert result is fake
