"""CRPS-008/009/013: Tests for profile-driven source_type_weights and negative pattern filtering."""
from __future__ import annotations

import pytest

from agent.services.retrieval_profile_service import (
    DOMAIN_CODECOMPASS,
    DOMAIN_WORKER,
    INTENT_CODE_EXPLANATION,
    RetrievalProfile,
    apply_profile_source_constraints,
)


class TestApplyProfileSourceConstraintsRanking:
    """CRPS-013: repo before book-of-ananta when code profile is applied."""

    def _make_chunk(
        self,
        source: str,
        source_type: str = "repo",
        score: float = 0.9,
        source_id: str = "",
    ) -> dict:
        return {
            "source": source,
            "content": f"content of {source}",
            "score": score,
            "engine": "repository_map" if source_type == "repo" else "knowledge_index",
            "metadata": {
                "source_type": source_type,
                "source_id": source_id or source,
            },
        }

    def test_negative_pattern_removes_game_docs_when_repo_present(self):
        profile = RetrievalProfile(
            profile_id=f"{DOMAIN_CODECOMPASS}/{INTENT_CODE_EXPLANATION}",
            domain=DOMAIN_CODECOMPASS,
            intent=INTENT_CODE_EXPLANATION,
            source_types=["repo", "artifact"],
            source_type_weights={"repo": 1.45, "artifact": 1.05, "wiki": 0.3, "task_memory": 0.95},
            retrieval_intent="code_explanation_with_codecompass",
            negative_source_patterns=["book-of-ananta", "snake_tutor", "terminal-header-logo-renderer", "markdown_mermaid"],
        )
        chunks = [
            self._make_chunk("agent/services/rag_service.py", "repo", score=0.85),
            self._make_chunk("agent/services/retrieval_service.py", "repo", score=0.83),
            self._make_chunk("docs/ananta-game/book-of-ananta.md", "artifact", score=0.92, source_id="book-of-ananta"),
            self._make_chunk("docs/snake_tutor.md", "artifact", score=0.80, source_id="snake_tutor"),
            self._make_chunk("agent/routes/snakes.py", "repo", score=0.78),
        ]
        filtered, meta = apply_profile_source_constraints(chunks, profile)

        sources = [c["source"] for c in filtered]
        # repo sources survive
        assert "agent/services/rag_service.py" in sources
        assert "agent/services/retrieval_service.py" in sources
        assert "agent/routes/snakes.py" in sources
        # negative sources removed
        assert "docs/ananta-game/book-of-ananta.md" not in sources
        assert "docs/snake_tutor.md" not in sources
        assert meta["removed"] == 2

    def test_top_3_contain_repo_sources(self):
        """CRPS-013: Top chunks after filter are repo sources."""
        profile = RetrievalProfile(
            profile_id=f"{DOMAIN_CODECOMPASS}/{INTENT_CODE_EXPLANATION}",
            domain=DOMAIN_CODECOMPASS,
            intent=INTENT_CODE_EXPLANATION,
            source_types=["repo", "artifact"],
            source_type_weights={"repo": 1.45, "artifact": 1.05, "wiki": 0.3, "task_memory": 0.95},
            retrieval_intent="code_explanation_with_codecompass",
            negative_source_patterns=["book-of-ananta", "snake_tutor"],
        )
        chunks = [
            self._make_chunk("agent/services/rag_service.py", "repo", score=0.85),
            self._make_chunk("agent/services/retrieval_service.py", "repo", score=0.83),
            self._make_chunk("docs/ananta-game/book-of-ananta.md", "artifact", score=0.95, source_id="book-of-ananta"),
            self._make_chunk("docs/snake_tutor.md", "artifact", score=0.90, source_id="snake_tutor"),
            self._make_chunk("agent/routes/snakes.py", "repo", score=0.78),
            self._make_chunk("wiki/general.md", "wiki", score=0.60),
        ]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        filtered_sorted = sorted(filtered, key=lambda c: -c["score"])
        top3 = [c["source"] for c in filtered_sorted[:3]]
        repo_in_top3 = sum(1 for s in top3 if "agent/" in s)
        assert repo_in_top3 >= 2, f"Expected ≥2 repo in top3, got: {top3}"

    def test_insufficient_positive_sources_flag_when_all_filtered(self):
        profile = RetrievalProfile(
            profile_id="test",
            domain="d",
            intent="i",
            negative_source_patterns=["book-of-ananta", "snake_tutor"],
        )
        chunks = [
            self._make_chunk("docs/book-of-ananta.md", "artifact", source_id="book-of-ananta"),
            self._make_chunk("docs/snake_tutor.md", "artifact", source_id="snake_tutor"),
        ]
        filtered, meta = apply_profile_source_constraints(chunks, profile)
        assert len(filtered) == 0
        assert meta["insufficient_positive_sources"] is True

    def test_source_type_contributions_repo_nonzero_after_filter(self):
        """After filtering game docs, repo source_type must have contribution > 0."""
        profile = RetrievalProfile(
            profile_id=f"{DOMAIN_CODECOMPASS}/{INTENT_CODE_EXPLANATION}",
            domain=DOMAIN_CODECOMPASS,
            intent=INTENT_CODE_EXPLANATION,
            negative_source_patterns=["book-of-ananta"],
        )
        chunks = [
            self._make_chunk("agent/services/rag_service.py", "repo"),
            self._make_chunk("docs/book-of-ananta.md", "artifact", source_id="book-of-ananta"),
        ]
        filtered, _ = apply_profile_source_constraints(chunks, profile)
        source_types = [c["metadata"]["source_type"] for c in filtered]
        assert "repo" in source_types


class TestProfileWeightsMerging:
    """CRPS-008: source_type_weights from profile override/merge with _task_profile_for_fusion defaults."""

    def test_codecompass_profile_weights_structure(self):
        """Profile spec for codecompass/implemented_code_explanation has correct weight structure."""
        from agent.services.retrieval_profile_service import _PROFILE_TABLE

        spec = _PROFILE_TABLE.get((DOMAIN_CODECOMPASS, INTENT_CODE_EXPLANATION))
        assert spec is not None
        weights = spec["source_type_weights"]
        assert weights["repo"] >= 1.3, "repo weight must dominate"
        assert weights["wiki"] <= 0.5, "wiki weight must be penalized"

    def test_worker_profile_weights_structure(self):
        """Worker/code_explanation profile must heavily weight repo."""
        from agent.services.retrieval_profile_service import _PROFILE_TABLE

        spec = _PROFILE_TABLE.get((DOMAIN_WORKER, INTENT_CODE_EXPLANATION))
        assert spec is not None
        assert spec["source_type_weights"]["repo"] >= 1.3

    def test_game_profile_weights_repo_low(self):
        """Game/tutorial profile must have low repo weight."""
        from agent.services.retrieval_profile_service import (
            DOMAIN_ANANTA_GAME,
            INTENT_TUTORIAL,
            _PROFILE_TABLE,
        )

        spec = _PROFILE_TABLE.get((DOMAIN_ANANTA_GAME, INTENT_TUTORIAL))
        assert spec is not None
        assert spec["source_type_weights"]["repo"] <= 0.8

    def test_resolve_profile_code_question_weights_match_table(self):
        """resolve_profile must produce weights consistent with _PROFILE_TABLE for exact (domain, intent) match."""
        from agent.services.retrieval_profile_service import resolve_profile

        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {"chat_use_codecompass": True, "chat_include_local_project": True},
        )
        # Must come from the codecompass/implemented_code_explanation entry
        assert profile.source_type_weights.get("repo", 0) >= 1.3
        assert profile.source_type_weights.get("wiki", 1.0) <= 0.5


class TestNegativeSourcePatternsInProfiles:
    """CRPS-009: Negative patterns for code profiles exclude game/tutorial/TUI docs."""

    def test_codecompass_code_profile_negative_patterns_exist(self):
        from agent.services.retrieval_profile_service import _PROFILE_TABLE

        spec = _PROFILE_TABLE[(DOMAIN_CODECOMPASS, INTENT_CODE_EXPLANATION)]
        neg = spec["negative_source_patterns"]
        assert len(neg) >= 2
        patterns_joined = " ".join(neg).lower()
        assert "book" in patterns_joined or "ananta" in patterns_joined

    def test_worker_code_profile_negative_patterns_exist(self):
        from agent.services.retrieval_profile_service import _PROFILE_TABLE

        spec = _PROFILE_TABLE[(DOMAIN_WORKER, INTENT_CODE_EXPLANATION)]
        neg = spec["negative_source_patterns"]
        assert len(neg) >= 1

    def test_architecture_profile_no_negative_patterns(self):
        """Architecture overview needs wiki — must not penalize it."""
        from agent.services.retrieval_profile_service import _PROFILE_TABLE

        spec = _PROFILE_TABLE.get((DOMAIN_CODECOMPASS, INTENT_CODE_EXPLANATION))
        # Architecture profile should have empty negative_source_patterns
        arch_spec = _PROFILE_TABLE.get(
            (DOMAIN_CODECOMPASS, "architecture_overview"),
            {"negative_source_patterns": []},
        )
        assert arch_spec["negative_source_patterns"] == []
