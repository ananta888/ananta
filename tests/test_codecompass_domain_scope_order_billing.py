"""CCRDS-019: end-to-end mini fixture for the Bestellmodul/Rechnung case.

The motivating example: a user selects ``Bestellmodul`` and asks for
``Rechnungserzeugung``. A *prompt mention* of invoices must not expand
retrieval into Artikelkatalog or other areas — only the explicit domain
scope decides. The fixture project has ``orders/``, ``catalog/`` and
``billing/`` plus a realistic ``domains.detected.json``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent.codecompass.domain_scope import DomainScope
from agent.codecompass.domain_scope_resolver import DomainScopeResolver
from agent.hybrid_orchestrator import HybridOrchestrator

FIXTURE = Path(__file__).parent / "fixtures" / "codecompass_domain_scope_order_billing"

QUERY = "Rechnungserzeugung soll ins Bestellmodul: wo ist der invoice service?"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    # Copy so the repository-map cache and any artifacts stay test-local.
    target = tmp_path / "shop"
    shutil.copytree(FIXTURE, target)
    return target


def _resolve(project: Path, *, strict: bool = True, external: int = 0) -> object:
    resolver = DomainScopeResolver(repo_root=project)
    return resolver.resolve(
        DomainScope(
            selected_domain_ids=["bestellmodul"],
            strict=strict,
            allow_external_references=external > 0,
            max_external_reference_chunks=external,
        )
    )


def test_strict_orders_scope_excludes_catalog(project: Path) -> None:
    resolved = _resolve(project)
    assert resolved.ok
    assert resolved.allowed_read_paths == ["orders"]

    orchestrator = HybridOrchestrator(repo_root=project, data_roots=[], max_context_chars=6000)
    result = orchestrator.get_relevant_context(QUERY, domain_scope=resolved)

    sources = [c["source"] for c in result["chunks"]]
    assert sources, "expected in-scope context for the orders domain"
    assert all(not s.startswith("catalog/") for s in sources)
    assert all(not s.startswith("billing/") for s in sources)
    assert any("orders/" in s for s in sources)
    assert "DOMAIN-SCOPE AKTIV" in result["context_text"]


def test_relation_expansion_marks_billing_as_external_reference(project: Path) -> None:
    from agent.codecompass.domain_scope_filter import filter_chunks
    from agent.hybrid_orchestrator import ContextChunk

    resolved = _resolve(project, external=1)
    chunks = [
        ContextChunk(engine="repository_map", source="orders/order_service.py", content="x", score=2.0),
        ContextChunk(engine="repository_map", source="billing/invoice_renderer.py", content="y", score=1.5),
        ContextChunk(engine="repository_map", source="catalog/catalog_service.py", content="z", score=1.0),
    ]
    kept, stats = filter_chunks(chunks, resolved, repo_root=project)
    kept_sources = {c.source for c in kept}
    assert "orders/order_service.py" in kept_sources
    # Exactly one external reference allowed; it is explicitly marked.
    external = [c for c in kept if c.metadata.get("domain_scope_external_reference") == "true"]
    assert len(external) == 1
    assert stats.dropped == 1


def test_without_scope_normal_ranking_spans_areas(project: Path) -> None:
    orchestrator = HybridOrchestrator(repo_root=project, data_roots=[], max_context_chars=6000)
    result = orchestrator.get_relevant_context(QUERY)
    sources = {c["source"] for c in result["chunks"]}
    # The invoice token appears in several areas — without a scope nothing
    # restricts retrieval to orders/.
    assert any(s.startswith("orders/") for s in sources)
    assert any(s.startswith("catalog/") or s.startswith("billing/") for s in sources)
    assert "domain_scope" not in result


def test_prompt_mention_vs_explicit_scope(project: Path) -> None:
    """Der Unterschied: der Prompt erwähnt 'invoice' überall, aber nur der
    explizit gesetzte Domain-Scope begrenzt den Kontext (CCRDS-DD-002)."""
    resolved = _resolve(project)
    orchestrator = HybridOrchestrator(repo_root=project, data_roots=[], max_context_chars=6000)

    scoped = orchestrator.get_relevant_context(QUERY, domain_scope=resolved)
    unscoped = orchestrator.get_relevant_context(QUERY)

    scoped_sources = {c["source"] for c in scoped["chunks"]}
    unscoped_sources = {c["source"] for c in unscoped["chunks"]}
    assert all(s.startswith("orders/") for s in scoped_sources if "/" in s)
    assert scoped_sources != unscoped_sources
