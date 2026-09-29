import pytest
from agent.services.commit_message_validator import (
    ALLOWED_TYPES,
    BLOCKED_SUBJECTS,
    CommitMessageValidator,
)


def v():
    return CommitMessageValidator()


def test_valid_message_with_scope():
    r = v().validate("feat(goal-config): export ALLOWED_GOAL_CONFIG_KEYS as public frozenset")
    assert r.valid is True
    assert r.parsed_type == "feat"
    assert r.parsed_scope == "goal-config"


def test_valid_message_without_scope():
    r = v().validate("fix: correct checksum computation")
    assert r.valid is True
    assert r.parsed_scope is None


def test_blocked_fixup_planning():
    r = v().validate("fixup planning")
    assert r.valid is False
    assert any("blocked" in e.lower() for e in r.errors)


def test_blocked_wip():
    r = v().validate("wip")
    assert r.valid is False


def test_blocked_update_code():
    r = v().validate("update code")
    assert r.valid is False


def test_invalid_type():
    r = v().validate("hotfix(scope): something")
    assert r.valid is False
    assert any("type" in e for e in r.errors)


def test_subject_too_long():
    r = v().validate("feat(scope): " + "a" * 73)
    assert r.valid is False


def test_security_type_allowed():
    r = v().validate("security(goal-config): extend redaction markers")
    assert r.valid is True
    assert r.parsed_type == "security"


def test_breaking_change_exclamation():
    r = v().validate("feat(api)!: remove deprecated endpoint")
    assert r.valid is True


def test_blocked_subjects_case_insensitive():
    r = v().validate("FIXUP PLANNING")
    assert r.valid is False


def test_allowed_types_is_public_frozenset():
    assert isinstance(ALLOWED_TYPES, frozenset)
    assert "feat" in ALLOWED_TYPES
    assert "security" in ALLOWED_TYPES
    assert "fix" in ALLOWED_TYPES


def test_blocked_subjects_is_tuple():
    assert isinstance(BLOCKED_SUBJECTS, tuple)
    assert "fixup planning" in BLOCKED_SUBJECTS
    assert "wip" in BLOCKED_SUBJECTS


def test_validate_or_raise_raises_on_invalid():
    with pytest.raises(ValueError, match="blocked"):
        v().validate_or_raise("fixup planning")


def test_validate_or_raise_returns_result_on_valid():
    result = v().validate_or_raise("chore(ctx): increase OLLAMA_NUM_CTX to 32768")
    assert result.valid is True


def test_valid_chore_no_scope():
    r = v().validate("chore: update dependencies")
    assert r.valid is True


def test_ci_type():
    r = v().validate("ci(github): add commitlint workflow")
    assert r.valid is True
