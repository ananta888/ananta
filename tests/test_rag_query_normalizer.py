"""Tests for agent/rag_query_normalizer.py — RCFG-009, RTS-008."""
from __future__ import annotations

from agent.rag_query_normalizer import (
    normalize_query,
    normalize_query_from_settings,
    _keyword_de_to_en,
    _keyword_en_to_de,
    _is_mixed_code_query,
    _mixed_code_query_expansion,
)


# ---------------------------------------------------------------------------
# off mode
# ---------------------------------------------------------------------------

def test_off_mode_returns_only_original() -> None:
    result = normalize_query("Wie funktioniert der Autopilot?", mode="off")
    assert result == ["Wie funktioniert der Autopilot?"]


def test_off_mode_no_network_no_expansion() -> None:
    """off mode must never call any expansion — verified by checking length."""
    result = normalize_query("wo ist die datei konfiguration", mode="off")
    assert len(result) == 1


def test_off_mode_empty_query() -> None:
    result = normalize_query("", mode="off")
    assert result == [""]


# ---------------------------------------------------------------------------
# keyword DE→EN mode
# ---------------------------------------------------------------------------

def test_keyword_de_autopilot_tick_engine() -> None:
    result = normalize_query(
        "Wie funktioniert der autopilot_tick_engine?",
        mode="keyword",
        directions="de_to_en",
    )
    assert result[0] == "Wie funktioniert der autopilot_tick_engine?"
    assert len(result) >= 2
    second = result[1].lower()
    assert "autopilot_tick_engine" in second
    assert "works" in second or "process" in second or "function" in second


def test_keyword_original_query_always_first() -> None:
    q = "Wie erstellt man eine Konfiguration?"
    result = normalize_query(q, mode="keyword", directions="de_to_en")
    assert result[0] == q


def test_keyword_preserves_snake_case_tokens() -> None:
    result = normalize_query(
        "wo wird repository_map initialisiert",
        mode="keyword",
        directions="de_to_en",
    )
    full = " ".join(result)
    assert "repository_map" in full


def test_keyword_german_verb_mapping() -> None:
    for de_word, en_word in [
        ("verarbeitet", "process"),
        ("erstellt", "create"),
        ("speichert", "save"),
        ("startet", "start"),
        ("prüft", "check"),
    ]:
        result = normalize_query(f"Der Service {de_word} die tasks", mode="keyword", directions="de_to_en")
        combined = " ".join(result[1:]).lower() if len(result) > 1 else ""
        assert en_word in combined, f"Expected '{en_word}' in expansion for verb '{de_word}'"


def test_keyword_german_noun_mapping() -> None:
    for de_noun, en_noun in [
        ("aufgabe", "task"),
        ("datei", "file"),
        ("konfiguration", "config"),
        ("berechtigung", "permission"),
        ("fehler", "error"),
    ]:
        result = normalize_query(f"zeige alle {de_noun} im system", mode="keyword", directions="de_to_en")
        combined = " ".join(result[1:]).lower() if len(result) > 1 else ""
        assert en_noun in combined, f"Expected '{en_noun}' in expansion for noun '{de_noun}'"


def test_keyword_deduplication() -> None:
    result = normalize_query("aufgabe aufgabe aufgabe", mode="keyword", directions="de_to_en")
    seen = set()
    for v in result:
        key = v.lower()
        assert key not in seen, f"Duplicate variant: {v!r}"
        seen.add(key)


def test_keyword_english_query_no_expansion() -> None:
    """An already-English query should not get a redundant expansion."""
    result = normalize_query("get all tasks from service", mode="keyword", directions="de_to_en")
    # No German → no mapping hit; result should be just the original
    assert result == ["get all tasks from service"]


def test_keyword_no_llm_network_call(monkeypatch) -> None:
    """keyword mode must not import or call any LLM/network path."""
    import agent.rag_query_normalizer as mod

    called = []

    def _fake_llm(*args, **kwargs):
        called.append(True)
        raise AssertionError("LLM should not be called in keyword mode")

    monkeypatch.setattr(mod, "_llm_provider_stub", _fake_llm, raising=False)
    normalize_query("Wie funktioniert der agent?", mode="keyword", directions="de_to_en")
    assert not called


# ---------------------------------------------------------------------------
# llm mode fallback
# ---------------------------------------------------------------------------

def test_llm_mode_falls_back_to_keyword() -> None:
    """llm mode without provider falls back to keyword silently."""
    result_llm = normalize_query("Wie funktioniert der agent?", mode="llm", directions="de_to_en")
    result_kw = normalize_query("Wie funktioniert der agent?", mode="keyword", directions="de_to_en")
    assert result_llm == result_kw


# ---------------------------------------------------------------------------
# EN→DE mode (RTS-004)
# ---------------------------------------------------------------------------

def test_en_to_de_permission_check() -> None:
    result = normalize_query(
        "permission check user update field",
        mode="keyword",
        directions="en_to_de",
    )
    assert result[0] == "permission check user update field"
    if len(result) > 1:
        combined = " ".join(result[1:]).lower()
        assert "berechtigung" in combined or "benutzer" in combined


def test_en_to_de_code_tokens_preserved() -> None:
    result = normalize_query(
        "permission check in PaymentService.validate",
        mode="keyword",
        directions="en_to_de",
    )
    full = " ".join(result)
    assert "PaymentService.validate" in full


def test_en_to_de_deactivatable() -> None:
    result = normalize_query("permission check user", mode="keyword", directions="de_to_en")
    combined = " ".join(result).lower()
    assert "berechtigung" not in combined


# ---------------------------------------------------------------------------
# Mixed-code-query (RTS-005)
# ---------------------------------------------------------------------------

def test_mixed_code_query_detection() -> None:
    assert _is_mixed_code_query("wo wird repository_map in der snake initialisiert") is True
    assert _is_mixed_code_query("get all tasks from service") is False
    assert _is_mixed_code_query("welche aufgaben gibt es") is False


def test_mixed_code_query_expansion_preserves_code_tokens() -> None:
    v = _mixed_code_query_expansion("welche tasks verarbeitet autopilot_tick_engine.py")
    assert v is not None
    assert "autopilot_tick_engine.py" in v


def test_mixed_code_query_env_vars_unchanged() -> None:
    result = normalize_query(
        "was macht RAG_SCAN_EXCLUDE_DIRS in der konfiguration",
        mode="keyword",
        directions="de_to_en,mixed_code_query",
    )
    full = " ".join(result)
    assert "RAG_SCAN_EXCLUDE_DIRS" in full


def test_mixed_code_repo_snake_example() -> None:
    result = normalize_query(
        "wo wird repository_map in der snake initialisiert",
        mode="keyword",
        directions="mixed_code_query",
    )
    assert result[0].startswith("wo wird repository_map")
    if len(result) > 1:
        assert "repository_map" in result[1]


# ---------------------------------------------------------------------------
# Integration: normalize_query_from_settings
# ---------------------------------------------------------------------------

def test_normalize_from_settings_returns_list(monkeypatch) -> None:
    from agent.config import settings
    monkeypatch.setattr(settings, "rag_query_normalize_mode", "keyword", raising=False)
    monkeypatch.setattr(settings, "rag_query_translation_directions", "de_to_en", raising=False)
    result = normalize_query_from_settings("Wie funktioniert der service?")
    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0] == "Wie funktioniert der service?"


def test_normalize_from_settings_off_mode(monkeypatch) -> None:
    from agent.config import settings
    monkeypatch.setattr(settings, "rag_query_normalize_mode", "off", raising=False)
    result = normalize_query_from_settings("irgendeine query")
    assert result == ["irgendeine query"]
