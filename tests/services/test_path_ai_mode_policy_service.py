"""RTIPM-008: Tests for PathAiModePolicyService.

Covers glob matching, priority, block-vs-allow semantics, backward compat.
"""
from __future__ import annotations

import pytest

from agent.services.path_ai_mode_policy_service import (
    AI_MODE_CODECOMPASS_ONLY,
    AI_MODE_DETERMINISTIC_ONLY,
    AI_MODE_DIRECT_LLM,
    AI_MODE_EMBEDDING_ONLY,
    AI_MODE_FULL_LLM,
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModeRule,
    PathAiModePolicyService,
    get_path_ai_mode_policy_service,
    reset_path_ai_mode_policy_service,
)


def _svc(rules: list[dict]) -> PathAiModePolicyService:
    return PathAiModePolicyService.from_config({"path_ai_modes": rules})


class TestDefaultBehavior:
    def test_no_rules_allows_all_modes(self):
        svc = PathAiModePolicyService()
        result = svc.resolve("src/main.py")
        assert result.is_mode_allowed(AI_MODE_FULL_LLM)
        assert result.is_mode_allowed(AI_MODE_EMBEDDING_ONLY)
        assert result.matched_rule is None

    def test_empty_config_allows_all_modes(self):
        svc = PathAiModePolicyService.from_config({})
        assert svc.resolve("anything.py").is_mode_allowed(AI_MODE_FULL_LLM)

    def test_none_config_allows_all_modes(self):
        svc = PathAiModePolicyService.from_config(None)
        assert svc.resolve("file.py").is_mode_allowed(AI_MODE_FULL_LLM)


class TestGlobMatching:
    def test_exact_glob_matches(self):
        svc = _svc([{
            "path_glob": "src/security/auth.py",
            "blocked_ai_modes": [AI_MODE_FULL_LLM],
        }])
        assert not svc.resolve("src/security/auth.py").is_mode_allowed(AI_MODE_FULL_LLM)
        assert svc.resolve("src/other/auth.py").is_mode_allowed(AI_MODE_FULL_LLM)

    def test_wildcard_glob_matches_subdirectory(self):
        svc = _svc([{
            "path_glob": "src/security/**",
            "blocked_ai_modes": [AI_MODE_FULL_LLM, AI_MODE_DIRECT_LLM],
        }])
        assert not svc.resolve("src/security/auth.py").is_mode_allowed(AI_MODE_FULL_LLM)
        assert not svc.resolve("src/security/deep/nested/file.py").is_mode_allowed(AI_MODE_FULL_LLM)
        assert svc.resolve("src/docs/readme.md").is_mode_allowed(AI_MODE_FULL_LLM)

    def test_docs_glob_allows_full_llm(self):
        svc = _svc([
            {
                "path_glob": "src/security/**",
                "blocked_ai_modes": [AI_MODE_FULL_LLM],
                "priority": 20,
            },
            {
                "path_glob": "docs/**",
                "allowed_ai_modes": [AI_MODE_FULL_LLM, AI_MODE_EMBEDDING_ONLY],
                "priority": 10,
            },
        ])
        assert not svc.resolve("src/security/token.py").is_mode_allowed(AI_MODE_FULL_LLM)
        assert svc.resolve("docs/readme.md").is_mode_allowed(AI_MODE_FULL_LLM)

    def test_backslash_normalized(self):
        svc = _svc([{"path_glob": "src/security/**", "blocked_ai_modes": [AI_MODE_FULL_LLM]}])
        assert not svc.resolve(r"src\security\auth.py").is_mode_allowed(AI_MODE_FULL_LLM)


class TestPriority:
    def test_higher_priority_rule_wins(self):
        svc = _svc([
            {
                "path_glob": "src/**",
                "blocked_ai_modes": [AI_MODE_FULL_LLM],
                "priority": 5,
            },
            {
                "path_glob": "src/security/**",
                "blocked_ai_modes": [AI_MODE_FULL_LLM, AI_MODE_DIRECT_LLM],
                "priority": 20,
            },
        ])
        result = svc.resolve("src/security/auth.py")
        assert result.matched_rule is not None
        # Higher priority wins → more specific rule (security/**) matched first
        assert AI_MODE_DIRECT_LLM in result.blocked_modes

    def test_auto_priority_prefers_longer_globs(self):
        r1 = PathAiModeRule.from_raw({"path_glob": "src/**"})
        r2 = PathAiModeRule.from_raw({"path_glob": "src/security/**"})
        assert r2.priority > r1.priority


class TestModeSemantics:
    def test_blocked_mode_is_denied(self):
        svc = _svc([{"path_glob": "**", "blocked_ai_modes": [AI_MODE_FULL_LLM]}])
        assert not svc.resolve("any/file.py").is_mode_allowed(AI_MODE_FULL_LLM)

    def test_allowed_modes_restrict_to_listed(self):
        svc = _svc([{
            "path_glob": "src/security/**",
            "allowed_ai_modes": [AI_MODE_CODECOMPASS_ONLY, AI_MODE_EMBEDDING_ONLY],
        }])
        result = svc.resolve("src/security/policy.py")
        assert result.is_mode_allowed(AI_MODE_CODECOMPASS_ONLY)
        assert result.is_mode_allowed(AI_MODE_EMBEDDING_ONLY)
        assert not result.is_mode_allowed(AI_MODE_FULL_LLM)

    def test_restricted_transformer_allowed_while_full_llm_blocked(self):
        svc = _svc([{
            "path_glob": "src/security/**",
            "allowed_ai_modes": [
                AI_MODE_CODECOMPASS_ONLY,
                AI_MODE_EMBEDDING_ONLY,
                AI_MODE_RESTRICTED_TRANSFORMER,
                AI_MODE_DETERMINISTIC_ONLY,
            ],
            "blocked_ai_modes": [AI_MODE_FULL_LLM, AI_MODE_DIRECT_LLM],
        }])
        result = svc.resolve("src/security/auth.py")
        assert result.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER)
        assert not result.is_mode_allowed(AI_MODE_FULL_LLM)


class TestPolicyResultFields:
    def test_allow_free_text_generation_false_for_security(self):
        svc = _svc([{
            "path_glob": "src/security/**",
            "allow_free_text_generation": False,
            "allow_code_generation": False,
        }])
        result = svc.resolve("src/security/auth.py")
        assert not result.allow_free_text_generation
        assert not result.allow_code_generation

    def test_llm_scope_local_only(self):
        svc = _svc([{
            "path_glob": "src/payment/**",
            "llm_scope": "local_only",
        }])
        result = svc.resolve("src/payment/checkout.py")
        assert result.llm_scope == "local_only"

    def test_max_input_chars(self):
        svc = _svc([{
            "path_glob": "src/security/**",
            "max_input_chars": 12000,
        }])
        result = svc.resolve("src/security/token.py")
        assert result.max_input_chars == 12000

    def test_reason_codes_present(self):
        svc = _svc([{
            "path_glob": "src/**",
            "blocked_ai_modes": [AI_MODE_FULL_LLM],
        }])
        result = svc.resolve("src/main.py")
        assert any("matched_glob" in rc for rc in result.reason_codes)


class TestBatchResolve:
    def test_batch_resolve_returns_one_result_per_path(self):
        svc = _svc([{"path_glob": "src/security/**", "blocked_ai_modes": [AI_MODE_FULL_LLM]}])
        paths = ["src/security/auth.py", "docs/readme.md", "tests/test_auth.py"]
        results = svc.resolve_for_candidates(paths)
        assert set(results.keys()) == set(paths)

    def test_is_mode_allowed_shortcut(self):
        svc = _svc([{"path_glob": "src/security/**", "blocked_ai_modes": [AI_MODE_FULL_LLM]}])
        assert not svc.is_mode_allowed("src/security/auth.py", AI_MODE_FULL_LLM)
        assert svc.is_mode_allowed("tests/test_auth.py", AI_MODE_FULL_LLM)


class TestToDict:
    def test_to_dict_contains_expected_keys(self):
        svc = _svc([{"path_glob": "src/**", "blocked_ai_modes": [AI_MODE_FULL_LLM]}])
        result = svc.resolve("src/main.py")
        d = result.to_dict()
        for key in ("path", "matched_rule", "allowed_modes", "blocked_modes",
                    "allow_free_text_generation", "reason_codes"):
            assert key in d


class TestSingleton:
    def teardown_method(self):
        reset_path_ai_mode_policy_service(None)

    def test_singleton_getter_returns_instance(self):
        svc = get_path_ai_mode_policy_service()
        assert isinstance(svc, PathAiModePolicyService)

    def test_reset_replaces_singleton(self):
        new = PathAiModePolicyService(rules=[
            PathAiModeRule.from_raw({"path_glob": "**", "blocked_ai_modes": [AI_MODE_FULL_LLM]})
        ])
        reset_path_ai_mode_policy_service(new)
        assert not get_path_ai_mode_policy_service().is_mode_allowed("x.py", AI_MODE_FULL_LLM)
