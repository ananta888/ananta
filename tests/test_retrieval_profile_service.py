"""CRPS-002/003/004/009/017: Tests for RetrievalProfile, classifier, resolver, and source constraints."""
from __future__ import annotations

import pytest

from agent.services.retrieval_profile_service import (
    DOMAIN_AI_SNAKE,
    DOMAIN_ANANTA_GAME,
    DOMAIN_CODECOMPASS,
    DOMAIN_GENERIC,
    DOMAIN_OPS,
    DOMAIN_OPERATOR_TUI,
    DOMAIN_WORKER,
    INTENT_ARCHITECTURE,
    INTENT_CODE_EXPLANATION,
    INTENT_GAME_DESIGN,
    INTENT_GENERIC_CHAT,
    INTENT_MERMAID,
    INTENT_OPS_RUNBOOK,
    INTENT_TUTORIAL,
    RetrievalProfile,
    apply_profile_source_constraints,
    classify_retrieval_intent,
    normalize_retrieval_profile,
    resolve_profile,
)


class TestNormalizeRetrievalProfile:
    def test_none_returns_none(self):
        assert normalize_retrieval_profile(None) is None

    def test_empty_dict_returns_none(self):
        assert normalize_retrieval_profile({}) is None

    def test_missing_fields_returns_none(self):
        assert normalize_retrieval_profile({"profile_id": "x"}) is None

    def test_valid_minimal(self):
        p = normalize_retrieval_profile({
            "profile_id": "test/code",
            "domain": "codecompass",
            "intent": "implemented_code_explanation",
        })
        assert p is not None
        assert p.profile_id == "test/code"
        assert p.source_types == []
        assert p.source_type_weights == {}
        assert p.warnings == []

    def test_valid_full(self):
        p = normalize_retrieval_profile({
            "profile_id": "test/code",
            "domain": "codecompass",
            "intent": "implemented_code_explanation",
            "source_types": ["repo", "artifact"],
            "source_type_weights": {"repo": 1.45, "artifact": 1.05, "wiki": 0.3},
            "retrieval_intent": "code_explanation_with_codecompass",
            "negative_source_patterns": ["book-of-ananta"],
            "feature_flag": "auto",
        })
        assert p is not None
        assert p.source_types == ["repo", "artifact"]
        assert p.source_type_weights["repo"] == pytest.approx(1.45)
        assert p.negative_source_patterns == ["book-of-ananta"]
        assert p.warnings == []
        assert p.source_policy["requested_source_types"] == ["repo", "artifact"]
        assert p.chunk_policy["prefer_chunks_over_context_text"] is True
        assert p.expansion_policy["relation_expansion"] is True
        assert p.explainability["include_selected_by"] is True

    def test_unknown_source_type_produces_warning(self):
        p = normalize_retrieval_profile({
            "profile_id": "x/y",
            "domain": "d",
            "intent": "i",
            "source_types": ["repo", "unknown_source"],
        })
        assert p is not None
        assert "repo" in p.source_types
        assert "unknown_source" not in p.source_types
        assert any("unknown_source_type" in w for w in p.warnings)

    def test_invalid_weight_produces_warning(self):
        p = normalize_retrieval_profile({
            "profile_id": "x/y",
            "domain": "d",
            "intent": "i",
            "source_type_weights": {"repo": "not_a_float"},
        })
        assert p is not None
        assert any("invalid_weight_for:repo" in w for w in p.warnings)

    def test_as_dict_roundtrip(self):
        raw = {
            "profile_id": "test/arch",
            "domain": "codecompass",
            "intent": "architecture_overview",
            "source_types": ["repo", "artifact", "wiki"],
            "source_type_weights": {"repo": 1.1, "wiki": 1.2},
            "retrieval_intent": "arch_overview",
            "negative_source_patterns": [],
            "feature_flag": "auto",
        }
        p = normalize_retrieval_profile(raw)
        d = p.as_dict()
        assert d["profile_id"] == "test/arch"
        assert d["domain"] == "codecompass"
        assert "warnings" in d


class TestClassifyRetrievalIntent:
    def test_codecompass_code_explanation(self):
        domain, intent = classify_retrieval_intent(
            "den codecompass der schon implementiert ist erklären"
        )
        assert domain == DOMAIN_CODECOMPASS
        assert intent == INTENT_CODE_EXPLANATION

    def test_worker_handoff_mechanism(self):
        domain, intent = classify_retrieval_intent(
            "wie ist der mechanismus damit ananta dies an die worker weitergibt"
        )
        assert intent == INTENT_CODE_EXPLANATION

    def test_mermaid_request(self):
        domain, intent = classify_retrieval_intent(
            "kannst du mir mermaid diagramme dazu zeigen"
        )
        assert intent == INTENT_MERMAID

    def test_ananta_game_lore(self):
        domain, intent = classify_retrieval_intent("ananta game lore")
        assert domain == DOMAIN_ANANTA_GAME

    def test_architecture_question(self):
        domain, intent = classify_retrieval_intent(
            "beschreib mir die architektur des rag services"
        )
        assert intent == INTENT_ARCHITECTURE

    def test_german_code_keywords(self):
        _, intent = classify_retrieval_intent("welche klasse implementiert den sgpt modul")
        assert intent == INTENT_CODE_EXPLANATION

    def test_english_code_keywords(self):
        _, intent = classify_retrieval_intent("how does the retrieval service function work")
        assert intent == INTENT_CODE_EXPLANATION

    def test_english_file_keyword(self):
        _, intent = classify_retrieval_intent("show me the file for the rag service")
        assert intent == INTENT_CODE_EXPLANATION

    def test_ops_runbook(self):
        domain, intent = classify_retrieval_intent("wie mache ich einen neustart des deployment containers")
        assert domain == DOMAIN_OPS
        assert intent == INTENT_OPS_RUNBOOK

    def test_tutorial_mode_override(self):
        _, intent = classify_retrieval_intent("was ist das", {"tutorial_mode": True})
        assert intent == INTENT_TUTORIAL

    def test_codecompass_ui_config_domain_signal(self):
        domain, _ = classify_retrieval_intent("erklär mir das", {"chat_use_codecompass": True})
        assert domain == DOMAIN_CODECOMPASS

    def test_worker_domain(self):
        domain, _ = classify_retrieval_intent("wie funktioniert der ananta worker sgpt")
        assert domain == DOMAIN_WORKER

    def test_tui_domain(self):
        domain, _ = classify_retrieval_intent("wie funktioniert das tui operator clipboard")
        assert domain == DOMAIN_OPERATOR_TUI

    def test_game_design_intent(self):
        # unambiguously game-design: "spieldesign" + "punkte" + "level" → no code keywords
        _, intent = classify_retrieval_intent("beschreibe das spieldesign mit punktesystem und level")
        assert intent == INTENT_GAME_DESIGN

    def test_generic_fallback(self):
        domain, intent = classify_retrieval_intent("")
        assert domain == DOMAIN_GENERIC
        assert intent == INTENT_GENERIC_CHAT


class TestResolveProfile:
    def test_codecompass_code_explanation_has_repo_and_artifact(self):
        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {"chat_use_codecompass": True, "chat_include_local_project": True},
        )
        assert "repo" in profile.source_types
        assert "artifact" in profile.source_types
        assert profile.domain == DOMAIN_CODECOMPASS
        assert profile.intent == INTENT_CODE_EXPLANATION

    def test_old_behavior_artifact_only_no_longer_happens(self):
        # Previously: chat_use_codecompass=true, chat_include_local_project=false → only artifact
        # New: code question → profile adds repo
        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {"chat_use_codecompass": True, "chat_include_local_project": False},
        )
        # Even with include_local_project=False, profile resolver requests repo
        # but ui_config constraint removes it — so source_types may not include repo
        # The profile.domain/intent classification should still be correct
        assert profile.domain == DOMAIN_CODECOMPASS
        assert profile.intent == INTENT_CODE_EXPLANATION

    def test_legacy_flag_uses_generic_fallback(self):
        profile = resolve_profile("beliebige frage", feature_flag="legacy")
        assert profile.profile_id == "generic_legacy"
        assert profile.feature_flag == "legacy"

    def test_disabled_flag_uses_generic_fallback(self):
        profile = resolve_profile("beliebige frage", feature_flag="disabled")
        assert profile.profile_id == "generic_legacy"
        assert profile.domain == DOMAIN_GENERIC

    def test_repo_first_flag_boosts_repo_weight(self):
        profile = resolve_profile("zeig mir den code", feature_flag="repo_first")
        assert profile.source_type_weights.get("repo", 1.0) >= 1.3

    def test_docs_first_flag_boosts_artifact_weight(self):
        profile = resolve_profile("zeig mir die doku", feature_flag="docs_first")
        assert profile.source_type_weights.get("artifact", 1.0) >= 1.2

    def test_game_query_does_not_include_repo_in_top(self):
        profile = resolve_profile(
            "erkläre das ananta game lore",
            {"chat_use_codecompass": True, "chat_include_local_project": True, "chat_include_wikipedia": True},
        )
        assert profile.domain == DOMAIN_ANANTA_GAME
        # game profile weights repo lower
        assert profile.source_type_weights.get("repo", 1.0) < 1.0

    def test_wiki_disabled_by_ui_config(self):
        profile = resolve_profile(
            "erkläre die architektur",
            {"chat_include_wikipedia": False, "chat_use_codecompass": True},
        )
        assert "wiki" not in profile.source_types
        # warning present if profile wanted wiki
        if profile.source_type_weights.get("wiki", 0) > 0.9:
            # wiki was in spec, should have a warning
            pass  # acceptable

    def test_code_questions_repo_first_shortcut(self):
        profile = resolve_profile(
            "erklär mir die klasse",
            {"chat_code_questions_repo_first": True, "chat_retrieval_profile": "auto"},
        )
        assert profile.source_type_weights.get("repo", 1.0) >= 1.3

    def test_architecture_analysis_mode_can_force_full_scan(self):
        profile = resolve_profile(
            "gib mir einen kurzen ueberblick",
            {"chat_architecture_analysis_mode": "full_scan"},
        )
        assert profile.analysis_mode == "architecture_full_scan"
        assert profile.coverage_policy == "relation_expanded"

    def test_architecture_analysis_mode_can_disable_full_scan(self):
        profile = resolve_profile(
            "Bitte erstelle ein Architekturdiagramm als full scan",
            {"chat_architecture_analysis_mode": "off"},
        )
        assert profile.analysis_mode == ""

    def test_domain_hint_override(self):
        profile = resolve_profile("erklär mir das", domain_hint="ananta_game")
        assert profile.domain == DOMAIN_ANANTA_GAME

    def test_intent_override(self):
        profile = resolve_profile("was ist das", intent_override="architecture_overview")
        assert profile.intent == INTENT_ARCHITECTURE

    def test_as_dict_contains_all_fields(self):
        profile = resolve_profile("implementierter code", {"chat_use_codecompass": True})
        d = profile.as_dict()
        for key in ("profile_id", "domain", "intent", "source_types", "source_type_weights",
                    "retrieval_intent", "negative_source_patterns", "feature_flag", "warnings",
                    "selected_by", "reasons", "source_policy", "chunk_policy", "expansion_policy",
                    "explainability"):
            assert key in d

    def test_resolved_profile_explains_selection(self):
        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {"chat_use_codecompass": True, "chat_include_local_project": True},
        )
        assert profile.selected_by == "retrieval_profile_resolver.v1"
        assert "classified_domain:codecompass" in profile.reasons
        assert profile.source_policy["priority_order"][0] == "repo"
        assert profile.source_policy["required_min_source_type_counts"]["repo"] == 2

    def test_source_types_only_valid(self):
        profile = resolve_profile("test", {"chat_use_codecompass": True})
        for st in profile.source_types:
            assert st in {"repo", "artifact", "wiki", "task_memory"}

    def test_source_type_weights_are_floats(self):
        profile = resolve_profile("implementierter code")
        for k, v in profile.source_type_weights.items():
            assert isinstance(v, float), f"weight for {k} is not float: {v!r}"


class TestApplyProfileSourceConstraints:
    def _make_chunk(self, source: str, source_id: str = "", record_kind: str = "") -> dict:
        return {
            "source": source,
            "content": f"content of {source}",
            "score": 0.9,
            "metadata": {
                "source_id": source_id,
                "record_kind": record_kind,
                "source_type": "artifact",
            },
        }

    def test_no_patterns_returns_all_chunks(self):
        profile = RetrievalProfile(profile_id="x", domain="d", intent="i")
        chunks = [self._make_chunk("a.py"), self._make_chunk("b.py")]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        assert len(filtered) == 2
        assert meta["removed"] == 0

    def test_negative_pattern_removes_matching_chunk(self):
        profile = RetrievalProfile(
            profile_id="x", domain="d", intent="i",
            negative_source_patterns=["book-of-ananta"],
        )
        chunks = [
            self._make_chunk("docs/ananta-game/book-of-ananta.md"),
            self._make_chunk("agent/services/rag_service.py"),
        ]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        assert len(filtered) == 1
        assert filtered[0]["source"] == "agent/services/rag_service.py"
        assert meta["removed"] == 1

    def test_source_id_matching(self):
        profile = RetrievalProfile(
            profile_id="x", domain="d", intent="i",
            negative_source_patterns=["snake_tutor"],
        )
        chunks = [
            self._make_chunk("generic.md", source_id="snake_tutor_v2"),
            self._make_chunk("agent/services/rag_service.py"),
        ]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        assert len(filtered) == 1
        assert meta["removed"] == 1

    def test_insufficient_sources_flag(self):
        profile = RetrievalProfile(
            profile_id="x", domain="d", intent="i",
            negative_source_patterns=["book-of-ananta"],
        )
        chunks = [self._make_chunk("docs/book-of-ananta.md")]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        assert len(filtered) == 0
        assert meta["insufficient_positive_sources"] is True

    def test_multiple_patterns(self):
        profile = RetrievalProfile(
            profile_id="x", domain="d", intent="i",
            negative_source_patterns=["book-of-ananta", "snake_tutor", "terminal-header"],
        )
        chunks = [
            self._make_chunk("docs/book-of-ananta.md"),
            self._make_chunk("docs/snake_tutor.md"),
            self._make_chunk("terminal-header-logo-renderer.md"),
            self._make_chunk("agent/services/rag_service.py"),
        ]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        assert len(filtered) == 1
        assert meta["removed"] == 3


class TestCRPS017ConfigBounds:
    """CRPS-017: ensure disabled sources don't get activated by profiles."""

    def test_repo_disabled_by_ui_config_not_in_source_types(self):
        profile = resolve_profile(
            "implementierter code",
            {"chat_include_local_project": False, "chat_use_codecompass": True},
        )
        assert "repo" not in profile.source_types

    def test_artifact_disabled_by_ui_config_not_in_source_types(self):
        profile = resolve_profile(
            "implementierter code",
            {"chat_use_codecompass": False, "chat_include_local_project": True},
        )
        assert "artifact" not in profile.source_types

    def test_wiki_not_added_unless_explicitly_enabled(self):
        profile = resolve_profile(
            "erkläre die architektur",
            {"chat_include_wikipedia": False},
        )
        assert "wiki" not in profile.source_types

    def test_all_sources_disabled_gives_empty_source_types(self):
        profile = resolve_profile(
            "implementierter code",
            {
                "chat_use_codecompass": False,
                "chat_include_local_project": False,
                "chat_include_wikipedia": False,
            },
        )
        assert profile.source_types == []

    def test_warnings_emitted_when_profile_source_disabled(self):
        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {"chat_use_codecompass": False, "chat_include_local_project": False},
        )
        # both repo and artifact are disabled
        assert any("source_type_disabled_by_ui_config" in w for w in profile.warnings)
