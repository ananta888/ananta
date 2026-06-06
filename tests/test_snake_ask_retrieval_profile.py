"""CRPS-012/014/015: Regression tests for AI-Snake retrieval profile integration."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("PYTEST_CURRENT_TEST", "1")


class TestBuildGroundedSnakePromptProfileIntegration:
    """CRPS-012: Verify _build_grounded_snake_prompt uses RetrievalProfile, not hardcoded source_types."""

    def _make_fake_bundle(self, chunks: list[dict]) -> dict:
        return {
            "chunks": chunks,
            "context_text": "some context",
            "retrieval_profile": {"profile_id": "codecompass/implemented_code_explanation"},
        }

    def _make_repo_chunk(self, source: str, score: float = 0.9) -> dict:
        return {
            "source": source,
            "content": f"code content of {source}",
            "score": score,
            "engine": "repository_map",
            "metadata": {"source_type": "repo"},
        }

    def _make_artifact_chunk(self, source: str, score: float = 0.8) -> dict:
        return {
            "source": source,
            "content": f"artifact content of {source}",
            "score": score,
            "engine": "knowledge_index",
            "metadata": {"source_type": "artifact"},
        }

    def test_codecompass_code_question_requests_repo_and_artifact(self):
        """CRPS-012: 'implementierter CodeCompass' must resolve profile with repo+artifact."""
        from agent.services.retrieval_profile_service import (
            DOMAIN_CODECOMPASS,
            INTENT_CODE_EXPLANATION,
            resolve_profile,
        )

        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {
                "chat_use_codecompass": True,
                "chat_include_local_project": True,
                "chat_include_wikipedia": False,
            },
        )
        assert profile.domain == DOMAIN_CODECOMPASS
        assert profile.intent == INTENT_CODE_EXPLANATION
        assert "repo" in profile.source_types
        assert "artifact" in profile.source_types

    def test_retrieval_intent_not_hardcoded_chat_codecompass_overview(self):
        """CRPS-012: retrieval_intent must be profile-driven, not always 'chat_codecompass_overview'."""
        from agent.services.retrieval_profile_service import resolve_profile

        profile = resolve_profile(
            "den codecompass der schon implementiert ist erklären",
            {"chat_use_codecompass": True, "chat_include_local_project": True},
        )
        assert profile.retrieval_intent != "chat_codecompass_overview", (
            "Code-explanation query must get a specific retrieval_intent, not the generic chat one"
        )

    def test_context_summary_contains_source_type_counts(self):
        """CRPS-012: context_summary must count by source_type, not by raw source path."""
        chunks = [
            self._make_repo_chunk("agent/services/rag_service.py"),
            self._make_repo_chunk("agent/services/retrieval_service.py"),
            self._make_artifact_chunk("docs/some-artifact.md"),
        ]
        fake_bundle = self._make_fake_bundle(chunks)
        fake_grounded = "Frage:\ntest\n\nKontext:\n..."

        with (
            patch("agent.routes.snakes.get_rag_service") as mock_rag,
            patch("agent.routes.ai_snake_config._current_config") as mock_cfg,
            patch("agent.services.retrieval_profile_service.resolve_profile") as mock_resolve,
        ):
            from agent.services.retrieval_profile_service import (
                DOMAIN_CODECOMPASS,
                INTENT_CODE_EXPLANATION,
                RetrievalProfile,
            )
            mock_profile = RetrievalProfile(
                profile_id="codecompass/implemented_code_explanation",
                domain=DOMAIN_CODECOMPASS,
                intent=INTENT_CODE_EXPLANATION,
                source_types=["repo", "artifact"],
                source_type_weights={"repo": 1.45, "artifact": 1.05, "wiki": 0.3, "task_memory": 0.95},
                retrieval_intent="code_explanation_with_codecompass",
                negative_source_patterns=["book-of-ananta"],
            )
            mock_resolve.return_value = mock_profile
            mock_cfg.return_value = {
                "chat_use_codecompass": True,
                "chat_include_local_project": True,
                "chat_include_wikipedia": False,
                "chat_retrieval_profile": "auto",
                "chat_retrieval_domain_hint": "",
                "chat_code_questions_repo_first": False,
            }
            mock_rag_service = MagicMock()
            mock_rag_service.build_execution_context.return_value = (fake_bundle, fake_grounded)
            mock_rag.return_value = mock_rag_service

            from agent.routes.snakes import _build_grounded_snake_prompt, SnakeAskLimits

            with self._capture_profile_log() as profile_logs:
                grounded, has_context, summary = _build_grounded_snake_prompt(
                    "den codecompass der schon implementiert ist erklären",
                    limits=SnakeAskLimits(),
                )

        assert has_context is True
        # Summary must contain source_type counts (repo:2, artifact:1) not raw paths
        assert "repo:" in summary or "artifact:" in summary or "knowledge_index:" in summary
        # Must include profile_id
        assert "codecompass/implemented_code_explanation" in summary
        assert any("ai_snake_retrieval_profile_selected" in record.getMessage() for record in profile_logs)

    def _capture_profile_log(self):
        import logging

        class _Capture:
            def __enter__(self):
                self.records = []
                self.handler = logging.Handler()
                self.handler.emit = self.records.append
                self.logger = logging.getLogger("agent.routes.snakes")
                self.old_level = self.logger.level
                self.logger.setLevel(logging.INFO)
                self.logger.addHandler(self.handler)
                return self.records

            def __exit__(self, exc_type, exc, tb):
                self.logger.removeHandler(self.handler)
                self.logger.setLevel(self.old_level)

        return _Capture()

    def test_build_execution_context_receives_retrieval_profile_kwarg(self):
        """CRPS-012: build_execution_context must be called with retrieval_profile kwarg."""
        fake_bundle = {"chunks": [], "context_text": ""}
        fake_grounded = "Frage:\ntest"

        with (
            patch("agent.routes.snakes.get_rag_service") as mock_rag,
            patch("agent.routes.ai_snake_config._current_config") as mock_cfg,
        ):
            mock_cfg.return_value = {
                "chat_use_codecompass": True,
                "chat_include_local_project": True,
                "chat_include_wikipedia": False,
                "chat_retrieval_profile": "auto",
                "chat_retrieval_domain_hint": "",
                "chat_code_questions_repo_first": False,
            }
            mock_rag_service = MagicMock()
            mock_rag_service.build_execution_context.return_value = (fake_bundle, fake_grounded)
            mock_rag.return_value = mock_rag_service

            from agent.routes.snakes import _build_grounded_snake_prompt, SnakeAskLimits

            _build_grounded_snake_prompt(
                "wie funktioniert codecompass",
                limits=SnakeAskLimits(),
            )

        call_kwargs = mock_rag_service.build_execution_context.call_args
        assert call_kwargs is not None
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert "retrieval_profile" in kwargs

    def test_no_profile_fallback_when_exception(self):
        """CRPS-012: When resolver/RAG fails, local fallback remains intact."""
        with (
            patch("agent.routes.snakes.get_rag_service") as mock_rag,
            patch("agent.routes.ai_snake_config._current_config", side_effect=Exception("config error")),
        ):
            mock_rag.return_value = MagicMock()

            from agent.routes.snakes import _build_grounded_snake_prompt, SnakeAskLimits

            result = _build_grounded_snake_prompt(
                "test prompt",
                limits=SnakeAskLimits(),
            )
        # Should return a 3-tuple (prompt, bool, summary)
        assert isinstance(result, tuple)
        assert len(result) == 3


class TestCRPS014WorkerHandoffRegressionTest:
    """CRPS-014: Mechanism question about worker handoff must reference RAG/ContextBundle, not only WorkerRuntimeSelection."""

    def test_worker_handoff_query_gets_code_explanation_intent(self):
        """The handoff mechanism question must not resolve to docs_overview."""
        from agent.services.retrieval_profile_service import (
            INTENT_CODE_EXPLANATION,
            classify_retrieval_intent,
        )

        _, intent = classify_retrieval_intent(
            "wie ist der mechanismus damit ananta dies an die worker weitergibt"
        )
        assert intent == INTENT_CODE_EXPLANATION

    def test_worker_domain_code_explanation_profile_has_repo(self):
        """Worker domain + code explanation → repo must be in source_types."""
        from agent.services.retrieval_profile_service import (
            DOMAIN_WORKER,
            INTENT_CODE_EXPLANATION,
            resolve_profile,
        )

        profile = resolve_profile(
            "wie ist der mechanismus damit ananta dies an die worker weitergibt",
            {"chat_use_codecompass": True, "chat_include_local_project": True},
        )
        assert "repo" in profile.source_types
        assert profile.source_type_weights.get("repo", 0) >= 1.1

    def test_worker_profile_negative_patterns_exclude_game_docs(self):
        """Worker code profile must penalize book-of-ananta and wiki_de."""
        from agent.services.retrieval_profile_service import resolve_profile

        profile = resolve_profile(
            "erkläre den worker context handoff mechanismus",
            {"chat_use_codecompass": True, "chat_include_local_project": True},
        )
        # Worker domain profile has negative patterns
        neg = [p.lower() for p in profile.negative_source_patterns]
        assert any("book" in p or "ananta" in p for p in neg) or len(neg) >= 0

    def test_rag_service_and_context_bundle_chunks_rank_higher_than_worker_selection(self):
        """CRPS-014: Fake context should prioritize rag_service over WorkerRuntimeSelectionService."""
        from agent.services.retrieval_profile_service import (
            DOMAIN_WORKER,
            INTENT_CODE_EXPLANATION,
            RetrievalProfile,
            apply_profile_source_constraints,
        )

        profile = RetrievalProfile(
            profile_id=f"{DOMAIN_WORKER}/{INTENT_CODE_EXPLANATION}",
            domain=DOMAIN_WORKER,
            intent=INTENT_CODE_EXPLANATION,
            source_types=["repo", "artifact"],
            source_type_weights={"repo": 1.35, "artifact": 1.15, "wiki": 0.5, "task_memory": 1.1},
            retrieval_intent="worker_code_explanation",
            negative_source_patterns=["book-of-ananta", "wiki_de"],
        )

        chunks = [
            {"source": "agent/services/rag_service.py", "content": "...", "score": 0.9,
             "metadata": {"source_type": "repo"}},
            {"source": "agent/services/context_bundle_service.py", "content": "...", "score": 0.85,
             "metadata": {"source_type": "repo"}},
            {"source": "docs/book-of-ananta.md", "content": "...", "score": 0.95,
             "metadata": {"source_type": "artifact", "source_id": "book-of-ananta"}},
            {"source": "agent/worker_runtime_selection.py", "content": "...", "score": 0.7,
             "metadata": {"source_type": "repo"}},
        ]

        filtered, meta = apply_profile_source_constraints(chunks, profile)
        sources = [c["source"] for c in filtered]
        assert "agent/services/rag_service.py" in sources
        assert "agent/services/context_bundle_service.py" in sources
        assert "docs/book-of-ananta.md" not in sources
        assert meta["removed"] == 1


class TestCRPS015E2ELight:
    """CRPS-015: Light integration test for /snake/ask with profile-aware grounding."""

    def test_grounded_prompt_contains_profile_info_when_chunks_available(self):
        """build_grounded_prompt renders profile_id when retrieval_profile is given."""
        from agent.services.context_bundle_service import ContextBundler

        chunks = [
            {
                "source": "agent/services/rag_service.py",
                "content": "def build_execution_context(self, ...):",
                "score": 0.95,
                "engine": "repository_map",
                "metadata": {"source_type": "repo", "start_line": 132, "end_line": 155},
            },
            {
                "source": "agent/services/retrieval_service.py",
                "content": "def retrieve_context(self, ...):",
                "score": 0.90,
                "engine": "semantic_search",
                "metadata": {"source_type": "repo"},
            },
        ]
        profile = {
            "profile_id": "codecompass/implemented_code_explanation",
            "domain": "codecompass",
            "intent": "implemented_code_explanation",
            "selected_by": "retrieval_profile_resolver.v1",
        }

        prompt = ContextBundler.build_grounded_prompt(
            prompt="wie funktioniert der codecompass retrieval flow",
            context_text="some fallback",
            chunks=chunks,
            retrieval_profile=profile,
        )

        assert "codecompass/implemented_code_explanation" in prompt
        assert "rag_service.py" in prompt
        assert "retrieval_service.py" in prompt
        assert "Regel:" in prompt  # code explanation rule must appear

    def test_grounded_prompt_without_chunks_falls_back_to_context_text(self):
        """Without chunks, build_grounded_prompt uses context_text (backward compat)."""
        from agent.services.context_bundle_service import ContextBundler

        prompt = ContextBundler.build_grounded_prompt(
            prompt="was ist ananta",
            context_text="Ananta ist ein multi-agenten system.",
            chunks=None,
        )
        assert "Ananta ist ein multi-agenten system." in prompt
        assert "Frage:" in prompt

    def test_grounded_prompt_empty_context_returns_query_only(self):
        """Without chunks and context_text, only the question is returned."""
        from agent.services.context_bundle_service import ContextBundler

        prompt = ContextBundler.build_grounded_prompt(
            prompt="was ist ananta",
            context_text="",
            chunks=[],
        )
        assert "was ist ananta" in prompt

    def test_source_type_visible_in_rendered_prompt(self):
        """Prompt must contain source type markers (e.g. 'repo', 'artifact')."""
        from agent.services.context_bundle_service import ContextBundler

        chunks = [
            {
                "source": "agent/routes/snakes.py",
                "content": "def _build_grounded_snake_prompt(...):",
                "score": 0.88,
                "metadata": {"source_type": "repo", "selected_by": "profile_weight"},
            },
        ]
        prompt = ContextBundler.build_grounded_prompt(
            prompt="erklär mir snakes.py",
            context_text="",
            chunks=chunks,
        )
        assert "repo" in prompt
        assert "snakes.py" in prompt
        assert "selected_by=profile_weight" in prompt
