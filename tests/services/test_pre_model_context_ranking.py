"""APMCO-010: Tests for CandidateScorer and task classifier.

Covers APMCO-003 (task classification) and APMCO-004 (ranking).
"""
from __future__ import annotations

import pytest

from agent.services.pre_model_context_config import (
    ALL_TASK_KINDS,
    TASK_ARCHITECTURE,
    TASK_BUGFIX,
    TASK_CONFIG,
    TASK_GENERIC_CHAT,
    TASK_IMPLEMENTATION,
    TASK_NAVIGATION,
    TASK_SECURITY,
    TASK_TEST,
    classify_task,
)
from agent.services.pre_model_context_ranking import CandidateScorer, RankingWeights, ScoredCandidate


# ── Task classification ───────────────────────────────────────────────────────

class TestTaskClassification:
    @pytest.mark.parametrize("text,expected", [
        ("wo ist die Konfigurationsdatei?", TASK_NAVIGATION),
        ("where is the config file?", TASK_NAVIGATION),
        ("zeig mir die datei für die Routes", TASK_NAVIGATION),
        ("find the file", TASK_NAVIGATION),
        ("bugfix: crash in startup", TASK_BUGFIX),
        ("es gibt einen Fehler beim Login", TASK_BUGFIX),
        ("there's an exception in the auth module", TASK_BUGFIX),
        ("security vulnerability in the payment module", TASK_SECURITY),
        ("Sicherheitslücke im Auth-Modul", TASK_SECURITY),
        ("SQL injection in the API", TASK_SECURITY),
        ("implementiere die neue Funktion für Export", TASK_IMPLEMENTATION),
        ("write a new function for data processing", TASK_IMPLEMENTATION),
        ("schreibe einen Test für den Login-Flow", TASK_TEST),
        ("add unittest coverage for this class", TASK_TEST),
        ("erkläre die Architektur des Systems", TASK_ARCHITECTURE),
        ("how does the routing work?", TASK_ARCHITECTURE),
        ("config.yaml hat eine neue Option", TASK_CONFIG),
        ("update the environment variable", TASK_CONFIG),
        ("Wie geht es dir?", TASK_GENERIC_CHAT),
        ("what is the weather today?", TASK_GENERIC_CHAT),
    ])
    def test_classify_task_german_and_english(self, text: str, expected: str):
        result = classify_task(text)
        assert result == expected, f"Expected {expected!r} for {text!r}, got {result!r}"

    def test_explicit_task_kind_overrides_heuristic(self):
        result = classify_task("bugfix critical crash", explicit_task_kind=TASK_SECURITY)
        assert result == TASK_SECURITY

    def test_unknown_explicit_kind_falls_through_to_heuristic(self):
        result = classify_task("bugfix critical crash", explicit_task_kind="unknown_kind")
        assert result in ALL_TASK_KINDS

    def test_security_overrides_bugfix(self):
        result = classify_task("security bug: token injection vulnerability")
        assert result == TASK_SECURITY

    def test_empty_text_returns_valid_kind(self):
        result = classify_task("")
        assert result in ALL_TASK_KINDS

    def test_short_text_without_question_mark(self):
        result = classify_task("main.py")
        assert result in (TASK_NAVIGATION, TASK_GENERIC_CHAT)

    @pytest.mark.parametrize("text", [
        "auth token validation fehler",
        "passwort reset nicht funktionierend",
        "CSRF Schutz fehlt",
        "exploit möglich über XSS",
        "vulnerability in payment service",
    ])
    def test_security_keywords_trigger_security_kind(self, text: str):
        result = classify_task(text)
        assert result == TASK_SECURITY


# ── Candidate ranking ─────────────────────────────────────────────────────────

class TestCandidateScorer:
    def _make_scorer(self, **kwargs) -> CandidateScorer:
        return CandidateScorer(**kwargs)

    def test_same_input_always_same_order(self):
        scorer = self._make_scorer()
        candidates = [
            {"path": "a.py", "record_id": "a", "embedding_score": 0.7},
            {"path": "b.py", "record_id": "b", "embedding_score": 0.5},
            {"path": "c.py", "record_id": "c", "embedding_score": 0.9},
        ]
        r1 = [c.path for c in scorer.score_all(candidates)]
        r2 = [c.path for c in scorer.score_all(candidates)]
        assert r1 == r2, "Ranking must be deterministic"

    def test_working_file_is_preferred(self):
        scorer = self._make_scorer(working_files=["important.py"])
        candidates = [
            {"path": "other.py", "record_id": "o", "embedding_score": 0.8},
            {"path": "important.py", "record_id": "i", "embedding_score": 0.3},
        ]
        ranked = scorer.score_all(candidates)
        assert ranked[0].path == "important.py", "Working file must be ranked first"

    def test_policy_denied_path_has_policy_denied_flag(self):
        scorer = self._make_scorer(denied_paths={"secret.py"})
        candidates = [
            {"path": "secret.py", "record_id": "s"},
            {"path": "public.py", "record_id": "p", "embedding_score": 0.5},
        ]
        ranked = scorer.score_all(candidates)
        secret = next(c for c in ranked if c.path == "secret.py")
        public = next(c for c in ranked if c.path == "public.py")
        assert secret.policy_denied
        assert secret.final_score < public.final_score

    def test_graph_neighbors_boost_candidate(self):
        scorer = self._make_scorer()
        candidates = [
            {"path": "a.py", "record_id": "a", "embedding_score": 0.5, "graph_distance": 1},
            {"path": "b.py", "record_id": "b", "embedding_score": 0.5, "graph_distance": 10},
        ]
        ranked = scorer.score_all(candidates)
        a = next(c for c in ranked if c.path == "a.py")
        b = next(c for c in ranked if c.path == "b.py")
        assert a.graph_distance_score > b.graph_distance_score

    def test_sensitive_path_penalty_applied(self):
        scorer = self._make_scorer()
        candidates = [
            {"path": "src/auth/login.py", "record_id": "s", "embedding_score": 0.9},
            {"path": "utils/helper.py", "record_id": "u", "embedding_score": 0.7},
        ]
        ranked = scorer.score_all(candidates)
        auth_c = next(c for c in ranked if "auth" in c.path)
        assert auth_c.sensitivity_penalty < 0

    def test_tie_breaking_by_path_then_record_id(self):
        scorer = self._make_scorer()
        candidates = [
            {"path": "z.py", "record_id": "z"},
            {"path": "a.py", "record_id": "a"},
            {"path": "m.py", "record_id": "m"},
        ]
        ranked = scorer.score_all(candidates)
        paths = [c.path for c in ranked]
        assert paths == sorted(paths)

    def test_score_clamped_to_0_1(self):
        scorer = self._make_scorer()
        candidates = [
            {
                "path": "x.py",
                "record_id": "x",
                "embedding_score": 999.0,  # out of range
                "symbol_match_score": -5.0,
                "working_file_bonus": 1000,
            }
        ]
        ranked = scorer.score_all(candidates)
        assert 0.0 <= ranked[0].final_score <= 1.0

    def test_custom_ranking_weights(self):
        weights = RankingWeights(embedding_score=0.0, working_file_bonus=1.0)
        scorer = self._make_scorer(weights=weights, working_files=["target.py"])
        candidates = [
            {"path": "target.py", "record_id": "t", "embedding_score": 0.1},
            {"path": "other.py", "record_id": "o", "embedding_score": 0.99},
        ]
        ranked = scorer.score_all(candidates)
        assert ranked[0].path == "target.py"

    def test_empty_candidates_returns_empty_list(self):
        scorer = self._make_scorer()
        assert scorer.score_all([]) == []
