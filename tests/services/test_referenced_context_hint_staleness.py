"""RCHCS-008: Integration tests for staleness, generators, service, and prompt integration.

Key invariants tested:
1. Unreferenced non-manual hint → rejected.
2. Hint can improve ranking (confidence-sorted retrieval).
3. Original source wins over hint (staleness + task validity).
4. Deterministic generator works without LLM key.
5. Stale hint excluded from authoritative tasks.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.services.referenced_context_hint_schema import (
    GEN_DETERMINISTIC,
    GEN_LLM,
    GEN_MANUAL,
    KIND_FILE_SUMMARY,
    KIND_SYMBOL_HINT,
    KIND_TEST_HINT,
    STALENESS_FRESH,
    STALENESS_INVALID,
    STALENESS_POSSIBLY_STALE,
    STALENESS_STALE,
    GeneratorMetadata,
    HashMetadata,
    HintValidationError,
    ReferencedContextHint,
    SourceRef,
    ValidityMetadata,
    make_hint_id,
)
from agent.services.referenced_context_hint_store import ReferencedContextHintStore
from agent.services.referenced_context_hint_generators import (
    DeterministicHintGenerator,
    LlmHintGenerator,
    ManualHintImporter,
    HybridHintGenerator,
)
from agent.services.referenced_context_hint_service import ReferencedContextHintService


def tmp_store() -> ReferencedContextHintStore:
    return ReferencedContextHintStore(tempfile.mkdtemp())


def tmp_svc(store=None) -> ReferencedContextHintService:
    return ReferencedContextHintService(store=store or tmp_store())


# ── KEY INVARIANT 1: Unreferenced hint rejected ───────────────────────────────

def test_unreferenced_non_manual_hint_rejected():
    """Non-manual hint with no source_refs or codecompass_refs → HintValidationError."""
    store = tmp_store()
    h = ReferencedContextHint(
        id="hint:file_summary:foo.py:abc123",
        kind=KIND_FILE_SUMMARY,
        title="Unreferenced",
        summary="This hint has no refs.",
        source_refs=[],  # ← no refs
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
    )
    with pytest.raises(HintValidationError, match="source_ref"):
        store.put(h)


def test_manual_hint_without_refs_allowed_for_domain_scope():
    """Manual hint with domain scope may omit source_refs."""
    store = tmp_store()
    importer = ManualHintImporter(require_source_refs=False)
    h = importer.import_hint({
        "id": "hint:domain_summary:auth:manual001",
        "kind": "domain_summary",
        "title": "Auth domain",
        "summary": "Auth handles login, OAuth, MFA flows.",
        "source_refs": [],
        "generator": {"kind": "manual"},
        "validity": {"scope": "domain", "valid_for_tasks": ["navigation"], "not_valid_for": []},
        "confidence": {"score": 0.7},
    })
    store.put(h)
    assert store.get(h.id) is not None


# ── KEY INVARIANT 2: Hint improves ranking ────────────────────────────────────

def test_hint_can_improve_candidate_ranking():
    """hints_for_candidates returns hints sorted by confidence — high-confidence hints first."""
    store = tmp_store()
    h_high = ReferencedContextHint(
        id=make_hint_id("agent/hot.py", KIND_FILE_SUMMARY),
        kind=KIND_FILE_SUMMARY,
        title="Hot module summary",
        summary="This module handles the hot path.",
        source_refs=[SourceRef(path="agent/hot.py", sha256="aaa")],
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
        hashes=HashMetadata(source_hash="aaa"),
    )
    h_high.confidence.score = 0.95

    h_low = ReferencedContextHint(
        id=make_hint_id("agent/cold.py", KIND_FILE_SUMMARY),
        kind=KIND_FILE_SUMMARY,
        title="Cold module summary",
        summary="This module is rarely used.",
        source_refs=[SourceRef(path="agent/cold.py", sha256="bbb")],
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
        hashes=HashMetadata(source_hash="bbb"),
    )
    h_low.confidence.score = 0.30

    store.put(h_high)
    store.put(h_low)

    svc = tmp_svc(store=store)
    candidates = [{"path": "agent/hot.py"}, {"path": "agent/cold.py"}]
    hints = svc.hints_for_candidates(candidates, task="context_selection")
    assert len(hints) >= 1
    scores = [h["confidence"] for h in hints]
    assert scores == sorted(scores, reverse=True)


# ── KEY INVARIANT 3: Original source wins ─────────────────────────────────────

def test_stale_hint_excluded_from_authoritative_task():
    """Stale hint not returned for 'authoritative_code_review'."""
    store = tmp_store()
    h = ReferencedContextHint(
        id=make_hint_id("agent/secure.py", KIND_FILE_SUMMARY),
        kind=KIND_FILE_SUMMARY,
        title="Secure module",
        summary="Handles secrets.",
        source_refs=[SourceRef(path="agent/secure.py", sha256="hash1")],
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
        hashes=HashMetadata(source_hash="hash1"),
    )
    h.staleness_status = STALENESS_STALE
    store.put(h)

    svc = tmp_svc(store=store)
    results = svc.get_hints(path="agent/secure.py", task="authoritative_code_review")
    assert len(results) == 0


def test_security_claim_not_valid_for_hint():
    """Hint is not safe_for_task('security_claim_without_original_source') by design."""
    h = ReferencedContextHint(
        id=make_hint_id("agent/auth.py", KIND_FILE_SUMMARY),
        kind=KIND_FILE_SUMMARY,
        title="Auth module",
        summary="Handles tokens.",
        source_refs=[SourceRef(path="agent/auth.py", sha256="h")],
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
    )
    assert not h.is_safe_for_task("security_claim_without_original_source")
    assert not h.is_safe_for_task("authoritative_code_review")


def test_fresh_hint_valid_for_navigation():
    h = ReferencedContextHint(
        id=make_hint_id("agent/foo.py", KIND_FILE_SUMMARY),
        kind=KIND_FILE_SUMMARY,
        title="Foo",
        summary="Foo module.",
        source_refs=[SourceRef(path="agent/foo.py", sha256="h")],
        generator=GeneratorMetadata(kind=GEN_DETERMINISTIC),
    )
    assert h.is_safe_for_task("navigation")
    assert h.is_safe_for_task("explanation")
    assert h.is_safe_for_task("context_selection")


# ── KEY INVARIANT 4: Deterministic generator without LLM ──────────────────────

def test_deterministic_generator_produces_file_summary(tmp_path):
    py_file = tmp_path / "my_module.py"
    py_file.write_text(
        '"""My module."""\n\nclass MyClass:\n    """Does things."""\n    def my_method(self): pass\n\ndef my_func(): pass\n'
    )
    gen = DeterministicHintGenerator()
    hint = gen.generate_file_summary(py_file, workspace_root=tmp_path)
    assert hint is not None
    assert hint.kind == KIND_FILE_SUMMARY
    assert "MyClass" in hint.summary or "my_func" in hint.summary
    assert hint.source_refs[0].path is not None
    assert hint.source_refs[0].sha256 != ""
    assert hint.generator.kind == GEN_DETERMINISTIC


def test_deterministic_generator_produces_symbol_hints(tmp_path):
    py_file = tmp_path / "symbols.py"
    py_file.write_text(
        'class Alpha:\n    """Alpha class."""\n    pass\n\ndef beta():\n    """Beta function."""\n    pass\n'
    )
    gen = DeterministicHintGenerator()
    hints = gen.generate_symbol_hints(py_file, workspace_root=tmp_path)
    names = [h.title for h in hints]
    assert any("Alpha" in n for n in names)
    assert any("beta" in n for n in names)


def test_deterministic_generator_produces_test_hint(tmp_path):
    test_file = tmp_path / "test_foo.py"
    test_file.write_text(
        "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n"
    )
    gen = DeterministicHintGenerator()
    hint = gen.generate_test_hint(test_file, workspace_root=tmp_path)
    assert hint is not None
    assert "3" in hint.summary
    assert hint.kind == KIND_TEST_HINT


def test_deterministic_generator_nonexistent_file():
    gen = DeterministicHintGenerator()
    hint = gen.generate_file_summary("/no/such/file.py")
    assert hint is None


# ── Manual hint importer ──────────────────────────────────────────────────────

def test_manual_hint_importer_accepts_valid():
    importer = ManualHintImporter(owner="dev@example.com")
    raw = {
        "id": "hint:file_summary:agent/foo.py:manual001",
        "kind": KIND_FILE_SUMMARY,
        "title": "Manual note on foo.py",
        "summary": "This file handles the core loop.",
        "source_refs": [{"path": "agent/foo.py", "sha256": "abc"}],
        "generator": {"kind": "manual"},
        "validity": {"scope": "file", "valid_for_tasks": ["navigation"],
                     "not_valid_for": ["authoritative_code_review",
                                        "security_claim_without_original_source"]},
        "confidence": {"score": 0.8},
    }
    hint = importer.import_hint(raw)
    assert hint.generator.kind == GEN_MANUAL
    assert hint.confidence.requires_human_review is False


def test_manual_hint_importer_rejects_no_refs():
    importer = ManualHintImporter(require_source_refs=True)
    raw = {
        "id": "hint:file_summary:agent/foo.py:manual002",
        "kind": KIND_FILE_SUMMARY,
        "title": "No refs",
        "summary": "Missing references.",
        "source_refs": [],
        "generator": {"kind": "manual"},
        "validity": {"scope": "file", "valid_for_tasks": [], "not_valid_for": []},
        "confidence": {"score": 0.5},
    }
    with pytest.raises(HintValidationError):
        importer.import_hint(raw)


def test_manual_hint_import_many():
    importer = ManualHintImporter(require_source_refs=False)
    raws = [
        {
            "id": f"hint:domain_summary:auth:m{i}",
            "kind": "domain_summary",
            "title": f"Domain note {i}",
            "summary": f"Auth note {i}.",
            "source_refs": [],
            "generator": {"kind": "manual"},
            "validity": {"scope": "domain", "valid_for_tasks": ["navigation"],
                         "not_valid_for": ["authoritative_code_review",
                                            "security_claim_without_original_source"]},
            "confidence": {"score": 0.7},
        }
        for i in range(3)
    ]
    hints, errors = importer.import_many(raws)
    assert len(hints) == 3
    assert errors == []


# ── LLM generator (disabled / no generate_fn) ────────────────────────────────

def test_llm_generator_returns_none_when_not_allowed():
    gen = LlmHintGenerator(generate_fn=None, policy_allows_llm=False)
    result = gen.generate_file_summary("/any/file.py")
    assert result is None


def test_llm_generator_returns_none_when_no_fn():
    gen = LlmHintGenerator(generate_fn=None, policy_allows_llm=True)
    result = gen.generate_file_summary("/any/file.py")
    assert result is None


def test_llm_generator_uses_fn(tmp_path):
    py_file = tmp_path / "llm_subject.py"
    py_file.write_text("# subject\ndef foo(): pass\n")

    calls: list[str] = []
    def fake_generate(prompt: str) -> str:
        calls.append(prompt)
        return "This module provides the foo utility."

    gen = LlmHintGenerator(generate_fn=fake_generate, model_id="test-model", policy_allows_llm=True)
    hint = gen.generate_file_summary(py_file, workspace_root=tmp_path)
    assert hint is not None
    assert "foo utility" in hint.summary
    assert hint.generator.kind == GEN_LLM
    assert hint.generator.model_id == "test-model"
    assert len(calls) == 1


# ── Hybrid generator ──────────────────────────────────────────────────────────

def test_hybrid_falls_back_to_deterministic_when_no_llm(tmp_path):
    py_file = tmp_path / "hybrid.py"
    py_file.write_text("class HybridClass:\n    pass\n")
    gen = HybridHintGenerator(llm=None)
    hint = gen.generate_file_summary(py_file, workspace_root=tmp_path)
    assert hint is not None
    assert hint.generator.kind == GEN_DETERMINISTIC


def test_hybrid_marks_as_hybrid_when_llm_available(tmp_path):
    from agent.services.referenced_context_hint_generators import GEN_HYBRID
    py_file = tmp_path / "hybrid2.py"
    py_file.write_text("class Foo:\n    pass\n")

    llm = LlmHintGenerator(
        generate_fn=lambda _: "LLM enhanced summary.",
        policy_allows_llm=True,
    )
    gen = HybridHintGenerator(llm=llm)
    hint = gen.generate_file_summary(py_file, workspace_root=tmp_path)
    assert hint is not None
    assert hint.generator.kind == GEN_HYBRID


# ── Service: generate_and_store ───────────────────────────────────────────────

def test_service_generate_and_store(tmp_path):
    py_file = tmp_path / "svc_target.py"
    py_file.write_text("class SvcClass:\n    \"\"\"SvcClass docstring.\"\"\"\n    pass\n")
    store = tmp_store()
    svc = ReferencedContextHintService(store=store, workspace_root=tmp_path)
    hint = svc.generate_and_store(py_file, kind=KIND_FILE_SUMMARY)
    assert hint is not None
    assert store.count(kind=KIND_FILE_SUMMARY) == 1


def test_service_generate_nonexistent_returns_none():
    store = tmp_store()
    svc = tmp_svc(store=store)
    hint = svc.generate_and_store("/no/such/file.py")
    assert hint is None


# ── Service: on_files_changed ─────────────────────────────────────────────────

def test_service_on_files_changed_marks_stale(tmp_path):
    py_file = tmp_path / "changing.py"
    py_file.write_text("# v1\n")
    store = tmp_store()
    svc = ReferencedContextHintService(store=store, workspace_root=tmp_path)

    # Generate with v1 hash
    hint = svc.generate_and_store(py_file, kind=KIND_FILE_SUMMARY)
    assert hint is not None

    # Modify file → hash changes
    py_file.write_text("# v2 — modified content here\n")

    changed = svc.on_files_changed([str(py_file)])
    assert len(changed) > 0


# ── Service: build_hints_prompt_section ──────────────────────────────────────

def test_build_hints_prompt_section_non_empty():
    svc = tmp_svc()
    hints = [
        {
            "hint_id": "h1", "kind": KIND_FILE_SUMMARY, "title": "Foo summary",
            "summary": "Foo does bar.", "source_paths": ["agent/foo.py"],
            "generator_kind": GEN_DETERMINISTIC, "staleness_status": STALENESS_FRESH,
            "confidence": 0.9, "requires_human_review": False,
        }
    ]
    section = svc.build_hints_prompt_section(hints)
    assert "abgeleitete" in section.lower() or "Abgeleitete" in section
    assert "nicht autoritativ" in section or "non-authoritative" in section.lower()
    assert "agent/foo.py" in section


def test_build_hints_prompt_section_empty_for_no_hints():
    svc = tmp_svc()
    section = svc.build_hints_prompt_section([])
    assert section == ""


def test_build_hints_prompt_section_truncates_at_max_chars():
    svc = tmp_svc()
    hints = [
        {
            "hint_id": f"h{i}", "kind": KIND_FILE_SUMMARY, "title": f"Module {i}",
            "summary": "A" * 300, "source_paths": [f"agent/file{i}.py"],
            "generator_kind": GEN_DETERMINISTIC, "staleness_status": STALENESS_FRESH,
            "confidence": 0.8, "requires_human_review": False,
        }
        for i in range(10)
    ]
    section = svc.build_hints_prompt_section(hints, max_chars=400)
    assert len(section) <= 450  # small margin for header


# ── ChatPromptBuilder integration ─────────────────────────────────────────────

def test_prompt_builder_includes_hints_section():
    from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
    from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder

    memory = ChatMemoryContext(recent_turns=[])
    hints = [
        {
            "hint_id": "h1", "kind": KIND_FILE_SUMMARY, "title": "Auth module",
            "summary": "Handles authentication.", "source_paths": ["agent/auth.py"],
            "generator_kind": GEN_DETERMINISTIC, "staleness_status": STALENESS_FRESH,
            "confidence": 0.85, "requires_human_review": False,
        }
    ]
    builder = ChatPromptBuilder(
        question="What does the auth module do?",
        depth="overview",
        memory=memory,
        derived_hints=hints,
    )
    result = builder.build()
    assert "hints" in result.included_sections
    # Hints section should contain reference marker
    assert result.included_sections["hints"] > 0


def test_prompt_builder_worker_v2_includes_hints():
    from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
    from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder

    memory = ChatMemoryContext(recent_turns=[])
    hints = [{"hint_id": "h1", "kind": KIND_FILE_SUMMARY, "title": "Foo",
               "summary": "Foo.", "source_paths": ["foo.py"],
               "generator_kind": GEN_DETERMINISTIC, "staleness_status": STALENESS_FRESH,
               "confidence": 0.8, "requires_human_review": False}]
    builder = ChatPromptBuilder(question="?", depth="overview", memory=memory, derived_hints=hints)
    result = builder.build()
    assert "derived_context_hints" in result.worker_v2_payload
    assert len(result.worker_v2_payload["derived_context_hints"]) == 1


def test_prompt_builder_no_hints_works_without_hints():
    from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
    from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder

    memory = ChatMemoryContext(recent_turns=[])
    builder = ChatPromptBuilder(question="Hallo?", depth="overview", memory=memory)
    result = builder.build()
    assert "hints" not in result.included_sections


# ── CodeCompass integration: derived_context_hints ────────────────────────────

def test_codecompass_resolve_context_has_derived_hints_key():
    from agent.services.codecompass_context_service import CodeCompassContextService
    svc = CodeCompassContextService()
    result = svc.resolve_context(query="what does foo do?")
    assert "derived_context_hints" in result
    assert isinstance(result["derived_context_hints"], list)
