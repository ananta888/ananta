"""RCHCS-008: Tests for referenced_context_hint.v1 schema."""
from __future__ import annotations

import time
import pytest

from agent.services.referenced_context_hint_schema import (
    ALL_GEN_KINDS,
    ALL_KINDS,
    FRESH_TTL_SECONDS,
    GEN_DETERMINISTIC,
    GEN_LLM,
    GEN_MANUAL,
    GEN_RESTRICTED_TRANSFORMER_INFERENCE,
    HINT_SCHEMA_VERSION,
    KIND_ARCHITECTURE_HINT,
    KIND_DOMAIN_SUMMARY,
    KIND_FILE_SUMMARY,
    KIND_MANUAL_NOTE,
    KIND_RISK_HINT,
    KIND_SYMBOL_HINT,
    KIND_TEST_HINT,
    STALENESS_FRESH,
    STALENESS_INVALID,
    STALENESS_POSSIBLY_STALE,
    STALENESS_STALE,
    CodeCompassRef,
    ConfidenceMetadata,
    GeneratorMetadata,
    HashMetadata,
    HintPolicyMetadata,
    HintValidationError,
    ReferencedContextHint,
    SourceRef,
    ValidityMetadata,
    compute_confidence,
    compute_staleness,
    hash_text,
    make_hint_id,
    validate_hint,
)


def make_hint(*, kind=KIND_FILE_SUMMARY, with_refs=True, generator_kind=GEN_DETERMINISTIC) -> ReferencedContextHint:
    source_refs = [SourceRef(path="agent/foo.py", sha256="abc123")] if with_refs else []
    return ReferencedContextHint(
        id=make_hint_id("agent/foo.py", kind),
        kind=kind,
        title="Foo summary",
        summary="Foo is a module that does bar.",
        source_refs=source_refs,
        generator=GeneratorMetadata(kind=generator_kind),
    )


# ── Schema version ────────────────────────────────────────────────────────────

def test_schema_version():
    assert HINT_SCHEMA_VERSION == "referenced_context_hint.v1"


def test_all_kinds_count():
    assert len(ALL_KINDS) == 8


def test_all_gen_kinds_count():
    assert len(ALL_GEN_KINDS) == 5


# ── make_hint_id ──────────────────────────────────────────────────────────────

def test_make_hint_id_format():
    hid = make_hint_id("agent/foo.py", KIND_FILE_SUMMARY)
    assert hid.startswith("hint:file_summary:agent/foo.py:")
    assert len(hid) > 20


def test_make_hint_id_deterministic():
    a = make_hint_id("agent/foo.py", KIND_FILE_SUMMARY)
    b = make_hint_id("agent/foo.py", KIND_FILE_SUMMARY)
    assert a == b


def test_make_hint_id_extra_differentiates():
    a = make_hint_id("agent/foo.py", KIND_SYMBOL_HINT)
    b = make_hint_id("agent/foo.py", KIND_SYMBOL_HINT, extra="MyClass")
    assert a != b


# ── ReferencedContextHint ─────────────────────────────────────────────────────

def test_hint_to_dict_has_required_keys():
    h = make_hint()
    d = h.to_dict()
    for k in ("schema", "id", "kind", "title", "summary", "source_refs",
               "codecompass_refs", "generator", "confidence", "validity",
               "hashes", "created_at", "updated_at", "staleness_status", "policy"):
        assert k in d, f"missing: {k}"


def test_hint_from_dict_roundtrip():
    h = make_hint()
    d = h.to_dict()
    h2 = ReferencedContextHint.from_dict(d)
    assert h2.id == h.id
    assert h2.kind == h.kind
    assert h2.summary == h.summary
    assert len(h2.source_refs) == len(h.source_refs)


def test_hint_schema_field_preserved_in_dict():
    h = make_hint()
    assert h.to_dict()["schema"] == HINT_SCHEMA_VERSION


def test_is_fresh_default():
    h = make_hint()
    assert h.is_fresh() is True


def test_is_safe_for_task_navigation():
    h = make_hint()
    assert h.is_safe_for_task("navigation") is True


def test_is_not_safe_for_authoritative_code_review():
    h = make_hint()
    assert h.is_safe_for_task("authoritative_code_review") is False


def test_stale_hint_not_safe_for_any_task():
    h = make_hint()
    h.staleness_status = STALENESS_STALE
    assert h.is_safe_for_task("navigation") is False
    assert h.is_safe_for_task("context_selection") is False


def test_invalid_hint_not_safe():
    h = make_hint()
    h.staleness_status = STALENESS_INVALID
    assert h.is_safe_for_task("explanation") is False


def test_is_safe_for_external_cloud_false_by_default():
    h = make_hint()
    h.policy.external_cloud_safe = False
    assert h.is_safe_for_external_cloud() is False


def test_is_safe_for_external_cloud_true_when_allowed():
    h = make_hint()
    h.policy.external_cloud_safe = True
    h.policy.contains_original_code_excerpt = False
    h.policy.redaction_status = "none"
    assert h.is_safe_for_external_cloud() is True


# ── ValidityMetadata ──────────────────────────────────────────────────────────

def test_validity_is_valid_for_listed_task():
    v = ValidityMetadata(valid_for_tasks=["navigation", "explanation"])
    assert v.is_valid_for("navigation") is True
    assert v.is_valid_for("explanation") is True


def test_validity_not_valid_for_excluded_task():
    v = ValidityMetadata(not_valid_for=["authoritative_code_review"])
    assert v.is_valid_for("authoritative_code_review") is False


def test_validity_security_claim_always_excluded():
    v = ValidityMetadata()
    assert v.is_valid_for("security_claim_without_original_source") is False


def test_validity_empty_valid_for_tasks_allows_all():
    v = ValidityMetadata(valid_for_tasks=[])
    assert v.is_valid_for("navigation") is True
    assert v.is_valid_for("explanation") is True


# ── validate_hint ─────────────────────────────────────────────────────────────

def test_validate_hint_passes_for_valid_hint():
    h = make_hint()
    validate_hint(h)  # should not raise


def test_validate_hint_fails_empty_id():
    h = make_hint()
    h.id = ""
    with pytest.raises(HintValidationError, match="id"):
        validate_hint(h)


def test_validate_hint_fails_unknown_kind():
    h = make_hint()
    h.kind = "magic_hint"
    with pytest.raises(HintValidationError, match="kind"):
        validate_hint(h)


def test_validate_hint_fails_empty_summary():
    h = make_hint()
    h.summary = "   "
    with pytest.raises(HintValidationError, match="summary"):
        validate_hint(h)


def test_validate_hint_fails_no_refs_for_non_manual():
    h = make_hint(with_refs=False, generator_kind=GEN_DETERMINISTIC)
    with pytest.raises(HintValidationError, match="source_ref"):
        validate_hint(h)


def test_validate_hint_no_refs_allowed_for_manual_domain():
    h = make_hint(with_refs=False, generator_kind=GEN_MANUAL)
    h.validity.scope = "domain"
    validate_hint(h)  # should not raise


def test_validate_hint_confidence_out_of_range():
    h = make_hint()
    h.confidence.score = 1.5
    with pytest.raises(HintValidationError, match="confidence"):
        validate_hint(h)


# ── compute_staleness ─────────────────────────────────────────────────────────

def test_staleness_fresh_unchanged_hash():
    h = make_hint()
    h.hashes.source_hash = "abc"
    result = compute_staleness(h, current_source_hash="abc", source_exists=True)
    assert result == STALENESS_FRESH


def test_staleness_stale_when_hash_changed():
    h = make_hint()
    h.hashes.source_hash = "abc"
    result = compute_staleness(h, current_source_hash="xyz", source_exists=True)
    assert result == STALENESS_STALE


def test_staleness_invalid_when_source_deleted():
    h = make_hint()
    result = compute_staleness(h, source_exists=False)
    assert result == STALENESS_INVALID


def test_staleness_possibly_stale_when_old():
    h = make_hint()
    h.hashes.source_hash = "abc"
    old_time = time.time() - FRESH_TTL_SECONDS - 3600  # 25 hours ago
    result = compute_staleness(h, current_source_hash="abc", source_exists=True, now=time.time(), )
    # Just updated → fresh
    assert result == STALENESS_FRESH
    # Simulate old hint
    h.updated_at = old_time
    result = compute_staleness(h, current_source_hash="abc", source_exists=True)
    assert result == STALENESS_POSSIBLY_STALE


def test_staleness_possibly_stale_manifest_changed():
    h = make_hint()
    h.hashes.source_hash = "abc"
    h.hashes.knowledge_index_manifest_hash = "manifest1"
    result = compute_staleness(
        h, current_source_hash="abc",
        current_manifest_hash="manifest2",
        source_exists=True,
    )
    assert result == STALENESS_POSSIBLY_STALE


def test_staleness_fresh_same_manifest():
    h = make_hint()
    h.hashes.source_hash = "abc"
    h.hashes.knowledge_index_manifest_hash = "manifest1"
    result = compute_staleness(
        h, current_source_hash="abc",
        current_manifest_hash="manifest1",
        source_exists=True,
    )
    assert result == STALENESS_FRESH


# ── compute_confidence ────────────────────────────────────────────────────────

def test_confidence_deterministic_three_refs():
    score = compute_confidence(
        source_ref_count=3,
        generator_kind=GEN_DETERMINISTIC,
        staleness_status=STALENESS_FRESH,
    )
    assert score > 0.85


def test_confidence_llm_lower_than_deterministic():
    det = compute_confidence(
        source_ref_count=1,
        generator_kind=GEN_DETERMINISTIC,
        staleness_status=STALENESS_FRESH,
    )
    llm = compute_confidence(
        source_ref_count=1,
        generator_kind=GEN_LLM,
        staleness_status=STALENESS_FRESH,
    )
    assert det > llm


def test_confidence_stale_lower_than_fresh():
    fresh = compute_confidence(source_ref_count=1, generator_kind=GEN_DETERMINISTIC,
                               staleness_status=STALENESS_FRESH)
    stale = compute_confidence(source_ref_count=1, generator_kind=GEN_DETERMINISTIC,
                               staleness_status=STALENESS_STALE)
    assert fresh > stale


def test_confidence_invalid_is_zero():
    score = compute_confidence(source_ref_count=1, generator_kind=GEN_DETERMINISTIC,
                               staleness_status=STALENESS_INVALID)
    assert score == 0.0


def test_confidence_no_refs_reduces_score():
    with_refs = compute_confidence(source_ref_count=2, generator_kind=GEN_DETERMINISTIC,
                                   staleness_status=STALENESS_FRESH)
    no_refs = compute_confidence(source_ref_count=0, generator_kind=GEN_DETERMINISTIC,
                                 staleness_status=STALENESS_FRESH)
    assert with_refs > no_refs


def test_confidence_in_range():
    for gen_kind in ALL_GEN_KINDS:
        for staleness in (STALENESS_FRESH, STALENESS_POSSIBLY_STALE, STALENESS_STALE, STALENESS_INVALID):
            score = compute_confidence(source_ref_count=2, generator_kind=gen_kind,
                                       staleness_status=staleness)
            assert 0.0 <= score <= 1.0, f"{gen_kind} {staleness}: {score}"


# ── SourceRef / CodeCompassRef / GeneratorMetadata roundtrip ─────────────────

def test_source_ref_roundtrip():
    sr = SourceRef(path="foo.py", sha256="abc", line_start=1, line_end=50, role="primary_source")
    sr2 = SourceRef.from_dict(sr.to_dict())
    assert sr2.path == "foo.py"
    assert sr2.sha256 == "abc"
    assert sr2.line_start == 1
    assert sr2.line_end == 50


def test_codecompass_ref_roundtrip():
    cr = CodeCompassRef(record_id="cc:1", node_id="file:foo.py",
                         manifest_hash="mhash", relation_types=["defines", "calls"])
    cr2 = CodeCompassRef.from_dict(cr.to_dict())
    assert cr2.record_id == "cc:1"
    assert "defines" in cr2.relation_types


def test_generator_metadata_roundtrip():
    gm = GeneratorMetadata(kind=GEN_LLM, model_id="llama-3.2-1b", prompt_hash="ph123")
    gm2 = GeneratorMetadata.from_dict(gm.to_dict())
    assert gm2.kind == GEN_LLM
    assert gm2.model_id == "llama-3.2-1b"
    assert gm2.prompt_hash == "ph123"
