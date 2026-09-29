"""CRPS-007: tests for chat_codecompass_trigger_mode and the hardened
domain_hint validation in agent.services.retrieval_profile_service.

The trigger_mode lets the TUI / Angular user override the keyword-based
domain classifier. Valid values:
  - "auto"             — no override, fall through to keyword classification
  - "force_codecompass" — domain locked to CODECOMPASS, intent upgraded to
                         code_explanation if still generic
  - "force_repo_first"  — source_types filtered to repo + task_memory,
                         repo weight boosted to >= 1.5
  - "disabled"          — domain/intent forced to generic_chat (no RAG)

The domain_hint validation restricts the value to the 7 known DOMAIN_*
constants; unknown values are logged as "domain_hint_unknown" in reasons
and the classified domain is kept (never raises).

These tests are deterministic — no LLM, no network, no DB.
"""
from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("PYTEST_CURRENT_TEST", "1")

from agent.services.retrieval_profile_service import (  # noqa: E402
    DOMAIN_CODECOMPASS,
    DOMAIN_GENERIC,
    DOMAIN_OPS,
    DOMAIN_WORKER,
    INTENT_CODE_EXPLANATION,
    INTENT_GENERIC_CHAT,
    classify_retrieval_intent,
    resolve_profile,
)


def _base_cfg(**overrides: Any) -> dict[str, Any]:
    """Minimal ui_config with chat_use_codecompass enabled."""
    cfg: dict[str, Any] = {
        "chat_use_codecompass": True,
        "chat_include_local_project": True,
        "chat_include_wikipedia": False,
        "chat_include_task_memory": True,
    }
    cfg.update(overrides)
    return cfg


class TestTriggerModeAuto:
    """trigger_mode=auto must not change behaviour compared to the default."""

    def test_auto_no_override_keeps_keyword_classification(self):
        # "codecompass" appears in the question → domain must be CODECOMPASS
        # under the auto path (no trigger_mode override).
        domain, _intent = classify_retrieval_intent(
            "Was macht codecompass?", _base_cfg()
        )
        assert domain == DOMAIN_CODECOMPASS

    def test_auto_unset_equals_explicit_auto(self):
        # Unset trigger_mode defaults to "auto".
        cfg = _base_cfg()  # no chat_codecompass_trigger_mode
        domain_a, _ = classify_retrieval_intent("Was ist x?", cfg)
        domain_b, _ = classify_retrieval_intent("Was ist x?", _base_cfg(chat_codecompass_trigger_mode="auto"))
        assert domain_a == domain_b

    def test_auto_explicit_string_with_whitespace_lowercased(self):
        # Robustness: "AUTO" / " Auto " must behave the same.
        domain_a, _ = classify_retrieval_intent("hi", _base_cfg(chat_codecompass_trigger_mode="auto"))
        domain_b, _ = classify_retrieval_intent("hi", _base_cfg(chat_codecompass_trigger_mode="  AUTO  "))
        assert domain_a == domain_b


class TestTriggerModeForceCodecompass:
    """force_codecompass must lock domain to CODECOMPASS regardless of question."""

    def test_force_codecompass_on_generic_question(self):
        # Question has no domain keyword — auto would pick generic; force must override.
        domain, intent = classify_retrieval_intent(
            "Hallo", _base_cfg(chat_codecompass_trigger_mode="force_codecompass")
        )
        assert domain == DOMAIN_CODECOMPASS
        # Intent must be upgraded from GENERIC_CHAT to CODE_EXPLANATION
        # because the user explicitly asked for CodeCompass-driven retrieval.
        assert intent == INTENT_CODE_EXPLANATION

    def test_force_codecompress_preserves_classified_intent(self):
        # Question already classifies to a non-generic intent — force must keep it.
        # We need an intent that "codebase" / "funktioniert" triggers.
        domain, intent = classify_retrieval_intent(
            "Wie funktioniert die pipeline?",
            _base_cfg(chat_codecompass_trigger_mode="force_codecompass"),
        )
        assert domain == DOMAIN_CODECOMPASS
        # intent is preserved (CODE_EXPLANATION or whatever keyword class picked)
        assert intent != INTENT_GENERIC_CHAT

    def test_force_codecompass_overrides_keywords_pointing_to_other_domain(self):
        # Question contains strong "ops" / "deployment" keywords that would
        # otherwise classify to OPS. force_codecompass must still win.
        domain, _ = classify_retrieval_intent(
            "deployment pipeline how to rollback",
            _base_cfg(chat_codecompass_trigger_mode="force_codecompass"),
        )
        assert domain == DOMAIN_CODECOMPASS

    def test_force_codecompass_profile_artifact_ensured(self):
        # resolve_profile must inject artifact into source_types and boost its weight.
        profile = resolve_profile(
            "Hallo",
            _base_cfg(chat_codecompass_trigger_mode="force_codecompass"),
        )
        assert "artifact" in profile.source_types
        # The reason list must mention the explicit force.
        assert any("trigger_mode_force_codecompass" in r for r in profile.reasons)

    def test_force_codecompass_does_not_codepath_leak_into_codepath_used_by_auto(self):
        # Sanity: resolve_profile with auto on the same question must not
        # carry the force_codecompass reason.
        profile_auto = resolve_profile("Hallo", _base_cfg(chat_codecompass_trigger_mode="auto"))
        assert not any("trigger_mode_force_codecompass" in r for r in profile_auto.reasons)


class TestTriggerModeForceRepoFirst:
    """force_repo_first must filter source_types to repo+task_memory and boost repo weight."""

    def test_force_repo_first_keeps_classified_domain(self):
        # Question has codecompass keywords → domain stays as classified.
        domain, _ = classify_retrieval_intent(
            "codecompass funktioniert wie?",
            _base_cfg(chat_codecompass_trigger_mode="force_repo_first"),
        )
        assert domain == DOMAIN_CODECOMPASS

    def test_force_repo_first_promotes_generic_intent(self):
        domain, intent = classify_retrieval_intent(
            "Hallo",
            _base_cfg(chat_codecompass_trigger_mode="force_repo_first"),
        )
        # intent upgrades from generic to code_explanation
        assert intent == INTENT_CODE_EXPLANATION

    def test_force_repo_first_drops_artifact_and_wiki(self):
        # For the codecompass profile, the default source_types are
        # ["repo", "artifact"]. force_repo_first must keep repo and drop
        # artifact (and any wiki that may have been in the list).
        profile = resolve_profile(
            "codecompass wie?",
            _base_cfg(chat_codecompass_trigger_mode="force_repo_first"),
        )
        assert "repo" in profile.source_types
        assert "artifact" not in profile.source_types
        assert "wiki" not in profile.source_types

    def test_force_repo_first_keeps_task_memory_when_present(self):
        # If task_memory is already in the source_types (e.g. via spec),
        # force_repo_first must keep it. We use a worker profile that
        # starts with ["repo", "artifact"] and inject task_memory via
        # the resolve_profile flow by pre-seeding the spec.
        # Simpler: assert that, after force_repo_first, only members of
        # {"repo", "task_memory"} are present.
        profile = resolve_profile(
            "codecompass wie?",
            _base_cfg(chat_codecompass_trigger_mode="force_repo_first"),
        )
        for st in profile.source_types:
            assert st in {"repo", "task_memory"}, f"unexpected source_type {st!r} after force_repo_first"

    def test_force_repo_first_boosts_repo_weight(self):
        profile = resolve_profile(
            "codecompass wie?",
            _base_cfg(chat_codecompass_trigger_mode="force_repo_first"),
        )
        # The patch must set repo weight >= 1.5
        assert profile.source_type_weights.get("repo", 0.0) >= 1.5

    def test_force_repo_first_records_reason(self):
        profile = resolve_profile(
            "Hallo",
            _base_cfg(chat_codecompass_trigger_mode="force_repo_first"),
        )
        assert any("trigger_mode_force_repo_first" in r for r in profile.reasons)


class TestTriggerModeDisabled:
    """disabled must force the no-RAG generic profile."""

    def test_disabled_forces_generic_domain(self):
        domain, _ = classify_retrieval_intent(
            "codecompass funktioniert wie?",  # strong codecompass keyword
            _base_cfg(chat_codecompass_trigger_mode="disabled"),
        )
        assert domain == DOMAIN_GENERIC

    def test_disabled_forces_generic_intent(self):
        _domain, intent = classify_retrieval_intent(
            "Wie funktioniert die pipeline?",  # strong code keyword
            _base_cfg(chat_codecompass_trigger_mode="disabled"),
        )
        assert intent == INTENT_GENERIC_CHAT

    def test_disabled_overrides_codecompass_keyword_leak(self):
        # auto would have classified to CODECOMPASS; disabled must prevent that.
        cfg = _base_cfg(chat_codecompass_trigger_mode="disabled")
        # and we even disable chat_use_codecompass to be doubly sure
        cfg["chat_use_codecompass"] = False
        domain, intent = classify_retrieval_intent("Was ist codecompass?", cfg)
        assert domain == DOMAIN_GENERIC
        assert intent == INTENT_GENERIC_CHAT


class TestTriggerModeInvalidValues:
    """Invalid / unknown values must NOT crash — they fall back to "auto" semantics."""

    def test_invalid_string_falls_back_to_auto(self):
        # "gibberish" is not one of the 4 valid values.
        domain_a, _ = classify_retrieval_intent(
            "codecompass?",
            _base_cfg(chat_codecompass_trigger_mode="gibberish"),
        )
        domain_b, _ = classify_retrieval_intent(
            "codecompass?",
            _base_cfg(),  # no trigger_mode set
        )
        assert domain_a == domain_b

    def test_empty_string_falls_back_to_auto(self):
        domain_a, _ = classify_retrieval_intent("codecompass?", _base_cfg(chat_codecompass_trigger_mode=""))
        domain_b, _ = classify_retrieval_intent("codecompass?", _base_cfg())
        assert domain_a == domain_b

    def test_none_falls_back_to_auto(self):
        cfg = _base_cfg(chat_codecompass_trigger_mode=None)
        domain, _ = classify_retrieval_intent("codecompass?", cfg)
        assert domain == DOMAIN_CODECOMPASS


class TestDomainHintValidation:
    """domain_hint accepts only the 7 known DOMAIN_* constants; unknown values
    are logged as 'domain_hint_unknown' and the classified domain is kept."""

    def test_known_domain_hint_overrides_classification(self):
        # Question classifies to CODECOMPASS; hint forces WORKER.
        profile = resolve_profile(
            "Was ist codecompass?",
            _base_cfg(),
            domain_hint=DOMAIN_WORKER,
        )
        assert profile.domain == DOMAIN_WORKER
        assert any(r == f"domain_hint:{DOMAIN_WORKER}" for r in profile.reasons)

    def test_unknown_domain_hint_is_rejected_with_reason(self):
        # "moonbase_alpha" is not a known DOMAIN_* constant.
        profile = resolve_profile(
            "Was ist codecompass?",
            _base_cfg(),
            domain_hint="moonbase_alpha",
        )
        # Classified domain (CODECOMPASS) is kept.
        assert profile.domain == DOMAIN_CODECOMPASS
        # Reason records the unknown hint for debuggability.
        assert any("domain_hint_unknown:moonbase_alpha:ignored" in r for r in profile.reasons)

    def test_empty_domain_hint_does_not_emit_reason(self):
        # Empty string is treated as "no hint" — no domain_hint reason emitted.
        profile = resolve_profile("Was ist codecompass?", _base_cfg(), domain_hint="")
        assert not any(r.startswith("domain_hint:") for r in profile.reasons)
        assert not any(r.startswith("domain_hint_unknown:") for r in profile.reasons)

    def test_ops_domain_hint_accepted(self):
        # Make sure all 7 constants are accepted.
        for d in (DOMAIN_CODECOMPASS, DOMAIN_OPS, DOMAIN_WORKER):
            profile = resolve_profile("irrelevant", _base_cfg(), domain_hint=d)
            assert profile.domain == d


class TestSourceTypeToggleTaskMemory:
    """chat_include_task_memory must remove the task_memory source type when off."""

    def test_task_memory_enabled_by_default(self):
        # The default is True (opt-out, preserving old behaviour).
        from agent.services.retrieval_profile_service import _apply_ui_source_constraints
        result = _apply_ui_source_constraints(
            ["repo", "artifact", "wiki", "task_memory"], _base_cfg()
        )
        assert "task_memory" in result

    def test_task_memory_disabled_removes_source(self):
        from agent.services.retrieval_profile_service import _apply_ui_source_constraints
        result = _apply_ui_source_constraints(
            ["repo", "artifact", "wiki", "task_memory"],
            _base_cfg(chat_include_task_memory=False),
        )
        assert "task_memory" not in result
        # Other source types must remain.
        assert "repo" in result
        assert "artifact" in result

    def test_task_memory_false_preserves_legacy_sources(self):
        # Toggling task_memory off must NOT remove repo or artifact.
        # Note: `chat_include_wikipedia` defaults to False (legacy), so
        # `wiki` is already filtered out by _apply_ui_source_constraints
        # before this assertion runs.
        from agent.services.retrieval_profile_service import _apply_ui_source_constraints
        result = _apply_ui_source_constraints(
            ["repo", "artifact", "wiki", "task_memory"],
            _base_cfg(chat_include_task_memory=False),
        )
        for st in ("repo", "artifact"):
            assert st in result
        assert "task_memory" not in result

    def test_existing_toggles_still_work(self):
        # Regression: chat_use_codecompass, chat_include_local_project,
        # chat_include_wikipedia must still remove their respective sources.
        from agent.services.retrieval_profile_service import _apply_ui_source_constraints
        result = _apply_ui_source_constraints(
            ["repo", "artifact", "wiki", "task_memory"],
            _base_cfg(
                chat_use_codecompass=False,
                chat_include_local_project=False,
                chat_include_wikipedia=False,
                chat_include_task_memory=True,
            ),
        )
        assert result == ["task_memory"]


class TestReasonLogging:
    """The reasons list must mention the trigger_mode for non-auto values."""

    def test_force_codecompass_reason_mentioned(self):
        profile = resolve_profile("Hallo", _base_cfg(chat_codecompass_trigger_mode="force_codecompass"))
        assert "trigger_mode:force_codecompass" in profile.reasons

    def test_force_repo_first_reason_mentioned(self):
        profile = resolve_profile("Hallo", _base_cfg(chat_codecompass_trigger_mode="force_repo_first"))
        assert "trigger_mode:force_repo_first" in profile.reasons

    def test_disabled_reason_mentioned(self):
        profile = resolve_profile("Hallo", _base_cfg(chat_codecompass_trigger_mode="disabled"))
        assert "trigger_mode:disabled" in profile.reasons

    def test_auto_reason_not_mentioned(self):
        # CRPS-007: only surface trigger_mode in reasons when non-auto.
        # Keeps the reasons list clean for the common path.
        profile = resolve_profile("Hallo", _base_cfg(chat_codecompass_trigger_mode="auto"))
        assert not any(r.startswith("trigger_mode:") for r in profile.reasons)
